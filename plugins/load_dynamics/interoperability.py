"""Load adapter to the shared electrical-dynamics contracts."""
from __future__ import annotations

from dataclasses import dataclass
from math import acos, isfinite, tan
from typing import Sequence

from core.solver.dynamics.component_contract import DynamicTerminalInjection
from core.model import EquipmentType

from plugins.electrical_dynamics.contracts import (
    DynamicsEquipmentKind, DynamicsModelCapability, DynamicsModelReadiness,
    MeasurementPointContract, normalize_measurement_points,
)
from .catalog import LoadConnection, LoadDemandProfile, LoadFamily, LoadNameplate, ZIPLoadModel
from .instrumentation import load_measurement_points

PLUGIN_ID = "gridforge.plugin.load_dynamics"



@dataclass(frozen=True, slots=True)
class LoadPhaseTerminal:
    """Host-resolved terminal/phase mapping and electrical per-unit bases.

    terminal_voltage_v and phase_voltage_base_v must use the same branch
    voltage definition (line-neutral, line-line, delta branch or DC pair).
    phase_power_base_va is the matching per-branch apparent-power base.
    power_share allocates the equipment's total demand across its branches.
    """

    injection_id: str
    terminal_id: str
    phase_id: str
    terminal_voltage_v: complex
    phase_voltage_base_v: float
    phase_power_base_va: float
    power_share: float

    def __post_init__(self) -> None:
        for name in ("injection_id", "terminal_id", "phase_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty stable identity")
            object.__setattr__(self, name, value.strip())
        try:
            voltage = complex(self.terminal_voltage_v)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("terminal_voltage_v must be finite complex data") from exc
        if not isfinite(voltage.real) or not isfinite(voltage.imag):
            raise ValueError("terminal_voltage_v must be finite complex data")
        object.__setattr__(self, "terminal_voltage_v", voltage)
        for name in ("phase_voltage_base_v", "phase_power_base_va", "power_share"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite numeric data")
            if name != "power_share" and value <= 0.0:
                raise ValueError(f"{name} must be positive")
            if name == "power_share" and not 0.0 < value <= 1.0:
                raise ValueError("power_share must be in (0, 1]")
            object.__setattr__(self, name, float(value))


def evaluate_zip_terminal_injections(
    *,
    nameplate: LoadNameplate,
    zip_model: ZIPLoadModel,
    time_s: float,
    phase_terminals: Sequence[LoadPhaseTerminal],
    profile: LoadDemandProfile | None = None,
) -> tuple[DynamicTerminalInjection, ...]:
    """Evaluate a ZIP load as phase-resolved network current injections.

    The host supplies the canonical terminal-to-branch mapping and matching
    voltage/apparent-power bases. Shares must allocate the total load demand
    exactly once. Positive injection is equipment-to-network, so consuming
    load current is negative. This is a reduced ZIP representation, not a
    detailed motor, converter, furnace or unbalanced device model.
    """
    if not isinstance(nameplate, LoadNameplate):
        raise TypeError("nameplate must be LoadNameplate")
    if not isinstance(zip_model, ZIPLoadModel):
        raise TypeError("zip_model must be ZIPLoadModel")
    if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) or not isfinite(float(time_s)) or time_s < 0.0:
        raise ValueError("time_s must be finite and non-negative")
    if profile is not None and not isinstance(profile, LoadDemandProfile):
        raise TypeError("profile must be LoadDemandProfile or None")
    phases = tuple(phase_terminals)
    if not phases or any(not isinstance(item, LoadPhaseTerminal) for item in phases):
        raise ValueError("phase_terminals must contain LoadPhaseTerminal values")
    injection_ids = [item.injection_id for item in phases]
    if len(injection_ids) != len(set(injection_ids)):
        raise ValueError("load phase injection IDs must be unique")
    if abs(sum(item.power_share for item in phases) - 1.0) > 1e-9:
        raise ValueError("phase power shares must sum to 1.0 to avoid duplicate or omitted demand")

    p0_kw = nameplate.rated_active_power_kw
    if nameplate.connection in {LoadConnection.DC_TWO_WIRE, LoadConnection.DC_THREE_WIRE}:
        q0_kvar = 0.0
    else:
        q0_kvar = p0_kw * tan(acos(nameplate.rated_power_factor))
    active_multiplier, reactive_multiplier = (
        profile.multipliers(float(time_s)) if profile is not None else (1.0, 1.0)
    )

    result: list[DynamicTerminalInjection] = []
    for phase in phases:
        voltage_pu = phase.terminal_voltage_v / phase.phase_voltage_base_v
        magnitude_pu = abs(voltage_pu)
        if not isfinite(magnitude_pu) or magnitude_pu <= 1e-12:
            raise ValueError(
                f"ZIP constant-power current is undefined at terminal {phase.terminal_id!r} near zero voltage"
            )
        p_kw, q_kvar = zip_model.demand(p0_kw, q0_kvar, magnitude_pu)
        p_va = p_kw * 1000.0 * active_multiplier * phase.power_share
        q_var = q_kvar * 1000.0 * reactive_multiplier * phase.power_share
        apparent_power_pu = complex(p_va, q_var) / phase.phase_power_base_va
        current_injection_pu = -complex(apparent_power_pu / voltage_pu).conjugate()
        result.append(DynamicTerminalInjection(
            injection_id=phase.injection_id,
            terminal_id=phase.terminal_id,
            phase_id=phase.phase_id,
            current_injection_pu=current_injection_pu,
        ))
    return tuple(result)


def load_measurement_contracts(family: LoadFamily) -> tuple[MeasurementPointContract, ...]:
    if not isinstance(family, LoadFamily):
        raise TypeError("family must be LoadFamily")
    return normalize_measurement_points(
        load_measurement_points(family),
        equipment_kind=DynamicsEquipmentKind.LOAD,
        family_id=family.value,
    )


def load_model_capability(family: LoadFamily) -> DynamicsModelCapability:
    if not isinstance(family, LoadFamily):
        raise TypeError("family must be LoadFamily")
    points = load_measurement_contracts(family)
    # The initial executable shared electrical representation is ZIP demand.
    # Family-specific transient solvers are not advertised by this catalogue.
    return DynamicsModelCapability(
        plugin_id=PLUGIN_ID,
        equipment_kind=DynamicsEquipmentKind.LOAD,
        family_id=family.value,
        model_id=f"load.zip.{family.value}",
        readiness=DynamicsModelReadiness.EXPERIMENTAL,
        study_types=frozenset(),
        numerical_workloads=frozenset(),
        measurement_point_ids=frozenset(point.point_id for point in points),
    )


def load_equipment_type():
    """Return the canonical Core equipment discriminator."""
    return EquipmentType.LOAD


__all__ = [
    "LoadPhaseTerminal", "evaluate_zip_terminal_injections",
    "load_measurement_contracts", "load_model_capability", "load_equipment_type",
]
