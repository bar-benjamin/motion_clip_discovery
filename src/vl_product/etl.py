"""Validate a source frame, fingerprint its contents, and reduce it to meter-based facts."""

import hashlib
import json
import logging
import re
from typing import TypedDict

import numpy as np

from vl_product.contracts import MAX_FRAMES_PER_DRIVE, MAX_POINTS_PER_FRAME, DriveRecord, Frame

CENTIMETERS_PER_METER = 100
MAX_ABSOLUTE_COORDINATE_CM = 1_000_000_000


class FrameFact(TypedDict):
    """One warehouse row before ingestion adds source ordinal and run identity."""

    drive_id: str
    frame_index: int
    timestamp_ns: int
    point_count: int
    content_hash: str
    x_m: float
    min_x_m: float
    max_x_m: float
    y_m: float
    min_y_m: float
    max_y_m: float
    z_m: float
    min_z_m: float
    max_z_m: float


class DataError(ValueError):
    """A rejected source record, with a stable code for the quality audit."""

    def __init__(self, code: str, message: str):
        logging.getLogger(__name__).debug("Frame validation rejected: %s", code)
        self.code = code
        super().__init__(f"{code}: {message}")


def _validate_metadata(frame: Frame, drive: DriveRecord) -> None:
    """Check the envelope and synthetic clock before inspecting its large array."""
    if (
        not isinstance(frame, Frame)
        or type(frame.schema_version) is not int
        or frame.schema_version != 1
    ):
        raise DataError("E_SCHEMA", "Expected a version 1 Frame")
    if not isinstance(frame.units, str) or frame.units != "cm":
        raise DataError("E_UNITS", "Coordinates must use centimeters")
    if not isinstance(frame.coordinate_system, str) or frame.coordinate_system != "local_world":
        raise DataError("E_COORDINATE_SYSTEM", "Expected local_world Cartesian coordinates")

    if not isinstance(frame.drive_id, str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,64}", frame.drive_id
    ):
        raise DataError("E_METADATA", "Invalid drive ID or frame index")
    if type(frame.frame_index) is not int or not 0 <= frame.frame_index < MAX_FRAMES_PER_DRIVE:
        raise DataError("E_METADATA", "Invalid drive ID or frame index")

    if frame.drive_id != drive.drive_id or frame.coordinate_system != drive.coordinate_system:
        raise DataError("E_METADATA", "Frame does not belong to the declared drive")

    expected_timestamp_ns = drive.start_ns + frame.frame_index * drive.period_ns
    if type(frame.timestamp_ns) is not int or frame.timestamp_ns != expected_timestamp_ns:
        raise DataError("E_TIMESTAMP", "Timestamp does not match the declared drive clock")


def _validate_points(points: np.ndarray) -> None:
    """Reject invalid shape, values or units before converting coordinates to int32."""
    if not isinstance(points, np.ndarray) or points.ndim != 2:
        raise DataError("E_SHAPE", "Expected 1 to 1,000,000 XYZ points")
    if points.shape[1] != 3 or not 1 <= len(points) <= MAX_POINTS_PER_FRAME:
        raise DataError("E_SHAPE", "Expected 1 to 1,000,000 XYZ points")

    numeric_coordinate_types = "iuf"  # Signed integer, unsigned integer or float.
    if points.dtype.kind not in numeric_coordinate_types or (
        points.dtype.kind == "f" and not np.isfinite(points).all()
    ):
        raise DataError("E_COORDINATES", "Coordinates must be finite numbers within ±1e9 cm")
    # Comparisons avoid signed-minimum abs overflow and dense float64 copies.
    if points.min() < -MAX_ABSOLUTE_COORDINATE_CM or points.max() > MAX_ABSOLUTE_COORDINATE_CM:
        raise DataError("E_COORDINATES", "Coordinates must be finite numbers within ±1e9 cm")
    if points.dtype.kind == "f" and not np.equal(points, np.floor(points)).all():
        raise DataError("E_COORDINATES", "Coordinates must lie on the integer centimeter grid")


def _content_hash(frame: Frame, canonical_points: np.ndarray) -> str:
    """Hash metadata and ordered little-endian int32 XYZ bytes for duplicate detection.

    Equivalent integer/float input arrays produce the same hash. Reordering the
    points changes the hash, even when the geometric shape is unchanged.
    """
    metadata = (
        frame.drive_id,
        frame.frame_index,
        frame.timestamp_ns,
        frame.units,
        frame.coordinate_system,
        frame.schema_version,
    )
    digest = hashlib.sha256(json.dumps(metadata).encode())
    digest.update(canonical_points.tobytes())
    return digest.hexdigest()


def reduce_frame(frame: Frame, drive: DriveRecord) -> FrameFact:
    """Return the center, bounds, point count and provenance of one valid cloud.

    The caller may quarantine DataError and continue with later frames. Other
    failures must propagate so ingestion can roll back its transaction.
    """
    _validate_metadata(frame, drive)
    _validate_points(frame.points_cm)

    # A fixed byte order and memory layout make the content hash reproducible.
    canonical_points = np.asarray(frame.points_cm, dtype="<i4", order="C")
    x_cm = canonical_points[:, 0]
    y_cm = canonical_points[:, 1]
    z_cm = canonical_points[:, 2]

    return {
        "drive_id": frame.drive_id,
        "frame_index": frame.frame_index,
        "timestamp_ns": frame.timestamp_ns,
        "point_count": len(canonical_points),
        "content_hash": _content_hash(frame, canonical_points),
        "x_m": float(x_cm.mean(dtype=np.float64) / CENTIMETERS_PER_METER),
        "min_x_m": float(x_cm.min() / CENTIMETERS_PER_METER),
        "max_x_m": float(x_cm.max() / CENTIMETERS_PER_METER),
        "y_m": float(y_cm.mean(dtype=np.float64) / CENTIMETERS_PER_METER),
        "min_y_m": float(y_cm.min() / CENTIMETERS_PER_METER),
        "max_y_m": float(y_cm.max() / CENTIMETERS_PER_METER),
        "z_m": float(z_cm.mean(dtype=np.float64) / CENTIMETERS_PER_METER),
        "min_z_m": float(z_cm.min() / CENTIMETERS_PER_METER),
        "max_z_m": float(z_cm.max() / CENTIMETERS_PER_METER),
    }
