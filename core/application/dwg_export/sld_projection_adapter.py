"""Application-side conversion from detached SLD snapshots to export snapshots.

This module intentionally imports no UI, document manager, canvas, Qt, or CAD
backend implementation. Capture and lifecycle coordination belong to the UI
composition boundary; this adapter only accepts the detached contract.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from core.application.sld_export_snapshot import SLDExportSnapshot

from .drawing_plan import DrawingEntity, SymbolProfile
from .source_snapshot import ApplicationDrawingSnapshot


class SLDProjectionDrawingSnapshotAdapter:
    """Convert an immutable SLD export snapshot into the common Application DTO."""

    def capture(
        self,
        snapshot: SLDExportSnapshot,
        *,
        symbol_profile: SymbolProfile = SymbolProfile.ANSI_IEEE,
        entity_builder: Callable[[SLDExportSnapshot, SymbolProfile], tuple[DrawingEntity, ...]],
    ) -> ApplicationDrawingSnapshot:
        if not isinstance(snapshot, SLDExportSnapshot):
            raise TypeError("snapshot must be an SLDExportSnapshot")
        if not callable(entity_builder):
            raise TypeError("entity_builder must be callable")
        profile = symbol_profile if isinstance(symbol_profile, SymbolProfile) else SymbolProfile(symbol_profile)
        entities = tuple(entity_builder(snapshot, profile))
        if any(not isinstance(entity, DrawingEntity) for entity in entities):
            raise TypeError("entity_builder must return only DrawingEntity values")
        return ApplicationDrawingSnapshot(
            project_id=snapshot.project_id,
            export_id=str(uuid4()),
            source_revision=snapshot.source_revision,
            source_kind="sld_export_snapshot",
            source_id=snapshot.document_id,
            symbol_profile=profile,
            drawing_types=("sld",),
            entities=entities,
            units=snapshot.units,
        )


__all__ = ["SLDProjectionDrawingSnapshotAdapter"]
