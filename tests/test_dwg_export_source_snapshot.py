import pytest

from core.application.dwg_export import (
    ApplicationDrawingSnapshot,
    DrawingEntity,
    DrawingPlanFactory,
    DrawingPlanValidationError,
    SymbolProfile,
)


def _entity(entity_id="wire"):
    return DrawingEntity(entity_id, "LINE", "WIRE", {"start": [0, 0], "end": [10, 0]}, {})


def _snapshot(*entities, source_revision="rev-4"):
    return ApplicationDrawingSnapshot(
        project_id="project-a",
        export_id="export-a",
        source_revision=source_revision,
        source_kind="sld_projection",
        source_id="document-a",
        symbol_profile=SymbolProfile.ANSI_IEEE,
        drawing_types=("sld",),
        entities=tuple(entities),
    )


def test_factory_builds_plan_from_detached_application_snapshot():
    plan = DrawingPlanFactory.from_snapshot(_snapshot(_entity()))
    assert plan.source_revision == "rev-4"
    assert plan.entities == (_entity(),)
    assert plan.drawing_types == ("sld",)


def test_factory_fails_closed_on_duplicate_ids_in_snapshot():
    with pytest.raises(DrawingPlanValidationError, match="Duplicate"):
        DrawingPlanFactory.from_snapshot(_snapshot(_entity("same"), _entity("same")))


def test_snapshot_rejects_non_drawing_entity_objects():
    with pytest.raises(TypeError, match="DrawingEntity"):
        _snapshot(object())


def test_snapshot_requires_source_revision():
    with pytest.raises(ValueError, match="source_revision"):
        ApplicationDrawingSnapshot(
            project_id="p",
            export_id="e",
            source_revision=" ",
            source_kind="read_model",
            source_id="id",
            symbol_profile=SymbolProfile.IEC_60617,
            drawing_types=("sld",),
            entities=(),
        )
