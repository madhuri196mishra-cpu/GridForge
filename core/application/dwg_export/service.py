"""Application-level orchestration for backend-neutral drawing export."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .drawing_plan import DrawingPlan
from .validation import validate_drawing_plan


@dataclass(frozen=True, slots=True)
class ExportResult:
    destination: Path
    format: str
    entity_count: int
    backend_id: str


class DrawingExportBackend(Protocol):
    """Backend contract. Implementations must fail rather than drop primitives."""
    backend_id: str
    supported_formats: frozenset[str]

    def export(self, plan: DrawingPlan, destination: Path, *, target_version: str | None = None) -> ExportResult:
        """Serialize a validated plan to a declared supported format."""


class DWGExportService:
    """Validate a plan before delegating; no IngeCAD process is required."""
    def __init__(self, backend: DrawingExportBackend) -> None:
        if backend is None or not callable(getattr(backend, "export", None)):
            raise TypeError("backend must implement export(plan, destination, target_version=...)")
        supported = getattr(backend, "supported_formats", None)
        if not isinstance(supported, (set, frozenset, tuple, list)):
            raise TypeError("backend.supported_formats must declare supported output formats")
        normalized = frozenset(str(item).lower().lstrip(".") for item in supported)
        if not normalized or any(item not in {"dwg", "dxf"} for item in normalized):
            raise ValueError("backend may declare only supported dwg and/or dxf formats")
        self._backend = backend
        self._supported_formats = normalized
        self._backend_id = str(getattr(backend, "backend_id", type(backend).__name__))

    def export(self, plan: DrawingPlan, destination: str | Path, *, format: str = "dwg", target_version: str | None = None) -> ExportResult:
        validate_drawing_plan(plan)
        normalized_format = str(format).lower().lstrip(".")
        if normalized_format not in self._supported_formats:
            raise ValueError(f"Backend does not support {normalized_format!r}; supported: {', '.join(sorted(self._supported_formats))}")
        target = Path(destination)
        if target.suffix.lower() != f".{normalized_format}":
            raise ValueError(f"Destination extension must be .{normalized_format}")
        result = self._backend.export(plan, target, target_version=target_version)
        if not isinstance(result, ExportResult):
            raise TypeError("backend.export() must return ExportResult")
        if result.format.lower().lstrip(".") != normalized_format:
            raise RuntimeError("Backend returned a format different from the requested format")
        if result.destination != target:
            raise RuntimeError("Backend returned a different destination than requested")
        if result.entity_count != len(plan.entities):
            raise RuntimeError("Backend entity count does not match the validated plan")
        if result.backend_id != self._backend_id:
            raise RuntimeError("Backend identity in ExportResult does not match configured backend")
        return result
