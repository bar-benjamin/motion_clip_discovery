"""Research dashboard: filters → matching clips → frame detail → quality audit.

main() describes the page flow. Each render function owns one visible section;
SQL continues to own motion and aggregation rules.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from html import escape
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from demo_catalog import demo_catalog
from vl_product.bi_queries import catalog_tiles, read_bi
from vl_product.discovery import find_clips
from vl_product.exploration import ObservationFilter, explore
from vl_product.exports import build_selection_manifest

if TYPE_CHECKING:
    from pandas import DataFrame, Series


@dataclass(frozen=True)
class ClipFilters:
    """The three controls that determine which catalog clips are shown."""

    drive_id: str
    speed_threshold: float
    stopped_only: bool
    minimum_seconds: float = 0


def main(database_path: Path | None = None) -> None:
    """Keep catalog, observation and whole-snapshot scopes visible together."""
    st.set_page_config(
        page_title="Motion-based Clip Discovery",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    dark = _selected_theme() == "dark"
    tokens = {
        "THEME_INK": "#FAFAFA" if dark else "#18181B",
        "THEME_MUTED": "#A1A1AA" if dark else "#52525B",
        "THEME_LINE": "#FFFFFF14" if dark else "#E4E4E7",
        "THEME_SURFACE": "#111113" if dark else "#FFFFFF",
        "THEME_CANVAS": "#09090B" if dark else "#F4F4F5",
        "THEME_ACCENT": "#B4F24E" if dark else "#4D7000",
        "THEME_SECONDARY": "#B699EB" if dark else "#724AAB",
        "THEME_PRIMARY_INK": "#122505" if dark else "#FFFFFF",
    }
    css = Path(__file__).with_name("dashboard.css").read_text()
    for token, value in tokens.items():
        css = css.replace(token, escape(value))
    st.html(css)
    with st.sidebar:
        st.html(
            '<div class="vl-workspace"><b>VL</b><div>Motion-based Clip Discovery'
            "<small>Research workspace</small></div></div>"
        )
    st.title("Motion-based Clip Discovery")
    topbar = st.container(key="topbar")
    if database_path is None:
        configured_database = os.environ.get("VL_DATABASE")
        database_path = Path(
            configured_database if configured_database is not None else "data/catalog.duckdb"
        )
        if configured_database is None and not database_path.is_file():
            try:
                database_path = demo_catalog()
            except (OSError, duckdb.Error) as error:
                st.error(f"E_STORAGE: Cannot prepare the synthetic demo. Details: {error}")
                return
    if not database_path.is_file():
        st.info("Run the demo to create the catalog: uv run vl-demo")
        return
    try:
        drive_rows = read_bi(database_path, "drive_ids")
        stop_speed = float(read_bi(database_path, "rules").iloc[0, 0])
        drive_ids = drive_rows["drive_id"].tolist()
        filters = _render_filters(drive_ids, stop_speed)
        matching_clips = _find_matching_clips(database_path, filters)
        search = st.sidebar.text_input(
            "Search clips",
            key="clip_search",
            value=st.query_params.get("search", ""),
            placeholder="Clip or drive ID",
        )
        selection = build_selection_manifest(
            database_path,
            {
                "drive_id": filters.drive_id,
                "speed_threshold": filters.speed_threshold,
                "minimum_seconds": filters.minimum_seconds,
                "stopped_only": filters.stopped_only,
                "search": search,
            },
        )
        st.sidebar.download_button(
            "Download selection manifest",
            json.dumps(selection, indent=2),
            "selection.json",
            "application/json",
            key="selection_manifest",
            on_click="ignore",
        )
        if search:
            matching_clips = matching_clips[
                matching_clips["clip_id"].str.contains(search, case=False, regex=False)
                | matching_clips["drive_id"].str.contains(search, case=False, regex=False)
            ]
        runs = read_bi(database_path, "current_metadata")
        if runs.empty:
            st.info("No completed snapshot. Run the demo to populate the catalog.")
            return
        completed_run = runs.iloc[0]
        if matching_clips.empty:
            st.info("No clips match these filters. Clear search or adjust the catalog filters.")
        observations, observation_filters = _observation_data(database_path, matching_clips)
        with topbar:
            _render_theme_switch(filters, search, completed_run)
        tiles = catalog_tiles(
            database_path, matching_clips["clip_id"].tolist(), filters.speed_threshold
        )
        _render_bento(
            tiles,
            matching_clips,
            completed_run,
            observations,
            observation_filters,
            filters,
            drive_ids,
            stop_speed,
        )
        if observations is not None and not observations["frames"].empty:
            _render_results(observations["frames"], matching_clips, observation_filters)
        with st.container(key="clip_explorer"):
            _tile_header("Clip explorer", "Catalog")
            _render_clip_explorer(database_path, matching_clips)
        with st.container(key="quality_provenance"):
            _tile_header("Quality & provenance", "Whole snapshot")
            _render_quality_tab(database_path, completed_run)
    except (duckdb.Error, OSError) as error:
        st.error(
            "E_STORAGE: Cannot read the catalog. Stop ingestion before opening BI. "
            f"Details: {error}"
        )


def _render_filters(drive_ids: list[str], stop_speed: float) -> ClipFilters:
    """Keep visible controls and their explanations together in the sidebar."""
    with st.sidebar:
        st.subheader("Catalog filters")
        options = ["All drives", *drive_ids]
        requested_drive = st.query_params.get("drive", "All drives")
        drive_id = st.selectbox(
            "Drive",
            options,
            index=options.index(requested_drive) if requested_drive in options else 0,
            key="catalog_drive",
            on_change=_sync_drive_pills,
        )
        speed_threshold = st.number_input(
            "Speed threshold (m/s)",
            min_value=0.0,
            value=_speed_from_url(),
            step=1.0,
            key="speed_threshold",
        )
        minimum_seconds = st.number_input(
            "Minimum time at or above threshold (s)",
            min_value=0.0,
            value=_nonnegative_from_url("duration"),
            step=0.5,
            key="minimum_seconds",
        )
        stopped_only = st.checkbox(
            "Only clips with stopped intervals", value=st.query_params.get("stopped") == "true"
        )
        st.caption(
            "Zero speed/duration include unknown clips. "
            f"Stopped means ≤{stop_speed:g} m/s over a valid interval."
        )
    return ClipFilters(
        drive_id=drive_id,
        speed_threshold=speed_threshold,
        stopped_only=stopped_only,
        minimum_seconds=minimum_seconds,
    )


def _find_matching_clips(database_path: Path, filters: ClipFilters) -> DataFrame:
    """Filter warehouse summaries; do not recalculate business metrics in the UI."""
    return find_clips(
        database_path,
        filters.drive_id,
        filters.speed_threshold,
        filters.minimum_seconds,
        filters.stopped_only,
    )


def _selected_theme() -> str:
    return st.context.theme.type


def _speed_from_url() -> float:
    return _nonnegative_from_url("speed")


def _sync_drive_pills() -> None:
    st.session_state["drive_pills"] = st.session_state["catalog_drive"]


def _select_drive() -> None:
    st.session_state["catalog_drive"] = st.session_state["drive_pills"] or "All drives"


def _render_theme_switch(filters: ClipFilters, search: str, completed_run: Series) -> None:
    target = "light" if _selected_theme() == "dark" else "dark"
    parameters = {
        "embed": "true",
        "embed_options": f"{target}_theme",
        "drive": filters.drive_id,
        "speed": filters.speed_threshold,
        "duration": filters.minimum_seconds,
        "stopped": str(filters.stopped_only).lower(),
        "search": search,
    }
    if "frame_window" in st.session_state:
        parameters.update(zip(("start", "end"), st.session_state["frame_window"], strict=True))
    parameters["state"] = st.session_state.get("motion_state", "All states")
    parameters["spatial"] = str(st.session_state.get("spatial_filter", False)).lower()
    if st.session_state.get("spatial_filter"):
        for name in ("min_x", "max_x", "min_y", "max_y"):
            if name in st.session_state:
                parameters[name] = st.session_state[name]
    icon = "☀︎" if target == "light" else "☾"
    label = f"Switch to {target} mode"
    completed = completed_run["completed_at"].strftime("%d %b %Y · %H:%M %Z")
    st.html(
        '<div class="topbar"><div class="topbar-chips">'
        '<span class="top-chip">Synthetic · 16 FPS · local XYZ m</span>'
        f'<span class="top-chip">Snapshot · {_e(completed)}</span>'
        "</div>"
        f'<a class="vl-theme" target="_self" href="?{_e(urlencode(parameters))}" '
        f'aria-label="{_e(label)}" title="{_e(label)}">'
        f'<span class="theme-icon" aria-hidden="true">{_e(icon)}</span></a></div>'
    )


def _render_clip_explorer(database_path: Path, matching_clips: DataFrame) -> None:
    """Show matching summaries, offer CSV export, and open the selected clip."""
    if matching_clips.empty:
        st.info("No clips match these filters. Lower the speed threshold or choose another drive.")
        return

    st.subheader("Matching clips")
    st.dataframe(matching_clips, hide_index=True, width="stretch")
    selected_clip_id = st.selectbox(
        "Inspect clip", matching_clips["clip_id"].tolist(), key="inspect_clip"
    )
    selected_clip = matching_clips.loc[matching_clips["clip_id"] == selected_clip_id].iloc[0]
    _render_clip_detail(database_path, selected_clip)


def _render_clip_detail(database_path: Path, clip: Series) -> None:
    """Load a bounded frame preview while leaving full-clip metrics in SQL."""
    frame_details = read_bi(
        database_path,
        "clip_detail",
        {
            "drive_id": clip["drive_id"],
            "start_frame": int(clip["start_frame"]),
            "end_frame": int(clip["end_frame"]),
        },
    )
    st.subheader(clip["clip_id"])
    st.caption(
        f"{clip['uri']} · Synthetic catalog reference; no playable footage. "
        "Charts and frame preview show at most 5,000 frames; summary metrics use all frames."
    )
    if frame_details.empty:
        st.info("This clip has no accepted frames. Inspect Quality & provenance for reasons.")
        return

    _render_motion_charts(frame_details)
    with st.expander("Frame facts and source lineage"):
        st.dataframe(frame_details, hide_index=True, width="stretch")


def _render_motion_charts(frame_details: DataFrame) -> None:
    """Plot positions and speed with physical units and honest handling of gaps."""
    trajectory_column, speed_column = st.columns(2)
    trajectory_chart = px.scatter(
        frame_details,
        x="x_m",
        y="y_m",
        color="frame_index",
        labels={"x_m": "World X (m)", "y_m": "World Y (m)", "frame_index": "Frame"},
        title="Trajectory · XY projection",
        hover_data=["frame_index", "z_m"],
        color_continuous_scale=(
            ["#B4F24E", "#B699EB"] if _selected_theme() == "dark" else ["#4D7000", "#724AAB"]
        ),
    )
    # Equal axis scales prevent the displayed trajectory from distorting distances.
    trajectory_chart.update_yaxes(scaleanchor="x", scaleratio=1)
    trajectory_column.plotly_chart(_chart_style(trajectory_chart), width="stretch", theme=None)

    speed_chart = px.line(
        frame_details,
        x="frame_index",
        y="speed_mps",
        labels={"frame_index": "Frame", "speed_mps": "Speed (m/s)"},
        title="Observed speed",
    )
    speed_chart.update_traces(connectgaps=False, line_color=_palette()[0])
    # A zero baseline avoids magnifying floating-point noise in constant speed.
    speed_chart.update_yaxes(rangemode="tozero", tickformat=".1f")
    speed_column.plotly_chart(_chart_style(speed_chart), width="stretch", theme=None)
    st.caption(
        "Speed is unknown for the first frame and after gaps. "
        "Centroid motion assumes a rigid, uniformly sampled shape."
    )


def _render_quality_tab(database_path: Path, completed_run: Series) -> None:
    """Show the snapshot's audit records and the settings needed to replay it."""
    st.subheader("Ingestion quality")
    st.write(
        f"Accepted {int(completed_run['accepted']):,} frames · "
        f"Rejected {int(completed_run['rejected']):,} inputs · "
        f"Skipped {int(completed_run['duplicates']):,} identical duplicates"
    )
    quality_events = read_bi(database_path, "quality_events")
    if quality_events.empty:
        st.success("No quality events in this snapshot.")
    else:
        st.dataframe(quality_events, hide_index=True, width="stretch")
        st.caption("Showing at most 1,000 events. Query quality_events for the complete audit.")
    st.subheader("Source manifest")
    st.json(completed_run["manifest"])
    st.caption(f"Run {completed_run['run_id']} · Completed {completed_run['completed_at']}")


