# ============================================================
# File: ui/plugins/menu_plugin.py
# GridForge V2 — Application Menu Composition
# Author: Subhendu Mishra
# ============================================================
"""Canonical, hierarchical application menu composition boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TypeAlias

from ui.core.qt import QAction, QMainWindow, QMenu, QMenuBar, QObject
from ui.core.action_definition import ActionDefinition
from ui.core.action_router import UIActionRouter
from ui.plugins.plugin_context import PluginContext


@dataclass(frozen=True, slots=True)
class MenuActionSpec:
    """One leaf menu entry routed through the canonical action router."""

    action_id: str
    text: str
    shortcut: str | None = None
    separator_before: bool = False


@dataclass(frozen=True, slots=True)
class MenuSubmenuSpec:
    """A named submenu containing further menu entries."""

    menu_id: str
    title: str
    items: tuple["MenuItemSpec", ...]
    separator_before: bool = False


MenuItemSpec: TypeAlias = MenuActionSpec | MenuSubmenuSpec


@dataclass(frozen=True, slots=True)
class MenuSpec:
    menu_id: str
    title: str
    actions: tuple[MenuItemSpec, ...]


class MenuPlugin(QObject):
    """Compose hierarchical menus; route every leaf through UIActionRouter."""

    plugin_id = "menu"
    plugin_name = "Menu"
    plugin_version = "1.1"
    plugin_description = "Canonical hierarchical GridForge application menu composition."
    plugin_dependencies: tuple[str, ...] = ()
    plugin_optional = False

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: PluginContext | None = None
        self._menu_bar: QMenuBar | None = None
        self._menus: dict[str, QMenu] = {}
        self._actions: dict[str, QAction] = {}
        self._initialized = False

    @property
    def menu_bar(self) -> QMenuBar | None:
        return self._menu_bar

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self, context: PluginContext) -> QMenuBar:
        if not isinstance(context, PluginContext):
            raise TypeError("MenuPlugin requires PluginContext.")
        if self._initialized:
            if self._context is not context:
                raise RuntimeError("MenuPlugin is already initialized with a different PluginContext.")
            return self._require_menu_bar()

        main_window = context.main_window
        router = context.action_router
        if not isinstance(main_window, QMainWindow):
            raise TypeError("PluginContext.main_window must be QMainWindow for MenuPlugin.")
        if not isinstance(router, UIActionRouter):
            raise TypeError("MenuPlugin requires PluginContext.action_router.")

        menu_bar = QMenuBar(main_window)
        menu_bar.setObjectName("GridForgeMenuBar")
        main_window.setMenuBar(menu_bar)
        self._context = context
        self._menu_bar = menu_bar
        try:
            for spec in default_menus():
                menu = menu_bar.addMenu(spec.title)
                menu.setObjectName(f"menu.{spec.menu_id}")
                self._menus[spec.menu_id] = menu
                if spec.menu_id == "tools":
                    self._populate_tool_menu(menu, router)
                else:
                    self._populate_menu(menu, spec.actions, router)
            self._initialized = True
            return menu_bar
        except Exception:
            self.shutdown()
            raise

    def _populate_menu(
        self,
        menu: QMenu,
        items: tuple[MenuItemSpec, ...],
        router: UIActionRouter,
    ) -> None:
        for item in items:
            if item.separator_before:
                menu.addSeparator()
            if isinstance(item, MenuSubmenuSpec):
                if not item.menu_id.strip() or not item.title.strip():
                    raise ValueError("Submenu IDs and titles must be non-empty.")
                if item.menu_id in self._menus:
                    raise ValueError(f"Duplicate menu ID: {item.menu_id!r}")
                submenu = menu.addMenu(item.title)
                submenu.setObjectName(f"menu.{item.menu_id}")
                self._menus[item.menu_id] = submenu
                self._populate_menu(submenu, item.items, router)
                continue

            if not isinstance(item, MenuActionSpec):
                raise TypeError("Menu entries must be MenuActionSpec or MenuSubmenuSpec.")
            if item.action_id in self._actions:
                raise ValueError(f"Duplicate menu action ID: {item.action_id!r}")
            router.require(item.action_id)
            definition = router.definition(item.action_id)
            action = QAction(item.text, self)
            action.setObjectName(item.action_id)
            action.setToolTip(definition.description)
            action.setStatusTip(definition.description)
            action.setEnabled(router.is_enabled(item.action_id))
            shortcut = item.shortcut or definition.shortcut
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(
                lambda _checked=False, action_id=item.action_id: self._dispatch_action(action_id)
            )
            menu.addAction(action)
            self._actions[item.action_id] = action

    def _populate_tool_menu(self, menu: QMenu, router: UIActionRouter) -> None:
        """Build discipline/category submenus from canonical routed tool actions."""
        grouped: dict[str, dict[str, list[ActionDefinition]]] = {}
        for definition in router.definitions.values():
            if not definition.action_id.startswith("tool."):
                continue
            if not router.has(definition.action_id):
                continue
            editor_type = str(definition.metadata.get("editor_type") or definition.scope or "general")
            category = str(definition.category or "General")
            grouped.setdefault(editor_type, {}).setdefault(category, []).append(definition)

        if not grouped:
            return

        editor_titles = {
            "sld": "SLD",
            "control": "Control",
            "protection": "Protection",
            "study": "Study",
            "editor": "General",
            "global": "General",
        }
        editor_order = ("sld", "control", "protection", "study", "editor", "global")
        editor_types = sorted(
            grouped,
            key=lambda value: editor_order.index(value) if value in editor_order else len(editor_order),
        )
        editor_items: list[MenuSubmenuSpec] = []
        for editor_type in editor_types:
            category_items: list[MenuSubmenuSpec] = []
            categories = grouped[editor_type]
            for category, definitions in categories.items():
                actions = tuple(
                    MenuActionSpec(
                        action_id=definition.action_id,
                        text=definition.title,
                        shortcut=definition.shortcut,
                    )
                    for definition in definitions
                )
                category_items.append(
                    MenuSubmenuSpec(
                        menu_id=f"tools.{self._menu_slug(editor_type)}.{self._menu_slug(category)}",
                        title=category,
                        items=actions,
                    )
                )
            editor_items.append(
                MenuSubmenuSpec(
                    menu_id=f"tools.{self._menu_slug(editor_type)}",
                    title=editor_titles.get(editor_type, editor_type.replace("_", " ").title()),
                    items=tuple(category_items),
                )
            )

        self._populate_menu(menu, tuple(editor_items), router)

    @staticmethod
    def _menu_slug(value: str) -> str:
        """Create a stable menu ID fragment from a human-readable category."""
        normalized = "".join(character.lower() if character.isalnum() else "_" for character in value)
        return "_".join(part for part in normalized.split("_") if part)

    def shutdown(self) -> None:
        if self._menu_bar is not None:
            self._menu_bar.clear()
            self._menu_bar.deleteLater()
        self._actions.clear()
        self._menus.clear()
        self._menu_bar = None
        self._context = None
        self._initialized = False

    def refresh_enabled_states(self) -> None:
        if self._context is None:
            return
        router = self._context.action_router
        for action_id, action in tuple(self._actions.items()):
            action.setEnabled(router.is_enabled(action_id))

    def _dispatch_action(self, action_id: str) -> None:
        """Recheck canonical availability and refresh presentation around dispatch."""
        if self._context is None:
            return
        router = self._context.action_router
        if not router.is_enabled(action_id):
            self.refresh_enabled_states()
            return
        try:
            router.dispatch(action_id)
        finally:
            self.refresh_enabled_states()

    def action(self, action_id: str) -> QAction | None:
        return self._actions.get(action_id)

    def menu(self, menu_id: str) -> QMenu | None:
        """Return a top-level menu or nested submenu by its stable ID."""
        return self._menus.get(menu_id)

    def _require_menu_bar(self) -> QMenuBar:
        if self._menu_bar is None:
            raise RuntimeError("MenuPlugin is initialized without a menu bar.")
        return self._menu_bar


def default_menus() -> tuple[MenuSpec, ...]:
    """Return a compact engineering menu taxonomy using existing action IDs."""
    def a(
        action_id: str,
        text: str,
        shortcut: str | None = None,
        separator_before: bool = False,
    ) -> MenuActionSpec:
        return MenuActionSpec(action_id, text, shortcut, separator_before)

    def sub(
        menu_id: str,
        title: str,
        *items: MenuItemSpec,
        separator_before: bool = False,
    ) -> MenuSubmenuSpec:
        return MenuSubmenuSpec(menu_id, title, tuple(items), separator_before)

    return (
        MenuSpec("file", "File", (
            a("project.new", "New Project", "Ctrl+N"),
            a("project.open", "Open Project…", "Ctrl+O"),
            a("project.save", "Save Project", "Ctrl+S"),
            a("project.save_as", "Save Project As…", "Ctrl+Shift+S"),
            a("project.close", "Close Project", "Ctrl+W", True),
            a("application.exit", "Exit", "Alt+F4", True),
        )),
        MenuSpec("edit", "Edit", (
            a("edit.undo", "Undo", "Ctrl+Z"),
            a("edit.redo", "Redo", "Ctrl+Y"),
            a("edit.delete_selection", "Delete Selection", "Delete", True),
            a("edit.select_all", "Select All", "Ctrl+A"),
            a("edit.copy", "Copy", "Ctrl+C"),
            a("edit.paste", "Paste", "Ctrl+V"),
            a("edit.cut", "Cut", "Ctrl+X"),
            a("edit.rotate", "Rotate 90° Clockwise", "R", True),
            a("edit.rotate_ccw", "Rotate 90° Counter-clockwise", "Shift+R"),
        )),
        MenuSpec("view", "View", (
            sub("view.panels", "Panels",
                a("view.toggle_explorer", "Project Explorer"),
                a("view.layers", "SLD Layers"),
                a("view.equipment_browser", "Equipment Browser"),
                a("view.toggle_inspector", "Inspector"),
                a("view.toggle_tool_settings", "Tool Settings"),
                a("view.toggle_messages", "Messages & Diagnostics"),
            ),
            sub("view.navigation", "Canvas Navigation",
                a("view.fit", "Fit SLD View"),
                a("view.zoom_in", "Zoom In"),
                a("view.zoom_out", "Zoom Out"),
                a("view.pan", "Pan"),
            ),
            a("view.maximize_editor", "Maximize Editor", "Ctrl+Space"),
        )),
        MenuSpec("workspaces", "Workspaces", (
            a("view.sld_workspace", "SLD Workspace"),
            a("view.control_workspace", "Control Workspace"),
            a("view.protection_workspace", "Protection Workspace"),
            a("view.study_workspace", "Study Workspace"),
            a("view.topology", "Network Topology", separator_before=True),
            a("view.map", "Geographic / Layout Map"),
            a("view.reports", "Study Reports"),
        )),
        MenuSpec("tools", "Tools", ()),
        MenuSpec("network", "Network", (
            a("network.commit_draft", "Commit Network Draft", "Ctrl+Shift+Enter"),
        )),
        MenuSpec("study", "Studies", (
            a("study.cases", "Study Cases"),
        )),
        MenuSpec("help", "Help", (
            a("help.about", "About GridForge"),
        )),
    )


def create_menu_plugin(parent: Optional[QObject] = None) -> MenuPlugin:
    return MenuPlugin(parent=parent)


__all__ = [
    "MenuActionSpec",
    "MenuItemSpec",
    "MenuPlugin",
    "MenuSpec",
    "MenuSubmenuSpec",
    "create_menu_plugin",
    "default_menus",
]
