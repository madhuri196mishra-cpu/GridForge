"""Detached transient-study event state and mutation boundary."""

from __future__ import annotations

from dataclasses import dataclass

from core.analysis.transient_network import DetachedTransientNetworkState


@dataclass
class TransientEventState:
    """Mutable state owned exclusively by one transient study execution."""

    snapshot: DetachedTransientNetworkState
    breaker_states: dict[str, bool]
    equipment_states: dict[str, bool]
    active_fault: object | None = None
    topology_revision: int | None = None

    @classmethod
    def from_snapshot(cls, snapshot: DetachedTransientNetworkState) -> "TransientEventState":
        return cls(snapshot, dict(snapshot.breaker_states), dict(snapshot.equipment_states), snapshot.active_fault, snapshot.topology_revision)

    def set_breaker(self, breaker_id: str, closed: bool) -> None:
        self.breaker_states[str(breaker_id)] = bool(closed)
        self.equipment_states[str(breaker_id)] = bool(closed)
        self._advance_revision()

    def set_equipment(self, equipment_id: str, conducting: bool) -> None:
        self.equipment_states[str(equipment_id)] = bool(conducting)
        self._advance_revision()

    def apply_fault(self, fault: object) -> None:
        if self.active_fault is not None:
            raise ValueError("A transient fault is already active.")
        self.active_fault = fault
        self._advance_revision()

    def clear_fault(self) -> None:
        self.active_fault = None
        self._advance_revision()

    def is_conducting(self, equipment_id: str, default: bool = True) -> bool:
        return self.equipment_states.get(str(equipment_id), default)

    def detached_view(self) -> DetachedTransientNetworkState:
        return DetachedTransientNetworkState(
            bus_ids=self.snapshot.bus_ids,
            branches=self.snapshot.branches,
            transformers=self.snapshot.transformers,
            shunts=self.snapshot.shunts,
            breaker_states=dict(self.breaker_states),
            equipment_states=dict(self.equipment_states),
            active_fault=self.active_fault,
            topology_revision=self.topology_revision,
        )

    def _advance_revision(self) -> None:
        self.topology_revision = (self.topology_revision or 0) + 1


__all__ = ["TransientEventState"]
