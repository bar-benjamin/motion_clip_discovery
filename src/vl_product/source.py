"""Generate reproducible dense point clouds with a logical cadence of 16 FPS.

Frames are yielded as quickly as the consumer can process them. The timestamps,
not wall-clock delays, represent the source's 16 FPS sampling rate.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from vl_product.contracts import (
    MAX_FRAMES_PER_DRIVE,
    ClipRecord,
    DriveRecord,
    Frame,
    SourceManifest,
)

PERIOD_NS = 62_500_000
START_NS = 1_735_689_600_000_000_000  # 2025-01-01 00:00:00 UTC
MAX_CUBE_SIDE_CM = 100
SCENARIOS = ("steady", "stop_start", "turn", "stationary")
GENERATOR_VERSION = "rigid-cube-v1"


@dataclass(frozen=True)
class Config:
    """Size of each drive and cube; defaults produce 1,000 million-point frames."""

    frames_per_drive: int = 250
    side_cm: int = 100

    def __post_init__(self) -> None:
        # bool is an int subclass, but True must not silently become one frame.
        if type(self.frames_per_drive) is not int or not (
            1 <= self.frames_per_drive <= MAX_FRAMES_PER_DRIVE
        ):
            raise ValueError(
                f"E_CONFIG: frames_per_drive must be an integer from 1 to {MAX_FRAMES_PER_DRIVE}"
            )
        if type(self.side_cm) is not int or not 1 <= self.side_cm <= MAX_CUBE_SIDE_CM:
            raise ValueError(f"E_CONFIG: side_cm must be an integer from 1 to {MAX_CUBE_SIDE_CM}")

    def manifest(self) -> SourceManifest:
        """Return JSON-compatible source settings, without any raw point arrays."""
        return {
            "generator": GENERATOR_VERSION,
            "frames_per_drive": self.frames_per_drive,
            "side_cm": self.side_cm,
            "period_ns": PERIOD_NS,
        }


def _build_cube_points(side_cm: int) -> np.ndarray:
    """List every centimeter-grid position inside the cube, in stable XYZ order."""
    coordinates = np.arange(side_cm, dtype=np.int32)
    x_grid, y_grid, z_grid = np.meshgrid(coordinates, coordinates, coordinates, indexing="ij")
    return np.column_stack((x_grid.ravel(), y_grid.ravel(), z_grid.ravel()))


def _position_cm(scenario: str, frame_index: int, midpoint: int) -> tuple[int, int, int]:
    """Return the cube's translation; shape and orientation remain unchanged."""
    if scenario == "steady":
        return (100 * frame_index, 0, 0)
    if scenario == "stop_start":
        moving_intervals = max(0, frame_index - midpoint)
        return (50 * moving_intervals, 500, 0)
    if scenario == "turn":
        x_intervals = min(frame_index, midpoint)
        y_intervals = max(0, frame_index - midpoint)
        return (60 * x_intervals, 60 * y_intervals, 0)
    return (0, 1000, 0)  # The fourth declared scenario is stationary.


def frames(config: Config) -> Iterator[Frame]:
    """Yield each scenario one frame at a time, reusing a single cube template."""
    cube_points = _build_cube_points(config.side_cm)
    midpoint = config.frames_per_drive // 2

    for drive_id in SCENARIOS:
        for frame_index in range(config.frames_per_drive):
            position = _position_cm(drive_id, frame_index, midpoint)
            translation_cm = np.asarray(position, dtype=np.int32)
            timestamp_ns = START_NS + frame_index * PERIOD_NS

            yield Frame(
                drive_id=drive_id,
                frame_index=frame_index,
                timestamp_ns=timestamp_ns,
                points_cm=cube_points + translation_cm,
            )


@dataclass(frozen=True)
class SyntheticSource:
    """The demo adapter supplies independent catalog metadata and frame iteration."""

    config: Config

    def manifest(self) -> SourceManifest:
        return self.config.manifest()

    def drives(self) -> list[DriveRecord]:
        return [DriveRecord(name, START_NS, PERIOD_NS, "local_world") for name in SCENARIOS]

    def clips(self) -> list[ClipRecord]:
        return [
            ClipRecord(f"clip-{name}", name, 0, self.config.frames_per_drive - 1, f"mock://{name}")
            for name in SCENARIOS
        ]

    def frames(self) -> Iterator[Frame]:
        return frames(self.config)
