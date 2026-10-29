from dataclasses import replace

import numpy as np
import pytest

from vl_product.contracts import DriveRecord
from vl_product.etl import DataError, reduce_frame
from vl_product.source import PERIOD_NS, START_NS, Config, frames

TEST_DRIVE = DriveRecord("steady", START_NS, PERIOD_NS, "local_world")


def test_reduction_has_metric_units_and_canonical_hash():
    frame = next(frames(Config(side_cm=2)))
    fact = reduce_frame(frame, TEST_DRIVE)
    assert fact["x_m"] == pytest.approx(0.005)
    assert fact["min_x_m"] == 0
    assert fact["max_x_m"] == 0.01
    assert fact["point_count"] == 8
    assert (
        fact["content_hash"]
        == reduce_frame(replace(frame, points_cm=frame.points_cm.astype(float)), TEST_DRIVE)[
            "content_hash"
        ]
    )
    assert (
        fact["content_hash"]
        != reduce_frame(replace(frame, points_cm=frame.points_cm + 1), TEST_DRIVE)["content_hash"]
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"units": "m"}, "E_UNITS"),
        ({"coordinate_system": "unknown"}, "E_COORDINATE_SYSTEM"),
        ({"schema_version": 2}, "E_SCHEMA"),
        ({"drive_id": ""}, "E_METADATA"),
        ({"frame_index": True}, "E_METADATA"),
        ({"frame_index": -1}, "E_METADATA"),
        ({"timestamp_ns": 0}, "E_TIMESTAMP"),
        ({"points_cm": None}, "E_SHAPE"),
        ({"points_cm": np.zeros((0, 3))}, "E_SHAPE"),
        ({"points_cm": np.zeros((3, 2))}, "E_SHAPE"),
        ({"points_cm": np.array([[np.nan, 0, 0]])}, "E_COORDINATES"),
        ({"points_cm": np.array([[np.inf, 0, 0]])}, "E_COORDINATES"),
        ({"points_cm": np.array([[0.1, 0, 0]])}, "E_COORDINATES"),
        ({"points_cm": np.array([[1e10, 0, 0]])}, "E_COORDINATES"),
        ({"points_cm": np.array([["a", "b", "c"]])}, "E_COORDINATES"),
    ],
)
def test_invalid_inputs_are_coded(changes, code):
    frame = next(frames(Config(side_cm=2)))
    with pytest.raises(DataError) as error:
        reduce_frame(replace(frame, **changes), TEST_DRIVE)
    assert error.value.code == code


def test_malformed_envelope():
    with pytest.raises(DataError, match="E_SCHEMA"):
        reduce_frame({"unexpected": 42}, TEST_DRIVE)


@pytest.mark.parametrize(
    "changes",
    [
        {"units": np.array(["cm", "cm"])},
        {"coordinate_system": np.array(["local_world", "local_world"])},
    ],
)
def test_malformed_metadata_is_quarantinable(changes):
    with pytest.raises(DataError):
        reduce_frame(replace(next(frames(Config(side_cm=2))), **changes), TEST_DRIVE)
