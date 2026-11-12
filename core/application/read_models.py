# ============================================================
# File: core/application/read_models.py
# GridForge V2 — Application Read Models
# Author: Subhendu Mishra
# ============================================================
"""Immutable read-side DTOs exposed across the Application boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID

from .revision import ProjectRevision
from core.protection.decision import ProtectionDecision


_CANONICAL_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "BUS": ("nominal_voltage_kv", "voltage_pu", "angle_deg", "frequency_hz", "in_service"),
    "LINE": ("resistance_ohm", "reactance_ohm", "shunt_susceptance_siemens", "rate_mva", "in_service"),
    "CABLE": ("length_km", "rated_voltage_kv", "rated_current_a", "r1", "x1", "b1", "r0", "x0", "b0", "thermal_limit_mva", "conductor_count", "in_service"),
    "TRANSFORMER": ("r", "x", "b", "impedance_basis", "impedance_base_mva", "impedance_base_voltage_kv", "rate_mva", "tap", "tap_ratio", "turns_ratio", "shift", "phase_shift_rad", "phase_shift_deg", "in_service"),
    "SWITCH": ("closed", "in_service", "normally_closed", "rated_voltage_kv", "rated_current_a"),
    "BREAKER": ("closed", "failed", "in_service", "voltage_kv", "current_a", "interrupting_ka"),
    "DISCONNECTOR": ("closed", "in_service", "normally_closed", "voltage_kv", "rated_current_a", "operating_time"),
    "FUSE": ("in_service", "blown", "rated_current_a", "rated_voltage_v", "interrupting_rating_ka"),
    "LOAD": ("p", "q", "in_service"),
    "GENERATOR": ("p", "q", "V_setpoint", "q_min", "q_max", "in_service"),
    "SYNCHRONOUS_MACHINE": ("active_power_injection_mw", "reactive_power_injection_mvar", "rated_power_mva", "rated_voltage_kv", "frequency_hz", "in_service"),
    "MOTOR": ("rated_mva", "rated_kv", "power_factor", "p", "q", "efficiency", "slip", "starting_current_pu", "running", "in_service"),
    "SHUNT": ("g_pu", "b_pu", "in_service"),
    "CAPACITOR": ("reactive_power_injection_mvar", "in_service"),
    "REACTOR": ("reactive_power_injection_mvar", "in_service"),
    "SOLAR": ("p_mw", "q_mvar", "p_min_mw", "p_max_mw", "q_min_mvar", "q_max_mvar", "in_service"),
    "BATTERY": ("p_mw", "q_mvar", "max_charge_mw", "max_discharge_mw", "energy_capacity_mwh", "soc", "soc_min", "soc_max", "in_service"),
    "GRID": ("nominal_voltage_kv", "frequency_hz", "voltage_pu", "angle_deg", "p_mw", "q_mvar", "short_circuit_mva", "x_over_r", "z1_pu", "z2_pu", "z0_pu", "in_service", "grounded"),
    "CT": ("primary_rated_current_a", "secondary_rated_current_a", "ratio", "burden_va", "accuracy_class", "frequency_hz", "polarity", "in_service"),
    "PT": ("primary_voltage_kv", "secondary_voltage_v", "voltage_ratio", "accuracy_class", "burden_va", "phase_displacement_deg", "in_service"),
    "CVT": ("rated_primary_voltage_kv", "rated_secondary_voltage_v", "voltage_ratio", "accuracy_class", "rated_burden_va", "polarity", "frequency_hz", "in_service"),
}


def _freeze(value: Any) -> Any:
    """Recursively freeze supported container values for read-side snapshots."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class InstrumentationWiringReadModel:
    """Detached, immutable Application projection of project wiring identity.

    Values are plain read-side data. Core records, mutable dictionaries and
    canvas geometry are never exposed through this boundary.
    """

    project_id: str
    activation_generation: int
    cables: tuple[Mapping[str, Any], ...]
    cores: tuple[Mapping[str, Any], ...]
    panel_wires: tuple[Mapping[str, Any], ...]
    signals: tuple[Mapping[str, Any], ...]
    signal_bindings: tuple[Mapping[str, Any], ...]
    tag_policy: tuple[Mapping[str, Any], ...]
    connection_hops: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("InstrumentationWiringReadModel.project_id must be non-empty")
        if isinstance(self.activation_generation, bool) or not isinstance(self.activation_generation, int) or self.activation_generation < 1:
            raise ValueError("InstrumentationWiringReadModel.activation_generation must be positive")
        for name in ("cables", "cores", "panel_wires", "signals", "signal_bindings", "tag_policy", "connection_hops"):
            values = tuple(getattr(self, name))
            if any(not isinstance(item, Mapping) for item in values):
                raise TypeError(f"InstrumentationWiringReadModel.{name} must contain mappings")
            object.__setattr__(self, name, tuple(_freeze(item) for item in values))


