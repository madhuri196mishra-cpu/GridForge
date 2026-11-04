# ============================================================
# File: core/application/creation.py
# GridForge V2 — Application creation intent preparation boundary
# Author: Subhendu Mishra
# ============================================================
"""Application-owned translation from immutable creation intent to commands."""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .command import Command
from .commands.model_commands import (
    CreateBusCommand, CreateGridCommand, CreateGeneratorCommand, CreateLoadCommand,
    CreateShuntCommand, CreateLineCommand, CreateTransformerCommand, CreateCableCommand,
    CreateSwitchCommand, CreateDisconnectorCommand, CreateFuseCommand,
)
from .commands.breaker_commands import CreateBreakerCommand
from .commands.capacitor_commands import CreateCapacitorCommand
from .commands.reactor_commands import CreateReactorCommand
from .commands.motor_commands import CreateMotorCommand
from .commands.synchronous_machine_commands import CreateSynchronousMachineCommand
from .commands.solar_commands import CreateSolarCommand
from .commands.battery_commands import CreateBatteryCommand
from .commands.measurement_commands import CreateCurrentTransformerCommand, CreateCapacitiveVoltageTransformerCommand
from .commands.pt_commands import CreatePTCommand
from .commands.relay_commands import CreateRelayCommand

_CREATE_COMMANDS = {
    "model.create_bus": CreateBusCommand,
    "model.create_grid": CreateGridCommand,
    "model.create_generator": CreateGeneratorCommand,
    "model.create_load": CreateLoadCommand,
    "model.create_shunt": CreateShuntCommand,
    "model.create_line": CreateLineCommand,
    "model.create_transformer": CreateTransformerCommand,
    "model.create_cable": CreateCableCommand,
    "model.create_switch": CreateSwitchCommand,
    "model.create_breaker": CreateBreakerCommand,
    "model.create_disconnector": CreateDisconnectorCommand,
    "model.create_fuse": CreateFuseCommand,
    "model.create_capacitor": CreateCapacitorCommand,
    "model.create_reactor": CreateReactorCommand,
    "model.create_motor": CreateMotorCommand,
    "model.create_synchronous_machine": CreateSynchronousMachineCommand,
    "model.create_solar": CreateSolarCommand,
    "model.create_battery": CreateBatteryCommand,
    "model.create_current_transformer": CreateCurrentTransformerCommand,
    "model.create_potential_transformer": CreatePTCommand,
    "model.create_capacitive_voltage_transformer": CreateCapacitiveVoltageTransformerCommand,
    "model.create_relay": CreateRelayCommand,
}