def _palette() -> list[str]:
    return (
        ["#B4F24E", "#B699EB", "#A1A1AA", "#FAFAFA"]
        if _selected_theme() == "dark"
        else ["#4D7000", "#724AAB", "#52525B", "#18181B"]
    )


def _chart_style(figure, height=330):
    """Use the same readable axes, spacing and surface throughout the workspace."""
    dark = _selected_theme() == "dark"
    grid = "#27272A" if dark else "#E4E4E7"
    figure.update_layout(
        height=height,
        margin=dict(l=40, r=16, t=48, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="ui-monospace, Menlo, Consolas, monospace",
            size=12,
            color="#A1A1AA" if dark else "#52525B",
        ),
        legend=dict(orientation="h", y=1.12, title_text="", font=dict(size=11)),
        hoverlabel=dict(
            bgcolor="#111113" if dark else "white",
            font_size=14,
            font_color="#FAFAFA" if dark else "#18181B",
        ),
    )
    figure.update_xaxes(
        gridcolor=grid,
        griddash="dot",
        zeroline=False,
        automargin=True,
        nticks=5,
        title_font_size=12,
        tickfont_size=12,
    )
    figure.update_yaxes(
        gridcolor=grid,
        griddash="dot",
        zeroline=False,
        automargin=True,
        nticks=5,
        title_font_size=12,
        tickfont_size=12,
    )
    return figure


