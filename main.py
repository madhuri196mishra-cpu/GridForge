# ============================================================
# GridForge V2 — Application Composition Root
# ============================================================
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from uuid import uuid4

from core.application.bootstrap import create_application
from core.application.errors import ExecutionError
from core.application.events import ProjectLoaded
from core.application.services.sld_service import SLDService
from core.plugin_sdk import EngineeringPluginHost
from plugins.motor_dynamics.plugin import MotorDynamicsPlugin
from plugins.motor_dynamics.dashboard import MotorTelemetryDashboardWidget
from core.network.network import Network

from ui.canvas.canvas_composition import CanvasComposer
from ui.events.control_update_coordinator import ControlUpdateCoordinator
from ui.control.control_surface_host import ControlSurfaceHost
from ui.control.control_workspace import ControlWorkspace
from ui.protection.protection_workspace import ProtectionWorkspace
from ui.canvas.sld_canvas_projection import SLDCanvasProjection
from ui.canvas.sld_canvas_render_system import SLDCanvasRenderSystem
from ui.controllers.study_case_controller import StudyCaseController
from ui.core.controller import Controller
from ui.core.action_router import UIActionRouter
from ui.core.action_definition import ActionDefinition
from ui.core.action_catalog import build_action_definitions, surface_action_definitions
from ui.core.tool_manager import ToolManager
from ui.bootstrap.presentation_bootstrap import PresentationBootstrap
from ui.core.qt import QApplication, QFileDialog, QMessageBox, QWidget, QGraphicsView, QVBoxLayout
from ui.events.sld_update_coordinator import SLDUpdateCoordinator
from ui.events.update_boundary import UIUpdateBoundary
from ui.lifecycle import UILifecycle
from ui.lifecycle.project_close_controller import ProjectCloseController
from ui.main_window import MainWindow
from ui.panels.panel_presentation_bridge import PanelPresentationBridge
from ui.plugins.plugin_context import PluginContext
from ui.styling.style_manager import StyleManager
from ui.plugins.plugin_manager import PluginManager
from ui.projection.element_list_projection import ElementListProjection
from ui.projection.application_event_messages import ApplicationEventMessagesProjection
from ui.projection.project_hierarchy_projection import ProjectHierarchyProjection
from ui.projection.study_projection import StudyProjection
from ui.projection.ui_projection_coordinator import UIProjectionCoordinator
from ui.projection.validation_projection import ValidationProjection
from ui.sld.sld_controller import SLDController
from ui.sld.sld_document import SLDDocument
from ui.sld.sld_projection_manager import SLDProjectionManager
from ui.sld.sld_state import SLDState
from ui.sld.sld_read_synchronizer import SLDReadSynchronizer
from ui.workspace.project_workspace import ProjectWorkspaceLifecycle
from ui.workspace.project_workspace_adapter import ProjectWorkspaceApplicationAdapter, ProjectWorkspaceChanged
from ui.workspace.workspace_controller import WorkspaceController
from ui.workspace.workspace_defaults import CONTROL_WORKSPACE_ID, PROTECTION_WORKSPACE_ID, SLD_WORKSPACE_ID, STUDY_WORKSPACE_ID, default_workspaces
from ui.workspace.workspace_manager import WorkspaceManager
from ui.workspace.workspace_realizer import WorkspaceRealizer
from ui.workspace.engineering_context import EngineeringContextStore
from ui.tools.default_tool_registry import create_default_tool_factories
from ui.tools.tool_definition import contextual_tool_definitions
from ui.tools.tool_mode import ToolMode
from ui.tools.tool_settings import ToolSettings
from ui.editors.study.study_tool_runtime import StudyToolRuntime
from ui.editors.common.tool_icon_adapter import ToolIconAdapter
from ui.control.control_tool_palette import ControlToolRegistry
from ui.protection.protection_tools import ProtectionInteractionController
from core.application.commands.sld_commands import AddSLDNodeCommand, RemoveSLDNodeCommand, SetSLDNodePresentationCommand
from core.application.commands.model_commands import CREATE_BUS, CreateBusCommand, DeleteBusCommand, CreateTransformerCommand, DeleteTransformerCommand
from core.application.commands.breaker_commands import CreateBreakerCommand, DeleteBreakerCommand
from core.application.commands.simple_wire_commands import CREATE_SIMPLE_WIRE, RemoveSimpleWireConnectionCommand
from core.application.commands.draft_commands import RemoveDraftConnectionCommand, RemoveDraftEquipmentCommand
from ui.branding import BrandingService
from ui.splash_screen import StartupSplash
Cleanup = Callable[[], None]


def _invoke_cleanup(cleanup: Cleanup, errors: list[BaseException]) -> None:
    try: cleanup()
    except BaseException as exc: errors.append(exc)


def _cleanup_startup_failure(resources: dict[str, object]) -> None:
    errors: list[BaseException] = []
    sld_controller = resources.get("sld_controller")
    if isinstance(sld_controller, SLDController):
        _invoke_cleanup(sld_controller.dispose, errors)
    startup_splash = resources.get("startup_splash")
    if isinstance(startup_splash, StartupSplash):
        _invoke_cleanup(startup_splash.close, errors)
    ui_lifecycle = resources.get("ui_lifecycle"); workspace_controller = resources.get("workspace_controller")
    if isinstance(ui_lifecycle, UILifecycle):
        _invoke_cleanup(ui_lifecycle.close, errors)
        if not ui_lifecycle.closed and isinstance(workspace_controller, WorkspaceController): _invoke_cleanup(workspace_controller.close, errors)
    else:
        adapter = resources.get("project_workspace_adapter")
        if isinstance(adapter, ProjectWorkspaceApplicationAdapter): _invoke_cleanup(adapter.close_project, errors)
        if isinstance(workspace_controller, WorkspaceController): _invoke_cleanup(workspace_controller.close, errors)
    ui_update_boundary = resources.get("ui_update_boundary")
    if isinstance(ui_update_boundary, UIUpdateBoundary): _invoke_cleanup(ui_update_boundary.dispose, errors)
    motor_dynamics_dashboard = resources.get("motor_dynamics_dashboard")
    if isinstance(motor_dynamics_dashboard, MotorTelemetryDashboardWidget):
        _invoke_cleanup(motor_dynamics_dashboard.close, errors)
    plugin_manager = resources.get("plugin_manager")
    if isinstance(plugin_manager, PluginManager): _invoke_cleanup(plugin_manager.shutdown_all, errors)
    engineering_plugin_host = resources.get("engineering_plugin_host")
    if isinstance(engineering_plugin_host, EngineeringPluginHost):
        _invoke_cleanup(engineering_plugin_host.shutdown_all, errors)


def _shutdown_components(*, ui_lifecycle: UILifecycle, workspace_controller: WorkspaceController, ui_update_boundary: UIUpdateBoundary, plugin_manager: PluginManager, engineering_plugin_host: EngineeringPluginHost | None = None, motor_dynamics_dashboard: MotorTelemetryDashboardWidget | None = None) -> None:
    errors: list[BaseException] = []; _invoke_cleanup(ui_lifecycle.close, errors)
    if not ui_lifecycle.closed: _invoke_cleanup(workspace_controller.close, errors)
    _invoke_cleanup(ui_update_boundary.dispose, errors)
    # The UI subscriber is closed before its headless engineering publisher.
    if motor_dynamics_dashboard is not None:
        _invoke_cleanup(motor_dynamics_dashboard.close, errors)
    _invoke_cleanup(plugin_manager.shutdown_all, errors)
    if engineering_plugin_host is not None:
        _invoke_cleanup(engineering_plugin_host.shutdown_all, errors)
    if errors: raise errors[0]


