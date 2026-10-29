"""Versioned interpretation of adjacent-frame motion."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisRules:
    rule_version: int = 1
    stop_speed_mps: float = 0.5
