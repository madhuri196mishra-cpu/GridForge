from pathlib import Path

import pytest

from core.application.dwg_export import (
    DrawingEntity,
    DrawingPlan,
    DrawingPlanValidationError,
    DWGExportService,
    ExportResult,
    SymbolProfile,
    validate_drawing_plan,
)


def _plan(*entities: DrawingEntity) -> DrawingPlan:
    return DrawingPlan(
        project_id="project-1",
        export_id="export-1",
        source_revision="revision-1",
        symbol_profile=SymbolProfile.ANSI_IEEE,
        drawing_types=("sld",),
        entities=tuple(entities),
    )


def test_drawing_entity_recursively_freezes_nested_geometry() -> None:
    source = {"points": [[0.0, 1.0], [2.0, 3.0]]}
    entity = DrawingEntity("e1", "LWPOLYLINE", "WIRE", source, {})
    source["points"][0][0] = 999.0
    assert entity.geometry["points"][0][0] == 0.0
    with pytest.raises(TypeError):
        entity.geometry["points"][0][0] = 4.0


def test_plan_rejects_non_finite_geometry() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        DrawingEntity("e1", "LINE", "WIRE", {"start": [0.0, float("nan")]}, {})


def test_validator_rejects_duplicate_stable_entity_ids() -> None:
    first = DrawingEntity("same", "LINE", "WIRE", {"start": [0, 0], "end": [1, 1]}, {})
    second = DrawingEntity("same", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 1}, {})
    with pytest.raises(DrawingPlanValidationError, match="Duplicate"):
        validate_drawing_plan(_plan(first, second))


def test_validator_rejects_unknown_primitive() -> None:
    entity = DrawingEntity("e1", "SPLINE3D", "SYMBOL", {}, {})
    with pytest.raises(DrawingPlanValidationError, match="Unsupported drawing primitive"):
        validate_drawing_plan(_plan(entity))


def test_insert_requires_a_block_name() -> None:
    entity = DrawingEntity("e1", "INSERT", "SYMBOL", {"point": [0, 0]}, {})
    with pytest.raises(DrawingPlanValidationError, match="symbol_id"):
        validate_drawing_plan(_plan(entity))


def test_service_rejects_dxf_backend_for_dwg_request_before_writing(tmp_path: Path) -> None:
    class DxfOnlyBackend:
        backend_id = "test-dxf"
        supported_formats = frozenset({"dxf"})

        def export(self, plan, destination, *, target_version=None):
            raise AssertionError("backend must not be called for unsupported DWG")

    service = DWGExportService(DxfOnlyBackend())
    with pytest.raises(ValueError, match="does not support 'dwg'"):
        service.export(_plan(), tmp_path / "drawing.dwg")


def test_service_rejects_wrong_output_suffix(tmp_path: Path) -> None:
    class DwgBackend:
        backend_id = "test-dwg"
        supported_formats = frozenset({"dwg"})

        def export(self, plan, destination, *, target_version=None):
            return ExportResult(destination, "dwg", len(plan.entities), self.backend_id)

    service = DWGExportService(DwgBackend())
    with pytest.raises(ValueError, match="extension must be .dwg"):
        service.export(_plan(), tmp_path / "drawing.dxf")
