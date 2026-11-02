from __future__ import annotations

import pytest

from core.application.dwg_export import DrawingEntity, SLDProjectionDrawingSnapshotAdapter, SymbolProfile
from ui.canvas.sld_canvas_projection import SLDCanvasProjection
from ui.sld.sld_model import SLDModel


def test_sld_projection_adapter_captures_approved_renderer_neutral_projection() -> None:
    model = SLDModel()
    projection = SLDCanvasProjection().project(model)
    adapter = SLDProjectionDrawingSnapshotAdapter()

    snapshot = adapter.capture(
        projection,
        project_id="project-1",
        document_id="document-1",
        source_revision="revision-42",
        symbol_profile=SymbolProfile.IEC_60617,
        entity_builder=lambda value: (),
    )

    assert snapshot.project_id == "project-1"
    assert snapshot.source_kind == "sld_canvas_projection"
    assert snapshot.source_id == "document-1"
    assert snapshot.source_revision == "revision-42"
    assert snapshot.symbol_profile is SymbolProfile.IEC_60617
    assert snapshot.entities == ()


def test_sld_projection_adapter_rejects_non_projection_and_core_like_objects() -> None:
    adapter = SLDProjectionDrawingSnapshotAdapter()
    with pytest.raises(TypeError, match="SLDCanvasSnapshot"):
        adapter.capture(
            object(),
            project_id="project-1",
            document_id="document-1",
            source_revision="revision-42",
            entity_builder=lambda value: (),
        )


def test_sld_projection_adapter_rejects_builder_outputs_that_are_not_drawing_entities() -> None:
    projection = SLDCanvasProjection().project(SLDModel())
    with pytest.raises(TypeError, match="DrawingEntity"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            projection,
            project_id="project-1",
            document_id="document-1",
            source_revision="revision-42",
            entity_builder=lambda value: (object(),),
        )


def test_lifecycle_capture_requires_model_owned_by_active_sld_document() -> None:
    from types import SimpleNamespace

    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    active = SLDDocument("document-1", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    application = SimpleNamespace(
        revision="revision-1",
        project=SimpleNamespace(project_id="project-1"),
        presentation=active,
    )
    adapter = SLDProjectionDrawingSnapshotAdapter()

    with pytest.raises(RuntimeError, match="not owned by the active SLDDocument"):
        adapter.capture_from_document_lifecycle(
            application,
            manager,
            SLDCanvasProjection(),
            project_id="project-1",
            document_id="document-1",
            model=SLDModel(),
            entity_builder=lambda value: (),
        )


def test_lifecycle_capture_requires_application_and_workspace_active_document_identity() -> None:
    from types import SimpleNamespace

    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    active = SLDDocument("document-1", project_id="project-1")
    other = SLDDocument("document-2", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    application = SimpleNamespace(
        revision="revision-1",
        project=SimpleNamespace(project_id="project-1"),
        presentation=other,
    )

    with pytest.raises(RuntimeError, match="not the Application presentation"):
        SLDProjectionDrawingSnapshotAdapter().capture_from_document_lifecycle(
            application,
            manager,
            SLDCanvasProjection(),
            project_id="project-1",
            document_id="document-1",
            model=active.model,
            entity_builder=lambda value: (),
        )


def test_lifecycle_capture_rejects_sld_document_mutation_during_entity_build() -> None:
    from types import SimpleNamespace

    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    active = SLDDocument("document-1", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    application = SimpleNamespace(
        revision="revision-1",
        project=SimpleNamespace(project_id="project-1"),
        presentation=active,
    )

    def mutate_document(_projection):
        active.name = "Changed during export"
        return ()

    with pytest.raises(RuntimeError, match="SLD document content changed"):
        SLDProjectionDrawingSnapshotAdapter().capture_from_document_lifecycle(
            application,
            manager,
            SLDCanvasProjection(),
            project_id="project-1",
            document_id="document-1",
            model=active.model,
            entity_builder=mutate_document,
        )
