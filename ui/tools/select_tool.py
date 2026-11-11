# ============================================================
# GridForge V2
# File: ui/tools/select_tool.py
# Purpose: Canonical SLD selection, box-selection, and drag-move.
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from typing import Any, Optional, Tuple
from math import cos, radians, sin

from core.application.commands.sld_commands import SetSLDNodePositionsCommand, SetSLDNodeTransformsCommand
from ui.equipment.symbol.symbol_base import SymbolBase
from core.application.services.insertion_contract import INSERTION_CONTRACTS
from .electrical_insertion_tool import ElectricalInsertionTool
from ui.core.qt import QPointF
from ui.canvas.canvas_hit import CanvasHitKind, CanvasHitTarget
from .tool_base import ToolBase


class SelectTool(ToolBase):
    """Canonical transient SLD selection and presentation-move tool."""

    TOOL_ID = "select"
    SHIFT_MODIFIER = 0x02000000
    CTRL_MODIFIER = 0x04000000
    META_MODIFIER = 0x10000000
    DRAG_TOLERANCE = 3.0

    def __init__(self, controller: Any, application: Any, selection_manager: Any, snap_system: Any) -> None:
        super().__init__(controller=controller, application=application,
                         selection_manager=selection_manager, snap_system=snap_system)
        self._pressed_object_id: Any = None
        self._pressed_scene_position: Optional[Tuple[float, float]] = None
        self._dragging = False
        self._insertion_active = False
        self._insertion_tool: ElectricalInsertionTool | None = None
        self._insertion_target: dict[str, Any] | None = None
        self._original_drag_position: Tuple[float, float] | None = None
        self._pressed_hit = CanvasHitTarget.none()
        self._route_edit_item: Any | None = None
        self._route_edit_bend_index: int | None = None
        self._route_edit_position: Tuple[float, float] | None = None
        self._grab_mode = False
        self._grab_axis: str | None = None
        self._rotation_pivot: Tuple[float, float] | None = None
        self._pivot_pick_pending = False

    @property
    def tool_id(self) -> str:
        return self.TOOL_ID

    @property
    def name(self) -> str:
        return "Select"

    @property
    def description(self) -> str:
        return "Select, box-select, and drag-move SLD objects."

    def on_activate(self) -> None:
        self._clear_pointer_state()
        self._grab_mode = False
        self._grab_axis = None
        self._rotation_pivot = None
        self._pivot_pick_pending = False

    def on_deactivate(self) -> None:
        self._cancel_transient_insertion()
        self._clear_pointer_state()
        self._grab_mode = False
        self._grab_axis = None
        self._rotation_pivot = None
        self._pivot_pick_pending = False

    def on_mouse_press(self, event: Any) -> bool:
        self._ensure_active()
        self._pressed_hit = self._event_presentation_hit(event)
        self._pressed_object_id = self._selection_object_id(self._pressed_hit, event)
        self._pressed_scene_position = self._xy(self._event_scene_position(event))
        self._dragging = False
        self._route_edit_item = None
        self._route_edit_bend_index = None
        self._route_edit_position = None
        modifiers = self._event_modifiers(event)

        if self._pivot_pick_pending:
            self._rotation_pivot = self._pressed_scene_position
            self._pivot_pick_pending = False
            return True
        if self._grab_mode:
            if not self._selected_transform_nodes():
                self._grab_mode = False
                self._grab_axis = None
                self._clear_pointer_state()
                return False
            return True

        if self._pressed_hit.kind == CanvasHitKind.SIMPLE_WIRE_SEGMENT:
            core_connection_id = self._pressed_hit.core_connection_id
            if core_connection_id is not None:
                self._handle_connection_click(core_connection_id, modifiers)
            if core_connection_id is not None or self._pressed_hit.draft_connection_id is not None:
                self._capture_route_edit_target(self._pressed_hit)
            return True

        if self._pressed_hit.kind == CanvasHitKind.CONNECTION:
            core_connection_id = self._pressed_hit.core_connection_id
            if core_connection_id is not None:
                self._handle_connection_click(core_connection_id, modifiers)
            return True

        if self._pressed_object_id is None:
            if not self._has_additive_modifier(modifiers) and not self._has_toggle_modifier(modifiers):
                self.get_selection_manager().clear()
        else:
            self._handle_node_click(self._pressed_object_id, modifiers)
        return True

    def on_mouse_double_click(self, event: Any) -> bool:
        """Insert a route bend on a draft wire without committing Core topology."""
        self._ensure_active()
        hit = self._event_presentation_hit(event)
        if hit.kind != CanvasHitKind.SIMPLE_WIRE_SEGMENT or hit.closest_point is None:
            return False
        is_draft = hit.draft_connection_id is not None
        if not is_draft and hit.route_ownership != "engineer":
            return False
        item = self._connection_item_for_hit(hit)
        if item is None:
            return False
        insert = getattr(item, "insert_bend", None)
        if not callable(insert):
            return False
        insert(
            len(hit.route_points) if hit.segment_index is None else min(
                hit.segment_index, len(hit.route_points)
            ),
            hit.closest_point[0],
            hit.closest_point[1],
        )
        return True

    def on_mouse_move(self, event: Any) -> bool:
        self._ensure_active()
        if self._pressed_scene_position is None:
            return False
        current = self._xy(self._event_scene_position(event))
        self._dragging = self._dragging or (
            self._distance(self._pressed_scene_position, current) >= self.DRAG_TOLERANCE
        )

        if self._grab_mode:
            return True

        if self._route_edit_bend_index is not None and self._route_edit_item is not None:
            if self._dragging:
                self._route_edit_position = current
            return True

        if not self._dragging or self._pressed_object_id is None:
            return self._dragging
        if self._pressed_hit.kind != CanvasHitKind.NODE:
            return True
        equipment_type = self._equipment_type_for(self._pressed_object_id)
        if equipment_type not in INSERTION_CONTRACTS:
            return True
        target = self._resolve_insertion_target(current)
        if target is not None:
            if not self._insertion_active:
                self._begin_transient_insertion(equipment_type)
            if self._insertion_tool is not None:
                self._insertion_target = dict(target)
                self._insertion_tool.on_mouse_move(self._insertion_event(current, target))
            return True
        if self._insertion_active:
            self._cancel_transient_insertion()
        return True

    def on_mouse_release(self, event: Any) -> bool:
        self._ensure_active()
        if self._pressed_scene_position is None:
            self._clear_pointer_state()
            return False
        end = self._xy(self._event_scene_position(event))
        if self._grab_mode:
            try:
                if self._dragging:
                    start = self._pressed_scene_position
                    # Snap the translation vector, not the absolute pointer
                    # endpoint. Snapping an endpoint and subtracting an
                    # arbitrary press position introduces a fractional delta,
                    # shifting every selected node off the engineering grid.
                    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
                    if self._grab_axis == "x":
                        delta_y = 0.0
                    elif self._grab_axis == "y":
                        delta_x = 0.0
                    snap_grid_point = getattr(self.get_snap_system(), "snap_grid_point", None)
                    if callable(snap_grid_point):
                        snapped_position = snap_grid_point(QPointF(delta_x, delta_y))
                        if snapped_position is not None:
                            delta_x = float(snapped_position.x())
                            delta_y = float(snapped_position.y())
                    self._commit_group_transform(delta_x=delta_x, delta_y=delta_y)
            finally:
                self._grab_mode = False
                self._grab_axis = None
                self._clear_pointer_state()
            return True

        if self._route_edit_bend_index is not None and self._route_edit_item is not None:
            try:
                if self._dragging and self._route_edit_position is not None:
                    item = self._route_edit_item
                    item.set_bend(
                        self._route_edit_bend_index,
                        self._route_edit_position[0],
                        self._route_edit_position[1],
                    )
            finally:
                # Route-edit interaction state is transient even when the
                # Application rejects the proposed presentation command.
                self._clear_pointer_state()
            return True

        if self._dragging:
            if self._pressed_hit.kind == CanvasHitKind.NONE and self._pressed_object_id is None:
                self._box_select(self._pressed_scene_position, end, self._event_modifiers(event))
            elif self._pressed_hit.kind == CanvasHitKind.NODE and self._is_selected(self._pressed_object_id):
                if self._insertion_active and self._insertion_tool is not None:
                    target = self._resolve_insertion_target(end)
                    if target is not None:
                        self._insertion_target = dict(target)
                        accepted = self._insertion_tool.on_mouse_release(self._insertion_event(end, target))
                        self._finish_transient_insertion()
                        self._clear_pointer_state()
                        return bool(accepted)
                self._commit_drag_move(self._pressed_object_id, end)
        self._cancel_transient_insertion()
        self._clear_pointer_state()
        return True

    def on_key_press(self, event: Any) -> bool:
        self._ensure_active()
        key = self._event_key(event)
        if key in {16777223, 127}:  # Qt.Key_Delete
            dispatch = getattr(self.get_controller(), "delete_selection", None)
            if not callable(dispatch):
                # Do not consume Delete in a partially composed tool host.
                return False
            dispatch()
            return True
        if key == 16777216 or key in ("Escape", "escape"):  # Qt.Key_Escape
            return self.on_cancel()
        if key in (71, "G", "g"):  # Grab: arm the next pointer drag for the selected group.
            self._grab_mode = True
            self._grab_axis = None
            self._clear_pointer_state()
            return True
        if self._grab_mode and key in (88, "X", "x"):
            self._grab_axis = "x"
            return True
        if self._grab_mode and key in (89, "Y", "y"):
            self._grab_axis = "y"
            return True
        if key in (80, "P", "p"):  # Pick a transient shared pivot with the next canvas click.
            self._pivot_pick_pending = True
            return True
        if key in (83, "S", "s"):
            factor = (1.0 / 1.1) if self._event_modifiers(event) & self.SHIFT_MODIFIER else 1.1
            self._commit_group_transform(scale_factor=factor)
            return True
        if key in (72, "H", "h"):
            self._commit_group_transform(mirror_x=True)
            return True
        if key in (86, "V", "v"):
            self._commit_group_transform(mirror_y=True)
            return True
        return False

    def on_cancel(self) -> bool:
        had_state = (
            self._pressed_scene_position is not None
            or self._dragging
            or self._insertion_active
            or self._route_edit_bend_index is not None
            or self._grab_mode
            or self._pivot_pick_pending
        )
        self._cancel_transient_insertion()
        self._grab_mode = False
        self._grab_axis = None
        self._pivot_pick_pending = False
        self._rotation_pivot = None
        self._clear_pointer_state()
        return had_state

    def _selected_transform_nodes(self) -> list[Any]:
        presentation = getattr(self.application, "presentation", None)
        model = getattr(presentation, "model", None)
        if model is None:
            return []
        manager = self.get_selection_manager()
        selected_ids = tuple(manager.get_selected_ids(domain=manager.NODE))
        equipment_getter = getattr(model, "get_node_by_equipment_id_optional", None)
        node_getter = getattr(model, "get_node_optional", None)
        nodes = []
        seen = set()
        scene = self._scene()
        items = tuple(scene.items()) if scene is not None and callable(getattr(scene, "items", None)) else ()
        for selected_id in selected_ids:
            selected_key = str(selected_id)
            node = equipment_getter(selected_key) if callable(equipment_getter) else None
            if node is None and callable(node_getter):
                node = node_getter(selected_key)
            if node is None and selected_key.startswith("presentation:") and callable(node_getter):
                node = node_getter(selected_key[len("presentation:"):])
            if node is None:
                # A group transform is all-or-nothing: never silently apply
                # to only the resolvable subset of a user's selection.
                return []
            node_key = str(node.node_id)
            if node_key in seen:
                continue
            seen.add(node_key)
            symbol = node.presentation
            if symbol is None:
                accepted_ids = {selected_key, node_key, str(node.equipment_id)}
                item = next((
                    candidate for candidate in items
                    if str(getattr(candidate, "object_id", "")) in accepted_ids
                    and getattr(candidate, "symbol_instance", None) is not None
                ), None)
                symbol = getattr(item, "symbol_instance", None)
            if symbol is None:
                return []
            try:
                nodes.append((node, SymbolBase.from_dict(symbol.to_dict())))
            except (AttributeError, TypeError, ValueError):
                return []
        if len(nodes) != len(seen) or not nodes:
            return []
        return nodes

    def selected_transform_nodes(self) -> tuple[Any, ...]:
        """Expose the fully preflighted selected node/symbol snapshots to canvas UI."""
        return tuple(self._selected_transform_nodes())

    def selected_transform_count(self) -> int:
        """Return the number of fully resolvable selected SLD nodes."""
        return len(self.selected_transform_nodes())

    def apply_group_transform(
        self,
        *,
        rotation_degrees: float = 0.0,
        mirror_x: bool = False,
        mirror_y: bool = False,
    ) -> bool:
        """Public selection-tool entry point for contextual group transforms."""
        if len(self.selected_transform_nodes()) < 2:
            self.controller.show_status_message(
                "Select at least two resolvable equipment symbols for a group transform."
            )
            return False
        try:
            committed = self._commit_group_transform(
                rotation_degrees=rotation_degrees,
                mirror_x=mirror_x,
                mirror_y=mirror_y,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            self.controller.show_status_message(
                f"Group symbol transform rejected: {exc}"
            )
            return False
        return bool(committed)

    def _commit_group_transform(
        self,
        *,
        rotation_degrees: float = 0.0,
        scale_factor: float = 1.0,
        delta_x: float = 0.0,
        delta_y: float = 0.0,
        mirror_x: bool = False,
        mirror_y: bool = False,
    ) -> bool:
        selected = self._selected_transform_nodes()
        if len(selected) < 2:
            return False
        values = (rotation_degrees, scale_factor, delta_x, delta_y)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise TypeError("group transform values must be numeric")
        if not isinstance(mirror_x, bool) or not isinstance(mirror_y, bool):
            raise TypeError("group mirror flags must be boolean")
        if scale_factor <= 0.0:
            raise ValueError("group scale factor must be positive")
        if mirror_x and mirror_y:
            raise ValueError("apply one group mirror axis per operation")
        nodes = [node for node, _symbol in selected]
        if self._rotation_pivot is None:
            scene = self._scene()
            items = tuple(scene.items()) if scene is not None and callable(getattr(scene, "items", None)) else ()
            object_ids = {
                str(value)
                for node in nodes
                for value in (
                    node.equipment_id,
                    f"presentation:{node.node_id}",
                    node.node_id,
                )
                if value is not None
            }
            bounds = []
            for item in items:
                if str(getattr(item, "object_id", "")) not in object_ids:
                    continue
                # Use the same symbol-bearing graphics item selected for each
                # node; labels/handles sharing object_id must not skew the pivot.
                if getattr(item, "symbol_instance", None) is None:
                    continue
                getter = getattr(item, "sceneBoundingRect", None)
                if not callable(getter):
                    continue
                rect = getter()
                x = float(rect.x())
                y = float(rect.y())
                width = float(rect.width())
                height = float(rect.height())
                bounds.append((x, y, x + width, y + height))
            if bounds:
                pivot = (
                    (min(value[0] for value in bounds) + max(value[2] for value in bounds)) / 2.0,
                    (min(value[1] for value in bounds) + max(value[3] for value in bounds)) / 2.0,
                )
            else:
                xs = [float(node.x) for node in nodes]
                ys = [float(node.y) for node in nodes]
                pivot = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
        else:
            pivot = self._rotation_pivot
        angle = radians(float(rotation_degrees))
        c, sn = cos(angle), sin(angle)
        transforms = []
        for node, symbol in selected:
            dx, dy = float(node.x) - pivot[0], float(node.y) - pivot[1]
            if mirror_x:
                dx = -dx
                # The graphics item applies its local reflection before its
                # rotation. Negating the symbol angle makes the resulting
                # linear transform equal a reflection about the group axis in
                # canvas coordinates, including already-rotated symbols.
                symbol.rotation = (-float(symbol.rotation)) % 360.0
                symbol.set_property(
                    "mirror_x", not bool(symbol.get_property("mirror_x", False))
                )
            if mirror_y:
                dy = -dy
                symbol.rotation = (-float(symbol.rotation)) % 360.0
                symbol.set_property(
                    "mirror_y", not bool(symbol.get_property("mirror_y", False))
                )
            x = pivot[0] + (dx * c - dy * sn) * scale_factor + delta_x
            y = pivot[1] + (dx * sn + dy * c) * scale_factor + delta_y
            symbol.rotation = (symbol.rotation + float(rotation_degrees)) % 360.0
            symbol.scale = symbol.scale * scale_factor
            transforms.append({
                "node_id": str(node.node_id),
                "x": x,
                "y": y,
                "presentation": symbol.to_dict(),
            })
        result = self.application.execute(SetSLDNodeTransformsCommand(transforms=transforms))
        if not getattr(result, "success", False):
            raise RuntimeError(getattr(result, "message", "Failed to transform selected SLD nodes."))
        return True

    def _box_select(self, start: Tuple[float, float], end: Tuple[float, float], modifiers: int) -> None:
        scene = self._scene()
        if scene is None:
            return
        left, right = sorted((start[0], end[0]))
        top, bottom = sorted((start[1], end[1]))
        selected: list[Any] = []
        items_method = getattr(scene, "items", None)
        if not callable(items_method):
            return
        for item in tuple(items_method()):
            object_id = getattr(item, "object_id", None)
            if object_id is None:
                continue
            # Connection graphics may expose object_id for compatibility,
            # but marquee selection is node-only. Wires remain selectable by
            # an explicit pointer hit or modifier-assisted click.
            if (
                getattr(item, "core_connection_id", None) is not None
                or getattr(item, "connection_kind", None) is not None
                or getattr(item, "source_node_id", None) is not None
                or getattr(item, "target_node_id", None) is not None
            ):
                continue
            rect_method = getattr(item, "sceneBoundingRect", None)
            if not callable(rect_method):
                continue
            rect = rect_method()
            rx = rect.x() if callable(getattr(rect, "x", None)) else 0.0
            ry = rect.y() if callable(getattr(rect, "y", None)) else 0.0
            rw = rect.width() if callable(getattr(rect, "width", None)) else 0.0
            rh = rect.height() if callable(getattr(rect, "height", None)) else 0.0
            if not (rx + rw < left or rx > right or ry + rh < top or ry > bottom):
                selected.append(object_id)
        manager = self.get_selection_manager()
        additive = self._has_additive_modifier(modifiers)
        toggle = self._has_toggle_modifier(modifiers)
        if not additive and not toggle:
            manager.clear()
        for object_id in selected:
            if toggle:
                manager.toggle_selection(object_id)
            else:
                manager.add_to_selection(object_id)

    def _commit_drag_move(self, object_id: Any, end: Tuple[float, float]) -> None:
        application = self.application
        presentation = getattr(application, "presentation", None)
        model = getattr(presentation, "model", None)
        if model is None:
            return

        start = self._pressed_scene_position
        if start is None:
            return
        # Keep the pointer's grab offset out of the engineering translation.
        # Snap the delta once so every selected node receives the same
        # grid-aligned movement and relative geometry is preserved.
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        snap_grid_point = getattr(self.get_snap_system(), "snap_grid_point", None)
        if callable(snap_grid_point):
            # This translation-specific API bypasses cursor snap tolerance:
            # the shared delta is grid-aligned even when the pointer grab
            # offset is far from the nearest grid intersection.
            snapped_position = snap_grid_point(QPointF(delta_x, delta_y))
            if snapped_position is not None:
                delta_x = float(snapped_position.x())
                delta_y = float(snapped_position.y())

        manager = self.get_selection_manager()
        selected_ids = tuple(manager.get_selected_ids(domain=manager.NODE))
        if object_id not in selected_ids:
            selected_ids = (object_id,)

        positions = []
        getter = getattr(model, "get_node_by_equipment_id_optional", None)
        node_getter = getattr(model, "get_node_optional", None)
        for selected_id in selected_ids:
            node = getter(str(selected_id)) if callable(getter) else None
            if node is None and callable(node_getter):
                node = node_getter(str(selected_id))
            if node is None:
                continue
            positions.append({
                "node_id": str(node.node_id),
                "x": float(node.x) + delta_x,
                "y": float(node.y) + delta_y,
            })

        if not positions:
            return
        result = application.execute(SetSLDNodePositionsCommand(positions=positions))
        if not getattr(result, "success", False):
            raise RuntimeError(getattr(result, "message", "Failed to move selected SLD nodes."))

    def _equipment_type_for(self, object_id: Any) -> str | None:
        presentation = getattr(self.application, "presentation", None)
        model = getattr(presentation, "model", None)
        if model is None:
            return None
        node = None
        getter = getattr(model, "get_node_by_equipment_id_optional", None)
        if callable(getter):
            node = getter(str(object_id))
        if node is None:
            getter = getattr(model, "get_node_optional", None)
            if callable(getter):
                node = getter(str(object_id))
        if node is None:
            return None
        raw = getattr(node, "element_type", None)
        if raw is None:
            raw = (getattr(node, "properties", {}) or {}).get("element_type")
        return None if raw is None else str(getattr(raw, "value", raw)).strip().lower()

    def _begin_transient_insertion(self, equipment_type: str) -> None:
        tool = self._insertion_tool or self._resolve_insertion_tool()
        if tool is None:
            return
        presentation = getattr(self.application, "presentation", None)
        model = getattr(presentation, "model", None)
        node = None
        if model is not None:
            getter = getattr(model, "get_node_by_equipment_id_optional", None)
            if callable(getter):
                node = getter(str(self._pressed_object_id))
        orientation = float((getattr(node, "properties", {}) or {}).get("rotation", 0.0)) if node is not None else 0.0
        tool.begin(equipment_type, equipment_id=str(self._pressed_object_id), creation_parameters={}, orientation=orientation)
        if not tool.is_active:
            tool.activate()
        self._insertion_tool = tool
        self._insertion_active = True
        self._original_drag_position = self._pressed_scene_position

    def _resolve_insertion_tool(self) -> ElectricalInsertionTool | None:
        manager_getter = getattr(self.controller, "get_tool_manager", None)
        manager = manager_getter() if callable(manager_getter) else getattr(self.controller, "_tool_manager", None)
        if manager is not None:
            for name in ("get_tool", "get", "tool", "find_tool"):
                getter = getattr(manager, name, None)
                if callable(getter):
                    try:
                        candidate = getter(ElectricalInsertionTool.TOOL_ID)
                    except (KeyError, TypeError, ValueError):
                        candidate = None
                    if isinstance(candidate, ElectricalInsertionTool):
                        return candidate
        preview_layer = None
        symbol_registry = None
        for owner in (self.controller, getattr(self.application, "presentation", None)):
            if owner is None:
                continue
            preview_layer = preview_layer or getattr(owner, "preview_layer", None) or getattr(owner, "_preview_layer", None)
            symbol_registry = symbol_registry or getattr(owner, "symbol_registry", None)
        try:
            return ElectricalInsertionTool(controller=self.controller, application=self.application, selection_manager=self.get_selection_manager(), snap_system=self.get_snap_system(), preview_layer=preview_layer, symbol_registry=symbol_registry)
        except (TypeError, ValueError):
            return None

    def _resolve_insertion_target(self, position: Tuple[float, float]) -> dict[str, Any] | None:
        item = self._connection_item_at(position)
        if item is None:
            return None
        resolver = getattr(item, "insertion_target", None)
        if not callable(resolver):
            return None
        try:
            target = resolver(position)
        except (TypeError, ValueError):
            return None
        if not isinstance(target, dict) or not target.get("connection_id"):
            return None
        if str(getattr(item, "connection_kind", "")).upper() != "SIMPLE_WIRE":
            return None
        return dict(target, connection_item=item)

    def _connection_item_at(self, position: Tuple[float, float]) -> Any | None:
        for name in ("connection_item_at", "sld_connection_item_at", "canvas_item_at"):
            resolver = getattr(self.controller, name, None)
            if callable(resolver):
                try:
                    item = resolver(position)
                except (TypeError, ValueError):
                    item = None
                if item is not None and callable(getattr(item, "insertion_target", None)):
                    return item
        scene = self._scene()
        items_method = getattr(scene, "items", None) if scene is not None else None
        if not callable(items_method):
            return None
        try:
            items = tuple(items_method(QPointF(float(position[0]), float(position[1]))))
        except (TypeError, ValueError):
            try:
                items = tuple(items_method())
            except TypeError:
                return None
        for item in items:
            if callable(getattr(item, "insertion_target", None)) and str(getattr(item, "connection_kind", "")).upper() == "SIMPLE_WIRE":
                return item
        return None

    @staticmethod
    def _insertion_event(position: Tuple[float, float], target: dict[str, Any]) -> dict[str, Any]:
        return {"scene_position": position, "connection_item": target.get("connection_item")}

    def _finish_transient_insertion(self) -> None:
        tool = self._insertion_tool
        self._insertion_active = False
        self._insertion_target = None
        self._original_drag_position = None
        if tool is not None and tool.is_active:
            tool.deactivate()
        self._insertion_tool = None

    def _cancel_transient_insertion(self) -> None:
        tool = self._insertion_tool
        if tool is not None:
            try:
                if tool.is_active:
                    tool.on_cancel()
            finally:
                if tool.is_active:
                    tool.deactivate()
        self._insertion_active = False
        self._insertion_target = None
        self._original_drag_position = None

    def _scene(self) -> Any:
        return getattr(self.get_selection_manager(), "scene", None)

    def _is_selected(self, object_id: Any) -> bool:
        return bool(self.get_selection_manager().is_selected(object_id))

    def _handle_node_click(self, object_id: Any, modifiers: int) -> None:
        manager = self.get_selection_manager()
        if self._has_toggle_modifier(modifiers):
            manager.toggle_selection(object_id, domain=manager.NODE)
        elif self._has_additive_modifier(modifiers):
            manager.add_to_selection(object_id, domain=manager.NODE)
        else:
            manager.select_single(object_id, domain=manager.NODE)

    def _handle_connection_click(self, core_connection_id: str, modifiers: int) -> None:
        manager = self.get_selection_manager()
        if self._has_toggle_modifier(modifiers):
            manager.toggle_selection(core_connection_id, domain=manager.CONNECTION)
        elif self._has_additive_modifier(modifiers):
            manager.add_to_selection(core_connection_id, domain=manager.CONNECTION)
        else:
            manager.select_single(core_connection_id, domain=manager.CONNECTION)

    def _capture_route_edit_target(self, hit: CanvasHitTarget) -> None:
        if hit.kind != CanvasHitKind.SIMPLE_WIRE_SEGMENT:
            return
        if hit.route_ownership != "engineer":
            return
        route_points = hit.route_points
        if not route_points:
            return
        segment_index = hit.segment_index
        if segment_index is None:
            return
        # A routed polyline is [source, bend0, ..., bendN, target].
        # A segment therefore borders one existing bend; choose the adjacent
        # bend deterministically without inspecting the graphics scene again.
        bend_index = segment_index if segment_index < len(route_points) else len(route_points) - 1
        self._route_edit_item = self._connection_item_for_hit(hit)
        if self._route_edit_item is None:
            return
        self._route_edit_bend_index = bend_index
        self._route_edit_position = hit.closest_point

    def _connection_item_for_hit(self, hit: CanvasHitTarget) -> Any | None:
        manager = self.get_selection_manager()
        scene = getattr(manager, "scene", None)
        if scene is None:
            return None
        items_method = getattr(scene, "items", None)
        if not callable(items_method):
            return None
        for item in tuple(items_method()):
            if (
                getattr(item, "presentation_id", None) == hit.presentation_id
                and getattr(item, "core_connection_id", None) == hit.core_connection_id
                and (
                    hit.draft_connection_id is None
                    or getattr(item, "projection_source", None) == "draft_network"
                )
            ):
                return item
        return None

    @classmethod
    def _has_additive_modifier(cls, modifiers: int) -> bool:
        return bool(modifiers & (cls.SHIFT_MODIFIER | cls.META_MODIFIER))

    @classmethod
    def _has_toggle_modifier(cls, modifiers: int) -> bool:
        return bool(modifiers & (cls.CTRL_MODIFIER | cls.META_MODIFIER))

    @staticmethod
    def _event_presentation_hit(event: Any) -> CanvasHitTarget:
        value = getattr(event, "presentation_hit", None)
        if value is None:
            value = getattr(event, "hit_target", None)
        if isinstance(event, dict):
            value = event.get("presentation_hit", event.get("hit_target", value))
        if isinstance(value, CanvasHitTarget):
            return value
        # Legacy raw/dict events expose only object_id. Preserve their node
        # selection semantics without attempting to reconstruct scene hits.
        object_id = SelectTool._event_object_id(event)
        return CanvasHitTarget.node(object_id) if object_id is not None else CanvasHitTarget.none()

    @staticmethod
    def _selection_object_id(hit: CanvasHitTarget, event: Any) -> Any:
        if hit.kind in {CanvasHitKind.CONNECTION, CanvasHitKind.SIMPLE_WIRE_SEGMENT}:
            return hit.core_connection_id
        if hit.kind == CanvasHitKind.NODE:
            return hit.object_id
        return SelectTool._event_object_id(event)

    @staticmethod
    def _event_object_id(event: Any) -> Any:
        value = getattr(event, "object_id", None)
        return event.get("object_id", value) if isinstance(event, dict) else value

    @staticmethod
    def _event_scene_position(event: Any) -> Any:
        value = getattr(event, "scene_position", None)
        if isinstance(event, dict):
            value = event.get("scene_position", value)
        return value if value is not None else getattr(event, "position", None)

    @staticmethod
    def _event_modifiers(event: Any) -> int:
        value = getattr(event, "modifiers", 0)
        if callable(value):
            value = value()
        if isinstance(event, dict):
            value = event.get("modifiers", value)
        return int(value or 0)

    @staticmethod
    def _event_key(event: Any) -> Any:
        value = getattr(event, "key", None)
        if callable(value):
            value = value()
        return event.get("key", value) if isinstance(event, dict) else value

    @staticmethod
    def _xy(value: Any) -> Tuple[float, float]:
        if value is None:
            raise ValueError("Canvas event position is required.")
        x = value.x() if callable(getattr(value, "x", None)) else value[0]
        y = value.y() if callable(getattr(value, "y", None)) else value[1]
        return float(x), float(y)

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        return (dx * dx + dy * dy) ** 0.5

    def _clear_pointer_state(self) -> None:
        self._pressed_object_id = None
        self._pressed_scene_position = None
        self._dragging = False
        self._pressed_hit = CanvasHitTarget.none()
        self._route_edit_item = None
        self._route_edit_bend_index = None
        self._route_edit_position = None

    def get_state(self) -> dict[str, Any]:
        state = super().get_state()
        state.update({
            "pressed_object_id": self._pressed_object_id,
            "pressed_scene_position": self._pressed_scene_position,
            "dragging": self._dragging,
            "insertion_active": self._insertion_active,
            "insertion_target": dict(self._insertion_target or {}),
            "original_drag_position": self._original_drag_position,
            "pressed_hit": self._pressed_hit,
            "route_edit_active": self._route_edit_bend_index is not None,
            "route_edit_bend_index": self._route_edit_bend_index,
            "route_edit_position": self._route_edit_position,
            "grab_mode": self._grab_mode,
            "grab_axis": self._grab_axis,
            "rotation_pivot": self._rotation_pivot,
            "pivot_pick_pending": self._pivot_pick_pending,
        })
        return state


__all__ = ["SelectTool"]
