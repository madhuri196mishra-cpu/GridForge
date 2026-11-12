"""Fail-closed validation for backend-neutral drawing plans."""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from .control_coordinate_transform import ControlCoordinateTransform

from .drawing_plan import DrawingPlan


class DrawingPlanValidationError(ValueError):
    """Raised when a drawing plan is unsafe or internally inconsistent."""


_SUPPORTED_KINDS = {"LINE", "CIRCLE", "ARC", "TEXT", "INSERT", "LWPOLYLINE"}
_FORBIDDEN_LAYER_CHARS = frozenset('<>/\\":;?*|=,')


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


def _keys(geometry: Mapping[str, object], required: set[str], optional: set[str], entity_id: str) -> None:
    actual = set(geometry)
    missing = required - actual
    extra = actual - required - optional
    if missing:
        raise DrawingPlanValidationError(
            f"Entity {entity_id} is missing geometry fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise DrawingPlanValidationError(
            f"Entity {entity_id} has unsupported geometry fields: {', '.join(sorted(extra))}"
        )


def _number(geometry: Mapping[str, object], key: str, entity_id: str) -> float:
    value = geometry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DrawingPlanValidationError(f"Entity {entity_id} requires numeric geometry.{key}")
    number = float(value)
    if not math.isfinite(number):
        raise DrawingPlanValidationError(f"Entity {entity_id} geometry.{key} must be finite")
    return number


def _point(value: object, path: str, entity_id: str, *, dimensions: tuple[int, ...] = (2,)) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) not in dimensions:
        expected = " or ".join(str(size) for size in dimensions)
        raise DrawingPlanValidationError(
            f"Entity {entity_id} requires {path} as a {expected}D point"
        )
    result = []
    for axis, coordinate in zip(("x", "y", "z"), value):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise DrawingPlanValidationError(f"Entity {entity_id} requires numeric {path}.{axis}")
        if not math.isfinite(float(coordinate)):
            raise DrawingPlanValidationError(f"Entity {entity_id} {path}.{axis} must be finite")
        result.append(float(coordinate))
    return tuple(result)


def _validate_layer(entity_id: str, layer: str) -> None:
    if len(layer) > 255 or any(ord(char) < 32 or char in _FORBIDDEN_LAYER_CHARS for char in layer):
        raise DrawingPlanValidationError(
            f"Entity {entity_id} has invalid CAD layer name {layer!r}"
        )


