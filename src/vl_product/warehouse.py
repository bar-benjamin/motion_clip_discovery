"""Persist complete source snapshots and expose read-only analytical queries.

Read ingest() first for the transaction lifecycle. Its helpers handle catalog
setup, per-frame decisions, and the audit trail without owning transactions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import TYPE_CHECKING, TypedDict
from uuid import uuid4

import duckdb

from vl_product.contracts import (
    MAX_FRAMES_PER_DRIVE,
    ClipRecord,
    DriveRecord,
    Frame,
    FrameSource,
    SourceManifest,
)
from vl_product.etl import DataError, FrameFact, reduce_frame
from vl_product.quality import QualityGateError, QualityPolicy
from vl_product.rules import AnalysisRules

if TYPE_CHECKING:
    from pandas import DataFrame


@dataclass
class IngestionCounts:
    """Mutually exclusive outcomes for input records; gap warnings are separate."""

    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0


class IngestionReport(TypedDict):
    """JSON-compatible result returned to the CLI and benchmark."""

    run_id: str
    accepted: int
    rejected: int
    duplicates: int
    elapsed_seconds: float
    database: str
    manifest: SourceManifest
    stage_seconds: dict[str, float]
    rejections_by_code: dict[str, int]


def ingest(
    path: Path, source: FrameSource, policy: QualityPolicy | None = None, *, retention: int = 3
) -> IngestionReport:
    """Atomically replace a catalog with one complete replay of source.

    Invalid records are quarantined. Iterator failures, storage failures and
    interrupts abort the snapshot, leaving previously committed data intact.
    """
    if type(retention) is not int or retention < 1:
        raise ValueError("E_CONFIG: Retention must be a positive integer")
    policy = policy if policy is not None else QualityPolicy()
    started_at = perf_counter()
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {**source.manifest(), "analysis_rules": asdict(AnalysisRules())}
    drives, clips = list(source.drives()), list(source.clips())
    _validate_catalog(drives, clips)
    manifest_json = json.dumps(manifest, sort_keys=True)
    run_id = uuid4().hex
    logger = logging.getLogger(__name__)
    logger.info(json.dumps({"event": "run started", "run_id": run_id}))
    stage_seconds = {"read_reduce": 0.0, "load": 0.0, "derive": 0.0, "quality_gate": 0.0}
    config_hash = hashlib.sha256(manifest_json.encode()).hexdigest()
    catalog_json = json.dumps(
        {
            "drives": sorted((asdict(d) for d in drives), key=lambda d: d["drive_id"]),
            "clips": sorted((asdict(c) for c in clips), key=lambda c: c["clip_id"]),
        },
        sort_keys=True,
    )
    catalog_fingerprint = hashlib.sha256(catalog_json.encode()).hexdigest()

    with duckdb.connect(str(database_path)) as connection:
        _verify_database_schema(connection)
        if not connection.execute("SHOW TABLES").fetchall():
            connection.execute(Path(__file__).with_name("schema.sql").read_text())
        counts = IngestionCounts()
        connection.execute("BEGIN TRANSACTION")
        try:
            _prepare_snapshot(connection, drives, clips, run_id)
            counts = _ingest_frames(
                connection, source.frames(), drives, clips, run_id, counts, stage_seconds
            )
            stage_start = perf_counter()
            _record_missing_frames(connection, run_id)
            stage_seconds["derive"] = perf_counter() - stage_start
            stage_start = perf_counter()
            _check_quality(connection, counts, policy)
            stage_seconds["quality_gate"] = perf_counter() - stage_start
            logger.info(json.dumps({"event": "gate accepted", "run_id": run_id}))
            _record_completed_run(
                connection, run_id, manifest_json, counts, config_hash, catalog_fingerprint
            )
            rejections_by_code = dict(
                connection.execute(
                    "SELECT code, count(*) FROM quality_events "
                    "WHERE starts_with(code, 'E_') GROUP BY code"
                ).fetchall()
            )
            _prune_runs(connection, retention)
            connection.execute("COMMIT")
        except BaseException as error:
            logger.warning(
                json.dumps({"event": "run failed", "run_id": run_id, "reason": str(error)})
            )
            # Include keyboard interrupts: a partial source is never a complete snapshot.
            connection.execute("ROLLBACK")
            try:
                connection.execute(
                    "INSERT INTO run_attempts VALUES (?, current_timestamp, 'failed', ?, ?)",
                    [run_id, f"{type(error).__name__}: {error}", json.dumps(asdict(counts))],
                )
            except (duckdb.Error, OSError):
                logging.getLogger(__name__).exception("Failed to record ingestion attempt")
            raise

    for stage, seconds in stage_seconds.items():
        logger.info(json.dumps({"event": "stage", "stage": stage, "seconds": seconds}))
    logger.info(
        json.dumps(
            {"event": "run completed", "run_id": run_id, "rejections_by_code": rejections_by_code}
        )
    )
    return {
        "stage_seconds": stage_seconds,
        "rejections_by_code": rejections_by_code,
        "run_id": run_id,
        "accepted": counts.accepted,
        "rejected": counts.rejected,
        "duplicates": counts.duplicates,
        "elapsed_seconds": round(perf_counter() - started_at, 3),
        "database": str(database_path),
        "manifest": manifest,
    }


def _verify_database_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Allow a new database or our supported schema; protect unrelated files."""
    existing_tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    if not existing_tables:
        return
    if "vl_schema" not in existing_tables:
        raise ValueError("E_DATABASE: Refusing to replace an unrecognized database")
    schema_versions = connection.execute("SELECT version FROM vl_schema").fetchall()
    if schema_versions != [(3,)]:
        raise ValueError("E_DATABASE: Unsupported warehouse schema version")
    signature_sql = (
        "SELECT table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns WHERE table_schema='main' "
        "ORDER BY table_name, ordinal_position"
    )
    with duckdb.connect(":memory:") as reference:
        reference.execute(Path(__file__).with_name("schema.sql").read_text())
        expected = reference.execute(signature_sql).fetchall()
    if connection.execute(signature_sql).fetchall() != expected:
        raise ValueError("E_DATABASE: Warehouse structure does not match its schema version")


