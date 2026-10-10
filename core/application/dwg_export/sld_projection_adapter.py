'''Detached DWG snapshot adapter for the approved SLD canvas projection.

The projection is presentation-owned and renderer-neutral. This adapter never
constructs a canvas, reads Qt graphics, mutates the SLD document, or consults
Core topology. A caller supplies a stable document revision and an entity
builder that understands GridForge's persisted symbol/presentation vocabulary.
'''
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from ui.canvas.sld_canvas_projection import SLDCanvasSnapshot

from .drawing_plan import DrawingEntity, SymbolProfile
from .source_snapshot import ApplicationDrawingSnapshot


class SLDProjectionDrawingSnapshotAdapter:
    '''Detach an approved SLD projection into the common DWG snapshot contract.

    ``source_revision`` must come from the owning Application/document lifecycle;
    the projection itself does not carry a revision. ``entity_builder`` converts
    projected nodes/connections into CAD-neutral primitives without inferring
    electrical connectivity from layout coordinates.
    '''

    def capture(
        self,
        projection: SLDCanvasSnapshot,
        *,
        project_id: str,
        document_id: str,
        source_revision: str,
        drawing_types: tuple[str, ...] = ("sld",),
        symbol_profile: SymbolProfile = SymbolProfile.ANSI_IEEE,
        entity_builder: Callable[[SLDCanvasSnapshot], tuple[DrawingEntity, ...]],
    ) -> ApplicationDrawingSnapshot:
        if not isinstance(projection, SLDCanvasSnapshot):
            raise TypeError("projection must be an SLDCanvasSnapshot from SLDCanvasProjection")
        for name, value in (("project_id", project_id), ("document_id", document_id), ("source_revision", source_revision)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not callable(entity_builder):
            raise TypeError("entity_builder must be callable")

        # Projection DTOs are frozen, but node property mappings may be nested.
        # DrawingEntity performs the final detached/frozen conversion for output.
        entities = tuple(entity_builder(projection))
        if any(not isinstance(entity, DrawingEntity) for entity in entities):
            raise TypeError("entity_builder must return only DrawingEntity values")
        return ApplicationDrawingSnapshot(
            project_id=project_id.strip(),
            export_id=str(uuid4()),
            source_revision=source_revision.strip(),
            source_kind="sld_canvas_projection",
            source_id=document_id.strip(),
            symbol_profile=symbol_profile,
            drawing_types=tuple(drawing_types),
            entities=entities,
        )


__all__ = ["SLDProjectionDrawingSnapshotAdapter"]
