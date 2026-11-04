from __future__ import annotations

import pytest

from core.application.sld_export_snapshot import (
    SLDAnnotationExportSnapshot, SLDExportSnapshot, SLDNodeExportSnapshot,
    SLDSymbolPlacementSnapshot,
)
from plugins.dwg_export import (
    DrawingEntity, SLDCoordinateTransform, SLDProjectionDrawingSnapshotAdapter,
    SymbolProfile,
)


def _snapshot(*, units="sld-unit") -> SLDExportSnapshot:
    return SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-42", nodes=(), connections=(),
        coordinate_system="sld-document-local", units=units,
    )


def _mapping(*, units="sld-unit", scale=2.0) -> SLDCoordinateTransform:
    return SLDCoordinateTransform(
        mapping_id="site-grid-01", mapping_version="1.0.0",
        source_coordinate_system="sld-document-local", source_units=units,
        millimetres_per_source_unit=scale,
        world_origin_x_mm=1000.0, world_origin_y_mm=2000.0,
        rotation_degrees=90.0, reviewer="engineering-review",
        reviewed_on="2026-11-03", review_reference="review:DWG-COORD-001",
    )


def test_application_adapter_requires_reviewed_mapping_and_emits_world_mm() -> None:
    adapter = SLDProjectionDrawingSnapshotAdapter()
    snapshot = adapter.capture(
        _snapshot(), coordinate_transform=_mapping(),
        symbol_profile=SymbolProfile.IEC_60617,
        entity_builder=lambda value, profile: (),
    )
    assert snapshot.project_id == "project-1"
    assert snapshot.source_kind == "sld_export_snapshot"
    assert snapshot.source_id == "document-1"
    assert snapshot.source_revision == "revision-42"
    assert snapshot.symbol_profile is SymbolProfile.IEC_60617
    assert snapshot.units == "mm"
    assert snapshot.coordinate_system == "world"


def test_adapter_rejects_ui_projection_objects() -> None:
    with pytest.raises(TypeError, match="SLDExportSnapshot"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            object(), coordinate_transform=_mapping(),
            entity_builder=lambda value, profile: (),
        )


def test_adapter_rejects_non_drawing_entity_builder_output() -> None:
    with pytest.raises(TypeError, match="DrawingEntity"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            _snapshot(), coordinate_transform=_mapping(),
            entity_builder=lambda value, profile: (object(),),
        )


def test_adapter_rejects_mapping_for_different_source_units() -> None:
    with pytest.raises(ValueError, match="does not match"):
        SLDProjectionDrawingSnapshotAdapter().capture(
            _snapshot(units="inch"), coordinate_transform=_mapping(),
            entity_builder=lambda value, profile: (),
        )


def test_adapter_transforms_all_entity_geometry_and_records_review_provenance() -> None:
    source = (
        DrawingEntity("line", "LINE", "WIRE", {"start": (1, 0), "end": (2, 0)}, {}),
        DrawingEntity("symbol", "INSERT", "SYMBOL", {
            "symbol_id": "breaker", "insert": (1, 0), "scale": 1.5, "rotation": 0,
        }, {}),
        DrawingEntity("wire", "LWPOLYLINE", "WIRE", {
            "points": ((0, 0), (1, 0), (1, 1)), "closed": False,
        }, {}),
        DrawingEntity("label", "TEXT", "TEXT", {
            "insert": (0, 1), "text": "Bus A", "height": 2.5, "rotation": 0,
        }, {}),
        DrawingEntity("circle", "CIRCLE", "SYMBOL", {"center": (1, 1), "radius": 3}, {}),
        DrawingEntity("arc", "ARC", "SYMBOL", {
            "center": (1, 1), "radius": 3, "start_angle": 0, "end_angle": 90,
        }, {}),
    )
    result = SLDProjectionDrawingSnapshotAdapter().capture(
        _snapshot(), coordinate_transform=_mapping(),
        entity_builder=lambda value, profile: source,
    )
    by_id = {entity.entity_id: entity for entity in result.entities}
    assert by_id["line"].geometry["start"] == pytest.approx((1000.0, 2002.0))
    assert by_id["symbol"].geometry["insert"] == pytest.approx((1000.0, 2002.0))
    assert by_id["symbol"].geometry["scale"] == pytest.approx(3.0)
    assert by_id["symbol"].geometry["rotation"] == pytest.approx(90.0)
    assert by_id["wire"].geometry["points"][1] == pytest.approx((1000.0, 2002.0))
    assert by_id["label"].geometry["height"] == pytest.approx(5.0)
    assert by_id["circle"].geometry["radius"] == pytest.approx(6.0)
    assert by_id["arc"].geometry["start_angle"] == pytest.approx(90.0)
    evidence = by_id["symbol"].metadata["coordinate_transform"]
    assert evidence["review_reference"] == "review:DWG-COORD-001"
    assert evidence["target_units"] == "mm"


