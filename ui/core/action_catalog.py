# ============================================================
# GridForge V2 — Canonical Presentation Action Catalog
# Author: Subhendu Mishra
# ============================================================
"""Single source of presentation action metadata."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from ui.core.action_definition import ActionDefinition
from ui.tools.tool_definition import ToolDefinition


_COMMON: tuple[ActionDefinition, ...] = (
    ActionDefinition("select", "Select", "Select and inspect engineering objects.", "select", None, "selection", True, True, True, "editor", "select"),
    ActionDefinition("move", "Move", "Move the current selection.", "move", None, "selection", True, False, False, "editor", "move"),
    ActionDefinition("pan", "Pan", "Pan the active engineering view.", "pan", None, "navigation"),
    ActionDefinition("zoom_in", "Zoom In", "Zoom into the active view.", "zoom_in", "Ctrl+=", "navigation"),
    ActionDefinition("zoom_out", "Zoom Out", "Zoom out of the active view.", "zoom_out", "Ctrl+-", "navigation"),
    ActionDefinition("fit_view", "Fit", "Fit the active engineering view.", "fit_view", "F", "navigation"),
    ActionDefinition("copy", "Copy", "Copy the current semantic selection.", "copy", "Ctrl+C", "edit"),
    ActionDefinition("paste", "Paste", "Paste the current semantic clipboard.", "paste", "Ctrl+V", "edit"),
    ActionDefinition("delete", "Delete", "Delete the current selection.", "delete", "Delete", "edit"),
    ActionDefinition("undo", "Undo", "Undo the last Application command.", "undo", "Ctrl+Z", "history"),
    ActionDefinition("redo", "Redo", "Redo the last Application command.", "redo", "Ctrl+Y", "history"),
    ActionDefinition("sld_select", "SLD Select", "Select SLD objects.", "select", None, "sld", True, True, True, "sld", "select"),
    ActionDefinition("sld_move", "SLD Move", "Move selected SLD objects.", "move", None, "sld", True, False, False, "sld", "move"),
    ActionDefinition("sld_wire", "SLD Wire", "Create a terminal-to-terminal SLD connection.", "wire", None, "sld", True, True, False, "sld", "wire"),
)


_SURFACE_ACTIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition("project.new", "New Project", "Create a new project.", shortcut="Ctrl+N", category="file"),
    ActionDefinition("project.open", "Open Project…", "Open an existing project.", shortcut="Ctrl+O", category="file"),
    ActionDefinition("project.save", "Save Project", "Save the active project.", shortcut="Ctrl+S", category="file"),
    ActionDefinition("project.save_as", "Save Project As…", "Save the active project to a new path.", shortcut="Ctrl+Shift+S", category="file"),
    ActionDefinition("project.close", "Close Project", "Close the active project.", shortcut="Ctrl+W", category="file"),
    ActionDefinition("application.exit", "Exit", "Exit GridForge.", shortcut="Alt+F4", category="file"),
    ActionDefinition("edit.select_all", "Select All", "Select all visible objects.", shortcut="Ctrl+A", category="edit"),
    ActionDefinition("edit.cut", "Cut", "Copy and remove the selection.", shortcut="Ctrl+X", category="edit"),
    ActionDefinition("edit.box_select", "Box Select", "Select objects inside a rectangle.", category="edit"),
    ActionDefinition("edit.move", "Move", "Move selected objects.", category="edit"),
    ActionDefinition("edit.drag_move", "Drag Move", "Drag selected objects through the Select tool.", category="edit"),
    ActionDefinition("edit.rotate", "Rotate 90° Clockwise", "Rotate the active SLD selection 90 degrees clockwise.", shortcut="R", category="edit"),
    ActionDefinition("edit.rotate_ccw", "Rotate 90° Counter-clockwise", "Rotate the active SLD selection 90 degrees counter-clockwise.", shortcut="Shift+R", category="edit"),
    ActionDefinition("edit.mirror_horizontal", "Mirror H", "Mirror selected symbols horizontally.", category="edit"),
    ActionDefinition("edit.mirror_vertical", "Mirror V", "Mirror selected symbols vertically.", category="edit"),
    ActionDefinition("network.commit_draft", "Commit Network", "Commit the active DraftNetwork.", shortcut="Ctrl+Shift+Enter", category="project"),
    ActionDefinition("view.sld_workspace", "SLD", "Activate the SLD workspace.", category="workspace"),
    ActionDefinition("view.control_workspace", "Control", "Activate the Control workspace.", category="workspace"),
    ActionDefinition("view.protection_workspace", "Protection", "Activate the Protection workspace.", category="workspace"),
    ActionDefinition("view.study_workspace", "Study", "Activate the Study workspace.", category="workspace"),
    ActionDefinition("view.topology", "Topology", "Show the topology projection.", category="workspace"),
    ActionDefinition("view.map", "Map", "Show persisted SLD geometry.", category="workspace"),
    ActionDefinition("view.reports", "Reports", "Show published study results.", category="workspace"),
    ActionDefinition("view.maximize_editor", "Maximize Editor", "Maximize or restore the active workspace editor.", shortcut="Ctrl+Space", category="view"),
    ActionDefinition("view.toggle_explorer", "Explorer", "Show or hide the explorer when available in the active workspace.", category="view"),
    ActionDefinition("view.toggle_inspector", "Inspector", "Show or hide the inspector in the active workspace.", category="view"),
    ActionDefinition("view.toggle_messages", "Messages", "Show or hide messages and diagnostics in the active workspace.", category="view"),
    ActionDefinition("view.toggle_tool_settings", "Tool Settings", "Show or hide contextual tool settings in the active workspace.", category="view"),
    ActionDefinition("view.equipment_browser", "Equipment Browser", "Open the equipment browser.", category="workspace"),
    ActionDefinition("control.panel", "Control Workspace", "Activate the Control workspace.", category="workspace"),
    ActionDefinition("protection.panel", "Protection Workspace", "Activate the Protection workspace.", category="workspace"),
    ActionDefinition("study.cases", "Study Cases", "Open Study Cases.", category="study"),
    ActionDefinition("help.about", "About GridForge", "About GridForge V2.", category="help"),
)


def surface_action_definitions() -> tuple[ActionDefinition, ...]:
    """Return canonical metadata for non-tool application surface actions."""
    return _SURFACE_ACTIONS


def build_action_definitions(tool_definitions: Iterable[ToolDefinition] = ()) -> tuple[ActionDefinition, ...]:
    """Build immutable action metadata, scoped to each declared editor.

    A tool ID is only unique within its editor. Definitions that declare an
    editor therefore receive one canonical "tool.<editor>.<tool_id>" action
    per editor. Editor-agnostic definitions retain the legacy "tool.<id>"
    form for compatibility.

    Repeated definitions for the same action ID are accepted only when their
    resulting metadata is identical. Conflicting definitions are rejected
    instead of silently allowing whichever registry happened to be iterated
    first to determine the visible action.
    """
    result: list[ActionDefinition] = list(_COMMON)
    by_action_id = {item.action_id: item for item in result}
    for definition in tool_definitions:
        if not isinstance(definition, ToolDefinition):
            raise TypeError("tool_definitions must contain ToolDefinition objects.")
        editor_scopes: tuple[str | None, ...] = tuple(definition.editor_types) or (None,)
        for editor_type in editor_scopes:
            action_id = (
                f"tool.{editor_type}.{definition.tool_id}"
                if editor_type is not None
                else f"tool.{definition.tool_id}"
            )
            candidate = ActionDefinition(
                action_id=action_id,
                title=definition.display_name,
                description=definition.description or definition.display_name,
                icon_id=definition.icon_id,
                shortcut=definition.shortcuts[0] if definition.shortcuts else None,
                category=definition.category or "engineering",
                checkable=True,
                scope=editor_type or "editor",
                tool_id=definition.tool_id,
                metadata={
                    "tool_definition": definition.tool_id,
                    "editor_type": editor_type,
                    "editor_types": (editor_type,) if editor_type is not None else (),
                    "capabilities": tuple(definition.capabilities),
                },
            )
            existing = by_action_id.get(action_id)
            if existing is not None:
                if existing != candidate:
                    raise ValueError(
                        f"Conflicting action definitions for {action_id!r}; "
                        "tool metadata must be unique within each editor scope."
                    )
                continue
            result.append(candidate)
            by_action_id[action_id] = candidate
    return tuple(result)


def index_action_definitions(definitions: Iterable[ActionDefinition]) -> Mapping[str, ActionDefinition]:
    values = tuple(definitions)
    result: dict[str, ActionDefinition] = {}
    for definition in values:
        if not isinstance(definition, ActionDefinition):
            raise TypeError("definitions must contain ActionDefinition objects.")
        if definition.action_id in result:
            raise ValueError(f"Duplicate action definition: {definition.action_id!r}")
        result[definition.action_id] = definition
    return MappingProxyType(result)


__all__ = ["build_action_definitions", "index_action_definitions", "surface_action_definitions"]
