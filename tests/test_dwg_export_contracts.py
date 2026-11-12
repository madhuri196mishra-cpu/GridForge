from pathlib import Path

import pytest

from plugins.dwg_export import (
    DrawingEntity,
    DrawingPlan,
    DrawingPlanValidationError,
    DWGExportService,
    ExportResult,
    SymbolProfile,
    SLDCoordinateTransform,
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


def test_validator_rejects_empty_sld_plan_without_entity_bound_mapping_provenance() -> None:
    plan = DrawingPlan(
        project_id="project-1",
        export_id="export-empty-sld",
        source_revision="revision-1",
        symbol_profile=SymbolProfile.ANSI_IEEE,
        drawing_types=("sld",),
        entities=(),
        coordinate_system="world",
        units="mm",
        source_kind="sld_export_snapshot",
        source_id="document-1",
        source_document_id="document-1",
    )
    with pytest.raises(
        DrawingPlanValidationError,
        match="SLD export plans require transformed entities carrying reviewed coordinate provenance",
    ):
        validate_drawing_plan(plan)


def _sld_mapping_provenance(**overrides):
    provenance = {
        "mapping_id": "site-grid-01",
        "mapping_version": "1.0.0",
        "source_coordinate_system": "sld-document-local",
        "source_units": "sld-unit",
        "target_coordinate_system": "world",
        "target_units": "mm",
        "millimetres_per_source_unit": 2.0,
        "world_origin_mm": (1000.0, 2000.0),
        "rotation_degrees": 90.0,
        "reviewer": "engineering-review",
        "reviewed_on": "2026-11-03",
        "review_reference": "review:DWG-COORD-001",
        **overrides,
    }
    provenance["mapping_fingerprint"] = SLDCoordinateTransform.fingerprint_from_provenance(provenance)
    return provenance


def _sld_entity(entity_id="sld-entity-1", provenance=None):
    return DrawingEntity(
        entity_id, "LINE", "WIRE",
        {"start": (0.0, 0.0), "end": (1.0, 1.0)},
        {"coordinate_transform": _sld_mapping_provenance() if provenance is None else provenance},
    )


def _sld_plan(*entities):
    return DrawingPlan(
        project_id="project-1", export_id="export-sld",
        source_revision="revision-1", symbol_profile=SymbolProfile.ANSI_IEEE,
        drawing_types=("sld",), entities=tuple(entities),
        coordinate_system="world", units="mm",
        source_kind="sld_export_snapshot", source_id="document-1",
        source_document_id="document-1",
    )


def test_validator_accepts_complete_reviewed_sld_coordinate_provenance() -> None:
    validate_drawing_plan(_sld_plan(_sld_entity()))


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_coordinate_system": "invented-frame"},
        {"source_units": ""},
        {"target_units": "inch"},
        {"reviewed_on": "not-a-date"},
        {"millimetres_per_source_unit": 0},
        {"millimetres_per_source_unit": float("inf")},
        {"world_origin_mm": (0.0,)},
        {"review_reference": ""},
    ],
)
def test_validator_rejects_invalid_sld_coordinate_provenance(overrides) -> None:
    with pytest.raises(DrawingPlanValidationError, match="SLD entity"):
        validate_drawing_plan(_sld_plan(_sld_entity(provenance=_sld_mapping_provenance(**overrides))))


def test_validator_rejects_conflicting_sld_coordinate_mapping_provenance() -> None:
    first = _sld_entity("first")
    second = _sld_entity(
        "second",
        _sld_mapping_provenance(mapping_id="different-map"),
    )
    with pytest.raises(DrawingPlanValidationError, match="conflicting coordinate mapping"):
        validate_drawing_plan(_sld_plan(first, second))


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


@pytest.mark.parametrize(
    ("kind", "geometry", "message"),
    [
        ("LINE", {"start": [1, 1], "end": [1, 1]}, "distinct endpoints"),
        ("CIRCLE", {"center": [0, 0], "radius": 0}, "radius must be positive"),
        ("ARC", {"center": [0, 0], "radius": 1, "start_angle": 20, "end_angle": 20}, "angles must differ"),
        ("LWPOLYLINE", {"points": [[0, 0], [0, 0]]}, "distinct points"),
        ("INSERT", {"symbol_id": "breaker", "insert": [0, 0], "scale": 0}, "scale values must be finite and non-zero"),
    ],
)
def test_validator_rejects_invalid_supported_geometry(kind, geometry, message) -> None:
    entity = DrawingEntity("e1", kind, "SYMBOL", geometry, {})
    with pytest.raises(DrawingPlanValidationError, match=message):
        validate_drawing_plan(_plan(entity))


def test_validator_rejects_malformed_polyline_point_dimension() -> None:
    entity = DrawingEntity("e1", "LWPOLYLINE", "WIRE", {"points": [[0, 0, 1], [1, 1, 1]]}, {})
    with pytest.raises(DrawingPlanValidationError, match="2D point"):
        validate_drawing_plan(_plan(entity))


def test_validator_accepts_backend_supported_three_axis_insert_scale() -> None:
    entity = DrawingEntity(
        "e1", "INSERT", "SYMBOL",
        {"symbol_id": "breaker", "insert": [0, 0], "scale": [1.0, -1.0, 0.5]}, {},
    )
    validate_drawing_plan(_plan(entity))



def test_service_rejects_backend_result_with_mismatched_source_provenance(tmp_path: Path):
    class IncorrectProvenanceBackend:
        backend_id = "test-dwg"
        supported_formats = frozenset({"dwg"})

        def export(self, plan, destination, *, target_version=None):
            return ExportResult(
                destination, "dwg", len(plan.entities), self.backend_id,
                project_id="different-project",
                source_revision=plan.source_revision,
                source_kind=plan.source_kind,
                source_id=plan.source_id,
                source_document_id=plan.source_document_id,
            )

    service = DWGExportService(IncorrectProvenanceBackend())
    with pytest.raises(RuntimeError, match="provenance mismatch for project_id"):
        service.export(_plan(), tmp_path / "drawing.dwg")



def test_service_accepts_export_result_with_matching_source_provenance(tmp_path: Path):
    class ProvenanceBackend:
        backend_id = "test-dwg"
        supported_formats = frozenset({"dwg"})

        def export(self, plan, destination, *, target_version=None):
            return ExportResult(
                destination, "dwg", len(plan.entities), self.backend_id,
                project_id=plan.project_id,
                source_revision=plan.source_revision,
                source_kind=plan.source_kind,
                source_id=plan.source_id,
                source_document_id=plan.source_document_id,
            )

    service = DWGExportService(ProvenanceBackend())
    result = service.export(_plan(), tmp_path / "drawing.dwg")
    assert result.project_id == "project-1"
    assert result.source_revision == "revision-1"
