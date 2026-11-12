"""Compatibility exports for the Application-owned motor simulation projection.

New consumers must obtain the store from Application.electrical_dynamics_composition.
This module remains as an import-compatible facade and owns no projection state.
"""
from core.application.electrical_dynamics_read_models import (
    MotorSimulationReadModelStore,
    SimulationSummary,
)

__all__ = ["MotorSimulationReadModelStore", "SimulationSummary"]