def _finite_url_number(name: str, default: float) -> float:
    try:
        value = float(st.query_params.get(name, str(default)))
        return value if isfinite(value) else default
    except ValueError:
        return default


def _window_from_url(end_frame: int) -> tuple[int, int]:
    start = int(_finite_url_number("start", 0))
    end = int(_finite_url_number("end", end_frame))
    return (start, end) if 0 <= start <= end <= end_frame else (0, end_frame)


def _observation_data(database_path: Path, clips: DataFrame):
    st.caption("OBSERVATION FILTERS · Apply to positions, activity, motion states and results.")
    with st.expander("Observation filters · time, motion & position"):
        time_column, state_column = st.columns([2, 1])
        end_frame = int(clips["end_frame"].max()) if not clips.empty else 0
        with time_column:
            if end_frame > 0:
                window = st.slider(
                    "Frame window", 0, end_frame, _window_from_url(end_frame), key="frame_window"
                )
            else:
                window = (0, 0)
                st.caption("Only frame 0 is available.")
            st.caption(
                f"Frame indices {window[0]}-{window[1]} · elapsed time uses each drive clock"
            )
        with state_column:
            state = st.selectbox(
                "Motion state",
                ["All states", "Moving", "Stopped", "Unknown"],
                index=["All states", "Moving", "Stopped", "Unknown"].index(
                    st.query_params.get("state", "All states")
                    if st.query_params.get("state", "All states")
                    in ["All states", "Moving", "Stopped", "Unknown"]
                    else "All states"
                ),
                key="motion_state",
            )
        bounds = {}
        st.caption("Local XYZ meters. Bounds include their endpoints.")
        if st.checkbox(
            "Limit spatial area",
            value=st.query_params.get("spatial") == "true",
            key="spatial_filter",
        ):
            extent = read_bi(database_path, "spatial_extent").iloc[0]
            if extent.isna().any():
                st.info("No accepted positions are available. Inspect Quality & provenance.")
                return None, ObservationFilter()
            columns = st.columns(4)
            for column, name, label, value in zip(
                columns,
                ["min_x", "max_x", "min_y", "max_y"],
                ["Minimum X (m)", "Maximum X (m)", "Minimum Y (m)", "Maximum Y (m)"],
                extent,
                strict=True,
            ):
                bounds[name] = column.number_input(
                    label, value=_finite_url_number(name, float(value)), key=name
                )
    filters = ObservationFilter(start_frame=window[0], end_frame=window[1], state=state, **bounds)
    try:
        with st.spinner("Reading warehouse observations…"):
            result = explore(database_path, clips["clip_id"].tolist(), filters)
    except ValueError:
        st.error("Minimum position must not exceed maximum. Check the local position bounds.")
        return None, filters
    records, states = result["frames"], result["states"]
    total = int(states["frames"].sum())
    st.caption(
        f"{total:,} matching observations · {len(records):,} shown · "
        "Counts and trends use all matches"
    )
    if records.empty:
        st.info(
            "No observations in this selection. "
            "Expand the frame window, position bounds or motion state."
        )
    if total > len(records):
        st.warning(
            "Preview limited to the first 5,000 observations by frame and drive. "
            "Narrow filters to inspect later records."
        )

    return result, filters


