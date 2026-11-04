from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.application.services.control_service import (
    ControlComponentReadModel,
    ControlConnectionReadModel,
    ControlProgramReadModel,
    LadderRungReadModel,
)
from plugins.dwg_export import (
    ControlCoordinateTransform,
    ControlDrawingEntityBuilder,
    ControlDrawingLayout,
    ControlProjectionDrawingSnapshotAdapter,
    DrawingEntity,
    DrawingPlanFactory,
    SymbolDefinition,
    SymbolProfile,
    SymbolRegistry,
    validate_drawing_plan,
)
from plugins.dwg_export.validation import DrawingPlanValidationError


def _transform(**overrides) -> ControlCoordinateTransform:
    values = {
        "mapping_id": "control-site-1",
        "mapping_version": "2",
        "world_origin_x_mm": 1000.0,
        "world_origin_y_mm": 2000.0,
        "rotation_degrees": 90.0,
        "reviewer": "control-coordinate-reviewer",
        "reviewed_on": "2026-11-04",
        "review_reference": "review:CONTROL-COORD-001",
    }
    values.update(overrides)
    values["reviewed_mapping_fingerprint"] = ControlCoordinateTransform.calculate_fingerprint(**values)
    return ControlCoordinateTransform(**values)


def _read_model() -> ControlProgramReadModel:
    return ControlProgramReadModel(
        project_id="project-1",
        program_id="ladder-main",
        schema_version=1,
        rungs=(
            LadderRungReadModel(
                "rung-1", 0, True, ("contact-1", "coil-1"),
                {"contact-1": 0, "coil-1": 2},
            ),
        ),
        components=(
            ControlComponentReadModel(
                "contact-1", "normally_open_contact", (), ("out",), {}, {},
                rung_id="rung-1", position=0,
            ),
            ControlComponentReadModel(
                "coil-1", "coil", ("in",), (), {}, {},
                rung_id="rung-1", position=2,
            ),
        ),
        connections=(
            ControlConnectionReadModel("contact-1", "out", "coil-1", "in"),
        ),
        dependencies=(),
    )


def _builder() -> ControlDrawingEntityBuilder:
    primitive = DrawingEntity(
        "symbol-body", "LINE", "SYMBOL",
        {"start": (-8, 0), "end": (8, 0)}, {},
    )
    contact = SymbolDefinition(
        symbol_id="control.contact",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,),
        review_status="reviewed",
        reviewer="symbol-reviewer",
        reviewed_on="2026-11-04",
        review_reference="review:CONTACT",
        standard_reference="standard:CONTACT",
        terminal_anchors={"output:out": (8, 0)},
    )
    coil = SymbolDefinition(
        symbol_id="control.coil",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,),
        review_status="reviewed",
        reviewer="symbol-reviewer",
        reviewed_on="2026-11-04",
        review_reference="review:COIL",
        standard_reference="standard:COIL",
        terminal_anchors={"input:in": (-8, 0)},
    )
    return ControlDrawingEntityBuilder(
        SymbolRegistry((contact, coil)),
        symbol_ids={
            "normally_open_contact": "control.contact",
            "coil": "control.coil",
        },
        layout=ControlDrawingLayout(
            origin=(10, 20), component_pitch_mm=100, rung_pitch_mm=80,
        ),
    )


class _Application:
    def __init__(self) -> None:
        self.revision = "revision-42"
        self.project_lifecycle = SimpleNamespace(
            context=SimpleNamespace(project_id="project-1"),
            activation_generation=4,
        )

    def read_control(self) -> ControlProgramReadModel:
        return _read_model()


def _capture(transform: ControlCoordinateTransform | None = None):
    return ControlProjectionDrawingSnapshotAdapter().capture(
        _Application(),
        project_id="project-1",
        coordinate_transform=transform or _transform(),
        entity_builder=_builder().build,
    )


