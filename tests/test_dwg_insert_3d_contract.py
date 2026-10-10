from core.application.dwg_export import DrawingEntity, DrawingPlan, SymbolProfile, validate_drawing_plan


def _plan(entity):
    return DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(entity,),
    )


def test_insert_accepts_three_dimensional_point_supported_by_backend():
    entity = DrawingEntity(
        "e1", "INSERT", "SYMBOL",
        {"symbol_id": "breaker", "insert": [1.0, 2.0, 3.0]}, {},
    )
    validate_drawing_plan(_plan(entity))


def test_insert_rejects_non_finite_z_coordinate():
    entity = DrawingEntity(
        "e1", "INSERT", "SYMBOL",
        {"symbol_id": "breaker", "insert": [1.0, 2.0, float("inf")]}, {},
    )
    try:
        validate_drawing_plan(_plan(entity))
    except ValueError as exc:
        assert "insert.z must be finite" in str(exc)
    else:
        raise AssertionError("non-finite INSERT coordinate must be rejected")
