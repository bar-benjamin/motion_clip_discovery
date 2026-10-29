import logging

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_stage_and_gate_logs_without_raw_arrays(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    report = ingest(tmp_path / "db", SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    assert {"read_reduce", "load", "derive", "quality_gate"} <= report["stage_seconds"].keys()
    assert report["rejections_by_code"] == {}
    assert "gate accepted" in caplog.text
    assert "run completed" in caplog.text
    assert "array(" not in caplog.text