def _freeze_contract_value(value: Any) -> Any:
    """Recursively freeze plain contract data at the Application boundary."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_contract_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_contract_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_contract_value(item) for item in value)
    return value


def _plain_contract_value(value: Any) -> Any:
    """Normalize immutable contract containers for structural equality checks."""
    if isinstance(value, Mapping):
        return {str(key): _plain_contract_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain_contract_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_plain_contract_value(item) for item in value)
    return value


class CreationContractRegistry:
    """Application-owned snapshot of trusted creation contracts injected at composition."""

    def __init__(self) -> None:
        self._contracts: Mapping[str, Mapping[str, Any]] = MappingProxyType({})

    @property
    def configured(self) -> bool:
        return bool(self._contracts)

    def configure(self, contracts: Mapping[str, Mapping[str, Any]]) -> None:
        if not isinstance(contracts, Mapping) or not contracts:
            raise ValueError("At least one canonical creation contract is required.")
        normalized: dict[str, Mapping[str, Any]] = {}
        for raw_type, raw_contract in contracts.items():
            if not isinstance(raw_contract, Mapping):
                raise TypeError(f"Creation contract for {raw_type!r} must be a mapping.")
            equipment_type = str(raw_contract.get("equipment_type", raw_type)).strip().lower()
            if not equipment_type or equipment_type != str(raw_type).strip().lower():
                raise ValueError(f"Creation contract key does not match equipment type {raw_type!r}.")
            command_type = str(raw_contract.get("command_type", "")).strip()
            id_field = str(raw_contract.get("id_field", "")).strip()
            if command_type not in _CREATE_COMMANDS:
                raise ValueError(f"{equipment_type}: unsupported canonical creation command {command_type!r}.")
            if not id_field:
                raise ValueError(f"{equipment_type}: canonical ID field is missing.")
            command_signature = inspect.signature(_CREATE_COMMANDS[command_type])
            command_fields = {
                name for name, parameter in command_signature.parameters.items()
                if name not in {"self", "command_id", "correlation_id", "causation_id"}
            }
            if id_field not in command_fields:
                raise ValueError(f"{equipment_type}: ID field {id_field!r} is absent from {command_type!r}.")

            terminal_contract = tuple(str(role).strip() for role in raw_contract.get("terminal_contract", ()))
            if any(not role for role in terminal_contract) or len(set(terminal_contract)) != len(terminal_contract):
                raise ValueError(f"{equipment_type}: terminal contract contains blank or duplicate roles.")
            raw_terminal_requirements = raw_contract.get("terminal_requirements")
            if raw_terminal_requirements is None:
                # Compatibility for callers that supply only the legacy name
                # inventory. The built-in catalogue always provides rich records.
                terminal_requirements = tuple({
                    "terminal_name": role,
                    "required": False,
                    "cardinality": "single",
                    "allowed_connection_types": (),
                    "acquisition_state": "optional",
                    "initial_endpoint_required": False,
                } for role in terminal_contract)
            else:
                terminal_requirements = tuple(
                    _freeze_contract_value(item) for item in raw_terminal_requirements
                )
                requirement_names = tuple(
                    str(item.get("terminal_name", "")).strip()
                    if isinstance(item, Mapping) else ""
                    for item in terminal_requirements
                )
                if (
                    any(not name for name in requirement_names)
                    or len(set(requirement_names)) != len(requirement_names)
                    or set(requirement_names) != set(terminal_contract)
                ):
                    raise ValueError(
                        f"{equipment_type}: terminal requirement records must cover the "
                        "terminal contract exactly once."
                    )
                for item in terminal_requirements:
                    if not isinstance(item, Mapping):
                        raise TypeError(f"{equipment_type}: terminal requirement records must be mappings.")
                    if item.get("cardinality") not in {"single", "pair", "multiple"}:
                        raise ValueError(f"{equipment_type}: terminal cardinality is invalid.")
                    if item.get("acquisition_state") not in {"required", "optional", "acquired", "pending"}:
                        raise ValueError(f"{equipment_type}: terminal acquisition state is invalid.")
                    if not isinstance(item.get("required"), bool) or not isinstance(item.get("initial_endpoint_required"), bool):
                        raise TypeError(f"{equipment_type}: terminal requirement flags must be boolean.")
                    connection_types = item.get("allowed_connection_types")
                    if not isinstance(connection_types, (tuple, list)) or any(
                        not isinstance(value, str) or not value.strip() for value in connection_types
                    ):
                        raise ValueError(f"{equipment_type}: allowed terminal connection types are invalid.")
            parameter_mapping = dict(raw_contract.get("parameter_mapping", {}))
            endpoint_mapping = dict(raw_contract.get("endpoint_mapping", {}))
            parameter_contract = tuple(_freeze_contract_value(item) for item in raw_contract.get("parameter_contract", ()))
            conditional_requirements = tuple(_freeze_contract_value(item) for item in raw_contract.get("conditional_requirements", ()))
            parameter_ids = tuple(str(item.get("parameter_id", "")).strip() for item in parameter_contract)
            if any(not key for key in parameter_ids) or len(set(parameter_ids)) != len(parameter_ids):
                raise ValueError(f"{equipment_type}: canonical parameter IDs are blank or duplicated.")
            if set(parameter_mapping) != set(parameter_ids):
                raise ValueError(f"{equipment_type}: canonical parameter mapping must cover exactly the declared parameter IDs.")
            raw_field_dispositions = raw_contract.get("command_field_dispositions")
            if not isinstance(raw_field_dispositions, Mapping):
                raise ValueError(
                    f"{equipment_type}: every create-command field requires an explicit lifecycle disposition."
                )
            field_dispositions: dict[str, Mapping[str, str]] = {}
            allowed_dispositions = {
                "metadata", "operating_point", "later_configuration",
                "lifecycle_state", "operational_state", "measurement_association",
                "provisional_default", "not_applicable",
            }
            for field_name, raw_disposition in raw_field_dispositions.items():
                if field_name not in command_fields or not isinstance(raw_disposition, Mapping):
                    raise ValueError(f"{equipment_type}: invalid command-field disposition for {field_name!r}.")
                if not isinstance(raw_disposition.get("kind"), str) or raw_disposition["kind"] not in allowed_dispositions:
                    raise ValueError(f"{equipment_type}: {field_name!r} has an unsupported disposition kind.")
                reason = raw_disposition.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError(f"{equipment_type}: {field_name!r} requires a non-empty disposition reason.")
                workflow = raw_disposition.get("workflow")
                if raw_disposition["kind"] in {
                    "operating_point", "later_configuration", "lifecycle_state",
                    "operational_state", "measurement_association",
                } and (not isinstance(workflow, str) or not workflow.strip()):
                    raise ValueError(f"{equipment_type}: {field_name!r} requires an explicit workflow.")
                field_dispositions[field_name] = MappingProxyType({
                    "kind": raw_disposition["kind"],
                    "reason": reason.strip(),
                    **({"workflow": workflow.strip()} if isinstance(workflow, str) and workflow.strip() else {}),
                })
            for parameter_id, target in parameter_mapping.items():
                if not isinstance(target, str) or target not in command_fields:
                    raise ValueError(f"{equipment_type}: parameter {parameter_id!r} maps to invalid command field {target!r}.")
            for role, target in endpoint_mapping.items():
                if role not in terminal_contract or not isinstance(target, str) or target not in command_fields:
                    raise ValueError(f"{equipment_type}: endpoint mapping {role!r} -> {target!r} is not canonical.")
            # Reject destination collisions during trusted contract configuration,
            # rather than waiting until an engineer attempts to create equipment.
            parameter_targets: dict[str, list[str]] = {}
            for parameter_id, target in parameter_mapping.items():
                parameter_targets.setdefault(target, []).append(parameter_id)
            duplicate_parameter_targets = {
                target: sorted(ids)
                for target, ids in parameter_targets.items()
                if len(ids) > 1
            }
            if duplicate_parameter_targets:
                raise ValueError(
                    f"{equipment_type}: multiple engineering parameters target the same "
                    f"command field: {duplicate_parameter_targets!r}."
                )
            endpoint_targets: dict[str, list[str]] = {}
            for role, target in endpoint_mapping.items():
                endpoint_targets.setdefault(target, []).append(role)
            duplicate_endpoint_targets = {
                target: sorted(roles)
                for target, roles in endpoint_targets.items()
                if len(roles) > 1
            }
            if duplicate_endpoint_targets:
                raise ValueError(
                    f"{equipment_type}: multiple endpoint roles target the same "
                    f"command field: {duplicate_endpoint_targets!r}."
                )
            cross_collisions = set(parameter_targets) & set(endpoint_targets)
            if cross_collisions:
                raise ValueError(
                    f"{equipment_type}: engineering and endpoint mappings collide on "
                    f"command fields: {sorted(cross_collisions)!r}."
                )
            if id_field in set(parameter_targets) | set(endpoint_targets):
                raise ValueError(
                    f"{equipment_type}: ID field {id_field!r} collides with a mapped "
                    "engineering parameter or endpoint."
                )
            generated_presentation_fields = {
                field for field in ("presentation_x", "presentation_y")
                if field in command_fields
            }
            explicitly_supplied_fields = {
                id_field, *parameter_mapping.values(), *endpoint_mapping.values(),
                *generated_presentation_fields,
            }
            disposition_collisions = explicitly_supplied_fields & set(field_dispositions)
            if disposition_collisions:
                raise ValueError(
                    f"{equipment_type}: command fields already supplied by identity, "
                    f"parameter, endpoint, or presentation mappings cannot also have "
                    f"dispositions: {sorted(disposition_collisions)!r}."
                )
            undisposed_fields = command_fields - explicitly_supplied_fields - set(field_dispositions)
            if undisposed_fields:
                raise ValueError(
                    f"{equipment_type}: create-command fields have no explicit engineering "
                    f"disposition: {sorted(undisposed_fields)!r}."
                )
            # Every required constructor field must have an explicit source in
            # the trusted contract. Without this check, a structurally valid
            # mapping can be accepted and then fail later during command
            # preparation because a required argument was never supplied.
            supplied_fields = {
                id_field,
                *parameter_mapping.values(),
                *endpoint_mapping.values(),
            }
            missing_required_fields = {
                name
                for name, parameter in command_signature.parameters.items()
                if name not in {"self", "command_id", "correlation_id", "causation_id"}
                and parameter.default is inspect.Parameter.empty
                and name not in supplied_fields
            }
            if missing_required_fields:
                raise ValueError(
                    f"{equipment_type}: required command fields have no canonical source: "
                    f"{sorted(missing_required_fields)!r}."
                )
            required_terminals = tuple(str(role).strip() for role in raw_contract.get("required_connection_terminals", ()))
            if any(not role for role in required_terminals) or len(set(required_terminals)) != len(required_terminals):
                raise ValueError(f"{equipment_type}: required connection terminal list is invalid.")
            if not set(required_terminals).issubset(set(terminal_contract)):
                raise ValueError(f"{equipment_type}: required connection terminals exceed the terminal contract.")
            unmapped_required_terminals = set(required_terminals) - set(endpoint_mapping)
            if unmapped_required_terminals:
                raise ValueError(
                    f"{equipment_type}: required connection terminals have no endpoint "
                    f"mapping: {sorted(unmapped_required_terminals)!r}."
                )
            known_parameters = set(parameter_ids)
            for rule in conditional_requirements:
                if not isinstance(rule, Mapping):
                    raise TypeError(f"{equipment_type}: conditional requirements must be mappings.")
                referenced = (*tuple(rule.get("require_any", ())), *tuple(rule.get("require_all", ())))
                if not set(referenced).issubset(known_parameters):
                    raise ValueError(f"{equipment_type}: conditional requirement references an unknown parameter.")
            normalized[equipment_type] = MappingProxyType({
                "equipment_type": equipment_type,
                "terminal_contract": terminal_contract,
                "terminal_requirements": terminal_requirements,
                "command_type": command_type,
                "id_field": id_field,
                "parameter_mapping": _freeze_contract_value(parameter_mapping),
                "endpoint_mapping": _freeze_contract_value(endpoint_mapping),
                "required_connection_terminals": required_terminals,
                "parameter_contract": parameter_contract,
                "conditional_requirements": conditional_requirements,
                "command_field_dispositions": MappingProxyType(field_dispositions),
            })
        frozen = MappingProxyType(normalized)
        if self._contracts and _plain_contract_value(self._contracts) != _plain_contract_value(frozen):
            raise RuntimeError("Canonical creation contracts are already configured and cannot be replaced.")
        self._contracts = frozen

    def resolve(self, equipment_type: str) -> Mapping[str, Any]:
        if not self._contracts:
            raise RuntimeError("Canonical creation contracts are not configured in the Application composition path.")
        key = str(equipment_type).strip().lower()
        try:
            return self._contracts[key]
        except KeyError as exc:
            raise ValueError(f"No canonical creation contract is registered for equipment type {key!r}.") from exc

    def snapshot(self) -> Mapping[str, Mapping[str, Any]]:
        return self._contracts

@dataclass(frozen=True, slots=True)
class CreationCommitIntent:
    """Immutable UI intent; it contains data, not an Application command."""
    command_type: str
    id_field: str
    object_id: str
    parameter_mapping: Mapping[str, str]
    endpoint_mapping: Mapping[str, str]
    values: Mapping[str, Any]
    endpoints: Mapping[str, Any]
    position: tuple[float, float] | None = None
    parameter_contract: tuple[Mapping[str, Any], ...] = ()
    conditional_requirements: tuple[Mapping[str, Any], ...] = ()
    equipment_type: str = ""

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.command_type, self.id_field, self.object_id)):
            raise ValueError("CreationCommitIntent requires non-empty command type, ID field, and object ID.")
        object.__setattr__(self, "command_type", self.command_type.strip())
        object.__setattr__(self, "equipment_type", str(self.equipment_type).strip().lower())
        object.__setattr__(self, "id_field", self.id_field.strip())
        object.__setattr__(self, "object_id", self.object_id.strip())
        object.__setattr__(self, "parameter_mapping", MappingProxyType(dict(self.parameter_mapping)))
        object.__setattr__(self, "endpoint_mapping", MappingProxyType(dict(self.endpoint_mapping)))
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "endpoints", MappingProxyType(dict(self.endpoints)))
        object.__setattr__(self, "parameter_contract", tuple(MappingProxyType(dict(x)) for x in self.parameter_contract))
        object.__setattr__(self, "conditional_requirements", tuple(MappingProxyType(dict(x)) for x in self.conditional_requirements))
        if self.position is not None:
            object.__setattr__(self, "position", (float(self.position[0]), float(self.position[1])))


def validate_creation_contract_metadata(
    *,
    equipment_type: str,
    command_type: str,
    id_field: str,
    parameter_mapping: Mapping[str, str],
    endpoint_mapping: Mapping[str, str],
    terminal_contract: tuple[str, ...],
    required_connection_terminals: tuple[str, ...],
    parameter_contract: tuple[Mapping[str, Any], ...],
    conditional_requirements: tuple[Mapping[str, Any], ...],
    canonical_contract: Mapping[str, Any],
    command_field_dispositions: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Reject draft/intent metadata that differs from the trusted composition snapshot."""
    actual = {
        "equipment_type": str(equipment_type).strip().lower(),
        "command_type": str(command_type).strip(),
        "id_field": str(id_field).strip(),
        "parameter_mapping": parameter_mapping,
        "endpoint_mapping": endpoint_mapping,
        "terminal_contract": terminal_contract,
        "required_connection_terminals": required_connection_terminals,
        "parameter_contract": parameter_contract,
        "conditional_requirements": conditional_requirements,
    }
    for field, value in actual.items():
        expected = canonical_contract[field]
        if _plain_contract_value(value) != _plain_contract_value(expected):
            raise ValueError(
                f"{actual['equipment_type']}: draft creation metadata for {field!r} "
                "does not match the trusted canonical Application contract."
            )
    # Schema-4 drafts predate disposition preservation and are deliberately
    # accepted with no supplied disposition metadata. When present, however,
    # the serialized draft must match the trusted Application catalogue exactly.
    if command_field_dispositions is not None:
        if _plain_contract_value(command_field_dispositions) != _plain_contract_value(
            canonical_contract["command_field_dispositions"]
        ):
            raise ValueError(
                f"{actual['equipment_type']}: draft command-field dispositions "
                "do not match the trusted canonical Application contract."
            )