def test_control_transform_is_rigid_mm_to_mm_and_keeps_symbol_local_anchors() -> None:
    snapshot = _capture()
    assert snapshot.coordinate_system == "world"
    assert snapshot.units == "mm"
    assert snapshot.source_kind == "control_program_read_model"
    assert snapshot.source_id == "ladder-main"

    by_id = {entity.entity_id: entity for entity in snapshot.entities}
    assert by_id["control-component:contact-1"].geometry["insert"] == pytest.approx((980, 2010))
    assert by_id["control-rung:rung-1"].geometry["start"] == pytest.approx((980, 2010))
    assert by_id["control-rung:rung-1"].geometry["end"] == pytest.approx((980, 2910))
    assert by_id["control-component:contact-1"].geometry["scale"] == (1.0, 1.0, 1.0)
    assert by_id["control-component:contact-1"].metadata["symbol_terminal_anchors"]["output:out"] == (8, 0)
    evidence = by_id["control-component:contact-1"].metadata["coordinate_transform"]
    assert evidence["mapping_id"] == "control-site-1"
    assert evidence["target_coordinate_system"] == "world"
    assert evidence["mapping_fingerprint"] == _transform().fingerprint


def test_control_snapshot_composes_to_a_valid_world_mm_drawing_plan() -> None:
    plan = DrawingPlanFactory.from_snapshot(_capture())
    validate_drawing_plan(plan)
    assert plan.drawing_types == ("control",)
    assert plan.coordinate_system == "world"
    assert plan.source_id == "ladder-main"
    assert plan.source_document_id is None


def test_control_transform_rejects_a_mapping_not_matching_its_pinned_review_digest() -> None:
    transform = _transform()
    object.__setattr__(transform, "world_origin_x_mm", 1234.0)
    with pytest.raises(ValueError, match="pinned reviewed fingerprint"):
        transform.transform_point((0, 0))


def test_control_transform_rejects_double_transform() -> None:
    first = _transform().transform_entities((
        DrawingEntity("line", "LINE", "WIRE", {"start": (0, 0), "end": (1, 0)}, {}),
    ))
    with pytest.raises(ValueError, match="already carries coordinate-transform"):
        _transform().transform_entities(first)


def test_control_plan_rejects_tampered_transform_provenance() -> None:
    snapshot = _capture()
    entities = list(snapshot.entities)
    target = entities[0]
    metadata = dict(target.metadata)
    provenance = dict(metadata["coordinate_transform"])
    provenance["world_origin_x_mm"] += 1.0
    metadata["coordinate_transform"] = provenance
    entities[0] = DrawingEntity(
        target.entity_id, target.kind, target.layer, target.geometry, metadata,
    )
    tampered = type(snapshot)(
        project_id=snapshot.project_id,
        export_id=snapshot.export_id,
        source_revision=snapshot.source_revision,
        source_kind=snapshot.source_kind,
        source_id=snapshot.source_id,
        symbol_profile=snapshot.symbol_profile,
        drawing_types=snapshot.drawing_types,
        entities=tuple(entities),
        units=snapshot.units,
        coordinate_system=snapshot.coordinate_system,
    )
    with pytest.raises(DrawingPlanValidationError, match="fingerprint mismatch"):
        DrawingPlanFactory.from_snapshot(tampered)


def test_control_plan_rejects_mixed_mapping_fingerprints() -> None:
    snapshot = _capture()
    other = _transform(
        mapping_id="control-site-2",
        world_origin_x_mm=3000.0,
    )
    transformed = other.transform_entities((
        DrawingEntity(
            "extra", "LINE", "CONTROL_AUX",
            {"start": (0, 0), "end": (10, 0)}, {},
        ),
    ))[0]
    mixed = type(snapshot)(
        project_id=snapshot.project_id,
        export_id=snapshot.export_id,
        source_revision=snapshot.source_revision,
        source_kind=snapshot.source_kind,
        source_id=snapshot.source_id,
        symbol_profile=snapshot.symbol_profile,
        drawing_types=snapshot.drawing_types,
        entities=(*snapshot.entities, transformed),
        units=snapshot.units,
        coordinate_system=snapshot.coordinate_system,
    )
    with pytest.raises(DrawingPlanValidationError, match="mixed coordinate mapping fingerprints"):
        DrawingPlanFactory.from_snapshot(mixed)


def test_control_adapter_requires_explicit_reviewed_transform() -> None:
    with pytest.raises(TypeError, match="ControlCoordinateTransform"):
        ControlProjectionDrawingSnapshotAdapter().capture(
            _Application(),
            project_id="project-1",
            coordinate_transform=object(),
            entity_builder=_builder().build,
        )
