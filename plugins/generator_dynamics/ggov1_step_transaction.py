"""Transactional accepted-step protocol for sampled GGOV1 feedback.

This module defines detached immutable checkpoints and step-attempt records.
It owns no integrator, time loop, DAE state vector, persistence or scheduler.
The host creates an attempt from its committed checkpoint, evaluates the
continuous solver using that checkpoint, and commits the candidate FSR only
after the complete numerical step has been accepted. Rejection returns the
unchanged checkpoint, so intermediate RK/Newton stages cannot advance FSRA.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class GGOV1AcceptedStepCheckpoint:
    """Discrete controller feedback committed at one accepted solver boundary."""

    sample_index: int
    simulation_time_s: float
    selected_fuel_command_pu: float

    def __post_init__(self) -> None:
        if isinstance(self.sample_index, bool) or not isinstance(self.sample_index, int) or self.sample_index < 0:
            raise ValueError("sample_index must be a non-negative integer")
        time = float(self.simulation_time_s)
        command = float(self.selected_fuel_command_pu)
        if not math.isfinite(time) or time < 0.0:
            raise ValueError("simulation_time_s must be finite and non-negative")
        if not math.isfinite(command):
            raise ValueError("selected_fuel_command_pu must be finite")
        object.__setattr__(self, "simulation_time_s", time)
        object.__setattr__(self, "selected_fuel_command_pu", command)


@dataclass(frozen=True, slots=True)
class GGOV1StepAttempt:
    """One candidate accepted-step update, still detached from committed state."""

    base: GGOV1AcceptedStepCheckpoint
    candidate_time_s: float
    candidate_selected_fuel_command_pu: float

    def __post_init__(self) -> None:
        if not isinstance(self.base, GGOV1AcceptedStepCheckpoint):
            raise TypeError("base must be GGOV1AcceptedStepCheckpoint")
        time = float(self.candidate_time_s)
        command = float(self.candidate_selected_fuel_command_pu)
        if not math.isfinite(time) or time <= self.base.simulation_time_s:
            raise ValueError("candidate_time_s must advance beyond the committed checkpoint")
        if not math.isfinite(command):
            raise ValueError("candidate_selected_fuel_command_pu must be finite")
        object.__setattr__(self, "candidate_time_s", time)
        object.__setattr__(self, "candidate_selected_fuel_command_pu", command)

    def accept(self) -> GGOV1AcceptedStepCheckpoint:
        """Commit candidate discrete feedback after host solver acceptance."""
        return GGOV1AcceptedStepCheckpoint(
            sample_index=self.base.sample_index + 1,
            simulation_time_s=self.candidate_time_s,
            selected_fuel_command_pu=self.candidate_selected_fuel_command_pu,
        )

    def reject(self) -> GGOV1AcceptedStepCheckpoint:
        """Discard the candidate and preserve the exact prior checkpoint."""
        return self.base


def begin_ggov1_step_attempt(
    checkpoint: GGOV1AcceptedStepCheckpoint,
    *,
    candidate_time_s: float,
    candidate_selected_fuel_command_pu: float,
    expected_start_time_s: float,
) -> GGOV1StepAttempt:
    """Create an attempt only when the host begins at the committed boundary."""
    if not isinstance(checkpoint, GGOV1AcceptedStepCheckpoint):
        raise TypeError("checkpoint must be GGOV1AcceptedStepCheckpoint")
    start = float(expected_start_time_s)
    if not math.isfinite(start) or not math.isclose(
        start, checkpoint.simulation_time_s, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError("solver step start does not match committed GGOV1 checkpoint time")
    return GGOV1StepAttempt(
        base=checkpoint,
        candidate_time_s=candidate_time_s,
        candidate_selected_fuel_command_pu=candidate_selected_fuel_command_pu,
    )


__all__ = [
    "GGOV1AcceptedStepCheckpoint",
    "GGOV1StepAttempt",
    "begin_ggov1_step_attempt",
]
