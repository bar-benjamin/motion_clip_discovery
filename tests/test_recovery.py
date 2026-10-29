import subprocess
import sys

import duckdb
import pytest
from source_fixture import FixtureSource

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def test_keyboard_interrupt_preserves_published_and_retained_rows(tmp_path):
    path = tmp_path / "db"
    source = SyntheticSource(Config(frames_per_drive=2, side_cm=2))
    first = ingest(path, source)

    def interrupted():
        yield next(source.frames())
        raise KeyboardInterrupt("cancelled")

    with pytest.raises(KeyboardInterrupt):
        ingest(path, FixtureSource(source.config, interrupted()))
    with duckdb.connect(str(path), read_only=True) as c:
        assert c.execute("select count(*) from frames_history").fetchone() == (8,)
        assert c.execute("select run_id from current_run").fetchone() == (first["run_id"],)
        assert c.execute("select count(*) from runs").fetchone() == (1,)
        assert "KeyboardInterrupt" in c.execute("select reason from run_attempts").fetchone()[0]


def test_cli_reports_lock_held_by_another_reader_process(tmp_path):
    path = tmp_path / "db"
    ingest(path, SyntheticSource(Config(frames_per_drive=2, side_cm=2)))
    code = (
        "import duckdb,sys; c=duckdb.connect(sys.argv[1],read_only=True); "
        'print("READY",flush=True); sys.stdin.readline()'
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        result = subprocess.run(
            [sys.executable, "-m", "vl_product.cli", "--database", str(path), "--side-cm", "2"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 3
        assert "E_STORAGE" in result.stderr
    finally:
        child.communicate("\n", timeout=10)


@pytest.mark.parametrize("translation", [(-100, 0, 7), (0, 300, -25), (999, -999, 0)])
def test_reduction_translation_invariant(translation):
    from dataclasses import replace

    import numpy as np

    from vl_product.etl import reduce_frame

    source = SyntheticSource(Config(side_cm=3))
    original = next(source.frames())
    a = reduce_frame(original, source.drives()[0])
    b = reduce_frame(
        replace(original, points_cm=original.points_cm + np.array(translation)), source.drives()[0]
    )
    for axis, delta in zip("xyz", translation, strict=True):
        for field in (f"{axis}_m", f"min_{axis}_m", f"max_{axis}_m"):
            assert b[field] == pytest.approx(a[field] + delta / 100)
    assert a["content_hash"] != b["content_hash"]
