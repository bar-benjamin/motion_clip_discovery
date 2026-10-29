import duckdb

from vl_product.exploration import ObservationFilter, explore
from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_rule_changes_all_sql_consumers(tmp_path):
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(side_cm=2)))
    with duckdb.connect(str(path)) as c:
        assert c.execute(
            "select motion_state, count(*) from motion group by 1 order by 1"
        ).fetchall() == [("Moving", 622), ("Stopped", 374), ("Unknown", 4)]
        c.execute("update analysis_rules_history set stop_speed_mps=20")
        assert c.execute("select count(*) from motion where motion_state='Moving'").fetchone() == (
            0,
        )
        assert c.execute(
            "select stopped_seconds from clip_summary where drive_id='steady'"
        ).fetchone() == (249 / 16,)
    result = explore(path, ["clip-steady"], ObservationFilter())
    assert dict(zip(result["states"].state, result["states"].frames, strict=True)) == {
        "Stopped": 249,
        "Unknown": 1,
    }


def test_dashboard_caption_uses_stored_threshold(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=4, side_cm=2)))
    with duckdb.connect(str(path)) as c:
        c.execute("UPDATE analysis_rules_history SET stop_speed_mps=20")
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(Path("app.py").resolve()).run(timeout=15)
    assert not app.exception
    assert any("≤20 m/s" in caption.value for caption in app.caption)


def test_elapsed_time_uses_each_drive_clock(tmp_path):
    from test_contracts import ArchiveSource

    path = tmp_path / "db"
    ingest(path, ArchiveSource())
    result = explore(path, ["b1"], ObservationFilter())
    assert result["trend"].elapsed_seconds.max() == 1.0
