"""Read-only observation exploration over the warehouse's existing motion view."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from vl_product.contracts import MAX_FRAMES_PER_DRIVE
from vl_product.warehouse import query

if TYPE_CHECKING:
    from pandas import DataFrame


@dataclass(frozen=True)
class ObservationFilter:
    """Inclusive local-world bounds; state describes a sample, not an incident."""

    start_frame: int = 0
    end_frame: int = MAX_FRAMES_PER_DRIVE - 1
    state: str = "All states"
    min_x: float = -10_000_000
    max_x: float = 10_000_000
    min_y: float = -10_000_000
    max_y: float = 10_000_000


# Select after motion is calculated: a time filter must not change prior-frame speed.
OBSERVATIONS = """
WITH labeled AS (
    SELECT motion.*,
           motion_state AS state
    FROM motion
    WHERE EXISTS (
        SELECT 1 FROM clips
        WHERE clips.clip_id IN (SELECT unnest($clip_ids))
          AND clips.drive_id = motion.drive_id
          AND motion.frame_index BETWEEN clips.start_frame AND clips.end_frame
    )
), selected AS (
    SELECT * FROM labeled
    WHERE frame_index BETWEEN $start_frame AND $end_frame
      AND x_m BETWEEN $min_x AND $max_x
      AND y_m BETWEEN $min_y AND $max_y
      AND ($state = 'All states' OR state = $state)
)
"""


def explore(path: Path, clip_ids: list[str], filters: ObservationFilter) -> dict[str, "DataFrame"]:
    """Return bounded detail plus full-selection counts and time-bin summaries.

    The plot/table preview is capped independently of aggregate calculations.
    No writes, interpolation, or recalculation of source motion take place here.
    """
    values = asdict(filters)
    bounds = (filters.min_x, filters.max_x, filters.min_y, filters.max_y)
    if (
        not all(isfinite(value) for value in bounds)
        or filters.min_x > filters.max_x
        or filters.min_y > filters.max_y
        or not 0 <= filters.start_frame <= filters.end_frame <= MAX_FRAMES_PER_DRIVE - 1
    ):
        raise ValueError("Invalid observation bounds")
    if filters.state not in ("All states", "Moving", "Stopped", "Unknown"):
        raise ValueError("Invalid observation state")
    parameters = {**values, "clip_ids": clip_ids}
    with duckdb.connect(str(path), read_only=True) as connection:
        connection.execute("BEGIN TRANSACTION")
        detail = connection.execute(
            OBSERVATIONS
            + """
            SELECT drive_id, frame_index, timestamp_ns, x_m, y_m, z_m,
                   speed_mps, state, point_count, content_hash, run_id
            FROM selected ORDER BY frame_index, drive_id LIMIT 5000
        """,
            parameters,
        ).fetchdf()
        states = connection.execute(
            OBSERVATIONS
            + """
            SELECT state, count(*) AS frames FROM selected GROUP BY state ORDER BY state
        """,
            parameters,
        ).fetchdf()
        trend = connection.execute(
            OBSERVATIONS
            + """
            , extent AS (
                SELECT greatest(1e-9, (max(elapsed_seconds) - min(elapsed_seconds)) / 100.0)
                       AS bin_seconds FROM selected
            )
            SELECT floor(elapsed_seconds / bin_seconds) * bin_seconds AS elapsed_seconds,
                   count(*) AS frames, avg(speed_mps) AS mean_speed_mps
            FROM selected CROSS JOIN extent
            GROUP BY 1 ORDER BY 1
        """,
            parameters,
        ).fetchdf()
        return {"frames": detail, "states": states, "trend": trend}


DISCOVERY_SQL = """
WITH durations AS (
    SELECT clip.clip_id,
           coalesce(sum(m.interval_seconds) FILTER (
               WHERE m.frame_index > clip.start_frame AND m.speed_mps >= $threshold
           ), 0) AS seconds_at_or_above
    FROM clips clip LEFT JOIN motion m
      ON clip.drive_id=m.drive_id AND clip.run_id=m.run_id
     AND m.frame_index BETWEEN clip.start_frame AND clip.end_frame
    GROUP BY clip.clip_id
)
SELECT summary.*, durations.seconds_at_or_above,
       (end_frame-start_frame) * drives.period_ns / 1e9 AS duration_seconds,
       CASE WHEN peak_speed_mps IS NULL THEN 'Unknown'
            WHEN moving_seconds = 0 THEN 'Stopped' ELSE 'Moving' END AS motion_status
FROM clip_summary summary JOIN durations USING (clip_id)
JOIN drives USING (drive_id)
WHERE ($drive = 'All drives' OR drive_id=$drive)
  AND seconds_at_or_above >= $minimum_seconds
  AND ($threshold=0 OR seconds_at_or_above>0)
  AND (NOT $stopped_only OR stopped_seconds>0)
