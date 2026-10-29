"""Measure a full-density replay and check its local performance budgets.

Run this script in a fresh process so peak RSS belongs to this benchmark rather
than an earlier workload. Generated data and the JSON report stay in artifacts/.
"""

import json
import platform
from pathlib import Path
from time import perf_counter, time_ns

import duckdb

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


def _peak_resident_memory_mib() -> float:
    """Normalize the OS peak-memory measurement to MiB."""
    if platform.system() not in ("Darwin", "Linux"):
        raise SystemExit("Dense benchmark unsupported on this platform; use macOS or Linux")
    import resource

    peak_memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return peak_memory / (1024 * 1024)  # macOS reports bytes.
    return peak_memory / 1024  # Linux reports KiB.


def main() -> None:
    _peak_resident_memory_mib()
    database_path = Path("artifacts/review") / f"benchmark-{time_ns()}.duckdb"
    config = Config()
    ingestion_report = ingest(database_path, SyntheticSource(config))

    query_started_at = perf_counter()
    with duckdb.connect(str(database_path), read_only=True) as connection:
        clip_summaries = connection.execute(
            "SELECT * FROM clip_summary ORDER BY clip_id"
        ).fetchall()
        frame_count, point_count = connection.execute(
            "SELECT count(*), sum(point_count) FROM frames"
        ).fetchone()
    query_seconds = perf_counter() - query_started_at
    with duckdb.connect(str(database_path), read_only=True) as connection:
        clip_started = perf_counter()
        connection.execute("SELECT * FROM clip_summary ORDER BY clip_id").fetchall()
        clip_query_seconds = perf_counter() - clip_started

    report = {
        **ingestion_report,
        "query_seconds": query_seconds,
        "clip_query_seconds": clip_query_seconds,
        "peak_rss_mib": _peak_resident_memory_mib(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "point_count": point_count,
    }
    checks = {
        "frames": frame_count == 1000,
        "points": point_count == 1_000_000_000,
        "clips": len(clip_summaries) == 4,
        "wall time": report["elapsed_seconds"] < 120,
        "memory": report["peak_rss_mib"] < 1024,
        "query": report["clip_query_seconds"] < 1,
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise SystemExit(f"Benchmark failed: {failures}: {report}")

    report_json = json.dumps(report, indent=2)
    Path("artifacts/benchmark.json").write_text(report_json + "\n")
    print(report_json)


if __name__ == "__main__":
    main()
