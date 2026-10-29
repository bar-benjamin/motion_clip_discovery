-- Shared local_world reference only. Cells are 10 m squares, including negative XY.
WITH occupancy AS (
    SELECT drive_id, floor(x_m / 10) AS cell_x, floor(y_m / 10) AS cell_y,
           count(*) AS observations
    FROM frames GROUP BY drive_id, cell_x, cell_y
), overlap AS (
    SELECT cell_x, cell_y, count(*) AS drives_in_cell FROM occupancy GROUP BY cell_x, cell_y
)
SELECT occupancy.*, overlap.drives_in_cell
FROM occupancy JOIN overlap USING (cell_x, cell_y)
ORDER BY drive_id, cell_x, cell_y;
