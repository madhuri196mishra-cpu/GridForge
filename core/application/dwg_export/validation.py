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
        if kind == "INSERT" and not entity.geometry.get("block_name"):
            raise DrawingPlanValidationError(f"INSERT entity {entity.entity_id} requires geometry.block_name")
        if kind == "TEXT" and "text" not in entity.geometry:
            raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} requires geometry.text")
        _finite_numbers(entity.geometry, f"entities[{entity.entity_id}].geometry")