def canonicalize_creation_intent(intent: "CreationCommitIntent", canonical_contract: Mapping[str, Any]) -> "CreationCommitIntent":
    """Build an intent using trusted contract fields, never draft-supplied schema metadata."""
    if not intent.equipment_type:
        raise ValueError("Creation intent must identify its equipment_type for canonical contract resolution.")
    validate_creation_contract_metadata(
        equipment_type=intent.equipment_type,
        command_type=intent.command_type,
        id_field=intent.id_field,
        parameter_mapping=intent.parameter_mapping,
        endpoint_mapping=intent.endpoint_mapping,
        terminal_contract=tuple(canonical_contract["terminal_contract"]),
        required_connection_terminals=tuple(canonical_contract["required_connection_terminals"]),
        parameter_contract=intent.parameter_contract,
        conditional_requirements=intent.conditional_requirements,
        canonical_contract=canonical_contract,
    )
    return CreationCommitIntent(
        command_type=canonical_contract["command_type"],
        id_field=canonical_contract["id_field"],
        object_id=intent.object_id,
        parameter_mapping=canonical_contract["parameter_mapping"],
        endpoint_mapping=canonical_contract["endpoint_mapping"],
        values=intent.values,
        endpoints=intent.endpoints,
        position=intent.position,
        parameter_contract=canonical_contract["parameter_contract"],
        conditional_requirements=canonical_contract["conditional_requirements"],
        equipment_type=canonical_contract["equipment_type"],
    )