def _build_application_impl(resources: dict[str, object]) -> tuple[QApplication, MainWindow, PluginManager, WorkspaceController, UIUpdateBoundary, UILifecycle]:
    app = QApplication.instance()
    if app is None: app = QApplication(sys.argv)
    branding = BrandingService()
    resources["branding"] = branding

    # Establish the application identity before constructing/showing the splash
    # or any application window. BrandingService is the single resource authority.
    branding.apply_application_identity(app)

    startup_splash = StartupSplash(app, branding)
    resources["startup_splash"] = startup_splash
    startup_splash.show(f"Starting {branding.PRODUCT_NAME} {branding.version}…")
    # Give Qt a real paint cycle and enforce a short minimum splash duration
    # so the canonical logo is actually visible before startup work proceeds.
    startup_splash.hold()
    style_manager = StyleManager()
    startup_splash.status("Initializing application style…")
    style_manager.apply(app)
    startup_splash.status("Initializing engineering application…")
    network = Network(); gridforge_application = create_application(network)
    # Engineering plugins have a lifecycle owner separate from UI composition.
    # This composition root explicitly opts Motor Dynamics in and passes only
    # its existing Application-owned registration adapter; no services are cloned.
    engineering_plugin_host = EngineeringPluginHost(gridforge_application)
    resources["engineering_plugin_host"] = engineering_plugin_host
    motor_dynamics_plugin = MotorDynamicsPlugin()
    engineering_plugin_host.activate(
        motor_dynamics_plugin,
        services={
            "motor_dynamics_composition": gridforge_application.motor_dynamics_composition,
        },
    )
    startup_splash.status("Initializing SLD presentation…")
    sld_projection_manager = SLDProjectionManager(); sld_read_synchronizer = SLDReadSynchronizer(sld_projection_manager, application=gridforge_application)

    def serialize_sld(document: SLDDocument) -> dict:
        if not isinstance(document, SLDDocument): raise TypeError("Persistent presentation must be an SLDDocument")
        return document.to_dict()

    def deserialize_sld(data: dict) -> SLDDocument:
        document = SLDDocument.from_dict(
            data,
            default_symbol_presentation_factory=presentation_bootstrap.default_symbol_presentation,
        )
        if not isinstance(document, SLDDocument): raise TypeError("Persistent presentation must deserialize to an SLDDocument")
        return document

    startup_splash.status("Preparing workspaces and engineering tools…")
    workspace_manager = WorkspaceManager(definitions={definition.workspace_id: definition for definition in default_workspaces()}, default_workspace_id=SLD_WORKSPACE_ID)
    presentation_bootstrap = PresentationBootstrap.create(workspace_manager=workspace_manager, application=gridforge_application, sld_read_synchronizer=sld_read_synchronizer)
    equipment_registry = presentation_bootstrap.equipment_registry
    controller = Controller(application=gridforge_application); canvas_composer = CanvasComposer(); canvas_preparation = canvas_composer.prepare(controller=controller)
    tool_registry = create_default_tool_factories(
        controller=controller,
        application=gridforge_application,
        selection_manager=canvas_preparation.selection_manager,
        snap_system=canvas_preparation.snap_system,
        preview_layer=canvas_preparation.preview_layer,
        symbol_registry=presentation_bootstrap.symbol_registry,
    )
    tool_manager = ToolManager(
        controller=controller,
        application=gridforge_application,
        selection_manager=canvas_preparation.selection_manager,
        snap_system=canvas_preparation.snap_system,
        tool_registry=tool_registry,
        preview_layer=canvas_preparation.preview_layer,
        equipment_registry=equipment_registry,
    )
    # Construct the one canonical SLD controller as soon as its authoritative
    # projection manager and shared interaction dependencies exist. The active
    # document is registered later, after project activation creates it.
    sld_state = SLDState(
        selection_manager=canvas_preparation.selection_manager,
        tool_manager=tool_manager,
    )
    sld_controller = SLDController(
        state=sld_state,
        projection_manager=sld_projection_manager,
        application=gridforge_application,
    )
    resources["sld_controller"] = sld_controller
    sld_canvas_projection = SLDCanvasProjection(
        read_model_provider=sld_projection_manager.read_model,
    )
    sld_canvas_render_system = SLDCanvasRenderSystem(
        scene=canvas_preparation.scene,
        item_factory=presentation_bootstrap.sld_graphics_item_factory,
        semantic_realization=presentation_bootstrap.semantic_realization,
        snap_system=canvas_preparation.snap_system,
    )
    canvas_composition = canvas_composer.compose(
        controller=controller,
        tool_manager=tool_manager,
        preparation=canvas_preparation,
        sld_controller=sld_controller,
        parent=None,
        sld_canvas_projection=sld_canvas_projection,
        sld_canvas_render_system=sld_canvas_render_system,
    )
    control_workspace = ControlWorkspace(application=gridforge_application, controller=controller, selection_manager=canvas_preparation.selection_manager, parent=None)
    protection_workspace = ProtectionWorkspace(application=gridforge_application, selection_manager=canvas_preparation.selection_manager, parent=None)
    sld_tool_definitions = contextual_tool_definitions(
        tool_manager.get_tool_ids(),
        editor_type="sld",
    )
    control_tool_definitions = ControlToolRegistry.create_default(
        application=gridforge_application,
    ).definitions()
    protection_tool_definitions = ProtectionInteractionController.TOOL_DEFINITIONS
    study_tool_runtime = StudyToolRuntime(application=gridforge_application)
    study_tool_definitions = study_tool_runtime.definitions
    contextual_tool_definitions_all = (
        *sld_tool_definitions,
        *control_tool_definitions,
        *protection_tool_definitions,
        *study_tool_definitions,
    )
    # Tool-shaft presentation is deliberately separate from the equipment
    # palette. ToolIconAdapter may reuse SymbolRegistry definitions, but
    # ToolDefinition remains the sole tool identity input.
    _tool_icon_adapter = ToolIconAdapter(presentation_bootstrap.symbol_registry)

    def _tool_icon(definition):
        return _tool_icon_adapter.icon_for(definition)

    workspace_surface_host = ControlSurfaceHost(
        surfaces={
            "sld": canvas_composition.widget,
            "control": control_workspace.editor_canvas,
            "protection": protection_workspace.editor_canvas,
        },
        application=gridforge_application,
        tool_definitions=contextual_tool_definitions_all,
        tool_activator=controller.set_tool,
        tool_activators={
            "sld": controller.set_tool,
            "control": control_workspace.activate_tool_id,
            "protection": protection_workspace.activate_tool_id,
            "study": study_tool_runtime.activate,
        },
        tool_active_providers={
            "sld": lambda: tool_manager.active_tool_id,
            "control": lambda: control_workspace.active_tool_id,
            "protection": lambda: protection_workspace.active_tool_id,
            "study": lambda: study_tool_runtime.active_tool_id,
        },
        icon_provider=_tool_icon,
        study_runtime=study_tool_runtime,
        parent=None,
    )
    plugin_manager = PluginManager(); resources["plugin_manager"] = plugin_manager; plugin_manager.define_defaults(); plugin_manager.load_all(); plugin_registry = plugin_manager.registry
    canvas_entry = plugin_registry.get_entry("canvas"); panels_entry = plugin_registry.get_entry("panels")
    if canvas_entry is None: raise RuntimeError("CanvasPlugin is not registered.")
    if panels_entry is None: raise RuntimeError("PanelsPlugin is not registered.")
    canvas_plugin = canvas_entry.plugin; panels_plugin = panels_entry.plugin; set_composition = getattr(canvas_plugin, "set_composition", None)
    if not callable(set_composition): raise RuntimeError("CanvasPlugin does not expose set_composition().")
    set_composition(canvas_composition); panel_presentation_bridge = PanelPresentationBridge(panels_plugin)
    set_workspace_surface = getattr(canvas_plugin, "set_workspace_surface", None)
    if not callable(set_workspace_surface): raise RuntimeError("CanvasPlugin does not expose set_workspace_surface().")
    set_workspace_surface(workspace_surface_host)

    # ShellPlugin owns the visible central widget composition. It requires a
    # distinct root widget so that its QVBoxLayout never becomes a child
    # layout of the GraphicsView that it is intended to contain.
    root_widget = QWidget()
    root_widget.setObjectName("GridForgeShellRoot")
    window = MainWindow(controller=controller, plugin_registry=plugin_registry, central_surface=root_widget)
    # The engineering plugin stays headless; the composition root explicitly
    # attaches its optional telemetry projection to the Study workspace.
    motor_dynamics_dashboard = MotorTelemetryDashboardWidget(
        motor_dynamics_plugin.read_models,
        event_bus=gridforge_application.event_bus,
    )
    resources["motor_dynamics_dashboard"] = motor_dynamics_dashboard
    icon = branding.icon()
    if icon is not None:
        window.setWindowIcon(icon)
    window.setWindowTitle(branding.DEFAULT_TITLE)

    action_router = UIActionRouter()
    workspace_surface_host.set_action_router(action_router)
    engineering_context = EngineeringContextStore()

    def _editor_context_factory(workspace_id: str, area: object, editor: object, region_id: str) -> object:
        editor_type = str(getattr(editor, "editor_type", "sld"))
        active_tool = workspace_surface_host.active_tool_id_for(editor_type)
        definition = workspace_surface_host.tool_definition_for(editor_type, active_tool)
        # The shared SelectionManager is the SLD selection authority only.
        # Other editor disciplines must not inherit hidden/late SLD selection.
        selected_ids = (
            tuple(str(value) for value in canvas_composition.selection_manager.get_selected_ids())
            if editor_type == "sld"
            else ()
        )
        engineering = engineering_context.current.with_updates(
            project_id=getattr(getattr(gridforge_application.project_lifecycle, "context", None), "project_id", None),
            document_id=project_workspace_lifecycle.documents.active_document_id,
            discipline=editor_type,
            active_tool=active_tool,
            selected_ids=selected_ids,
        )
        mode = ToolMode.IDLE
        settings = None
        if definition is not None:
            if definition.default_mode:
                try:
                    mode = ToolMode(definition.default_mode)
                except ValueError:
                    mode = ToolMode.IDLE
            settings = ToolSettings(definition.tool_id, values=definition.settings)
        from ui.workspace.engineering_context import EditorContext
        region = next((item for item in getattr(editor, "regions", ()) if item.region_id == region_id), None)
        return EditorContext(
            workspace=workspace_id,
            workspace_id=workspace_id,
            area=area,
            editor=editor,
            editor_id=str(getattr(editor, "editor_id", editor_type)),
            region=region,
            project_id=engineering.project_id,
            document_id=engineering.document_id,
            engineering=engineering,
            selection_context={"selected_ids": selected_ids},
            active_tool=active_tool,
            tool_mode=mode,
            tool_settings=settings,
            interaction_state={},
            view_state={},
        )

    workspace_realizer = WorkspaceRealizer(
        main_window=window,
        editor_host=workspace_surface_host,
        context_factory=_editor_context_factory,
    )
    workspace_controller = WorkspaceController(
        manager=workspace_manager,        realizer=workspace_realizer,
    )
    resources["workspace_controller"] = workspace_controller
    project_workspace_lifecycle = ProjectWorkspaceLifecycle(
        workspace_controller=workspace_controller,
    )
    # Complete the canonical controller/lifecycle graph now that the workspace
    # host exists. SLDController is created earlier because Canvas composition
    # depends on it, so lifecycle ownership is attached explicitly here.
    sld_controller.bind_workspace_lifecycle(project_workspace_lifecycle)
    project_workspace_adapter = ProjectWorkspaceApplicationAdapter(
        application=gridforge_application,
        lifecycle=project_workspace_lifecycle,
        default_symbol_presentation_factory=presentation_bootstrap.default_symbol_presentation,
    )
    resources["project_workspace_adapter"] = project_workspace_adapter
    project_workspace_adapter.configure_presentation_activation_bridge(workspace_surface_host.set_sld_document)
    project_workspace_adapter.configure_document_transition_guard(lambda: controller.set_tool("select", cancel_active_creation=True))

    def _reset_interaction_for_project_transition():
        # Project replacement discards transient placement state; unlike a
        # document switch, it must not persist that state into the old project.
        controller.clear_tool()
        controller.set_tool("select")

        # If the Application/lifecycle transition fails, leave the still-active
        # old project in a deterministic safe interaction mode. Never recreate
        # the discarded creation session or its stale preview.
        def restore_safe_interaction() -> None:
            controller.set_tool("select")

        return restore_safe_interaction

    project_workspace_adapter.configure_project_transition_guard(
        _reset_interaction_for_project_transition
    )

    status_plugin = None

    def _refresh_status() -> None:
        project_context = getattr(gridforge_application.project_lifecycle, "context", None)
        project_name = getattr(project_context, "name", None)
        active_document = project_workspace_lifecycle.documents.active_document
        document_name = getattr(active_document, "name", None)
        title = f"{branding.PRODUCT_NAME} — {project_name}" if project_name else branding.DEFAULT_TITLE
        if document_name:
            title += f" — {document_name}"
        window.setWindowTitle(title)
        canvas_composition.surface.set_document_title(document_name or project_name)
        workspace_surface_host.refresh_sld_document_tabs()
        if status_plugin is not None:
            status_plugin.refresh_authoritative_state()

    def _new_project() -> None:
        project_workspace_adapter.new_project(name="Untitled Project")
        _refresh_status()

    def _open_project() -> None:
        path, _ = QFileDialog.getOpenFileName(window, "Open GridForge Project", "", "GridForge Project (*.gridforge);;All Files (*)")
        if path:
            project_workspace_adapter.open_project(path)
            _refresh_status()

    def _save_project() -> None:
        context = gridforge_application.project_lifecycle.context
        path = str(context.path) if context is not None and context.path is not None else ""
        if not path:
            _save_project_as()
            return
        gridforge_application.save_project(path)
        _refresh_status()

    def _choose_save_as_path(context: object) -> str | None:
        path, _ = QFileDialog.getSaveFileName(
            window,
            "Save GridForge Project",
            "",
            "GridForge Project (*.gridforge)",
        )
        return path or None

    def _save_project_as() -> None:
        path = _choose_save_as_path(gridforge_application.project_lifecycle.context)
        if path:
            gridforge_application.save_project_as(path)
            _refresh_status()

    def _close_project() -> None:
        close_controller.request_close()
        _refresh_status()

    def _show_equipment_browser() -> None:
        # The explorer Region is intentionally hidden in the default SLD layout.
        # Open it through the workspace host instead of trying to focus a hidden
        # Region (WorkspaceRealizer correctly rejects invisible Regions).
        _activate_workspace(SLD_WORKSPACE_ID, "sld")
        workspace_surface_host.toggle_region("sld", "explorer")

    def _show_study_cases() -> None:
        # The Study editor has a timeline region, not the SLD-only explorer.
        # Activate its main area instead of requesting a nonexistent Region.
        _activate_workspace(STUDY_WORKSPACE_ID, "study")
        workspace_controller.realizer.focus_area("main-study")

    def _show_unconfigured_surface(title: str) -> None:
        QMessageBox.information(window, title, f"{title} presentation is not configured in the current workspace.")

    def _commit_network() -> None:
        """Commit DraftNetwork through the canonical Application operation."""
        try:
            result = gridforge_application.commit_draft_network()
        except ExecutionError as exc:
            # Expected Application execution failures are surfaced once at
            # this UI boundary; retain structured and causal diagnostics.
            diagnostic = f"{exc.code}: {exc.message}"
            if exc.details:
                diagnostic += f"\nDetails: {dict(exc.details)!r}"
            if exc.cause is not None:
                diagnostic += f"\nCause: {exc.cause}"
            QMessageBox.critical(
                window,
                "Commit Network",
                diagnostic,
            )
            return
        except (RuntimeError, ValueError, TypeError) as exc:
            # Application preconditions and draft-contract errors are expected
            # user-facing failures, not uncaught UI exceptions.
            QMessageBox.critical(window, "Commit Network", str(exc))
            return
        if not result.success:
            # Some Application integrations return a failed result instead
            # of raising the structured execution error. Handle that expected
            # outcome here without converting it into an unrelated exception.
            QMessageBox.critical(
                window,
                "Commit Network",
                result.message or "The network commit failed without a diagnostic message.",
            )
            return
        _refresh_status()

    # Clipboard contains semantic Application read data plus the authored SLD
    # presentation payload. QGraphicsItems are never copied as project truth.
    sld_clipboard: list[dict[str, object]] = []

    def _find_sld_node(selected_id: object) -> object | None:
        model = getattr(gridforge_application.presentation, "model", None)
        if model is None:
            return None
        getter = getattr(model, "get_node_by_equipment_id_optional", None)
        if callable(getter):
            node = getter(str(selected_id))
            if node is not None:
                return node
        getter = getattr(model, "get_node_optional", None)
        if callable(getter):
            return getter(str(selected_id))
        return None

    def _find_network_element(selected_id: object) -> object | None:
        try:
            network = gridforge_application.read_network()
        except RuntimeError:
            return None
        return next(
            (element for element in network.elements if str(getattr(element, "object_id", "")) == str(selected_id)),
            None,
        )

    def _select_all() -> None:
        scene = canvas_composition.view.scene()
        manager = canvas_composition.selection_manager
        if scene is None:
            return
        manager.clear()
        for item in tuple(scene.items()):
            object_id = getattr(item, "object_id", None)
            if object_id is not None:
                manager.add_to_selection(object_id)

    def _copy_selection() -> None:
        sld_clipboard.clear()
        for selected_id in canvas_composition.selection_manager.get_selected_ids():
            element = _find_network_element(selected_id)
            node = _find_sld_node(selected_id)
            if element is None or node is None:
                continue
            read_model = gridforge_application.read_element(
                str(element.element_type),
                str(element.object_id),
            )
            element_type = str(read_model.element_type).strip().lower()
            if element_type not in {"bus", "transformer", "breaker"}:
                messages_panel.append_message(
                    f"Copy skipped: semantic paste is not defined for {element_type}."
                )
                continue
            if element_type == "transformer" and not bool(
                getattr(read_model, "attributes", {}).get("in_service", True)
            ):
                # CreateTransformerCommand has no in_service field and there
                # is no canonical Transformer service-state command. Pasting
                # this item would silently reactivate an out-of-service unit.
                messages_panel.append_message(
                    "Copy skipped: Transformer service state cannot yet be "
                    "preserved by the canonical create command."
                )
                continue
            sld_clipboard.append({
                "element_type": element_type,
                "attributes": dict(getattr(read_model, "attributes", {}) or {}),
                "labels": dict(getattr(read_model, "labels", {}) or {}),
                "position": (float(node.x), float(node.y)),
                "presentation": None if node.presentation is None else node.presentation.to_dict(),
            })

    def _paste_selection() -> None:
        if not sld_clipboard:
            return
        selection = canvas_composition.selection_manager
        selection.clear()
        offset_x, offset_y = 40.0, 40.0

        for source in tuple(sld_clipboard):
            element_type = str(source["element_type"]).strip().lower()
            attributes = dict(source.get("attributes", {}))
            labels = dict(source.get("labels", {}))
            source_x, source_y = source["position"]
            name = str(labels.get("name") or attributes.get("name") or element_type.title())
            new_id = f"{element_type}-copy-{uuid4().hex[:8]}"
            x = float(source_x) + offset_x
            y = float(source_y) + offset_y
            in_service = bool(attributes.get("in_service", True))

            if element_type == "bus":
                result = gridforge_application.execute(CreateBusCommand(
                    bus_id=new_id, name=name,
                    nominal_voltage_kv=float(attributes.get("nominal_voltage_kv", attributes.get("nominalVoltage", 0.0)) or 0.0),
                    voltage_pu=float(attributes.get("voltage_pu", 1.0) or 1.0),
                    angle_deg=float(attributes.get("angle_deg", 0.0) or 0.0),
                    frequency_hz=float(attributes.get("frequency_hz", 50.0) or 50.0),
                    in_service=in_service, presentation_x=x, presentation_y=y,
                ))
            elif element_type == "transformer":
                result = gridforge_application.execute(CreateTransformerCommand(
                    transformer_id=new_id, name=name,
                    r=float(attributes.get("r", 0.0) or 0.0),
                    x=float(attributes.get("x", 0.0) or 0.0),
                    b=float(attributes.get("b", 0.0) or 0.0),
                    impedance_basis=attributes.get("impedance_basis", "engineering"),
                    impedance_base_mva=attributes.get("impedance_base_mva"),
                    impedance_base_voltage_kv=attributes.get("impedance_base_voltage_kv"),
                    tap=float(attributes.get("tap", 1.0) or 1.0),
                    shift=float(attributes.get("shift", 0.0) or 0.0),
                    rate_mva=attributes.get("rate_mva"),
                    presentation_x=x, presentation_y=y,
                ))
            elif element_type == "breaker":
                result = gridforge_application.execute(CreateBreakerCommand(
                    breaker_id=new_id, name=name, in_service=in_service,
                    closed=bool(attributes.get("closed", True)),
                    failed=bool(attributes.get("failed", False)),
                    voltage_kv=attributes.get("voltage_kv"),
                    current_a=attributes.get("current_a"),
                    interrupting_ka=attributes.get("interrupting_ka"),
                    presentation_x=x, presentation_y=y,
                ))
            else:
                messages_panel.append_message(
                    f"Paste skipped: semantic clone is not defined for {element_type}."
                )
                continue

            if not result.success:
                raise RuntimeError(result.message)

            new_node = _find_sld_node(new_id)
            presentation = source.get("presentation")
            if new_node is not None and presentation is not None:
                presentation_result = gridforge_application.execute(
                    SetSLDNodePresentationCommand(
                        node_id=str(new_node.node_id),
                        presentation=dict(presentation),
                    )
                )
                if not presentation_result.success:
                    raise RuntimeError(presentation_result.message)
            selection.add_to_selection(new_id)

    def _delete_selection() -> None:
        selected_ids = tuple(canvas_composition.selection_manager.get_selected_ids())
        if not selected_ids:
            return

        for selected_id in selected_ids:
            element = _find_network_element(selected_id)
            if element is not None:
                element_type = str(element.element_type).strip().lower()
                if element_type == "bus":
                    result = gridforge_application.execute(DeleteBusCommand(bus_id=str(element.object_id)))
                elif element_type == "transformer":
                    result = gridforge_application.execute(DeleteTransformerCommand(transformer_id=str(element.object_id)))
                elif element_type == "breaker":
                    result = gridforge_application.execute(DeleteBreakerCommand(breaker_id=str(element.object_id)))
                else:
                    raise RuntimeError(
                        f"Delete is not semantically mapped for {element_type!r}; refusing to hide the object."
                    )
                if not result.success:
                    raise RuntimeError(result.message)
                continue

            try:
                wire = gridforge_application.read_simple_wire(str(selected_id))
            except (KeyError, RuntimeError):
                wire = None
            if wire is not None:
                result = gridforge_application.execute(
                    RemoveSimpleWireConnectionCommand(connection_id=str(selected_id))
                )
                if not result.success:
                    raise RuntimeError(result.message)
                continue

            # Draft equipment has not crossed the Application commit boundary;
            # remove it through the registered DraftNetwork command, never by
            # deleting only its transient graphics item.
            draft_snapshot = gridforge_application.read_draft_network()
            draft_equipment = (
                (draft_snapshot or {}).get("equipment", ())
                if isinstance(draft_snapshot, dict) else ()
            )
            if any(str(item.get("draft_id", "")) == str(selected_id) for item in draft_equipment):
                result = gridforge_application.execute(
                    RemoveDraftEquipmentCommand(draft_id=str(selected_id))
                )
                if not result.success:
                    raise RuntimeError(result.message)
                continue

            draft_connections = (
                (draft_snapshot or {}).get("connections", ())
                if isinstance(draft_snapshot, dict) else ()
            )
            if any(
                str(item.get("connection_id", "")) == str(selected_id)
                for item in draft_connections
                if isinstance(item, dict)
            ):
                result = gridforge_application.execute(
                    RemoveDraftConnectionCommand(connection_id=str(selected_id))
                )
                if not result.success:
                    raise RuntimeError(result.message)
                continue

            node = _find_sld_node(selected_id)
            if node is not None and getattr(node, "equipment_id", None) is None:
                result = gridforge_application.execute(RemoveSLDNodeCommand(node_id=str(node.node_id)))
                if not result.success:
                    raise RuntimeError(result.message)
                continue

            raise RuntimeError(
                f"Selection {selected_id!r} is not mapped to an authoritative delete command."
            )

        canvas_composition.selection_manager.clear()

    def _cut_selection() -> None:
        selected_ids = tuple(canvas_composition.selection_manager.get_selected_ids())
        if not selected_ids:
            return

        _copy_selection()
        if len(sld_clipboard) != len(selected_ids):
            messages_panel.append_message(
                "Cut canceled: one or more selected items cannot be copied "
                "as semantic equipment. Nothing was deleted."
            )
            return

        # Deletion currently dispatches one command per object and does not
        # provide an atomic batch/rollback boundary. A multi-object Cut could
        # therefore delete earlier objects before a later command fails.
        # Keep Cut non-destructive until Application exposes an atomic batch.
        if len(selected_ids) > 1:
            messages_panel.append_message(
                "Cut canceled: multi-object Cut is unavailable until deletion "
                "supports an atomic Application batch. Nothing was deleted."
            )
            return

        _delete_selection()

    def _selected_sld_nodes() -> tuple[object, ...]:
        return tuple(
            node for node in (
                _find_sld_node(selected_id)
                for selected_id in canvas_composition.selection_manager.get_selected_ids()
            )
            if node is not None
        )

    def _transform_selected_symbols(*, rotation_delta: float = 0.0, mirror: str | None = None) -> None:
        nodes = _selected_sld_nodes()
        for node in nodes:
            if node.presentation is None:
                continue
            presentation = node.presentation.to_dict()
            if rotation_delta:
                presentation["rotation"] = float(presentation.get("rotation", 0.0)) + rotation_delta
            properties = dict(presentation.get("properties", {}))
            if mirror == "horizontal":
                properties["mirror_x"] = not bool(properties.get("mirror_x", False))
            elif mirror == "vertical":                properties["mirror_y"] = not bool(properties.get("mirror_y", False))
            presentation["properties"] = properties
            result = gridforge_application.execute(
                SetSLDNodePresentationCommand(node_id=str(node.node_id), presentation=presentation)
            )
            if not result.success:
                raise RuntimeError(result.message)

    def _activate_select_for_editing() -> None:
        controller.set_tool("select", cancel_active_creation=True)

    def _activate_workspace(workspace_id: str, surface_id: str) -> None:
        # Publish context only after the canonical workspace transition succeeds.
        # The visible editor and action router must share one active discipline.
        workspace_controller.activate(workspace_id)

        # Selection belongs to the active presentation, not to every workspace.
        # Clear the shared SLD selection after a successful workspace switch so
        # stale IDs cannot leak into another editor's context or action routing.
        canvas_composition.selection_manager.clear()
        active_tool = workspace_surface_host.active_tool_id_for(surface_id)
        engineering_context.update(
            discipline=surface_id,
            active_tool=active_tool,
            selected_ids=(),
        )

    def _activate_surface(surface_id: str) -> None:
        # Topology and Map are compatibility surfaces outside WorkspaceController.
        # Keep their context transition explicit so SLD-only actions are disabled.
        workspace_surface_host.activate(surface_id)
        # These surfaces do not own the shared SLD selection. Do not carry
        # selected SLD IDs into their editor context or action-state evaluation.
        canvas_composition.selection_manager.clear()
        engineering_context.update(
            discipline=surface_id,
            active_tool=None,
            selected_ids=(),
        )

    def _toggle_sld_maximize() -> None:
        _activate_workspace(SLD_WORKSPACE_ID, "sld")
        maximized = workspace_surface_host.editor_host.maximized_area_id == "main-sld"
        workspace_surface_host.editor_host.set_area_maximized("main-sld", not maximized)

    def _toggle_sld_region(region_id: str) -> None:
        _activate_workspace(SLD_WORKSPACE_ID, "sld")
        workspace_surface_host.toggle_region("sld", region_id)

    action_router.register_many({
        "project.new": _new_project,
        "project.open": _open_project,
        "project.save": _save_project,
        "project.save_as": _save_project_as,
        "project.close": _close_project,
        "application.exit": window.close,
        "edit.select_all": _select_all,
        "edit.cut": _cut_selection,
        "edit.box_select": _activate_select_for_editing,
        "edit.move": _activate_select_for_editing,
        "edit.drag_move": _activate_select_for_editing,
        "edit.rotate": lambda: _transform_selected_symbols(rotation_delta=90.0),
        "edit.mirror_horizontal": lambda: _transform_selected_symbols(mirror="horizontal"),
        "edit.mirror_vertical": lambda: _transform_selected_symbols(mirror="vertical"),
        "network.commit_draft": _commit_network,
        "view.sld_workspace": lambda: _activate_workspace(SLD_WORKSPACE_ID, "sld"),
        "view.control_workspace": lambda: _activate_workspace(CONTROL_WORKSPACE_ID, "control"),
        "view.protection_workspace": lambda: _activate_workspace(PROTECTION_WORKSPACE_ID, "protection"),
        "view.study_workspace": lambda: _activate_workspace(STUDY_WORKSPACE_ID, "study"),
        "view.topology": lambda: _activate_surface("topology"),
        "view.map": lambda: _activate_surface("map"),
        "view.reports": lambda: _activate_workspace(STUDY_WORKSPACE_ID, "study"),
        "view.maximize_editor": _toggle_sld_maximize,
        "view.toggle_explorer": lambda: _toggle_sld_region("explorer"),
        "view.toggle_inspector": lambda: _toggle_sld_region("sidebar"),
        "view.toggle_messages": lambda: _toggle_sld_region("diagnostics"),
        "view.toggle_tool_settings": lambda: _toggle_sld_region("tool_settings"),
        "view.equipment_browser": _show_equipment_browser,
        "study.cases": _show_study_cases,
        "protection.panel": lambda: _activate_workspace(PROTECTION_WORKSPACE_ID, "protection"),
        "control.panel": lambda: _activate_workspace(CONTROL_WORKSPACE_ID, "control"),
        "help.about": lambda: QMessageBox.information(window, "About GridForge", "GridForge V2 — power-system engineering platform."),
    })

    # The action catalog is the sole source of canonical presentation metadata.
    # main.py only composes runtime handlers and compatibility aliases.
    canonical_definitions = {
        definition.action_id: definition
        for definition in build_action_definitions(contextual_tool_definitions_all)
    }
    def _activate_workspace_tool(activation, tool_id: str) -> object:
        result = activation(tool_id)
        if result is not False:
            # Each runtime remains the activation owner; the shared host only
            # projects the resulting active-tool state into its owning shelf
            # and current editor context.
            workspace_surface_host.refresh_tool_shelves()
        return result

    canonical_handlers = {
        "select": lambda: controller.set_tool("select", cancel_active_creation=True),
        "move": _activate_select_for_editing,
        "pan": lambda: canvas_composition.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag),
        "zoom_in": lambda: canvas_composition.navigation_controller.zoom_in(1),
        "zoom_out": lambda: canvas_composition.navigation_controller.zoom_out(1),
        "fit_view": lambda: canvas_composition.navigation_controller.fit_content(50.0),
        "copy": _copy_selection,
        "paste": _paste_selection,
        "delete": _delete_selection,
        "undo": controller.undo,
        "redo": controller.redo,
        "sld_select": lambda: controller.set_tool("select", cancel_active_creation=True),
        "sld_move": _activate_select_for_editing,
        "sld_wire": lambda: controller.set_tool("wire", cancel_active_creation=True),
    }
    for action_id, handler in canonical_handlers.items():
        definition = canonical_definitions.get(action_id)
        if definition is None:
            raise RuntimeError(f"Canonical action definition is missing: {action_id!r}")
        if not action_router.has(action_id):
            action_router.register_definition(definition, handler)
        else:
            action_router.set_definition(definition)

    # Register one routed action per editor/tool pair. Runtime ownership is
    # selected from the definition editor scope, never from a coincidentally
    # matching tool ID in another editor registry.
    _tool_definitions = tuple(
        definition
        for definition in build_action_definitions(contextual_tool_definitions_all)
        if definition.action_id.startswith("tool.")
    )
    _tool_action_ids_by_id: dict[str, list[str]] = {}
    for definition in _tool_definitions:
        if definition.tool_id is not None:
            _tool_action_ids_by_id.setdefault(definition.tool_id, []).append(definition.action_id)
    for definition in _tool_definitions:
        action_id = definition.action_id
        if action_router.has(action_id):
            continue
        tool_id = definition.tool_id
        if tool_id is None:
            continue
        editor_type = definition.metadata.get("editor_type") or definition.scope
        if editor_type == "sld" and tool_manager.has_tool(tool_id):
            handler = lambda tool_id=tool_id: controller.set_tool(
                tool_id,
                cancel_active_creation=True,
            )
        elif editor_type == "control":
            handler = lambda tool_id=tool_id: _activate_workspace_tool(control_workspace.activate_tool_id, tool_id)
        elif editor_type == "protection":
            handler = lambda tool_id=tool_id: _activate_workspace_tool(protection_workspace.activate_tool_id, tool_id)
        elif editor_type == "study":
            handler = lambda tool_id=tool_id: _activate_workspace_tool(study_tool_runtime.activate, tool_id)
        elif editor_type == "editor" and tool_manager.has_tool(tool_id):
            # Editor-agnostic legacy definitions are permitted only when the
            # canonical SLD runtime actually owns the requested tool.
            handler = lambda tool_id=tool_id: controller.set_tool(
                tool_id,
                cancel_active_creation=True,
            )
        else:
            # No concrete owner means no registered route; the shelf remains
            # disabled under the fail-closed ToolShelf contract.
            continue
        action_router.register_definition(definition, handler)

    # Keep unscoped tool aliases only when the full catalogue proves the tool
    # ID is unique. A colliding ID such as "select" has no ambiguous alias.
    for tool_id, scoped_action_ids in _tool_action_ids_by_id.items():
        if len(scoped_action_ids) == 1 and action_router.has(scoped_action_ids[0]):
            alias = f"tool.{tool_id}"
            if not action_router.has(alias):
                action_router.register_alias(alias, scoped_action_ids[0])

    # Existing surface IDs are compatibility aliases only.
    for alias, canonical in {
        "edit.undo": "undo", "edit.redo": "redo",
        "edit.delete_selection": "delete", "edit.copy": "copy", "edit.paste": "paste",
        "view.zoom_in": "zoom_in", "view.zoom_out": "zoom_out",
        "view.fit": "fit_view", "view.pan": "pan",
    }.items():
        if not action_router.has(alias):
            action_router.register_alias(alias, canonical)

    # Canonical non-tool surface metadata is owned by the action catalog.
    # The composition root binds those definitions to already-registered routes;
    # it does not maintain a second metadata table.
    for definition in surface_action_definitions():
        if action_router.has(definition.action_id):
            action_router.set_definition(definition)

    def _action_enabled(action_id: str) -> bool:
        context = engineering_context.current
        project_active = context.project_id is not None and gridforge_application.project_lifecycle.context is not None
        selected = bool(context.selected_ids)
        active_tool = context.active_tool
        discipline = context.discipline
        # These routed handlers operate on the SLD canvas/selection manager.
        # Do not advertise them as available while another editor owns focus;
        # otherwise a shortcut or menu action can mutate the hidden SLD.
        sld_only_actions = {
            "select", "move", "pan", "zoom_in", "zoom_out", "fit_view",
            "copy", "paste", "delete", "sld_select", "sld_move", "sld_wire",
            "edit.select_all", "edit.cut", "edit.box_select", "edit.move",
            "edit.drag_move", "edit.rotate", "edit.mirror_horizontal",
            "edit.mirror_vertical",
        }
        if action_id in sld_only_actions and (not project_active or discipline != "sld"):
            return False

        if action_id in {"project.save", "project.save_as", "project.close",
                         "study.cases", "view.sld_workspace", "view.control_workspace",
                         "view.protection_workspace", "view.topology", "view.map", "view.reports",
                         "view.maximize_editor", "view.toggle_explorer", "view.toggle_inspector",
                         "view.toggle_messages", "view.toggle_tool_settings"}:
            return project_active
        if action_id.startswith("tool."):
            try:
                definition = action_router.definition(action_id)
            except (KeyError, RuntimeError, ValueError):
                return False
            if definition.tool_id == "bus" and definition.scope == "sld":
                return project_active and discipline == "sld" and gridforge_application.supports(CREATE_BUS)
            if definition.tool_id == "wire" and definition.scope == "sld":
                return project_active and discipline == "sld" and gridforge_application.supports(CREATE_SIMPLE_WIRE)
            if definition.tool_id is None:
                return project_active
            allowed_editors = tuple(definition.metadata.get("editor_types", ()))
            if not allowed_editors:
                allowed_editors = (definition.scope,) if definition.scope in {"sld", "control", "protection", "study"} else ()
            return project_active and (not allowed_editors or discipline in allowed_editors)
        if action_id in {"undo", "edit.undo"}:
            return project_active and bool(getattr(controller, "can_undo", lambda: False)())
        if action_id in {"redo", "edit.redo"}:
            return project_active and bool(getattr(controller, "can_redo", lambda: False)())
        if action_id == "network.commit_draft":
            if not project_active or discipline != "sld":
                return False
            # Do not advertise Commit when the authoritative draft is empty.
            # Read the Application snapshot rather than inferring draft state
            # from canvas items, selection, or Qt presentation objects.
            try:
                draft_snapshot = gridforge_application.read_draft_network()
            except (RuntimeError, ValueError, TypeError):
                return False
            if not isinstance(draft_snapshot, dict):
                return False
            draft_equipment = draft_snapshot.get("equipment", ())
            draft_connections = draft_snapshot.get("connections", ())
            return bool(draft_equipment or draft_connections)
        if action_id in {"delete", "edit.delete_selection", "copy", "edit.copy", "edit.cut", "edit.rotate",
                         "edit.mirror_horizontal", "edit.mirror_vertical"}:
            return project_active and discipline == "sld" and selected
        if action_id in {"paste", "edit.paste"}:
            return project_active and bool(sld_clipboard) and discipline == "sld"
        if action_id in {"edit.box_select", "edit.move", "edit.drag_move"}:
            return project_active and discipline == "sld" and active_tool in {None, "select"}
        return True

    action_router.set_enabled_provider(_action_enabled)

    # The lifecycle service starts with an internal bootstrap context, but the
    # composition root must not materialize a presentation for that transient
    # context and then immediately replace it with a second project activation.
    # Configure the presentation factory first, then perform exactly one explicit
    # initial project activation; that activation creates the authoritative SLD
    # document for the project that the UI actually opens.
    project_id = "gridforge-project"
    project_context = project_workspace_adapter.new_project(
        name="GridForge Project",
        project_id=project_id,
        activate_workspace=False,
    )
    engineering_context.update(project_id=project_context.project_id, project_name=project_context.name, discipline="sld")
    sld_document = gridforge_application.presentation
    if not isinstance(sld_document, SLDDocument):
        raise RuntimeError("Application did not establish an SLDDocument for the active project.")
    gridforge_application.attach_sld_service(
        SLDService(
            sld_document,
            symbol_presentation_factory=presentation_bootstrap.default_symbol_presentation,
        )
    )
    gridforge_application.configure_project_presentation(
        presentation=sld_document,
        serializer=serialize_sld,
        deserializer=deserialize_sld,
    )
    sld_controller.register_document(sld_document)
    sld_controller.reconcile_presentation()

    # Resolve the canonical canvas synchronization callable before registering
    # lifecycle callbacks that may invoke it. This removes the composition-order
    # dependency on a later local binding.
    synchronize_canvas = getattr(canvas_plugin, "refresh_canvas", None)
    if not callable(synchronize_canvas):
        raise RuntimeError("CanvasPlugin does not expose refresh_canvas().")

    def _attempt_workspace_sync_step(step: str, operation: Callable[[], None], change: ProjectWorkspaceChanged) -> None:
        try:
            operation()
        except Exception:
            logging.getLogger(__name__).exception(
                "Project workspace projection step failed: operation=%s project_id=%s step=%s",
                change.operation,
                change.project_id,
                step,
            )

    def handle_project_workspace_changed(change: ProjectWorkspaceChanged) -> None:
        document = change.state.document
        project = change.state.project
        next_document_id = document.document_id if isinstance(document, SLDDocument) else None

        def update_context() -> None:
            current_context = engineering_context.current
            if current_context.document_id != next_document_id or current_context.project_id != getattr(project, "project_id", None):
                canvas_composition.selection_manager.clear()
            engineering_context.update(
                project_id=getattr(project, "project_id", None),
                project_name=getattr(project, "name", None),
                document_id=next_document_id,
                discipline=(
                    "sld"
                    if change.operation in {"new", "open"}
                    else current_context.discipline
                ),
                selected_ids=tuple(str(value) for value in canvas_composition.selection_manager.get_selected_ids()),
            )

        # These are independent projection steps after the Application transition
        # commits. A failure in one step must not prevent the remaining surfaces
        # from attempting to synchronize to the authoritative workspace state.
        _attempt_workspace_sync_step("engineering context", update_context, change)
        if isinstance(document, SLDDocument):
            _attempt_workspace_sync_step("SLD presentation reconciliation", sld_controller.reconcile_presentation, change)
            _attempt_workspace_sync_step(
                "active SLD surface",
                lambda: workspace_surface_host.set_sld_document(document),
                change,
            )
        else:
            _attempt_workspace_sync_step(
                "clear active SLD surface",
                lambda: workspace_surface_host.set_sld_document(None),
                change,
            )

        _attempt_workspace_sync_step("SLD document tabs", workspace_surface_host.refresh_sld_document_tabs, change)
        _attempt_workspace_sync_step("canvas synchronization", synchronize_canvas, change)

        def update_properties_panel() -> None:
            properties_panel = panels_plugin.get_panel("properties")
            if isinstance(document, SLDDocument):
                document_changed = getattr(properties_panel, "handle_sld_document_changed", None)
                if callable(document_changed):
                    document_changed()
            else:
                clear_annotations = getattr(properties_panel, "clear_sld_annotation_controls", None)
                if callable(clear_annotations):
                    clear_annotations()

        _attempt_workspace_sync_step("properties panel", update_properties_panel, change)

    project_workspace_adapter.subscribe(handle_project_workspace_changed)

    def _new_sld_document() -> None:
        # The adapter publishes ProjectWorkspaceChanged after activation;
        # handle_project_workspace_changed is the sole UI synchronization path.
        project_workspace_adapter.new_sld_document()

    def _close_sld_document(document_id: str) -> None:
        document = project_workspace_adapter.documents.require(document_id)
        if project_workspace_adapter.is_document_dirty(document_id):
            box = QMessageBox(window)
            box.setWindowTitle("Close SLD Document")
            box.setText("Save changes to " + str(document.name) + "?")
            save_button = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
            discard_button = box.addButton("Don't Save", QMessageBox.ButtonRole.DestructiveRole)
            cancel_button = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_button:
                return
            decision = "save" if clicked is save_button else "discard"
        else:
            decision = "discard"
        try:
            # Successful transitions publish once through the adapter. The
            # subscribed handler refreshes tabs, context, canvas and inspector.
            project_workspace_adapter.close_document(document_id, decision=decision)
        except BaseException as exc:
            QMessageBox.critical(window, "Close SLD Document", str(exc))

    workspace_surface_host.configure_sld_document_lifecycle(
        project_workspace_lifecycle.documents,
        activate=project_workspace_adapter.activate_document,
        new_document=_new_sld_document,
        close_document=_close_sld_document,
    )
    workspace_surface_host.refresh_sld_document_tabs()
    sld_canvas_snapshot = sld_canvas_projection.project(sld_document.model)
    context = PluginContext(main_window=window, parent=window, application=gridforge_application, root_widget=root_widget, controller=controller, action_router=action_router, equipment_registry=equipment_registry, symbol_registry=presentation_bootstrap.symbol_registry, sld_document=sld_document, sld_canvas_projection=sld_canvas_projection, sld_canvas_render_system=sld_canvas_render_system, tool_manager=tool_manager, services={"motor_dynamics_composition": getattr(gridforge_application, "motor_dynamics_composition", None)}, metadata={"sld_canvas_snapshot": sld_canvas_snapshot, "engineering_context_store": engineering_context, "project_id": project_context.project_id, "project_workspace_adapter": project_workspace_adapter, "panel_presentation_bridge": panel_presentation_bridge, "workspace_controller": workspace_controller, "selection_manager": canvas_composition.selection_manager, "graphics_view": canvas_composition.view})
    contexts = {plugin_id: context for plugin_id in plugin_manager.plugin_ids}; plugin_manager.set_contexts(contexts); plugin_manager.initialize_all()
    # Menu and toolbar enabled states are projections of the canonical action
    # router. Refresh them whenever selection, workspace, project, or tool
    # context changes outside an action-trigger callback.
    def _refresh_action_presentations(_context: object) -> None:
        for plugin_id in ("menu", "toolbar"):
            entry = plugin_registry.get_entry(plugin_id)
            plugin = entry.plugin if entry is not None else None
            refresh = getattr(plugin, "refresh_enabled_states", None)
            if callable(refresh):
                refresh()

    engineering_context.subscribe(_refresh_action_presentations)
    # Command-history availability can change without an engineering-context
    # update (for example, a canvas command). Refresh menu/toolbar QAction state
    # whenever the canonical Controller reports a state transition.
    controller.state_changed.connect(
        lambda: _refresh_action_presentations(engineering_context.current)
    )
    _refresh_action_presentations(engineering_context.current)
    status_plugin = plugin_registry.get_entry("status").plugin if plugin_registry.get_entry("status") is not None else None
    properties_panel = panels_plugin.get_panel("properties"); project_panel = panels_plugin.get_panel("project"); equipment_panel = panels_plugin.get_panel("equipment"); element_list_panel = panels_plugin.get_panel("element_list"); messages_panel = panels_plugin.get_panel("messages"); study_cases_panel = panels_plugin.get_panel("study_cases")
    for panel_id, panel in (("properties", properties_panel), ("project", project_panel), ("equipment", equipment_panel), ("element_list", element_list_panel), ("messages", messages_panel), ("study_cases", study_cases_panel)):
        if panel is None: raise RuntimeError(f"PanelsPlugin did not create required {panel_id!r} presentation.")

    def _report_action_failure(action_id: str, error: Exception) -> None:
        """Surface routed UI/Application failures in the persistent Messages panel."""
        code = str(getattr(error, "code", "") or type(error).__name__)
        message = str(getattr(error, "message", "") or error)
        details = getattr(error, "details", None)
        diagnostic = f"Action failed [{action_id}] — {code}: {message}"
        if details:
            diagnostic += f" | Details: {dict(details)!r}"
        cause = getattr(error, "cause", None)
        if cause is not None:
            diagnostic += f" | Cause: {cause}"
        messages_panel.append_message(diagnostic)

    action_router.set_error_handler(_report_action_failure)
    # Utility panels are physically re-homed into editor Regions. Their
    # QDockWidget wrappers remain compatibility-only.
    detached = {}
    for panel_id in ("project", "equipment", "properties", "element_list", "messages", "study_cases"):
        widget = panels_plugin.detach_panel(panel_id)
        if widget is not None:
            detached[panel_id] = widget
    explorer_container = QWidget(window)
    explorer_layout = QVBoxLayout(explorer_container)
    explorer_layout.setContentsMargins(0, 0, 0, 0)
    if "project" in detached:
        explorer_layout.addWidget(detached["project"], 1)
    if "equipment" in detached:
        explorer_layout.addWidget(detached["equipment"], 1)
    if "element_list" in detached:
        explorer_layout.addWidget(detached["element_list"], 1)
    workspace_surface_host.set_region_widgets(
        "sld",
        explorer=explorer_container,
        inspector=detached.get("properties"),
        diagnostics=detached.get("messages"),
    )
    workspace_surface_host.set_region_widgets(
        "control",
        inspector=control_workspace.editor_inspector,
        status=control_workspace.editor_status,
    )
    workspace_surface_host.set_region_widgets(
        "protection",
        explorer=protection_workspace.editor_explorer,
        inspector=protection_workspace.editor_inspector,
    )
    workspace_surface_host.set_region_widgets(
        "study",
        explorer=detached.get("study_cases"),
        inspector=motor_dynamics_dashboard,
    )

    def _on_render_diagnostic(diagnostic: object) -> None:
        messages_panel.append_message(
            "SLD rendering failure: "
            f"node={getattr(diagnostic, 'node_id', '<unknown>')} "
            f"equipment={getattr(diagnostic, 'equipment_id', None) or '<none>'} "
            f"symbol={getattr(diagnostic, 'symbol_id', None) or '<none>'} "
            f"{getattr(diagnostic, 'message', diagnostic)}"
        )

    sld_canvas_render_system.bind_diagnostic_sink(_on_render_diagnostic)
    for diagnostic in sld_canvas_render_system.render_diagnostics:        _on_render_diagnostic(diagnostic)
    def study_case_error_handler(error: BaseException) -> None:
        QMessageBox.critical(window, "Study Case", str(error))

    study_case_controller = StudyCaseController(
        application=gridforge_application,
        error_handler=study_case_error_handler,
    )
    study_tool_runtime.bind_study_case_controller(study_case_controller)
    study_cases_panel.set_run_handler(study_case_controller.run_study)
    study_cases_panel.set_create_handler(study_case_controller.create_study_case)
    resources["study_case_controller"] = study_case_controller

    canvas_composer.bind_selection_projection(composition=canvas_composition, properties_panel=properties_panel); selection_projection = canvas_composition.selection_projection
    if selection_projection is None: raise RuntimeError("CanvasComposer did not create the canonical SelectionProjectionCoordinator.")
    properties_panel.bind_sld_annotation_runtime(controller=sld_controller, selection_manager=canvas_composition.selection_manager)
    workspace_controller.activate_default()
    # Initial workspace activation may not publish a ProjectWorkspaceChanged
    # event; explicitly synchronize the annotation inspector after binding.
    active_sld_document = project_workspace_lifecycle.documents.active_document
    if isinstance(active_sld_document, SLDDocument):
        properties_panel.handle_sld_document_changed()
    else:
        properties_panel.clear_sld_annotation_controls()
    sld_update_coordinator = SLDUpdateCoordinator(application=gridforge_application, synchronizer=sld_read_synchronizer, canvas_refresh=synchronize_canvas, draft_projection=canvas_composition.draft_sld_projection)
    control_update_coordinator = ControlUpdateCoordinator(application=gridforge_application, canvas=control_workspace.canvas, canvas_refresh=control_workspace.refresh)
    element_list_panel.bind_selection_manager(canvas_composition.selection_manager)
    bind_project_selection = getattr(project_panel, "bind_selection_manager", None)
    if callable(bind_project_selection):
        bind_project_selection(canvas_composition.selection_manager)
    def _on_selection_changed(ids: object) -> None:
        # The shared SelectionManager represents SLD selection only. Ignore
        # late/hidden-canvas selection notifications while another discipline
        # owns the active editor context.
        if engineering_context.current.discipline != "sld":
            return
        engineering_context.update(selected_ids=ids)
        workspace_surface_host.refresh_selection_context(ids)

    canvas_composition.selection_manager.selection_changed.connect(_on_selection_changed)

    def _on_controller_tool_changed(current: object, previous: object) -> None:
        # This Controller owns SLD tool state. Do not let a hidden SLD tool
        # change overwrite the active Control/Protection/Study tool context.
        if engineering_context.current.discipline == "sld":
            engineering_context.update(active_tool=current)
        workspace_surface_host.refresh_tool_shelves()

    if callable(getattr(controller, "tool_changed", None).connect if getattr(controller, "tool_changed", None) is not None else None):
        controller.tool_changed.connect(_on_controller_tool_changed)
    def _update_study_result_overlay(result: object | None) -> None:
        """Refresh the SLD only when the selected result snapshot changes."""
        if sld_canvas_projection.set_study_result(result):
            synchronize_canvas()

    element_list_projection = ElementListProjection(application=gridforge_application, panel=element_list_panel); event_messages_projection = ApplicationEventMessagesProjection(panel=messages_panel); project_hierarchy_projection = ProjectHierarchyProjection(adapter=project_workspace_adapter, panel=project_panel, application=gridforge_application); validation_projection = ValidationProjection(application=gridforge_application, panel=messages_panel); study_projection = StudyProjection(application=gridforge_application, panel=study_cases_panel, result_changed=_update_study_result_overlay)
    projection_coordinator = UIProjectionCoordinator(projections=(sld_update_coordinator, control_update_coordinator, selection_projection, element_list_projection, event_messages_projection, project_hierarchy_projection, validation_projection, study_projection)); resources["ui_projection_coordinator"] = projection_coordinator
    ui_update_boundary = UIUpdateBoundary(event_bus=gridforge_application.event_bus, projection_coordinator=projection_coordinator); resources["ui_update_boundary"] = ui_update_boundary; ui_update_boundary.subscribe()
    sld_update_coordinator.reconcile_current_state()
    control_workspace.refresh()
    element_list_projection.refresh(ProjectLoaded(metadata={"project_id": project_context.project_id, "operation": "initial"})); event_messages_projection.refresh(ProjectLoaded(metadata={"project_id": project_context.project_id, "operation": "initial"})); validation_projection.refresh_from_application(); selection_projection.refresh()
    # Project close is already completed by MainWindow.closeEvent before
    # UILifecycle.shutdown. Shutdown must only release remaining presentation
    # document state; it must never re-run the Application project transition
    # and accidentally prompt/save a dirty project a second time.
    ui_lifecycle = UILifecycle(
        workspace_ready=lambda: project_workspace_adapter.state.workspace_id is not None,
        document_ready=lambda: project_workspace_adapter.state.document is not None,
        document_close=project_workspace_lifecycle.close_document,
        workspace_teardown=workspace_controller.close,
        cleanup=lambda: None,
    ); resources["ui_lifecycle"] = ui_lifecycle

    def project_transition_decision(context: object) -> object:
        project_name = getattr(context, "name", "the active project")
        result = QMessageBox.warning(
            window,
            "Unsaved Project Changes",
            f"{project_name} has unsaved changes. Do you want to save before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if result == QMessageBox.StandardButton.Save:
            from core.application.project_transition import ProjectTransitionDecision
            return ProjectTransitionDecision.SAVE
        if result == QMessageBox.StandardButton.Discard:
            from core.application.project_transition import ProjectTransitionDecision
            return ProjectTransitionDecision.DISCARD
        from core.application.project_transition import ProjectTransitionDecision
        return ProjectTransitionDecision.CANCEL

    project_workspace_adapter.configure_transition_decision_provider(project_transition_decision)
    project_workspace_adapter.configure_save_as_path_provider(_choose_save_as_path)
    close_controller = ProjectCloseController(
        application=project_workspace_adapter,
        decision_provider=project_transition_decision,
        save_as_path_provider=_choose_save_as_path,
    )
    window.set_close_handler(close_controller.request_close)
    if status_plugin is None:
        raise RuntimeError("StatusPlugin is required for authoritative status integration.")
    status_plugin.bind_authoritative_state(
        controller=controller,
        application=gridforge_application,
        selection_manager=canvas_composition.selection_manager,
        graphics_view=canvas_composition.view,
        workspace_controller=workspace_controller,
        project_adapter=project_workspace_adapter,
    )
    controller.bind_status_plugin(status_plugin)

    startup_splash.status("GridForge is ready.")
    ui_lifecycle.start(); ui_lifecycle.activate_document(); window.show()
    app.processEvents()
    startup_splash.finish(window)
    resources.pop("startup_splash", None)
    # Keep the established six-item build_application() return contract.
    # The engineering host is a composition-root lifecycle resource, attached
    # to the returned window for main() to shut down without changing callers.
    setattr(window, "_engineering_plugin_host", engineering_plugin_host)
    setattr(window, "_motor_dynamics_dashboard", motor_dynamics_dashboard)
    return app, window, plugin_manager, workspace_controller, ui_update_boundary, ui_lifecycle


def build_application() -> tuple[QApplication, MainWindow, PluginManager, WorkspaceController, UIUpdateBoundary, UILifecycle]:
    resources: dict[str, object] = {}
    try: return _build_application_impl(resources)
    except BaseException: _cleanup_startup_failure(resources); raise


def main() -> int:
    app, window, plugin_manager, workspace_controller, ui_update_boundary, ui_lifecycle = build_application()
    engineering_plugin_host = getattr(window, "_engineering_plugin_host", None)
    if not isinstance(engineering_plugin_host, EngineeringPluginHost):
        raise RuntimeError("EngineeringPluginHost was not retained by the composition root.")
    motor_dynamics_dashboard = getattr(window, "_motor_dynamics_dashboard", None)
    if not isinstance(motor_dynamics_dashboard, MotorTelemetryDashboardWidget):
        raise RuntimeError("Motor Dynamics dashboard was not retained by the composition root.")
    try: return int(app.exec())
    finally: _shutdown_components(ui_lifecycle=ui_lifecycle, workspace_controller=workspace_controller, ui_update_boundary=ui_update_boundary, plugin_manager=plugin_manager, engineering_plugin_host=engineering_plugin_host, motor_dynamics_dashboard=motor_dynamics_dashboard)


if __name__ == "__main__": raise SystemExit(main())