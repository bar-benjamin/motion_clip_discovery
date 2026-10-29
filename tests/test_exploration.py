import pytest

from vl_product.exploration import ObservationFilter, explore
from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_observation_filters_and_totals_use_warehouse(tmp_path):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=8, side_cm=2)
    ingest(path, SyntheticSource(config))
    result = explore(path, ["clip-stationary"], ObservationFilter())
    assert len(result["frames"]) == 8
    assert dict(zip(result["states"].state, result["states"].frames, strict=True)) == {
        "Stopped": 7,
        "Unknown": 1,
    }
    stopped = explore(path, ["clip-stationary"], ObservationFilter(state="Stopped"))
    assert len(stopped["frames"]) == 7
    assert stopped["frames"].speed_mps.eq(0).all()
    window = explore(path, ["clip-steady"], ObservationFilter(start_frame=2, end_frame=4))
    assert window["frames"].frame_index.tolist() == [2, 3, 4]
    assert window["frames"].speed_mps.tolist() == pytest.approx([16, 16, 16])
    spatial = explore(path, ["clip-steady"], ObservationFilter(min_x=2, max_x=3))
    assert spatial["frames"].frame_index.tolist() == [2]
    assert explore(path, ["' OR TRUE --"], ObservationFilter())["frames"].empty


def test_aggregate_counts_are_not_preview_counts(tmp_path):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=1300, side_cm=1)
    ingest(path, SyntheticSource(config))
    result = explore(
        path,
        [f"clip-{name}" for name in ("steady", "turn", "stationary", "stop_start")],
        ObservationFilter(),
    )
    assert len(result["frames"]) == 5000
    assert result["states"].frames.sum() == 5200
    assert result["trend"].frames.sum() == 5200
    assert len(result["trend"]) <= 104


def test_invalid_window_is_rejected_before_query(tmp_path):
    with pytest.raises(ValueError, match="bounds"):
        explore(tmp_path / "missing.duckdb", [], ObservationFilter(start_frame=3, end_frame=2))
