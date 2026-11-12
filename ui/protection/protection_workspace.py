# ============================================================
# File: ui/protection/protection_workspace.py
# GridForge V2 — Protection Engineering Workspace
# Author: Subhendu Mishra
# ============================================================
"""Protection engineering workstation over Application read models."""

from __future__ import annotations

from typing import Any

from core.protection import get_protection_function
from core.application.events import (
    ElementCreated, ElementRemoved, ElementUpdated, NetworkChanged,
    ProtectionChanged, TopologyChanged, NetworkCommitted, ProjectClosed, ProjectLoaded,
    StudyStarted, StudyCompleted, StudyFailed, StudyCancelled,
    InstrumentationWiringChanged,
)
from ui.canvas.canvas_framework import CanvasStateMachine
from ui.canvas.engineering_canvas_contract import CanvasFeedback, CanvasInteractionAdapter
from ui.core.qt import (
    QAction, QGraphicsLineItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem,
    QGraphicsView, QGroupBox, QLabel, QListWidget, QPen, QSplitter, QTabWidget, QToolBar,
    QVBoxLayout, QWidget, QComboBox, QLineEdit, QPushButton, QFormLayout, QCheckBox,
)
from ui.core.selection_manager import SelectionManager
from .protection_presentation import ProtectionPresentationDocument
from .protection_tools import ProtectionInteractionController
from .protection_engineering_panels import (
    ProtectionAnalysisCoveragePanel,
    ProtectionFunctionCatalogPanel,
    ProtectionTCCPanel,
    ProtectionCoordinationStudyPanel,
)


class ProtectionGraphicsNode(QGraphicsRectItem):
    """Selectable presentation node carrying canonical identity plus presentation role."""

    def __init__(self, object_id: str, title: str, x: float, y: float,
                 width: float = 150.0, *, kind: str = "object",
                 relay_id: str | None = None, input_name: str | None = None,
                 channel_id: str | None = None, element_id: str | None = None) -> None:
        super().__init__(0.0, 0.0, width, 52.0)
        self.setPos(float(x), float(y))
        self.object_id = str(object_id)
        self.title = str(title)
        self.kind = str(kind)
        self.relay_id = relay_id
        self.input_name = input_name
        self.channel_id = channel_id
        self.element_id = element_id
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setToolTip(f"{self.title}\nID: {self.object_id}")


