"""Immutable backend-neutral drawing-plan DTOs; never engineering authority."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
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


def _plain_json(value: Any) -> Any:
    """Return a deterministic JSON-compatible copy of frozen DTO values."""
    if isinstance(value, Mapping):
        return {key: _plain_json(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


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
        object.__setattr__(self, "kind", self.kind.upper())
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
    coordinate_system: str = "world"

    def __post_init__(self) -> None:
        for name in ("project_id", "export_id", "source_revision", "units", "coordinate_system"):
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
        drawing_types = tuple(self.drawing_types)
        if not drawing_types or any(not isinstance(item, str) or not item.strip() for item in drawing_types):
            raise ValueError("drawing_types must contain non-empty names")
        normalized_types = tuple(sorted({item.strip() for item in drawing_types}))
        if len(normalized_types) != len(drawing_types):
            raise ValueError("drawing_types must not contain duplicates")
        entities = tuple(self.entities)
        if any(not isinstance(item, DrawingEntity) for item in entities):
            raise TypeError("entities must contain only DrawingEntity values")
        object.__setattr__(self, "drawing_types", normalized_types)
        # Canonical order is independent of the caller's collection order.
        object.__setattr__(self, "entities", tuple(sorted(entities, key=lambda item: item.entity_id)))


def drawing_plan_to_dict(plan: DrawingPlan) -> dict[str, Any]:
    """Return the canonical, stable representation of a validated plan."""
    if not isinstance(plan, DrawingPlan):
        raise TypeError("plan must be a DrawingPlan")
    return {
        "schema_version": plan.schema_version,
        "project_id": plan.project_id,
        "export_id": plan.export_id,
        "source_revision": plan.source_revision,
        "symbol_profile": plan.symbol_profile.value,
        "drawing_types": list(plan.drawing_types),
        "coordinate_system": plan.coordinate_system,
        "units": plan.units,
        "entities": [
            {
                "entity_id": entity.entity_id,
                "kind": entity.kind,
                "layer": entity.layer,
                "geometry": _plain_json(entity.geometry),
                "metadata": _plain_json(entity.metadata),
            }
            for entity in plan.entities
        ],
    }


def serialize_drawing_plan(plan: DrawingPlan) -> str:
    """Serialize a plan deterministically for hashing, review or interchange."""
    return json.dumps(
        drawing_plan_to_dict(plan),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "DrawingEntity", "DrawingPlan", "SymbolProfile",
    "drawing_plan_to_dict", "serialize_drawing_plan",
]
