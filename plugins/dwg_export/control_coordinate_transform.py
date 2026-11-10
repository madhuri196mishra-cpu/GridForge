"""Reviewed Control-ladder-local millimetres to world millimetres transform.

Unlike the SLD mapping, Control layout coordinates are already millimetres.
This mapping is therefore rigid (translation + planar rotation) and never
silently rescales geometry. Its expected review fingerprint is supplied by
the reviewer/configuration and rechecked whenever geometry is transformed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
import math
from typing import Iterable, Mapping

from .drawing_plan import DrawingEntity


_PROVENANCE_FIELDS = (
    "mapping_id",
    "mapping_version",
    "source_coordinate_system",
    "source_units",
    "target_coordinate_system",
    "target_units",
    "world_origin_x_mm",
    "world_origin_y_mm",
    "rotation_degrees",
    "reviewer",
    "reviewed_on",
    "review_reference",
)


def _canonical_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ControlCoordinateTransform:
    """Explicit reviewed rigid transform for one Control ladder coordinate frame."""

    mapping_id: str
    mapping_version: str
    world_origin_x_mm: float
    world_origin_y_mm: float
    rotation_degrees: float
    reviewer: str
    reviewed_on: str
    review_reference: str
    reviewed_mapping_fingerprint: str
    source_coordinate_system: str = "control-ladder-local"
    source_units: str = "mm"

    @staticmethod
    def calculate_fingerprint(
        *,
        mapping_id: str,
        mapping_version: str,
        world_origin_x_mm: float,
        world_origin_y_mm: float,
        rotation_degrees: float,
        reviewer: str,
        reviewed_on: str,
        review_reference: str,
        source_coordinate_system: str = "control-ladder-local",
        source_units: str = "mm",
    ) -> str:
        """Calculate the digest a reviewer pins alongside the approved mapping."""
        payload = {
            "mapping_id": mapping_id.strip(),
            "mapping_version": mapping_version.strip(),
            "source_coordinate_system": source_coordinate_system.strip(),
            "source_units": source_units.strip(),
            "target_coordinate_system": "world",
            "target_units": "mm",
            "world_origin_x_mm": float(world_origin_x_mm),
            "world_origin_y_mm": float(world_origin_y_mm),
            "rotation_degrees": float(rotation_degrees),
            "reviewer": reviewer.strip(),
            "reviewed_on": reviewed_on.strip(),
            "review_reference": review_reference.strip(),
        }
        return _canonical_fingerprint(payload)

    def __post_init__(self) -> None:
        for name in (
            "mapping_id", "mapping_version", "reviewer", "reviewed_on",
            "review_reference", "reviewed_mapping_fingerprint",
            "source_coordinate_system", "source_units",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if self.source_coordinate_system != "control-ladder-local":
            raise ValueError("Control transform source must be control-ladder-local")
        if self.source_units != "mm":
            raise ValueError("Control ladder layout coordinates must be millimetres")
        try:
            date.fromisoformat(self.reviewed_on)
        except ValueError as exc:
            raise ValueError("reviewed_on must be an ISO calendar date") from exc
        for name in ("world_origin_x_mm", "world_origin_y_mm", "rotation_degrees"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        self.assert_reviewed_fingerprint()

    def _payload(self) -> dict[str, object]:
        return {
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "source_coordinate_system": self.source_coordinate_system,
            "source_units": self.source_units,
            "target_coordinate_system": "world",
            "target_units": "mm",
            "world_origin_x_mm": self.world_origin_x_mm,
            "world_origin_y_mm": self.world_origin_y_mm,
            "rotation_degrees": self.rotation_degrees,
            "reviewer": self.reviewer,
            "reviewed_on": self.reviewed_on,
            "review_reference": self.review_reference,
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self._payload())

    def assert_reviewed_fingerprint(self) -> None:
        if self.fingerprint != self.reviewed_mapping_fingerprint:
            raise ValueError(
                "Control coordinate mapping differs from its pinned reviewed fingerprint"
            )

    @classmethod
    def fingerprint_from_provenance(cls, provenance: Mapping[str, object]) -> str:
        if not isinstance(provenance, Mapping):
            raise TypeError("coordinate transform provenance must be a mapping")
        payload = {field: provenance[field] for field in _PROVENANCE_FIELDS}
        return _canonical_fingerprint(payload)

    def transform_point(self, point: object) -> tuple[float, float]:
        self.assert_reviewed_fingerprint()
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise ValueError("Control drawing geometry points must be 2D")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point):
            raise TypeError("Control drawing coordinates must be numeric")
        x, y = (float(value) for value in point)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Control drawing coordinates must be finite")
        angle = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        return (
            self.world_origin_x_mm + x * cosine - y * sine,
            self.world_origin_y_mm + x * sine + y * cosine,
        )

    def transform_entities(self, entities: Iterable[DrawingEntity]) -> tuple[DrawingEntity, ...]:
        """Transform drawing-space geometry once; keep symbol-local metadata intact."""
        self.assert_reviewed_fingerprint()
        transformed: list[DrawingEntity] = []
        angle = self.rotation_degrees
        for entity in entities:
            if not isinstance(entity, DrawingEntity):
                raise TypeError("entities must contain DrawingEntity values")
            metadata = dict(entity.metadata)
            if "coordinate_transform" in metadata:
                raise ValueError(
                    f"Entity {entity.entity_id!r} already carries coordinate-transform provenance"
                )
            geometry = dict(entity.geometry)
            kind = entity.kind.upper()
            if kind == "LINE":
                geometry["start"] = self.transform_point(geometry["start"])
                geometry["end"] = self.transform_point(geometry["end"])
            elif kind in {"CIRCLE", "ARC"}:
                geometry["center"] = self.transform_point(geometry["center"])
                if kind == "ARC":
                    geometry["start_angle"] = (float(geometry["start_angle"]) + angle) % 360.0
                    geometry["end_angle"] = (float(geometry["end_angle"]) + angle) % 360.0
            elif kind == "LWPOLYLINE":
                geometry["points"] = tuple(self.transform_point(point) for point in geometry["points"])
            elif kind in {"INSERT", "TEXT"}:
                geometry["insert"] = self.transform_point(geometry["insert"])
                geometry["rotation"] = float(geometry.get("rotation", 0.0)) + angle
            else:
                raise ValueError(
                    f"Unsupported Control export primitive for coordinate transform: {kind!r}"
                )
            metadata["coordinate_transform"] = {
                **self._payload(),
                "mapping_fingerprint": self.fingerprint,
            }
            transformed.append(replace(entity, geometry=geometry, metadata=metadata))
        return tuple(transformed)


__all__ = ["ControlCoordinateTransform"]
