# ============================================================
# File: ui/projection/projection_state.py
# GridForge V2 — Projection State Contract
# Author: Subhendu Mishra
# ============================================================
"""Framework-neutral presentation state for UI projections.

This state is a view-facing snapshot. It is deliberately not an electrical
model and must not become a second source of engineering truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from core.application.read_models import EquipmentConfigurationAssessmentReadModel


def _freeze(value: Any) -> Any:
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
class EngineeringParameterState:
    """Immutable generic presentation snapshot of one engineering parameter."""

    parameter_id: str
    value: Any
    unit: str | None = None
    datatype: str = "unknown"
    choices: tuple[str, ...] = ()
    editable: bool = False
    derived: bool = False
    validation: Mapping[str, Any] = field(default_factory=dict)
    coupling_group: str | None = None
    topology_impact: bool = False
    study_impact: bool = False
    display_name: str | None = None
    required_before_create: bool = False
    default_value: Any = None
    minimum: float | None = None
    maximum: float | None = None
    inspectable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_id, str) or not self.parameter_id:
            raise ValueError("EngineeringParameterState.parameter_id must be non-empty")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "display_name", (self.display_name or self.parameter_id).strip())
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("EngineeringParameterState minimum cannot exceed maximum.")
        object.__setattr__(self, "default_value", _freeze(self.default_value))
        object.__setattr__(self, "validation", _freeze(self.validation))


@dataclass(frozen=True, slots=True)
class ProjectionState:
    """Stable, render-ready state derived from authoritative model data."""

    object_id: str
    display_type: str
    labels: tuple[str, ...] = ()
    geometry: Any = None
    status: str | None = None
    connectivity_refs: tuple[str, ...] = ()
    visual_flags: frozenset[str] = field(default_factory=frozenset)
    engineering_parameters: tuple[EngineeringParameterState, ...] = ()
    identity_kind: str = "element"
    presentation_id: str | None = None
    connection_kind: str | None = None
    endpoint_a: Mapping[str, Any] | None = None
    endpoint_b: Mapping[str, Any] | None = None
    route_ownership: str | None = None
    route_points: tuple[tuple[float, float], ...] = ()
    placement: tuple[float, float] | None = None
    terminal_contract: tuple[str, ...] = ()
    endpoint_references: Mapping[str, Any] = field(default_factory=dict)
    validation_state: Mapping[str, Any] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)
    configuration_assessment: EquipmentConfigurationAssessmentReadModel | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id:
            raise ValueError("ProjectionState object_id must be a non-empty string")
        if not isinstance(self.display_type, str) or not self.display_type:
            raise ValueError("ProjectionState display_type must be a non-empty string")
        if self.identity_kind not in {"element", "protection", "simple_wire", "draft"}:
            raise ValueError("ProjectionState.identity_kind must be element, protection, simple_wire, or draft")
        if self.presentation_id is not None and (not isinstance(self.presentation_id, str) or not self.presentation_id):
            raise ValueError("ProjectionState.presentation_id must be non-empty when provided")
        object.__setattr__(self, "endpoint_a", _freeze(self.endpoint_a) if self.endpoint_a is not None else None)
        object.__setattr__(self, "endpoint_b", _freeze(self.endpoint_b) if self.endpoint_b is not None else None)
        object.__setattr__(self, "route_points", tuple((float(x), float(y)) for x, y in self.route_points))
        object.__setattr__(
            self, "placement",
            None if self.placement is None else (float(self.placement[0]), float(self.placement[1])),
        )
        object.__setattr__(self, "terminal_contract", tuple(str(value) for value in self.terminal_contract))
        object.__setattr__(self, "endpoint_references", _freeze(self.endpoint_references))
        object.__setattr__(self, "validation_state", _freeze(self.validation_state))
        object.__setattr__(self, "attributes", _freeze(self.attributes))
        if self.configuration_assessment is not None and not isinstance(
            self.configuration_assessment, EquipmentConfigurationAssessmentReadModel
        ):
            raise TypeError("ProjectionState.configuration_assessment must be an Application configuration assessment or None.")

    @classmethod
    def empty(cls, object_id: str, display_type: str) -> "ProjectionState":
        """Create a deterministic empty presentation state."""
        return cls(object_id=object_id, display_type=display_type)


__all__ = ["EngineeringParameterState", "ProjectionState"]