class ProtectionGraphicsSurface(QGraphicsView):
    """Projection-only Protection canvas; no Core mutation or persistence authority."""

    def __init__(self, *, application: Any, selection_manager: SelectionManager,
                 adapter: CanvasInteractionAdapter, interaction: ProtectionInteractionController,
                 presentation: ProtectionPresentationDocument, parent: QWidget | None = None) -> None:
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)
        self.setObjectName("ProtectionEngineeringCanvas")
        self.setMouseTracking(True)
        self._application = application
        self._selection_manager = selection_manager
        self._adapter = adapter
        self._interaction = interaction
        self._presentation = presentation
        self._nodes: dict[str, ProtectionGraphicsNode] = {}
        self._preview_line: QGraphicsLineItem | None = None
        self._scene.selectionChanged.connect(self._on_scene_selection)
        selection_manager.selection_changed.connect(self._on_canonical_selection)
        self.setMinimumHeight(360)

    @property
    def presentation(self) -> ProtectionPresentationDocument:
        return self._presentation

    def activate_tool_id(self, tool_id: str) -> None:
        """Activate a Protection tool through the discipline runtime."""
        normalized = str(tool_id).strip()
        if normalized == "fit":
            self._canvas.fit_to_content()
            return
        if normalized == "diagnostics":
            self._canvas.show_diagnostics()
            return
        self._interaction.activate(normalized)
        self._toolbar.set_active(self._interaction.active_tool)

    def refresh(self) -> None:
        self._scene.clear()
        self._nodes.clear()
        self._preview_line = None
        try:
            protection = self._application.read_protection()
            configurations = self._application.read_protection_function_configurations()
        except RuntimeError:
            self._draw_message("Protection read boundary unavailable.")
            return

        configuration_by_relay: dict[str, list[Any]] = {}
        for configuration in configurations:
            configuration_by_relay.setdefault(str(configuration.relay_id), []).append(configuration)

        next_relay_y = 30.0
        for relay in protection.relays:
            relay_id = str(relay.object_id)
            relay_configs = tuple(sorted(
                configuration_by_relay.get(relay_id, ()),
                key=lambda item: str(item.element_id),
            ))
            base_y = next_relay_y
            relay_block_height = sum(
                max(145.0, 80.0 + len(item.input_channel_ids) * 62.0)
                for item in relay_configs
            ) if relay_configs else 145.0
            next_relay_y = base_y + relay_block_height + 80.0
            if not relay_configs:
                relay_node = self._add_node(
                    relay_id,
                    f"RELAY\n{relay.name}\n{relay.function_type}",
                    510.0, base_y + 30.0, 175.0,
                    kind="relay",
                )
                status_node = self._add_node(
                    f"{relay_id}:configuration",
                    "PROTECTION NOT COMMISSIONED\nNO FUNCTION CONFIGURATION",
                    735.0, base_y + 30.0, 260.0,
                    kind="configuration_status",
                    relay_id=relay_id,
                )
                self._link(
                    relay_node,
                    status_node,
                    connection_id=f"{relay_id}->configuration",
                )
                continue

            decisions_by_element = {
                str(decision.element_id): decision
                for decision in getattr(relay, "element_decisions", ())
                if str(decision.relay_id) == relay_id
            }
            legacy_decision = getattr(relay, "decision", None)
            if (
                not decisions_by_element
                and len(relay_configs) == 1
                and legacy_decision is not None
                and str(getattr(legacy_decision, "element_id", "")) == str(relay_configs[0].element_id)
            ):
                decisions_by_element[str(relay_configs[0].element_id)] = legacy_decision

            total_height = sum(
                max(145.0, 80.0 + len(configuration.input_channel_ids) * 62.0)
                for configuration in relay_configs
            )
            relay_node_y = base_y + max(30.0, (total_height - 52.0) / 2.0)
            relay_node = self._add_node(
                relay_id,
                f"RELAY\n{relay.name}\n{relay.function_type}",
                510.0, relay_node_y, 175.0,
                kind="relay",
            )

            row_y = base_y
            for configuration in relay_configs:
                element_id = str(configuration.element_id)
                row_height = max(145.0, 80.0 + len(configuration.input_channel_ids) * 62.0)
                for input_index, (input_name, channel_id) in enumerate(
                    sorted(configuration.input_channel_ids.items())
                ):
                    input_y = row_y + 20.0 + input_index * 58.0
                    input_node_id = f"{relay_id}:element:{element_id}:input:{input_name}"
                    input_node = self._add_node(
                        input_node_id,
                        f"RELAY INPUT\n{input_name}\n{channel_id}",
                        285.0, input_y, 170.0,
                        kind="relay_input",
                        relay_id=relay_id,
                        element_id=element_id,
                        input_name=str(input_name),
                        channel_id=str(channel_id),
                    )
                    source_node = self._nodes.get(str(channel_id))
                    if source_node is None:
                        source_node = self._add_node(
                            str(channel_id),
                            f"MEASUREMENT CHANNEL\n{channel_id}",
                            30.0, input_y, 190.0,
                            kind="measurement_channel",
                            channel_id=str(channel_id),
                        )
                    self._link(
                        source_node,
                        input_node,
                        connection_id=f"{channel_id}->{relay_id}:element:{element_id}:{input_name}",
                    )
                    self._link(
                        input_node,
                        relay_node,
                        connection_id=f"{relay_id}:element:{element_id}:{input_name}->{relay_id}",
                    )

                function_y = row_y + max(30.0, (row_height - 52.0) / 2.0)
                function_node = self._add_node(
                    f"{relay_id}:element:{element_id}:function",
                    f"PROTECTION ELEMENT\n{configuration.function_code}\n{element_id}",
                    735.0, function_y, 190.0,
                    kind="function",
                    relay_id=relay_id,
                    element_id=element_id,
                )
                decision = decisions_by_element.get(element_id)
                if decision is not None:
                    decision_text = self._decision_text(decision)
                elif not bool(getattr(configuration, "enabled", True)):
                    decision_text = "DISABLED — NOT EVALUATED"
                else:
                    decision_text = "NOT EVALUATED"
                decision_node = self._add_node(
                    f"{relay_id}:element:{element_id}:decision",
                    f"DECISION\n{decision_text}",
                    980.0, function_y, 165.0,
                    kind="decision",
                    relay_id=relay_id,
                    element_id=element_id,
                )
                trip_node = self._add_node(
                    f"{relay_id}:element:{element_id}:trip",
                    "TRIP REQUEST\nAPPLICATION BOUNDARY",
                    1210.0, function_y, 190.0,
                    kind="trip_output",
                    relay_id=relay_id,
                    element_id=element_id,
                )
                self._link(
                    relay_node,
                    function_node,
                    connection_id=f"{relay_id}->element:{element_id}:function",
                )
                self._link(
                    function_node,
                    decision_node,
                    connection_id=f"{relay_id}:element:{element_id}:decision",
                )
                self._link(
                    decision_node,
                    trip_node,
                    connection_id=f"{relay_id}:element:{element_id}:trip",
                )
                row_y += row_height

        if self._scene.items():
            self.fitInView(self._scene.itemsBoundingRect().adjusted(-30, -30, 30, 30))

    def _add_node(self, object_id: str, title: str, x: float, y: float, width: float = 150.0,
                  **metadata: Any) -> ProtectionGraphicsNode:
        stored = self._presentation.node(object_id, default_x=x, default_y=y)
        node = ProtectionGraphicsNode(
            object_id, title.replace("\n", " | "),
            float(stored.get("x", x)), float(stored.get("y", y)), width, **metadata,
        )
        self._scene.addItem(node)
        self._nodes[object_id] = node
        text = QGraphicsTextItem(title, node)
        text.setPos(8.0, 7.0)
        return node

    def _link(self, source: ProtectionGraphicsNode, target: ProtectionGraphicsNode, *, connection_id: str) -> None:
        stored = self._presentation.connections.get(connection_id)
        route = stored.get("route", ()) if isinstance(stored, dict) else ()
        if len(route) >= 2:
            start, end = route[0], route[-1]
            line = QGraphicsLineItem(float(start[0]), float(start[1]), float(end[0]), float(end[1]))
        else:
            a = source.sceneBoundingRect().center()
            b = target.sceneBoundingRect().center()
            line = QGraphicsLineItem(a.x(), a.y(), b.x(), b.y())
            self._presentation.set_connection_route(connection_id, [(a.x(), a.y()), (b.x(), b.y())])
        line.setPen(QPen())
        line.setZValue(-1)
        self._scene.addItem(line)

    def fit_to_content(self) -> None:
        items = self._scene.itemsBoundingRect()
        if not items.isNull():
            self.fitInView(items.adjusted(-30, -30, 30, 30))

    def show_diagnostics(self) -> None:
        self._adapter.feedback(CanvasFeedback.NONE, "Protection diagnostics are read-side only.")

    def mousePressEvent(self, event: Any) -> None:
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, QGraphicsTextItem):
            item = item.parentItem()
        if isinstance(item, ProtectionGraphicsNode):
            result = self._interaction.click_node(item)
            if not self._interaction.session.connection_source_id:
                self._clear_preview()
            if result == "fit":
                self.fitInView(self._scene.itemsBoundingRect().adjusted(-30, -30, 30, 30))
            elif result == "diagnostics":
                self._adapter.feedback(CanvasFeedback.NONE, "Protection diagnostics are read-side only.")
            event.accept()
            return
        if self._interaction.active_tool == "select":
            self._selection_manager.clear()
        super().mousePressEvent(event)

    def _on_scene_selection(self) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, ProtectionGraphicsNode)]
        if selected and self._interaction.active_tool == "select":
            self._selection_manager.select_single(selected[0].object_id)

    def _on_canonical_selection(self, ids: object) -> None:
        selected = {str(value) for value in (ids or ())}
        for object_id, node in self._nodes.items():
            node.setSelected(object_id in selected)

    def _draw_message(self, message: str) -> None:
        text = QGraphicsTextItem(message)
        self._scene.addItem(text)
        text.setPos(20.0, 20.0)

    @staticmethod
    def _decision_text(decision: Any) -> str:
        if decision is None:
            return "NOT EVALUATED"
        if decision.blocked:
            return "BLOCKED"
        if not decision.valid:
            return "INVALID"
        if decision.trip_request:
            return "TRIP REQUEST"
        if decision.operate:
            return "OPERATING"
        if decision.pickup:
            return "PICKUP"
        return "NO OPERATION"


