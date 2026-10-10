"""Application-boundary snapshots and plan assembly for CAD export.

Adapters may construct these values from authoritative Application read models
and approved presentation projections. This module intentionally accepts no Core
model objects, Qt objects, canvases, or renderer instances.
"""
from __future__ import annotations

from dataclasses import dataclass

from .drawing_plan import DrawingEntity, DrawingPlan, SymbolProfile
from .validation import validate_drawing_plan


@dataclass(frozen=True, slots=True)
class ApplicationDrawingSnapshot:
    """Detached export input captured from one consistent Application revision."""

    project_id: str
    export_id: str
    source_revision: str
    source_kind: str
    source_id: str
    symbol_profile: SymbolProfile
    drawing_types: tuple[str, ...]
    entities: tuple[DrawingEntity, ...]
    units: str = "mm"

    def __post_init__(self) -> None:
        for field_name in ("project_id", "export_id", "source_revision", "source_kind", "source_id", "units"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        try:
            profile = self.symbol_profile if isinstance(self.symbol_profile, SymbolProfile) else SymbolProfile(self.symbol_profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("symbol_profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "symbol_profile", profile)
        drawing_types = tuple(self.drawing_types)
        entities = tuple(self.entities)
        if not drawing_types or any(not isinstance(item, str) or not item.strip() for item in drawing_types):
            raise ValueError("drawing_types must contain non-empty names")
        if any(not isinstance(item, DrawingEntity) for item in entities):
            raise TypeError("snapshot entities must be immutable DrawingEntity values")
        object.__setattr__(self, "drawing_types", tuple(item.strip() for item in drawing_types))
        object.__setattr__(self, "entities", entities)


class DrawingPlanFactory:
    """Assemble and validate a DrawingPlan from a detached Application snapshot."""

    @staticmethod
    def from_snapshot(snapshot: ApplicationDrawingSnapshot) -> DrawingPlan:
        if not isinstance(snapshot, ApplicationDrawingSnapshot):
            raise TypeError("snapshot must be an ApplicationDrawingSnapshot")
        plan = DrawingPlan(
            project_id=snapshot.project_id,
            export_id=snapshot.export_id,
            source_revision=snapshot.source_revision,
            symbol_profile=snapshot.symbol_profile,
            drawing_types=snapshot.drawing_types,
            entities=snapshot.entities,
            units=snapshot.units,
        )
        validate_drawing_plan(plan)
        return plan
