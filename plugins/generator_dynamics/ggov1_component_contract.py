"""Explicit identity and per-unit handoff for a GGOV1 controller binding.

This is a plugin-side association contract, not a Core machine model or
solver registration. The canonical Core machine remains the owner of
electrical states, terminals, topology and electrical power.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


class GGOV1BindingError(ValueError):
    """Raised when a controller-to-machine binding is inconsistent."""


@dataclass(frozen=True)
class GGOV1GeneratorBinding:
    """Bind one GGOV1 controller instance to one canonical generator machine.

    machine_id and bus_id refer to existing Core identities. terminal_id
    identifies the generator's authoritative electrical terminal.
    turbine_rating_mw is separate from generator MVA rating; conversion to
    the study system per-unit base is explicit.
    """

    component_id: str
    machine_id: str
    bus_id: str
    terminal_id: str
    turbine_rating_mw: float
    system_base_mva: float

    def __post_init__(self) -> None:
        for field_name in ("component_id", "machine_id", "bus_id", "terminal_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise GGOV1BindingError(f"{field_name} must be a non-empty stable identity")
        for field_name in ("turbine_rating_mw", "system_base_mva"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GGOV1BindingError(f"{field_name} must be a finite positive number")
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise GGOV1BindingError(f"{field_name} must be a finite positive number")

    @property
    def system_to_turbine_power_base_ratio(self) -> float:
        """Return system MVA base divided by turbine MW rating."""
        return float(self.system_base_mva) / float(self.turbine_rating_mw)

    def mechanical_power_handoff(
        self,
        *,
        turbine_mechanical_power_pu: float,
        simulation_time_s: float,
        accepted_sample_index: int,
    ) -> "GGOV1MechanicalPowerHandoff":
        """Convert turbine-base pu output to system-base pu without mutation."""
        return GGOV1MechanicalPowerHandoff(
            component_id=self.component_id,
            machine_id=self.machine_id,
            bus_id=self.bus_id,
            terminal_id=self.terminal_id,
            turbine_mechanical_power_pu=turbine_mechanical_power_pu,
            system_mechanical_power_pu=(
                float(turbine_mechanical_power_pu)
                / self.system_to_turbine_power_base_ratio
            ),
            turbine_rating_mw=float(self.turbine_rating_mw),
            system_base_mva=float(self.system_base_mva),
            simulation_time_s=simulation_time_s,
            accepted_sample_index=accepted_sample_index,
        )


@dataclass(frozen=True)
class GGOV1MechanicalPowerHandoff:
    """Immutable, provenance-bearing governor output for a Core machine.

    The consumer must verify that machine/bus/terminal identities still match
    the active study snapshot. This value is not itself a Core mutation.
    """

    component_id: str
    machine_id: str
    bus_id: str
    terminal_id: str
    turbine_mechanical_power_pu: float
    system_mechanical_power_pu: float
    turbine_rating_mw: float
    system_base_mva: float
    simulation_time_s: float
    accepted_sample_index: int

    def __post_init__(self) -> None:
        for field_name in ("component_id", "machine_id", "bus_id", "terminal_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise GGOV1BindingError(f"{field_name} must be a non-empty stable identity")
        for field_name in (
            "turbine_mechanical_power_pu",
            "system_mechanical_power_pu",
            "turbine_rating_mw",
            "system_base_mva",
            "simulation_time_s",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GGOV1BindingError(f"{field_name} must be finite numeric data")
            if not math.isfinite(float(value)):
                raise GGOV1BindingError(f"{field_name} must be finite numeric data")
        if self.turbine_rating_mw <= 0.0 or self.system_base_mva <= 0.0:
            raise GGOV1BindingError("power bases must be positive")
        if self.simulation_time_s < 0.0:
            raise GGOV1BindingError("simulation_time_s must be non-negative")
        if type(self.accepted_sample_index) is not int or self.accepted_sample_index < 0:
            raise GGOV1BindingError("accepted_sample_index must be a non-negative integer")
        expected = (
            self.turbine_mechanical_power_pu
            * self.turbine_rating_mw
            / self.system_base_mva
        )
        if not math.isclose(
            self.system_mechanical_power_pu, expected, rel_tol=1e-10, abs_tol=1e-12
        ):
            raise GGOV1BindingError("mechanical-power handoff has inconsistent per-unit bases")


__all__ = [
    "GGOV1BindingError",
    "GGOV1GeneratorBinding",
    "GGOV1MechanicalPowerHandoff",
]
