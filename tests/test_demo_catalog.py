"""The dashboard can prepare a cached, isolated synthetic catalog."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from vl_product.source import Config
from vl_product.warehouse import query


def test_demo_catalog_is_shared_and_isolated(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd()))
    import demo_catalog

    demo_catalog.demo_catalog.clear()
    monkeypatch.setattr(demo_catalog, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(demo_catalog, "Config", lambda: Config(frames_per_drive=8, side_cm=2))
    first = demo_catalog.demo_catalog()
    assert first == tmp_path / "catalog.duckdb"
    assert query(first, "SELECT count(*) AS n FROM frames").iloc[0]["n"] == 32

    def unexpected_ingestion(*args):
        raise AssertionError("Cached catalog must not be rebuilt")

    monkeypatch.setattr(demo_catalog, "ingest", unexpected_ingestion)
    assert demo_catalog.demo_catalog() == first
    demo_catalog.demo_catalog.clear()


def test_default_entrypoint_bootstraps_missing_catalog(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd()))
    import demo_catalog

    demo_catalog.demo_catalog.clear()
    monkeypatch.setattr(demo_catalog, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(demo_catalog, "Config", lambda: Config(frames_per_drive=8, side_cm=2))
    database = demo_catalog.demo_catalog()
    monkeypatch.delenv("VL_DATABASE", raising=False)
    entrypoint = str(Path("app.py").resolve())
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(entrypoint).run(timeout=15)
    assert not app.exception
    assert app.metric[0].value == "4"
    assert any("32 / 32" in item.value for item in app.markdown)
    assert database.is_file()
    demo_catalog.demo_catalog.clear()


def test_default_entrypoint_reports_bootstrap_failure(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd()))
    import demo_catalog

    def fail():
        raise OSError("disk unavailable")

    monkeypatch.setattr(demo_catalog, "demo_catalog", fail)
    monkeypatch.delenv("VL_DATABASE", raising=False)
    entrypoint = str(Path("app.py").resolve())
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(entrypoint).run()
    assert not app.exception
    assert any("Cannot prepare" in error.value for error in app.error)
    assert len(app.metric) == 0