def _validate_catalog(drives: list[DriveRecord], clips: list[ClipRecord]) -> None:
    """Reject ambiguous identities, unsupported clocks and invalid clip bounds."""

    def valid_id(value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) is not None

    drive_ids = set()
    for drive in drives:
        if (
            not isinstance(drive, DriveRecord)
            or not valid_id(drive.drive_id)
            or drive.drive_id in drive_ids
            or type(drive.start_ns) is not int
            or type(drive.period_ns) is not int
            or drive.period_ns <= 0
            or not 0 <= drive.start_ns <= 2**63 - 1
            or drive.start_ns + (MAX_FRAMES_PER_DRIVE - 1) * drive.period_ns > 2**63 - 1
            or drive.coordinate_system != "local_world"
        ):
            raise ValueError("E_CATALOG: Invalid or duplicate drive metadata")
        drive_ids.add(drive.drive_id)
    clip_ids = set()
    for clip in clips:
        if (
            not isinstance(clip, ClipRecord)
            or not valid_id(clip.clip_id)
            or clip.clip_id in clip_ids
            or clip.drive_id not in drive_ids
            or type(clip.start_frame) is not int
            or type(clip.end_frame) is not int
            or not 0 <= clip.start_frame <= clip.end_frame < MAX_FRAMES_PER_DRIVE
            or not isinstance(clip.uri, str)
            or not clip.uri
        ):
            raise ValueError("E_CATALOG: Invalid or duplicate clip metadata")
        clip_ids.add(clip.clip_id)


def _prepare_snapshot(
    connection: duckdb.DuckDBPyConnection,
    drives: list[DriveRecord],
    clips: list[ClipRecord],
    run_id: str,
) -> None:
    connection.execute("DELETE FROM current_run")
    connection.execute("INSERT INTO current_run VALUES (true, ?)", [run_id])
    connection.execute(
        "INSERT INTO analysis_rules_history VALUES ($run_id, $rule_version, $stop_speed_mps)",
        {"run_id": run_id, **asdict(AnalysisRules())},
    )
    for drive in drives:
        connection.execute(
            "INSERT INTO drives_history (drive_id, start_ns, period_ns, coordinate_system, run_id) "
            "VALUES ($drive_id, $start_ns, $period_ns, $coordinate_system, $run_id)",
            {**asdict(drive), "run_id": run_id},
        )
    for clip in clips:
        connection.execute(
            "INSERT INTO clips_history (clip_id, drive_id, start_frame, end_frame, uri, run_id) "
            "VALUES ($clip_id, $drive_id, $start_frame, $end_frame, $uri, $run_id)",
            {**asdict(clip), "run_id": run_id},
        )