@dataclass(frozen=True, slots=True)
class EngineeringParameterReadModel:
    """Immutable Application read-side description of one engineering parameter.

    This is read metadata, not engineering authority.  Values originate in the
    authoritative Core model through the Application read service.
    """

    parameter_id: str
    value: Any
    unit: str | None = None
    datatype: str = "unknown"
    choices: tuple[str, ...] = ()
    editable: bool = False
    derived: bool = False
    validation: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    coupling_group: str | None = None
    topology_impact: bool = False
    study_impact: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_id, str) or not self.parameter_id:
            raise ValueError("EngineeringParameterReadModel.parameter_id must be non-empty")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "choices", tuple(str(choice) for choice in self.choices))
        object.__setattr__(self, "validation", _freeze(self.validation))


@dataclass(frozen=True, slots=True)
class ElementReadModel:
    """Stable, UI-neutral immutable snapshot of one network element."""

    object_id: str
    element_type: str
    labels: Mapping[str, str]
    connectivity_refs: tuple[str, ...]
    attributes: Mapping[str, Any]
    engineering_parameters: tuple[EngineeringParameterReadModel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id:
            raise ValueError("ElementReadModel.object_id must be non-empty")
        if not isinstance(self.element_type, str) or not self.element_type:
            raise ValueError("ElementReadModel.element_type must be non-empty")
        if not isinstance(self.labels, Mapping):
            raise TypeError("ElementReadModel.labels must be a mapping")
        if not isinstance(self.connectivity_refs, tuple):
            raise TypeError("ElementReadModel.connectivity_refs must be a tuple")
        if not isinstance(self.attributes, Mapping):
            raise TypeError("ElementReadModel.attributes must be a mapping")
        if not isinstance(self.engineering_parameters, tuple):
            raise TypeError("ElementReadModel.engineering_parameters must be a tuple")
        if not all(isinstance(item, EngineeringParameterReadModel) for item in self.engineering_parameters):
            raise TypeError("ElementReadModel.engineering_parameters must contain EngineeringParameterReadModel values")
        attributes = dict(self.attributes)
        semantic_type = self.element_type.strip().upper()
        for key in _CANONICAL_ATTRIBUTES.get(semantic_type, ()):
            attributes.setdefault(key, None)
        object.__setattr__(self, "labels", _freeze(self.labels))
        object.__setattr__(self, "attributes", _freeze(attributes))
        object.__setattr__(self, "engineering_parameters", tuple(self.engineering_parameters))


@dataclass(frozen=True, slots=True)
class SimpleWireReadModel:
    """Immutable Application snapshot of one authoritative Simple Wire relationship."""

    connection_id: str
    endpoint_a: Mapping[str, Any]
    endpoint_b: Mapping[str, Any]
    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, str) or not self.connection_id:
            raise ValueError("SimpleWireReadModel.connection_id must be non-empty")
        if not isinstance(self.kind, str) or self.kind != "SIMPLE_WIRE":
            raise ValueError("SimpleWireReadModel.kind must be SIMPLE_WIRE")
        object.__setattr__(self, "endpoint_a", _freeze(self.endpoint_a))
        object.__setattr__(self, "endpoint_b", _freeze(self.endpoint_b))


