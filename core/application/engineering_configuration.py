# ============================================================
# File: core/application/engineering_configuration.py
# GridForge V2 — Application engineering update preparation
# Author: Subhendu Mishra
# ============================================================
"""Application-owned translation of typed engineering intent to update commands."""

from __future__ import annotations

import inspect
from typing import Any

from .command import Command
from .commands.model_commands import (
    UpdateBusCommand, UpdateGridCommand, UpdateGeneratorCommand, UpdateLoadCommand,
    UpdateShuntCommand, UpdateTransformerCommand, UpdateCableCommand, UpdateLineCommand,
    UpdateSwitchCommand, UpdateDisconnectorCommand, UpdateFuseCommand,
)
from .commands.breaker_commands import UpdateBreakerCommand
from .commands.capacitor_commands import UpdateCapacitorCommand
from .commands.reactor_commands import UpdateReactorCommand
from .commands.motor_commands import UpdateMotorCommand
from .commands.synchronous_machine_commands import UpdateSynchronousMachineCommand
from .commands.solar_commands import UpdateSolarCommand
from .commands.battery_commands import UpdateBatteryCommand
from .commands.measurement_commands import UpdateCurrentTransformerCommand, UpdateCapacitiveVoltageTransformerCommand
from .commands.pt_commands import UpdatePTCommand
from .commands.relay_commands import UpdateRelayCommand

_UPDATE_COMMANDS = {
    "bus": (UpdateBusCommand, "bus_id"),
    "buses": (UpdateBusCommand, "bus_id"),
    "grid": (UpdateGridCommand, "grid_id"),
    "generator": (UpdateGeneratorCommand, "generator_id"),
    "load": (UpdateLoadCommand, "load_id"),
    "shunt": (UpdateShuntCommand, "shunt_id"),
    "transformer": (UpdateTransformerCommand, "transformer_id"),
    "cable": (UpdateCableCommand, "cable_id"),
    "line": (UpdateLineCommand, "line_id"),
    "lines": (UpdateLineCommand, "line_id"),
    "switch": (UpdateSwitchCommand, "switch_id"),
    "disconnector": (UpdateDisconnectorCommand, "disconnector_id"),
    "fuse": (UpdateFuseCommand, "fuse_id"),
    "breaker": (UpdateBreakerCommand, "breaker_id"),
    "capacitor": (UpdateCapacitorCommand, "capacitor_id"),
    "reactor": (UpdateReactorCommand, "reactor_id"),
    "motor": (UpdateMotorCommand, "motor_id"),
    "synchronous_machine": (UpdateSynchronousMachineCommand, "synchronous_machine_id"),
    "synchronous_machines": (UpdateSynchronousMachineCommand, "synchronous_machine_id"),
    "solar": (UpdateSolarCommand, "solar_id"),
    "battery": (UpdateBatteryCommand, "battery_id"),
    "ct": (UpdateCurrentTransformerCommand, "transformer_id"),
    "current_transformer": (UpdateCurrentTransformerCommand, "transformer_id"),
    "current_transformers": (UpdateCurrentTransformerCommand, "transformer_id"),
    "pt": (UpdatePTCommand, "pt_id"),
    "potential_transformer": (UpdatePTCommand, "pt_id"),
    "potential_transformers": (UpdatePTCommand, "pt_id"),
    "cvt": (UpdateCapacitiveVoltageTransformerCommand, "transformer_id"),
    "capacitive_voltage_transformer": (UpdateCapacitiveVoltageTransformerCommand, "transformer_id"),
    "capacitive_voltage_transformers": (UpdateCapacitiveVoltageTransformerCommand, "transformer_id"),
    "relay": (UpdateRelayCommand, "relay_id"),
}


class EngineeringUpdatePreparer:
    """Prepare one immutable update command from typed engineering intent."""

    @classmethod
    def prepare(cls, intent: Any) -> Command:
        element_type = str(getattr(intent, "element_type", "")).strip().lower()
        element_id = str(getattr(intent, "element_id", "")).strip()
        values = dict(getattr(intent, "values", {}) or {})
        if not element_type or not element_id:
            raise ValueError("Engineering intent requires element_type and element_id.")
        entry = _UPDATE_COMMANDS.get(element_type)
        if entry is None:
            raise KeyError(f"No Application engineering update command for {element_type!r}.")
        command_class, id_field = entry
        if element_type == "generator":
            limit_fields = ("q_min", "q_max")
            supplied_limit_fields = tuple(field for field in limit_fields if field in values)
            if supplied_limit_fields:
                if supplied_limit_fields != limit_fields:
                    raise ValueError(
                        "Generator reactive-power limits must be updated together by supplying q_min and q_max."
                    )
                if "q_limits" in values:
                    raise ValueError(
                        "Specify Generator q_min/q_max or q_limits, not both."
                    )
                values["q_limits"] = (values.pop("q_min"), values.pop("q_max"))
        if element_type == "grid":
            base_fields = ("impedance_base_mva", "impedance_base_voltage_kv")
            supplied_base_fields = tuple(field for field in base_fields if field in values)
            if any(values[field] is None for field in supplied_base_fields):
                if supplied_base_fields != base_fields or any(values[field] is not None for field in base_fields):
                    raise ValueError(
                        "Grid impedance bases must be cleared together by explicitly supplying both fields as None."
                    )
                values.pop("impedance_base_mva")
                values.pop("impedance_base_voltage_kv")
                values["clear_impedance_base"] = True
            if "neutral_grounding_impedance_ohm" in values and values["neutral_grounding_impedance_ohm"] is None:
                values.pop("neutral_grounding_impedance_ohm")
                values["clear_neutral_grounding_impedance"] = True
        signature = inspect.signature(command_class)
        fields = {
            name for name, parameter in signature.parameters.items()
            if name not in {"self", "command_id", "correlation_id", "causation_id"}
        }
        unknown = set(values) - fields
        if unknown:
            raise ValueError(
                f"{element_type}: engineering intent contains unsupported fields "
                f"{sorted(unknown)!r}."
            )
        payload = {id_field: element_id, **values}
        return command_class(**payload)


__all__ = ["EngineeringUpdatePreparer"]