def _render_position(records: DataFrame, clips: DataFrame, filters: ObservationFilter) -> None:
    _tile_header("Position explorer", "Observation")
    if records.empty:
        st.info("No observations in this selection.")
        return
    st.caption("LOCAL POSITION · meters · select a point for its source record")
    unique_positions = len(records[["x_m", "y_m"]].drop_duplicates())
    if unique_positions == 1:
        st.caption(
            f"{len(records):,} observations share the same XY position. "
            "Use the results table to inspect individual frames."
        )
    else:
        st.caption(
            f"{records['drive_id'].nunique()} drives · "
            f"{len(records):,} observations in this position preview"
        )
    spatial = px.scatter(
        records,
        x="x_m",
        y="y_m",
        color="drive_id",
        symbol="drive_id",
        color_discrete_sequence=_palette(),
        custom_data=["drive_id", "frame_index"],
        hover_data={"frame_index": True, "speed_mps": ":.2f", "state": True, "z_m": ":.3f"},
        labels={"x_m": "World X (m)", "y_m": "World Y (m)", "drive_id": "Drive"},
    )
    spatial.update_traces(
        marker_size=10 if unique_positions == 1 else 7,
        marker_opacity=0.9,
        marker_line=dict(width=1, color="#111113" if _selected_theme() == "dark" else "#FFFFFF"),
        selected=dict(marker=dict(size=13, opacity=1)),
        unselected=dict(marker=dict(opacity=0.35)),
    )
    spatial.update_layout(clickmode="event+select", dragmode="pan")
    spatial.update_yaxes(scaleanchor="x", scaleratio=1)
    selection_key = f"positions-{clips['clip_id'].tolist()}-{filters}"
    event = st.plotly_chart(
        _chart_style(spatial, 450),
        width="stretch",
        theme=None,
        on_select="rerun",
        selection_mode="points",
        key=selection_key,
    )
    selected_points = event.selection.points
    if selected_points:
        drive_id, frame_index = selected_points[0]["customdata"][:2]
        matches = records[(records.drive_id == drive_id) & (records.frame_index == frame_index)]
        if not matches.empty:
            with st.container(border=True):
                st.subheader(f"Selected position · {drive_id} · frame {frame_index}")
                st.dataframe(matches, hide_index=True, width="stretch")
                st.caption("Exact warehouse record for the selected plot point.")


