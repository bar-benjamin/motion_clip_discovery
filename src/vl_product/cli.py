"""Generate and ingest the mock catalog from the command line."""

import argparse
import json
import logging
import sys
from pathlib import Path

import duckdb

from vl_product.exports import export_snapshot
from vl_product.quality import QualityGateError, QualityPolicy
from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import export_frames, ingest, validate_export_destination


def _parse_arguments() -> argparse.Namespace:
    """Define the command-line interface independently of pipeline execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/catalog.duckdb"))
    parser.add_argument("--frames-per-drive", type=int, default=250)
    parser.add_argument(
        "--side-cm",
        type=int,
        default=100,
        help="Cube side on the 1 cm grid; use 2 for a fast small fixture",
    )
    parser.add_argument(
        "--export-snapshot", type=Path, help="New self-describing snapshot directory"
    )
    parser.add_argument("--export", type=Path, help="Optional reduced frame Parquet export")
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING"
    )
    parser.add_argument("--retention", type=int, default=3)
    parser.add_argument("--min-accepted-frames", type=int, default=1)
    parser.add_argument("--max-rejected-ratio", type=float, default=0.01)
    parser.add_argument("--max-missing-ratio", type=float, default=0.01)
    return parser.parse_args()


def main() -> int:
    """Run ingestion, optionally export facts, and report a stable process exit code."""
    arguments = _parse_arguments()
    logging.basicConfig(level=arguments.log_level, format="%(message)s")
    logging.getLogger(__name__).info("CLI ingestion requested")
    try:
        config = Config(
            frames_per_drive=arguments.frames_per_drive,
            side_cm=arguments.side_cm,
        )
        policy = QualityPolicy(
            arguments.min_accepted_frames, arguments.max_rejected_ratio, arguments.max_missing_ratio
        )
        if arguments.export:
            validate_export_destination(arguments.database, arguments.export)
        if arguments.export_snapshot:
            validate_export_destination(arguments.database, arguments.export_snapshot)
        report = ingest(
            arguments.database, SyntheticSource(config), policy, retention=arguments.retention
        )
        print(json.dumps(report, sort_keys=True))
        if arguments.export:
            export_frames(arguments.database, arguments.export)
        if arguments.export_snapshot:
            export_snapshot(arguments.database, arguments.export_snapshot)
        return 0
    except QualityGateError as error:
        print(json.dumps(error.report, sort_keys=True), file=sys.stderr)
        return 4
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, duckdb.Error) as error:
        print(f"E_STORAGE: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
