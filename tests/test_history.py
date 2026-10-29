import duckdb
import pytest
from source_fixture import FixtureSource

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_retained_runs_and_failed_attempt_pointer(tmp_path):
    path = tmp_path / "db"
    source = SyntheticSource(Config(frames_per_drive=2, side_cm=2))
    first = ingest(path, source)
    second = ingest(path, source)
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute("select count(*) from frames_history").fetchone() == (16,)
        assert c.execute("select distinct run_id from frames").fetchall() == [(second["run_id"],)]
    with pytest.raises(ValueError, match="E_QUALITY_GATE"):
        ingest(path, FixtureSource(source.config, []))
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute("select run_id from current_run").fetchone() == (second["run_id"],)
        assert c.execute("select count(*) from run_attempts").fetchone() == (1,)
        assert c.execute(
            "select count(*) from frames_history where run_id=?", [first["run_id"]]
        ).fetchone() == (8,)
    third = ingest(path, source, retention=2)
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute("select run_id from runs order by completed_at").fetchall() == [
            (second["run_id"],),
            (third["run_id"],),
        ]
        assert c.execute("select count(*) from frames_history").fetchone() == (16,)


def test_clock_correction_cannot_prune_the_published_run(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import vl_product.warehouse as warehouse

    path = tmp_path / "db"
    source = SyntheticSource(Config(frames_per_drive=2, side_cm=2))
    ingest(path, source)
    monkeypatch.setattr(
        warehouse,
        "datetime",
        SimpleNamespace(now=lambda zone: datetime(2000, 1, 1, tzinfo=timezone.utc)),
    )
    latest = ingest(path, source, retention=1)
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute("select run_id from runs").fetchall() == [(latest["run_id"],)]
        assert c.execute("select count(*) from frames").fetchone() == (8,)
