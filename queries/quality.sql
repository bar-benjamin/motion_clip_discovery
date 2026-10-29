-- Rates count input records, including duplicate/conflicting records.
WITH codes AS (
    SELECT drive_id, code, count(*) AS events FROM quality_events GROUP BY drive_id, code
), accepted AS (
    SELECT drive_id, count(*) AS accepted FROM frames GROUP BY drive_id
), quality AS (
    SELECT drive_id,
       sum(events) FILTER (WHERE starts_with(code, 'E_')) AS rejected,
       sum(events) FILTER (WHERE code='W_DUPLICATE') AS duplicates,
       sum(events) FILTER (WHERE code='E_DUPLICATE_CONFLICT') AS conflicts
    FROM codes GROUP BY drive_id
), completeness AS (
    SELECT drive_id, sum(missing_frames) AS missing_frames,
           sum(end_frame-start_frame+1) AS expected_frames FROM clip_summary GROUP BY drive_id
), jumps AS (
    -- 70 m/s is a diagnostic threshold (252 km/h), not a rejection rule.
    SELECT drive_id, count(*) FILTER (WHERE speed_mps>70) AS implausible_jumps
    FROM motion GROUP BY drive_id
)
SELECT d.drive_id, coalesce(a.accepted,0) AS accepted, coalesce(q.rejected,0) AS rejected,
       coalesce(q.duplicates,0) AS duplicates, coalesce(q.conflicts,0) AS conflicts,
       coalesce(q.rejected,0)::DOUBLE / greatest(1,coalesce(a.accepted,0)+
           coalesce(q.rejected,0)+coalesce(q.duplicates,0)) AS rejected_ratio,
       coalesce(c.missing_frames,0)::DOUBLE / greatest(1,coalesce(c.expected_frames,0)) AS missing_ratio,
       coalesce(j.implausible_jumps,0) AS implausible_jumps,
       coalesce((SELECT to_json(map(list(code ORDER BY code),list(events ORDER BY code)))
                 FROM codes WHERE codes.drive_id IS NOT DISTINCT FROM d.drive_id), '{}') AS events_by_code
FROM (SELECT drive_id FROM drives UNION SELECT drive_id FROM quality_events) d
LEFT JOIN accepted a ON d.drive_id IS NOT DISTINCT FROM a.drive_id
LEFT JOIN quality q ON d.drive_id IS NOT DISTINCT FROM q.drive_id
LEFT JOIN completeness c ON d.drive_id IS NOT DISTINCT FROM c.drive_id
LEFT JOIN jumps j ON d.drive_id IS NOT DISTINCT FROM j.drive_id
ORDER BY d.drive_id NULLS LAST;
