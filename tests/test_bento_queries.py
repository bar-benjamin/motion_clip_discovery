from hashlib import sha256

import pandas as pd
import pytest
from source_fixture import FixtureSource

from vl_product import exploration
from vl_product.quality import QualityPolicy
from vl_product.source import Config, SyntheticSource, frames
from vl_product.warehouse import ingest


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    return path


def test_catalog_summary_uses_only_matching_clips(catalog):
    result = exploration.catalog_tiles(catalog, ["clip-steady", "clip-stationary"], 10.0)
    summary = result["summary"].iloc[0]
    assert summary["clips"] == 2
    assert summary["accepted_frames"] == 16
    assert summary["missing_frames"] == 0
    assert summary["observed_distance_m"] == pytest.approx(7)
    assert summary["min_peak_speed_mps"] == 0
    assert summary["max_peak_speed_mps"] == pytest.approx(16)
    assert summary["threshold_clips"] == 1
    assert summary["mean_duration_seconds"] == pytest.approx(7 / 16)


def test_drive_cards_group_matching_clips_without_duplicate_ids(catalog):
    result = exploration.catalog_tiles(catalog, ["clip-steady", "clip-steady", "clip-stop_start"])
    drives = result["drives"].set_index("drive_id")
    assert drives.index.tolist() == ["steady", "stop_start"]
    assert drives["clips"].tolist() == [1, 1]
    assert drives["accepted_frames"].tolist() == [8, 8]
    assert drives["observed_distance_m"].tolist() == pytest.approx([7, 1.5])
    assert result["summary"].iloc[0]["clips"] == 2


def test_clip_rows_keep_frame_order_duration_and_peak_based_status(catalog):
    result = exploration.catalog_tiles(
        catalog, ["clip-turn", "clip-stationary", "clip-steady", "clip-stop_start"]
    )
    clips = result["clips"]
    assert clips["drive_id"].tolist() == ["stationary", "steady", "stop_start", "turn"]
    assert clips["duration_seconds"].tolist() == pytest.approx([7 / 16] * 4)
    assert clips["motion_status"].tolist() == ["Stopped", "Moving", "Moving", "Moving"]
    assert clips.loc[clips.drive_id == "stop_start", "stopped_seconds"].iloc[0] > 0


def test_unknown_speed_is_null_not_zero(tmp_path):
    path = tmp_path / "single.duckdb"
    config = Config(frames_per_drive=1, side_cm=1)
    ingest(path, SyntheticSource(config))
    result = exploration.catalog_tiles(path, ["clip-steady"])
    assert pd.isna(result["clips"].iloc[0]["peak_speed_mps"])
    assert result["clips"].iloc[0]["motion_status"] == "Unknown"
    assert result["clips"].iloc[0]["duration_seconds"] == 0
    assert pd.isna(result["summary"].iloc[0]["min_peak_speed_mps"])
    assert pd.isna(result["summary"].iloc[0]["max_peak_speed_mps"])
    assert result["summary"].iloc[0]["threshold_clips"] == 0


@pytest.mark.parametrize("clip_ids", [[], ["' OR TRUE --"], ["not-a-clip"]])
def test_empty_selection_has_zero_counts_and_no_invented_range(catalog, clip_ids):
    result = exploration.catalog_tiles(catalog, clip_ids)
    summary = result["summary"].iloc[0]
    assert summary["clips"] == 0
    assert summary["accepted_frames"] == 0
    assert summary["missing_frames"] == 0
    assert summary["observed_distance_m"] == 0
    assert summary["threshold_clips"] == 0
    assert pd.isna(summary["min_peak_speed_mps"])
    assert pd.isna(summary["max_peak_speed_mps"])
    assert pd.isna(summary["mean_duration_seconds"])
    assert result["drives"].empty
    assert result["clips"].empty


def test_quality_events_are_uncapped_and_ignore_catalog_selection(tmp_path):
    path = tmp_path / "quality.duckdb"
    config = Config(frames_per_drive=2, side_cm=1)
    source = list(frames(config))
    ingest(path, FixtureSource(config, source + [source[0]] * 1005 + [{"bad": "frame"}]))
    result = exploration.catalog_tiles(path, [])
    assert dict(zip(result["quality"].code, result["quality"].events, strict=True)) == {
        "E_SCHEMA": 1,
        "W_DUPLICATE": 1005,
    }


def test_missing_frame_totals_stay_clip_scoped(tmp_path):
    path = tmp_path / "gap.duckdb"
    config = Config(frames_per_drive=4, side_cm=1)
    source = [f for f in frames(config) if not (f.drive_id == "steady" and f.frame_index == 1)]
    ingest(path, FixtureSource(config, source), QualityPolicy(1, 1, 1))
    summary = exploration.catalog_tiles(path, ["clip-steady"])["summary"].iloc[0]
    assert summary["accepted_frames"] == 3
    assert summary["missing_frames"] == 1
    assert summary["observed_distance_m"] == pytest.approx(1)


def test_catalog_tiles_do_not_modify_database(catalog):
    before = sha256(catalog.read_bytes()).hexdigest()
    exploration.catalog_tiles(catalog, ["clip-steady"])
    assert sha256(catalog.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("threshold", [-1.0, float("nan"), float("inf")])
def test_invalid_peak_threshold_is_rejected_before_read(tmp_path, threshold):
    with pytest.raises(ValueError, match="threshold"):
        exploration.catalog_tiles(tmp_path / "absent.duckdb", [], threshold)


def test_bento_uses_declared_clocks_and_versioned_motion_rule(tmp_path):
    import duckdb
    from test_contracts import ArchiveSource

    path = tmp_path / "archive.duckdb"
    ingest(path, ArchiveSource())
    result = exploration.catalog_tiles(path, ["a1", "a2", "b1"])
    assert result["clips"].duration_seconds.tolist() == [1.0, 1.0, 1.0]
    with duckdb.connect(str(path)) as connection:
        connection.execute("UPDATE analysis_rules_history SET stop_speed_mps=20")
    assert set(exploration.catalog_tiles(path, ["a1", "a2", "b1"])["clips"].motion_status) == {
        "Stopped"
    }
