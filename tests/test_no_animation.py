from pathlib import Path

from streamlit.testing.v1 import AppTest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_dashboard_has_no_executable_animation_dependency(tmp_path, monkeypatch):
    assert not Path("static/dashboard-motion.js").exists()
    assert not Path("package.json").exists()
    assert "unsafe_allow_javascript" not in Path("app.py").read_text()
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(Path("app.py").resolve()).run()
    assert not app.exception
    assert not app.error
    assert app.metric[0].value == "4"
