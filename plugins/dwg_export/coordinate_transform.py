"""Explicit, reviewed SLD-document-local to world/mm transformation.

The mapping is configuration, never inferred from drawing extents or defaults.
It uses a positive uniform scale, planar rotation, and world-origin translation;
reflection, non-uniform scaling, and unknown source units are deliberately absent.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import math
from typing import Iterable

from .drawing_plan import DrawingEntity


@dataclass(frozen=True, slots=True)
class SLDCoordinateTransform:
    """Reviewed mapping from one declared SLD coordinate space into world mm."""

    mapping_id: str
    mapping_version: str
    source_coordinate_system: str
    source_units: str
    millimetres_per_source_unit: float
    world_origin_x_mm: float
    world_origin_y_mm: float
    rotation_degrees: float
    reviewer: str
    reviewed_on: str
    review_reference: str

    def __post_init__(self) -> None:
        for name in (
            "mapping_id", "mapping_version", "source_coordinate_system",
            "source_units", "reviewer", "reviewed_on", "review_reference",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        try:
            date.fromisoformat(self.reviewed_on)
        except ValueError as exc:
            raise ValueError("reviewed_on must be an ISO calendar date") from exc
        for name in (
            "millimetres_per_source_unit", "world_origin_x_mm",
            "world_origin_y_mm", "rotation_degrees",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.millimetres_per_source_unit <= 0:
            raise ValueError("millimetres_per_source_unit must be positive")
        if self.source_coordinate_system != "sld-document-local":
            raise ValueError("only the declared sld-document-local source is supported")
        if self.source_units == "mm":
            # Even millimetre labels do not imply a world coordinate frame;
            # the explicit mapping remains required, but identity scale is clear.
            pass

    def transform_point(self, point: object) -> tuple[float, float]:
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise ValueError("SLD geometry points must be 2D")
        x, y = point
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point):
            raise TypeError("SLD geometry coordinates must be numeric")
        x, y = float(x), float(y)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("SLD geometry coordinates must be finite")
        angle = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        scale = self.millimetres_per_source_unit
        return (
            self.world_origin_x_mm + scale * (x * cosine - y * sine),
            self.world_origin_y_mm + scale * (x * sine + y * cosine),
        )

    def transform_entities(self, entities: Iterable[DrawingEntity]) -> tuple[DrawingEntity, ...]:
        """Transform every supported world-space primitive; retain local symbol definitions."""
        transformed: list[DrawingEntity] = []
        angle = self.rotation_degrees
        scale_factor = self.millimetres_per_source_unit
        for entity in entities:
            if not isinstance(entity, DrawingEntity):
                raise TypeError("entities must contain DrawingEntity values")
            geometry = dict(entity.geometry)
            kind = entity.kind.upper()
            if kind == "LINE":
                geometry["start"] = self.transform_point(geometry["start"])
                geometry["end"] = self.transform_point(geometry["end"])
            elif kind in {"CIRCLE", "ARC"}:
                geometry["center"] = self.transform_point(geometry["center"])
                geometry["radius"] = float(geometry["radius"]) * scale_factor
                if kind == "ARC":
                    # DXF ARC angles are represented within one revolution.
                    # Normalize both endpoints independently; adding the same
                    # rotation preserves the authored counter-clockwise sweep,
                    # including arcs that cross the 0-degree boundary.
                    geometry["start_angle"] = (float(geometry["start_angle"]) + angle) % 360.0
                    geometry["end_angle"] = (float(geometry["end_angle"]) + angle) % 360.0
            elif kind == "LWPOLYLINE":
                geometry["points"] = tuple(self.transform_point(point) for point in geometry["points"])
            elif kind in {"INSERT", "TEXT"}:
                geometry["insert"] = self.transform_point(geometry["insert"])
                if kind == "TEXT":
                    geometry["height"] = float(geometry.get("height", 2.5)) * scale_factor
                # INSERT scale is dimensionless: the block definition is local
                # geometry and the instance scale must not absorb the document
                # unit conversion. Only the insertion point is in SLD units.
                geometry["rotation"] = float(geometry.get("rotation", 0.0)) + angle
            else:
                raise ValueError(f"Unsupported SLD export primitive for coordinate transform: {kind!r}")
            metadata = dict(entity.metadata)
            if kind == "INSERT" and "symbol_terminal_anchor_positions" in metadata:
                anchor_positions = metadata["symbol_terminal_anchor_positions"]
                anchor_coordinate_system = metadata.get(
                    "symbol_terminal_anchor_positions_coordinate_system"
                )
                if not isinstance(anchor_positions, dict) and not hasattr(anchor_positions, "items"):
                    raise ValueError(
                        f"INSERT {entity.entity_id} terminal anchor positions must be a mapping"
                    )
                if anchor_coordinate_system != "sld-document-local":
                    raise ValueError(
                        f"INSERT {entity.entity_id} terminal anchors are not in the expected "
                        "sld-document-local coordinate system; refusing a repeated or ambiguous transform"
                    )
                metadata["symbol_terminal_anchor_positions"] = {
                    name: self.transform_point(point)
                    for name, point in sorted(anchor_positions.items())
                }
                metadata["symbol_terminal_anchor_positions_coordinate_system"] = "world-mm"
            metadata["coordinate_transform"] = {
                "mapping_id": self.mapping_id,
                "mapping_version": self.mapping_version,
                "source_coordinate_system": self.source_coordinate_system,
                "source_units": self.source_units,
                "target_coordinate_system": "world",
                "target_units": "mm",
                "millimetres_per_source_unit": scale_factor,
                "world_origin_mm": (self.world_origin_x_mm, self.world_origin_y_mm),
                "rotation_degrees": angle,
                "reviewer": self.reviewer,
                "reviewed_on": self.reviewed_on,
                "review_reference": self.review_reference,
            }
            transformed.append(replace(entity, geometry=geometry, metadata=metadata))
        return tuple(transformed)


__all__ = ["SLDCoordinateTransform"]