def _render_results(records: DataFrame, clips: DataFrame, filters: ObservationFilter) -> None:
    selection_key = f"positions-{clips['clip_id'].tolist()}-{filters}"
    with st.container(key="results_card"):
        _tile_header("Observation results", "Observation · preview")
        st.caption(
            "Sort any column. Search in the table toolbar or use catalog search. "
            "Select a row for full lineage."
        )
        table = st.dataframe(
            records,
            hide_index=True,
            width="stretch",
            height=280,
            on_select="rerun",
            selection_mode="single-row",
            key=f"rows-{selection_key}",
            column_config={
                "drive_id": "Drive",
                "frame_index": "Frame",
                "timestamp_ns": "Timestamp (ns UTC)",
                "speed_mps": st.column_config.NumberColumn("Speed (m/s)", format="%.2f"),
                "x_m": st.column_config.NumberColumn("X (m)", format="%.3f"),
                "y_m": st.column_config.NumberColumn("Y (m)", format="%.3f"),
                "z_m": st.column_config.NumberColumn("Z (m)", format="%.3f"),
                "content_hash": None,
                "run_id": None,
                "point_count": None,
            },
        )
        st.download_button(
            "Export displayed observations",
            records.to_csv(index=False),
            "vl-observations.csv",
            "text/csv",
        )
        detail = None
        if table.selection.rows:
            detail = records.iloc[table.selection.rows[0]]
        with st.expander("Inspect an observation by index (keyboard alternative)"):
            index = st.number_input(
                "Displayed row index", 0, len(records) - 1, 0, key=f"detail-{selection_key}"
            )
            if st.button("Inspect observation"):
                detail = records.iloc[index]
        if detail is not None:
            st.subheader(f"Selected row · {detail['drive_id']} · frame {detail['frame_index']}")
            st.dataframe(detail.rename("Value").astype(str), width="stretch")
            st.caption(
                "Exact accepted warehouse observation. Synthetic local-coordinate observation."
            )


