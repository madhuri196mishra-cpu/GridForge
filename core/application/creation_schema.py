# ============================================================
# File: core/application/creation_schema.py
# GridForge V2 — Framework-neutral creation parameter schema
# Author: Subhendu Mishra
# ============================================================
"""Framework-neutral immutable schema primitives for equipment creation.

This module has no UI, Qt, plugin lifecycle, or Core-model dependencies.
The Application and UI adapters may share these declarative parameter
contracts; authority over committed electrical state remains in Core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping

@dataclass(frozen=True, slots=True)
class EngineeringParameterDefinition:
    """Canonical engineering parameter contract for equipment creation."""
    parameter_id: str
    display_name: str
    datatype: str = "str"
    unit: str | None = None
    required_before_create: bool = False
    default_value: Any = None
    editable: bool = True
    derived: bool = False
    choices: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    validation: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_id, str) or not self.parameter_id.strip():
            raise ValueError("parameter_id must be non-empty")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name must be non-empty")
        if not isinstance(self.datatype, str) or not self.datatype.strip():
            raise ValueError("datatype must be non-empty")
        if self.derived and self.editable:
            raise ValueError("derived parameters cannot be editable")
        object.__setattr__(self, "parameter_id", self.parameter_id.strip())
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(self, "datatype", self.datatype.strip())
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "validation", MappingProxyType(dict(self.validation)))


@dataclass(frozen=True, slots=True)
class CreationTerminalRequirement:
    """Declarative acquisition requirement for one canonical Core terminal."""

    terminal_name: str
    required: bool = False
    cardinality: str = "single"
    allowed_connection_types: tuple[str, ...] = ()
    acquisition_state: str = "required"
    initial_endpoint_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_name, str) or not self.terminal_name.strip():
            raise ValueError("terminal_name must be non-empty.")
        if self.cardinality not in {"single", "pair", "multiple"}:
            raise ValueError("cardinality must be single, pair, or multiple.")
        if self.acquisition_state not in {"required", "optional", "acquired", "pending"}:
            raise ValueError("acquisition_state must be required, optional, acquired, or pending.")
        object.__setattr__(self, "terminal_name", self.terminal_name.strip())
        object.__setattr__(self, "allowed_connection_types", tuple(self.allowed_connection_types))


@dataclass(frozen=True, slots=True)
class CreationTopologyRequirement:
    """Declarative endpoint/topology acquisition requirement."""

    name: str
    required: bool = False
    cardinality: str = "single"
    allowed_connection_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("topology requirement name must be non-empty.")
        if self.cardinality not in {"single", "pair", "multiple"}:
            raise ValueError("cardinality must be single, pair, or multiple.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "allowed_connection_types", tuple(self.allowed_connection_types))


@dataclass(frozen=True, slots=True)
class ConditionalParameterRequirement:
    """Declarative conditional completeness rule."""

    label: str
    require_any: tuple[str, ...] = ()
    require_all: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.require_any and not self.require_all:
            raise ValueError("A conditional requirement must name parameters.")
        object.__setattr__(self, "require_any", tuple(self.require_any))
        object.__setattr__(self, "require_all", tuple(self.require_all))


@dataclass(frozen=True, slots=True)
class CreationDefinition:
    """Immutable canonical creation contract for one equipment type."""

    equipment_type: str
    tool_id: str
    parameter_definitions: tuple[EngineeringParameterDefinition, ...]
    command_type: str
    id_field: str
    parameter_mapping: Mapping[str, str] = field(default_factory=dict)
    endpoint_mapping: Mapping[str, str] = field(default_factory=dict)
    topology_requirements: tuple[CreationTopologyRequirement, ...] = ()
    terminal_requirements: tuple[CreationTerminalRequirement, ...] = ()
    conditional_requirements: tuple[ConditionalParameterRequirement, ...] = ()
    placement_required: bool = True
    preview_supported: bool = True
    configuration_required: bool = False
    command_field_dispositions: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.equipment_type.strip() or not self.tool_id.strip():
            raise ValueError("CreationDefinition identities must be non-empty.")
        if not isinstance(self.command_type, str) or not self.command_type.strip():
            raise TypeError("command_type must be a non-empty Application command type.")
        if not self.id_field.strip():
            raise ValueError("id_field must be non-empty.")
        parameters = tuple(self.parameter_definitions)
        ids = [item.parameter_id for item in parameters]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate creation parameter in {self.equipment_type!r}.")
        mapping = dict(self.parameter_mapping)
        for parameter_id in ids:
            mapping.setdefault(parameter_id, parameter_id)
        unknown = set(mapping) - set(ids)
        if unknown:
            raise ValueError(f"Command mapping references unknown parameters: {sorted(unknown)!r}")
        object.__setattr__(self, "equipment_type", self.equipment_type.strip())
        object.__setattr__(self, "tool_id", self.tool_id.strip())
        object.__setattr__(self, "id_field", self.id_field.strip())
        object.__setattr__(self, "command_type", self.command_type.strip())
        object.__setattr__(self, "parameter_definitions", parameters)
        object.__setattr__(self, "parameter_mapping", MappingProxyType(mapping))
        object.__setattr__(self, "endpoint_mapping", MappingProxyType(dict(self.endpoint_mapping)))
        object.__setattr__(self, "topology_requirements", tuple(self.topology_requirements))
        object.__setattr__(self, "terminal_requirements", tuple(self.terminal_requirements))
        dispositions: dict[str, Mapping[str, str]] = {}
        if not isinstance(self.command_field_dispositions, Mapping):
            raise TypeError("command_field_dispositions must be a mapping.")
        for field_name, disposition in self.command_field_dispositions.items():
            if not isinstance(field_name, str) or not field_name.strip() or not isinstance(disposition, Mapping):
                raise TypeError("Command-field dispositions require non-empty field names and mapping values.")
            dispositions[field_name.strip()] = MappingProxyType({
                str(key): str(value) for key, value in disposition.items()
            })
        object.__setattr__(self, "command_field_dispositions", MappingProxyType(dispositions))
        object.__setattr__(self, "conditional_requirements", tuple(self.conditional_requirements))

    @property
    def required_parameters(self) -> tuple[EngineeringParameterDefinition, ...]:
        return tuple(p for p in self.parameter_definitions if p.required_before_create)

    @property
    def optional_parameters(self) -> tuple[EngineeringParameterDefinition, ...]:
        return tuple(p for p in self.parameter_definitions if not p.required_before_create)

    @property
    def default_values(self) -> Mapping[str, Any]:
        return {p.parameter_id: p.default_value for p in self.parameter_definitions if p.default_value is not None}

    def parameter(self, parameter_id: str) -> EngineeringParameterDefinition:
        for parameter in self.parameter_definitions:
            if parameter.parameter_id == parameter_id:
                return parameter
        raise KeyError(parameter_id)

    def validate_values(self, values: Mapping[str, Any]) -> tuple[str, ...]:
        errors: list[str] = []
        for parameter in self.parameter_definitions:
            value = values.get(parameter.parameter_id)
            if parameter.required_before_create and value is None:
                errors.append(f"{parameter.display_name} is required.")
                continue
            if value is None:
                continue
            try:
                _validate_parameter(parameter, value)
            except (TypeError, ValueError) as exc:
                errors.append(f"{parameter.display_name}: {exc}")
        for conditional in self.conditional_requirements:
            if conditional.require_any and not any(values.get(key) is not None for key in conditional.require_any):
                errors.append(conditional.label)
            if conditional.require_all and not all(values.get(key) is not None for key in conditional.require_all):
                errors.append(conditional.label)
        return tuple(errors)



def _validate_parameter(definition: EngineeringParameterDefinition, value: Any) -> None:
    unit = None
    if isinstance(value, Mapping):
        unit = value.get("unit")
        if "value" not in value:
            raise ValueError("value mapping must contain 'value'")
        value = value["value"]
    if unit is not None and definition.unit is None:
        raise ValueError("unit metadata is not defined by the canonical contract")
    if unit is not None and definition.unit is not None and str(unit).strip() != str(definition.unit).strip():
        raise ValueError(f"unit must be {definition.unit!r}")
    datatype = definition.datatype.strip().lower()
    canonical = value
    if datatype in {"float", "number"}:
        if isinstance(value, bool):
            raise TypeError("must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("must be finite")
        if definition.minimum is not None and numeric < definition.minimum:
            raise ValueError(f"must be >= {definition.minimum}")
        if definition.maximum is not None and numeric > definition.maximum:
            raise ValueError(f"must be <= {definition.maximum}")
        canonical = numeric
    elif datatype in {"int", "integer"}:
        if isinstance(value, bool):
            raise TypeError("must be an integer")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("must be finite")
        if not numeric_value.is_integer():
            raise TypeError("must be an integer")
        numeric = int(numeric_value)
        if definition.minimum is not None and numeric < definition.minimum:
            raise ValueError(f"must be >= {definition.minimum}")
        if definition.maximum is not None and numeric > definition.maximum:
            raise ValueError(f"must be <= {definition.maximum}")
        canonical = numeric
    elif datatype in {"bool", "boolean"}:
        if not isinstance(value, bool):
            raise TypeError("must be boolean")
        canonical = value
    elif datatype == "enum":
        if str(value) not in tuple(str(choice) for choice in definition.choices):
            raise ValueError(f"must be one of {tuple(definition.choices)!r}")
        canonical = value
    elif datatype in {"str", "string"}:
        if not isinstance(value, str):
            raise TypeError("must be text")
        canonical = value
    else:
        raise ValueError(f"unsupported datatype {datatype!r}")
    rules = dict(definition.validation)
    if rules.get("non_empty") and (canonical is None or (isinstance(canonical, str) and not canonical.strip())):
        raise ValueError("must not be empty")
    if rules.get("nonzero") and canonical == 0:
        raise ValueError("must be non-zero")
    if "allowed_values" in rules and canonical not in tuple(rules["allowed_values"]):
        raise ValueError(f"must be one of {tuple(rules['allowed_values'])!r}")



def _p(parameter_id: str, display_name: str | None = None, datatype: str = "float",
       unit: str | None = None, *, required: bool = False, default: Any = None,
       editable: bool = True, derived: bool = False, choices: tuple[Any, ...] = (),
       minimum: float | None = None, maximum: float | None = None,
       validation: Mapping[str, Any] | None = None) -> EngineeringParameterDefinition:
    return EngineeringParameterDefinition(
        parameter_id=parameter_id,
        display_name=display_name or parameter_id.replace("_", " ").title(),
        datatype=datatype,
        unit=unit,
        required_before_create=required,
        default_value=default,
        editable=editable,
        derived=derived,
        choices=choices,
        minimum=minimum,
        maximum=maximum,
        validation={} if validation is None else dict(validation),
    )


def _terminals(
    names: tuple[str, ...],
    *,
    required: bool = True,
    initial_endpoint_required: bool = False,
    measurement_terminal_names: tuple[str, ...] = (),
) -> tuple[CreationTerminalRequirement, ...]:
    """Describe physical terminals without treating measurement interfaces as wires."""
    measurement_terminals = set(measurement_terminal_names)
    unknown_measurement_terminals = measurement_terminals - set(names)
    if unknown_measurement_terminals:
        raise ValueError(
            f"Measurement terminals are absent from the terminal inventory: "
            f"{sorted(unknown_measurement_terminals)!r}"
        )
    return tuple(
        CreationTerminalRequirement(
            name,
            required=False if name in measurement_terminals else required,
            cardinality="single",
            allowed_connection_types=() if name in measurement_terminals else ("electrical",),
            acquisition_state="optional" if name in measurement_terminals else "required",
            initial_endpoint_required=False if name in measurement_terminals else initial_endpoint_required,
        )
        for name in names
    )


def _definition(
    equipment_type: str,
    tool_id: str,
    parameters: tuple[EngineeringParameterDefinition, ...],
    command_type: str,
    id_field: str,
    *,
    topology: tuple[CreationTopologyRequirement, ...] = (),
    endpoint_mapping: Mapping[str, str] | None = None,
    parameter_mapping: Mapping[str, str] = {},
    terminal_names: tuple[str, ...] = (),
    conditional: tuple[ConditionalParameterRequirement, ...] = (),
    initial_endpoint_required: bool = False,
    measurement_terminal_names: tuple[str, ...] = (),
) -> CreationDefinition:
    return CreationDefinition(
        equipment_type=equipment_type,
        tool_id=tool_id,
        parameter_definitions=parameters,
        command_type=command_type,
        id_field=id_field,
        parameter_mapping=parameter_mapping,
        endpoint_mapping=endpoint_mapping or {},
        # Endpoint-field mappings describe how connected endpoints are passed
        # to a Core create command; they do not make every mapped endpoint
        # mandatory. Required topology is declared explicitly per equipment type.
        topology_requirements=topology,
        terminal_requirements=_terminals(
            terminal_names,
            initial_endpoint_required=initial_endpoint_required,
            measurement_terminal_names=measurement_terminal_names,
        ),
        conditional_requirements=conditional,
        configuration_required=any(p.required_before_create for p in parameters),
        command_field_dispositions=_BUILTIN_COMMAND_FIELD_DISPOSITIONS.get(equipment_type, {}),
    )


_BUILTIN_TERMINAL_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "bus": ("bus",),
    "grid": ("terminal",),
    "generator": ("terminal",),
    "load": ("terminal",),
    "shunt": ("terminal",),
    "capacitor": ("terminal",),
    "reactor": ("terminal",),
    "solar": ("terminal",),
    "battery": ("terminal",),
    "motor": ("terminal",),
    "synchronous_machine": ("terminal",),
    "line": ("FROM", "TO"),
    "cable": ("FROM", "TO"),
    "transformer": ("FROM", "TO"),
    "switch": ("from", "to"),
    "breaker": ("from", "to"),
    "disconnector": ("from", "to"),
    "fuse": ("from", "to"),
    "current_transformer": ("P1", "P2", "S1", "S2"),
    "potential_transformer": ("primary_a", "primary_b", "secondary_a", "secondary_b"),
    "cvt": ("H1", "H2", "X1", "X2"),
    "relay": (),
})


def _field_disposition(kind: str, reason: str, workflow: str | None = None) -> Mapping[str, str]:
    value = {"kind": kind, "reason": reason}
    if workflow:
        value["workflow"] = workflow
    return MappingProxyType(value)


# Every create-command constructor field not supplied by a canonical parameter,
# endpoint, identity, or generated presentation coordinate has an explicit
# lifecycle disposition. Adding a new optional command field therefore requires
# a corresponding engineering decision before the built-in contract is accepted.
_BUILTIN_COMMAND_FIELD_DISPOSITIONS: Mapping[str, Mapping[str, Mapping[str, str]]] = MappingProxyType({
    "bus": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "voltage_pu": _field_disposition("operating_point", "Initial bus voltage is operating state, not nameplate data.", "Bus operating-point editor / UpdateBusCommand"),
        "angle_deg": _field_disposition("operating_point", "Initial bus angle is operating state, not nameplate data.", "Bus operating-point editor / UpdateBusCommand"),
    },
    "grid": {
        "name": _field_disposition("metadata", "Human-facing source label; not electrical configuration."),
        "voltage_pu": _field_disposition("operating_point", "Initial source voltage is operating state, not nameplate data.", "Grid operating-point editor / UpdateGridCommand"),
        "angle_deg": _field_disposition("operating_point", "Initial source angle is operating state, not nameplate data.", "Grid operating-point editor / UpdateGridCommand"),
        "p_mw": _field_disposition("operating_point", "Initial source active-power dispatch is operating state, not nameplate data.", "Grid operating-point editor / UpdateGridCommand"),
        "q_mvar": _field_disposition("operating_point", "Initial source reactive-power dispatch is operating state, not nameplate data.", "Grid operating-point editor / UpdateGridCommand"),
        "z1_pu": _field_disposition("later_configuration", "Complex sequence impedance is not represented by the scalar creation schema.", "Grid sequence-impedance configuration / UpdateGridCommand"),
        "z2_pu": _field_disposition("later_configuration", "Complex sequence impedance is not represented by the scalar creation schema.", "Grid sequence-impedance configuration / UpdateGridCommand"),
        "z0_pu": _field_disposition("later_configuration", "Complex sequence impedance is not represented by the scalar creation schema.", "Grid sequence-impedance configuration / UpdateGridCommand"),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Put/Take Grid In Service commands"),
    },
    "generator": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "q_limits": _field_disposition("later_configuration", "Reactive capability is a coupled lower/upper pair, not a scalar creation parameter.", "Generator capability configuration / UpdateGeneratorCommand"),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Generator service lifecycle commands"),
    },
    "load": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Load service lifecycle / UpdateLoadCommand"),
    },
    "shunt": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Shunt service lifecycle / UpdateShuntCommand"),
    },
    "line": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "conductor_system": _field_disposition("later_configuration", "Typed conductor construction is not a scalar creation parameter.", "Line conductor configuration / UpdateLineCommand"),
    },
    "transformer": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "hv_winding_connection": _field_disposition("later_configuration", "Winding connection belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_winding_connection": _field_disposition("later_configuration", "Winding connection belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "hv_neutral_grounding_impedance_ohm": _field_disposition("later_configuration", "Neutral grounding evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_neutral_grounding_impedance_ohm": _field_disposition("later_configuration", "Neutral grounding evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "hv_neutral_earth_return_impedance_ohm": _field_disposition("later_configuration", "Physical neutral earth-return evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_neutral_earth_return_impedance_ohm": _field_disposition("later_configuration", "Physical neutral earth-return evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "hv_neutral_terminal_role": _field_disposition("later_configuration", "Physical neutral terminal mapping belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_neutral_terminal_role": _field_disposition("later_configuration", "Physical neutral terminal mapping belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "hv_phase_roles": _field_disposition("later_configuration", "Explicit phase ordering belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_phase_roles": _field_disposition("later_configuration", "Explicit phase ordering belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "phase_domain_terminal_admittance_siemens": _field_disposition("later_configuration", "The complete phase-domain terminal matrix is manufacturer/test-derived typed evidence, not a scalar creation field.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_multiport_evidence": _field_disposition("later_configuration", "Matrix-bound zero-sequence evidence and provenance require the dedicated typed configurator.", "Transformer multiport/evidence configuration (#939)"),
        "phase_domain_delta_loop_states": _field_disposition("later_configuration", "Internal delta circulating-loop state is a structured phase-domain contract.", "Transformer multiport/evidence configuration (#939)"),
        "phase_domain_multiport_tap_ratio": _field_disposition("later_configuration", "Multiport tap snapshot must be committed with the matching matrix and phase-shift snapshot.", "Transformer multiport/evidence configuration (#939)"),
        "phase_domain_multiport_phase_shift_rad": _field_disposition("later_configuration", "Multiport phase-shift snapshot must be committed with the matching matrix and tap snapshot.", "Transformer multiport/evidence configuration (#939)"),
        "hv_terminal_role": _field_disposition("later_configuration", "Winding terminal roles belong to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "lv_terminal_role": _field_disposition("later_configuration", "Winding terminal roles belong to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "impedance_reference_winding": _field_disposition("later_configuration", "Impedance reference winding belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_resistance_ohm": _field_disposition("later_configuration", "Zero-sequence evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_reactance_ohm": _field_disposition("later_configuration", "Zero-sequence evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "core_construction": _field_disposition("later_configuration", "Core construction belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_test_basis": _field_disposition("later_configuration", "Test basis belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "vector_group_configuration": _field_disposition("later_configuration", "Vector-group evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_test_configuration": _field_disposition("later_configuration", "Zero-sequence test evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
        "zero_sequence_two_winding_test_data": _field_disposition("later_configuration", "Two-winding test evidence belongs to the dedicated transformer configurator.", "Transformer multiport/evidence configuration (#939)"),
    },
    "cable": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Cable service lifecycle / UpdateCableCommand"),
        "conductor_system": _field_disposition("later_configuration", "Typed conductor construction is not a scalar creation parameter.", "Cable conductor configuration / UpdateCableCommand"),
    },
    "switch": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "closed": _field_disposition("operational_state", "Initial open/closed state belongs to the switching-device lifecycle.", "Switch state commands and switching configurator (#938)"),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to the switching-device lifecycle.", "Switch service commands (#938)"),
        "normally_closed": _field_disposition("later_configuration", "Normal operating state belongs to the switching-device construction/configuration contract.", "Switching-device configurator (#938)"),
    },
    "breaker": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to the switching-device lifecycle.", "Breaker service commands (#938)"),
        "closed": _field_disposition("operational_state", "Initial contact state belongs to the switching-device lifecycle.", "Breaker state commands and switching configurator (#938)"),
        "failed": _field_disposition("operational_state", "Failure state is runtime/operational state, not nameplate configuration.", "Breaker operational-state commands (#938)"),
    },
    "disconnector": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "operating_time": _field_disposition("later_configuration", "Operating-time characteristic is optional physical switching-device data; it must remain unavailable until supplied.", "Switching-device configurator (#938)"),
        "normally_closed": _field_disposition("later_configuration", "Configured normal operating state is distinct from the present contact state.", "Switching-device configurator (#938)"),
        "closed": _field_disposition("operational_state", "Initial contact state belongs to the switching-device lifecycle.", "Disconnector state commands (#938)"),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to the switching-device lifecycle.", "Disconnector service commands (#938)"),
    },
    "fuse": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to the fuse lifecycle.", "Fuse service commands"),
        "blown": _field_disposition("operational_state", "Blown state is operational state, not fuse nameplate data.", "Blow/Reset Fuse commands"),
    },
    "capacitor": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Capacitor service lifecycle commands"),
    },
    "reactor": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Reactor service lifecycle commands"),
    },
    "motor": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "running": _field_disposition("operational_state", "Running state is dynamic operating state, not nameplate configuration.", "Motor operating-state workflow"),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Motor service lifecycle commands"),
    },
    "synchronous_machine": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Synchronous-machine service lifecycle"),
    },
    "solar": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Solar service lifecycle"),
    },
    "battery": {
        "name": _field_disposition("metadata", "Human-facing asset label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state is governed by explicit lifecycle operations.", "Battery service lifecycle"),
    },
    "current_transformer": {
        "name": _field_disposition("metadata", "Human-facing instrument label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to instrument lifecycle.", "Current-transformer service lifecycle"),
        "s1_endpoint": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
        "s2_endpoint": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
    },
    "potential_transformer": {
        "name": _field_disposition("metadata", "Human-facing instrument label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to instrument lifecycle.", "Potential-transformer service lifecycle"),
        "secondary_a": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
        "secondary_b": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
    },
    "cvt": {
        "name": _field_disposition("metadata", "Human-facing instrument label; not electrical configuration."),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to instrument lifecycle.", "CVT service lifecycle"),
        "x1_endpoint": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
        "x2_endpoint": _field_disposition("measurement_association", "Secondary terminals are measurement interfaces, not SimpleWire topology.", "MeasurementChannelService association workflow"),
    },
    "relay": {
        "name": _field_disposition("metadata", "Human-facing relay label; not protection configuration."),
        "plugin_id": _field_disposition("later_configuration", "Plugin binding is a protection-runtime configuration decision.", "Protection relay plugin configuration workflow"),
        "settings": _field_disposition("later_configuration", "Typed relay settings must be validated against the selected plugin contract.", "Protection relay settings workflow"),
        "in_service": _field_disposition("lifecycle_state", "Service state belongs to relay lifecycle.", "Relay service lifecycle"),
        "enabled": _field_disposition("operational_state", "Enable state is protection operational state.", "Protection relay operational-state workflow"),
        "blocked": _field_disposition("operational_state", "Block state is protection operational state.", "Protection relay operational-state workflow"),
    },
})


def creation_definition_for(
    equipment_type: str,
    terminal_names: tuple[str, ...] | None = None,
) -> CreationDefinition:
    """Return the trusted built-in creation contract for one equipment type.

    The optional terminal_names argument preserves the legacy UI adapter API.
    Application callers can use the neutral built-in inventory directly.
    """
    equipment_type = str(equipment_type).strip().lower()
    try:
        canonical_terminal_names = _BUILTIN_TERMINAL_NAMES[equipment_type]
    except KeyError as exc:
        raise KeyError(f"No canonical creation definition for {equipment_type!r}") from exc
    if terminal_names is None:
        terminal_names = canonical_terminal_names
    else:
        terminal_names = tuple(terminal_names)
        if terminal_names != canonical_terminal_names:
            raise ValueError(
                f"{equipment_type}: supplied terminal inventory {terminal_names!r} "
                f"does not match canonical inventory {canonical_terminal_names!r}."
            )
    common_endpoints = {"FROM": "endpoint_from", "TO": "endpoint_to"}
    switching_endpoints = {"from": "endpoint_from", "to": "endpoint_to"}
    # Required connection terminals are explicit per equipment family. They
    # are checked only at network commit, never during placement or editing.
    single_terminal_topology = (CreationTopologyRequirement("terminal", required=True),)
    branch_topology = (
        CreationTopologyRequirement("FROM", required=True),
        CreationTopologyRequirement("TO", required=True),
    )
    switching_topology = (
        CreationTopologyRequirement("from", required=True),
        CreationTopologyRequirement("to", required=True),
    )

    definitions: dict[str, CreationDefinition] = {
        # Core Bus owns one authoritative Terminal(role="bus"). Draft bus
        # attachment references remain a separate connectivity form, but the
        # equipment/presentation/creation terminal inventory must still agree.
        "bus": _definition("bus", "bus", (
            _p("nominal_voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("frequency_hz", unit="Hz", required=True, minimum=0.0),
            _p("in_service", "In service", "bool", required=True, default=True),
        ), "model.create_bus", "bus_id", terminal_names=_BUILTIN_TERMINAL_NAMES["bus"]),
        "grid": _definition("grid", "grid", (
            _p("nominal_voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("frequency_hz", unit="Hz", required=True, minimum=0.0),
            _p("short_circuit_mva", "Short-circuit strength", unit="MVA", minimum=0.0),
            _p("x_over_r", "X/R ratio", minimum=0.0),
            _p("grounded", "Grounded", "bool", default=True),
        ), "model.create_grid", "grid_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["grid"]),
        "generator": _definition("generator", "generator", (
            _p("p", "Active power", unit="MW", required=True),
            _p("q", "Reactive power", unit="MVAr", required=True),
            _p("V_setpoint", "Voltage setpoint", unit="pu", required=True),
        ), "model.create_generator", "generator_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["generator"]),
        "load": _definition("load", "load", (
            _p("p", "Active power", unit="MW", required=True),
            _p("q", "Reactive power", unit="MVAr", required=True),
        ), "model.create_load", "load_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["load"]),
        "shunt": _definition("shunt", "shunt", (
            _p("g_pu", "Conductance", required=True),
            _p("b_pu", "Susceptance", required=True),
        ), "model.create_shunt", "shunt_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["shunt"]),
        "capacitor": _definition("capacitor", "capacitor", (
            _p("reactive_power_injection_mvar", "Reactive power", unit="MVAr", required=True),
        ), "model.create_capacitor", "capacitor_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["capacitor"]),
        "reactor": _definition("reactor", "reactor", (
            _p("reactive_power_injection_mvar", "Reactive power", unit="MVAr", required=True),
        ), "model.create_reactor", "reactor_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["reactor"]),
        "solar": _definition("solar", "solar", (
            _p("p_mw", "Active power", unit="MW", required=True),
            _p("q_mvar", "Reactive power", unit="MVAr", required=True),
            _p("p_max_mw", unit="MW", minimum=0.0),
            _p("p_min_mw", unit="MW", default=0.0, minimum=0.0),
            _p("q_max_mvar", unit="MVAr"),
            _p("q_min_mvar", unit="MVAr"),
        ), "model.create_solar", "solar_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["solar"]),
        "battery": _definition("battery", "battery", (
            _p("p_mw", "Active power", unit="MW", required=True),
            _p("q_mvar", "Reactive power", unit="MVAr", required=True),
            _p("energy_capacity_mwh", unit="MWh", required=True, minimum=0.0),
            _p("max_charge_mw", unit="MW", minimum=0.0),
            _p("max_discharge_mw", unit="MW", minimum=0.0),
            _p("soc", "State of charge", unit="pu", default=1.0, minimum=0.0, maximum=1.0),
            _p("soc_min", "Minimum state of charge", unit="pu", default=0.0, minimum=0.0, maximum=1.0),
            _p("soc_max", "Maximum state of charge", unit="pu", default=1.0, minimum=0.0, maximum=1.0),
        ), "model.create_battery", "battery_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["battery"]),
        "motor": _definition("motor", "motor", (
            _p("rated_mva", unit="MVA", required=True, minimum=0.0),
            _p("rated_kv", unit="kV", required=True, minimum=0.0),
            _p("power_factor", minimum=0.0, maximum=1.0, default=0.9),
            _p("p", "Active power", unit="MW", required=True),
            _p("q", "Reactive power", unit="MVAr", required=True),
            _p("efficiency", minimum=0.0, maximum=1.0, default=1.0),
            _p("slip", default=0.0),
            _p("starting_current_pu", default=0.0),
        ), "model.create_motor", "motor_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["motor"]),
        "synchronous_machine": _definition("synchronous_machine", "synchronous_machine", (
            _p("active_power_injection_mw", "Active power", unit="MW", required=True),
            _p("reactive_power_injection_mvar", "Reactive power", unit="MVAr", required=True),
            _p("rated_power_mva", unit="MVA", minimum=0.0),
            _p("rated_voltage_kv", unit="kV", minimum=0.0),
            _p("frequency_hz", unit="Hz", required=True, default=50.0, minimum=0.0),
        ), "model.create_synchronous_machine", "synchronous_machine_id", topology=single_terminal_topology, endpoint_mapping={"terminal": "endpoint"}, terminal_names=_BUILTIN_TERMINAL_NAMES["synchronous_machine"]),
        "line": _definition("line", "line", (
            _p("resistance_ohm", unit="ohm", required=True),
            _p("reactance_ohm", unit="ohm", required=True),
            _p("rate_mva", unit="MVA", required=True, minimum=0.0),
            _p("shunt_susceptance_siemens", unit="S", default=0.0),
            _p("zero_sequence_resistance_ohm", unit="ohm"),
            _p("zero_sequence_reactance_ohm", unit="ohm"),
            _p("name", "Name", "str", default=""),
        ), "model.create_line", "line_id", topology=branch_topology, endpoint_mapping=common_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["line"]),
        "cable": _definition("cable", "cable", (
            _p("length_km", unit="km", required=True, minimum=0.0),
            _p("r1_ohm_per_km", unit="ohm/km", required=True),
            _p("x1_ohm_per_km", unit="ohm/km", required=True),
            _p("b1_us_per_km", unit="uS/km", default=0.0),
            _p("r0_ohm_per_km", unit="ohm/km", minimum=0.0),
            _p("x0_ohm_per_km", unit="ohm/km", minimum=0.0),
            _p("b0_us_per_km", unit="uS/km", minimum=0.0),
            _p("rated_voltage_kv", unit="kV", minimum=0.0),
            _p("rated_current_a", unit="A", minimum=0.0),
            _p("thermal_limit_mva", unit="MVA", minimum=0.0),
            _p("conductor_count", "Conductor count", "int", default=1, minimum=1.0),
            _p("name", "Name", "str", default=""),
        ), "model.create_cable", "cable_id", topology=branch_topology, endpoint_mapping=common_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["cable"]),
        "transformer": _definition("transformer", "transformer", (
            _p("r", "Resistance", required=True),
            _p("x", "Reactance", required=True),
            _p("b", "Susceptance", default=0.0),
            _p("impedance_basis", "Impedance basis", "enum", required=True, choices=("pu", "engineering")),
            _p("impedance_base_voltage_kv", unit="kV", required=True, minimum=0.0, validation={"nonzero": True}),
            _p("impedance_base_mva", unit="MVA", minimum=0.0, validation={"nonzero": True}),
            _p("rate_mva", unit="MVA", minimum=0.0, validation={"nonzero": True}),
            _p("tap", "Tap", default=1.0),
            _p("shift", "Phase shift", unit="deg", default=0.0),
            _p("name", "Name", "str", default=""),
        ), "model.create_transformer", "transformer_id", topology=branch_topology, endpoint_mapping=common_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["transformer"],
           conditional=(ConditionalParameterRequirement(
               "Transformer requires impedance_base_mva or rate_mva.",
               require_any=("impedance_base_mva", "rate_mva"),
           ),)),
        "switch": _definition("switch", "switch", (
            _p("rated_voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("rated_current_a", unit="A", required=True, minimum=0.0),
        ), "model.create_switch", "switch_id", topology=switching_topology, endpoint_mapping={"from": "endpoint_a", "to": "endpoint_b"}, terminal_names=_BUILTIN_TERMINAL_NAMES["switch"]),
        "breaker": _definition("breaker", "breaker", (
            _p("voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("current_a", unit="A", required=True, minimum=0.0),
            _p("interrupting_ka", unit="kA", required=True, minimum=0.0),
            _p("closed", "Closed", "bool", default=True),
            _p("in_service", "In service", "bool", default=True),
        ), "model.create_breaker", "breaker_id", topology=switching_topology, endpoint_mapping=switching_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["breaker"]),
        "disconnector": _definition("disconnector", "disconnector", (
            _p("voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("rated_current_a", unit="A", required=True, minimum=0.0),
        ), "model.create_disconnector", "disconnector_id", topology=switching_topology, endpoint_mapping=switching_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["disconnector"]),
        "fuse": _definition("fuse", "fuse", (
            _p("rated_current_a", unit="A", required=True, minimum=0.0),
            _p("rated_voltage_v", unit="V", required=True, minimum=0.0),
            _p("interrupting_rating_ka", unit="kA", required=True, minimum=0.0),
        ), "model.create_fuse", "fuse_id", topology=switching_topology, endpoint_mapping=switching_endpoints, terminal_names=_BUILTIN_TERMINAL_NAMES["fuse"]),
        "current_transformer": _definition("current_transformer", "current_transformer", (
            _p("primary_rated_current_a", unit="A", required=True, minimum=0.0),
            _p("secondary_rated_current_a", unit="A", required=True, minimum=0.0),
            _p("burden_va", unit="VA", minimum=0.0),
            _p("accuracy_class", "Accuracy class", "str"),
            _p("frequency_hz", unit="Hz", required=True, minimum=0.0),
            _p("polarity", "Polarity", "enum", required=True, choices=("P1_P2", "P2_P1")),
        ), "model.create_current_transformer", "transformer_id", topology=(CreationTopologyRequirement("P1", required=True), CreationTopologyRequirement("P2", required=True)),
           # Primary terminals participate in the electrical DraftConnection
           # graph. Secondary terminals are measurement/protection interfaces
           # and are associated through measurement channels, not required to
           # be simple-wire endpoints during equipment creation.
           endpoint_mapping={"P1": "p1_endpoint", "P2": "p2_endpoint"},
            terminal_names=_BUILTIN_TERMINAL_NAMES["current_transformer"],
            measurement_terminal_names=("S1", "S2")),
        "potential_transformer": _definition("potential_transformer", "potential_transformer", (
            _p("primary_voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("secondary_voltage_v", unit="V", required=True, minimum=0.0),
            _p("accuracy_class", "Accuracy class", "str", default="0.5"),
            _p("burden_va", unit="VA", default=100.0, minimum=0.0),
            _p("phase_displacement_deg", unit="deg", default=0.0),
        ), "model.create_potential_transformer", "pt_id", topology=(CreationTopologyRequirement("primary_a", required=True), CreationTopologyRequirement("primary_b", required=True)),
           # Primary terminals belong to electrical topology. Secondary
           # terminals feed measurement channels and are not required to be
           # DraftConnection endpoints at equipment-creation commit.
           endpoint_mapping={"primary_a": "primary_a", "primary_b": "primary_b"},
            terminal_names=_BUILTIN_TERMINAL_NAMES["potential_transformer"],
            measurement_terminal_names=("secondary_a", "secondary_b")),
        "cvt": _definition("cvt", "cvt", (
            _p("rated_primary_voltage_kv", unit="kV", required=True, minimum=0.0),
            _p("rated_secondary_voltage_v", unit="V", required=True, minimum=0.0),
            _p("accuracy_class", "Accuracy class", "str", default="0.5"),
            _p("rated_burden_va", unit="VA", default=100.0, minimum=0.0),
            _p("frequency_hz", unit="Hz", required=True, minimum=0.0),
            _p("polarity", "Polarity", "enum", required=True, choices=("NORMAL", "REVERSED")),
        ), "model.create_capacitive_voltage_transformer", "transformer_id", topology=(CreationTopologyRequirement("H1", required=True), CreationTopologyRequirement("H2", required=True)),
           # H1/H2 are the electrical primary-side endpoints. X1/X2 are
           # measurement-side terminals and are associated through the
           # measurement-channel boundary rather than required Simple Wires.
           endpoint_mapping={"H1": "h1_endpoint", "H2": "h2_endpoint"},
            terminal_names=_BUILTIN_TERMINAL_NAMES["cvt"],
            measurement_terminal_names=("X1", "X2")),
        "relay": _definition("relay", "relay", (
            _p("relay_type", "Relay type", "str", required=True),
        ), "model.create_relay", "relay_id"),
    }
    try:
        definition = definitions[equipment_type]
    except KeyError as exc:
        raise KeyError(f"No canonical creation definition for {equipment_type!r}") from exc
    return definition


def creation_contracts_from_definitions(
    definitions: Mapping[str, CreationDefinition] | None = None,
) -> Mapping[str, Mapping[str, Any]]:
    """Convert the neutral built-in catalogue to Application registry records.

    This performs schema-shape conversion only. CreationContractRegistry remains
    responsible for validating command signatures and rejecting inconsistent
    mappings before a snapshot is accepted.
    """
    if definitions is None:
        source = {
            equipment_type: creation_definition_for(equipment_type)
            for equipment_type in _BUILTIN_TERMINAL_NAMES
        }
    else:
        if not isinstance(definitions, Mapping):
            raise TypeError("definitions must be a mapping when supplied.")
        if not definitions:
            raise ValueError("An explicit creation catalogue cannot be empty.")
        source = definitions
    contracts: dict[str, Mapping[str, Any]] = {}
    for key, definition in source.items():
        if not isinstance(definition, CreationDefinition):
            raise TypeError(f"{key!r}: expected a CreationDefinition.")
        equipment_type = str(key).strip().lower()
        if equipment_type != definition.equipment_type:
            raise ValueError(f"{key!r}: catalogue key does not match definition identity.")
        parameters = tuple({
            "parameter_id": item.parameter_id,
            "display_name": item.display_name,
            "datatype": item.datatype,
            "unit": item.unit,
            "required_before_create": item.required_before_create,
            "default_value": item.default_value,
            "editable": item.editable,
            "derived": item.derived,
            "choices": item.choices,
            "minimum": item.minimum,
            "maximum": item.maximum,
            "validation": dict(item.validation),
        } for item in definition.parameter_definitions)
        contracts[equipment_type] = MappingProxyType({
            "equipment_type": equipment_type,
            "command_type": definition.command_type,
            "id_field": definition.id_field,
            "terminal_contract": tuple(item.terminal_name for item in definition.terminal_requirements),
            "terminal_requirements": tuple({
                "terminal_name": item.terminal_name,
                "required": item.required,
                "cardinality": item.cardinality,
                "allowed_connection_types": item.allowed_connection_types,
                "acquisition_state": item.acquisition_state,
                "initial_endpoint_required": item.initial_endpoint_required,
            } for item in definition.terminal_requirements),
            "parameter_mapping": dict(definition.parameter_mapping),
            "endpoint_mapping": dict(definition.endpoint_mapping),
            "required_connection_terminals": tuple(
                item.name for item in definition.topology_requirements if item.required
            ),
            "parameter_contract": parameters,
            "conditional_requirements": tuple({
                "label": item.label,
                "require_any": item.require_any,
                "require_all": item.require_all,
            } for item in definition.conditional_requirements),
            "command_field_dispositions": {
                field_name: dict(disposition)
                for field_name, disposition in definition.command_field_dispositions.items()
            },
        })
    return MappingProxyType(contracts)


__all__ = [
    "EngineeringParameterDefinition",
    "CreationTerminalRequirement",
    "CreationTopologyRequirement",
    "ConditionalParameterRequirement",
    "CreationDefinition",
    "creation_definition_for",
    "creation_contracts_from_definitions",
]
