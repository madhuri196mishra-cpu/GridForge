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

    def capture_from_document_lifecycle(
        self,
        application: object,
        document_manager: object,
        projection_factory: object,
        *,
        project_id: str,
        document_id: str,
        model: object,
        entity_builder: Callable[[SLDCanvasSnapshot], tuple[DrawingEntity, ...]],
        drawing_types: tuple[str, ...] = ("sld",),
        symbol_profile: SymbolProfile = SymbolProfile.ANSI_IEEE,
    ) -> ApplicationDrawingSnapshot:
        """Capture an SLD projection only while its owning lifecycle identity and revision remain stable.

        The active document registry is the identity authority; Application.revision
        is the project revision authority. This is a fail-closed before/after guard,
        not a lock: callers must still invoke it on the lifecycle's serialized
        execution thread to prevent concurrent document mutation.
        """
        from ui.workspace.document_manager import DocumentManager
        from ui.workspace.document import Document
        from ui.sld.sld_model import SLDModel
        from ui.canvas.sld_canvas_projection import SLDCanvasProjection

        if not hasattr(application, "revision") or not hasattr(application, "project"):
            raise TypeError("application must expose revision and project context")
        if not isinstance(document_manager, DocumentManager):
            raise TypeError("document_manager must be a DocumentManager")
        if not isinstance(projection_factory, SLDCanvasProjection):
            raise TypeError("projection_factory must be an SLDCanvasProjection")
        if not isinstance(model, SLDModel):
            raise TypeError("model must be the active document's SLDModel")
        active = document_manager.active_document
        if not isinstance(active, Document) or active.document_id != document_id:
            raise RuntimeError("requested document is not the DocumentManager active document")
        if active.document_type.lower() not in {"sld", "single_line_diagram", "single-line-diagram"}:
            raise RuntimeError("active document is not an SLD document")
        if active.project_id is not None and active.project_id != project_id:
            raise RuntimeError("active document belongs to a different project")
        context = application.project
        if context is None or context.project_id != project_id:
            raise RuntimeError("project_id does not match the active Application project")

        revision_before = str(application.revision)
        active_id_before = document_manager.active_document_id
        projection = projection_factory.project(model)
        snapshot = self.capture(
            projection,
            project_id=project_id,
            document_id=document_id,
            source_revision=revision_before,
            drawing_types=drawing_types,
            symbol_profile=symbol_profile,
            entity_builder=entity_builder,
        )
        revision_after = str(application.revision)
        if revision_after != revision_before:
            raise RuntimeError("Application revision changed during SLD projection capture")
        if document_manager.active_document_id != active_id_before:
            raise RuntimeError("active document changed during SLD projection capture")
        if document_manager.get(document_id) is not active:
            raise RuntimeError("active document registration changed during SLD projection capture")
        return snapshot


__all__ = ["SLDProjectionDrawingSnapshotAdapter"]
