from __future__ import annotations

import json
import pytest

from plugins.dwg_export import (
    DrawingEntity, DrawingPlan, DrawingPlanValidationError, SymbolProfile,
    serialize_drawing_plan, validate_drawing_plan,
)


def _plan(entities, *, coordinate_system="world", units="mm"):
    return DrawingPlan(
        project_id="project", export_id="export", source_revision="revision",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=tuple(entities), coordinate_system=coordinate_system, units=units,
    )


def test_plan_canonicalizes_entity_order_and_serialization():
    first = DrawingEntity("b", "line", "WIRE", {"start": [0, 0], "end": [1, 1]}, {"z": 2, "a": 1})
    second = DrawingEntity("a", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 1}, {})
    plan_one = _plan((first, second))
    plan_two = _plan((second, first))
    assert [item.entity_id for item in plan_one.entities] == ["a", "b"]
    assert serialize_drawing_plan(plan_one) == serialize_drawing_plan(plan_two)
    serialized = json.loads(serialize_drawing_plan(plan_one))
    assert serialized["coordinate_system"] == "world"
    assert serialized["source_kind"] is None
    assert serialized["source_id"] is None
    assert serialized["source_document_id"] is None


def test_plan_rejects_missing_coordinate_system_or_units():
    with pytest.raises(ValueError, match="coordinate_system"):
        _plan((), coordinate_system=" ")
    with pytest.raises(ValueError, match="units"):
        _plan((), units=" ")


@pytest.mark.parametrize(
    "entity",
    [
        DrawingEntity("e", "LINE", "WIRE", {"start": [0, 0], "end": [1, 1], "radius": 3}, {}),
        DrawingEntity("e", "CIRCLE", "WIRE", {"center": [0, 0], "radius": 1, "start": [0, 0]}, {}),
        DrawingEntity("e", "TEXT", "LABEL", {"text": "x", "insert": [0, 0], "width": 3}, {}),
        DrawingEntity("e", "LWPOLYLINE", "WIRE", {"points": [[0, 0], [1, 1]], "closed": "yes"}, {}),
        DrawingEntity("e", "INSERT", "SYMBOL", {"symbol_id": "s", "insert": [0, 0], "base_point": [0, 0]}, {}),
    ],
)
def test_validator_rejects_unsupported_primitive_fields(entity):
    with pytest.raises(DrawingPlanValidationError, match="unsupported geometry fields|closed must be boolean"):
        validate_drawing_plan(_plan((entity,)))


def test_validator_rejects_invalid_layer_name():
    entity = DrawingEntity("e", "LINE", "BAD/LAYER", {"start": [0, 0], "end": [1, 1]}, {})
    with pytest.raises(DrawingPlanValidationError, match="invalid CAD layer"):
        validate_drawing_plan(_plan((entity,)))


def test_validator_accepts_explicit_insert_transform_and_registry_basepoint_contract():
    entity = DrawingEntity(
        "e", "INSERT", "SYMBOL",
        {"symbol_id": "breaker", "insert": [10, 20], "scale": [2, -2, 1], "rotation": 90}, {},
    )
    validate_drawing_plan(_plan((entity,)))



def test_serialization_preserves_source_document_provenance():
    plan = DrawingPlan(
        project_id="project", export_id="export", source_revision="revision",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(), source_kind="sld_export_snapshot",
        source_id="document-7", source_document_id="document-7",
    )
    serialized = json.loads(serialize_drawing_plan(plan))
    assert serialized["source_kind"] == "sld_export_snapshot"
    assert serialized["source_id"] == "document-7"
    assert serialized["source_document_id"] == "document-7"


def test_plan_rejects_combined_sld_and_control_drawing_types():
    with pytest.raises(ValueError, match="exactly one drawing type"):
        DrawingPlan(
            project_id="project", export_id="export", source_revision="revision",
            symbol_profile=SymbolProfile.ANSI_IEEE,
            drawing_types=("sld", "control"), entities=(),
        )


def test_sld_export_plan_cannot_be_labelled_as_control():
    with pytest.raises(ValueError, match="SLD-only"):
        DrawingPlan(
            project_id="project", export_id="export", source_revision="revision",
            symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("control",),
            entities=(), source_kind="sld_export_snapshot",
            source_id="document-1", source_document_id="document-1",
        )