class ProtectionExplorer(QListWidget):
    """Read-side relay explorer synchronized with the canonical SelectionManager."""

    def __init__(self, application: Any, selection_manager: SelectionManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._application = application
        self._selection_manager = selection_manager
        self.currentRowChanged.connect(self._selection_changed)
        selection_manager.selection_changed.connect(self._selection_changed_from_manager)

    def refresh(self) -> None:
        self.clear()
        try:
            protection = self._application.read_protection()
        except RuntimeError:
            self.addItem("No active protection project")
            return
        for relay in protection.relays:
            self.addItem(f"{relay.name}  |  {relay.function_type}")
            self.item(self.count() - 1).setData(256, str(relay.object_id))

    def _selection_changed(self, row: int) -> None:
        if row < 0:
            return
        object_id = self.item(row).data(256) if self.item(row) is not None else None
        if object_id:
            self._selection_manager.select_single(object_id)

    def _selection_changed_from_manager(self, ids: object) -> None:
        selected = {str(value) for value in (ids or ())}
        if not selected:
            return
        for row in range(self.count()):
            if str(self.item(row).data(256)) in selected:
                self.blockSignals(True)
                self.setCurrentRow(row)
                self.blockSignals(False)
                return


class ProtectionInspector(QGroupBox):
    """Dedicated Protection configuration editor over Application commands."""

    def __init__(self, *, application: Any, selection_manager: SelectionManager, parent: QWidget | None = None) -> None:
        super().__init__("Protection Inspector", parent)
        self._application = application
        self._selection_manager = selection_manager
        self._label = QLabel("Select a protection object.", self)
        self._label.setWordWrap(True)
        self._editor_host = QWidget(self)
        self._editor_form = QFormLayout(self._editor_host)
        self._apply_button = QPushButton("Apply Function Settings", self)
        self._apply_button.setEnabled(False)
        self._editor_status = QLabel("", self)
        self._setting_controls: dict[str, Any] = {}
        self._active_element_id: str | None = None
        self._active_relay_id: str | None = None
        self._active_setting_snapshot: tuple[Any, ...] = ()
        self._relay_setting_snapshot: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(self._editor_host)
        layout.addWidget(self._apply_button)
        layout.addWidget(self._editor_status)
        self._apply_button.clicked.connect(self._apply_settings)
        selection_manager.selection_changed.connect(self._on_selection_changed)
        self.refresh()

    def _on_selection_changed(self, _ids: Any) -> None:
        self.refresh()

    def dispose(self) -> None:
        try:
            self._selection_manager.selection_changed.disconnect(self._on_selection_changed)
        except (RuntimeError, TypeError):
            pass

    def _clear_editor(self) -> None:
        while self._editor_form.count():
            item = self._editor_form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._setting_controls.clear()
        self._active_element_id = None
        self._active_relay_id = None
        self._active_setting_snapshot = ()
        self._relay_setting_snapshot = {}
        self._apply_button.setEnabled(False)
        self._editor_status.setText("")

    def _build_settings_editor(self, configuration: Any) -> None:
        self._clear_editor()
        if not configuration.settings_editable or not configuration.settings:
            self._editor_status.setText(
                "No implemented typed settings contract is available for this function."
            )
            return

        self._active_element_id = str(configuration.element_id)
        self._active_setting_snapshot = tuple(configuration.settings)
        for setting in configuration.settings:
            label = f"{setting.label} ({setting.unit})" if setting.unit else setting.label
            if setting.choices or setting.choices_by_value:
                control = QComboBox(self._editor_host)
                if setting.choices:
                    control.addItems(list(setting.choices))
                    if setting.value is not None and str(setting.value) in setting.choices:
                        control.setCurrentText(str(setting.value))
                else:
                    control.addItem("Select a curve…")
                self._setting_controls[setting.key] = control
            else:
                control = QLineEdit(
                    "" if setting.value is None else str(setting.value),
                    self._editor_host,
                )
                control.setPlaceholderText("Enter engineering value")
                self._setting_controls[setting.key] = control
            self._editor_form.addRow(label, control)

        # Curve options depend on the selected characteristic standard.
        for setting in configuration.settings:
            if not setting.choices_by or not setting.choices_by_value:
                continue
            selector = self._setting_controls.get(setting.choices_by)
            dependent = self._setting_controls.get(setting.key)
            if not isinstance(selector, QComboBox) or not isinstance(dependent, QComboBox):
                continue
            current_value = None if setting.value is None else str(setting.value)

            def refresh_choices(
                selected_standard: str,
                *,
                target: Any = dependent,
                definition: Any = setting,
                selected_value: str | None = current_value,
            ) -> None:
                options = tuple(definition.choices_by_value.get(selected_standard, ()))
                target.blockSignals(True)
                target.clear()
                target.addItem("Select a curve…")
                for option in options:
                    target.addItem(option)
                if selected_value in options:
                    target.setCurrentText(selected_value)
                target.blockSignals(False)

            selector.currentTextChanged.connect(refresh_choices)
            refresh_choices(selector.currentText())

        self._apply_button.setText("Apply Function Settings")
        self._apply_button.setEnabled(bool(self._setting_controls))

    def _build_physical_relay_editor(self, configuration: Any) -> None:
        self._clear_editor()
        self._active_relay_id = str(configuration.relay_id)
        self._relay_setting_snapshot = {
            "name": str(configuration.name),
            "enabled": bool(configuration.enabled),
            "blocked": bool(configuration.blocked),
            "in_service": bool(configuration.in_service),
        }
        name_control = QLineEdit(self._relay_setting_snapshot["name"], self._editor_host)
        self._setting_controls["name"] = name_control
        self._editor_form.addRow("Relay name", name_control)
        for key, label in (("in_service", "In service"), ("enabled", "Enabled"), ("blocked", "Blocked")):
            control = QCheckBox(self._editor_host)
            control.setChecked(self._relay_setting_snapshot[key])
            self._setting_controls[key] = control
            self._editor_form.addRow(label, control)
        self._apply_button.setText("Apply Physical Relay Configuration")
        self._apply_button.setEnabled(True)
        self._editor_status.setText(
            "Only Relay identity and lifecycle flags are editable here. "
            "Plugin-specific settings remain read-only without a typed plugin schema."
        )

    def _apply_physical_relay_settings(self) -> None:
        relay_id = self._active_relay_id
        if not relay_id:
            return
        name_control = self._setting_controls.get("name")
        if not isinstance(name_control, QLineEdit):
            return
        values = {
            "name": name_control.text(),
            "in_service": bool(self._setting_controls["in_service"].isChecked()),
            "enabled": bool(self._setting_controls["enabled"].isChecked()),
            "blocked": bool(self._setting_controls["blocked"].isChecked()),
        }
        try:
            result = self._application.update_physical_relay_configuration(
                relay_id=relay_id, **values
            )
        except Exception as exc:
            self._editor_status.setText(f"Relay configuration update rejected: {exc}")
            return
        if not getattr(result, "success", False):
            self._editor_status.setText(
                getattr(result, "message", "Relay configuration update was rejected.")
            )
            return
        self.refresh()
        self._editor_status.setText(
            getattr(result, "message", "Physical Relay configuration updated.")
        )

    def _apply_settings(self) -> None:
        if self._active_relay_id:
            self._apply_physical_relay_settings()
            return
        if not self._active_element_id:
            return
        updates: dict[str, Any] = {}
        for setting in self._active_setting_snapshot:
            control = self._setting_controls.get(setting.key)
            if isinstance(control, QComboBox):
                value = control.currentText()
                if value.startswith("Select a curve"):
                    value = ""
            elif isinstance(control, QLineEdit):
                value = control.text().strip()
            else:
                continue
            if not value:
                if setting.required or setting.choices_by:
                    self._editor_status.setText(
                        f"Select or enter a value for {setting.label}."
                    )
                    return
                continue
            updates[setting.key] = value

        try:
            result = self._application.update_protection_function_settings(
                element_id=self._active_element_id,
                settings=updates,
            )
        except Exception as exc:
            self._editor_status.setText(f"Settings update rejected: {exc}")
            return
        if not getattr(result, "success", False):
            self._editor_status.setText(
                getattr(result, "message", "Protection settings update was rejected.")
            )
            return
        self.refresh()
        self._editor_status.setText(
            getattr(result, "message", "Protection function settings updated.")
        )

    def refresh(self) -> None:
        self._clear_editor()
        selected = self._selection_manager.selected_ids
        if not selected:
            self._label.setText("Select a relay or a specific Protection function element.")
            return
        object_id = str(selected[0])
        try:
            protection = self._application.read_protection()
            configurations = self._application.read_protection_function_configurations()
        except RuntimeError:
            self._label.setText("Protection read boundary unavailable.")
            return
        relay_id = object_id.split(":", 1)[0]
        relay = next((item for item in protection.relays if str(item.object_id) == relay_id), None)
        if relay is None:
            self._label.setText(f"Protection presentation object: {object_id}")
            return

        relay_configurations = tuple(
            sorted(
                (item for item in configurations if str(item.relay_id) == relay_id),
                key=lambda item: str(item.element_id),
            )
        )
        selected_configuration = next(
            (
                item for item in relay_configurations
                if object_id.startswith(f"{relay_id}:element:{item.element_id}:")
            ),
            None,
        )
        visible_configurations = (
            (selected_configuration,)
            if selected_configuration is not None
            else relay_configurations
        )
        decision_by_element = {
            str(item.element_id): item
            for item in getattr(relay, "element_decisions", ())
            if str(item.relay_id) == relay_id
        }
        legacy_decision = getattr(relay, "decision", None)
        if (
            not decision_by_element
            and len(relay_configurations) == 1
            and legacy_decision is not None
            and str(getattr(legacy_decision, "element_id", "")) == str(relay_configurations[0].element_id)
        ):
            decision_by_element[str(relay_configurations[0].element_id)] = legacy_decision

        physical_state = (
            "DISABLED" if not bool(getattr(relay, "enabled", True))
            else "BLOCKED" if bool(getattr(relay, "blocked", False))
            else "TRIPPED" if bool(getattr(relay, "tripped", False))
            else "PICKED UP" if bool(getattr(relay, "picked_up", False))
            else "NO ACTIVE DEVICE STATE"
        )
        try:
            physical_configuration = self._application.read_physical_relay_configuration(relay_id)
        except (RuntimeError, KeyError):
            self._label.setText("Physical Relay configuration is no longer available; refresh the selection.")
            return
        lines = [
            "Identity", f"  {relay.object_id}",
            "Relay", f"  {physical_configuration.name} ({physical_configuration.relay_type})",
            "Physical Relay Function Label", f"  {relay.function_type}",
            "Plugin", f"  {physical_configuration.plugin_id or 'n/a'}",
            "Physical Relay Settings (read-only)", f"  {dict(physical_configuration.settings)}",
            f"  {physical_configuration.settings_editability_reason}",
            "Configured Protection Elements", f"  {len(relay_configurations)}",
            "Physical Relay Inputs", f"  {len(relay.input_channel_bindings)}",
            "Physical Relay State", f"  {physical_state}",
            "In Service", f"  {physical_configuration.in_service}",
            "Enabled", f"  {physical_configuration.enabled}",
            "Blocked", f"  {physical_configuration.blocked}",
        ]
        for configuration in visible_configurations:
            element_id = str(configuration.element_id)
            lines.extend([
                f"Protection Element: {element_id}",
                f"  ANSI Function: {configuration.function_code}",
                f"  Input / Measurement Bindings: {dict(configuration.input_channel_ids)}",
                f"  Residual Current Source Configuration: {dict(configuration.residual_current_input) if configuration.residual_current_input is not None else 'n/a'}",
                f"  Settings: {', '.join(f'{item.label} ({item.key}) = {item.value!r} {item.unit}' for item in configuration.settings) or 'No typed settings metadata'}",
                f"  Unmodelled Settings (read-only): {dict(configuration.unmodeled_settings)}",
                f"  Enabled: {configuration.enabled}",
                f"  Blocked: {configuration.blocked}",
            ])
            try:
                specification = get_protection_function(configuration.function_code)
                lines.append(f"  Implementation Status: {specification.status.value}")
                if specification.input_contract is not None and not specification.input_contract.supported:
                    lines.append(
                        f"  Input Contract: FAIL-CLOSED — {specification.input_contract.unsupported_reason}"
                    )
            except (KeyError, TypeError, ValueError):
                lines.append("  Implementation Status: catalogue entry unavailable")

            decision = decision_by_element.get(element_id)
            if decision is None:
                if not bool(getattr(configuration, "enabled", True)):
                    lines.append("  Decision: DISABLED — this element was not evaluated.")
                else:
                    lines.append("  Decision: No evaluated ProtectionDecision is available for this element.")
            else:
                lines.extend([
                    f"  Decision: {ProtectionGraphicsSurface._decision_text(decision)}",
                    f"  Pickup: {decision.pickup}",
                    f"  Operate: {decision.operate}",
                    f"  Trip Request: {decision.trip_request}",
                    f"  Valid: {decision.valid}",
                    f"  Reason: {decision.reason or 'n/a'}",
                    f"  Operating Time: {decision.operating_time if decision.operating_time is not None else 'n/a'}",
                    f"  Timestamp: {decision.timestamp if decision.timestamp is not None else 'n/a'}",
                ])

        if not relay_configurations:
            lines.extend([
                "Commissioning Status",
                "  NOT COMMISSIONED — no ProtectionFunctionConfiguration is associated with this Relay.",
                "  Protection evaluation is not implied by the physical Relay function label or its settings.",
            ])
        self._label.setText("\n".join(lines))
        if selected_configuration is not None:
            self._build_settings_editor(selected_configuration)
        elif object_id == relay_id:
            self._build_physical_relay_editor(physical_configuration)


class ProtectionToolbar(QToolBar):
    """Canonical Protection tool activation surface."""

    def __init__(self, interaction: ProtectionInteractionController, *, on_fit: Any = None, on_diagnostics: Any = None, parent: QWidget | None = None) -> None:
        super().__init__("Protection Engineering", parent)
        self._actions: dict[str, QAction] = {}
        self._on_fit = on_fit
        self._on_diagnostics = on_diagnostics
        for tool_id, text in (
            ("select", "Select"),
            ("connect_measurement", "Connect Measurement"),
            ("inspect", "Inspect"),
            ("fit", "Fit"),
            ("diagnostics", "Diagnostics"),
        ):
            action = self.addAction(text)
            action.setCheckable(tool_id in {"select", "connect_measurement", "inspect"})
            if tool_id in {"select", "connect_measurement", "inspect"}:
                action.triggered.connect(lambda _checked=False, value=tool_id: interaction.activate(value))
            elif tool_id == "fit" and callable(on_fit):
                action.triggered.connect(lambda _checked=False: on_fit())
            elif tool_id == "diagnostics" and callable(on_diagnostics):
                action.triggered.connect(lambda _checked=False: on_diagnostics())
            self._actions[tool_id] = action
        self._actions["select"].setChecked(True)

    def set_active(self, tool_id: str) -> None:
        for key, action in self._actions.items():
            action.setChecked(key == tool_id)


def _study_event_matches_active_project(application: Any, payload: Any) -> bool:
    """Reject lifecycle events emitted by a different project activation."""
    if not hasattr(payload, "get"):
        return False
    project_id = payload.get("project_id")
    generation = payload.get("activation_generation")
    if project_id is None or generation is None:
        return False
    try:
        lifecycle = application.project_lifecycle
        context = lifecycle.context
        active = (
            lifecycle.state == "ACTIVE"
            and lifecycle.has_project
            and context is not None
        )
        return bool(
            active
            and str(project_id) == str(context.project_id)
            and int(generation) == int(lifecycle.activation_generation)
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


class ProtectionWorkspace(QWidget):
    workspace_id = "protection"
    _EVENT_TYPES = (
        ElementCreated, ElementRemoved, ElementUpdated, NetworkChanged,
        ProtectionChanged, TopologyChanged, NetworkCommitted,
        ProjectLoaded, ProjectClosed, InstrumentationWiringChanged,
        StudyStarted, StudyCompleted, StudyFailed, StudyCancelled,
    )

    def __init__(self, *, application: Any, selection_manager: SelectionManager | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if application is None:
            raise ValueError("application is required.")
        if selection_manager is None:
            raise ValueError("ProtectionWorkspace requires the canonical SelectionManager.")
        self._application = application
        self._selection_manager = selection_manager
        self._state_machine = CanvasStateMachine(workspace_id=self.workspace_id)
        self._adapter = CanvasInteractionAdapter(workspace_id=self.workspace_id, discipline=self.workspace_id)
        self._interaction = ProtectionInteractionController(
            application=application,
            selection_manager=selection_manager,
            adapter=self._adapter,
        )
        existing = getattr(application, "protection_presentation", None)
        self._presentation = (
            existing if isinstance(existing, ProtectionPresentationDocument)
            else ProtectionPresentationDocument.from_dict(existing)
            if isinstance(existing, dict) else ProtectionPresentationDocument()
        )
        application.protection_presentation = self._presentation
        self._explorer = ProtectionExplorer(application, selection_manager, self)
        self._canvas = ProtectionGraphicsSurface(
            application=application,
            selection_manager=selection_manager,
            adapter=self._adapter,
            interaction=self._interaction,
            presentation=self._presentation,
            parent=self,
        )
        self._inspector = ProtectionInspector(application=application, selection_manager=selection_manager, parent=self)

        # Engineer-facing navigation exposes the canonical function catalogue
        # alongside live relay objects; analysis remains a read-only projection.
        self._explorer_tabs = QTabWidget(self)
        self._explorer_tabs.addTab(self._explorer, "Relays")
        self._function_catalog = ProtectionFunctionCatalogPanel(self)
        self._explorer_tabs.addTab(self._function_catalog, "ANSI Catalogue")

        self._inspector_tabs = QTabWidget(self)
        self._inspector_tabs.addTab(self._inspector, "Relay Details")
        self._tcc_panel = ProtectionTCCPanel(
            application=application, selection_manager=selection_manager, parent=self
        )
        self._inspector_tabs.addTab(self._tcc_panel, "TCC")
        self._analysis_coverage = ProtectionAnalysisCoveragePanel(self)
        self._inspector_tabs.addTab(self._analysis_coverage, "Analysis Coverage")
        self._coordination_study = ProtectionCoordinationStudyPanel(
            application=application, parent=self
        )
        self._inspector_tabs.addTab(self._coordination_study, "Coordination Study")

        self._toolbar = ProtectionToolbar(
            self._interaction,
            on_fit=self._canvas.fit_to_content,
            on_diagnostics=self._canvas.show_diagnostics,
            parent=self,
        )
        self._subscriptions: list[tuple[Any, Any]] = []
        for event_type in self._EVENT_TYPES:
            application.event_bus.subscribe(event_type, self._on_application_event)
            self._subscriptions.append((event_type, self._on_application_event))
        self._toolbar.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas, 1)
        # Explorer, inspector and the historical toolbar are retained as
        # compatibility-owned widgets and are realized by ProtectionEditor.
        self.refresh()

    @property
    def selection_manager(self) -> SelectionManager:
        return self._selection_manager

    @property
    def active_tool_id(self) -> str:
        return self._interaction.active_tool

    def activate_tool_id(self, tool_id: str) -> None:
        """Activate a Protection tool by its canonical ToolDefinition ID."""
        normalized = str(tool_id).strip().lower().replace(" ", "_")
        self._interaction.activate(normalized)
        if normalized == "fit":
            self._canvas.fit_to_content()
        elif normalized == "diagnostics":
            self._canvas.show_diagnostics()
        self._toolbar.set_active(self._interaction.active_tool)

    @property
    def editor_canvas(self) -> QWidget:
        return self._canvas

    @property
    def editor_explorer(self) -> QWidget:
        return self._explorer_tabs

    @property
    def editor_inspector(self) -> QWidget:
        return self._inspector_tabs

    @property
    def canvas_state(self):
        return self._state_machine.state

    @property
    def presentation(self) -> ProtectionPresentationDocument:
        return self._presentation

    def refresh(self) -> None:
        self._explorer.refresh()
        self._canvas.refresh()
        self._inspector.refresh()
        self._function_catalog.refresh()
        self._tcc_panel.refresh()
        self._analysis_coverage.refresh()
        self._coordination_study.refresh()
        self._toolbar.set_active(self._interaction.active_tool)

    def _on_application_event(self, event: Any) -> None:
        if isinstance(event, ProjectClosed):
            self._coordination_study.clear_in_progress()
            self._interaction.cancel()
            self._presentation.clear()
            self._selection_manager.clear()
        elif isinstance(event, (StudyStarted, StudyCompleted, StudyFailed, StudyCancelled)):
            payload = getattr(event, "payload", {})
            # Study execution is scoped to a project activation. A late event
            # from a closed/replaced project must never clear or create the
            # active project's in-progress marker for a reused Study Case ID.
            if not _study_event_matches_active_project(self._application, payload):
                return
            study_id = payload.get("study_id")
            if study_id is not None:
                self._coordination_study.set_study_in_progress(
                    study_id, isinstance(event, StudyStarted)
                )
        elif isinstance(event, ProjectLoaded):
            self._coordination_study.clear_in_progress()
            loaded = getattr(self._application, "protection_presentation", None)
            if isinstance(loaded, dict):
                restored = ProtectionPresentationDocument.from_dict(loaded)
                self._presentation.clear()
                self._presentation.nodes.update(restored.nodes)
                self._presentation.connections.update(restored.connections)
                self._application.protection_presentation = self._presentation
        self.refresh()
    def dispose(self) -> None:
        for event_type, handler in tuple(self._subscriptions):
            self._application.event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._interaction.dispose()
        self._state_machine.cancel()
        self._inspector.dispose()
        self._tcc_panel.dispose()


__all__ = [
    "ProtectionWorkspace", "ProtectionExplorer", "ProtectionGraphicsSurface",
    "ProtectionInspector", "ProtectionToolbar",
]
