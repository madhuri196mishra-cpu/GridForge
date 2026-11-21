"""
GridForge V2 — Canvas Graphics View
===================================

File:
    ui/canvas/graphics_view.py

Purpose
-------
Thin Qt viewport boundary for the GridForge SLD canvas.

Architectural role
------------------
GraphicsView is a Qt viewport boundary. Canvas services are composed
outside the view and injected into it.

It does not:
    - create Presentation controllers;
    - create ToolManager;
    - create InteractionManager;
    - create NavigationController;
    - own tool lifecycle;
    - perform electrical calculations;
    - modify Core state;
    - construct domain objects.

The Application layer is the explicit Core↔UI integration boundary.
GraphicsView does not bypass that boundary.

Author:
    Subhendu Mishra
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ui.core.qt import QAction, QBrush, QGraphicsScene, QGraphicsView, QMenu, QPainter, QPointF, QRectF, Qt, Signal, QHBoxLayout, QToolButton, QWidget
from ui.styling.presentation_style import token_color
from ui.canvas.interaction_manager import InteractionManager
from ui.canvas.navigation_controller import NavigationController
from ui.canvas.mouse_event_adapter import CanvasMouseEvent
from ui.canvas.canvas_hit import CanvasHitTarget


class GraphicsView(QGraphicsView):
    """Canonical GridForge Canvas viewport."""

    cursor_scene_position = Signal(object)
    layers_requested = Signal()
    PALETTE_EQUIPMENT_MIME = "application/x-gridforge-equipment-type"

    def __init__(
        self,
        controller: Any,
        tool_manager: Any,
        *,
        scene: QGraphicsScene,
        interaction_manager: Optional[InteractionManager] = None,
        navigation_controller: Optional[NavigationController] = None,
        parent: Optional[Any] = None,
        sld_controller: Any | None = None,
    ) -> None:
        if controller is None:
            raise ValueError("controller must not be None.")
        if tool_manager is None:
            raise ValueError("tool_manager must not be None.")
        if scene is None:
            raise ValueError("scene must not be None.")

        super().__init__(parent)
        self.controller = controller
        self.sld_controller: Any | None = None
        self.tool_manager = tool_manager
        self._scene = scene
        self._last_cursor_scene_position: tuple[float, float] | None = None
        self.interaction_manager = interaction_manager
        self.navigation_controller = navigation_controller
        self._palette_drag_equipment_type: str | None = None
        self._palette_drag_previous_tool_id: str | None = None
        self._palette_drag_insertion_started = False
        self._palette_drag_insertion_ambiguous = False
        self._palette_drag_preview_started = False
        self._space_pan_active = False
        self._navigation_pan_button: int | None = None

        if self._scene.parent() is None:
            self._scene.setParent(self)
        self.setScene(self._scene)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setObjectName("SLDCanvasView")
        self.setBackgroundBrush(QBrush(token_color("canvas_background")))
        # Electrical symbols and connection routes are vector geometry. Enable
        # predictable edge/text quality while keeping the view's update policy
        # incremental for interactive edits.
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Transient, selection-aware presentation controls. This overlay is a
        # viewport child and never becomes part of the persistent SLD scene.
        self._contextual_strip = QWidget(self.viewport())
        self._contextual_strip.setObjectName("SLDContextualToolStrip")
        self._contextual_strip.setStyleSheet(
            "QWidget#SLDContextualToolStrip { background: #263442; "
            "border: 1px solid #526579; border-radius: 5px; } "
            "QToolButton { color: #f2f5f8; padding: 4px 7px; border: 0; } "
            "QToolButton:hover { background: #40566b; }"
        )
        strip_layout = QHBoxLayout(self._contextual_strip)
        strip_layout.setContentsMargins(4, 3, 4, 3)
        strip_layout.setSpacing(2)
        self._single_symbol_buttons = []
        for label, tooltip, operation in (
            ("↶", "Rotate selected symbol 90° counter-clockwise (Q)", "rotate_ccw"),
            ("↷", "Rotate selected symbol 90° clockwise (E)", "rotate_cw"),
            ("Flip H", "Mirror selected symbol horizontally", "flip_h"),
            ("Flip V", "Mirror selected symbol vertically", "flip_v"),
        ):
            button = QToolButton(self._contextual_strip)
            button.setText(label)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            if operation in {"rotate_ccw", "rotate_cw"}:
                degrees = -90.0 if operation == "rotate_ccw" else 90.0
                button.clicked.connect(
                    lambda _checked=False, value=degrees:
                        self.apply_selection_rotation(value, single_symbol_only=True)
                )
            else:
                button.clicked.connect(
                    lambda _checked=False, op=operation: self._apply_contextual_symbol_action(op)
                )
            strip_layout.addWidget(button)
            self._single_symbol_buttons.append(button)

        self._group_transform_buttons = []
        for label, tooltip, rotation, mirror_x, mirror_y in (
            ("↶ Group", "Rotate selected group 90° counter-clockwise", -90.0, False, False),
            ("↷ Group", "Rotate selected group 90° clockwise", 90.0, False, False),
            ("Flip H", "Mirror selected group horizontally (H)", 0.0, True, False),
            ("Flip V", "Mirror selected group vertically (V)", 0.0, False, True),
        ):
            button = QToolButton(self._contextual_strip)
            button.setText(label)
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            if rotation:
                button.clicked.connect(
                    lambda _checked=False, degrees=rotation:
                        self.apply_selection_rotation(degrees)
                )
            else:
                button.clicked.connect(
                    lambda _checked=False, mx=mirror_x, my=mirror_y:
                        self._apply_contextual_group_action(mirror_x=mx, mirror_y=my)
                )
            strip_layout.addWidget(button)
            button.hide()
            self._group_transform_buttons.append(button)
        self._annotation_quadrant_button = QToolButton(self._contextual_strip)
        self._annotation_quadrant_button.setToolTip(
            "Cycle selected equipment annotations through top-right, top-left, bottom-right, and bottom-left quadrants"
        )
        self._annotation_quadrant_button.setAccessibleName("Change annotation quadrant")
        self._annotation_quadrant_button.clicked.connect(self._cycle_contextual_annotation_quadrant)
        strip_layout.addWidget(self._annotation_quadrant_button)
        self._contextual_strip.hide()

        # Keep rotation shortcuts active when focus moves to a canvas child.
        self._symbol_rotation_actions = []
        for shortcut, operation, description in (
            ("E", "rotate_cw", "Rotate selected symbol 90 degrees clockwise"),
            ("Q", "rotate_ccw", "Rotate selected symbol 90 degrees counter-clockwise"),
        ):
            action = QAction(description, self)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            degrees = -90.0 if operation == "rotate_ccw" else 90.0
            action.triggered.connect(
                lambda _checked=False, value=degrees:
                    self.apply_selection_rotation(value, single_symbol_only=True)
            )
            self.addAction(action)
            self._symbol_rotation_actions.append(action)

        if sld_controller is not None:
            self.bind_sld_controller(sld_controller)

        self._scene.selectionChanged.connect(self._refresh_contextual_tool_strip)
        self.horizontalScrollBar().valueChanged.connect(self._refresh_contextual_tool_strip)
        self.verticalScrollBar().valueChanged.connect(self._refresh_contextual_tool_strip)

    def bind_sld_controller(self, sld_controller: Any) -> None:
        """Bind the canonical SLD document/command capability for contextual edits."""
        if sld_controller is None:
            raise ValueError("sld_controller must not be None.")
        if not callable(getattr(sld_controller, "set_node_presentation", None)):
            raise TypeError("sld_controller must provide set_node_presentation().")
        if not callable(getattr(sld_controller, "set_annotation_blocks", None)):
            raise TypeError("sld_controller must provide set_annotation_blocks().")
        if not hasattr(type(sld_controller), "active_document"):
            raise TypeError("sld_controller must expose active_document.")
        if self.sld_controller is not None and self.sld_controller is not sld_controller:
            raise RuntimeError("GraphicsView is already bound to a different SLDController.")
        self.sld_controller = sld_controller
        self._refresh_contextual_tool_strip()

    def bind_services(
        self,
        *,
        interaction_manager: InteractionManager,
        navigation_controller: NavigationController,
    ) -> None:
        if interaction_manager is None:
            raise ValueError("interaction_manager must not be None.")
        if navigation_controller is None:
            raise ValueError("navigation_controller must not be None.")
        if self.interaction_manager is not None:
            raise RuntimeError("interaction_manager is already bound.")
        if self.navigation_controller is not None:
            raise RuntimeError("navigation_controller is already bound.")
        self.interaction_manager = interaction_manager
        self.navigation_controller = navigation_controller

    @property
    def graphics_scene(self) -> QGraphicsScene:
        return self._scene

    @property
    def last_cursor_scene_position(self) -> tuple[float, float] | None:
        return self._last_cursor_scene_position

    def _selected_presentation_node(self):
        """Resolve exactly one selected SLD node through canonical UI selection."""
        application = getattr(self.controller, "application", None)
        presentation = getattr(application, "presentation", None)
        model = getattr(presentation, "model", None)
        selection_manager = getattr(self.tool_manager, "selection_manager", None)
        active_document = getattr(self.sld_controller, "active_document", None)
        active_model = getattr(active_document, "model", None)
        if (
            model is None
            or selection_manager is None
            or active_document is None
            or active_model is not model
        ):
            return None, None

        # The SelectTool stores node selection in the typed NODE domain.
        # Do not let a connection selection with a coincident identifier
        # accidentally resolve as an equipment symbol.
        node_domain = getattr(selection_manager, "NODE", "node")
        selected_ids = tuple(selection_manager.get_selected_ids(domain=node_domain))
        if len(selected_ids) != 1:
            return None, None
        selected_id = str(selected_ids[0])

        # CanvasHitTarget.NODE may expose the stable presentation item ID
        # ("presentation:<node-id>"), while the SLD model is keyed by node ID.
        # Resolve the same identity variants accepted by SelectTool's group
        # transform path before falling back to the bound equipment identity.
        node = None
        getter = getattr(model, "get_node_optional", None)
        if selected_id.startswith("presentation:") and callable(getter):
            node = getter(selected_id[len("presentation:"):])
        if node is None:
            equipment_getter = getattr(model, "get_node_by_equipment_id_optional", None)
            if callable(equipment_getter):
                node = equipment_getter(selected_id)
        if node is None and callable(getter):
            node = getter(selected_id)
        if node is None:
            return None, None
        active_nodes = getattr(active_model, "nodes", None)
        if active_nodes is not None and node.node_id not in active_nodes:
            return None, None

        item = selection_manager.get_item_for_id(selected_ids[0], scene=self._scene)
        if item is None or getattr(item, "symbol_instance", None) is None:
            accepted_item_ids = {
                selected_id,
                str(getattr(node, "node_id", "")),
                str(getattr(node, "equipment_id", "")),
                f"presentation:{getattr(node, 'node_id', '')}",
            }
            item = next(
                (
                    candidate for candidate in self._scene.items()
                    if str(getattr(candidate, "object_id", "")) in accepted_item_ids
                    and getattr(candidate, "symbol_instance", None) is not None
                ),
                None,
            )
        if item is None or getattr(item, "symbol_instance", None) is None:
            return None, None
        return node, item

    def _refresh_contextual_tool_strip(self, *_args) -> None:
        """Anchor single- or multi-selection controls to visible SLD content."""
        document = getattr(self.sld_controller, "active_document", None)
        if document is None:
            for action in self._symbol_rotation_actions:
                action.setEnabled(False)
            self._contextual_strip.hide()
            return
        select_tool = (
            self.tool_manager.get_tool("select")
            if self.tool_manager is not None
            and callable(getattr(self.tool_manager, "get_tool", None))
            else None
        )
        rotation_mode = self._rotation_selection_mode()
        if rotation_mode is None:
            for action in self._symbol_rotation_actions:
                action.setEnabled(False)
            self._contextual_strip.hide()
            return
        group_mode = rotation_mode == "group"
        for action in self._symbol_rotation_actions:
            action.setEnabled(rotation_mode == "single")
        for button in self._single_symbol_buttons:
            button.setVisible(not group_mode)
        for button in self._group_transform_buttons:
            button.setVisible(group_mode)
        self._annotation_quadrant_button.setVisible(not group_mode)

        if group_mode:
            selected = select_tool.selected_transform_nodes()
            active_model = getattr(document, "model", None)
            application = getattr(self.controller, "application", None)
            presentation = getattr(application, "presentation", None)
            if active_model is None or getattr(presentation, "model", None) is not active_model:
                self._contextual_strip.hide()
                return
            active_nodes = getattr(active_model, "nodes", None)
            if not selected:
                self._contextual_strip.hide()
                return
            scene_bounds_list = []
            for node, _symbol in selected:
                if active_nodes is not None and node.node_id not in active_nodes:
                    self._contextual_strip.hide()
                    return
                accepted_ids = {
                    str(node.node_id),
                    str(node.equipment_id),
                    f"presentation:{node.node_id}",
                }
                item = next((
                    candidate for candidate in self._scene.items()
                    if str(getattr(candidate, "object_id", "")) in accepted_ids
                    and getattr(candidate, "symbol_instance", None) is not None
                ), None)
                if item is None:
                    self._contextual_strip.hide()
                    return
                scene_bounds_list.append(item.mapRectToScene(item.boundingRect()))
            scene_bounds = scene_bounds_list[0]
            for bounds in scene_bounds_list[1:]:
                scene_bounds = scene_bounds.united(bounds)
        else:
            node, item = self._selected_presentation_node()
            if node is None or item is None:
                self._contextual_strip.hide()
                return
            document_model = getattr(document, "model", None)
            nodes = getattr(document_model, "nodes", None)
            if nodes is not None and node.node_id not in nodes:
                self._contextual_strip.hide()
                return
            annotation_blocks = tuple(
                block for block in getattr(document, "annotation_blocks", ())
                if block.node_id == node.node_id
            )
            self._annotation_quadrant_button.setEnabled(bool(annotation_blocks))
            self._annotation_quadrant_button.setText("Label ↻")
            scene_bounds = item.mapRectToScene(item.boundingRect())

        # Keep the overlay attached only while the selected content is actually
        # visible. Clamping off-screen content to a viewport edge is misleading.
        viewport_bounds = self.mapFromScene(scene_bounds).boundingRect()
        viewport_rect = self.viewport().rect()
        if not viewport_bounds.intersects(viewport_rect):
            self._contextual_strip.hide()
            return
        anchor_x = viewport_bounds.right()
        anchor_y = viewport_bounds.top()
        self._contextual_strip.adjustSize()
        width = self._contextual_strip.sizeHint().width()
        height = self._contextual_strip.sizeHint().height()
        x = min(max(4, anchor_x + 8), max(4, viewport_rect.width() - width - 4))
        y = min(max(4, anchor_y - height - 8), max(4, viewport_rect.height() - height - 4))
        self._contextual_strip.move(x, y)
        self._contextual_strip.show()
        self._contextual_strip.raise_()

    def _rotation_selection_mode(self) -> str | None:
        """Return single/group only for a complete, active-document symbol selection."""
        if self.sld_controller is None or self.tool_manager is None:
            return None
        document = getattr(self.sld_controller, "active_document", None)
        application = getattr(self.controller, "application", None)
        presentation = getattr(application, "presentation", None)
        if document is None or getattr(document, "model", None) is not getattr(presentation, "model", None):
            return None
        selection_manager = getattr(self.tool_manager, "selection_manager", None)
        if selection_manager is None:
            return None
        node_domain = getattr(selection_manager, "NODE", "node")
        selected_ids = tuple(selection_manager.get_selected_ids(domain=node_domain))
        if len(selected_ids) == 1:
            node, item = self._selected_presentation_node()
            return "single" if node is not None and item is not None else None
        if len(selected_ids) < 2:
            return None
        select_tool = (
            self.tool_manager.get_tool("select")
            if callable(getattr(self.tool_manager, "get_tool", None))
            else None
        )
        resolver = getattr(select_tool, "selected_transform_nodes", None)
        if not callable(resolver):
            return None
        resolved = tuple(resolver())
        # SelectTool preflights every selected node and symbol; a partial
        # resolution is never advertised or transformed.
        return "group" if len(resolved) == len(selected_ids) else None

    def can_rotate_selection(self) -> bool:
        """Whether the active SLD selection is fully resolvable for rotation."""
        return self._rotation_selection_mode() is not None

    def can_rotate_single_selection(self) -> bool:
        """Whether Q/E may rotate exactly one active SLD symbol."""
        return self._rotation_selection_mode() == "single"

    def apply_selection_rotation(
        self,
        rotation_degrees: float,
        *,
        single_symbol_only: bool = False,
    ) -> bool:
        """Rotate one symbol or a fully resolved group through existing Application commands."""
        if (
            isinstance(rotation_degrees, bool)
            or not isinstance(rotation_degrees, (int, float))
            or not math.isfinite(float(rotation_degrees))
            or not math.isclose(abs(float(rotation_degrees)), 90.0)
        ):
            raise ValueError("Selection rotation must be exactly +90 or -90 degrees.")
        mode = self._rotation_selection_mode()
        if mode == "single":
            operation = "rotate_cw" if rotation_degrees > 0 else "rotate_ccw"
            return self._apply_contextual_symbol_action(operation)
        if mode == "group" and not single_symbol_only:
            return self._apply_contextual_group_action(
                rotation_degrees=float(rotation_degrees)
            )
        if not single_symbol_only:
            self.controller.show_status_message(
                "Rotation rejected: select one symbol or a fully resolvable SLD symbol group."
            )
        return False

    def _apply_contextual_group_action(
        self,
        *,
        rotation_degrees: float = 0.0,
        mirror_x: bool = False,
        mirror_y: bool = False,
    ) -> bool:
        """Route contextual group operations through the canonical SelectTool."""
        select_tool = (
            self.tool_manager.get_tool("select")
            if self.tool_manager is not None
            and callable(getattr(self.tool_manager, "get_tool", None))
            else None
        )
        apply_transform = getattr(select_tool, "apply_group_transform", None)
        if not callable(apply_transform):
            self.controller.show_status_message(
                "Group transformation is unavailable in the active SLD workspace."
            )
            return False
        committed = bool(apply_transform(
            rotation_degrees=rotation_degrees,
            mirror_x=mirror_x,
            mirror_y=mirror_y,
        ))
        self._refresh_contextual_tool_strip()
        return committed

    def _cycle_contextual_annotation_quadrant(self) -> None:
        """Persist annotation placement through the existing Application command path."""
        if self.sld_controller is None:
            return
        node, _item = self._selected_presentation_node()
        document = getattr(self.sld_controller, "active_document", None)
        if node is None or document is None:
            return
        blocks = tuple(getattr(document, "annotation_blocks", ()))
        selected_blocks = tuple(block for block in blocks if block.node_id == node.node_id)
        if not selected_blocks:
            return
        quadrants = ("top_right", "top_left", "bottom_right", "bottom_left")
        updated = []
        for block in blocks:
            payload = block.to_dict()
            if block.node_id == node.node_id:
                current = block.quadrant
                payload["quadrant"] = (
                    quadrants[(quadrants.index(current) + 1) % len(quadrants)]
                    if current in quadrants else "top_right"
                )
            updated.append(payload)
        try:
            self.sld_controller.set_annotation_blocks(updated)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            self.controller.show_status_message(f"Annotation placement update rejected: {exc}")
            return
        self._refresh_contextual_tool_strip()

    def _apply_contextual_symbol_action(self, operation: str) -> bool:
        """Persist a single-symbol presentation change through SLDController."""
        from ui.equipment.symbol.symbol_base import SymbolBase

        if self.sld_controller is None:
            self.controller.show_status_message("Contextual SLD editing is not bound to the active document.")
            return False
        node, item = self._selected_presentation_node()
        if node is None or item is None:
            return False
        source = node.presentation or item.symbol_instance
        presentation = SymbolBase.from_dict(source.to_dict())
        if operation == "rotate_ccw":
            presentation.rotation = (presentation.rotation - 90.0) % 360.0
        elif operation == "rotate_cw":
            presentation.rotation = (presentation.rotation + 90.0) % 360.0
        elif operation == "flip_h":
            # Reflect across the canvas vertical axis, not the symbol's
            # already-rotated local axis. The item applies local reflection
            # before rotation, so negate the angle to compose the world-space
            # reflection correctly.
            presentation.rotation = (-float(presentation.rotation)) % 360.0
            presentation.set_property("mirror_x", not bool(presentation.get_property("mirror_x", False)))
        elif operation == "flip_v":
            presentation.rotation = (-float(presentation.rotation)) % 360.0
            presentation.set_property("mirror_y", not bool(presentation.get_property("mirror_y", False)))
        else:
            raise ValueError(f"Unsupported contextual symbol action: {operation}")
        try:
            self.sld_controller.set_node_presentation(
                str(node.node_id),
                presentation,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            self.controller.show_status_message(
                f"SLD symbol presentation update rejected: {exc}"
            )
            return False
        self._refresh_contextual_tool_strip()
        return True

    def _palette_type_from_event(self, event: Any) -> str | None:
        mime = event.mimeData()
        if mime is None or not mime.hasFormat(self.PALETTE_EQUIPMENT_MIME):
            return None
        try:
            value = bytes(mime.data(self.PALETTE_EQUIPMENT_MIME)).decode("utf-8").strip()
        except (TypeError, UnicodeDecodeError):
            return None
        return value or None

    def _begin_palette_drag(self, equipment_type: str) -> bool:
        if self.interaction_manager is None:
            return False
        registry = getattr(self.tool_manager, "equipment_registry", None)
        creation_context = getattr(self.tool_manager, "creation_context", None)
        if registry is None or creation_context is None or creation_context.active:
            return False
        try:
            definition = registry.require(equipment_type)
            previous_tool_id = self.tool_manager.active_tool_id
            self.tool_manager.activate(definition.tool_id, cancel_active_creation=False)
        except (KeyError, TypeError, ValueError, RuntimeError):
            return False
        self._palette_drag_equipment_type = definition.equipment_type
        self._palette_drag_previous_tool_id = previous_tool_id
        self._palette_drag_insertion_started = False
        self._palette_drag_preview_started = False
        return True

    def _palette_event_without_hit(self, event: Any) -> CanvasMouseEvent:
        position = event.position()
        viewport_point = QPointF(float(position.x()), float(position.y()))
        scene_point = self.mapToScene(viewport_point.toPoint())
        modifiers = int(event.modifiers())
        return CanvasMouseEvent(
            position=viewport_point,
            scene_position=QPointF(scene_point),
            presentation_hit=CanvasHitTarget.none(),
            button=int(Qt.MouseButton.LeftButton.value),
            buttons=int(Qt.MouseButton.LeftButton.value),
            modifiers=modifiers,
            insertion_intent=False,
        )

    def _adapt_palette_event(self, event: Any) -> CanvasMouseEvent | None:
        adapter = getattr(self.interaction_manager, "input_adapter", None)
        if adapter is None:
            return None
        try:
            semantic = adapter.adapt(event)
            # The insertion corridor is specified in viewport pixels, not
            # scene units, so its physical hit width remains stable at every zoom.
            viewport = semantic.position
            x, y = float(viewport.x()), float(viewport.y())
            center = semantic.scene_position
            left = self.mapToScene(QPointF(x - 15.0, y).toPoint())
            right = self.mapToScene(QPointF(x + 15.0, y).toPoint())
            up = self.mapToScene(QPointF(x, y - 15.0).toPoint())
            down = self.mapToScene(QPointF(x, y + 15.0).toPoint())
            tolerance = max(
                math.hypot(float(left.x()) - float(center.x()), float(left.y()) - float(center.y())),
                math.hypot(float(right.x()) - float(center.x()), float(right.y()) - float(center.y())),
                math.hypot(float(up.x()) - float(center.x()), float(up.y()) - float(center.y())),
                math.hypot(float(down.x()) - float(center.x()), float(down.y()) - float(center.y())),
            )
            return CanvasMouseEvent(
                position=semantic.position,
                scene_position=semantic.scene_position,
                object_id=semantic.object_id,
                presentation_hit=semantic.presentation_hit,
                button=semantic.button,
                buttons=semantic.buttons,
                modifiers=semantic.modifiers,
                event_type=semantic.event_type,
                timestamp=semantic.timestamp,
                insertion_intent=semantic.insertion_intent,
                insertion_tolerance_scene=tolerance,
            )
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return None

    def _begin_palette_insertion(self, event: Any) -> bool:
        semantic = self._adapt_palette_event(event)
        if semantic is None:
            return False
        hit = semantic.hit_target
        # Resolve against the full 15-pixel viewport corridor. The semantic
        # point hit may be empty when the pointer is close to, but not directly
        # on, the routed wire.
        creation_context = self.tool_manager.creation_context
        if not creation_context.active:
            return False
        draft = creation_context.require_draft()
        insertion_tool = self.tool_manager.get_tool("electrical-insertion")
        parameters = dict(draft.snapshot_values())
        orientation = float(draft.orientation)
        try:
            insertion_tool.begin(
                self._palette_drag_equipment_type,
                creation_parameters=parameters,
                orientation=orientation,
            )
            candidate = insertion_tool.insertion_target(semantic)
            if candidate is None:
                return False
            hit_connection_id = getattr(hit, "core_connection_id", None)
            if (
                getattr(getattr(hit, "kind", None), "value", None) == "simple_wire_segment"
                and hit_connection_id
                and str(candidate.get("connection_id", "")) != str(hit_connection_id)
            ):
                return False
            return bool(
                self.tool_manager.begin_equipment_insertion(
                    self._palette_drag_equipment_type,
                    creation_parameters=parameters,
                    orientation=orientation,
                    equipment_id=None,
                    initial_event=semantic,
                )
            )
        except (KeyError, TypeError, ValueError, RuntimeError):
            return False

    def _finish_palette_drag(self, *, restore_previous_tool: bool) -> None:
        previous_tool_id = self._palette_drag_previous_tool_id
        try:
            if restore_previous_tool:
                self.tool_manager.cancel()
                if self.tool_manager.active_tool_id != previous_tool_id:
                    self.tool_manager.activate(previous_tool_id)
        finally:
            self._palette_drag_equipment_type = None
            self._palette_drag_previous_tool_id = None
            self._palette_drag_insertion_started = False
            self._palette_drag_insertion_ambiguous = False
            self._palette_drag_preview_started = False

    def dragEnterEvent(self, event: Any) -> None:
        equipment_type = self._palette_type_from_event(event)
        if equipment_type is None:
            event.ignore()
            return
        if self._palette_drag_equipment_type is None and not self._begin_palette_drag(equipment_type):
            event.ignore()
            return
        if self._palette_drag_equipment_type != equipment_type:
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event: Any) -> None:
        if self._palette_drag_equipment_type is None or self.interaction_manager is None:
            event.ignore()
            return
        shift_mask = int(Qt.KeyboardModifier.ShiftModifier.value)
        insertion_requested = bool(int(event.modifiers()) & shift_mask)
        if not self._palette_drag_preview_started:
            preview_event = self._palette_event_without_hit(event)
            self.interaction_manager.mouse_move(preview_event)
            self._palette_drag_preview_started = True
        if not self._palette_drag_insertion_started:
            insertion_started = self._begin_palette_insertion(event)
            insertion_tool = self.tool_manager.get_tool("electrical-insertion")
            self._palette_drag_insertion_ambiguous = bool(
                not insertion_started
                and getattr(insertion_tool, "last_resolution_ambiguous", False)
            )
            if insertion_started:
                self._palette_drag_insertion_started = True
            elif self._palette_drag_insertion_ambiguous:
                self.controller.show_status_message(
                    "Insertion target is ambiguous. Move away from the crossing or cancel the drop."
                )
                event.acceptProposedAction()
                return
            elif insertion_requested:
                # Explicit Shift insertion remains fail-closed when no
                # eligible wire target can be resolved.
                self.interaction_manager.mouse_move(
                    self._palette_event_without_hit(event)
                )
                event.acceptProposedAction()
                return
        elif self._palette_drag_insertion_started:
            semantic = self._adapt_palette_event(event)
            self.interaction_manager.mouse_move(semantic if semantic is not None else event)
        elif not insertion_requested:
            self.interaction_manager.mouse_move(self._palette_event_without_hit(event))
        event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        if self._palette_drag_equipment_type is None or self.interaction_manager is None:
            event.ignore()
            return
        shift_mask = int(Qt.KeyboardModifier.ShiftModifier.value)
        insertion_requested = bool(int(event.modifiers()) & shift_mask) or self._palette_drag_insertion_started
        if not self._palette_drag_preview_started:
            self.interaction_manager.mouse_move(self._palette_event_without_hit(event))
            self._palette_drag_preview_started = True
        if not insertion_requested:
            insertion_requested = self._begin_palette_insertion(event)
            self._palette_drag_insertion_started = insertion_requested
            insertion_tool = self.tool_manager.get_tool("electrical-insertion")
            self._palette_drag_insertion_ambiguous = bool(
                not insertion_requested
                and getattr(insertion_tool, "last_resolution_ambiguous", False)
            )
        if self._palette_drag_insertion_ambiguous:
            self.controller.show_status_message(
                "Insertion target is ambiguous. Drop cancelled; move away from the crossing."
            )
            self._finish_palette_drag(restore_previous_tool=True)
            event.ignore()
            return
        if insertion_requested:
            if not self._palette_drag_insertion_started:
                if not self._begin_palette_insertion(event):
                    self._finish_palette_drag(restore_previous_tool=True)
                    event.ignore()
                    return
                self._palette_drag_insertion_started = True
            semantic = self._adapt_palette_event(event)
            if semantic is None or not self._palette_drag_insertion_started:
                self._finish_palette_drag(restore_previous_tool=True)
                event.ignore()
                return
            hit = semantic.hit_target
            try:
                insertion_tool = self.tool_manager.get_tool("electrical-insertion")
                candidate = insertion_tool.insertion_target(semantic)
            except (KeyError, TypeError, ValueError, RuntimeError):
                candidate = None
            hit_connection_id = getattr(hit, "core_connection_id", None)
            if candidate is None or (
                getattr(getattr(hit, "kind", None), "value", None) == "simple_wire_segment"
                and hit_connection_id
                and str(candidate.get("connection_id", "")) != str(hit_connection_id)
            ):
                self._finish_palette_drag(restore_previous_tool=True)
                event.ignore()
                return
            handled = self.interaction_manager.mouse_release(semantic)
        else:
            if self._palette_drag_insertion_started:
                self._finish_palette_drag(restore_previous_tool=True)
                event.ignore()
                return
            semantic = self._palette_event_without_hit(event)
            pressed = self.interaction_manager.mouse_press(semantic)
            handled = self.interaction_manager.mouse_release(semantic) if pressed else False
        if handled:
            self._finish_palette_drag(restore_previous_tool=False)
            event.acceptProposedAction()
        else:
            self._finish_palette_drag(restore_previous_tool=True)
            event.ignore()

    def dragLeaveEvent(self, event: Any) -> None:
        if self._palette_drag_equipment_type is not None:
            self._finish_palette_drag(restore_previous_tool=True)
        super().dragLeaveEvent(event)

    @staticmethod
    def _event_flag_value(event: Any, name: str) -> int:
        value = getattr(event, name, None)
        value = value() if callable(value) else value
        raw = getattr(value, "value", value)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_viewport_position(event: Any) -> QPointF:
        position = getattr(event, "position", None)
        if callable(position):
            value = position()
            return QPointF(float(value.x()), float(value.y()))
        point = event.pos()
        return QPointF(float(point.x()), float(point.y()))

    def mousePressEvent(self, event: Any) -> None:
        # Restore canvas focus even when InteractionManager consumes the press.
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        navigation = self.navigation_controller
        button = self._event_flag_value(event, "button")
        middle_button = int(Qt.MouseButton.MiddleButton.value)
        left_button = int(Qt.MouseButton.LeftButton.value)
        if navigation is not None and (
            button == middle_button
            or (self._space_pan_active and button == left_button)
        ):
            navigation.start_pan(self._event_viewport_position(event))
            self._navigation_pan_button = button
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self.interaction_manager is not None and self.interaction_manager.mouse_press(event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        navigation = self.navigation_controller
        if navigation is not None and navigation.is_panning:
            navigation.update_pan(self._event_viewport_position(event))
            event.accept()
            self._refresh_contextual_tool_strip()
            return
        # mapToScene(QPoint) rounds/truncates QPointF mouse positions to
        # integer viewport coordinates. Invert the current viewport transform
        # instead so cursor feedback remains aligned during pan and placement.
        viewport_position = (
            event.position()
            if callable(getattr(event, "position", None))
            else QPointF(float(event.pos().x()), float(event.pos().y()))
        )
        inverse, invertible = self.viewportTransform().inverted()
        if invertible:
            point = inverse.map(
                QPointF(float(viewport_position.x()), float(viewport_position.y()))
            )
        else:
            integer_position = (
                viewport_position.toPoint()
                if callable(getattr(viewport_position, "toPoint", None))
                else event.pos()
            )
            point = self.mapToScene(integer_position)
        self._last_cursor_scene_position = (float(point.x()), float(point.y()))
        self.cursor_scene_position.emit(self._last_cursor_scene_position)
        if self.interaction_manager is not None and self.interaction_manager.mouse_move(event):
            self._refresh_contextual_tool_strip()
            return
        super().mouseMoveEvent(event)
        self._refresh_contextual_tool_strip()

    def mouseReleaseEvent(self, event: Any) -> None:
        navigation = self.navigation_controller
        button = self._event_flag_value(event, "button")
        if (
            navigation is not None
            and navigation.is_panning
            and button == self._navigation_pan_button
        ):
            navigation.end_pan()
            self._navigation_pan_button = None
            self.unsetCursor()
            event.accept()
            self._refresh_contextual_tool_strip()
            return
        if self.interaction_manager is not None and self.interaction_manager.mouse_release(event):
            self._refresh_contextual_tool_strip()
            return
        super().mouseReleaseEvent(event)
        self._refresh_contextual_tool_strip()

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if self.interaction_manager is not None and self.interaction_manager.mouse_double_click(event):
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: Any) -> None:
        if self.navigation_controller is not None and self.navigation_controller.handle_wheel(event):
            self._refresh_contextual_tool_strip()
            return
        super().wheelEvent(event)
        self._refresh_contextual_tool_strip()

    def contextMenuEvent(self, event: Any) -> None:
        """Expose the canonical Layer Manager from the SLD canvas context menu."""
        if self.sld_controller is None:
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        manage_layers = menu.addAction("Manage SLD Layers…")
        selected = menu.exec(event.globalPos())
        if selected == manage_layers:
            self.layers_requested.emit()
        event.accept()

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        modifiers = self._event_flag_value(event, "modifiers")
        shift = int(Qt.KeyboardModifier.ShiftModifier.value)
        no_modifiers = modifiers == 0
        navigation = self.navigation_controller

        if key == Qt.Key.Key_Space:
            self._space_pan_active = True
            event.accept()
            return
        if navigation is not None:
            zoom_in_key = (
                (key == Qt.Key.Key_Plus and modifiers in (0, shift))
                or (key == Qt.Key.Key_Equal and no_modifiers)
            )
            if zoom_in_key:
                navigation.zoom_in()
                event.accept()
                self._refresh_contextual_tool_strip()
                return
            if no_modifiers and key == Qt.Key.Key_Minus:
                navigation.zoom_out()
                event.accept()
                self._refresh_contextual_tool_strip()
                return
            if no_modifiers and key == Qt.Key.Key_0:
                navigation.fit_content()
                event.accept()
                self._refresh_contextual_tool_strip()
                return
            if no_modifiers and key == Qt.Key.Key_Home:
                navigation.reset_view()
                event.accept()
                self._refresh_contextual_tool_strip()
                return
        if self.interaction_manager is not None and self.interaction_manager.key_press(event):
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Space:
            self._space_pan_active = False
            event.accept()
            return
        if self.interaction_manager is not None and self.interaction_manager.key_release(event):
            return
        super().keyReleaseEvent(event)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._refresh_contextual_tool_strip()

    def dispose(self) -> None:
        self.interaction_manager = None
        self.navigation_controller = None
        self._contextual_strip.hide()
        self.sld_controller = None
        self.controller = None
        self.tool_manager = None


__all__ = ["GraphicsView"]