def _canonical_fingerprint_value(value: Any) -> Any:
    """Convert immutable read-model values to deterministic JSON primitives."""
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_fingerprint_value(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Structural fingerprint values must be finite.")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    raise TypeError(f"Unsupported structural fingerprint value: {type(value).__name__}.")


@dataclass(frozen=True, slots=True)
class NetworkReadModel:
    """Immutable collection snapshot used by presentation projections.

    Structural fingerprinting includes equipment identity/family/connectivity
    references and canonical wire endpoints. Labels, ratings, telemetry, and
    other non-topological attributes do not invalidate background SLD audits.
    """

    elements: tuple[ElementReadModel, ...]
    simple_wires: tuple[SimpleWireReadModel, ...] = ()

    @property
    def structural_fingerprint(self) -> str:
        """Return a stable SHA-256 fingerprint of the electrical network structure."""
        elements = [
            {
                "object_id": item.object_id,
                "element_type": item.element_type,
                "connectivity_refs": list(item.connectivity_refs),
            }
            for item in sorted(self.elements, key=lambda value: value.object_id)
        ]
        wires = []
        for wire in sorted(self.simple_wires, key=lambda value: value.connection_id):
            endpoints = [
                _canonical_fingerprint_value(wire.endpoint_a),
                _canonical_fingerprint_value(wire.endpoint_b),
            ]
            endpoints.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
            wires.append({
                "connection_id": wire.connection_id,
                "kind": wire.kind,
                "endpoints": endpoints,
            })
        payload = {"elements": elements, "simple_wires": wires}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RelayInputBindingReadModel:
    """Immutable identifier-only relay input binding."""

    input_name: str
    channel_id: str | None


@dataclass(frozen=True, slots=True)
class RelayReadModel:
    """Immutable presentation snapshot of one authoritative physical Relay."""

    object_id: str
    name: str
    relay_type: str
    function_type: str
    plugin_id: str | None
    settings: Mapping[str, Any]
    in_service: bool
    enabled: bool
    blocked: bool
    picked_up: bool
    tripped: bool
    input_channel_bindings: tuple[RelayInputBindingReadModel, ...] = ()
    decision: ProtectionDecision | None = None
    element_decisions: tuple[ProtectionDecision, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", _freeze(self.settings))
        object.__setattr__(self, "input_channel_bindings", tuple(self.input_channel_bindings))
        decisions = tuple(self.element_decisions)
        if self.decision is not None and not isinstance(self.decision, ProtectionDecision):
            raise TypeError("RelayReadModel.decision must be ProtectionDecision or None.")
        if not decisions and self.decision is not None:
            decisions = (self.decision,)
        if any(not isinstance(item, ProtectionDecision) for item in decisions):
            raise TypeError("RelayReadModel.element_decisions must contain ProtectionDecision values.")
        if any(str(item.relay_id) != str(self.object_id) for item in decisions):
            raise ValueError("RelayReadModel element decisions must belong to its physical Relay.")
        element_ids = tuple(str(item.element_id) for item in decisions)
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("RelayReadModel element decisions must have unique protection-element identities.")
        if self.decision is not None and len(decisions) > 1:
            raise ValueError("A relay-level singular decision cannot represent multiple protection elements.")
        if (
            self.decision is not None
            and decisions
            and str(self.decision.element_id) != str(decisions[0].element_id)
        ):
            raise ValueError("RelayReadModel singular and element decision identities must agree.")
        object.__setattr__(self, "element_decisions", decisions)


@dataclass(frozen=True, slots=True)
class PhysicalRelayConfigurationReadModel:
    """Detached physical Relay configuration, separate from runtime telemetry."""

    relay_id: str
    name: str
    relay_type: str
    plugin_id: str | None
    settings: Mapping[str, Any]
    in_service: bool
    enabled: bool
    blocked: bool
    settings_editable: bool = False
    settings_editability_reason: str = "No plugin-declared setting schema is available."

    def __post_init__(self) -> None:
        for name in ("relay_id", "name", "relay_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"PhysicalRelayConfigurationReadModel.{name} must be non-empty.")
            object.__setattr__(self, name, value.strip())
        if self.plugin_id is not None and (
            not isinstance(self.plugin_id, str) or not self.plugin_id.strip()
        ):
            raise ValueError("plugin_id must be a non-empty string or None.")
        if not isinstance(self.settings, Mapping):
            raise TypeError("Physical Relay settings must be a mapping.")
        if any(not isinstance(getattr(self, name), bool) for name in ("in_service", "enabled", "blocked", "settings_editable")):
            raise TypeError("Physical Relay configuration state flags must be boolean.")
        object.__setattr__(self, "settings", _freeze(self.settings))


@dataclass(frozen=True, slots=True)
class ProtectionSettingValueReadModel:
    """One configured Protection function setting with canonical editor metadata."""

    key: str
    label: str
    datatype: str
    unit: str
    value: Any
    required: bool
    configured: bool
    default_value: Any = None
    has_default: bool = False
    choices: tuple[str, ...] = ()
    choices_by: str | None = None
    choices_by_value: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    editable: bool = True

    def __post_init__(self) -> None:
        for name in ("key", "label", "datatype", "unit"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ProtectionSettingValueReadModel.{name} must be non-empty.")
            object.__setattr__(self, name, value.strip())
        if self.datatype not in {"number", "string"}:
            raise ValueError("Protection setting datatype must be number or string.")
        for name in ("required", "configured", "has_default", "editable"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean.")
        if not isinstance(self.choices, tuple):
            raise TypeError("choices must be a tuple.")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "default_value", _freeze(self.default_value))
        object.__setattr__(self, "choices_by_value", _freeze(self.choices_by_value))


@dataclass(frozen=True, slots=True)
class ProtectionFunctionConfigurationReadModel:
    """Immutable Application snapshot of one project-scoped Protection function."""

    element_id: str
    relay_id: str
    function_code: str
    name: str
    settings: tuple[ProtectionSettingValueReadModel, ...]
    input_channel_ids: Mapping[str, str]
    enabled: bool
    blocked: bool
    priority: int
    action_target_type: str | None = None
    action_target_id: str | None = None
    action_type: str | None = None
    residual_current_input: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    unmodeled_settings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    settings_editable: bool = True
    implementation_status: str = "IMPLEMENTED"

    def __post_init__(self) -> None:
        for name in ("element_id", "relay_id", "function_code"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ProtectionFunctionConfigurationReadModel.{name} must be non-empty.")
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "name", str(self.name))
        if not isinstance(self.settings, tuple) or any(
            not isinstance(item, ProtectionSettingValueReadModel) for item in self.settings
        ):
            raise TypeError("settings must contain ProtectionSettingValueReadModel values.")
        if not isinstance(self.input_channel_ids, Mapping):
            raise TypeError("input_channel_ids must be a mapping.")
        if any(not isinstance(getattr(self, name), bool) for name in ("enabled", "blocked", "settings_editable")):
            raise TypeError("Protection function state flags must be boolean.")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer.")
        object.__setattr__(self, "input_channel_ids", _freeze(self.input_channel_ids))
        object.__setattr__(self, "residual_current_input", _freeze(self.residual_current_input))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        object.__setattr__(self, "unmodeled_settings", _freeze(self.unmodeled_settings))


@dataclass(frozen=True, slots=True)
class StudyResultReadModel:
    """Immutable Application read-side view of one published study result.

    The canonical Core/Analysis result remains the value; this model adds only
    Application identity, lifecycle, provenance, and freshness metadata.
    It is presentation/read state, never a second engineering result authority.
    """

    study_id: UUID
    project_id: str
    activation_generation: int
    source_revision: ProjectRevision
    study_type: str
    status: str
    value: Any = None
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    current: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.study_id, UUID):
            raise TypeError("study_id must be a UUID.")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id must be a non-empty string.")
        if not isinstance(self.activation_generation, int) or isinstance(self.activation_generation, bool) or self.activation_generation < 1:
            raise ValueError("activation_generation must be a positive integer.")
        if not isinstance(self.source_revision, ProjectRevision):
            raise TypeError("source_revision must be ProjectRevision.")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status must be completed, failed, or cancelled.")
        object.__setattr__(self, "project_id", self.project_id.strip())
        object.__setattr__(self, "study_type", str(self.study_type).strip())
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class TransientSignalReadModel:
    """One complete, sample-aligned scalar channel from a transient result."""

    signal_id: str
    label: str
    quantity: str
    unit: str
    values: tuple[float, ...]
    source_indices: tuple[int, ...]
    machine_id: str | None = None
    bus_id: str | None = None
    component: str | None = None

    def __post_init__(self) -> None:
        for name in ("signal_id", "label", "quantity", "unit"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"TransientSignalReadModel.{name} must be non-empty.")
            object.__setattr__(self, name, value.strip())
        values = tuple(float(value) for value in self.values)
        indices = tuple(self.source_indices)
        if len(values) != len(indices):
            raise ValueError("Transient signal values and source indices must have identical lengths.")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Transient signal values must be finite.")
        if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indices):
            raise ValueError("Transient signal source indices must be non-negative integers.")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "source_indices", indices)


@dataclass(frozen=True, slots=True)
class TransientAlgebraicQualityReadModel:
    """Solver-quality evidence for one recorded transient sample."""

    sample_index: int
    time_s: float
    status: str
    accepted: bool
    quality_verified: bool
    solver_name: str
    solver_time_s: float | None = None
    bus_voltages: Mapping[str, complex] = field(default_factory=lambda: MappingProxyType({}))
    absolute_residual_norm: float | None = None
    normalized_residual: float | None = None
    residual_tolerance: float | None = None
    condition_number: float | None = None
    condition_limit: float | None = None
    bus_ids: tuple[str, ...] = ()
    topology_revision: int | None = None
    source_ids: tuple[str, ...] = ()
    active_fault_id: str | None = None
    active_fault_bus_id: str | None = None
    active_fault_type: str | None = None
    study_provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.sample_index, bool) or not isinstance(self.sample_index, int) or self.sample_index < 0:
            raise ValueError("Transient algebraic-quality sample_index must be a non-negative integer.")
        time_s = float(self.time_s)
        if not math.isfinite(time_s):
            raise ValueError("Transient algebraic-quality time_s must be finite.")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("Transient algebraic-quality status must be non-empty.")
        if not isinstance(self.solver_name, str) or not self.solver_name.strip():
            raise ValueError("Transient algebraic-quality solver_name must be non-empty.")
        if not isinstance(self.accepted, bool) or not isinstance(self.quality_verified, bool):
            raise TypeError("Transient algebraic-quality acceptance flags must be booleans.")
        if not isinstance(self.bus_voltages, Mapping) or not isinstance(self.study_provenance, Mapping):
            raise TypeError("Transient algebraic-quality voltage and provenance values must be mappings.")
        voltages = {str(bus_id): complex(value) for bus_id, value in self.bus_voltages.items()}
        if any(not (math.isfinite(value.real) and math.isfinite(value.imag)) for value in voltages.values()):
            raise ValueError("Transient algebraic-quality Bus voltages must be finite.")
        if self.solver_time_s is not None and not math.isfinite(float(self.solver_time_s)):
            raise ValueError("Transient algebraic-quality solver_time_s must be finite when supplied.")
        known_statuses = {"ACCEPTED", "UNVERIFIED", "SINGULAR", "ILL_CONDITIONED", "RESIDUAL_REJECTED", "NON_FINITE"}
        if self.status not in known_statuses:
            raise ValueError(f"Unsupported transient algebraic-quality status {self.status!r}.")
        if self.accepted != (self.status in {"ACCEPTED", "UNVERIFIED"}):
            raise ValueError("Transient algebraic-quality acceptance flag does not match its status.")
        if self.quality_verified and self.status == "UNVERIFIED":
            raise ValueError("UNVERIFIED transient algebraic quality cannot claim verification.")
        for name in ("absolute_residual_norm", "normalized_residual", "residual_tolerance", "condition_number", "condition_limit"):
            value = getattr(self, name)
            if value is None:
                continue
            numeric = float(value)
            allow_infinite_residual = (
                name in {"absolute_residual_norm", "normalized_residual"}
                and self.status in {"RESIDUAL_REJECTED", "ILL_CONDITIONED"}
                and math.isinf(numeric)
            )
            allow_infinite_condition = name == "condition_number" and self.status == "SINGULAR" and math.isinf(numeric)
            if math.isnan(numeric) or numeric < 0.0 or (
                not math.isfinite(numeric) and not (allow_infinite_residual or allow_infinite_condition)
            ):
                raise ValueError(f"Transient algebraic-quality {name} is outside its canonical numeric domain.")
            object.__setattr__(self, name, numeric)
        if self.residual_tolerance is not None and self.residual_tolerance <= 0.0:
            raise ValueError("Transient algebraic-quality residual_tolerance must be positive.")
        if self.condition_limit is not None and self.condition_limit <= 1.0:
            raise ValueError("Transient algebraic-quality condition_limit must be greater than one.")
        if self.normalized_residual is not None and self.residual_tolerance is None:
            raise ValueError("Transient algebraic-quality normalized_residual requires a tolerance.")
        if self.quality_verified and self.status == "ACCEPTED":
            required = (
                self.absolute_residual_norm, self.normalized_residual,
                self.residual_tolerance, self.condition_number, self.condition_limit,
            )
            if any(value is None for value in required):
                raise ValueError("Verified accepted algebraic quality requires residual and conditioning evidence.")
            if self.normalized_residual > self.residual_tolerance or self.condition_number > self.condition_limit:
                raise ValueError("Verified accepted algebraic quality exceeds its residual or conditioning limit.")
        if self.topology_revision is not None and (
            isinstance(self.topology_revision, bool) or not isinstance(self.topology_revision, int)
            or self.topology_revision < 0
        ):
            raise ValueError("Transient algebraic-quality topology_revision must be non-negative.")
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "status", self.status.strip())
        object.__setattr__(self, "solver_name", self.solver_name.strip())
        if self.solver_time_s is not None:
            object.__setattr__(self, "solver_time_s", float(self.solver_time_s))
        object.__setattr__(self, "bus_voltages", MappingProxyType(voltages))
        object.__setattr__(self, "bus_ids", tuple(str(value) for value in self.bus_ids))
        object.__setattr__(self, "source_ids", tuple(str(value) for value in self.source_ids))
        object.__setattr__(self, "study_provenance", _freeze(self.study_provenance))
        object.__setattr__(self, "diagnostics", tuple(str(value) for value in self.diagnostics))


