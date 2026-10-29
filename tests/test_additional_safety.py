import duckdb
import pytest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import export_frames, ingest


def test_export_cannot_overwrite_file_created_after_preflight(tmp_path, monkeypatch):
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    target = tmp_path / "export.parquet"
    original = duckdb.connect

    def racing_connect(*args, **kwargs):
        target.write_bytes(b"important concurrent file")
        return original(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", racing_connect)
    with pytest.raises(ValueError, match="E_EXPORT"):
        export_frames(path, target)
    assert target.read_bytes() == b"important concurrent file"


def test_schema_marker_alone_does_not_authorize_unrelated_database(tmp_path):
    path = tmp_path / "db"
    with duckdb.connect(str(path)) as c:
        c.execute("create table vl_schema(version integer); insert into vl_schema values (3)")
        c.execute(
            "create table private_data(value varchar); insert into private_data values ('preserve')"
        )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="E_DATABASE"):
        ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    assert path.read_bytes() == before