def _e(value: object) -> str:
    """Escape every dynamic text or attribute value at the HTML boundary."""
    return escape(str(value), quote=True)


def _tile_header(title: str, scope: str) -> None:
    st.html(
        f'<header class="tile-head"><h2 class="title">{_e(title)}</h2>'
        f'<span class="scope-chip">{_e(scope)}</span></header>'
    )


def _strip(rows: list[tuple[str, float | None]], *, activity: bool = False) -> str:
    """Render focusable bars; unknown and known zero retain distinct marks."""
    maximum = max((value for _, value in rows if value is not None), default=0)
    bars = []
    for index, (label, value) in enumerate(rows):
        kind = "strip-bar" if activity else "clip-bar"
        kind += " unknown" if value is None else " zero" if value == 0 else ""
        height = 100 * value / maximum if value is not None and maximum else 0
        opacity = 0.35 + 0.65 * (index + 1) / max(len(rows), 1)
        bars.append(
            f'<span class="{_e(kind)}" tabindex="0" role="img" '
            f'aria-label="{_e(label)}" style="--height:{_e(height)}%;'
            f'--opacity:{_e(opacity)}"><span class="bar-tooltip">'
            f"{_e(label)}</span></span>"
        )
    kind = "activity-strip" if activity else "clip-strip"
    return f'<div class="{_e(kind)}">' + "".join(bars) + "</div>"


def _render_bento(
    tiles, matching_clips, run, observations, observation_filters, filters, drive_ids, stop_speed
) -> None:
    summary = tiles["summary"].iloc[0]
    with st.container(key="bento_grid"):
        with st.container(key="motion_catalog"):
            _tile_header("Motion catalog", "Catalog")
            st.html(
                f'<div class="hero-value" aria-label="Observed distance: '
                f'{_e(summary.observed_distance_m)} meters">'
                f'{_e(f"{summary.observed_distance_m:,.1f}")}<span class="hero-unit">m</span></div>'
                '<p class="tile-note">Observed distance · summed across matching clips</p>'
            )
            st.html(
                '<p class="tile-note">Observation activity '
                '<span class="scope-chip">Observation</span></p>'
            )
            trend = observations["trend"] if observations is not None else pd.DataFrame()
            if trend.empty:
                st.caption("No observation activity in this selection.")
            else:
                st.html(
                    _strip(
                        [
                            (
                                f"Bin starting {row.elapsed_seconds} s · "
                                f"{row.frames:,} observations",
                                float(row.frames),
                            )
                            for row in trend.itertuples()
                        ],
                        activity=True,
                    )
                )
                st.caption(
                    f"{int(trend.frames.sum()):,} observations · "
                    f"{len(trend)} time bins · full selection"
                )
                st.caption("Bins use time from drive start; the final bin may be partial.")
        with st.container(key="drive_cards"):
            _tile_header("Drive cards", "Catalog")
            _render_drive_cards(tiles["drives"])
            if "drive_pills" not in st.session_state:
                st.session_state["drive_pills"] = filters.drive_id
            st.pills(
                "Select drive",
                ["All drives", *drive_ids],
                key="drive_pills",
                on_change=_select_drive,
            )
        with st.container(key="completeness"):
            _tile_header("Frame completeness", "Catalog")
            received, missing = int(summary.accepted_frames), int(summary.missing_frames)
            total = received + missing
            share = received / total if total else None
            label = f"{received:,} received · {missing:,} missing"
            value = f"{share:.1%}" if share is not None else "-"
            st.html(
                f'<div class="gauge" role="img" aria-label="{_e(label)}" '
                f'style="--percent:{_e(100 * share if share is not None else 0)}">'
                '<div class="gauge-track"></div><div class="gauge-fill"></div>'
                f'<div class="gauge-value">{_e(value)}</div></div>'
                f'<p class="tile-note">{_e(label)}</p>'
            )
            st.write(f"**Frames received** · {received:,} / {total:,}")
            st.caption(
                "Received / expected clip frames"
                if total
                else "No expected frames in this selection."
            )
        with st.container(key="position_explorer"):
            if observations is not None:
                _render_position(observations["frames"], matching_clips, observation_filters)
            else:
                _tile_header("Position explorer", "Observation")
                st.info("Adjust the observation bounds to see positions.")
        with st.container(key="motion_states"):
            _tile_header("Motion states", "Observation")
            _render_states(
                observations["states"] if observations is not None else pd.DataFrame(), stop_speed
            )
        with st.container(key="snapshot_quality"):
            _tile_header("Snapshot quality", "Whole snapshot")
            _render_snapshot_quality(run, tiles["quality"], matching_clips)
        with st.container(key="peak_band"):
            _tile_header("Peak-speed band", "Catalog")
            _render_peak_band(summary, filters.speed_threshold)
        with st.container(key="clip_stream"):
            _tile_header("Clip stream", "Catalog")
            rows = []
            for clip in tiles["clips"].itertuples():
                speed = None if pd.isna(clip.peak_speed_mps) else float(clip.peak_speed_mps)
                label = (
                    f"{clip.clip_id} · Peak speed unknown"
                    if speed is None
                    else f"{clip.clip_id} · Peak speed {speed} m/s"
                )
                rows.append((label, speed))
            st.html(_strip(rows) if rows else '<p class="tile-note">No matching clips.</p>')
            st.metric("Matching clips", int(summary.clips))
            duration = (
                f"{summary.mean_duration_seconds:.3f} s"
                if pd.notna(summary.mean_duration_seconds)
                else "Unavailable"
            )
            st.caption(f"Average duration · {duration} · declared drive clocks")
            st.caption("Frame order within each drive · hollow = unknown · baseline = zero")
        with st.container(key="top_clips"):
            _tile_header("Top clips", "Catalog")
            _render_top_clips(matching_clips)


