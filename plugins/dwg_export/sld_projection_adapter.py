"""Plugin-side conversion from detached SLD snapshots to world/mm export snapshots.

No coordinate scale, origin, or orientation is inferred. Callers must supply
an explicitly reviewed mapping that matches the snapshot's declared source.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from core.application.sld_export_snapshot import SLDExportSnapshot

from .coordinate_transform import SLDCoordinateTransform
from .drawing_plan import DrawingEntity, SymbolProfile
from .source_snapshot import ApplicationDrawingSnapshot


class SLDProjectionDrawingSnapshotAdapter:
    """Convert an immutable SLD snapshot using an explicit reviewed mapping."""

    def capture(
        self,
        snapshot: SLDExportSnapshot,
        *,
        coordinate_transform: SLDCoordinateTransform,
        symbol_profile: SymbolProfile = SymbolProfile.ANSI_IEEE,
        entity_builder: Callable[[SLDExportSnapshot, SymbolProfile], tuple[DrawingEntity, ...]],
    ) -> ApplicationDrawingSnapshot:
        if not isinstance(snapshot, SLDExportSnapshot):
            raise TypeError("snapshot must be an SLDExportSnapshot")
        if not isinstance(coordinate_transform, SLDCoordinateTransform):
            raise TypeError("coordinate_transform must be an explicitly reviewed SLDCoordinateTransform")
        if (
            snapshot.coordinate_system != coordinate_transform.source_coordinate_system
            or snapshot.units != coordinate_transform.source_units
        ):
            raise ValueError(
                "coordinate transform source declaration does not match the captured SLD snapshot"
            )
        if not callable(entity_builder):
            raise TypeError("entity_builder must be callable")
        profile = symbol_profile if isinstance(symbol_profile, SymbolProfile) else SymbolProfile(symbol_profile)
        source_entities = tuple(entity_builder(snapshot, profile=profile))
        if any(not isinstance(entity, DrawingEntity) for entity in source_entities):
            raise TypeError("entity_builder must return only DrawingEntity values")
        entities = coordinate_transform.transform_entities(source_entities)
        return ApplicationDrawingSnapshot(
            project_id=snapshot.project_id,
            export_id=str(uuid4()),
            source_revision=snapshot.source_revision,
            source_kind="sld_export_snapshot",
            source_id=snapshot.document_id,
            symbol_profile=profile,
            drawing_types=("sld",),
            entities=entities,
            units="mm",
            coordinate_system="world",
        )


__all__ = ["SLDProjectionDrawingSnapshotAdapter"]
