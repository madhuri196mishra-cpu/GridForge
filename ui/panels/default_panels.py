# ============================================================
# GridForge V2 — Default Panels
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4
import logging
from typing import Any

from core.application.errors import ExecutionError

from ui.projection.projection_state import EngineeringParameterState, ProjectionState
from ui.creation.creation_context import CreationContext, CreationDraft

from ui.core.qt import (
    QCheckBox, QComboBox, QDoubleSpinBox, QDoubleValidator, QIntValidator, QFormLayout, QLabel, QLineEdit, Signal,
    QListWidget, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QSize, QLineEdit, Qt,
)
from ui.plugins.panels_plugin import PanelSpec
from ui.equipment.symbol.palette_symbol_adapter import PaletteSymbolAdapter
from .element_list_panel import ElementListPanelWidget
from .messages_panel import MessagesPanelWidget
from .properties_panel import PropertiesPanel
from .engineering_parameter_editor import EngineeringParameterEditor
from .transformer_configuration_editor import TransformerConfigurationEditor
from .study_cases_panel import StudyCasesPanelWidget


class ProjectPanelWidget(QWidget):
    """Presentation surface for the Application project hierarchy projection."""

    _SYSTEM_GROUPS = (
        "Buses", "Equipment", "Lines", "Transformers", "Loads",
        "Generators", "Shunts", "Measurements", "Protection", "Scenarios",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridForgePanel_project")
        self._hierarchy: Any | None = None
        self._selection_manager: Any | None = None
        self._updating_selection = False
        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setObjectName("ProjectHierarchyTree")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._tree)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)

    def bind_selection_manager(self, selection_manager: Any) -> None:
        if selection_manager is None or not callable(getattr(selection_manager, "select_single", None)):
            raise TypeError("selection_manager must provide select_single().")
        self._selection_manager = selection_manager
        selection_manager.selection_changed.connect(self._on_canonical_selection)

    @property
    def hierarchy(self) -> Any | None:
        return self._hierarchy

    def set_hierarchy(self, hierarchy: Any | None) -> None:
        self._hierarchy = hierarchy
        self._render_hierarchy()

    def clear_hierarchy(self) -> None:
        self._hierarchy = None
        self._tree.clear()

    def _on_tree_selection(self) -> None:
        if self._updating_selection or self._selection_manager is None:
            return
        items = self._tree.selectedItems()
        if not items:
            return
        object_id = items[0].data(0, 32)
        if object_id:
            self._selection_manager.select_single(str(object_id))

    def _on_canonical_selection(self, ids: object) -> None:
        selected = {str(value) for value in (ids or ())}
        self._updating_selection = True
        try:
            self._tree.clearSelection()
            if not selected:
                return
            iterator = self._tree.findItems("", Qt.MatchFlag.MatchContains | Qt.MatchFlag.MatchRecursive, 0)
            for item in iterator:
                if str(item.data(0, 32) or "") in selected:
                    item.setSelected(True)
                    self._tree.scrollToItem(item)
                    break
        finally:
            self._updating_selection = False

    def _render_hierarchy(self) -> None:
        self._tree.clear()
        if not isinstance(self._hierarchy, dict):
            return
        project = self._hierarchy.get("project") or {}
        root = QTreeWidgetItem([str(project.get("name") or "Project")])
        root.setData(0, 32, str(project.get("id") or ""))
        self._tree.addTopLevelItem(root)

        network = QTreeWidgetItem(["Network Elements"])
        root.addChild(network)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for element in self._hierarchy.get("network_elements") or ():
            grouped.setdefault(str(element.get("type") or "Other"), []).append(element)
        for element_type, elements in sorted(grouped.items()):
            group = QTreeWidgetItem([element_type.title()])
            network.addChild(group)
            for element in elements:
                item = QTreeWidgetItem([str(element.get("name") or element.get("id"))])
                item.setData(0, 32, str(element.get("id") or ""))
                group.addChild(item)

        documents_node = QTreeWidgetItem(["Documents"])
        root.addChild(documents_node)
        for document in project.get("documents") or ():
            if isinstance(document, dict):
                documents_node.addChild(QTreeWidgetItem([
                    str(document.get("name") or document.get("type") or "Document")
                ]))

        studies = QTreeWidgetItem(["Study Cases"])
        root.addChild(studies)
        workspace_id = self._hierarchy.get("workspace_id")
        if workspace_id:
            studies.addChild(QTreeWidgetItem([f"Workspace: {workspace_id}"]))

        root.setExpanded(True)
        network.setExpanded(True)
        for group in (network.child(i) for i in range(network.childCount())):
            group.setExpanded(True)
        documents_node.setExpanded(True)
        studies.setExpanded(True)