def validate_drawing_plan(plan: DrawingPlan) -> None:
    """Validate identity, primitive fields and geometry before backend writes."""
    if not isinstance(plan, DrawingPlan):
        raise TypeError("plan must be a DrawingPlan")
    if plan.schema_version != 1:
        raise DrawingPlanValidationError(f"Unsupported drawing plan schema: {plan.schema_version}")
    if not plan.coordinate_system.strip() or not plan.units.strip():
        raise DrawingPlanValidationError("Drawing plan must declare coordinate_system and units")
    if plan.source_kind == "sld_export_snapshot" and (
        plan.coordinate_system != "world" or plan.units != "mm"
    ):
        raise DrawingPlanValidationError(
            "SLD export plans must contain transformed world-coordinate geometry in millimetres"
        )
    if plan.source_kind == "control_program_read_model" and (
        plan.coordinate_system != "world" or plan.units != "mm"
    ):
        raise DrawingPlanValidationError(
            "Control export plans must contain reviewed world-coordinate geometry in millimetres"
        )
    if plan.source_kind == "sld_export_snapshot" and not plan.entities:
        raise DrawingPlanValidationError(
            "SLD export plans require transformed entities carrying reviewed coordinate provenance"
        )
    if plan.source_kind == "control_program_read_model" and not plan.entities:
        raise DrawingPlanValidationError(
            "Control export plans require transformed entities carrying reviewed coordinate provenance"
        )

    control_mapping_fingerprint: str | None = None
    sld_mapping_provenance: dict[str, object] | None = None
    seen: set[str] = set()
    for entity in plan.entities:
        if entity.entity_id in seen:
            raise DrawingPlanValidationError(f"Duplicate drawing entity ID: {entity.entity_id}")
        seen.add(entity.entity_id)
        _validate_layer(entity.entity_id, entity.layer)
        kind = entity.kind.upper()
        if kind not in _SUPPORTED_KINDS:
            raise DrawingPlanValidationError(
                f"Unsupported drawing primitive {entity.kind!r} on {entity.entity_id}"
            )
        geometry = entity.geometry
        _finite_numbers(geometry, f"entities[{entity.entity_id}].geometry")

        if plan.source_kind == "sld_export_snapshot":
            provenance = entity.metadata.get("coordinate_transform")
            required_provenance = {
                "mapping_id", "mapping_version", "source_coordinate_system",
                "source_units", "target_coordinate_system", "target_units",
                "millimetres_per_source_unit", "world_origin_mm",
                "rotation_degrees", "reviewer", "reviewed_on", "review_reference",
            }
            if not isinstance(provenance, Mapping) or set(provenance) != required_provenance:
                raise DrawingPlanValidationError(
                    f"SLD entity {entity.entity_id} has incomplete or unsupported coordinate-transform provenance"
                )
            if (
                provenance.get("source_coordinate_system") != "sld-document-local"
                or not isinstance(provenance.get("source_units"), str)
                or not provenance.get("source_units", "").strip()
                or provenance.get("target_coordinate_system") != "world"
                or provenance.get("target_units") != "mm"
            ):
                raise DrawingPlanValidationError(
                    f"SLD entity {entity.entity_id} has an incompatible coordinate mapping declaration"
                )
            if not all(
                isinstance(provenance.get(field), str) and str(provenance[field]).strip()
                for field in ("mapping_id", "mapping_version", "reviewer", "review_reference")
            ):
                raise DrawingPlanValidationError(
                    f"SLD entity {entity.entity_id} has incomplete coordinate review evidence"
                )
            try:
                date.fromisoformat(str(provenance["reviewed_on"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise DrawingPlanValidationError(
                    f"SLD entity {entity.entity_id} has an invalid coordinate review date"
                ) from exc
            scale = provenance.get("millimetres_per_source_unit")
            rotation = provenance.get("rotation_degrees")
            origin = provenance.get("world_origin_mm")
            if (
                isinstance(scale, bool) or not isinstance(scale, (int, float))
                or not math.isfinite(float(scale)) or float(scale) <= 0
                or isinstance(rotation, bool) or not isinstance(rotation, (int, float))
                or not math.isfinite(float(rotation))
            ):
                raise DrawingPlanValidationError(
                    f"SLD entity {entity.entity_id} has invalid coordinate mapping scale or rotation"
                )
            _point(origin, "coordinate_transform.world_origin_mm", entity.entity_id)
            normalized_provenance = dict(provenance)
            if sld_mapping_provenance is None:
                sld_mapping_provenance = normalized_provenance
            elif normalized_provenance != sld_mapping_provenance:
                raise DrawingPlanValidationError(
                    "SLD drawing contains conflicting coordinate mapping provenance"
                )

        if plan.source_kind == "control_program_read_model":
            provenance = entity.metadata.get("coordinate_transform")
            if not isinstance(provenance, Mapping):
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} lacks reviewed coordinate-transform provenance"
                )
            required_provenance = {
                "mapping_id", "mapping_version", "source_coordinate_system",
                "source_units", "target_coordinate_system", "target_units",
                "world_origin_x_mm", "world_origin_y_mm", "rotation_degrees",
                "reviewer", "reviewed_on", "review_reference", "mapping_fingerprint",
            }
            if set(provenance) != required_provenance:
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} has incomplete or unsupported coordinate provenance fields"
                )
            if (
                provenance.get("source_coordinate_system") != "control-ladder-local"
                or provenance.get("source_units") != "mm"
                or provenance.get("target_coordinate_system") != "world"
                or provenance.get("target_units") != "mm"
            ):
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} has an incompatible coordinate mapping declaration"
                )
            try:
                reviewed_on = date.fromisoformat(str(provenance["reviewed_on"]))
                calculated_fingerprint = ControlCoordinateTransform.fingerprint_from_provenance(provenance)
            except (KeyError, TypeError, ValueError) as exc:
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} has invalid coordinate mapping provenance"
                ) from exc
            if not all(
                isinstance(provenance.get(field), str) and str(provenance[field]).strip()
                for field in ("mapping_id", "mapping_version", "reviewer", "review_reference")
            ):
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} has incomplete coordinate review evidence"
                )
            for field in ("world_origin_x_mm", "world_origin_y_mm", "rotation_degrees"):
                value = provenance.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise DrawingPlanValidationError(
                        f"Control entity {entity.entity_id} has invalid coordinate mapping {field}"
                    )
            fingerprint = provenance.get("mapping_fingerprint")
            if not isinstance(fingerprint, str) or fingerprint != calculated_fingerprint:
                raise DrawingPlanValidationError(
                    f"Control entity {entity.entity_id} coordinate mapping fingerprint mismatch"
                )
            if control_mapping_fingerprint is None:
                control_mapping_fingerprint = fingerprint
            elif fingerprint != control_mapping_fingerprint:
                raise DrawingPlanValidationError(
                    "Control drawing contains mixed coordinate mapping fingerprints"
                )

        if kind == "LINE":
            _keys(geometry, {"start", "end"}, set(), entity.entity_id)
            start = _point(geometry["start"], "geometry.start", entity.entity_id)
            end = _point(geometry["end"], "geometry.end", entity.entity_id)
            if start == end:
                raise DrawingPlanValidationError(f"LINE entity {entity.entity_id} must have distinct endpoints")

        elif kind == "CIRCLE":
            _keys(geometry, {"center", "radius"}, set(), entity.entity_id)
            _point(geometry["center"], "geometry.center", entity.entity_id)
            if _number(geometry, "radius", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"CIRCLE entity {entity.entity_id} radius must be positive")

        elif kind == "ARC":
            _keys(geometry, {"center", "radius", "start_angle", "end_angle"}, set(), entity.entity_id)
            _point(geometry["center"], "geometry.center", entity.entity_id)
            if _number(geometry, "radius", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"ARC entity {entity.entity_id} radius must be positive")
            start = _number(geometry, "start_angle", entity.entity_id)
            end = _number(geometry, "end_angle", entity.entity_id)
            if start == end:
                raise DrawingPlanValidationError(f"ARC entity {entity.entity_id} start_angle and end_angle must differ")

        elif kind == "LWPOLYLINE":
            _keys(geometry, {"points"}, {"closed"}, entity.entity_id)
            if "closed" in geometry and not isinstance(geometry["closed"], bool):
                raise DrawingPlanValidationError(f"LWPOLYLINE entity {entity.entity_id} closed must be boolean")
            points = geometry["points"]
            if not isinstance(points, (tuple, list)) or len(points) < 2:
                raise DrawingPlanValidationError(f"LWPOLYLINE entity {entity.entity_id} requires at least two points")
            normalized = tuple(
                _point(point, f"geometry.points[{index}]", entity.entity_id)
                for index, point in enumerate(points)
            )
            if len(set(normalized)) < 2:
                raise DrawingPlanValidationError(
                    f"LWPOLYLINE entity {entity.entity_id} requires at least two distinct points"
                )

        elif kind == "INSERT":
            _keys(geometry, {"symbol_id", "insert"}, {"scale", "rotation"}, entity.entity_id)
            symbol_id = geometry["symbol_id"]
            if not isinstance(symbol_id, str) or not symbol_id.strip():
                raise DrawingPlanValidationError(f"INSERT entity {entity.entity_id} requires geometry.symbol_id")
            _point(geometry["insert"], "geometry.insert", entity.entity_id, dimensions=(2, 3))
            scale = geometry.get("scale", (1.0, 1.0, 1.0))
            if isinstance(scale, (int, float)) and not isinstance(scale, bool):
                scale_values = (scale,)
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
            _keys(geometry, {"text", "insert"}, {"height", "rotation"}, entity.entity_id)
            if not isinstance(geometry["text"], str):
                raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} requires string geometry.text")
            if "\x00" in geometry["text"]:
                raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} contains a NUL character")
            _point(geometry["insert"], "geometry.insert", entity.entity_id)
            if "height" in geometry and _number(geometry, "height", entity.entity_id) <= 0:
                raise DrawingPlanValidationError(f"TEXT entity {entity.entity_id} height must be positive")
            if "rotation" in geometry:
                _number(geometry, "rotation", entity.entity_id)


__all__ = ["DrawingPlanValidationError", "validate_drawing_plan"]
