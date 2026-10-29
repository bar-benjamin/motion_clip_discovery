import re
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def dashboard(tmp_path, monkeypatch, count=8):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=count, side_cm=2)
    ingest(path, SyntheticSource(config))
    monkeypatch.setenv("VL_DATABASE", str(path))
    return path, AppTest.from_file(str(Path("app.py").resolve()))


def html(app):
    return "\n".join(str(item.value) for item in app.get("html"))


def test_bento_tiles_scopes_and_real_values(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    app.run()
    assert not app.exception
    content = html(app)
    for title in [
        "Motion catalog",
        "Drive cards",
        "Frame completeness",
        "Motion states",
        "Snapshot quality",
        "Peak-speed band",
        "Clip stream",
        "Top clips",
    ]:
        assert title in content
    assert "Whole snapshot" in content
    assert "Catalog" in content and "Observation" in content
    assert "32 received · 0 missing" in content
    assert "100.0%" in content
    assert "No quality events" in content
    assert "<script" not in content


def test_drive_pills_update_catalog_and_front_card(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    app.run()
    app.button_group(key="drive_pills").set_value("stationary").run()
    assert not app.exception
    assert app.sidebar.selectbox[0].value == "stationary"
    assert app.metric[0].value == "1"
    assert "Front drive: stationary" in html(app)


def test_empty_observation_selection_keeps_catalog_and_export(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    app.run()
    app.sidebar.selectbox[0].select("steady").run()
    app.selectbox(key="motion_state").select("Stopped").run()
    assert not app.exception
    assert any("No observations" in item.value for item in app.info)
    assert "Snapshot quality" in html(app)
    assert app.metric[0].value == "1"
    assert len(app.get("download_button")) >= 1


def test_unknown_speed_is_hollow_and_not_zero(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch, count=1)
    app.run()
    assert not app.exception
    content = html(app)
    assert "clip-bar unknown" in content
    assert "Peak speed unknown" in content
    assert "No known peak speeds" in content
    assert "Unknown" in content


def test_matching_csv_is_available_in_quality_tile(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    downloads = {}
    original = st.download_button

    def capture(label, data, *args, **kwargs):
        downloads[label] = data
        return original(label, data, *args, **kwargs)

    monkeypatch.setattr(st, "download_button", capture)
    app.run()
    app.sidebar.selectbox[0].select("stationary").run()
    csv = downloads["Download matching clips"]
    assert "clip-stationary" in csv and "clip-steady" not in csv
    assert "Catalog export" in html(app)


def test_theme_link_retains_observation_window_state_and_bounds(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    app.run()
    app.slider(key="frame_window").set_value((2, 5)).run()
    app.selectbox(key="motion_state").select("Moving").run()
    app.checkbox(key="spatial_filter").check().run()
    app.number_input(key="min_x").set_value(-2.0).run()
    link = re.search(r'class="vl-theme"[^>]*href="([^"]+)"', html(app))
    assert link
    params = parse_qs(urlsplit(unescape(link.group(1))).query)
    assert params["start"] == ["2"] and params["end"] == ["5"]
    assert params["state"] == ["Moving"] and params["min_x"] == ["-2.0"]
    restored = AppTest.from_file(str(Path("app.py").resolve()))
    restored.query_params.update({key: values[0] for key, values in params.items()})
    restored.run()
    assert not restored.exception
    assert restored.slider(key="frame_window").value == (2, 5)
    assert restored.selectbox(key="motion_state").value == "Moving"
    assert restored.number_input(key="min_x").value == -2.0


def test_html_escapes_catalog_identifiers(tmp_path, monkeypatch):
    path, app = dashboard(tmp_path, monkeypatch)
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "UPDATE clips_history SET clip_id = $id WHERE drive_id = 'steady'",
            {"id": "<img src=x onerror=alert(1)>"},
        )
    app.run()
    assert not app.exception
    content = html(app)
    assert "&lt;img src=x onerror=alert(1)&gt;" in content
    assert "<img src=x" not in content


def test_equal_peak_range_marks_outside_threshold_at_edge(tmp_path, monkeypatch):
    _, app = dashboard(tmp_path, monkeypatch)
    app.run()
    app.sidebar.selectbox[0].select("stationary").run()
    # Known stationary peak is exactly zero: threshold equals it and is centered.
    assert "--position:50.0%" in html(app)
    app.sidebar.selectbox[0].select("steady").run()
    assert "--position:0.0%" in html(app)


def test_accessible_strip_values_are_not_rounded(monkeypatch):
    monkeypatch.syspath_prepend(str(Path.cwd()))
    import app

    label = "Bin starting 1000.0625 s · 7 observations"
    markup = app._strip([(label, 7)], activity=True)
    assert label in markup


@pytest.mark.parametrize("theme,target", [("dark", "light"), ("light", "dark")])
def test_product_title_and_accessible_theme_icon(tmp_path, monkeypatch, theme, target):
    _, app = dashboard(tmp_path, monkeypatch)
    page_titles = []
    original = st.set_page_config

    def capture(**kwargs):
        page_titles.append(kwargs.get("page_title"))
        return original(**kwargs)

    monkeypatch.setattr(st, "set_page_config", capture)
    with patch.object(
        type(st.context),
        "theme",
        new_callable=PropertyMock,
        return_value=SimpleNamespace(type=theme),
    ):
        app.run()
    assert not app.exception
    assert app.title[0].value == "Motion-based Clip Discovery"
    assert page_titles[-1] == "Motion-based Clip Discovery"
    content = html(app)
    assert f'aria-label="Switch to {target} mode"' in content
    assert f'title="Switch to {target} mode"' in content
    assert 'class="theme-icon" aria-hidden="true"' in content
    assert ">Switch to light theme</a>" not in content
