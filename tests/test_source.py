import numpy as np
import pytest

from vl_product.source import PERIOD_NS, START_NS, Config, frames


def test_dense_cube_has_every_centimeter_cell():
    frame = next(frames(Config(frames_per_drive=1)))
    points = frame.points_cm
    assert points.shape == (1_000_000, 3)
    assert points.dtype == np.int32
    np.testing.assert_array_equal(points.min(axis=0), [0, 0, 0])
    np.testing.assert_array_equal(points.max(axis=0), [99, 99, 99])
    linear = points[:, 0] * 10000 + points[:, 1] * 100 + points[:, 2]
    np.testing.assert_array_equal(np.sort(linear), np.arange(1_000_000))
    np.testing.assert_allclose(points.mean(axis=0) / 100, [0.495] * 3)


def test_source_is_deterministic_and_16_fps():
    config = Config(frames_per_drive=4, side_cm=2)
    first, second = list(frames(config)), list(frames(config))
    assert len(first) == 16
    for a, b in zip(first, second, strict=True):
        assert a.timestamp_ns == START_NS + a.frame_index * PERIOD_NS
        np.testing.assert_array_equal(a.points_cm, b.points_cm)
    np.testing.assert_array_equal(first[1].points_cm - first[0].points_cm, [[100, 0, 0]] * 8)


@pytest.mark.parametrize(
    "kwargs", [{"side_cm": 0}, {"side_cm": 101}, {"frames_per_drive": 0}, {"side_cm": True}]
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)
