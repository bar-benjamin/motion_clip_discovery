from dataclasses import replace

import duckdb
import numpy as np
import pytest

from vl_product.contracts import ClipRecord, DriveRecord, Frame
from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


class ArchiveSource:
    def manifest(self):
        return {"source": "test-archive-v1"}

    def drives(self):
        return [
            DriveRecord("a", 100, 1_000_000_000, "local_world"),
            DriveRecord("b", 200, 500_000_000, "local_world"),
        ]

    def clips(self):
        return [
            ClipRecord("a1", "a", 0, 1, "mock://a1"),
            ClipRecord("a2", "a", 1, 2, "mock://a2"),
            ClipRecord("b1", "b", 0, 2, "mock://b1"),
        ]

    def frames(self):
        for d in self.drives():
            for i in range(3):
                yield Frame(
                    d.drive_id,
                    i,
                    d.start_ns + i * d.period_ns,
                    np.array([[100 * i, 0, 0]], dtype=np.int32),
                )


def test_independent_catalog_clocks_and_lineage(tmp_path):
    path = tmp_path / "catalog.duckdb"
    first = ingest(path, ArchiveSource())
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute(
            "select clip_id, observed_distance_m from clip_summary order by 1"
        ).fetchall() == [("a1", 1), ("a2", 1), ("b1", 2)]
        assert c.execute(
            "select distinct speed_mps from motion where drive_id='b' and frame_index>0"
        ).fetchall() == [(2,)]
        identity = c.execute(
            "select config_hash, input_fingerprint, catalog_fingerprint from runs "
            "WHERE run_id=(SELECT run_id FROM current_run)"
        ).fetchone()
    second = ingest(path, ArchiveSource())
    assert first["run_id"] != second["run_id"]
    with duckdb.connect(str(path), read_only=True) as c:
        assert (
            c.execute(
                "select config_hash, input_fingerprint, catalog_fingerprint from runs "
                "WHERE run_id=(SELECT run_id FROM current_run)"
            ).fetchone()
            == identity
        )


def test_invalid_catalog_does_not_consume_frames_or_replace_data(tmp_path):
    path = tmp_path / "catalog.duckdb"
    report = ingest(path, SyntheticSource(Config(side_cm=2)))

    class Invalid(ArchiveSource):
        def clips(self):
            return [replace(super().clips()[0], drive_id="missing")]

        def frames(self):
            raise AssertionError("catalog must be validated first")

    with pytest.raises(ValueError, match="E_CATALOG"):
        ingest(path, Invalid())
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute(
            "select run_id from runs WHERE run_id=(SELECT run_id FROM current_run)"
        ).fetchall() == [(report["run_id"],)]


def test_processing_modules_do_not_import_generator():
    import ast
    from pathlib import Path

    for module in ["etl", "warehouse"]:
        tree = ast.parse(Path(f"src/vl_product/{module}.py").read_text())
        assert not any(
            isinstance(n, ast.ImportFrom) and n.module == "vl_product.source"
            for n in ast.walk(tree)
        )


def test_arrival_order_and_catalog_content_have_distinct_fingerprints(tmp_path):
    source = ArchiveSource()

    class Reordered(ArchiveSource):
        def frames(self):
            return iter(reversed(list(source.frames())))

    class Changed(ArchiveSource):
        def frames(self):
            return iter([replace(f, points_cm=f.points_cm + 1) for f in source.frames()])

    class CatalogChanged(ArchiveSource):
        def clips(self):
            return [replace(c, uri=c.uri + "/new") for c in source.clips()]

    def fingerprints(adapter):
        path = tmp_path / "lineage"
        report = ingest(path, adapter)
        with duckdb.connect(str(path), read_only=True) as c:
            return c.execute(
                "select config_hash,input_fingerprint,catalog_fingerprint from runs where run_id=?",
                [report["run_id"]],
            ).fetchone()

    first = fingerprints(source)
    assert fingerprints(Reordered()) == first
    changed = fingerprints(Changed())
    assert changed[0] == first[0] and changed[1] != first[1]
    catalog = fingerprints(CatalogChanged())
    assert catalog[1] == first[1] and catalog[2] != first[2]