@dataclass(frozen=True, slots=True)
class TransientEventReadModel:
    """Immutable event-timeline record retaining dispatch and failure evidence."""

    event_id: str
    event_type: str
    scheduled_time: float
    dispatch_time: float | None
    priority: int | None
    description: str | None
    status: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    result: Any = None
    failure_type: str | None = None
    failure_message: str | None = None
    pre_event_revision: int | None = None
    post_event_revision: int | None = None
    post_event_re_evaluation_status: str = "not_run"
    post_event_re_evaluation_failure_type: str | None = None
    post_event_re_evaluation_failure_message: str | None = None
    transition: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.event_type:
            raise ValueError("Transient event identity and type must be non-empty.")
        if not math.isfinite(float(self.scheduled_time)) or float(self.scheduled_time) < 0.0:
            raise ValueError("Transient event scheduled_time must be finite and non-negative.")
        if self.dispatch_time is not None and (
            not math.isfinite(float(self.dispatch_time)) or float(self.dispatch_time) < 0.0
        ):
            raise ValueError("Transient event dispatch_time must be finite and non-negative when known.")
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("Transient event status must be succeeded or failed.")
        if self.post_event_re_evaluation_status not in {"not_run", "succeeded", "failed", "unavailable"}:
            raise ValueError("Transient event re-evaluation status is invalid.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Transient event metadata must be a mapping.")
        if self.transition is not None and not isinstance(self.transition, Mapping):
            raise TypeError("Transient event transition must be a typed mapping when supplied.")
        object.__setattr__(self, "scheduled_time", float(self.scheduled_time))
        if self.dispatch_time is not None:
            object.__setattr__(self, "dispatch_time", float(self.dispatch_time))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        object.__setattr__(self, "result", _freeze(self.result))
        if self.transition is not None:
            transition = dict(self.transition)
            if not isinstance(transition.get("transition_type"), str) or not transition["transition_type"].strip():
                raise ValueError("Transient event transition requires a non-empty transition_type.")
            for key in ("target_kind", "target_id"):
                value = transition.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ValueError(f"Transient event transition {key} must be non-empty when supplied.")
            object.__setattr__(self, "transition", _freeze(transition))

    @property
    def marker_time(self) -> float:
        """Use actual dispatch time for chart markers, retaining scheduled time separately."""
        return self.scheduled_time if self.dispatch_time is None else self.dispatch_time


