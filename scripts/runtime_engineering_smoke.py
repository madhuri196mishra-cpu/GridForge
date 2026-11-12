# ============================================================
# GridForge V2 — Runtime Engineering Workflow Smoke Verification
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from types import SimpleNamespace

import main
from core.application.commands.draft_commands import CommitNetworkCommand
from ui.core.qt import QListWidget, QPointF, QWidget


TARGET_TOOL_IDS = (
    "bus",
    "breaker",
    "transformer",
    "generator",
    "load",
    "current_transformer",
    "relay",
)


def _event(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(scene_position=QPointF(x, y))


def main_smoke() -> None:
    app, window, plugin_manager, workspace_controller, ui_update_boundary, ui_lifecycle = main.build_application()
    try:
        controller = window.controller
        if controller is None:
            raise SystemExit("Runtime smoke: MainWindow controller missing.")
        tool_manager = controller._tool_manager
        if tool_manager is None:
            raise SystemExit("Runtime smoke: ToolManager is not bound.")

        panels = plugin_manager.get("panels")
        if panels is None:
            raise SystemExit("Runtime smoke: Panels plugin missing.")
        # Workspace owns panel placement; legacy dock wrappers are not the
        # authoritative widget lookup once the panel is embedded in an Area.
        equipment_panel = window.findChild(QWidget, "GridForgePanel_equipment")
        if equipment_panel is None:
            equipment_panel = getattr(panels, "_panels", {}).get("equipment")
        if equipment_panel is None:
            raise SystemExit("Runtime smoke: equipment panel widget missing.")
        palette = equipment_panel.findChild(QListWidget, "EquipmentPalette")
        if palette is None:
            raise SystemExit("Runtime smoke: equipment palette widget missing.")

        if palette.count() < len(TARGET_TOOL_IDS):
            raise SystemExit(
                f"Runtime smoke: equipment palette unexpectedly small ({palette.count()})."
            )

        for tool_id in TARGET_TOOL_IDS:
            definition = tool_manager.equipment_registry.require(tool_id)
            if definition is None:
                raise SystemExit(f"Runtime smoke: no equipment definition for {tool_id!r}.")
            tool_manager.activate(tool_id, cancel_active_creation=True)
            tool_manager.mouse_move(_event(120.0, 120.0))
            app.processEvents()
            if not tool_manager.preview_layer.scene.items():
                raise SystemExit(f"Runtime smoke: no preview graphics for {tool_id!r}.")
            tool_manager.cancel()
            app.processEvents()

        tool_manager.activate("bus", cancel_active_creation=True)
        tool_manager.creation_context.update_many({"nominal_voltage_kv": 132.0, "frequency_hz": 50.0})
        tool_manager.mouse_move(_event(200.0, 160.0))
        if not tool_manager.mouse_release(_event(200.0, 160.0)):
            raise SystemExit("Runtime smoke: Bus placement did not commit.")
        app.processEvents()

        application = controller.application
        draft = application.draft_network
        draft_buses = tuple(
            item for item in draft.equipment
            if str(getattr(item, "equipment_type", "")).lower() == "bus"
        )
        if not draft_buses:
            raise SystemExit("Runtime smoke: placed Bus is absent from DraftNetwork.")

        commit_result = application.execute(
            CommitNetworkCommand(
                project_id=draft.project_id,
                activation_generation=draft.activation_generation,
                draft_network=draft.to_dict(),
                document_id=application.presentation.document_id,
            )
        )
        if not commit_result.success:
            raise SystemExit(f"Runtime smoke: DraftNetwork commit failed: {commit_result.message}")

        network = application.read_network()
        buses = tuple(
            element for element in network.elements
            if str(getattr(element, "element_type", "")).lower() in {"bus", "buses"}
        )
        if not buses:
            raise SystemExit("Runtime smoke: committed Bus is absent from Application read model.")

        if not tool_manager.preview_layer.scene.items():
            raise SystemExit("Runtime smoke: committed Bus did not produce a permanent SLD graphics item.")

        # WorkspaceController is the canonical Area → Editor → Region
        # activation boundary. Do not require a legacy QTabWidget or invent
        # workspace IDs for editors that are regions within another workspace.
        for workspace_id in ("sld", "control", "protection", "study"):
            workspace_controller.activate(workspace_id)
            app.processEvents()
            if workspace_controller.active_workspace_id != workspace_id:
                raise SystemExit(
                    f"Runtime smoke: workspace {workspace_id!r} did not become active."
                )
            if workspace_controller.realized_workspace_id != workspace_id:
                raise SystemExit(
                    f"Runtime smoke: workspace {workspace_id!r} was not realized."
                )

        selected_ids = tuple(tool_manager.selection_manager.get_selected_ids())
        committed_bus_id = str(getattr(buses[-1], "object_id", ""))
        if not committed_bus_id or committed_bus_id not in selected_ids:
            raise SystemExit("Runtime smoke: committed Bus was not selected through SelectionManager.")

        print("RUNTIME VERIFIED: palette icons, tool activation, live previews, Bus placement, read-model visibility, and selection.")
    finally:
        main._shutdown_components(
            ui_lifecycle=ui_lifecycle,
            workspace_controller=workspace_controller,
            ui_update_boundary=ui_update_boundary,
            plugin_manager=plugin_manager,
        )


if __name__ == "__main__":
    main_smoke()
