"""Shared, identity-preserving contracts across generator, motor and load plugins.

These are plugin interoperability DTOs only. Core remains authoritative for
equipment identity and MeasurementChannel; Application remains authoritative
for studies, scheduling, persistence, events and results.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from core.model import EndpointReference, EndpointReferenceKind, EquipmentType
from core.measurement.measurement_channel import MeasurementPhase, MeasurementSignalType


class DynamicsEquipmentKind(str, Enum):
    GENERATOR = "generator"
    MOTOR = "motor"
    LOAD = "load"


_CORE_EQUIPMENT_TYPES = {
    DynamicsEquipmentKind.GENERATOR: EquipmentType.GENERATOR,
    DynamicsEquipmentKind.MOTOR: EquipmentType.MOTOR,
    DynamicsEquipmentKind.LOAD: EquipmentType.LOAD,
}


class DynamicsModelReadiness(str, Enum):
    CATALOGUE_ONLY = "catalogue_only"
    EXPERIMENTAL = "experimental"
    HOST_SOLVER_REQUIRED = "host_solver_required"
    REFERENCE_VALIDATED = "reference_validated"
    FIELD_VALIDATED = "field_validated"


@dataclass(frozen=True, slots=True)
class DynamicsEquipmentBinding:
    """Reference an existing Core equipment identity; never contain a model object."""
    equipment_type: EquipmentType
    equipment_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.equipment_type, EquipmentType):
            raise TypeError("equipment_type must be Core EquipmentType")
        if not isinstance(self.equipment_id, str) or not self.equipment_id.strip():
            raise ValueError("equipment_id must be a non-empty Core equipment identifier")
        object.__setattr__(self, "equipment_id", self.equipment_id.strip())


def equipment_binding_from_endpoint(endpoint: EndpointReference) -> DynamicsEquipmentBinding:
    """Resolve identity from a canonical terminal reference, not a copied object."""
    if not isinstance(endpoint, EndpointReference):
        raise TypeError("endpoint must be a Core EndpointReference")
    if endpoint.kind is not EndpointReferenceKind.TERMINAL:
        raise ValueError("dynamics equipment binding requires a terminal endpoint")
    if endpoint.equipment_type not in set(_CORE_EQUIPMENT_TYPES.values()):
        raise ValueError("endpoint does not identify a generator, motor or load")
    return DynamicsEquipmentBinding(endpoint.equipment_type, endpoint.object_id)


@dataclass(frozen=True, slots=True)
class MeasurementPointContract:
    """Normalized view of a plugin's logical point; not a second channel/value store."""
    equipment_kind: DynamicsEquipmentKind
    family_id: str
    point_id: str
    name: str
    signal_type: MeasurementSignalType
    phase: MeasurementPhase
    unit: str
    consumers: frozenset[str]
    required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.equipment_kind, DynamicsEquipmentKind):
            raise TypeError("equipment_kind must be DynamicsEquipmentKind")
        for name in ("family_id", "point_id", "name", "unit"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.signal_type, MeasurementSignalType):
            raise TypeError("signal_type must be canonical MeasurementSignalType")
        if not isinstance(self.phase, MeasurementPhase):
            raise TypeError("phase must be canonical MeasurementPhase")
        if not isinstance(self.consumers, frozenset) or not self.consumers:
            raise TypeError("consumers must be a non-empty frozenset")
        if any(not isinstance(item, str) or not item.strip() for item in self.consumers):
            raise ValueError("consumers must contain non-empty strings")
        if not isinstance(self.required, bool):
            raise TypeError("required must be bool")


def normalize_measurement_points(
    points: Iterable[object],
    *,
    equipment_kind: DynamicsEquipmentKind,
    family_id: str,
) -> tuple[MeasurementPointContract, ...]:
    """Adapt existing plugin point DTOs without replacing canonical Core channels."""
    if not isinstance(equipment_kind, DynamicsEquipmentKind):
        raise TypeError("equipment_kind must be DynamicsEquipmentKind")
    if not isinstance(family_id, str) or not family_id.strip():
        raise ValueError("family_id must be a non-empty string")
    normalized: list[MeasurementPointContract] = []
    seen: set[str] = set()
    for point in points:
        point_id = getattr(point, "point_id", None)
        if not isinstance(point_id, str) or not point_id.strip():
            raise TypeError("measurement point must expose a non-empty point_id")
        if point_id in seen:
            raise ValueError(f"duplicate measurement point id {point_id!r} for {family_id!r}")
        seen.add(point_id)
        normalized.append(MeasurementPointContract(
            equipment_kind=equipment_kind,
            family_id=family_id,
            point_id=point_id,
            name=getattr(point, "name"),
            signal_type=getattr(point, "signal_type"),
            phase=getattr(point, "phase"),
            unit=getattr(point, "unit"),
            consumers=frozenset(getattr(point, "consumers")),
            required=getattr(point, "required", False),
        ))
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class DynamicsModelCapability:
    """Truthful model-availability declaration for one equipment family."""
    plugin_id: str
    equipment_kind: DynamicsEquipmentKind
    family_id: str
    model_id: str
    readiness: DynamicsModelReadiness
    study_types: frozenset[str] = frozenset()
    numerical_workloads: frozenset[str] = frozenset()
    measurement_point_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("plugin_id", "family_id", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.equipment_kind, DynamicsEquipmentKind):
            raise TypeError("equipment_kind must be DynamicsEquipmentKind")
        if not isinstance(self.readiness, DynamicsModelReadiness):
            raise TypeError("readiness must be DynamicsModelReadiness")
        for name in ("study_types", "numerical_workloads", "measurement_point_ids"):
            value = getattr(self, name)
            if not isinstance(value, frozenset):
                raise TypeError(f"{name} must be a frozenset")
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{name} must contain non-empty strings")
        if self.readiness is DynamicsModelReadiness.CATALOGUE_ONLY and self.study_types:
            raise ValueError("catalogue-only models cannot advertise executable study types")


def validate_unique_equipment_participants(
    bindings: Iterable[DynamicsEquipmentBinding],
) -> tuple[DynamicsEquipmentBinding, ...]:
    """Reject repeated Core equipment identity in a coupled dynamics participant set."""
    result = tuple(bindings)
    if any(not isinstance(item, DynamicsEquipmentBinding) for item in result):
        raise TypeError("participants must contain DynamicsEquipmentBinding values")
    keys = [(item.equipment_type, item.equipment_id) for item in result]
    if len(keys) != len(set(keys)):
        raise ValueError("a Core equipment identity may participate only once in a coupled study")
    return result


__all__ = [
    "DynamicsEquipmentKind", "DynamicsModelReadiness", "DynamicsEquipmentBinding",
    "MeasurementPointContract", "DynamicsModelCapability",
    "equipment_binding_from_endpoint", "normalize_measurement_points",
    "validate_unique_equipment_participants",
]
