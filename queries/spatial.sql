-- Centroid inside a local-world region; these are meters, not GPS coordinates.
SELECT drive_id, count(*) AS frames_in_region
FROM frames WHERE x_m BETWEEN 10 AND 50 AND y_m BETWEEN 0 AND 10
GROUP BY drive_id ORDER BY drive_id;
