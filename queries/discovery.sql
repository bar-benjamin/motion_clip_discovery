-- Accumulated exposure time, not a promise of one uninterrupted interval.
-- Example: at least one second at or above 10 m/s.
SELECT c.*, coalesce(sum(m.interval_seconds) FILTER (
    WHERE m.frame_index > c.start_frame AND m.speed_mps >= 10
), 0) AS seconds_at_or_above
FROM clip_summary c LEFT JOIN motion m ON c.drive_id=m.drive_id
 AND m.frame_index BETWEEN c.start_frame AND c.end_frame
GROUP BY ALL
HAVING seconds_at_or_above >= 1
ORDER BY seconds_at_or_above DESC, p90_speed_mps DESC NULLS LAST, clip_id;
