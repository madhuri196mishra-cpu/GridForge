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
