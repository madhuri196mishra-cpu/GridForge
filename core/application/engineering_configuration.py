# ============================================================
# File: core/application/engineering_configuration.py
# GridForge V2 — Application engineering update preparation
# Author: Subhendu Mishra
# ============================================================
"""Application-owned translation of typed engineering intent to update commands."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .command import Command
from .creation_schema import creation_definition_for
from core.model.transformer_zero_sequence_contracts import (
    TransformerVectorGroupConfiguration,
    TransformerZeroSequenceTestConfiguration,
)
from core.model.transformer_zero_sequence_test_data import TransformerZeroSequenceTwoWindingTestData
from core.model.transformer_zero_sequence_multiport_evidence import (
    TransformerDeltaLoopState,
    TransformerZeroSequenceMultiportEvidence,
    transformer_delta_loop_fingerprint,
    transformer_multiport_fingerprint,
)
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

_CREATION_TYPE_ALIASES = {
    "buses": "bus",
    "lines": "line",
    "synchronous_machines": "synchronous_machine",
    "ct": "current_transformer",
    "current_transformers": "current_transformer",
    "pt": "potential_transformer",
    "potential_transformers": "potential_transformer",
    "capacitive_voltage_transformer": "cvt",
    "capacitive_voltage_transformers": "cvt",
}


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


def _complex_value(value: Any, field: str) -> complex:
    if isinstance(value, Mapping):
        if set(value) != {"real", "imag"}:
            raise ValueError(f"{field} complex values require exactly real and imag components.")
        value = (value["real"], value["imag"])
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError(f"{field} complex values require exactly two components.")
        real, imag = value
        if isinstance(real, bool) or isinstance(imag, bool):
            raise TypeError(f"{field} complex components must be numeric.")
        return complex(float(real), float(imag))
    if isinstance(value, bool):
        raise TypeError(f"{field} must be a complex-compatible numeric value.")
    try:
        return complex(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"{field} must be a complex-compatible numeric value.") from exc


def _prepare_transformer_values(values: dict[str, Any]) -> dict[str, Any]:
    """Convert editor-neutral records into typed Transformer command values."""
    physical_fields = {
        "hv_neutral_earth_return_impedance_ohm",
        "lv_neutral_earth_return_impedance_ohm",
        "hv_neutral_terminal_role",
        "lv_neutral_terminal_role",
        "hv_phase_roles",
        "lv_phase_roles",
    }
    supplied_physical = physical_fields.intersection(values)
    if supplied_physical:
        if supplied_physical != physical_fields or values.get("replace_physical_conductor_configuration") is not True:
            raise ValueError(
                "Transformer physical conductor replacement requires all six HV/LV "
                "earth-return, neutral-terminal and phase-role fields plus "
                "replace_physical_conductor_configuration=True."
            )
        for field in (
            "hv_neutral_earth_return_impedance_ohm",
            "lv_neutral_earth_return_impedance_ohm",
            "hv_neutral_grounding_impedance_ohm",
            "lv_neutral_grounding_impedance_ohm",
        ):
            if field in values and values[field] is not None:
                values[field] = _complex_value(values[field], field)
        values["hv_phase_roles"] = None if values["hv_phase_roles"] is None else tuple(values["hv_phase_roles"])
        values["lv_phase_roles"] = None if values["lv_phase_roles"] is None else tuple(values["lv_phase_roles"])

    typed_contracts = (
        ("vector_group_configuration", TransformerVectorGroupConfiguration),
        ("zero_sequence_test_configuration", TransformerZeroSequenceTestConfiguration),
        ("zero_sequence_two_winding_test_data", TransformerZeroSequenceTwoWindingTestData),
    )
    for field, contract_type in typed_contracts:
        raw = values.get(field)
        if isinstance(raw, Mapping):
            values[field] = contract_type(**dict(raw))

    multiport_fields = {
        "phase_domain_terminal_admittance_siemens",
        "zero_sequence_multiport_evidence",
        "phase_domain_delta_loop_states",
        "phase_domain_multiport_tap_ratio",
        "phase_domain_multiport_phase_shift_rad",
    }
    supplied_multiport = multiport_fields.intersection(values)
    if supplied_multiport:
        if supplied_multiport != multiport_fields or values.get("replace_phase_domain_multiport_configuration") is not True:
            raise ValueError(
                "Transformer multiport replacement requires all five matrix/evidence/"
                "loop/tap/shift fields plus replace_phase_domain_multiport_configuration=True."
            )

        raw_matrix = values["phase_domain_terminal_admittance_siemens"]
        raw_loops = values["phase_domain_delta_loop_states"]
        if raw_loops is None:
            raw_loops = ()
        if not isinstance(raw_loops, (tuple, list)):
            raise TypeError("phase_domain_delta_loop_states must be a sequence.")
        if raw_matrix is None:
            if values["zero_sequence_multiport_evidence"] is not None or raw_loops not in ((), [], None):
                raise ValueError("Clearing the Transformer multiport must also clear evidence and delta-loop states.")
            values["phase_domain_delta_loop_states"] = ()
            if values["phase_domain_multiport_tap_ratio"] is not None or values["phase_domain_multiport_phase_shift_rad"] is not None:
                raise ValueError("Clearing the Transformer multiport must clear tap and phase-shift snapshots.")
        else:
            if not isinstance(raw_matrix, (tuple, list)):
                raise TypeError("Transformer terminal admittance matrix must be a row sequence.")
            matrix = tuple(
                tuple(_complex_value(cell, "phase_domain_terminal_admittance_siemens") for cell in row)
                for row in raw_matrix
            )
            values["phase_domain_terminal_admittance_siemens"] = matrix

            loop_states = []
            for raw_loop in raw_loops:
                if isinstance(raw_loop, TransformerDeltaLoopState):
                    loop_states.append(raw_loop)
                    continue
                if not isinstance(raw_loop, Mapping):
                    raise TypeError("Every Transformer delta-loop entry must be a typed record or mapping.")
                item = dict(raw_loop)
                item["coupling_coefficients"] = tuple(
                    _complex_value(value, "delta-loop coupling coefficient")
                    for value in item.get("coupling_coefficients", ())
                )
                item["impedance_ohm"] = _complex_value(item.get("impedance_ohm"), "delta-loop impedance_ohm")
                loop_states.append(TransformerDeltaLoopState(**item))
            values["phase_domain_delta_loop_states"] = tuple(loop_states)

            required_inventory = (
                "hv_winding_connection", "lv_winding_connection",
                "hv_terminal_role", "lv_terminal_role",
                "hv_phase_roles", "lv_phase_roles", "hv_neutral_terminal_role",
                "lv_neutral_terminal_role", "core_construction",
            )
            missing = [field for field in required_inventory if field not in values]
            if missing:
                raise ValueError(
                    "Transformer multiport configuration must include its complete ordered "
                    f"winding inventory; missing {', '.join(missing)}."
                )
            if not values["hv_phase_roles"] or not values["lv_phase_roles"]:
                raise ValueError("Transformer multiport configuration requires explicit HV and LV phase-role inventories.")
            labels = [f"HV:{role}" for role in values["hv_phase_roles"]]
            if values["hv_neutral_terminal_role"] is not None:
                labels.append("HV:N")
            labels.extend(f"LV:{role}" for role in values["lv_phase_roles"])
            if values["lv_neutral_terminal_role"] is not None:
                labels.append("LV:N")
            labels = tuple(labels)

            raw_evidence = values["zero_sequence_multiport_evidence"]
            if isinstance(raw_evidence, Mapping):
                evidence_data = dict(raw_evidence)
                supplied_labels = evidence_data.pop("terminal_labels", labels)
                if tuple(supplied_labels) != labels:
                    raise ValueError("Transformer evidence terminal order does not match the submitted winding inventory.")
                supplied_matrix_digest = evidence_data.pop("matrix_sha256", None)
                matrix_digest = transformer_multiport_fingerprint(matrix, labels)
                if supplied_matrix_digest is not None and str(supplied_matrix_digest).strip().lower() != matrix_digest:
                    raise ValueError("Submitted Transformer evidence matrix fingerprint does not match the matrix.")
                supplied_loop_digest = evidence_data.pop("delta_loop_sha256", None)
                evidence_data.setdefault("core_construction", values["core_construction"])
                evidence_data.setdefault("delta_circulating_test_references", {})
                references = evidence_data["delta_circulating_test_references"]
                loop_digest = transformer_delta_loop_fingerprint(values["phase_domain_delta_loop_states"]) if references else None
                if supplied_loop_digest is not None and str(supplied_loop_digest).strip().lower() != loop_digest:
                    raise ValueError("Submitted Transformer evidence delta-loop fingerprint does not match the loop states.")
                evidence_data["matrix_sha256"] = matrix_digest
                evidence_data["terminal_labels"] = labels
                evidence_data["delta_loop_sha256"] = loop_digest
                values["zero_sequence_multiport_evidence"] = TransformerZeroSequenceMultiportEvidence(**evidence_data)
            elif raw_evidence is not None and not isinstance(raw_evidence, TransformerZeroSequenceMultiportEvidence):
                raise TypeError("zero_sequence_multiport_evidence must be a typed evidence record or mapping.")
            if values["zero_sequence_multiport_evidence"] is None:
                raise ValueError("A supplied Transformer phase-domain matrix requires manufacturer/test evidence.")

    return values


class EngineeringUpdatePreparer:
    """Prepare one immutable update command from typed engineering intent."""

    @classmethod
    def prepare_batch(cls, intents: Any) -> tuple[Command, ...]:
        """Prepare homogeneous batch children with target-specific canonical validation."""
        values = tuple(intents)
        if len(values) < 2:
            raise ValueError("Engineering update batch requires at least two typed intents.")
        commands: list[Command] = []
        for intent in values:
            element_type = str(getattr(intent, "element_type", "")).strip().lower()
            element_id = str(getattr(intent, "element_id", "")).strip()
            try:
                commands.append(cls.prepare(intent))
            except Exception as exc:
                raise ValueError(
                    f"Engineering batch validation failed for {element_type} {element_id}: {exc}"
                ) from exc
        return tuple(commands)

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
        creation_type = _CREATION_TYPE_ALIASES.get(element_type, element_type)
        creation_definition = creation_definition_for(creation_type)
        parameter_updates: dict[str, Any] = {}
        for parameter in creation_definition.parameter_definitions:
            command_field = creation_definition.parameter_mapping.get(
                parameter.parameter_id,
                parameter.parameter_id,
            )
            if command_field in values:
                parameter_updates[parameter.parameter_id] = values[command_field]
            elif parameter.parameter_id in values:
                parameter_updates[parameter.parameter_id] = values[parameter.parameter_id]
        validation_errors = creation_definition.validate_updates(parameter_updates)
        if validation_errors:
            raise ValueError(
                f"{element_type}: engineering update violates the canonical parameter contract: "
                + "; ".join(validation_errors)
            )
        if element_type in {"transformer", "transformers"}:
            values = _prepare_transformer_values(values)
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
