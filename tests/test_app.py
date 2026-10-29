from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_dashboard_filters_drilldown_and_empty_state(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve())).run(timeout=15)
    assert not app.exception
    assert app.metric[0].value == "4"
    app.sidebar.selectbox[0].select("stationary").run()
    assert not app.exception
    assert app.metric[0].value == "1"
    assert app.selectbox(key="inspect_clip").value == "clip-stationary"
    app.number_input(key="speed_threshold").set_value(20.0).run()
    assert not app.exception
    assert any("No clips match" in message.value for message in app.info)


def test_dashboard_missing_database_has_help(tmp_path, monkeypatch):
    monkeypatch.setenv("VL_DATABASE", str(tmp_path / "absent.duckdb"))
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    assert not app.exception
    assert any("Run the demo" in message.value for message in app.info)


def test_stopped_interval_filter(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    app.sidebar.checkbox[0].check().run()
    assert app.metric[0].value == "2"
    assert set(app.selectbox(key="inspect_clip").options) == {"clip-stationary", "clip-stop_start"}


def test_observation_state_and_search(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    app.selectbox(key="motion_state").select("Unknown").run()
    assert not app.exception
    assert any("4 matching observations" in item.value for item in app.caption)
    app.text_input(key="clip_search").set_value("stationary").run()
    assert app.metric[0].value == "1"
    assert any("1 matching observations" in item.value for item in app.caption)
    app.text_input(key="clip_search").set_value("nothing-matches").run()
    assert app.metric[0].value == "0"
    assert any("No clips match" in item.value for item in app.info)


def test_map_payload_with_hover_fields_and_table_selection(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import streamlit as st

    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    plotly_chart, dataframe = st.plotly_chart, st.dataframe

    def selected_plot(figure, **kwargs):
        if kwargs.get("on_select") == "rerun":
            # Plotly appends hover fields after the authored drive/frame identity.
            payload = list(figure.data[0].customdata[0])
            assert len(payload) > 2
            return SimpleNamespace(selection=SimpleNamespace(points=[{"customdata": payload}]))
        return plotly_chart(figure, **kwargs)

    def selected_table(data, **kwargs):
        if kwargs.get("on_select") == "rerun":
            return SimpleNamespace(selection=SimpleNamespace(rows=[1]))
        return dataframe(data, **kwargs)

    monkeypatch.setattr(st, "plotly_chart", selected_plot)
    monkeypatch.setattr(st, "dataframe", selected_table)
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    assert not app.exception
    headings = [heading.value for heading in app.subheader]
    assert "Selected position · stationary · frame 0" in headings
    assert "Selected row · steady · frame 0" in headings


def test_spatial_bounds_error_and_empty_observations(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    app.checkbox(key="spatial_filter").check().run()
    app.number_input(key="min_x").set_value(100.0).run()
    assert not app.exception
    assert any("Minimum position" in item.value for item in app.error)
    app.number_input(key="max_x").set_value(200.0).run()
    assert not app.exception
    assert any("No observations" in item.value for item in app.info)


def test_unreadable_database_shows_recovery(tmp_path, monkeypatch):
    path = tmp_path / "broken.duckdb"
    path.write_text("not a database")
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    assert not app.exception
    assert any("E_STORAGE" in item.value for item in app.error)


def test_theme_switch_preserves_catalog_filter_in_link(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve()))
    with patch.object(
        type(st.context),
        "theme",
        new_callable=PropertyMock,
        return_value=SimpleNamespace(type="dark"),
    ):
        app.run()
        app.text_input(key="clip_search").set_value("stationary").run()
    assert not app.exception
    assert any(
        "light_theme" in str(item.value) and "stationary" in str(item.value)
        for item in app.get("html")
    )


def test_catalog_filter_links_restore_and_validate_inputs(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    app = AppTest.from_file(str(Path("app.py").resolve()))
    app.query_params.update(drive="stationary", speed="0", stopped="true", search="stationary")
    app.run()
    assert not app.exception
    assert app.metric[0].value == "1"
    assert app.sidebar.selectbox[0].value == "stationary"
    assert app.sidebar.checkbox[0].value is True
    for speed in ["invalid", "nan", "inf", "-1"]:
        invalid = AppTest.from_file(str(Path("app.py").resolve()))
        invalid.query_params.update(drive="missing", speed=speed)
        invalid.run()
        assert not invalid.exception
        assert invalid.metric[0].value == "4"
        assert invalid.sidebar.number_input[0].value == 0.0


def test_chart_totals_and_stationary_context(tmp_path, monkeypatch):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    captured = []
    original = st.plotly_chart

    def capture(figure, **kwargs):
        captured.append(figure)
        return original(figure, **kwargs)

    monkeypatch.setattr(st, "plotly_chart", capture)
    app = AppTest.from_file(str(Path("app.py").resolve())).run()
    app.sidebar.selectbox[0].select("stationary").run()
    assert not app.exception
    content = "\n".join(str(item.value) for item in app.get("html"))
    assert "Stopped: 7 of 8 observations (87.5%)" in content
    assert "Unknown: 1 of 8 observations (12.5%)" in content
    assert "Moving: 0 of 8 observations (0.0%)" in content
    assert all(figure.data[0].type != "pie" for figure in captured)
    assert any("same XY position" in item.value for item in app.caption)
    assert any("8 observations" in item.value for item in app.caption)
