"""Source-independent envelopes, catalog records and ingestion limits."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

import numpy as np

MAX_FRAMES_PER_DRIVE = 100_000
MAX_POINTS_PER_FRAME = 1_000_000
SourceManifest: TypeAlias = dict[str, object]


@dataclass(frozen=True)
class Frame:
    """Ordered centimeter-grid XYZ points with an exact acquisition clock."""

    drive_id: str
    frame_index: int
    timestamp_ns: int
    points_cm: np.ndarray
    units: str = "cm"
    coordinate_system: str = "local_world"
    schema_version: int = 1


@dataclass(frozen=True)
class DriveRecord:
    drive_id: str
    start_ns: int
    period_ns: int
    coordinate_system: str


@dataclass(frozen=True)
class ClipRecord:
    clip_id: str
    drive_id: str
    start_frame: int
    end_frame: int
    uri: str


class FrameSource(Protocol):
    def manifest(self) -> SourceManifest: ...
    def drives(self) -> Sequence[DriveRecord]: ...
    def clips(self) -> Sequence[ClipRecord]: ...
    def frames(self) -> Iterator[Frame]: ...
