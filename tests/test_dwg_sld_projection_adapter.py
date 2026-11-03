from __future__ import annotations

import pytest

from core.application.sld_export_snapshot import SLDExportSnapshot
from plugins.dwg_export import DrawingEntity, SLDProjectionDrawingSnapshotAdapter, SymbolProfile


def _snapshot() -> SLDExportSnapshot:
    return SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-42", nodes=(), connections=(),
        coordinate_system="sld-document-local", units="sld-unit",
    )


def test_application_adapter_accepts_only_detached_sld_snapshot() -> None:
    adapter = SLDProjectionDrawingSnapshotAdapter()
    snapshot = adapter.capture(
        _snapshot(), symbol_profile=SymbolProfile.IEC_60617,
        entity_builder=lambda value, profile: (),
    )
    assert snapshot.project_id == "project-1"
    assert snapshot.source_kind == "sld_export_snapshot"
    assert snapshot.source_id == "document-1"
    assert snapshot.source_revision == "revision-42"
    assert snapshot.symbol_profile is SymbolProfile.IEC_60617
    assert snapshot.units == "sld-unit"
    assert snapshot.coordinate_system == "sld-document-local"


def test_application_adapter_rejects_ui_projection_objects() -> None:
    with pytest.raises(TypeError, match="SLDExportSnapshot"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            object(), entity_builder=lambda value, profile: (),
        )


def test_application_adapter_rejects_non_drawing_entity_builder_output() -> None:
    with pytest.raises(TypeError, match="DrawingEntity"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            _snapshot(), entity_builder=lambda value, profile: (object(),),
        )


def test_snapshot_capture_is_separate_ui_lifecycle_boundary() -> None:
    from types import SimpleNamespace

    from ui.canvas.sld_canvas_projection import SLDCanvasProjection
    from ui.canvas.sld_export_snapshot_adapter import SLDExportSnapshotCapture
    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    active = SLDDocument("document-1", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    application = SimpleNamespace(
        revision="revision-42",
        project=SimpleNamespace(project_id="project-1"),
        presentation=active,
    )
    captured = SLDExportSnapshotCapture().capture(
        application, manager, SLDCanvasProjection(),
        project_id="project-1", document_id="document-1",
    )
    assert captured.source_revision == "revision-42"
    assert captured.document_id == "document-1"
    assert captured.units == "sld-unit"
