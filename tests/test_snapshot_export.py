import hashlib
import json

import duckdb
import pytest

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_bundle_checksums_and_readback(tmp_path):
    from vl_product.exports import export_snapshot

    path = tmp_path / "db"
    report = ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    target = tmp_path / "snapshot"
    export_snapshot(path, target)
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["run_id"] == report["run_id"]
    assert manifest["schema_version"] == 3
    with duckdb.connect() as c:
        for name, info in manifest["files"].items():
            file = target / name
            assert hashlib.sha256(file.read_bytes()).hexdigest() == info["sha256"]
            assert c.execute("select count(*) from read_parquet(?)", [str(file)]).fetchone() == (
                info["rows"],
            )
    with pytest.raises(ValueError, match="E_EXPORT"):
        export_snapshot(path, target)


def test_bundle_failure_removes_partial_output(tmp_path, monkeypatch):
    import vl_product.exports as exports

    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    monkeypatch.setattr(
        exports, "_file_hash", lambda path: (_ for _ in ()).throw(OSError("disk failure"))
    )
    target = tmp_path / "snapshot"
    with pytest.raises(OSError, match="disk failure"):
        exports.export_snapshot(path, target)
    assert not target.exists()