@dataclass(frozen=True, slots=True)
class TransientDiscontinuityReadModel:
    """UI-neutral pre/post electrical sides of one event boundary."""

    time_s: float
    event_ids: tuple[str, ...]
    post_status: str
    pre_state: tuple[float, ...]
    pre_terminal_voltages: Mapping[str, complex]
    pre_electrical_powers: Mapping[str, tuple[float, float]]
    pre_topology_revision: int | None
    post_state: tuple[float, ...] | None = None
    post_terminal_voltages: Mapping[str, complex] | None = None
    post_electrical_powers: Mapping[str, tuple[float, float]] | None = None
    post_topology_revision: int | None = None

    def __post_init__(self) -> None:
        time_s = float(self.time_s)
        if not math.isfinite(time_s) or time_s < 0.0:
            raise ValueError("Transient discontinuity time must be finite and non-negative.")
        if self.post_status not in {"succeeded", "dispatch_failed", "post_evaluation_failed"}:
            raise ValueError("Transient discontinuity post_status is invalid.")
        pre_state = tuple(float(value) for value in self.pre_state)
        post_state = None if self.post_state is None else tuple(float(value) for value in self.post_state)
        if any(not math.isfinite(value) for value in pre_state) or (post_state is not None and any(not math.isfinite(value) for value in post_state)):
            raise ValueError("Transient discontinuity states must be finite.")
        if self.post_status == "succeeded" and (
            post_state is None or self.post_terminal_voltages is None or self.post_electrical_powers is None
        ):
            raise ValueError("Successful transient discontinuities require both electrical sides.")
        if self.post_status != "succeeded" and (
            post_state is not None or self.post_terminal_voltages is not None or self.post_electrical_powers is not None
        ):
            raise ValueError("Failed transient discontinuities cannot claim a post electrical solution.")
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "event_ids", tuple(str(value) for value in self.event_ids))
        object.__setattr__(self, "pre_state", pre_state)
        object.__setattr__(self, "post_state", post_state)
        object.__setattr__(self, "pre_terminal_voltages", _freeze(self.pre_terminal_voltages))
        object.__setattr__(self, "pre_electrical_powers", _freeze(self.pre_electrical_powers))
        if self.post_terminal_voltages is not None:
            object.__setattr__(self, "post_terminal_voltages", _freeze(self.post_terminal_voltages))
        if self.post_electrical_powers is not None:
            object.__setattr__(self, "post_electrical_powers", _freeze(self.post_electrical_powers))