def _ingest_frames(
    connection: duckdb.DuckDBPyConnection,
    source: Iterable[Frame],
    drives: list[DriveRecord],
    clips: list[ClipRecord],
    run_id: str,
    counts: IngestionCounts,
    stage_seconds: dict[str, float],
) -> IngestionCounts:
    """Validate each input, skip identical repeats, and quarantine conflicts."""
    loop_start = perf_counter()
    accepted_hashes: dict[tuple[str, int], str] = {}

    drive_by_id = {d.drive_id: d for d in drives}
    for source_ordinal, frame in enumerate(source):
        try:
            if not isinstance(frame, Frame):
                raise DataError("E_SCHEMA", "Expected a version 1 Frame")
            drive = drive_by_id.get(frame.drive_id) if isinstance(frame.drive_id, str) else None
            if drive is None:
                raise DataError("E_METADATA", "Frame is outside the declared catalog")
            fact = reduce_frame(frame, drive)
            if not any(
                c.drive_id == frame.drive_id and c.start_frame <= frame.frame_index <= c.end_frame
                for c in clips
            ):
                raise DataError("E_METADATA", "Frame is outside declared clip bounds")

            frame_key = (frame.drive_id, frame.frame_index)
            if frame_key in accepted_hashes:
                if accepted_hashes[frame_key] != fact["content_hash"]:
                    raise DataError(
                        "E_DUPLICATE_CONFLICT", "Later payload conflicts with accepted key"
                    )
                _record_quality_event(
                    connection,
                    source_ordinal=source_ordinal,
                    drive_id=frame.drive_id,
                    frame_index=frame.frame_index,
                    code="W_DUPLICATE",
                    detail="Identical frame skipped",
                    run_id=run_id,
                )
                counts.duplicates += 1
                continue

            load_start = perf_counter()
            _insert_frame(connection, fact, source_ordinal, run_id)
            stage_seconds["load"] += perf_counter() - load_start
            accepted_hashes[frame_key] = fact["content_hash"]
            counts.accepted += 1
        except DataError as error:
            _record_rejection(connection, frame, source_ordinal, error, run_id)
            counts.rejected += 1

    stage_seconds["read_reduce"] = perf_counter() - loop_start - stage_seconds["load"]
    return counts


def _insert_frame(
    connection: duckdb.DuckDBPyConnection, fact: FrameFact, source_ordinal: int, run_id: str
) -> None:
    """Map each named summary field directly to its warehouse column."""
    parameters = {**fact, "source_ordinal": source_ordinal, "run_id": run_id}
    connection.execute(
        """
        INSERT INTO frames_history (
            drive_id, frame_index, timestamp_ns, point_count, content_hash,
            x_m, min_x_m, max_x_m, y_m, min_y_m, max_y_m, z_m, min_z_m, max_z_m,
            source_ordinal, run_id
        ) VALUES (
            $drive_id, $frame_index, $timestamp_ns, $point_count, $content_hash,
            $x_m, $min_x_m, $max_x_m, $y_m, $min_y_m, $max_y_m, $z_m, $min_z_m, $max_z_m,
            $source_ordinal, $run_id
        )
        """,
        parameters,
    )


def _record_quality_event(
    connection: duckdb.DuckDBPyConnection,
    *,
    source_ordinal: int,
    drive_id: str | None,
    frame_index: int | None,
    code: str,
    detail: str,
    run_id: str,
) -> None:
    """Store an input outcome without retaining its potentially huge point array."""
    connection.execute(
        """
        INSERT INTO quality_events_history (source_ordinal, drive_id, frame_index, code, detail,
            run_id)
        VALUES ($source_ordinal, $drive_id, $frame_index, $code, $detail, $run_id)
        """,
        {
            "source_ordinal": source_ordinal,
            "drive_id": drive_id,
            "frame_index": frame_index,
            "code": code,
            "detail": detail,
            "run_id": run_id,
        },
    )


def _record_rejection(
    connection: duckdb.DuckDBPyConnection,
    frame: Frame,
    source_ordinal: int,
    error: DataError,
    run_id: str,
) -> None:
    """Recover usable identity fields even when the source envelope is malformed."""
    drive_id = None
    frame_index = None
    if isinstance(frame, Frame):
        if isinstance(frame.drive_id, str):
            drive_id = frame.drive_id
        if type(frame.frame_index) is int and 0 <= frame.frame_index < MAX_FRAMES_PER_DRIVE:
            frame_index = frame.frame_index

    _record_quality_event(
        connection,
        source_ordinal=source_ordinal,
        drive_id=drive_id,
        frame_index=frame_index,
        code=error.code,
        detail=str(error),
        run_id=run_id,
    )