ORDER BY seconds_at_or_above DESC, p90_speed_mps DESC NULLS LAST, clip_id
"""


def find_clips(
    path: Path,
    drive_id: str = "All drives",
    speed_threshold: float = 0,
    minimum_seconds: float = 0,
    stopped_only: bool = False,
) -> DataFrame:
    for value in (speed_threshold, minimum_seconds):
        if not isfinite(value) or value < 0:
            raise ValueError("Speed and duration thresholds must be finite and nonnegative")
    return query(
        path,
        DISCOVERY_SQL,
        {
            "drive": drive_id,
            "threshold": speed_threshold,
            "minimum_seconds": minimum_seconds,
            "stopped_only": stopped_only,
        },
    )


BI_SQL = {
    "clip_detail": """
        SELECT frame_index, timestamp_ns, x_m, y_m, z_m, speed_mps,
               point_count, content_hash, source_ordinal, run_id
        FROM motion
        WHERE drive_id = $drive_id
          AND frame_index BETWEEN $start_frame AND $end_frame
        ORDER BY frame_index
        LIMIT 5000
        """,
    "quality_events": """SELECT * FROM quality_events
        ORDER BY source_ordinal NULLS LAST LIMIT 1000""",
    "drive_ids": """SELECT drive_id FROM drives ORDER BY drive_id""",
    "current_metadata": """SELECT r.* FROM runs r JOIN current_run c ON r.run_id=c.run_id""",
    "rules": """SELECT stop_speed_mps FROM analysis_rules""",
    "spatial_extent": """SELECT min(x_m), max(x_m), min(y_m), max(y_m) FROM frames""",
}


def read_bi(path: Path, name: str, parameters: dict[str, object] | None = None) -> DataFrame:
    """Use only named internal SQL; user values remain bound parameters."""
    return query(path, BI_SQL[name], parameters)


def catalog_tiles(
    path: Path, clip_ids: list[str], minimum_speed: float = 0.0
) -> dict[str, "DataFrame"]:
    """Read full matching-clip metrics and whole-snapshot quality counts.

    Frame and distance totals are summed across clips, so overlapping clips
    contribute separately. Observation exploration instead counts each frame once.
    Duration is the elapsed span between clip endpoints, including any gaps.
    A Moving clip has at least one moving interval; it may also contain stops.
    """
    if not isfinite(minimum_speed) or minimum_speed < 0:
        raise ValueError("Invalid minimum-speed threshold")
    selected = """
        WITH selected AS (
            SELECT s.*, d.period_ns FROM clip_summary s JOIN drives d USING (drive_id)
            WHERE clip_id IN (SELECT unnest($clip_ids))
        )
    """
    parameters: dict[str, object] = {"clip_ids": clip_ids}
    with duckdb.connect(str(path), read_only=True) as connection:
        connection.execute("BEGIN TRANSACTION")
        summary = connection.execute(
            selected
            + """
            SELECT count(*) AS clips,
                   coalesce(sum(accepted_frames), 0) AS accepted_frames,
                   coalesce(sum(missing_frames), 0) AS missing_frames,
                   coalesce(sum(observed_distance_m), 0) AS observed_distance_m,
                   min(peak_speed_mps) AS min_peak_speed_mps,
                   max(peak_speed_mps) AS max_peak_speed_mps,
                   count(*) FILTER (WHERE peak_speed_mps >= $minimum_speed) AS threshold_clips,
                   avg((end_frame - start_frame) * period_ns / 1e9) AS mean_duration_seconds
            FROM selected
            """,
            {**parameters, "minimum_speed": minimum_speed},
        ).fetchdf()
        drives = connection.execute(
            selected
            + """
            SELECT drives.drive_id, count(*) AS clips,
                   sum(selected.accepted_frames) AS accepted_frames,
                   sum(selected.observed_distance_m) AS observed_distance_m
            FROM selected JOIN drives ON selected.drive_id = drives.drive_id
            GROUP BY drives.drive_id ORDER BY drives.drive_id
            """,
            parameters,
        ).fetchdf()
        clips = connection.execute(
            selected
            + """
            SELECT *, (end_frame - start_frame) * period_ns / 1e9 AS duration_seconds,
                   CASE WHEN peak_speed_mps IS NULL THEN 'Unknown'
                        WHEN moving_seconds = 0 THEN 'Stopped'
                        ELSE 'Moving' END AS motion_status
            FROM selected ORDER BY start_frame, drive_id, clip_id
            """,
            parameters,
        ).fetchdf()
        quality = connection.execute(
            "SELECT code, count(*) AS events FROM quality_events GROUP BY code ORDER BY code",
        ).fetchdf()
        return {"summary": summary, "drives": drives, "clips": clips, "quality": quality}
