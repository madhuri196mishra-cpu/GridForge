# ============================================================
# File: ui/items/equipment_item.py
# GridForge V2 — Symbol-aware Equipment Graphics Projection
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from collections.abc import Mapping

from math import isfinite
from typing import Any, Optional

from ui.core.qt import QBrush, QFont, QGraphicsItem, QLineF, QPainter, QPainterPath, QPainterPathStroker, QPen, QRectF, QPointF, QTransform, Qt, QColor, Signal
from ui.equipment.equipment_base import EquipmentBase
from ui.equipment.symbol.symbol_base import SymbolBase
from ui.equipment.symbol.symbol_definition import SymbolDefinition
from ui.styling.presentation_style import PresentationState, VisualState, visual_brush, visual_font, visual_pen, token_color

from .base_item import BaseItem


_PRIMITIVE_FIELDS = {
    "line": ("x1", "y1", "x2", "y2"),
    "rect": ("x", "y", "width", "height"),
    "circle": ("cx", "cy", "r"),
    "text": ("x", "y", "width", "height", "text"),
}


def _validate_symbol_primitives(definition: SymbolDefinition) -> None:
    """Reject unsupported or malformed primitives before Qt invokes paint()."""
    for index, primitive in enumerate(definition.primitives):
        if not isinstance(primitive, Mapping):
            raise TypeError(
                f"Symbol {definition.symbol_id!r} primitive {index} must be a mapping."
            )
        kind = primitive.get("kind")
        if not isinstance(kind, str):
            raise ValueError(
                f"Symbol {definition.symbol_id!r} primitive {index} kind must be a string."
            )
        fields = _PRIMITIVE_FIELDS.get(kind)
        if fields is None:
            raise ValueError(
                f"Symbol {definition.symbol_id!r} primitive {index} has unsupported kind {kind!r}."
            )
        missing = tuple(field for field in fields if field not in primitive)
        if missing:
            raise ValueError(
                f"Symbol {definition.symbol_id!r} primitive {index} ({kind}) "
                f"is missing required fields: {', '.join(missing)}."
            )
        for field in fields:
            if kind == "text" and field == "text":
                if not isinstance(primitive[field], str):
                    raise TypeError(
                        f"Symbol {definition.symbol_id!r} primitive {index} text must be a string."
                    )
                continue
            value = primitive[field]
            if isinstance(value, bool):
                raise TypeError(
                    f"Symbol {definition.symbol_id!r} primitive {index} field {field!r} must be numeric."
                )
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"Symbol {definition.symbol_id!r} primitive {index} field {field!r} must be numeric."
                ) from exc
            if not isfinite(number):
                raise ValueError(
                    f"Symbol {definition.symbol_id!r} primitive {index} field {field!r} must be finite."
                )
            if field in {"width", "height", "r"} and number <= 0:
                raise ValueError(
                    f"Symbol {definition.symbol_id!r} primitive {index} field {field!r} must be greater than zero."
                )