def test_transform_rejects_invalid_scale_and_missing_review_evidence() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        _mapping(scale=0)
    with pytest.raises(ValueError, match="review_reference"):
        SLDCoordinateTransform(
            mapping_id="map", mapping_version="1", source_coordinate_system="sld-document-local",
            source_units="sld-unit", millimetres_per_source_unit=1,
            world_origin_x_mm=0, world_origin_y_mm=0, rotation_degrees=0,
            reviewer="reviewer", reviewed_on="2026-11-03", review_reference=" ",
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
    context = SimpleNamespace(project_id="project-1")
    lifecycle = SimpleNamespace(
        context=context,
        activation_generation=3,
    )
    application = SimpleNamespace(
        revision="revision-42",
        project_lifecycle=lifecycle,
        presentation=active,
    )
    captured = SLDExportSnapshotCapture().capture(
        application, manager, SLDCanvasProjection(),
        project_id="project-1", document_id="document-1",
    )
    assert captured.source_revision == "revision-42"
    assert captured.document_id == "document-1"
    assert captured.units == "sld-unit"



def test_snapshot_capture_rejects_project_activation_during_capture() -> None:
    from types import SimpleNamespace

    from ui.canvas.sld_canvas_projection import SLDCanvasProjection
    from ui.canvas.sld_export_snapshot_adapter import SLDExportSnapshotCapture
    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    active = SLDDocument("document-1", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    context = SimpleNamespace(project_id="project-1")
    lifecycle = SimpleNamespace(context=context, activation_generation=3)
    application = SimpleNamespace(
        revision="revision-42",
        project_lifecycle=lifecycle,
        presentation=active,
    )

    class ActivatingProjection(SLDCanvasProjection):
        def project(self, model, annotation_blocks=()):
            lifecycle.activation_generation += 1
            return super().project(model, annotation_blocks)

    with pytest.raises(RuntimeError, match="project identity or activation changed"):
        SLDExportSnapshotCapture().capture(
            application, manager, ActivatingProjection(),
            project_id="project-1", document_id="document-1",
        )



def test_snapshot_capture_detaches_typed_transformer_read_values() -> None:
    from dataclasses import dataclass
    from enum import Enum
    from types import SimpleNamespace

    from ui.canvas.sld_canvas_projection import SLDCanvasProjection
    from ui.canvas.sld_export_snapshot_adapter import SLDExportSnapshotCapture
    from ui.sld.sld_document import SLDDocument
    from ui.workspace.document_manager import DocumentManager

    class Winding(str, Enum):
        DELTA = "DELTA"

    @dataclass(frozen=True)
    class TestEvidence:
        reference: str
        measured_impedance: complex

    attributes = {
        "transformer_configuration_summary": {
            "hv_winding_connection": Winding.DELTA,
            "zero_sequence_multiport_evidence": TestEvidence(
                reference="test:multiport", measured_impedance=1.5 + 2.25j
            ),
            "phase_domain_terminal_admittance_siemens": (
                (1.0 + 2.0j, 0.0j),
                (0.0j, 3.0 - 4.0j),
            ),
        }
    }
    projected_node = SimpleNamespace(
        node_id="node-transformer",
        presentation=None,
        read_model=SimpleNamespace(attributes=attributes, engineering_parameters=()),
        x=10.0,
        y=20.0,
        properties={},
    )

    class TransformerProjection(SLDCanvasProjection):
        def project(self, model, annotation_blocks=()):
            return SimpleNamespace(
                nodes=(projected_node,), connections=(), annotations=()
            )

    active = SLDDocument("document-1", project_id="project-1")
    manager = DocumentManager()
    manager.register(active)
    context = SimpleNamespace(project_id="project-1")
    lifecycle = SimpleNamespace(context=context, activation_generation=3)
    application = SimpleNamespace(
        revision="revision-42",
        project_lifecycle=lifecycle,
        presentation=active,
    )

    captured = SLDExportSnapshotCapture().capture(
        application, manager, TransformerProjection(),
        project_id="project-1", document_id="document-1",
    )
    summary = captured.nodes[0].engineering_attributes[
        "transformer_configuration_summary"
    ]
    assert summary["hv_winding_connection"] == "DELTA"
    assert summary["zero_sequence_multiport_evidence"] == {
        "reference": "test:multiport",
        "measured_impedance": {"real": 1.5, "imag": 2.25},
    }
    assert summary["phase_domain_terminal_admittance_siemens"] == (
        ({"real": 1.0, "imag": 2.0}, {"real": 0.0, "imag": 0.0}),
        ({"real": 0.0, "imag": 0.0}, {"real": 3.0, "imag": -4.0}),
    )



def test_adapter_composes_directly_with_sld_node_entity_builder() -> None:
    from core.application.sld_export_snapshot import (
        SLDNodeExportSnapshot, SLDSymbolPlacementSnapshot,
    )
    from plugins.dwg_export import (
        SLDNodeEntityBuilder, SymbolDefinition, SymbolRegistry,
    )

    definition = SymbolDefinition(
        symbol_id="breaker",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(
            DrawingEntity(
                "body", "LINE", "SYMBOL",
                {"start": (-1, 0), "end": (1, 0)}, {},
            ),
        ),
        review_status="reviewed",
        reviewer="engineering-review",
        reviewed_on="2026-11-03",
        review_reference="review:DWG-SYMBOL-001",
        standard_reference="standard-reference:IEEE-reviewed-source",
    )
    registry = SymbolRegistry((definition,))
    node = SLDNodeExportSnapshot(
        node_id="node-1",
        equipment_id="equipment-1",
        x=1.0,
        y=2.0,
        properties={},
        symbol=SLDSymbolPlacementSnapshot(
            symbol_instance_id="instance-1",
            symbol_definition_id="breaker",
            representation_id="symbol",
            visible=True,
            scale=1.5,
            rotation_degrees=0.0,
        ),
    )
    snapshot = SLDExportSnapshot(
        project_id="project-1",
        document_id="document-1",
        source_revision="revision-42",
        nodes=(node,),
        connections=(),
    )

    result = SLDProjectionDrawingSnapshotAdapter().capture(
        snapshot,
        coordinate_transform=_mapping(),
        entity_builder=SLDNodeEntityBuilder(registry).build,
    )

    assert len(result.entities) == 1
    assert result.entities[0].kind == "INSERT"
    assert result.entities[0].geometry["insert"] == pytest.approx((996.0, 2002.0))
    assert result.entities[0].geometry["scale"] == pytest.approx(3.0)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda value: SLDSymbolPlacementSnapshot(
            "instance", "definition", "representation", True, value, 0.0
        ), "scale"),
        (lambda value: SLDSymbolPlacementSnapshot(
            "instance", "definition", "representation", True, 1.0, value
        ), "rotation_degrees"),
        (lambda value: SLDNodeExportSnapshot(
            "node", None, value, 0.0, {}, None
        ), "node.x"),
        (lambda value: SLDNodeExportSnapshot(
            "node", None, 0.0, value, {}, None
        ), "node.y"),
        (lambda value: SLDAnnotationExportSnapshot(
            "annotation", "node", value, 0.0, "label", True
        ), "annotation.x"),
        (lambda value: SLDAnnotationExportSnapshot(
            "annotation", "node", 0.0, value, "label", True
        ), "annotation.y"),
    ],
)
def test_sld_snapshot_numeric_fields_reject_booleans(factory, field) -> None:
    with pytest.raises(TypeError, match="real number"):
        factory(True)