@dataclass(frozen=True, slots=True)
class TransientResultPresentationReadModel:
    """Complete UI-neutral transient samples, selectable signals and event history.

    This is a read-side projection of solver output. It contains every source
    sample in its original order; it performs no interpolation or down-sampling.
    """

    study_id: UUID
    project_id: str
    activation_generation: int
    source_revision: ProjectRevision
    study_type: str
    status: str
    message: str
    current: bool
    historical: bool
    record_id: UUID | None
    saved_at: str | None
    time_s: tuple[float, ...]
    signals: tuple[TransientSignalReadModel, ...]
    events: tuple[TransientEventReadModel, ...]
    provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    algebraic_quality: tuple[TransientAlgebraicQualityReadModel, ...] = ()
    discontinuities: tuple[TransientDiscontinuityReadModel, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.study_id, UUID):
            raise TypeError("Transient presentation study_id must be UUID.")
        if not isinstance(self.source_revision, ProjectRevision):
            raise TypeError("Transient presentation source_revision must be ProjectRevision.")
        if self.historical and self.record_id is None:
            raise ValueError("Historical transient presentations require archive record identity.")
        if self.historical and self.current:
            raise ValueError("Historical transient presentations cannot be marked current.")
        if self.historical and not self.saved_at:
            raise ValueError("Historical transient presentations require archive saved_at provenance.")
        times = tuple(float(value) for value in self.time_s)
        if any(not math.isfinite(value) for value in times):
            raise ValueError("Transient presentation time samples must be finite.")
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError("Transient presentation time samples must remain strictly increasing.")
        count = len(times)
        signals = tuple(self.signals)
        if not all(isinstance(signal, TransientSignalReadModel) for signal in signals):
            raise TypeError("Transient presentation signals must be TransientSignalReadModel values.")
        if len({signal.signal_id for signal in signals}) != len(signals):
            raise ValueError("Transient presentation signal IDs must be unique.")
        expected_indices = tuple(range(count))
        if any(signal.source_indices != expected_indices or len(signal.values) != count for signal in signals):
            raise ValueError("Every transient signal must preserve every original source sample index.")
        events = tuple(self.events)
        if not all(isinstance(event, TransientEventReadModel) for event in events):
            raise TypeError("Transient presentation events must be TransientEventReadModel values.")
        discontinuities = tuple(self.discontinuities)
        if not all(isinstance(item, TransientDiscontinuityReadModel) for item in discontinuities):
            raise TypeError("Transient discontinuities must be TransientDiscontinuityReadModel values.")
        if any(not any(math.isclose(item.time_s, sample, rel_tol=0.0, abs_tol=1e-12) for sample in times) for item in discontinuities):
            raise ValueError("Transient discontinuities must align with a base trajectory boundary time.")
        algebraic_quality = tuple(self.algebraic_quality)
        if not all(isinstance(item, TransientAlgebraicQualityReadModel) for item in algebraic_quality):
            raise TypeError("Transient algebraic quality must contain TransientAlgebraicQualityReadModel values.")
        if algebraic_quality:
            if len(algebraic_quality) != count:
                raise ValueError("Transient algebraic-quality records must align one-to-one with source samples.")
            for index, item in enumerate(algebraic_quality):
                if item.sample_index != index or not math.isclose(item.time_s, times[index], rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError("Transient algebraic-quality records must preserve exact sample order and time alignment.")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("Transient presentation provenance must be a mapping.")
        object.__setattr__(self, "message", str(self.message))
        object.__setattr__(self, "time_s", times)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "algebraic_quality", algebraic_quality)
        object.__setattr__(self, "discontinuities", discontinuities)
        object.__setattr__(self, "provenance", _freeze(self.provenance))

    @property
    def sample_count(self) -> int:
        return len(self.time_s)


