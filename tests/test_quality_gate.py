from dataclasses import replace

import duckdb
import pytest
from source_fixture import FixtureSource

from vl_product.source import Config, SyntheticSource, frames
from vl_product.warehouse import ingest


def snapshot(path):
    with duckdb.connect(str(path), read_only=True) as c:
        tables = c.execute(
            "select table_name from information_schema.tables "
            "where table_type='BASE TABLE' and table_name!='run_attempts' order by 1"
        ).fetchall()
        return {
            name: sorted(
                c.execute(f"SELECT to_json(t)::VARCHAR FROM {name} t").fetchall(), key=repr
            )
            for (name,) in tables
        }


@pytest.mark.parametrize("empty", [True, False])
def test_bad_run_cannot_replace_good_catalog(tmp_path, empty):
    path = tmp_path / "catalog.duckdb"
    config = Config(frames_per_drive=2, side_cm=2)
    ingest(path, SyntheticSource(config))
    before = snapshot(path)
    inputs = [] if empty else [replace(f, units="m") for f in frames(config)]
    with pytest.raises(ValueError, match="E_QUALITY_GATE"):
        ingest(path, FixtureSource(config, inputs))
    assert snapshot(path) == before


def test_cli_quality_failure_is_json_and_exit_four(tmp_path, monkeypatch, capsys):
    import json
    import sys

    from vl_product.cli import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vl-demo",
            "--database",
            str(tmp_path / "db"),
            "--side-cm",
            "2",
            "--min-accepted-frames",
            "1001",
        ],
    )
    assert main() == 4
    report = json.loads(capsys.readouterr().err)
    assert report["code"] == "E_QUALITY_GATE"
    assert report["accepted"] == 1000
    assert report["failed_rules"] == [
        {"rule": "min_accepted_frames", "threshold": 1001, "actual": 1000}
    ]


@pytest.mark.parametrize("rule", ["max_rejected_ratio", "max_missing_ratio"])
def test_each_quality_ratio_rejects_default_publication(tmp_path, rule):
    from vl_product.quality import QualityGateError, QualityPolicy

    config = Config(frames_per_drive=4, side_cm=2)
    source = list(frames(config))
    source[0] = replace(source[0], units="m")
    policy = QualityPolicy(
        max_rejected_ratio=1 if rule == "max_missing_ratio" else 0.01,
        max_missing_ratio=1 if rule == "max_rejected_ratio" else 0.01,
    )
    with pytest.raises(QualityGateError) as failure:
        ingest(tmp_path / "db", FixtureSource(config, source), policy)
    assert any(item["rule"] == rule for item in failure.value.report["failed_rules"])
