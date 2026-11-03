from pathlib import Path

import pytest

from core.application.dwg_export.backends import ODAFileConverterBackend


def test_oda_backend_requires_explicit_existing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ODAFileConverterBackend(tmp_path / "missing-converter")


def test_oda_backend_rejects_non_executable_on_posix(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("not a real converter", encoding="utf-8")
    executable.chmod(0o600)
    if __import__("os").name != "nt":
        with pytest.raises(PermissionError):
            ODAFileConverterBackend(executable)


def test_oda_backend_rejects_unsupported_target_version_before_conversion(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    from core.application.dwg_export import DrawingPlan, SymbolProfile

    plan = DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(),
    )
    with pytest.raises(ValueError, match="Unsupported ODA target version"):
        backend.export(plan, tmp_path / "drawing.dwg", target_version="ACAD2030")


def test_oda_backend_validates_plan_when_called_without_export_service(tmp_path: Path) -> None:
    from core.application.dwg_export import DrawingEntity, DrawingPlan, SymbolProfile
    from core.application.dwg_export.validation import DrawingPlanValidationError

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    invalid = DrawingEntity(
        "invalid-line", "LINE", "SLD", {"start": [0, 0], "end": [0, 0]}, {},
    )
    plan = DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(invalid,),
    )

    with pytest.raises(DrawingPlanValidationError, match="distinct endpoints"):
        backend.export(plan, tmp_path / "drawing.dwg")

    assert not (tmp_path / "drawing.dwg").exists()



def test_oda_backend_rejects_registry_content_drift_even_when_versions_match(tmp_path: Path):
    from core.application.dwg_export import DrawingEntity, DrawingPlan, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    builder_definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="reviewer", reviewed_on="2026-11-03", review_reference="review:1",
    )
    backend_definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 6}, {}),),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="reviewer", reviewed_on="2026-11-03", review_reference="review:1",
    )
    builder_registry = SymbolRegistry((builder_definition,), catalogue_version="1.0.0")
    backend_registry = SymbolRegistry((backend_definition,), catalogue_version="1.0.0")
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]},
        {
            "symbol_definition_version": "1.0.0",
            "symbol_registry_schema_version": 1,
            "symbol_catalogue_version": "1.0.0",
            "symbol_registry_fingerprint": builder_registry.fingerprint,
        },
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=backend_registry)
    with pytest.raises(ValueError, match="registry fingerprint"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)
