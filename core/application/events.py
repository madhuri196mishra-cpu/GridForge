# ============================================================
# File: core/application/events.py
# GridForge V2 — Headless Application Events
# Author: Subhendu Mishra
# ============================================================
"""Immutable semantic events emitted by the GridForge Application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("Event payload must be a mapping.")

    def freeze(item: Any) -> Any:
        if isinstance(item, Mapping):
            return MappingProxyType({key: freeze(val) for key, val in item.items()})
        if isinstance(item, list):
            return tuple(freeze(element) for element in item)
        if isinstance(item, set):
            return frozenset(freeze(element) for element in item)
        if isinstance(item, tuple):
            return tuple(freeze(element) for element in item)
        return item

    return MappingProxyType({key: freeze(item) for key, item in value.items()})


@dataclass(frozen=True)
class ApplicationEvent:
    """Base immutable, headless Application event."""

    event_type: str
    payload: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("ApplicationEvent event_type must be a non-empty string.")
        object.__setattr__(self, "payload", _immutable_mapping(self.payload))
        if not isinstance(self.event_id, UUID):
            raise TypeError("ApplicationEvent event_id must be a UUID.")
        if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None:
            raise ValueError("ApplicationEvent occurred_at must be timezone-aware.")


@dataclass(frozen=True)
class ElementCreated(ApplicationEvent):
    def __init__(self, *, element_id: str, element_type: str,
                 correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"element_id": element_id, "element_type": element_type}
        if metadata: payload.update(metadata)
        super().__init__("element.created", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class ElementRemoved(ApplicationEvent):
    def __init__(self, *, element_id: str, element_type: str,
                 correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"element_id": element_id, "element_type": element_type}
        if metadata: payload.update(metadata)
        super().__init__("element.removed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class ElementUpdated(ApplicationEvent):
    def __init__(self, *, element_id: str, element_type: str,
                 changes: Mapping[str, Any] | None = None,
                 correlation_id: UUID | None = None,
                 causation_id: UUID | None = None) -> None:
        super().__init__(
            "element.updated",
            {"element_id": element_id, "element_type": element_type, "changes": dict(changes or {})},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


@dataclass(frozen=True)
class TopologyChanged(ApplicationEvent):
    def __init__(self, *, operation: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"operation": operation}
        if metadata: payload.update(metadata)
        super().__init__("topology.changed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class NetworkChanged(ApplicationEvent):
    def __init__(self, *, operation: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"operation": operation}
        if metadata: payload.update(metadata)
        super().__init__("network.changed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class JunctionCreated(ApplicationEvent):
    """Semantic fact that a topology Junction was created."""
    def __init__(self, *, junction_id: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None, metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"junction_id": junction_id, "junction_kind": "topology"}
        if metadata: payload.update(metadata)
        super().__init__("junction.created", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class JunctionRemoved(ApplicationEvent):
    """Semantic fact that a topology Junction was removed."""
    def __init__(self, *, junction_id: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None, metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"junction_id": junction_id, "junction_kind": "topology"}
        if metadata: payload.update(metadata)
        super().__init__("junction.removed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class SimpleWireConnectionCreated(ApplicationEvent):
    """Semantic fact that an authoritative Simple Wire was created."""

    def __init__(self, *, connection_id: str, endpoint_a: Mapping[str, Any], endpoint_b: Mapping[str, Any],
                 correlation_id: UUID | None = None, causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"connection_id": connection_id, "endpoint_a": dict(endpoint_a), "endpoint_b": dict(endpoint_b)}
        if metadata: payload.update(metadata)
        super().__init__("simple_wire.created", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class SimpleWireConnectionRemoved(ApplicationEvent):
    """Semantic fact that an authoritative Simple Wire was removed."""

    def __init__(self, *, connection_id: str, endpoint_a: Mapping[str, Any], endpoint_b: Mapping[str, Any],
                 correlation_id: UUID | None = None, causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"connection_id": connection_id, "endpoint_a": dict(endpoint_a), "endpoint_b": dict(endpoint_b)}
        if metadata: payload.update(metadata)
        super().__init__("simple_wire.removed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class ProtectionChanged(ApplicationEvent):
    """Semantic fact that Application-owned protection read state changed."""

    def __init__(self, *, operation: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"operation": operation}
        if metadata: payload.update(metadata)
        super().__init__("protection.changed", payload, correlation_id=correlation_id, causation_id=causation_id)


class SLDPresentationChanged(ApplicationEvent):
    """Semantic fact that persistent SLD presentation state changed."""

    def __init__(self, *, operation: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"operation": operation}
        if metadata: payload.update(metadata)
        super().__init__("sld.presentation.changed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class OperationCompleted(ApplicationEvent):
    def __init__(self, *, operation: str, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None,
                 metadata: Mapping[str, Any] | None = None) -> None:
        payload = {"operation": operation}
        if metadata: payload.update(metadata)
        super().__init__("operation.completed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class ProtectionTripRequested(ApplicationEvent):
    """Semantic Application event for an actionable protection trip request."""

    @classmethod
    def from_decision(cls, decision: Any, *, breaker_id: str,
                      correlation_id: UUID | None = None,
                      causation_id: UUID | None = None) -> "ProtectionTripRequested":
        from core.protection.decision import ProtectionDecision
        if not isinstance(decision, ProtectionDecision):
            raise TypeError("decision must be a ProtectionDecision.")
        if not isinstance(breaker_id, str) or not breaker_id.strip():
            raise ValueError("breaker_id must be a non-empty string.")
        return cls(
            "protection.trip.requested",
            {
                "relay_id": decision.relay_id,
                "element_id": decision.element_id,
                "function_code": decision.function_code,
                "breaker_id": breaker_id.strip(),
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


class _OperationEvent(ApplicationEvent):
    """Base for lifecycle/study semantic events with stable operation payload."""
    _EVENT_TYPE = ""

    def __init__(self, *, metadata: Mapping[str, Any] | None = None,
                 correlation_id: UUID | None = None,
                 causation_id: UUID | None = None) -> None:
        super().__init__(self._EVENT_TYPE, dict(metadata or {}),
                         correlation_id=correlation_id, causation_id=causation_id)


class ProjectLoaded(_OperationEvent):
    """Application project activation has committed.

    This event does not assert that an engineer-visible UI workspace/SLD is
    ready. UI readiness is a separate presentation transaction signalled by
    the workspace adapter after its activation callback commits.
    """

    _EVENT_TYPE = "project.loaded"


class ProjectSaved(_OperationEvent):
    _EVENT_TYPE = "project.saved"


class ProjectClosed(_OperationEvent):
    _EVENT_TYPE = "project.closed"


class StudyCaseCreated(_OperationEvent):
    """A structured, runnable Study Case was registered in Application scope."""
    _EVENT_TYPE = "study.case_created"


class StudyStarted(_OperationEvent):
    _EVENT_TYPE = "study.started"


class StudyCompleted(_OperationEvent):
    _EVENT_TYPE = "study.completed"


class StudyFailed(_OperationEvent):
    _EVENT_TYPE = "study.failed"


class StudyCancelled(_OperationEvent):
    _EVENT_TYPE = "study.cancelled"


class ValidationChanged(_OperationEvent):
    _EVENT_TYPE = "validation.changed"


_CONTROL_EVENT_NAMES = frozenset({
    "ControlComponentCreated", "ControlComponentUpdated", "ControlComponentRemoved",
    "ControlConnectionCreated", "ControlConnectionRemoved",
    "ControlDependencyCreated", "ControlDependencyRemoved", "ControlProgramChanged",
    "ControlStateChanged", "ControlExecutionStarted", "ControlExecutionCompleted",
    "ControlExecutionFailed",
})


def __getattr__(name: str) -> Any:
    """Lazily expose control events without creating an import cycle."""
    if name not in _CONTROL_EVENT_NAMES:
        raise AttributeError(name)
    from . import control_events
    value = getattr(control_events, name)
    globals()[name] = value
    return value


__all__ = [
    "ApplicationEvent", "ElementCreated", "ElementRemoved", "ElementUpdated",
    "TopologyChanged", "NetworkChanged", "SimpleWireConnectionCreated", "SimpleWireConnectionRemoved", "ProtectionChanged", "SLDPresentationChanged", "OperationCompleted",
    "ProtectionTripRequested",
    "ProjectLoaded", "ProjectSaved", "ProjectClosed", "DraftChanged",
    "StudyCaseCreated", "StudyStarted", "StudyCompleted", "StudyFailed", "StudyCancelled", "ValidationChanged",
    "ControlComponentCreated", "ControlComponentUpdated", "ControlComponentRemoved",
    "ControlConnectionCreated", "ControlConnectionRemoved", "ControlDependencyCreated", "ControlDependencyRemoved",
    "ControlProgramChanged", "ControlStateChanged", "ControlExecutionStarted", "ControlExecutionCompleted",
    "ControlExecutionFailed",
]


@dataclass(frozen=True)
class DraftChanged(ApplicationEvent):
    """Semantic fact that Application-owned DraftNetwork authoring changed.

    This event never asserts a Core electrical mutation or topology change.
    """
    def __init__(self, *, operation: str, metadata: Mapping[str, Any] | None = None,
                 correlation_id: UUID | None = None, causation_id: UUID | None = None) -> None:
        payload = {"operation": operation}
        if metadata:
            payload.update(metadata)
        super().__init__("draft.changed", payload, correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class NetworkCommitted(ApplicationEvent):
    """Semantic completion boundary for one aggregate DraftNetwork commit."""
    def __init__(self, *, metadata: Mapping[str, Any] | None = None, correlation_id: UUID | None = None, causation_id: UUID | None = None) -> None:
        super().__init__("network.committed", dict(metadata or {}), correlation_id=correlation_id, causation_id=causation_id)



@dataclass(frozen=True)
class InstrumentationWiringChanged(ApplicationEvent):
    """Canonical project instrumentation wiring inventory changed."""

    def __init__(self, *, operation: str, metadata: Mapping[str, Any] | None = None,
                 correlation_id: UUID | None = None, causation_id: UUID | None = None) -> None:
        payload = {"operation": operation, "inventory_kind": "instrumentation_wiring"}
        if metadata:
            payload.update(metadata)
        super().__init__("instrumentation.wiring_changed", payload,
                         correlation_id=correlation_id, causation_id=causation_id)


@dataclass(frozen=True)
class TemperatureInstrumentChanged(ApplicationEvent):
    """Canonical BTD/OTD/WTD inventory changed."""

    def __init__(self, *, operation: str, metadata: Mapping[str, Any] | None = None,
                 correlation_id: UUID | None = None, causation_id: UUID | None = None) -> None:
        payload = {"operation": operation, "inventory_kind": "temperature_instruments"}
        if metadata:
            payload.update(metadata)
        super().__init__("temperature_instrument.changed", payload,
                         correlation_id=correlation_id, causation_id=causation_id)


__all__ += ["InstrumentationWiringChanged", "TemperatureInstrumentChanged"]