def _render_drive_cards(drives: DataFrame) -> None:
    if drives.empty:
        st.html('<p class="tile-note">No drives in this selection.</p>')
        return
    cards = []
    # One matching drive is on the front; the native selector changes catalog scope.
    front = drives.iloc[0]
    for index, row in enumerate(reversed(list(drives.itertuples()))):
        is_front = index == len(drives) - 1
        description = (
            f"{row.drive_id}: {row.clips} clips, {row.accepted_frames} frames, "
            f"{row.observed_distance_m} m observed distance"
        )
        cards.append(
            f'<div class="drive-card{_e(" front" if is_front else "")}" '
            f'role="img" aria-label="{_e(description)}">'
            f"<b>{_e(row.drive_id)}</b>{_e(int(row.clips))} clips · "
            f"{_e(int(row.accepted_frames))} frames<br>"
            f"{_e(f'{row.observed_distance_m:,.1f}')} m</div>"
        )
    st.html(
        '<div class="drive-stack">'
        + "".join(cards)
        + f'<div class="drive-panel">Front drive: {_e(front.drive_id)}<br>'
        "Matching clip totals · select a drive below</div></div>"
    )


def _render_states(states: DataFrame, stop_speed: float) -> None:
    counts = dict(zip(states.state, states.frames, strict=True)) if not states.empty else {}
    total = sum(counts.values())
    if not total:
        st.html('<p class="tile-note">No observations in this selection.</p>')
        return
    segments, legend = [], []
    for state in ("Moving", "Stopped", "Unknown"):
        count = int(counts.get(state, 0))
        label = f"{state}: {count:,} of {total:,} observations ({count / total:.1%})"
        if count:
            segments.append(
                f'<span class="state-segment {_e(state.lower())}" '
                f'style="--share:{_e(count)}" aria-label="{_e(label)}"></span>'
            )
        legend.append(
            f'<div class="state-row" aria-label="{_e(label)}">'
            f'<span><i class="dot {_e(state.lower())}" aria-hidden="true"></i>'
            f"{_e(state)}</span><span>{_e(f'{count:,}')} · "
            f"{_e(f'{count / total:.1%}')}</span></div>"
        )
    st.html(
        '<div class="state-bar" role="img" aria-label="Motion state proportions">'
        + "".join(segments)
        + '</div><div class="state-legend">'
        + "".join(legend)
        + "</div>"
    )
    st.caption(f"Stopped ≤{stop_speed:g} m/s · Unknown = no valid prior frame.")
    st.caption("Tiny segments widened for visibility; the legend gives counts and shares.")


