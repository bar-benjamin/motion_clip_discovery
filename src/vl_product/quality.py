"""Explicit acceptance policy for publishing a complete catalog."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class QualityPolicy:
    min_accepted_frames: int = 1
    max_rejected_ratio: float = 0.01
    max_missing_ratio: float = 0.01

    def __post_init__(self) -> None:
        if type(self.min_accepted_frames) is not int or self.min_accepted_frames < 0:
            raise ValueError("E_CONFIG: Minimum accepted frames must be a nonnegative integer")
        for ratio in (self.max_rejected_ratio, self.max_missing_ratio):
            if not isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError("E_CONFIG: Quality ratios must be finite and between 0 and 1")


class QualityGateError(ValueError):
    def __init__(self, report: dict[str, object]):
        self.report = {"code": "E_QUALITY_GATE", **report}
        super().__init__("E_QUALITY_GATE: Catalog failed acceptance policy")
