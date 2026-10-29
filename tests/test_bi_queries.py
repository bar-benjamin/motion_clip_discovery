from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_explore_uses_one_read_transaction(tmp_path, monkeypatch):
    import vl_product.bi_queries as bi

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    original = bi.duckdb.connect
    opened = []

    def connect(*args, **kwargs):
        opened.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(bi.duckdb, "connect", connect)
    result = bi.explore(path, ["clip-steady"], bi.ObservationFilter())
    assert len(result["frames"]) == 2
    assert opened == [{"read_only": True}]


def test_named_queries_and_typed_acquisition_time(tmp_path):
    from vl_product.bi_queries import read_bi
    from vl_product.warehouse import query

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    assert len(read_bi(path, "drive_ids")) == 4
    assert len(read_bi(path, "current_metadata")) == 1
    assert (
        query(path, "select typeof(acquisition_ts) as t from motion limit 1").iloc[0, 0]
        == "TIMESTAMP_NS"
    )
