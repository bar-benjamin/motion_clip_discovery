import numpy as np

from vl_product.contracts import ClipRecord, DriveRecord, Frame
from vl_product.discovery import find_clips
from vl_product.warehouse import ingest


class MotionSource:
    def manifest(self):
        return {"source": "spike-versus-sustained"}

    def drives(self):
        return [DriveRecord(d, 0, 62_500_000, "local_world") for d in ["spike", "sustained"]]

    def clips(self):
        return [ClipRecord(d, d, 0, 48, f"mock://{d}") for d in ["spike", "sustained"]]

    def frames(self):
        for d in self.drives():
            for i in range(49):
                x = (100 if i else 0) if d.drive_id == "spike" else 75 * i
                yield Frame(d.drive_id, i, i * d.period_ns, np.array([[x, 0, 0]], dtype=np.int32))


def test_duration_filter_rejects_spike_and_preserves_peak(tmp_path):
    path = tmp_path / "db"
    ingest(path, MotionSource())
    found = find_clips(path, speed_threshold=10, minimum_seconds=1)
    assert found.clip_id.tolist() == ["sustained"]
    assert found.seconds_at_or_above.tolist() == [3.0]
    all_clips = find_clips(path, speed_threshold=10)
    assert all_clips.clip_id.tolist() == ["sustained", "spike"]
    assert all_clips.iloc[1].peak_speed_mps == 16


def test_unknown_speed_filter_combinations(tmp_path):
    from vl_product.source import Config, SyntheticSource

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=1, side_cm=2)))
    assert len(find_clips(path)) == 4
    assert find_clips(path, speed_threshold=1).empty
    assert find_clips(path, minimum_seconds=1).empty
    assert find_clips(path, stopped_only=True).empty
