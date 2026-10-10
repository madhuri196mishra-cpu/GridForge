"""Immutable backend-neutral drawing-plan DTOs; never engineering authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping


class SymbolProfile(str, Enum):
    ANSI_IEEE = "ansi_ieee"
    IEC_60617 = "iec_60617"


def _freeze_json(value: Any, *, path: str) -> Any:
    """Freeze JSON-compatible values and reject unsupported objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"{path} mapping keys must be non-empty strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise TypeError(f"{path} contains unsupported value type: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class DrawingEntity:
    """One CAD-neutral primitive with a stable GridForge identity."""
    entity_id: str
    kind: str
    layer: str
    geometry: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        for name in ("entity_id", "kind", "layer"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.geometry, Mapping) or not isinstance(self.metadata, Mapping):
            raise TypeError("geometry and metadata must be mappings")
        object.__setattr__(self, "geometry", _freeze_json(self.geometry, path="geometry"))
        object.__setattr__(self, "metadata", _freeze_json(self.metadata, path="metadata"))


@dataclass(frozen=True, slots=True)
class DrawingPlan:
    """Versioned immutable snapshot consumed by CAD export backends."""
    project_id: str
    export_id: str
    source_revision: str
    symbol_profile: SymbolProfile
    drawing_types: tuple[str, ...]
    entities: tuple[DrawingEntity, ...]
    units: str = "mm"
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("project_id", "export_id", "source_revision", "units"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version != 1:
            raise ValueError("schema_version must be the supported integer 1")
        if not isinstance(self.symbol_profile, SymbolProfile):
            try:
                object.__setattr__(self, "symbol_profile", SymbolProfile(self.symbol_profile))
            except (TypeError, ValueError) as exc:
                raise ValueError("symbol_profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "drawing_types", tuple(self.drawing_types))
        object.__setattr__(self, "entities", tuple(self.entities))
        if not self.drawing_types or any(not isinstance(item, str) or not item.strip() for item in self.drawing_types):
            raise ValueError("drawing_types must contain non-empty names")
        if any(not isinstance(item, DrawingEntity) for item in self.entities):
            raise TypeError("entities must contain only DrawingEntity values")