class EquipmentPanelWidget(QWidget):
    """Canonical Equipment Browser backed by the project-independent catalogue."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridForgePanel_equipment")
        self._equipment_registry: Any | None = None
        self._tool_manager: Any | None = None
        self._definitions: tuple[Any, ...] = ()
        self._visible_definitions: tuple[Any, ...] = ()
        self._selected_equipment_type: str | None = None
        self._active_tool_id: str | None = None
        self._properties_panel: Any | None = None
        self._symbol_registry: Any | None = None
        self._palette_symbol_adapter: PaletteSymbolAdapter | None = None

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search components…")
        self._search.setObjectName("EquipmentSearch")
        self._list = QListWidget(self)
        self._list.setViewMode(QListWidget.ViewMode.IconMode)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setMovement(QListWidget.Movement.Static)
        self._list.setIconSize(QSize(72, 52))
        self._list.setObjectName("EquipmentPalette")
        self._list.setGridSize(QSize(112, 88))
        self._list.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Equipment Library", self))
        layout.addWidget(self._search)
        layout.addWidget(self._list, 1)
        self._search.textChanged.connect(self._filter_equipment)
        self._list.itemClicked.connect(self._on_item_clicked)

    @property
    def equipment_registry(self) -> Any | None:
        return self._equipment_registry

    @property
    def equipment_definitions(self) -> tuple[Any, ...]:
        return self._definitions

    @property
    def selected_equipment_type(self) -> str | None:
        return self._selected_equipment_type

    @property
    def active_tool_id(self) -> str | None:
        return self._active_tool_id

    def bind_equipment_runtime(self, equipment_registry: Any, tool_manager: Any, properties_panel: Any | None = None, symbol_registry: Any | None = None) -> None:
        """Receive the canonical catalogue and live ToolManager from composition."""
        if equipment_registry is None or not callable(getattr(equipment_registry, "catalogue", None)):
            raise TypeError("equipment_registry must provide catalogue().")
        if tool_manager is None or not callable(getattr(tool_manager, "activate", None)):
            raise TypeError("tool_manager must provide activate().")
        if symbol_registry is None:
            raise TypeError("symbol_registry must be the canonical SymbolRegistry.")
        self._symbol_registry = symbol_registry
        self._palette_symbol_adapter = PaletteSymbolAdapter(symbol_registry)
        self._equipment_registry = equipment_registry
        self._tool_manager = tool_manager
        self._properties_panel = properties_panel
        self._definitions = tuple(equipment_registry.catalogue())
        self._rebuild_equipment_list()

    def _rebuild_equipment_list(self) -> None:
        query = self._search.text().strip().lower()
        self._visible_definitions = tuple(
            definition for definition in self._definitions
            if not query
            or query in str(definition.display_name).lower()
            or query in str(definition.category).lower()
            or query in str(definition.equipment_type).lower()
        )
        self._list.clear()
        last_category: str | None = None
        for definition in self._visible_definitions:
            category = str(definition.category)
            if category != last_category:
                self._list.addItem(f"— {category} —")
                header_item = self._list.item(self._list.count() - 1)
                header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                header_item.setData(32, None)
                last_category = category
            self._list.addItem(definition.display_name)
            item = self._list.item(self._list.count() - 1)
            if self._palette_symbol_adapter is None:
                raise RuntimeError("Equipment palette symbol adapter is not configured.")
            item.setIcon(self._palette_symbol_adapter.icon_for(definition.symbol_id))
            item.setToolTip(
                f"{definition.display_name}\n"
                f"Category: {category}\n"
                f"Tool: {definition.tool_id}\n"
                f"Symbol: {definition.symbol_id}"
            )
            item.setData(32, definition.equipment_type)

    def _filter_equipment(self, _text: str) -> None:
        if self._equipment_registry is not None:
            self._rebuild_equipment_list()

    def select_equipment_type(self, equipment_type: str | None) -> None:
        if equipment_type is None:
            self._selected_equipment_type = None
            return
        for definition in self._definitions:
            if definition.equipment_type == equipment_type:
                self._selected_equipment_type = equipment_type
                return
        raise KeyError(f"Unknown catalogue equipment type: {equipment_type!r}")

    def activate_selected_equipment(self) -> Any:
        if self._selected_equipment_type is None:
            raise RuntimeError("No equipment type is selected.")
        return self.activate_equipment(self._selected_equipment_type)

    def activate_equipment(self, equipment_type: str) -> Any:
        if self._tool_manager is None:
            raise RuntimeError("Equipment Browser has not been composed with ToolManager.")
        if self._equipment_registry is None:
            raise RuntimeError("Equipment Browser has no EquipmentRegistry.")
        definition = self._equipment_registry.require(equipment_type)
        self._selected_equipment_type = definition.equipment_type
        tool_id = definition.tool_id

        # Palette selection is an explicit user request to change equipment.
        # If another creation session is active, cancel that transient session
        # through ToolManager before requesting the new tool.  ToolManager
        # intentionally rejects implicit destruction of an active draft.
        active_tool_id = self._tool_manager.active_tool_id
        explicit_creation_switch = (
            active_tool_id is not None
            and active_tool_id != tool_id
        )

        tool = self._tool_manager.activate(
            tool_id,
            cancel_active_creation=explicit_creation_switch,
        )
        self._active_tool_id = self._tool_manager.active_tool_id
        if self._properties_panel is not None:
            setter = getattr(self._properties_panel, "set_creation_draft", None)
            if callable(setter):
                setter(self._tool_manager.creation_context.draft)
        return tool

    def configure_active_tool(self, **parameters: Any) -> None:
        """Compatibility adapter into the canonical CreationDraft."""
        if self._tool_manager is None:
            raise RuntimeError("Equipment Browser has not been composed with ToolManager.")
        if not parameters:
            raise ValueError("Creation parameters must not be empty.")
        self._tool_manager.creation_context.update_many(parameters)
        if self._properties_panel is not None:
            setter = getattr(self._properties_panel, "set_creation_draft", None)
            if callable(setter):
                setter(self._tool_manager.creation_context.draft)

    def _on_item_clicked(self, item: Any) -> None:
        equipment_type = item.data(32)
        if not equipment_type:
            return
        self.activate_equipment(str(equipment_type))


class PropertiesPanelWidget(QWidget):
    """Qt realization of the canonical PropertiesPanel projection."""

    _commit_future_completed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GridForgePanel_properties")
        self.logical_panel = PropertiesPanel()
        self.logical_panel.on_create()
        self._engineering_editor: EngineeringParameterEditor | None = None
        self._transformer_configuration_editor = TransformerConfigurationEditor(self)
        self._application: Any | None = None
        self._parameter_controls: dict[str, QWidget] = {}
        self._parameter_states: dict[str, EngineeringParameterState] = {}
        self._apply_button: QPushButton | None = None
        self._validation_label: QLabel | None = None
        self._form_layout: QFormLayout | None = None
        self._creation_context: CreationContext | None = None
        self._creation_mode = False
        self._creation_controller: Any | None = None
        self._apply_changes_connected = True
        self._place_draft_connected = False
        self._commit_network_button: QPushButton | None = None
        self._commit_future_completed.connect(self._on_commit_future_completed)
        self._build_controls()

    def _build_controls(self) -> None:
        root = QVBoxLayout(self)
        self._form_layout = QFormLayout()
        root.addLayout(self._form_layout)
        root.addWidget(self._transformer_configuration_editor)
        self._annotation_layout = QFormLayout()
        root.addWidget(QLabel("SLD Data Annotations", self))
        root.addLayout(self._annotation_layout)
        self._sld_controller: Any | None = None
        self._sld_selection_ids: tuple[str, ...] = ()
        self._sld_annotations_cleared = False
        self._validation_label = QLabel("Select an element to inspect.", self)
        self._validation_label.setProperty("role", "validationInfo")
        self._validation_label.setWordWrap(True)
        root.addWidget(self._validation_label)
        self._apply_button = QPushButton("Apply / Commit", self)
        self._apply_button.setProperty("role", "primaryAction")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply_changes)
        root.addWidget(self._apply_button)
        self._commit_network_button = QPushButton("Commit Network", self)
        self._commit_network_button.setProperty("role", "commitNetworkAction")
        self._commit_network_button.setEnabled(False)
        self._commit_network_button.clicked.connect(self._commit_network)
        root.addWidget(self._commit_network_button)

    def bind_configuration_runtime(self, application: Any, creation_context: CreationContext | None = None, controller: Any | None = None) -> None:
        self._application = application
        self._engineering_editor = EngineeringParameterEditor(application)
        self._transformer_configuration_editor.bind_application(application)
        self._creation_context = creation_context
        self._creation_controller = controller
        if controller is not None:
            signal = getattr(controller, "tool_changed", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                connect(self._on_tool_changed)
            state_signal = getattr(controller, "state_changed", None)
            state_connect = getattr(state_signal, "connect", None)
            if callable(state_connect):
                state_connect(self._refresh_commit_network_action)
        self._refresh_commit_network_action()

    def bind_sld_annotation_runtime(self, *, controller: Any, selection_manager: Any) -> None:
        """Bind annotation editing to canonical SLD selection and Application commands."""
        if controller is None or not callable(getattr(controller, "set_annotation_blocks", None)):
            raise TypeError("controller must provide set_annotation_blocks().")
        if selection_manager is None or not callable(getattr(selection_manager, "select_single", None)):
            raise TypeError("selection_manager must be the canonical selection manager.")
        self._sld_controller = controller
        signal = getattr(selection_manager, "selection_changed", None)
        connect = getattr(signal, "connect", None)
        if not callable(connect):
            raise TypeError("selection_manager must expose selection_changed.connect().")
        connect(self._on_sld_selection_changed)
        self._refresh_sld_annotation_controls()

    def _on_sld_selection_changed(self, ids: Any) -> None:
        self._sld_selection_ids = tuple(str(value) for value in (ids or ()))
        if self._sld_annotations_cleared:
            self.clear_sld_annotation_controls()
            return
        self._refresh_sld_annotation_controls()

    def _selected_sld_node(self) -> Any | None:
        controller = self._sld_controller
        document = getattr(controller, "active_document", None) if controller is not None else None
        if document is None:
            return None
        if len(self._sld_selection_ids) != 1:
            return None
        selected_id = self._sld_selection_ids[0]
        node = document.model.get_node_optional(selected_id)
        if node is not None:
            return node
        return document.model.get_node_by_equipment_id_optional(selected_id)

    def refresh_sld_annotation_controls(self) -> None:
        """Refresh annotation controls while preserving the current selection."""
        self._sld_annotations_cleared = False
        self._refresh_sld_annotation_controls()

    def handle_sld_document_changed(self) -> None:
        """Drop selection IDs from the previous document before rebuilding controls."""
        self._sld_selection_ids = ()
        self._sld_annotations_cleared = False
        self._refresh_sld_annotation_controls()

    def clear_sld_annotation_controls(self) -> None:
        """Clear document-specific annotation controls when no SLD document is active."""
        self._sld_annotations_cleared = True
        self._sld_selection_ids = ()
        layout = getattr(self, "_annotation_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        layout.addRow(QLabel("No active SLD document.", self))

    def _refresh_sld_annotation_controls(self) -> None:
        layout = getattr(self, "_annotation_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        controller = self._sld_controller
        document = getattr(controller, "active_document", None) if controller is not None else None
        node = self._selected_sld_node()
        if document is None or node is None:
            layout.addRow(QLabel("Select one SLD node to configure its data annotation.", self))
            return

        blocks = [block.to_dict() for block in document.annotation_blocks if block.node_id == node.node_id]
        if not blocks:
            layout.addRow(QLabel(f"Node: {node.node_id} · no annotation block configured.", self))
        for block in blocks:
            block_id = str(block["block_id"])
            layout.addRow(QLabel(f"Block: {block_id}", self))
            fields_edit = QLineEdit(", ".join(block["fields"]), self)
            visible_check = QCheckBox("Visible", self)
            visible_check.setChecked(bool(block["visible"]))
            offset_x = QDoubleSpinBox(self)
            offset_y = QDoubleSpinBox(self)
            for control, key in ((offset_x, "offset_x"), (offset_y, "offset_y")):
                control.setRange(-1000000.0, 1000000.0)
                control.setDecimals(2)
                control.setValue(float(block[key]))
            layout.addRow("Fields", fields_edit)
            layout.addRow("Offset X", offset_x)
            layout.addRow("Offset Y", offset_y)
            layout.addRow(visible_check)
            save_button = QPushButton("Apply Annotation Changes", self)
            save_button.clicked.connect(
                lambda _checked=False, bid=block_id, fields=fields_edit, visible=visible_check,
                       ox=offset_x, oy=offset_y: self._save_sld_annotation(
                           bid, fields.text(), visible.isChecked(), ox.value(), oy.value()
                       )
            )
            remove_button = QPushButton("Remove Annotation", self)
            remove_button.clicked.connect(
                lambda _checked=False, bid=block_id: self._remove_sld_annotation(bid)
            )
            layout.addRow(save_button, remove_button)

        add_button = QPushButton("Add Data Annotation Block", self)
        add_button.clicked.connect(lambda _checked=False, nid=node.node_id: self._add_sld_annotation(nid))
        layout.addRow(add_button)

    def _save_sld_annotation(
        self, block_id: str, fields_text: str, visible: bool, offset_x: float, offset_y: float
    ) -> None:
        controller = self._sld_controller
        document = getattr(controller, "active_document", None) if controller is not None else None
        if document is None:
            return
        fields = [value.strip() for value in fields_text.split(",") if value.strip()]
        blocks = [block.to_dict() for block in document.annotation_blocks]
        found = False
        for block in blocks:
            if block["block_id"] == block_id:
                block.update(fields=fields, visible=visible, offset_x=offset_x, offset_y=offset_y)
                found = True
                break
        if not found:
            return
        try:
            controller.set_annotation_blocks(blocks)
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(f"Annotation update rejected: {exc}")
            return
        self._refresh_sld_annotation_controls()

    def _add_sld_annotation(self, node_id: str) -> None:
        controller = self._sld_controller
        document = getattr(controller, "active_document", None) if controller is not None else None
        if document is None or document.model.get_node_optional(node_id) is None:
            return
        blocks = [block.to_dict() for block in document.annotation_blocks]
        blocks.append({
            "block_id": "annotation-" + uuid4().hex,
            "node_id": node_id,
            "visible": True,
            "fields": ["identity", "type"],
            "offset_x": 70.0,
            "offset_y": -20.0,
        })
        try:
            controller.set_annotation_blocks(blocks)
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(f"Annotation creation rejected: {exc}")
            return
        self._refresh_sld_annotation_controls()

    def _remove_sld_annotation(self, block_id: str) -> None:
        controller = self._sld_controller
        document = getattr(controller, "active_document", None) if controller is not None else None
        if document is None:
            return
        blocks = [block.to_dict() for block in document.annotation_blocks if block.block_id != block_id]
        try:
            controller.set_annotation_blocks(blocks)
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(f"Annotation removal rejected: {exc}")
            return
        self._refresh_sld_annotation_controls()

    def configure_parameter(self, parameter_id: str, value: Any) -> Any:
        if self._engineering_editor is None:
            raise RuntimeError("Properties configuration runtime is not bound.")
        target = self.logical_panel.target
        if target is None:
            raise RuntimeError("No projected element is selected.")
        intent = self._engineering_editor.intent_from_projection(target, {parameter_id: value})
        return self._engineering_editor.submit(intent)

    @property
    def target(self) -> Any | None:
        return self.logical_panel.target

    def set_target(self, target: Any | None) -> None:
        self._creation_mode = False
        self.logical_panel.set_target(target)
        self._render_projection(target)

    def set_creation_draft(self, draft: CreationDraft | None) -> None:
        if draft is None:
            self._creation_mode = False
            if self.logical_panel.target is None:
                self._render_projection(None)
            return
        self._creation_mode = True
        self.logical_panel.clear_target()
        self._render_creation_draft(draft)


    def clear_target(self) -> None:
        self.logical_panel.clear_target()
        if self._creation_context is not None and self._creation_context.draft is not None:
            self._creation_mode = True
            self._render_creation_draft(self._creation_context.draft)
        else:
            self._creation_mode = False
            self._render_projection(None)


    def _render_projection(self, target: ProjectionState | None) -> None:
        self._transformer_configuration_editor.set_target(target)
        self._clear_parameter_controls()
        if target is None:
            if self._validation_label is not None:
                self._validation_label.setText("Select an element to inspect.")
                self._validation_label.setProperty("role", "validationInfo")
            if self._apply_button is not None:
                if not self._apply_changes_connected:
                    try:
                        self._apply_button.clicked.disconnect(self._place_draft)
                    except (RuntimeError, TypeError):
                        pass
                    self._place_draft_connected = False
                    self._apply_button.clicked.connect(self._apply_changes)
                    self._apply_changes_connected = True
                self._apply_button.setText("Apply / Commit")
                self._apply_button.setProperty("role", "primaryAction")
                self._apply_button.setEnabled(False)
            return
        self._parameter_states = {item.parameter_id: item for item in target.engineering_parameters}
        assert self._form_layout is not None
        self._form_layout.addRow(QLabel("Identity", self), QLabel(str(target.object_id), self))
        self._form_layout.addRow(QLabel("Type", self), QLabel(str(target.display_type), self))
        self._form_layout.addRow(
            QLabel("Status", self),
            QLabel("Draft" if target.identity_kind == "draft" else str(target.status or "Unknown"), self),
        )
        assessment = target.configuration_assessment
        if assessment is not None and target.identity_kind == "element":
            self._form_layout.addRow(
                QLabel("Configuration assessment", self),
                QLabel(str(assessment.status), self),
            )
            if not assessment.fields:
                self._form_layout.addRow(
                    QLabel("Configuration evidence", self),
                    QLabel("No trusted creation-disposition evidence; engineering review is required.", self),
                )
            for field_assessment in assessment.fields:
                detail = str(field_assessment.status)
                if field_assessment.workflow:
                    detail += f" · Workflow: {field_assessment.workflow}"
                if field_assessment.reason:
                    detail += f" · {field_assessment.reason}"
                detail_label = QLabel(detail, self)
                detail_label.setWordWrap(True)
                self._form_layout.addRow(QLabel(str(field_assessment.field_name), self), detail_label)
        if target.identity_kind == "draft":
            self._form_layout.addRow(QLabel("Placement", self), QLabel(str(target.placement), self))
            self._form_layout.addRow(QLabel("Terminals", self), QLabel(", ".join(target.terminal_contract), self))
            if target.endpoint_references:
                self._form_layout.addRow(
                    QLabel("Acquired Endpoints", self),
                    QLabel(", ".join(f"{key}: {value}" for key, value in target.endpoint_references.items()), self),
                )
        if target.connectivity_refs:
            self._form_layout.addRow(QLabel("Connections", self), QLabel(", ".join(map(str, target.connectivity_refs)), self))
        unavailable_count = sum(not item.inspectable for item in target.engineering_parameters)
        self._form_layout.addRow(QLabel("Engineering Parameters", self), QLabel(
            f"{len(target.engineering_parameters)} catalogue parameter(s); "
            f"{unavailable_count} unavailable in the committed read model. Core validation remains authoritative.",
            self,
        ))
        for parameter in target.engineering_parameters:
            control = self._create_parameter_control(parameter)
            self._parameter_controls[parameter.parameter_id] = control
            assert self._form_layout is not None
            self._form_layout.addRow(self._parameter_label(parameter), control)
        if self._validation_label is not None:
            prefix = "Draft" if target.identity_kind == "draft" else target.display_type
            self._validation_label.setText(
                f"{prefix} {target.object_id} · "
                + ("DraftNetwork validation is authoritative until Commit Network." if target.identity_kind == "draft"
                   else "Core validation is authoritative on commit.")
            )
        if self._apply_button is not None:
            if self._place_draft_connected:
                try:
                    self._apply_button.clicked.disconnect(self._place_draft)
                except (RuntimeError, TypeError):
                    pass
                self._place_draft_connected = False
            if not self._apply_changes_connected:
                self._apply_button.clicked.connect(self._apply_changes)
                self._apply_changes_connected = True
            self._apply_button.setText("Apply Draft Changes" if target.identity_kind == "draft" else "Apply / Commit")
            self._apply_button.setProperty("role", "primaryAction")
            self._apply_button.setEnabled(
                any(item.editable and not item.derived for item in target.engineering_parameters)
            )

    def _refresh_commit_network_action(self, *args: Any) -> None:
        del args
        button = self._commit_network_button
        if button is None or self._engineering_editor is None:
            return
        application = self._application
        try:
            has_draft = bool(application is not None and application.read_draft_network())
        except (RuntimeError, TypeError, ValueError):
            has_draft = False
        button.setEnabled(has_draft)

    def _commit_network(self) -> None:
        controller = self._creation_controller
        commit = getattr(controller, "commit_network_async", None) if controller is not None else None
        if not callable(commit):
            if self._validation_label is not None:
                self._validation_label.setText("Non-blocking Commit Network action is not bound.")
                self._validation_label.setProperty("role", "validationError")
            return
        try:
            future = commit()
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(str(exc))
                self._validation_label.setProperty("role", "validationError")
            return
        if self._commit_network_button is not None:
            self._commit_network_button.setEnabled(False)
            self._commit_network_button.setText("Committing Network…")
        if self._validation_label is not None:
            self._validation_label.setText("Validating and committing network in the background…")
            self._validation_label.setProperty("role", "validationInfo")
        future.add_done_callback(self._commit_future_completed.emit)

    def _on_commit_future_completed(self, future: Any) -> None:
        """Present worker completion on the Qt object's owning thread."""
        try:
            result = future.result()
        except ExecutionError as exc:
            self._abort_async_commit()
            cause = exc.cause or exc.__cause__
            reason = str(cause).strip() if cause is not None else ""
            normalized_reason = reason.lower()
            is_empty_draft_rejection = (
                exc.code == "COMMAND_EXECUTION_FAILED"
                and isinstance(cause, ValueError)
                and "empty" in normalized_reason
                and ("draft" in normalized_reason or "network" in normalized_reason)
            )
            if not is_empty_draft_rejection:
                logging.getLogger(__name__).error(
                    "Unexpected DraftNetwork commit execution failure",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                message = str(exc)
            else:
                message = reason or "Draft network is empty. Add equipment or connections before committing."
            if self._validation_label is not None:
                self._validation_label.setText(message)
                self._validation_label.setProperty("role", "validationError")
        except (RuntimeError, TypeError, ValueError) as exc:
            self._abort_async_commit()
            if self._validation_label is not None:
                self._validation_label.setText(str(exc))
                self._validation_label.setProperty("role", "validationError")
        except BaseException as exc:
            self._abort_async_commit()
            logging.getLogger(__name__).exception("DraftNetwork background commit failed")
            if self._validation_label is not None:
                self._validation_label.setText(f"Network commit failed: {exc}")
                self._validation_label.setProperty("role", "validationError")
        else:
            controller = self._creation_controller
            complete = getattr(controller, "complete_network_commit", None) if controller is not None else None
            if callable(complete):
                try:
                    complete(result)
                except BaseException as exc:
                    self._abort_async_commit()
                    if self._validation_label is not None:
                        self._validation_label.setText(f"Network committed, but UI refresh failed: {exc}")
                        self._validation_label.setProperty("role", "validationError")
                    return
            if self._validation_label is not None:
                self._validation_label.setText(
                    getattr(result, "message", "Network commit completed.")
                )
                self._validation_label.setProperty("role", "validationSuccess")
        finally:
            if self._commit_network_button is not None:
                self._commit_network_button.setText("Commit Network")
            self._refresh_commit_network_action()

    def _abort_async_commit(self) -> None:
        controller = self._creation_controller
        abort = getattr(controller, "abort_network_commit", None) if controller is not None else None
        if callable(abort):
            try:
                abort()
            except Exception:
                logging.getLogger(__name__).exception("Failed to release asynchronous commit state")

    def _render_creation_draft(self, draft: CreationDraft) -> None:
        self._transformer_configuration_editor.set_target(None)
        self._clear_parameter_controls()
        assert self._form_layout is not None
        for definition in draft.parameter_schema:
            control = self._create_creation_control(definition, draft.values.get(definition.parameter_id))
            self._parameter_controls[definition.parameter_id] = control
            self._form_layout.addRow(
                QLabel(
                    (f"{definition.display_name} *" if definition.required_before_create else definition.display_name) + (f" ({definition.unit})" if definition.unit else ""),
                    self,
                ),
                control,
            )
        errors = draft.validation_state.get("configuration", ())
        if self._validation_label is not None:
            self._validation_label.setText(
                f"Creating {draft.equipment_type}: " +
                ("Configuration complete." if draft.configuration_complete else "; ".join(errors))
            )
            self._validation_label.setProperty(
                "role",
                "validationSuccess" if draft.configuration_complete else "validationError",
            )
            try:
                self._validation_label.style().unpolish(self._validation_label)
                self._validation_label.style().polish(self._validation_label)
            except Exception:
                pass
        if self._apply_button is not None:
            self._apply_button.setText("Place Draft Equipment")
            self._apply_button.setProperty("role", "draftPlacementAction")
            # DraftNetwork accepts valid position-first authoring state.
            # Engineering completeness is validated at the explicit Commit
            # Network boundary, not as a prerequisite for draft placement.
            self._apply_button.setEnabled(draft.placement_position is not None)
            if self._apply_changes_connected:
                try:
                    self._apply_button.clicked.disconnect(self._apply_changes)
                except (RuntimeError, TypeError):
                    pass
                self._apply_changes_connected = False
            if not self._place_draft_connected:
                self._apply_button.clicked.connect(self._place_draft)
                self._place_draft_connected = True

    def _place_draft(self) -> None:
        if not self._creation_mode or self._creation_context is None:
            return
        controller = self._creation_controller
        commit = getattr(controller, "place_creation_draft", None) if controller is not None else None
        if not callable(commit):
            if self._validation_label is not None:
                self._validation_label.setText("Draft placement runtime is not bound.")
            return
        try:
            committed = bool(commit())
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(str(exc))
            return
        if committed:
            self._creation_mode = False
            if self._apply_button is not None:
                if self._place_draft_connected:
                    try:
                        self._apply_button.clicked.disconnect(self._place_draft)
                    except (RuntimeError, TypeError):
                        pass
                    self._place_draft_connected = False
                if not self._apply_changes_connected:
                    self._apply_button.clicked.connect(self._apply_changes)
                    self._apply_changes_connected = True
                self._apply_button.setEnabled(False)
                self._apply_button.setText("Apply Draft Changes")
            target = self.logical_panel.target
            if target is not None:
                self._render_projection(target)

    def _create_creation_control(self, definition: Any, value: Any) -> QWidget:
        datatype = str(definition.datatype).strip().lower()
        if definition.derived or not definition.editable:
            return QLabel(self._display_value(value), self)
        if datatype in {"float", "number"}:
            control = QDoubleSpinBox(self)
            control.setDecimals(6)
            control.setRange(
                float(definition.minimum if definition.minimum is not None else -1.0e15),
                float(definition.maximum if definition.maximum is not None else 1.0e15),
            )
            if value is not None:
                control.setValue(float(value))
            control.valueChanged.connect(lambda new_value, pid=definition.parameter_id: self._update_creation_value(pid, float(new_value)))
            return control
        if datatype in {"bool", "boolean"}:
            control = QCheckBox(self)
            control.setChecked(bool(value))
            control.toggled.connect(lambda new_value, pid=definition.parameter_id: self._update_creation_value(pid, bool(new_value)))
            return control
        if datatype == "enum":
            control = QComboBox(self)
            choices = tuple(str(choice) for choice in definition.choices)
            control.addItems(choices)
            if value is not None and str(value) in choices:
                control.setCurrentText(str(value))
            control.currentTextChanged.connect(lambda new_value, pid=definition.parameter_id: self._update_creation_value(pid, str(new_value)))
            return control
        control = QLineEdit(self)
        control.setText("" if value is None else str(value))
        control.textChanged.connect(lambda new_value, pid=definition.parameter_id: self._update_creation_value(pid, new_value))
        return control

    def _update_creation_value(self, parameter_id: str, value: Any) -> None:
        if not self._creation_mode or self._creation_context is None:
            return
        try:
            draft = self._creation_context.update(parameter_id, value)
        except (TypeError, ValueError, KeyError):
            return
        errors = draft.validation_state.get("configuration", ())
        if self._validation_label is not None:
            self._validation_label.setText(
                f"Creating {draft.equipment_type}: " +
                ("Configuration complete." if draft.configuration_complete else "; ".join(errors))
            )

    def _on_tool_changed(self, current_tool_id: Any, previous_tool_id: Any) -> None:
        del current_tool_id, previous_tool_id
        if self._creation_context is None:
            return
        draft = self._creation_context.draft
        if draft is None:
            self._creation_mode = False
            if self.logical_panel.target is None:
                self._render_projection(None)
        else:
            self.set_creation_draft(draft)

    def _clear_parameter_controls(self) -> None:
        self._parameter_controls.clear()
        self._parameter_states.clear()
        if self._form_layout is None:
            return
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _create_parameter_control(self, parameter: EngineeringParameterState) -> QWidget:
        value = parameter.value
        tooltip = self._parameter_tooltip(parameter)
        if not parameter.inspectable:
            control = QLabel("Not available in the Application read model", self)
            control.setToolTip("This canonical creation parameter is not exposed by the committed equipment read model.")
            return control
        if parameter.derived or not parameter.editable:
            control = QLabel(self._display_value(value), self)
            control.setToolTip(tooltip)
            return control
        datatype = str(parameter.datatype).strip().lower()
        if datatype in {"float", "number", "int", "integer"}:
            # An unset numeric value remains visibly unset; the validator
            # constrains edits without coercing an empty value to zero.
            control = QLineEdit(self)
            control.setText("" if value is None else str(value))
            if datatype in {"int", "integer"}:
                validator = QIntValidator(
                    int(parameter.minimum if parameter.minimum is not None else -2147483647),
                    int(parameter.maximum if parameter.maximum is not None else 2147483647),
                    control,
                )
            else:
                validator = QDoubleValidator(control)
                validator.setBottom(float(parameter.minimum if parameter.minimum is not None else -1.0e15))
                validator.setTop(float(parameter.maximum if parameter.maximum is not None else 1.0e15))
            control.setValidator(validator)
            control.setToolTip(tooltip)
            return control
        if datatype in {"bool", "boolean"}:
            control = QCheckBox(self)
            control.setChecked(bool(value))
            control.setToolTip(tooltip)
            return control
        if datatype == "enum":
            control = QComboBox(self)
            choices = tuple(str(choice) for choice in parameter.choices)
            control.addItems(choices)
            if value is not None and str(value) in choices:
                control.setCurrentText(str(value))
            else:
                control.setCurrentIndex(-1)
            control.setToolTip(tooltip)
            return control
        control = QLineEdit(self)
        control.setText("" if value is None else str(value))
        control.setToolTip(tooltip)
        return control

    def _parameter_label(self, parameter: EngineeringParameterState) -> QLabel:
        suffix: list[str] = []
        if parameter.unit:
            suffix.append(str(parameter.unit))
        if parameter.required_before_create:
            suffix.append("required")
        if parameter.derived:
            suffix.append("derived / read-only")
        elif not parameter.editable:
            suffix.append("read-only")
        text = str(parameter.display_name or parameter.parameter_id)
        if suffix:
            text += " (" + ", ".join(suffix) + ")"
        return QLabel(text, self)

    @classmethod
    def _parameter_tooltip(cls, parameter: EngineeringParameterState) -> str:
        details = [cls._validation_text(parameter)]
        if parameter.minimum is not None or parameter.maximum is not None:
            lower = "-∞" if parameter.minimum is None else str(parameter.minimum)
            upper = "∞" if parameter.maximum is None else str(parameter.maximum)
            details.append(f"Allowed range: {lower} to {upper}.")
        if parameter.default_value is not None:
            details.append(f"Catalogue default: {cls._display_value(parameter.default_value)}.")
        if parameter.required_before_create:
            details.append("Required by the canonical creation contract.")
        return " ".join(details)

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, bool):
            return "True" if value else "False"
        return "" if value is None else str(value)

    @staticmethod
    def _validation_text(parameter: EngineeringParameterState) -> str:
        if parameter.validation.get("authoritative") == "Core":
            return "Core validation is authoritative."
        return "Presentation metadata only; Core remains authoritative."

    @staticmethod
    def _current_value(parameter: EngineeringParameterState, control: QWidget) -> Any:
        datatype = str(parameter.datatype).strip().lower()
        if datatype in {"float", "number"}:
            if isinstance(control, QLineEdit):
                text = control.text().strip()
                return None if not text else float(text)
            return float(control.value())  # type: ignore[attr-defined]
        if datatype in {"int", "integer"}:
            text = control.text().strip()  # type: ignore[attr-defined]
            return None if not text else int(text)
        if datatype in {"bool", "boolean"}:
            return bool(control.isChecked())  # type: ignore[attr-defined]
        if datatype == "enum":
            return None if control.currentIndex() < 0 else str(control.currentText())  # type: ignore[attr-defined]
        return str(control.text())  # type: ignore[attr-defined]

    def _apply_changes(self) -> None:
        if self._engineering_editor is None:
            raise RuntimeError("Properties configuration runtime is not bound.")
        target = self.logical_panel.target
        if target is None:
            raise RuntimeError("No projected element is selected.")
        changes: dict[str, Any] = {}
        try:
            for parameter_id, parameter in self._parameter_states.items():
                if parameter.derived or not parameter.editable or not parameter.inspectable:
                    continue
                value = self._current_value(parameter, self._parameter_controls[parameter_id])
                if value != parameter.value:
                    changes[parameter_id] = value
        except (TypeError, ValueError, OverflowError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(f"Invalid engineering value: {exc}")
            return
        if not changes:
            if self._validation_label is not None:
                self._validation_label.setText("No engineering changes to commit.")
            return
        try:
            intent = self._engineering_editor.intent_from_projection(target, changes)
            result = self._engineering_editor.submit(intent)
        except (RuntimeError, TypeError, ValueError) as exc:
            if self._validation_label is not None:
                self._validation_label.setText(f"Engineering update rejected: {exc}")
            # Rebuild from the panel's current projection, not the target
            # captured before submission: selection may have changed while the
            # Application command was being handled.
            self._render_projection(self.logical_panel.target)
            return

        if not getattr(result, "success", False):
            message = getattr(result, "message", None) or "Engineering update was rejected."
            if self._validation_label is not None:
                self._validation_label.setText(f"Engineering update rejected: {message}")
            # The command failed, so DraftChanged is not authoritative evidence
            # of a mutation. Restore the currently selected projection; never
            # resurrect a previous target after a selection transition.
            self._render_projection(self.logical_panel.target)
            return

        if self._apply_button is not None:
            self._apply_button.setEnabled(False)
        if self._validation_label is not None:
            # Application events are published synchronously by execute(); the
            # projection coordinator may already have refreshed this panel.
            # Do not overwrite that completed refresh with a misleading
            # "waiting" status after control returns from the command path.
            self._validation_label.setText("Engineering update accepted.")


PROJECT_PANEL = PanelSpec(panel_id="project", title="Project Explorer", metadata={"minimum_width": 230})
EQUIPMENT_PANEL = PanelSpec(panel_id="equipment", title="Equipment Browser", metadata={"minimum_width": 230})
PROPERTIES_PANEL = PanelSpec(panel_id="properties", title="Properties", metadata={"minimum_width": 300})
ELEMENT_LIST_PANEL = PanelSpec(panel_id="element_list", title="Element List", metadata={"minimum_height": 180})
MESSAGES_PANEL = PanelSpec(panel_id="messages", title="Messages / Events", metadata={"minimum_height": 150})
STUDY_CASES_PANEL = PanelSpec(panel_id="study_cases", title="Study Cases", metadata={"minimum_height": 150})

DEFAULT_PANEL_SPECS: tuple[PanelSpec, ...] = (
    PROJECT_PANEL,
    EQUIPMENT_PANEL,
    PROPERTIES_PANEL,
    ELEMENT_LIST_PANEL,
    MESSAGES_PANEL,
    STUDY_CASES_PANEL,
)

_PANEL_WIDGET_FACTORIES: dict[str, Callable[[QWidget | None], QWidget]] = {
    "project": ProjectPanelWidget,
    "equipment": EquipmentPanelWidget,
    "properties": PropertiesPanelWidget,
    "element_list": ElementListPanelWidget,
    "messages": MessagesPanelWidget,
    "study_cases": StudyCasesPanelWidget,
}


def create_panel_widget(panel_id: str, parent: QWidget | None = None) -> QWidget:
    factory = _PANEL_WIDGET_FACTORIES.get(panel_id)
    if factory is None:
        raise KeyError(f"Unknown default panel ID: {panel_id!r}")
    return factory(parent)


def panel_spec_with_widget(spec: PanelSpec) -> PanelSpec:
    return PanelSpec(
        panel_id=spec.panel_id,
        title=spec.title,
        widget=create_panel_widget(spec.panel_id),
        closable=spec.closable,
        movable=spec.movable,
        floatable=spec.floatable,
        metadata=dict(spec.metadata),
    )


def default_panel_specs() -> tuple[PanelSpec, ...]:
    return DEFAULT_PANEL_SPECS


def default_panel_ids() -> tuple[str, ...]:
    return tuple(spec.panel_id for spec in DEFAULT_PANEL_SPECS)


def compose_default_panel_specs() -> tuple[PanelSpec, ...]:
    return tuple(panel_spec_with_widget(spec) for spec in DEFAULT_PANEL_SPECS)


def validate_default_panel_ids() -> tuple[str, ...]:
    ids = default_panel_ids()
    if len(ids) != 6 or len(set(ids)) != 6:
        raise RuntimeError("Default panel IDs must be unique and complete.")
    return ids


__all__ = [
    "ProjectPanelWidget",
    "EquipmentPanelWidget",
    "PropertiesPanelWidget",
    "TransformerConfigurationEditor",
    "ElementListPanelWidget",
    "MessagesPanelWidget",
    "StudyCasesPanelWidget",
    "PROJECT_PANEL",
    "EQUIPMENT_PANEL",
    "PROPERTIES_PANEL",
    "ELEMENT_LIST_PANEL",
    "MESSAGES_PANEL",
    "STUDY_CASES_PANEL",
    "DEFAULT_PANEL_SPECS",
    "create_panel_widget",
    "panel_spec_with_widget",
    "default_panel_specs",
    "default_panel_ids",
    "compose_default_panel_specs",
    "validate_default_panel_ids",
]
