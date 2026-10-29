-- Snapshot tables store source facts separately from derived analytical views.
CREATE TABLE IF NOT EXISTS vl_schema (
    version INTEGER PRIMARY KEY
);
INSERT INTO vl_schema VALUES (3) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR PRIMARY KEY,
    manifest JSON NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    accepted INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    config_hash VARCHAR NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    catalog_fingerprint VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS drives_history (
    drive_id VARCHAR NOT NULL,
    start_ns BIGINT NOT NULL,
    period_ns BIGINT NOT NULL,
    coordinate_system VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (run_id, drive_id)
);

CREATE TABLE IF NOT EXISTS clips_history (
    clip_id VARCHAR NOT NULL,
    drive_id VARCHAR NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    uri VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (run_id, clip_id),
    CHECK (start_frame >= 0 AND end_frame >= start_frame)
);

CREATE TABLE IF NOT EXISTS frames_history (
    drive_id VARCHAR NOT NULL,
    frame_index INTEGER NOT NULL CHECK (frame_index >= 0),
    timestamp_ns BIGINT NOT NULL,
    point_count INTEGER NOT NULL CHECK (point_count > 0),
    content_hash VARCHAR NOT NULL,
    x_m DOUBLE NOT NULL,
    min_x_m DOUBLE NOT NULL,
    max_x_m DOUBLE NOT NULL,
    y_m DOUBLE NOT NULL,
    min_y_m DOUBLE NOT NULL,
    max_y_m DOUBLE NOT NULL,
    z_m DOUBLE NOT NULL,
    min_z_m DOUBLE NOT NULL,
    max_z_m DOUBLE NOT NULL,
    source_ordinal INTEGER NOT NULL,
    run_id VARCHAR NOT NULL,
    PRIMARY KEY (run_id, drive_id, frame_index)
);

CREATE TABLE IF NOT EXISTS quality_events_history (
    clip_id VARCHAR,
    source_ordinal INTEGER,
    drive_id VARCHAR,
    frame_index INTEGER,
    code VARCHAR NOT NULL,
    detail VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_rules_history (
    run_id VARCHAR PRIMARY KEY,
    rule_version INTEGER NOT NULL,
    stop_speed_mps DOUBLE NOT NULL CHECK (stop_speed_mps >= 0)
);

CREATE TABLE IF NOT EXISTS current_run (singleton BOOLEAN PRIMARY KEY CHECK(singleton), run_id VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS run_attempts (
    run_id VARCHAR PRIMARY KEY, failed_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL, reason VARCHAR NOT NULL, counts JSON NOT NULL
);

CREATE OR REPLACE VIEW drives AS SELECT h.* FROM drives_history h JOIN current_run c ON h.run_id=c.run_id;
CREATE OR REPLACE VIEW clips AS SELECT h.* FROM clips_history h JOIN current_run c ON h.run_id=c.run_id;
CREATE OR REPLACE VIEW frames AS SELECT h.* FROM frames_history h JOIN current_run c ON h.run_id=c.run_id;
CREATE OR REPLACE VIEW quality_events AS SELECT h.* FROM quality_events_history h JOIN current_run c ON h.run_id=c.run_id;
CREATE OR REPLACE VIEW analysis_rules AS SELECT h.* FROM analysis_rules_history h JOIN current_run c ON h.run_id=c.run_id;
-- Order by source time/index, not arrival order. LAG never crosses drive boundaries.
CREATE OR REPLACE VIEW motion AS
WITH previous AS (
    SELECT
        *,
        lag(frame_index) OVER drive_timeline AS previous_index,
        lag(timestamp_ns) OVER drive_timeline AS previous_ns,
        lag(x_m) OVER drive_timeline AS previous_x,
        lag(y_m) OVER drive_timeline AS previous_y,
        lag(z_m) OVER drive_timeline AS previous_z
    FROM frames
    WINDOW drive_timeline AS (PARTITION BY drive_id ORDER BY frame_index)
),
intervals AS (
    SELECT
        *,
        -- Missing predecessors stay NULL: interpolating would hide lost observations.
        CASE WHEN frame_index = previous_index + 1 THEN
            sqrt(
                pow(x_m - previous_x, 2)
                + pow(y_m - previous_y, 2)
                + pow(z_m - previous_z, 2)
            )
        END AS distance_m,
        CASE WHEN frame_index = previous_index + 1 THEN
            (timestamp_ns - previous_ns) / 1e9
        END AS interval_seconds
    FROM previous
)
SELECT intervals.*, make_timestamp_ns(timestamp_ns) AS acquisition_ts,
    distance_m / interval_seconds AS speed_mps,
    CASE WHEN distance_m IS NULL THEN 'Unknown'
         WHEN distance_m / interval_seconds <= rules.stop_speed_mps THEN 'Stopped'
         ELSE 'Moving' END AS motion_state,
    (timestamp_ns - drives.start_ns) / 1e9 AS elapsed_seconds
FROM intervals
JOIN analysis_rules AS rules ON rules.run_id = intervals.run_id
JOIN drives ON drives.drive_id = intervals.drive_id AND drives.run_id = intervals.run_id;

-- Exclude an interval ending on the clip's first frame: it begins outside the clip.
-- COALESCE applies to observed totals, while unknown speed remains NULL.
CREATE OR REPLACE VIEW clip_summary AS
SELECT
    clip.clip_id,
    clip.drive_id,
    clip.start_frame,
    clip.end_frame,
    clip.uri,
    (SELECT count(*) FROM quality_events q WHERE q.run_id=clip.run_id
       AND q.drive_id=clip.drive_id AND q.frame_index BETWEEN clip.start_frame AND clip.end_frame
       AND q.code='E_DUPLICATE_CONFLICT') AS conflicting_inputs,
    count(frame.frame_index) AS accepted_frames,
    clip.end_frame - clip.start_frame + 1 - count(frame.frame_index) AS missing_frames,
    max(frame.speed_mps) FILTER (
        WHERE frame.frame_index > clip.start_frame
    ) AS peak_speed_mps,
    quantile_cont(frame.speed_mps, 0.9) FILTER (
        WHERE frame.frame_index > clip.start_frame
    ) AS p90_speed_mps,
    coalesce(sum(frame.interval_seconds) FILTER (
        WHERE frame.frame_index > clip.start_frame AND frame.motion_state = 'Moving'
    ), 0) AS moving_seconds,
    avg(frame.speed_mps) FILTER (
        WHERE frame.frame_index > clip.start_frame
    ) AS mean_speed_mps,
    coalesce(sum(frame.distance_m) FILTER (
        WHERE frame.frame_index > clip.start_frame
    ), 0) AS observed_distance_m,
    coalesce(sum(frame.interval_seconds) FILTER (
        WHERE frame.frame_index > clip.start_frame AND frame.motion_state = 'Stopped'
    ), 0) AS stopped_seconds,
    coalesce(sum(frame.interval_seconds) FILTER (
        WHERE frame.frame_index > clip.start_frame
    ), 0) AS observed_seconds
FROM clips AS clip
LEFT JOIN motion AS frame
    ON clip.drive_id = frame.drive_id
    AND frame.frame_index BETWEEN clip.start_frame AND clip.end_frame
GROUP BY clip.clip_id, clip.drive_id, clip.start_frame, clip.end_frame, clip.uri, clip.run_id;