def _render_snapshot_quality(run: Series, events: DataFrame, clips: DataFrame) -> None:
    accepted, rejected = int(run["accepted"]), int(run["rejected"])
    total = accepted + rejected
    share = accepted / total if total else None
    value = f"{share:.1%}" if share is not None else "-"
    st.html(
        f'<div class="quality-value">{_e(value)}</div>'
        '<p class="tile-note">Accepted inputs</p>'
        f'<div class="quality-progress" role="img" aria-label="{_e(accepted)} accepted '
        f'of {_e(total)} nonduplicate inputs"><div class="quality-fill" '
        f'style="width:{_e(100 * share if share is not None else 0)}%"></div></div>'
    )
    facts = [
        ("Accepted", f"{accepted:,} / {total:,}"),
        ("Rejected", rejected),
        ("Duplicates", int(run["duplicates"])),
        ("Run", run["run_id"]),
        ("Completed", run["completed_at"].strftime("%d %b %Y · %H:%M %Z")),
    ]
    st.html(
        '<dl class="quality-facts">'
        + "".join(f"<dt>{_e(label)}</dt><dd>{_e(value)}</dd>" for label, value in facts)
        + "</dl>"
    )
    if events.empty:
        st.html('<div class="quality-events">No quality events</div>')
    else:
        st.html(
            '<div class="quality-events">Quality events by error code'
            + _strip(
                [
                    (f"{row.code}: {row.events} events", float(row.events))
                    for row in events.itertuples()
                ],
                activity=True,
            )
            + "</div>"
        )
    st.caption("Accepted / (accepted + rejected). Duplicates are counted separately.")
    st.html('<p class="tile-note">Catalog export · matching clips</p>')
    st.download_button(
        "Download matching clips",
        clips.to_csv(index=False),
        "motion-clips.csv",
        "text/csv",
        type="primary",
    )


def _render_peak_band(summary: Series, threshold: float) -> None:
    low, high = summary.min_peak_speed_mps, summary.max_peak_speed_mps
    st.html(f'<span class="scope-chip">{_e(int(summary.threshold_clips))} clips ≥ threshold</span>')
    if pd.isna(low) or pd.isna(high):
        st.html('<p class="tile-note">No known peak speeds.</p>')
    else:
        if threshold < low:
            position = 0.0
        elif threshold > high:
            position = 100.0
        else:
            position = 50.0 if low == high else 100 * (threshold - low) / (high - low)
        position = min(100.0, max(0.0, position))
        label = f"Peak speeds {low} to {high} m/s; threshold {threshold} m/s"
        st.html(
            f'<div class="range-track" role="img" aria-label="{_e(label)}">'
            f'<span class="range-marker" style="--position:{_e(position)}%"></span></div>'
            f'<div class="range-labels"><span>{_e(f"{low:.2f}")} m/s</span>'
            f"<span>{_e(f'{high:.2f}')} m/s</span></div>"
        )
        if threshold < low or threshold > high:
            st.caption("Threshold is outside the observed range; marker is shown at the edge.")
        elif low == high:
            st.caption("All known peak speeds are equal.")
    st.caption(f"Speed threshold · {threshold:g} m/s")


def _render_top_clips(clips: DataFrame) -> None:
    rows = []
    ranked = clips.sort_values(
        ["seconds_at_or_above", "p90_speed_mps", "clip_id"],
        ascending=[False, False, True],
        na_position="last",
    ).head(4)
    for clip in ranked.itertuples():
        speed = "Unknown" if pd.isna(clip.peak_speed_mps) else f"{clip.peak_speed_mps:.2f} m/s"
        symbol = {"Moving": "↗", "Stopped": "Ⅱ", "Unknown": "?"}[clip.motion_status]
        rows.append(
            '<div class="top-row">'
            f'<span class="state-icon" aria-hidden="true">{_e(symbol)}</span>'
            f'<div><div class="clip-name">{_e(clip.clip_id)}</div>'
            f'<div class="clip-meta">{_e(clip.drive_id)} · {_e(f"{clip.duration_seconds:.3f}")} s'
            f" · {_e(clip.accepted_frames)} frames</div></div>"
            f'<div class="clip-speed">{_e(speed)}<small>'
            f"{_e(clip.motion_status)}</small></div></div>"
        )
    st.html(
        '<div class="top-list">' + "".join(rows) + "</div>"
        if rows
        else '<p class="tile-note">No matching clips.</p>'
    )
    st.caption(
        "Ranked by time above the speed threshold, then p90 speed. "
        "Moving intervals may coexist with stops."
    )


def _nonnegative_from_url(name: str) -> float:
    try:
        speed = float(st.query_params.get(name, "0"))
        return speed if 0 <= speed < float("inf") else 0.0
    except ValueError:
        return 0.0


if __name__ == "__main__":
    main()
