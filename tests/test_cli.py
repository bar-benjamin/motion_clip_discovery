import json
import subprocess
import sys

import duckdb


def test_cli_creates_queryable_catalog_and_parquet(tmp_path):
    path = tmp_path / "demo.duckdb"
    export = tmp_path / "facts.parquet"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vl_product.cli",
            "--database",
            str(path),
            "--frames-per-drive",
            "3",
            "--side-cm",
            "2",
            "--export",
            str(export),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["accepted"] == 12
    with duckdb.connect() as con:
        assert con.execute("select count(*) from read_parquet(?)", [str(export)]).fetchone() == (
            12,
        )


def test_invalid_config_exits_cleanly():
    result = subprocess.run(
        [sys.executable, "-m", "vl_product.cli", "--side-cm", "0"], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "E_CONFIG" in result.stderr
