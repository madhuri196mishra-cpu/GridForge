"""Fail-closed validation for backend-neutral drawing plans."""
from __future__ import annotations

import math
from collections.abc import Mapping
from .drawing_plan import DrawingPlan


class DrawingPlanValidationError(ValueError):
    """Raised when a drawing plan is unsafe or internally inconsistent."""


_SUPPORTED_KINDS = {"LINE", "CIRCLE", "ARC", "TEXT", "INSERT", "LWPOLYLINE"}


def _finite_numbers(value: object, path: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise DrawingPlanValidationError(f"{path} must be finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _finite_numbers(item, f"{path}.{key}")
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _finite_numbers(item, f"{path}[{index}]")
        return
    raise DrawingPlanValidationError(f"{path} contains an unsupported value")


def _number(geometry: Mapping[str, object], key: str, entity_id: str) -> float:
    value = geometry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DrawingPlanValidationError(f"Entity {entity_id} requires numeric geometry.{key}")
    number = float(value)
    if not math.isfinite(number):
        raise DrawingPlanValidationError(f"Entity {entity_id} geometry.{key} must be finite")
    return number


def _point(value: object, path: str, entity_id: str) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise DrawingPlanValidationError(f"Entity {entity_id} requires {path} as a 2D point")
    result = []
    for axis, coordinate in zip(("x", "y"), value):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise DrawingPlanValidationError(f"Entity {entity_id} requires numeric {path}.{axis}")
        if not math.isfinite(float(coordinate)):
            raise DrawingPlanValidationError(f"Entity {entity_id} {path}.{axis} must be finite")
        result.append(float(coordinate))
    return result[0], result[1]


def validate_drawing_plan(plan: DrawingPlan) -> None:
    """Validate identity, primitive support and geometry before backend writes."""
    if not isinstance(plan, DrawingPlan):
        raise TypeError("plan must be a DrawingPlan")
    if plan.schema_version != 1:
        raise DrawingPlanValidationError(f"Unsupported drawing plan schema: {plan.schema_version}")
    seen: set[str] = set()
    for entity in plan.entities:
        if entity.entity_id in seen:
            raise DrawingPlanValidationError(f"Duplicate drawing entity ID: {entity.entity_id}")
        seen.add(entity.entity_id)
        kind = entity.kind.upper()
        if kind not in _SUPPORTED_KINDS:
            raise DrawingPlanValidationError(f"Unsupported drawing primitive {entity.kind!r} on {entity.entity_id}")
        geometry = entity.geometry
        _finite_numbers(geometry, f"entities[{entity.entity_id}].geometry")
        if kind == "LINE":
            start = _point(geometry.get("start"), "geometry.start", entity.entity_id)
            end = _point(geometry.get("end"), "geometry.end", entity.entity_id)
            if start == end:
                raise DrawingPlanValidationError(f"LINE entity {entity.entity_id} must have distinct endpoints")
        elif kind == "CIRCLE":
            _point(geometry.get("center"), "geometry.center", entity.entity_id)
            if _number(geometry, "radius", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"CIRCLE entity {entity.entity_id} radius must be positive")
        elif kind == "ARC":
            _point(geometry.get("center"), "geometry.center", entity.entity_id)
            if _number(geometry, "radius", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"ARC entity {entity.entity_id} radius must be positive")
            start = _number(geometry, "start_angle", entity.entity_id)
            end = _number(geometry, "end_angle", entity.entity_id)
            if start == end:
                raise DrawingPlanValidationError(f"ARC entity {entity.entity_id} start_angle and end_angle must differ")
        elif kind == "LWPOLYLINE":
            points = geometry.get("points")
            if not isinstance(points, (tuple, list)) or len(points) < 2:
                raise DrawingPlanValidationError(f"LWPOLYLINE entity {entity.entity_id} requires at least two points")
            normalized = tuple(_point(point, f"geometry.points[{index}]", entity.entity_id) for index, point in enumerate(points))
            if len(set(normalized)) < 2:
                raise DrawingPlanValidationError(f"LWPOLYLINE entity {entity.entity_id} requires at least two distinct points")
        elif kind == "INSERT":
            symbol_id = geometry.get("symbol_id")
            if not isinstance(symbol_id, str) or not symbol_id.strip():
                raise DrawingPlanValidationError(f"INSERT entity {entity.entity_id} requires geometry.symbol_id")
            insert = geometry.get("insert")
            if not isinstance(insert, (tuple, list)) or len(insert) not in (2, 3):
                raise DrawingPlanValidationError(
                    f"INSERT entity {entity.entity_id} requires geometry.insert as a 2D or 3D point"
                )
            for axis, coordinate in zip(("x", "y", "z"), insert):
                if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                    raise DrawingPlanValidationError(
                        f"INSERT entity {entity.entity_id} requires numeric geometry.insert.{axis}"
                    )
                if not math.isfinite(float(coordinate)):
                    raise DrawingPlanValidationError(
                        f"INSERT entity {entity.entity_id} geometry.insert.{axis} must be finite"
                    )
            scale = geometry.get("scale", (1.0, 1.0, 1.0))
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                scale_values = (float(scale),)
            elif isinstance(scale, (tuple, list)) and len(scale) in (2, 3):
                scale_values = tuple(scale)
            else:
                raise DrawingPlanValidationError(
                    f"INSERT entity {entity.entity_id} scale must be a scalar or 2D/3D tuple"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) == 0
                for value in scale_values
            ):
                raise DrawingPlanValidationError(
                    f"INSERT entity {entity.entity_id} scale values must be finite and non-zero"
                )
            if "rotation" in geometry:
                _number(geometry, "rotation", entity.entity_id)
        elif kind == "TEXT":
            if not isinstance(geometry.get("text"), str):
                raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} requires string geometry.text")
            _point(geometry.get("insert"), "geometry.insert", entity.entity_id)
            if "height" in geometry and _number(geometry, "height", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} height must be positive")