@dataclass(frozen=True, slots=True)
class ProtectionReadModel:
    """Immutable collection snapshot of protection-domain read data."""

    relays: tuple[RelayReadModel, ...]


@dataclass(frozen=True, slots=True)
class MeasurementChannelReadModel:
    """Detached Application snapshot of one live MeasurementChannel sample."""

    channel_id: str
    value: Any
    unit: str
    signal_type: str
    phase: str
    quality: str
    available: bool
    timestamp: float | None
    timestamp_domain: str | None
    sample_sequence: int | None
    stale_after: float | None
    producer_id: str | None
    validity: str
    source_id: str | None = None
    source_terminal_role: str | None = None
    name: str = ""
    nominal_value: float | None = None
    scale: float | None = None
    polarity: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.channel_id, str) or not self.channel_id.strip():
            raise ValueError("MeasurementChannelReadModel.channel_id must be non-empty.")
        if not isinstance(self.available, bool):
            raise TypeError("MeasurementChannelReadModel.available must be bool.")
        if self.timestamp is not None and (
            isinstance(self.timestamp, bool)
            or not isinstance(self.timestamp, (int, float))
            or not math.isfinite(self.timestamp)
        ):
            raise ValueError("MeasurementChannelReadModel.timestamp must be finite or None.")
        if self.sample_sequence is not None and (
            isinstance(self.sample_sequence, bool)
            or not isinstance(self.sample_sequence, int)
            or self.sample_sequence < 0
        ):
            raise ValueError("MeasurementChannelReadModel.sample_sequence must be non-negative or None.")
        if self.stale_after is not None and (
            isinstance(self.stale_after, bool)
            or not isinstance(self.stale_after, (int, float))
            or not math.isfinite(self.stale_after)
            or self.stale_after < 0
        ):
            raise ValueError("MeasurementChannelReadModel.stale_after must be finite and non-negative.")
        object.__setattr__(self, "channel_id", self.channel_id.strip())
        object.__setattr__(self, "value", _freeze(self.value))
        for field_name in ("source_id", "source_terminal_role"):
            field_value = getattr(self, field_name)
            if field_value is not None and (
                not isinstance(field_value, str) or not field_value.strip()
            ):
                raise ValueError(f"MeasurementChannelReadModel.{field_name} must be non-empty or None.")
        for field_name in ("nominal_value", "scale", "polarity"):
            field_value = getattr(self, field_name)
            if field_value is not None and (
                isinstance(field_value, bool)
                or not isinstance(field_value, (int, float))
                or not math.isfinite(field_value)
            ):
                raise ValueError(f"MeasurementChannelReadModel.{field_name} must be finite or None.")


__all__ = [
    "ElementReadModel",
    "EngineeringParameterReadModel",
    "NetworkReadModel",
    "SimpleWireReadModel",
    "ProtectionReadModel",
    "MeasurementChannelReadModel",
    "RelayInputBindingReadModel",
    "RelayReadModel",
    "PhysicalRelayConfigurationReadModel",
    "ProtectionSettingValueReadModel",
    "ProtectionFunctionConfigurationReadModel",
    "StudyResultReadModel",
    "TransientSignalReadModel",
    "TransientAlgebraicQualityReadModel",
    "TransientDiscontinuityReadModel",
    "TransientEventReadModel",
    "TransientResultPresentationReadModel",
    "InstrumentationWiringReadModel",
]