def _record_missing_frames(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Compare accepted facts with declared clip bounds, including empty clips."""
    connection.execute(
        """
        INSERT INTO quality_events_history (source_ordinal, drive_id, frame_index, code, detail,
            run_id, clip_id)
        SELECT NULL, drive_id, NULL, 'W_MISSING_FRAMES',
            cast(missing_frames AS VARCHAR) || ' expected frames absent', $run_id, clip_id
        FROM clip_summary
        WHERE missing_frames > 0
        """,
        {"run_id": run_id},
    )


def _record_completed_run(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    manifest_json: str,
    counts: IngestionCounts,
    config_hash: str,
    catalog_fingerprint: str,
) -> None:
    """Write audit metadata in the same transaction as the completed snapshot."""
    accepted_keys = connection.execute(
        "SELECT drive_id, frame_index, content_hash FROM frames ORDER BY drive_id, frame_index"
    ).fetchall()
    input_fingerprint = hashlib.sha256(json.dumps(accepted_keys).encode()).hexdigest()
    connection.execute(
        """
        INSERT INTO runs (run_id, manifest, completed_at, accepted, rejected, duplicates,
            config_hash, input_fingerprint, catalog_fingerprint)
        VALUES ($run_id, $manifest, $completed_at, $accepted, $rejected, $duplicates,
            $config_hash, $input_fingerprint, $catalog_fingerprint)
        """,
        {
            "run_id": run_id,
            "manifest": manifest_json,
            "config_hash": config_hash,
            "input_fingerprint": input_fingerprint,
            "catalog_fingerprint": catalog_fingerprint,
            "completed_at": datetime.now(timezone.utc),
            "accepted": counts.accepted,
            "rejected": counts.rejected,
            "duplicates": counts.duplicates,
        },
    )


def query(
    path: Path, sql: str, parameters: list[object] | dict[str, object] | None = None
) -> DataFrame:
    """Run a parameterized read-only query and close its connection immediately."""
    with duckdb.connect(str(path), read_only=True) as connection:
        return connection.execute(sql, parameters or []).fetchdf()


def export_frames(path: Path, destination: Path) -> None:
    """Export accepted facts to a new Parquet file; never overwrite an existing file."""
    validate_export_destination(path, destination)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "frames.parquet"
        with duckdb.connect(str(path), read_only=True) as connection:
            connection.execute(
                "COPY (SELECT * FROM frames ORDER BY drive_id, frame_index) TO ? (FORMAT PARQUET)",
                [str(staged)],
            )
        try:
            # An atomic hard link refuses a target created after preflight too.
            os.link(staged, destination)
        except FileExistsError as error:
            raise ValueError("E_EXPORT: Export target was created concurrently") from error


def _check_quality(
    connection: duckdb.DuckDBPyConnection, counts: IngestionCounts, policy: QualityPolicy
) -> None:
    completeness = connection.execute(
        "SELECT coalesce(sum(missing_frames), 0), coalesce(sum(end_frame-start_frame+1), 0) "
        "FROM clip_summary"
    ).fetchone()
    if completeness is None:
        raise RuntimeError("Completeness aggregate returned no row")
    missing, expected = completeness
    total = counts.accepted + counts.rejected + counts.duplicates
    actuals = {
        "min_accepted_frames": counts.accepted,
        "max_rejected_ratio": counts.rejected / total if total else 0,
        "max_missing_ratio": missing / expected if expected else 0,
    }
    failed = []
    for rule, limit in asdict(policy).items():
        actual = actuals[rule]
        violated = actual < limit if rule == "min_accepted_frames" else actual > limit
        if violated:
            failed.append({"rule": rule, "threshold": limit, "actual": actual})
    if failed:
        rejected_codes = dict(
            connection.execute(
                "SELECT code, count(*) FROM quality_events "
                "WHERE starts_with(code, 'E_') GROUP BY code"
            ).fetchall()
        )
        raise QualityGateError(
            {**asdict(counts), "rejections_by_code": rejected_codes, "failed_rules": failed}
        )


def validate_export_destination(path: Path, destination: Path) -> None:
    if destination.resolve() == path.resolve() or destination.exists():
        raise ValueError("E_EXPORT: Export requires a new path distinct from the database")


def _prune_runs(connection: duckdb.DuckDBPyConnection, retention: int) -> None:
    stale = connection.execute(
        "SELECT run_id FROM runs WHERE run_id != (SELECT run_id FROM current_run) "
        "ORDER BY completed_at DESC, run_id DESC OFFSET ?",
        [retention - 1],
    ).fetchall()
    for (run_id,) in stale:
        for table in (
            "frames_history",
            "drives_history",
            "clips_history",
            "quality_events_history",
            "analysis_rules_history",
            "runs",
        ):
            connection.execute(f"DELETE FROM {table} WHERE run_id=?", [run_id])