def validate_creation_values(equipment_type, values, parameter_mapping, parameter_contract, conditional_requirements=()):
    """Validate draft engineering values using a serialized, framework-neutral contract."""
    if not parameter_contract:
        raise ValueError(f"{equipment_type}: canonical engineering parameter contract is missing.")
    definitions = {str(x.get("parameter_id", "")): x for x in parameter_contract}
    if "" in definitions or len(definitions) != len(parameter_contract):
        raise ValueError(f"{equipment_type}: invalid or duplicate parameter contract identifiers.")
    unknown = set(values) - set(definitions)
    unmapped = set(values) - set(parameter_mapping)
    missing_mappings = set(definitions) - set(parameter_mapping)
    invalid_mappings = {key for key, target in parameter_mapping.items() if not isinstance(target, str) or not target.strip()}
    if unknown or unmapped or missing_mappings or invalid_mappings:
        raise ValueError(
            f"{equipment_type}: invalid engineering contract mappings; "
            f"unknown={sorted(unknown)!r}, unmapped_values={sorted(unmapped)!r}, "
            f"missing_mappings={sorted(missing_mappings)!r}, invalid_targets={sorted(invalid_mappings)!r}"
        )
    normalized = dict(values)
    errors = []
    for key, definition in definitions.items():
        value = normalized.get(key)
        if value is None and definition.get("default_value") is not None:
            value = definition["default_value"]
            normalized[key] = value
        if value is None:
            if definition.get("required_before_create", False):
                errors.append(f"{key}: required value is missing")
            continue
        unit = None
        if isinstance(value, Mapping):
            unit = value.get("unit")
            if "value" not in value:
                errors.append(f"{key}: value mapping must contain 'value'")
                continue
            raw = value["value"]
        else:
            raw = value
        expected_unit = definition.get("unit")
        if unit is not None and expected_unit is None:
            errors.append(f"{key}: unit metadata is not defined by the canonical contract")
            continue
        if unit is not None and expected_unit is not None and str(unit).strip() != str(expected_unit).strip():
            errors.append(f"{key}: unit must be {expected_unit!r}")
            continue
        datatype = str(definition.get("datatype", "str")).lower()
        try:
            if datatype in {"float", "number"}:
                if isinstance(raw, bool): raise TypeError("must be numeric, not boolean")
                n = float(raw)
                if not math.isfinite(n): raise ValueError("must be finite")
                if definition.get("minimum") is not None and n < float(definition["minimum"]): raise ValueError(f"must be >= {definition['minimum']}")
                if definition.get("maximum") is not None and n > float(definition["maximum"]): raise ValueError(f"must be <= {definition['maximum']}")
            elif datatype in {"int", "integer"}:
                if isinstance(raw, bool):
                    raise TypeError("must be an integer, not boolean")
                numeric = float(raw)
                if not math.isfinite(numeric):
                    raise ValueError("must be finite")
                if not numeric.is_integer():
                    raise TypeError("must be an integer")
                n = int(numeric)
                if definition.get("minimum") is not None and n < float(definition["minimum"]):
                    raise ValueError(f"must be >= {definition['minimum']}")
                if definition.get("maximum") is not None and n > float(definition["maximum"]):
                    raise ValueError(f"must be <= {definition['maximum']}")
            elif datatype in {"bool", "boolean"}:
                if not isinstance(raw, bool):
                    raise TypeError("must be boolean")
            elif datatype == "enum":
                if str(raw) not in tuple(str(v) for v in definition.get("choices", ())):
                    raise ValueError(f"must be one of {tuple(definition.get('choices', ()))!r}")
            elif datatype in {"str", "string"}:
                if not isinstance(raw, str):
                    raise TypeError("must be text")
            else:
                raise ValueError(f"unsupported datatype {datatype!r}")
            canonical = n if datatype in {"float", "number", "int", "integer"} else raw
            rules = dict(definition.get("validation", {}))
            if rules.get("non_empty") and isinstance(canonical, str) and not canonical.strip(): raise ValueError("must not be empty")
            if rules.get("nonzero") and canonical == 0: raise ValueError("must be non-zero")
            if "allowed_values" in rules and canonical not in tuple(rules["allowed_values"]): raise ValueError(f"must be one of {tuple(rules['allowed_values'])!r}")
            normalized[key] = canonical
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append(f"{key}: {exc}")
    for rule in conditional_requirements:
        any_keys = tuple(rule.get("require_any", ()))
        all_keys = tuple(rule.get("require_all", ()))
        label = str(rule.get("label") or "Conditional parameter requirement is not satisfied")
        for key in (*any_keys, *all_keys):
            if key not in definitions: errors.append(f"conditional rule references unknown parameter {key!r}")
        if any_keys and not any(normalized.get(key) is not None for key in any_keys): errors.append(label)
        if all_keys and not all(normalized.get(key) is not None for key in all_keys): errors.append(label)
    if errors:
        raise ValueError(f"{equipment_type} engineering validation failed: " + "; ".join(errors))
    return normalized


