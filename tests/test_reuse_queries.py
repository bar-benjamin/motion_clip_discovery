from pathlib import Path

import duckdb

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_coverage_and_quality_exact_results(tmp_path):
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute(Path("queries/coverage.sql").read_text()).fetchall() == [
            ("stationary", 0, 1, 2, 1),
            ("steady", 0, 0, 2, 3),
            ("stop_start", 0, 0, 2, 3),
            ("turn", 0, 0, 2, 3),
        ]
        results = c.execute(Path("queries/quality.sql").read_text()).fetchall()
        assert results == [
            (d, 2, 0, 0, 0, 0.0, 0.0, 0, "{}")
            for d in ["stationary", "steady", "stop_start", "turn"]
        ]


def test_quality_query_reports_jump_without_rejection(tmp_path):
    from dataclasses import replace

    from source_fixture import FixtureSource

    from vl_product.source import frames

    config = Config(frames_per_drive=2, side_cm=2)
    inputs = list(frames(config))
    inputs[1] = replace(inputs[1], points_cm=inputs[1].points_cm + 1000)
    path = tmp_path / "db"
    report = ingest(path, FixtureSource(config, inputs))
    assert report["rejected"] == 0
    with duckdb.connect(str(path), read_only=True) as c:
        result = c.execute(Path("queries/quality.sql").read_text()).fetchdf()
    assert result.set_index("drive_id").loc["steady", "implausible_jumps"] == 1
