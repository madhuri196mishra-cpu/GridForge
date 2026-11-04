# ============================================================
# File: core/application/read_models.py
# GridForge V2 — Application Read Models
# Author: Subhendu Mishra
# ============================================================
"""Immutable read-side DTOs exposed across the Application boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
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
class ConfigurationFieldAssessmentReadModel:
    """Immutable assessment of one catalogue-owned configuration obligation."""

    field_name: str
    kind: str
    reason: str
    workflow: str | None
    status: str

    def __post_init__(self) -> None:
        if not self.field_name or not self.kind or not self.reason:
            raise ValueError("Configuration field assessment requires field, kind and reason.")
        if self.status not in {
            "PENDING", "RESOLVED", "NOT_APPLICABLE", "PROVISIONAL",
            "UNAVAILABLE", "REVIEW_REQUIRED",
        }:
            raise ValueError(f"Unsupported configuration field status: {self.status!r}.")
        if self.workflow is not None and not self.workflow.strip():
            raise ValueError("Configuration workflow must be non-empty when supplied.")


@dataclass(frozen=True, slots=True)
class EquipmentConfigurationAssessmentReadModel:
    """Detached Application assessment; engineering values remain owned by Core."""

    equipment_id: str
    equipment_type: str
    fields: tuple[ConfigurationFieldAssessmentReadModel, ...]
    status: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.equipment_id or not self.equipment_type:
            raise ValueError("Equipment configuration assessment requires an equipment identity.")
        fields = tuple(self.fields)
        if not all(isinstance(item, ConfigurationFieldAssessmentReadModel) for item in fields):
            raise TypeError("Equipment configuration assessment fields must be typed field assessments.")
        names = tuple(item.field_name for item in fields)
        if len(set(names)) != len(names):
            raise ValueError("Equipment configuration assessment contains duplicate fields.")
        object.__setattr__(self, "fields", fields)
        statuses = {item.status for item in fields}
        if not statuses:
            status = "REVIEW_REQUIRED"
        elif "REVIEW_REQUIRED" in statuses:
            status = "REVIEW_REQUIRED"
        elif "UNAVAILABLE" in statuses:
            status = "UNAVAILABLE"
        elif "PENDING" in statuses:
            status = "PENDING"
        elif "PROVISIONAL" in statuses:
            status = "PROVISIONAL"
        elif statuses == {"NOT_APPLICABLE"}:
            status = "NOT_APPLICABLE"
        else:
            status = "RESOLVED"
        object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class ElementReadModel:
    """Stable, UI-neutral immutable snapshot of one network element."""

    object_id: str
    element_type: str
    labels: Mapping[str, str]
    connectivity_refs: tuple[str, ...]
    attributes: Mapping[str, Any]
    engineering_parameters: tuple[EngineeringParameterReadModel, ...] = ()
    configuration_assessment: EquipmentConfigurationAssessmentReadModel | None = None

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
        if self.configuration_assessment is not None and not isinstance(
            self.configuration_assessment, EquipmentConfigurationAssessmentReadModel
        ):
            raise TypeError("ElementReadModel.configuration_assessment must be an equipment configuration assessment or None.")
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


@dataclass(frozen=True, slots=True)
class NetworkReadModel:
    """Immutable collection snapshot used by presentation projections."""

    elements: tuple[ElementReadModel, ...]
    simple_wires: tuple[SimpleWireReadModel, ...] = ()


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
        object.__setattr__(self, "scheduled_time", float(self.scheduled_time))
        if self.dispatch_time is not None:
            object.__setattr__(self, "dispatch_time", float(self.dispatch_time))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        object.__setattr__(self, "result", _freeze(self.result))

    @property
    def marker_time(self) -> float:
        """Use actual dispatch time for chart markers, retaining scheduled time separately."""
        return self.scheduled_time if self.dispatch_time is None else self.dispatch_time


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
        object.__setattr__(self, "provenance", _freeze(self.provenance))

    @property
    def sample_count(self) -> int:
        return len(self.time_s)


@dataclass(frozen=True, slots=True)
class ProtectionReadModel:
    """Immutable collection snapshot of protection-domain read data."""

    relays: tuple[RelayReadModel, ...]


__all__ = [
    "ConfigurationFieldAssessmentReadModel",
    "ElementReadModel",
    "EquipmentConfigurationAssessmentReadModel",
    "EngineeringParameterReadModel",
    "NetworkReadModel",
    "SimpleWireReadModel",
    "ProtectionReadModel",
    "RelayInputBindingReadModel",
    "RelayReadModel",
    "StudyResultReadModel",
    "TransientSignalReadModel",
    "TransientAlgebraicQualityReadModel",
    "TransientEventReadModel",
    "TransientResultPresentationReadModel",
]