class CreationCommandPreparer:
    """Single Application boundary that constructs immutable create commands."""

    @staticmethod
    def command_class(command_type: str) -> type[Command]:
        try:
            return _CREATE_COMMANDS[command_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported creation command type: {command_type!r}") from exc

    @classmethod
    def prepare(cls, intent: CreationCommitIntent, *, canonical_contract: Mapping[str, Any]) -> Command:
        if not isinstance(intent, CreationCommitIntent):
            raise TypeError("intent must be a CreationCommitIntent.")
        if not isinstance(canonical_contract, Mapping):
            raise TypeError("canonical_contract must be a trusted Application contract mapping.")
        intent = canonicalize_creation_intent(intent, canonical_contract)
        values = validate_creation_values(intent.command_type, intent.values, intent.parameter_mapping, intent.parameter_contract, intent.conditional_requirements)
        command_class = cls.command_class(intent.command_type)
        signature = inspect.signature(command_class)
        fields = {
            name for name, parameter in signature.parameters.items()
            if name not in {"self", "command_id", "correlation_id", "causation_id"}
        }
        unknown_values = set(values) - set(intent.parameter_mapping)
        if unknown_values:
            raise ValueError(
                f"{intent.command_type}: unmapped engineering values: {sorted(unknown_values)!r}"
            )
        unknown_endpoints = set(intent.endpoints) - set(intent.endpoint_mapping)
        if unknown_endpoints:
            raise ValueError(
                f"{intent.command_type}: unmapped endpoint values: {sorted(unknown_endpoints)!r}"
            )

        # Validate destinations before constructing the payload: ordinary dict
        # assignment would otherwise silently overwrite engineering values,
        # endpoints, the object identity, or generated presentation coordinates.
        parameter_targets: dict[str, list[str]] = {}
        for parameter_id, command_field in intent.parameter_mapping.items():
            if isinstance(command_field, str) and command_field.strip():
                parameter_targets.setdefault(command_field, []).append(parameter_id)
        duplicate_parameter_targets = {
            field: sorted(keys)
            for field, keys in parameter_targets.items()
            if len(keys) > 1
        }
        if duplicate_parameter_targets:
            details = "; ".join(
                f"{field!r} <- {keys!r}"
                for field, keys in sorted(duplicate_parameter_targets.items())
            )
            raise ValueError(
                f"{intent.command_type}: multiple engineering parameters target the same "
                f"command field: {details}"
            )

        endpoint_targets: dict[str, list[str]] = {}
        for semantic_name, command_field in intent.endpoint_mapping.items():
            if isinstance(command_field, str) and command_field.strip():
                endpoint_targets.setdefault(command_field, []).append(semantic_name)
        duplicate_endpoint_targets = {
            field: sorted(keys)
            for field, keys in endpoint_targets.items()
            if len(keys) > 1
        }
        if duplicate_endpoint_targets:
            details = "; ".join(
                f"{field!r} <- {keys!r}"
                for field, keys in sorted(duplicate_endpoint_targets.items())
            )
            raise ValueError(
                f"{intent.command_type}: multiple endpoint roles target the same "
                f"command field: {details}"
            )

        parameter_fields = set(parameter_targets)
        endpoint_fields = set(endpoint_targets)
        cross_collisions = parameter_fields & endpoint_fields
        if cross_collisions:
            details = "; ".join(
                f"{field!r} (parameters={sorted(parameter_targets[field])!r}, "
                f"endpoints={sorted(endpoint_targets[field])!r})"
                for field in sorted(cross_collisions)
            )
            raise ValueError(
                f"{intent.command_type}: engineering and endpoint mappings collide: {details}"
            )

        mapped_fields = parameter_fields | endpoint_fields
        if intent.id_field in mapped_fields:
            owners = []
            if intent.id_field in parameter_targets:
                owners.append(f"parameters={sorted(parameter_targets[intent.id_field])!r}")
            if intent.id_field in endpoint_targets:
                owners.append(f"endpoints={sorted(endpoint_targets[intent.id_field])!r}")
            raise ValueError(
                f"{intent.command_type}: mapped field {intent.id_field!r} collides with "
                f"the object ID field ({', '.join(owners)})."
            )

        generated_position_fields = set()
        if intent.position is not None:
            generated_position_fields = {
                field for field in ("presentation_x", "presentation_y")
                if field in fields
            }
            position_collisions = mapped_fields & generated_position_fields
            if position_collisions:
                details = "; ".join(
                    f"{field!r} (parameters={sorted(parameter_targets.get(field, []))!r}, "
                    f"endpoints={sorted(endpoint_targets.get(field, []))!r})"
                    for field in sorted(position_collisions)
                )
                raise ValueError(
                    f"{intent.command_type}: explicit mappings collide with generated "
                    f"position fields: {details}"
                )

        payload: dict[str, Any] = {intent.id_field: intent.object_id}
        for parameter_id, command_field in intent.parameter_mapping.items():
            if parameter_id in values and values[parameter_id] is not None:
                payload[command_field] = values[parameter_id]
        for semantic_name, command_field in intent.endpoint_mapping.items():
            endpoint = intent.endpoints.get(semantic_name)
            if endpoint is not None:
                payload[command_field] = endpoint
        if intent.position is not None:
            x, y = intent.position
            if "presentation_x" in fields: payload["presentation_x"] = x
            if "presentation_y" in fields: payload["presentation_y"] = y
        unknown = set(payload) - fields
        if unknown:
            raise ValueError(f"{intent.command_type}: unmapped creation payload fields: {sorted(unknown)!r}")
        missing = [
            name for name, parameter in signature.parameters.items()
            if name not in {"self", "command_id", "correlation_id", "causation_id"}
            and parameter.default is inspect.Parameter.empty
            and name not in payload
        ]
        if missing:
            raise ValueError(f"{intent.command_type}: required command fields have no creation source: {missing!r}")
        return command_class(**payload)

def creation_command_contract(command_type: str) -> type[Command]:
    return CreationCommandPreparer.command_class(command_type)

__all__ = ["CreationCommitIntent", "CreationCommandPreparer", "creation_command_contract"]
