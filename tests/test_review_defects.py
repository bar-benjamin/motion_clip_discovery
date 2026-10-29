from dataclasses import replace

import duckdb
import pandas as pd

from vl_product.exploration import ObservationFilter, explore
from vl_product.quality import QualityPolicy
from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_imported_dashboard_palette_follows_each_session(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path.cwd()))
    import app

    charts = []
    from types import SimpleNamespace

    column = SimpleNamespace(plotly_chart=lambda chart, **kwargs: charts.append(chart))
    monkeypatch.setattr(app.st, "columns", lambda count: [column] * count)
    data = pd.DataFrame(
        {
            "frame_index": [0, 1],
            "x_m": [0, 1],
            "y_m": [0, 0],
            "z_m": [0, 0],
            "speed_mps": [None, 16],
        }
    )
    for theme in ["light", "dark"]:
        monkeypatch.setattr(app, "_selected_theme", lambda theme=theme: theme)
        app._render_motion_charts(data)
    assert charts[1].data[0].line.color != charts[3].data[0].line.color


def test_existing_export_prevents_ingestion(tmp_path, monkeypatch, capsys):
    import sys

    from vl_product.cli import main

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    before = path.read_bytes()
    target = tmp_path / "exists"
    target.write_text("keep")
    monkeypatch.setattr(
        sys, "argv", ["vl-demo", "--database", str(path), "--side-cm", "2", "--export", str(target)]
    )
    assert main() == 2
    assert path.read_bytes() == before
    assert "E_EXPORT" in capsys.readouterr().err


def test_empty_selection_is_typed_empty_without_bypassing_validation(tmp_path):
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    result = explore(path, [], ObservationFilter())
    assert all(value.empty for value in result.values())
    assert "content_hash" in result["frames"].columns
    assert "state" in result["states"].columns
    assert "elapsed_seconds" in result["trend"].columns


def test_multi_clip_warning_conflict_and_original_payload(tmp_path):
    from test_contracts import ArchiveSource

    from vl_product.etl import reduce_frame

    source = ArchiveSource()
    inputs = list(source.frames())
    original = inputs[0]

    class Corrupted(ArchiveSource):
        def frames(self):
            return iter(
                [f for f in inputs if not (f.drive_id == "a" and f.frame_index == 1)]
                + [replace(original, points_cm=original.points_cm + 100)]
            )

    path = tmp_path / "db"
    ingest(path, Corrupted(), QualityPolicy(0, 1, 1))
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute(
            "select clip_id from quality_events where code='W_MISSING_FRAMES' order by 1"
        ).fetchall() == [("a1",), ("a2",)]
        assert c.execute(
            "select conflicting_inputs from clip_summary where clip_id='a1'"
        ).fetchone() == (1,)
        fact = reduce_frame(original, source.drives()[0])
        assert c.execute(
            "select x_m, content_hash from frames where drive_id='a' and frame_index=0"
        ).fetchone() == (fact["x_m"], fact["content_hash"])