class EquipmentItem(BaseItem):
    """Presentation-only graphics projection for one canonical SLD symbol."""

    position_changed = Signal(object)

    def __init__(
        self,
        object_id: Any,
        element_type: str,
        *,
        position: object = None,
        symbol_definition: SymbolDefinition,
        equipment: EquipmentBase,
        symbol_instance: SymbolBase | None = None,
        parent: Optional[BaseItem] = None,
    ) -> None:
        if not isinstance(element_type, str) or not element_type.strip():
            raise ValueError("element_type must be a non-empty string")
        if not isinstance(symbol_definition, SymbolDefinition):
            raise TypeError("symbol_definition must be a SymbolDefinition")
        _validate_symbol_primitives(symbol_definition)
        for primitive in symbol_definition.primitives:
            if "stroke_width" in primitive:
                width = primitive["stroke_width"]
                if (
                    isinstance(width, bool)
                    or not isinstance(width, (int, float))
                    or not isfinite(float(width))
                    or float(width) <= 0
                ):
                    raise ValueError(
                        "primitive stroke_width must be a finite positive number"
                    )
                if primitive.get("kind") != "line":
                    raise ValueError(
                        "stroke_width is currently supported only for line primitives"
                    )
        if not isinstance(equipment, EquipmentBase):
            raise TypeError("equipment must be an EquipmentBase")
        super().__init__(object_id, parent)
        self._element_type = element_type
        self._symbol_definition = symbol_definition
        self._hit_shape = self._build_hit_shape()
        self._equipment = equipment
        if symbol_instance is None:
            symbol_instance = SymbolBase(
                symbol_id=symbol_definition.symbol_id,
                definition_id=symbol_definition.symbol_id,
            )
        if symbol_instance.symbol_id != symbol_definition.symbol_id:
            raise ValueError("symbol_instance must resolve to the supplied SymbolDefinition.")
        if symbol_instance.representation_id != "symbol":
            raise ValueError(
                f"Unsupported SLD representation: {symbol_instance.representation_id!r}"
            )
        self._symbol_instance = SymbolBase.from_dict(symbol_instance.to_dict())
        self._visual_state = VisualState.NORMAL
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.set_presentation_state(PresentationState(visual_state=VisualState.NORMAL, readout=f"{self._element_type}: {self.object_id}"))
        self.setAcceptHoverEvents(True)
        mirror_x = bool(self._symbol_instance.get_property("mirror_x", False))
        mirror_y = bool(self._symbol_instance.get_property("mirror_y", False))
        # Apply reflection once to the graphics transform. Drawing and
        # mapToScene() then share the same coordinate transform, so terminal
        # anchors remain attached to the rendered symbol for every mirror state.
        self.setTransform(
            QTransform.fromScale(
                -1.0 if mirror_x else 1.0,
                -1.0 if mirror_y else 1.0,
            )
        )
        self.setScale(self._symbol_instance.scale)
        self.setRotation(self._symbol_instance.rotation)
        self.setVisible(self._symbol_instance.visible)
        if position is not None:
            self.setPos(position)

    def itemChange(self, change: Any, value: Any) -> Any:
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and value is not None:
            self.position_changed.emit(QPointF(float(value.x()), float(value.y())))
        return result

    @property
    def element_type(self) -> str:
        return self._element_type

    @property
    def symbol_definition(self) -> SymbolDefinition:
        return self._symbol_definition

    @property
    def equipment(self) -> EquipmentBase:
        return self._equipment

    @property
    def symbol_instance(self) -> SymbolBase:
        return self._symbol_instance

    @property
    def visual_state(self) -> VisualState:
        return self._visual_state

    def set_visual_state(self, state: VisualState | str) -> None:
        normalized = state if isinstance(state, VisualState) else VisualState(str(state).lower())
        self._visual_state = normalized
        current = self.presentation_state
        self.set_presentation_state(PresentationState(visual_state=normalized, status=current.status, tool_state=current.tool_state, readout=current.readout or f"{self._element_type}: {self.object_id}"))

    def set_invalid(self, invalid: bool = True) -> None:
        self.set_visual_state(VisualState.INVALID if invalid else VisualState.NORMAL)

    def snap_points(self):
        points = []
        for terminal in self._equipment.terminals:
            local_anchor = self._symbol_definition.get_terminal_anchor(terminal.terminal_name)
            # The item's transform already applies symbol reflections;
            # do not mirror local anchor coordinates a second time.
            scene_point = self.mapToScene(
                QPointF(float(local_anchor[0]), float(local_anchor[1]))
            )
            points.append({
                "position": scene_point,
                "object_id": self.object_id,
                "terminal_id": terminal.terminal_id,
                "terminal_name": terminal.terminal_name,
            })
        return tuple(points)

    def _build_hit_shape(self) -> QPainterPath:
        """Build a local-coordinate hit shape from the actual symbol primitives.

        QGraphicsItem applies the item's rotation, scale, and reflection to
        this path for scene hit testing. Keeping the path in symbol-local
        coordinates makes visual geometry, hit testing, and transformed
        terminal anchors share the same transform without maintaining a
        second scene-space bounding frame.
        """
        hit_shape = QPainterPath()
        for primitive in self._symbol_definition.primitives:
            kind = primitive.get("kind")
            geometry = QPainterPath()
            if kind == "line":
                geometry.moveTo(float(primitive["x1"]), float(primitive["y1"]))
                geometry.lineTo(float(primitive["x2"]), float(primitive["y2"]))
                visual_width = float(primitive.get("stroke_width", 1.8))
            elif kind == "rect":
                geometry.addRect(QRectF(
                    float(primitive["x"]),
                    float(primitive["y"]),
                    float(primitive["width"]),
                    float(primitive["height"]),
                ))
                # Symbol rectangles are painted with the active symbol
                # brush, so their complete filled area is selectable.
                hit_shape.addPath(geometry)
                continue
            elif kind == "circle":
                radius = float(primitive["r"])
                geometry.addEllipse(QRectF(
                    float(primitive["cx"]) - radius,
                    float(primitive["cy"]) - radius,
                    radius * 2.0,
                    radius * 2.0,
                ))
                # Match the filled ellipse used by EquipmentItem.paint().
                hit_shape.addPath(geometry)
                continue
            elif kind == "text":
                # Text occupies its declared label rectangle rather than a
                # stroked outline; include the label area in the hit shape.
                hit_shape.addRect(QRectF(
                    float(primitive["x"]),
                    float(primitive["y"]),
                    float(primitive["width"]),
                    float(primitive["height"]),
                ))
                continue
            else:
                continue

            stroker = QPainterPathStroker()
            # Retain a practical pointer target while avoiding the broad
            # logical rectangle that previously made empty symbol space
            # selectable. Qt transforms this local hit path with the item.
            stroker.setWidth(max(4.0, visual_width + 2.0))
            hit_shape.addPath(stroker.createStroke(geometry))
        return hit_shape

    def shape(self) -> QPainterPath:
        """Return the symbol's precise local hit geometry."""
        return QPainterPath(self._hit_shape)

    def boundingRect(self) -> QRectF:
        """Return bounds for the actual hit/paint geometry in local space.

        QGraphicsItem maps these bounds through its current transform for
        scene indexing, so rotated and mirrored symbols retain aligned
        selection geometry without a manually cached axis-aligned frame.
        """
        if self._hit_shape.isEmpty():
            margin = 2.0
            return QRectF(
                -self._symbol_definition.width / 2 - margin,
                -self._symbol_definition.height / 2 - margin,
                self._symbol_definition.width + 2 * margin,
                self._symbol_definition.height + 2 * margin,
            )
        return self._hit_shape.boundingRect().adjusted(-0.5, -0.5, 0.5, 0.5)

    def _effective_state(self) -> VisualState:
        if self._visual_state not in {VisualState.NORMAL, VisualState.HOVER}:
            return self._visual_state
        if self.isSelected():
            return VisualState.SELECTED
        if self.isUnderMouse():
            return VisualState.HOVER
        return VisualState.NORMAL

    def paint(self, painter: QPainter, option: Any = None, widget: Any = None) -> None:
        del option, widget
        state = self._effective_state()
        role = "protection" if self._element_type.lower() == "relay" else "symbol"
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(visual_pen(role, state, width=1.8))
        painter.setBrush(visual_brush(role, state))
        painter.setFont(visual_font("engineering"))
        base_pen = visual_pen(role, state, width=1.8)
        for primitive in self._symbol_definition.primitives:
            kind = primitive.get("kind")
            if kind == "line":
                line_width = float(primitive.get("stroke_width", 1.8))
                painter.setPen(visual_pen(role, state, width=line_width))
                painter.drawLine(
                    QLineF(
                        float(primitive["x1"]), float(primitive["y1"]),
                        float(primitive["x2"]), float(primitive["y2"]),
                    )
                )
            elif kind == "rect":
                painter.setPen(base_pen)
                painter.drawRect(
                    QRectF(
                        float(primitive["x"]), float(primitive["y"]),
                        float(primitive["width"]), float(primitive["height"]),
                    )
                )
            elif kind == "circle":
                painter.setPen(base_pen)
                radius = float(primitive["r"])
                painter.drawEllipse(
                    QRectF(
                        float(primitive["cx"]) - radius,
                        float(primitive["cy"]) - radius,
                        radius * 2.0,
                        radius * 2.0,
                    )
                )
            elif kind == "text":
                # IEEE/ANSI 315 drafting practice says letters and numbers
                # that form part of a symbol must not be mirror-imaged. The
                # graphics-item transform mirrors the geometry, so cancel its
                # reflection locally around the text rectangle's centre.
                painter.setPen(QPen(token_color("text_inverse")))
                painter.setFont(visual_font("engineering"))
                text_x = float(primitive["x"])
                text_y = float(primitive["y"])
                text_width = float(primitive["width"])
                text_height = float(primitive["height"])
                text_rect = QRectF(text_x, text_y, text_width, text_height)
                mirror_x = bool(self._symbol_instance.get_property("mirror_x", False))
                mirror_y = bool(self._symbol_instance.get_property("mirror_y", False))
                # Current item transform is scale-X reflection, plus a 180°
                # rotation when both mirror flags are set. Its net reflection
                # is horizontal for X-only and vertical for X+Y.
                reflect_text_x = mirror_x and not mirror_y
                reflect_text_y = mirror_x and mirror_y
                painter.save()
                if reflect_text_x or reflect_text_y:
                    center_x = text_x + text_width / 2.0
                    center_y = text_y + text_height / 2.0
                    painter.translate(center_x, center_y)
                    painter.scale(
                        -1.0 if reflect_text_x else 1.0,
                        -1.0 if reflect_text_y else 1.0,
                    )
                    painter.translate(-center_x, -center_y)
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    str(primitive["text"]),
                )
                painter.restore()

    def hoverEnterEvent(self, event: Any) -> None:
        if self._visual_state == VisualState.NORMAL:
            self.set_visual_state(VisualState.HOVER)
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: Any) -> None:
        if self._visual_state == VisualState.HOVER:
            self.set_visual_state(VisualState.NORMAL)
        self.update()
        super().hoverLeaveEvent(event)

    def get_state(self) -> dict[str, Any]:
        state = super().get_state()
        state.update({
            "element_type": self._element_type,
            "mirror_x": bool(self._symbol_instance.get_property("mirror_x", False)),
            "mirror_y": bool(self._symbol_instance.get_property("mirror_y", False)),
            "symbol_id": self._symbol_instance.symbol_id,
            "representation_id": self._symbol_instance.representation_id,
            "scale": self._symbol_instance.scale,
            "rotation": self._symbol_instance.rotation,
            "visible": self._symbol_instance.visible,
            "properties": dict(self._symbol_instance.properties),
            "visual_state": self._visual_state.value,
        })
        return state


__all__ = ["EquipmentItem"]
