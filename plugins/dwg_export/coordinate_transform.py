"""Explicit, reviewed SLD-document-local to world/mm transformation.

The mapping is configuration, never inferred from drawing extents or defaults.
It uses a positive uniform scale, planar rotation, and world-origin translation;
reflection, non-uniform scaling, and unknown source units are deliberately absent.
"""
from __future__ import annotations

from collections.abc import Mapping
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

    def transform_vector(self, vector: object) -> tuple[float, float]:
        """Rotate a symbol-local millimetre vector without document scaling or translation."""
        if not isinstance(vector, (tuple, list)) or len(vector) != 2:
            raise ValueError("SLD symbol offset vectors must be 2D")
        x, y = vector
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
            raise TypeError("SLD symbol offset coordinates must be numeric")
        x, y = float(x), float(y)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("SLD symbol offset coordinates must be finite")
        angle = math.radians(self.rotation_degrees)
        cosine, sine = math.cos(angle), math.sin(angle)
        return (x * cosine - y * sine, x * sine + y * cosine)

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
            resolved_route_anchor_positions: dict[str, tuple[float, float]] = {}
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
                source_points = tuple(geometry["points"])
                transformed_points = [
                    self.transform_point(point) for point in source_points
                ]
                for endpoint_name, endpoint_index in (("source", 0), ("target", -1)):
                    offset_field = f"{endpoint_name}_terminal_anchor_offset_mm"
                    coordinate_field = f"{offset_field}_coordinate_system"
                    if offset_field not in entity.metadata:
                        continue
                    if entity.metadata.get(coordinate_field) != "sld-symbol-instance-mm-vector":
                        raise ValueError(
                            f"LWPOLYLINE {entity.entity_id} {endpoint_name} terminal anchor "
                            "offset has an unsupported coordinate system"
                        )
                    world_offset = self.transform_vector(entity.metadata[offset_field])
                    node_position = transformed_points[endpoint_index]
                    anchor_position = (
                        node_position[0] + world_offset[0],
                        node_position[1] + world_offset[1],
                    )
                    transformed_points[endpoint_index] = anchor_position
                    resolved_route_anchor_positions[endpoint_name] = anchor_position
                geometry["points"] = tuple(transformed_points)
            elif kind in {"INSERT", "TEXT"}:
                world_insert = self.transform_point(geometry["insert"])
                geometry["insert"] = world_insert
                if kind == "TEXT":
                    geometry["height"] = float(geometry.get("height", 2.5)) * scale_factor
                else:
                    base_point_offset = entity.metadata.get("symbol_base_point_offset_mm")
                    if base_point_offset is not None:
                        if entity.metadata.get("symbol_base_point_offset_coordinate_system") != "sld-symbol-instance-mm-vector":
                            raise ValueError(
                                f"INSERT {entity.entity_id} base-point offset has an unsupported coordinate system"
                            )
                        world_base_point_offset = self.transform_vector(base_point_offset)
                        geometry["insert"] = (
                            world_insert[0] + world_base_point_offset[0],
                            world_insert[1] + world_base_point_offset[1],
                        )
                    # INSERT scale is dimensionless: symbol geometry is local
                    # millimetres and must not absorb document-unit conversion.
                    # Only the SLD node origin is mapped as a document point.
                geometry["rotation"] = float(geometry.get("rotation", 0.0)) + angle
            else:
                raise ValueError(f"Unsupported SLD export primitive for coordinate transform: {kind!r}")
            metadata = dict(entity.metadata)
            if resolved_route_anchor_positions:
                for endpoint_name, position in resolved_route_anchor_positions.items():
                    metadata[f"{endpoint_name}_terminal_anchor_position_world_mm"] = position
                metadata["terminal_anchor_positions_coordinate_system"] = "world-mm"
            if kind == "INSERT" and "symbol_terminal_anchor_offsets_mm" in metadata:
                anchor_offsets = metadata["symbol_terminal_anchor_offsets_mm"]
                if not isinstance(anchor_offsets, Mapping):
                    raise ValueError(
                        f"INSERT {entity.entity_id} terminal anchor offsets must be a mapping"
                    )
                if metadata.get("symbol_terminal_anchor_offsets_coordinate_system") != "sld-symbol-instance-mm-vector":
                    raise ValueError(
                        f"INSERT {entity.entity_id} terminal anchor offsets have an unsupported coordinate system"
                    )
                if any(not isinstance(name, str) or not name.strip() for name in anchor_offsets):
                    raise ValueError(
                        f"INSERT {entity.entity_id} terminal anchor names must be non-empty strings"
                    )
                metadata["symbol_terminal_anchor_positions"] = {
                    name: (
                        world_insert[0] + self.transform_vector(anchor_offsets[name])[0],
                        world_insert[1] + self.transform_vector(anchor_offsets[name])[1],
                    )
                    for name in sorted(anchor_offsets)
                }
                metadata["symbol_terminal_anchor_positions_coordinate_system"] = "world-mm"
                if "symbol_base_point_offset_mm" in metadata:
                    metadata["symbol_base_point_offset_world_mm"] = self.transform_vector(
                        metadata["symbol_base_point_offset_mm"]
                    )
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
