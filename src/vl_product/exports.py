"""Self-describing exports from one committed warehouse read snapshot."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import duckdb

from vl_product.warehouse import validate_export_destination


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def export_snapshot(path: Path, destination: Path) -> None:
    """Claim a new directory exclusively; manifest.json marks completed publication.

    Readers must require the final manifest and verify checksums. Failure removes
    only files created by this call, then releases its empty directory.
    """
    validate_export_destination(path, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError as error:
        raise ValueError("E_EXPORT: Snapshot destination already exists") from error
    created: list[Path] = []
    try:
        with duckdb.connect(str(path), read_only=True) as connection:
            connection.execute("BEGIN TRANSACTION")
            record = connection.execute(
                "SELECT to_json(r)::VARCHAR FROM runs r JOIN current_run c ON r.run_id=c.run_id"
            ).fetchone()
            if record is None:
                raise ValueError("E_EXPORT: No completed snapshot")
            run = json.loads(record[0])
            version = connection.execute("SELECT version FROM vl_schema").fetchone()
            if version is None:
                raise ValueError("E_DATABASE: Missing schema version")
            files: dict[str, Any] = {}
            with TemporaryDirectory(dir=destination.parent) as temporary:
                for table in ("frames", "drives", "clips", "quality_events", "analysis_rules"):
                    temp = Path(temporary) / f"{table}.parquet"
                    connection.execute(
                        f"COPY (SELECT * FROM {table}) TO ? (FORMAT PARQUET)", [str(temp)]
                    )
                    target = destination / temp.name
                    with target.open("xb") as output, temp.open("rb") as source:
                        created.append(target)
                        shutil.copyfileobj(source, output)
                    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
                    if row is None:
                        raise RuntimeError("Missing count aggregate")
                    files[temp.name] = {"rows": row[0], "sha256": _file_hash(target)}
            manifest = {"schema_version": version[0], **run, "files": files}
            target = destination / "manifest.json"
            with target.open("x") as output:
                created.append(target)
                output.write(json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n")
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        destination.rmdir()
        raise


def build_selection_manifest(path: Path, criteria: dict[str, Any]) -> dict[str, Any]:
    """Identify a selected reduced dataset; no raw-data reconstruction is implied."""
    from datetime import datetime, timezone
    from math import isfinite

    from vl_product.bi_queries import DISCOVERY_SQL

    defaults: dict[str, Any] = {
        "drive_id": "All drives",
        "speed_threshold": 0.0,
        "minimum_seconds": 0.0,
        "stopped_only": False,
        "search": "",
    }
    if criteria.keys() - defaults.keys():
        raise ValueError("Unknown selection criteria")
    selected = {**defaults, **criteria}
    for key in ("speed_threshold", "minimum_seconds"):
        value = selected[key]
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError("Invalid numeric selection criterion")
        selected[key] = float(value)
    if (
        not isinstance(selected["drive_id"], str)
        or not isinstance(selected["search"], str)
        or type(selected["stopped_only"]) is not bool
    ):
        raise ValueError("Invalid selection criteria")
    parameters = {
        "drive": selected["drive_id"],
        "threshold": selected["speed_threshold"],
        "minimum_seconds": selected["minimum_seconds"],
        "stopped_only": selected["stopped_only"],
    }
    with duckdb.connect(str(path), read_only=True) as connection:
        connection.execute("BEGIN TRANSACTION")
        record = connection.execute(
            "SELECT to_json(r)::VARCHAR FROM runs r JOIN current_run c ON r.run_id=c.run_id"
        ).fetchone()
        if record is None:
            raise ValueError("E_EXPORT: No completed run to select")
        run = json.loads(record[0])
        rows = connection.execute(
            "SELECT to_json(s)::VARCHAR FROM (" + DISCOVERY_SQL + ") s", parameters
        ).fetchall()
        clips = [json.loads(row[0]) for row in rows]
        search = selected["search"].lower()
        clips = [
            clip
            for clip in clips
            if search in clip["clip_id"].lower() or search in clip["drive_id"].lower()
        ]
        rules = connection.execute("SELECT to_json(r)::VARCHAR FROM analysis_rules r").fetchone()
        version = connection.execute("SELECT version FROM vl_schema").fetchone()
        if rules is None or version is None:
            raise ValueError("E_DATABASE: Missing selection metadata")
    body = {
        "criteria": selected,
        "run_id": run["run_id"],
        "input_fingerprint": run["input_fingerprint"],
        "catalog_fingerprint": run["catalog_fingerprint"],
        "config_hash": run["config_hash"],
        "schema_version": version[0],
        "analysis_rules": json.loads(rules[0]),
        "clips": clips,
    }
    checksum = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {**body, "sha256": checksum, "created_at": datetime.now(timezone.utc).isoformat()}
