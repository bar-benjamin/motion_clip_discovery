import pytest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_selection_body_is_reproducible_and_criteria_change_hash(tmp_path):
    from vl_product.exports import build_selection_manifest

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=8, side_cm=2)))
    a = build_selection_manifest(path, {"speed_threshold": 10, "minimum_seconds": 0.1})
    b = build_selection_manifest(path, {"speed_threshold": 10, "minimum_seconds": 0.1})
    assert a["sha256"] == b["sha256"]
    assert {k: v for k, v in a.items() if k != "created_at"} == {
        k: v for k, v in b.items() if k != "created_at"
    }
    assert [row["clip_id"] for row in a["clips"]] == ["clip-steady"]
    c = build_selection_manifest(path, {"speed_threshold": 11, "minimum_seconds": 0.1})
    assert c["sha256"] != a["sha256"]
    assert (
        build_selection_manifest(path, {"search": "stationary"})["clips"][0]["clip_id"]
        == "clip-stationary"
    )
    with pytest.raises(ValueError):
        build_selection_manifest(path, {"speed_threshold": float("nan")})
