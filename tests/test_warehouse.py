from dataclasses import replace

import duckdb
import pytest
from source_fixture import FixtureSource

from vl_product.quality import QualityPolicy
from vl_product.source import Config, SyntheticSource, frames
from vl_product.warehouse import ingest


def rows(path, sql):
    with duckdb.connect(str(path), read_only=True) as con:
        return con.execute(sql).fetchall()


def test_pipeline_speed_order_and_replay(tmp_path):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=4, side_cm=2)
    report = ingest(path, FixtureSource(config, reversed(list(frames(config)))))
    assert report["accepted"] == 16
    speeds = rows(path, "select speed_mps from motion where drive_id='steady' order by frame_index")
    assert speeds[0] == (None,)
    assert [row[0] for row in speeds[1:]] == pytest.approx([16.0] * 3, abs=1e-12)
    before = rows(path, "select * exclude (run_id) from frames order by drive_id, frame_index")
    ingest(path, FixtureSource(config, reversed(list(frames(config)))))
    assert (
        rows(path, "select * exclude (run_id) from frames order by drive_id, frame_index") == before
    )
    assert rows(path, "select stopped_seconds from clip_summary where drive_id='stationary'") == [
        (0.1875,)
    ]


def test_bad_duplicate_conflict_and_gap_are_visible(tmp_path):
    config = Config(frames_per_drive=4, side_cm=2)
    source = [f for f in frames(config) if not (f.drive_id == "steady" and f.frame_index == 1)]
    first = source[0]
    source += [first, replace(first, points_cm=first.points_cm + 1), {"bad": "frame"}]
    path = tmp_path / "catalog.duckdb"
    report = ingest(path, FixtureSource(config, source), QualityPolicy(0, 1, 1))
    assert (report["accepted"], report["rejected"], report["duplicates"]) == (15, 2, 1)
    assert rows(
        path, "select speed_mps from motion where drive_id='steady' order by frame_index"
    ) == [(None,), (None,), (16.0,)]
    assert rows(path, "select missing_frames from clip_summary where drive_id='steady'") == [(1,)]
    codes = {r[0] for r in rows(path, "select code from quality_events")}
    assert {"E_SCHEMA", "E_DUPLICATE_CONFLICT", "W_DUPLICATE", "W_MISSING_FRAMES"} <= codes


def test_failure_rolls_back_previous_snapshot(tmp_path):
    config = Config(frames_per_drive=2, side_cm=2)
    path = tmp_path / "catalog.duckdb"
    ingest(path, SyntheticSource(config))
    before = rows(path, "select * from frames order by drive_id, frame_index")

    def broken():
        yield next(frames(config))
        raise RuntimeError("source disconnected")

    with pytest.raises(RuntimeError, match="source disconnected"):
        ingest(path, FixtureSource(config, broken()))
    assert rows(path, "select * from frames order by drive_id, frame_index") == before


def test_empty_source_and_unknown_drive(tmp_path):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=2, side_cm=2)
    frame = replace(next(frames(config)), drive_id="unlisted")
    report = ingest(path, FixtureSource(config, [frame]), QualityPolicy(0, 1, 1))
    assert report["accepted"] == 0
    assert report["rejected"] == 1
    assert rows(path, "select sum(missing_frames) from clip_summary") == [(8,)]


def test_export_never_overwrites_database_or_existing_file(tmp_path):
    from vl_product.warehouse import export_frames

    config = Config(frames_per_drive=2, side_cm=2)
    path = tmp_path / "catalog.duckdb"
    ingest(path, SyntheticSource(config))
    with pytest.raises(ValueError, match="E_EXPORT"):
        export_frames(path, path)
    target = tmp_path / "important.txt"
    target.write_text("keep me")
    with pytest.raises(ValueError, match="E_EXPORT"):
        export_frames(path, target)
    assert target.read_text() == "keep me"
    assert rows(path, "select count(*) from frames") == [(8,)]


def test_ingest_refuses_unrelated_database(tmp_path):
    path = tmp_path / "unrelated.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("create table frames (important varchar)")
        con.execute("insert into frames values ('keep me')")
    config = Config(frames_per_drive=1, side_cm=2)
    with pytest.raises(ValueError, match="E_DATABASE"):
        ingest(path, SyntheticSource(config))
    assert rows(path, "select * from frames") == [("keep me",)]


def test_stop_start_turn_and_xyz_motion(tmp_path):
    import numpy as np

    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    stop = rows(
        path,
        "select peak_speed_mps, observed_distance_m, stopped_seconds "
        "from clip_summary where drive_id='stop_start'",
    )[0]
    assert stop == pytest.approx((8, 1.5, 0.25))
    turn = rows(
        path, "select peak_speed_mps, observed_distance_m from clip_summary where drive_id='turn'"
    )[0]
    assert turn == pytest.approx((9.6, 4.2))
    source = list(frames(Config(frames_per_drive=2, side_cm=2)))
    source[1] = replace(source[1], points_cm=source[0].points_cm + np.array([3, 4, 12]))
    ingest(path, FixtureSource(Config(frames_per_drive=2, side_cm=2), source))
    assert rows(path, "select speed_mps from motion where drive_id='steady' and frame_index=1")[0][
        0
    ] == pytest.approx(2.08)
