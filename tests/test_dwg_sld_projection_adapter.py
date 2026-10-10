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
