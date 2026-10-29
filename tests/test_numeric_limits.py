from dataclasses import replace

import numpy as np
import pytest

from vl_product.etl import DataError, reduce_frame
from vl_product.source import Config, SyntheticSource


@pytest.mark.parametrize(
    "points",
    [
        np.array([[-(2**63), 0, 0]], dtype=np.int64),
        np.array([[2**64 - 1, 0, 0]], dtype=np.uint64),
        np.array([[np.nan, 0, 0]]),
        np.array([[np.inf, 0, 0]]),
        np.array([[0.25, 0, 0]]),
    ],
)
def test_numeric_extremes_rejected(points):
    source = SyntheticSource(Config(side_cm=2))
    frame = replace(next(source.frames()), points_cm=points)
    with pytest.raises(DataError, match="E_COORDINATES"):
        reduce_frame(frame, source.drives()[0])


def test_duplicates_across_fact_batches(tmp_path):
    from source_fixture import FixtureSource

    from vl_product.quality import QualityPolicy
    from vl_product.source import frames
    from vl_product.warehouse import ingest

    config = Config(frames_per_drive=3000, side_cm=1)
    inputs = list(frames(config))
    inputs += [inputs[0], replace(inputs[0], points_cm=inputs[0].points_cm + 1)]
    report = ingest(tmp_path / "db", FixtureSource(config, inputs), QualityPolicy(1, 1, 1))
    assert (report["accepted"], report["duplicates"], report["rejected"]) == (12000, 1, 1)
