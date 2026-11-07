# ============================================================
# Author: Subhendu Mishra
# File: ui/canvas/canvas_composition.py
# GridForge V2 — Canvas Composition
# ============================================================
"""GridForge V2 application-owned Canvas composition boundary."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Callable, Iterable
from typing import Any, Optional

from ui.canvas.coordinate_system import CoordinateSystem
from ui.canvas.grid_scene import GridScene
from ui.canvas.grid_system import GridSystem
from ui.canvas.graphics_view import GraphicsView
from ui.canvas.interaction_manager import InteractionManager
from ui.canvas.mouse_event_adapter import MouseEventAdapter
from ui.canvas.navigation_controller import NavigationController
from ui.canvas.engineering_canvas_contract import CanvasInteractionContract
from ui.canvas.preview_layer import PreviewLayer
from ui.core.controller import Controller
from ui.core.qt import QComboBox, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget, Qt
from ui.core.selection_manager import SelectionManager
from ui.core.snap_system import SnapSystem
from ui.core.tool_manager import ToolManager
from ui.projection.selection_projection_coordinator import SelectionProjectionCoordinator
from ui.canvas.sld_canvas_projection import SLDCanvasProjection, SLDCanvasSnapshot, CompositeSLDCanvasSnapshot
from ui.canvas.draft_sld_projection import DraftSLDProjection, DraftSLDCanvasSnapshot
from ui.canvas.sld_canvas_render_system import SLDCanvasRenderSystem
from ui.sld.sld_controller import SLDController
from ui.sld.sld_document import SLDDocument
from ui.sld.sld_route_edit_controller import SLDRouteEditController


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanvasCompositionPreparation:
    """Canvas services that must exist before ToolManager construction."""

    selection_manager: SelectionManager
    grid_system: GridSystem
    scene: GridScene
    snap_system: SnapSystem
    preview_layer: PreviewLayer


class ReadOnlySLDViewport(QWidget):
    """Independent, non-interactive renderer for a secondary SLD document."""

    def __init__(
        self,
        *,
        scene: GridScene,
        projection: SLDCanvasProjection,
        render_system: SLDCanvasRenderSystem,
        document_resolver: Callable[[str], SLDDocument | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(scene, GridScene):
            raise TypeError("scene must be a GridScene.")
        if not isinstance(projection, SLDCanvasProjection):
            raise TypeError("projection must be an SLDCanvasProjection.")
        if not isinstance(render_system, SLDCanvasRenderSystem):
            raise TypeError("render_system must be an SLDCanvasRenderSystem.")
        if render_system.scene is not scene:
            raise ValueError("The read-only renderer must target its own scene.")
        if not callable(document_resolver):
            raise TypeError("document_resolver must be callable.")
        self._scene = scene
        self._projection = projection
        self._render_system = render_system
        self._document_resolver = document_resolver
        self._document_id: str | None = None
        self.setObjectName("SLDReadOnlyComparisonViewport")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)
        header = QHBoxLayout()
        header.addWidget(QLabel("Compare with", self))
        self._document_selector = QComboBox(self)
        self._document_selector.setObjectName("SLDComparisonDocumentSelector")
        self._document_selector.currentIndexChanged.connect(self._select_document)
        header.addWidget(self._document_selector, 1)
        fit_button = QPushButton("Fit", self)
        fit_button.setToolTip("Fit the comparison sheet in its viewport.")
        fit_button.clicked.connect(self.fit_content)
        header.addWidget(fit_button)
        root.addLayout(header)
        self._view = QGraphicsView(self)
        self._view.setObjectName("SLDReadOnlyComparisonView")
        self._view.setScene(scene)
        self._view.setInteractive(False)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        root.addWidget(self._view, 1)
        self.hide()

    @property
    def document_id(self) -> str | None:
        return self._document_id

    def set_available_documents(
        self,
        documents: Iterable[SLDDocument],
        *,
        active_document_id: str | None,
    ) -> None:
        """Offer open non-active SLD documents without taking lifecycle ownership."""
        choices = tuple(
            document for document in documents
            if isinstance(document, SLDDocument)
            and document.document_id != active_document_id
        )
        selected_id = self._document_id
        self._document_selector.blockSignals(True)
        self._document_selector.clear()
        for document in choices:
            self._document_selector.addItem(
                str(getattr(document, "name", "") or "Untitled SLD"),
                document.document_id,
            )
        selected_index = next(
            (
                index for index in range(self._document_selector.count())
                if self._document_selector.itemData(index) == selected_id
            ),
            0,
        )
        if choices:
            self._document_selector.setCurrentIndex(selected_index)
        selected_document_id = self._document_selector.itemData(selected_index) if choices else None
        needs_fit = selected_document_id != selected_id or not self.isVisible()
        self._document_selector.blockSignals(False)
        if not choices:
            self.clear_document()
            self.hide()
            return
        self._select_document(self._document_selector.currentIndex())
        self.show()
        if needs_fit:
            self.fit_content()

    def fit_content(self) -> None:
        """Fit the currently realized comparison document without changing its source."""
        bounds = self._scene.itemsBoundingRect()
        if bounds.isValid() and not bounds.isNull():
            self._view.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def _select_document(self, index: int) -> None:
        if index < 0:
            self.clear_document()
            return
        document_id = self._document_selector.itemData(index)
        if not isinstance(document_id, str) or not document_id:
            self.clear_document()
            return
        document = self._document_resolver(document_id)
        if not isinstance(document, SLDDocument):
            self.clear_document()
            return
        document_changed = document.document_id != self._document_id
        self.present_document(document)
        if document_changed and self.isVisible():
            self.fit_content()

    def present_document(self, document: SLDDocument) -> SLDCanvasSnapshot:
        """Render committed presentation data without tools, selection, or snapping."""
        if not isinstance(document, SLDDocument):
            raise TypeError("document must be an SLDDocument.")
        snapshot = self._projection.project(document.model, document.annotation_blocks)
        self._render_system.synchronize(snapshot)
        self._document_id = document.document_id
        return snapshot

    def clear_document(self) -> None:
        self._render_system.clear()
        self._document_id = None

    def dispose(self) -> None:
        try:
            self._document_selector.currentIndexChanged.disconnect(self._select_document)
        except (RuntimeError, TypeError):
            pass
        self._render_system.dispose()
        self._document_id = None


@dataclass
class SLDCanvasSurface(QWidget):
    """Presentation-only SLD editor surface around the canonical GraphicsView.

    This container adds document chrome and an optional read-only comparison
    viewport while retaining one canonical interactive GraphicsView. The
    comparison surface owns an independent scene and renderer but no tool,
    selection, snapping, electrical, or document-lifecycle authority.
    """

    def __init__(
        self,
        view: GraphicsView,
        navigation_controller: NavigationController,
        *,
        sld_canvas_projection: SLDCanvasProjection,
        sld_canvas_render_system: SLDCanvasRenderSystem,
        comparison_viewport: ReadOnlySLDViewport | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(sld_canvas_projection, SLDCanvasProjection):
            raise TypeError("sld_canvas_projection must be an SLDCanvasProjection.")
        if not isinstance(sld_canvas_render_system, SLDCanvasRenderSystem):
            raise TypeError("sld_canvas_render_system must be an SLDCanvasRenderSystem.")
        if sld_canvas_render_system.scene is not view.scene():
            raise ValueError("sld_canvas_render_system must target the canonical GraphicsView scene.")
        self._sld_canvas_projection = sld_canvas_projection
        self._sld_canvas_render_system = sld_canvas_render_system
        if comparison_viewport is not None and not isinstance(comparison_viewport, ReadOnlySLDViewport):
            raise TypeError("comparison_viewport must be a ReadOnlySLDViewport or None.")
        self._comparison_viewport = comparison_viewport
        self._comparison_splitter: QSplitter | None = None
        self._navigation_controller = navigation_controller
        self._zoom_changed_listener = self._update_zoom_readout
        self._document_id: str | None = None
        self._composition: Any | None = None
        self._sld_canvas_snapshot = SLDCanvasSnapshot(nodes=(), connections=())
        self.setObjectName("SLDCanvasSurface")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("SLDCanvasHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 5, 8, 5)
        header_layout.setSpacing(5)

        title = QLabel("SLD (Main)", header)
        title.setObjectName("SLDDocumentTitle")
        self._title_label = title
        header_layout.addWidget(title)
        header_layout.addStretch(1)


        zoom_out = QPushButton("−", header)
        zoom_out.setObjectName("SLDZoomOut")
        zoom_out.setToolTip("Zoom out")
        self._zoom_out_button = zoom_out
        self._zoom_out_callback = navigation_controller.zoom_out
        zoom_out.clicked.connect(self._zoom_out_callback)
        header_layout.addWidget(zoom_out)

        zoom_label = QLabel("100%", header)
        zoom_label.setObjectName("SLDZoomValue")
        self._zoom_label = zoom_label
        header_layout.addWidget(zoom_label)

        zoom_in = QPushButton("+", header)
        zoom_in.setObjectName("SLDZoomIn")
        zoom_in.setToolTip("Zoom in")
        self._zoom_in_button = zoom_in
        self._zoom_in_callback = navigation_controller.zoom_in
        zoom_in.clicked.connect(self._zoom_in_callback)
        header_layout.addWidget(zoom_in)

        fit = QPushButton("Fit", header)
        fit.setObjectName("SLDFit")
        fit.setToolTip("Fit the SLD content in the viewport")
        # QPushButton.clicked emits a bool; do not pass that signal payload as
        # the numeric fit margin. Retain the exact slot for deterministic teardown.
        self._fit_button = fit
        self._fit_callback = lambda _checked=False: navigation_controller.fit_content()
        fit.clicked.connect(self._fit_callback)
        header_layout.addWidget(fit)

        root.addWidget(header)

        tabs = QTabWidget(self)
        tabs.setObjectName("SLDDocumentTabs")
        tabs.setTabsClosable(False)
        page = QWidget(tabs)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        if comparison_viewport is None:
            page_layout.addWidget(view)
        else:
            split = QSplitter(Qt.Orientation.Horizontal, page)
            split.setChildrenCollapsible(False)
            split.addWidget(view)
            split.addWidget(comparison_viewport)
            split.setStretchFactor(0, 2)
            split.setStretchFactor(1, 1)
            comparison_viewport.hide()
            self._comparison_splitter = split
            page_layout.addWidget(split)
        tabs.addTab(page, "SLD (Main)")
        plus = QPushButton("+", tabs)
        plus.setObjectName("SLDNewDocumentButton")
        plus.setEnabled(False)
        plus.setToolTip("Additional SLD documents are managed by the project/document lifecycle.")
        tabs.setCornerWidget(plus)
        root.addWidget(tabs, 1)

        # The navigation controller is the sole zoom authority. Subscribe once
        # and initialize from its actual state rather than a literal default.
        self._update_zoom_readout(navigation_controller.get_zoom_level())
        navigation_controller.add_zoom_changed_listener(self._zoom_changed_listener)

    def _update_zoom_readout(self, level: float) -> None:
        self._zoom_label.setText(f"{int(round(float(level) * 100.0))}%")

    def detach_navigation_listener(self) -> None:
        """Detach every surface callback that retains navigation before disposal."""
        controller = self._navigation_controller
        self._navigation_controller = None
        if controller is not None:
            try:
                controller.remove_zoom_changed_listener(self._zoom_changed_listener)
            except Exception:
                _LOGGER.exception("Failed to detach SLD surface zoom readout listener")

        # QPushButton connections retain bound methods (and the Fit closure)
        # even after GraphicsView releases its navigation reference. Disconnect
        # the exact slots installed by this surface so a retained editor cannot
        # keep the disposed NavigationController alive.
        for button_name, callback_name in (
            ("_zoom_out_button", "_zoom_out_callback"),
            ("_zoom_in_button", "_zoom_in_callback"),
            ("_fit_button", "_fit_callback"),
        ):
            button = getattr(self, button_name, None)
            callback = getattr(self, callback_name, None)
            setattr(self, callback_name, None)
            setattr(self, button_name, None)
            if button is None or callback is None:
                continue
            try:
                button.clicked.disconnect(callback)
            except Exception:
                # Continue releasing the remaining callbacks if Qt rejects one
                # disconnect because the button/signal is already being torn down.
                _LOGGER.exception("Failed to disconnect SLD surface navigation callback: %s", button_name)

    def bind_composition(self, composition: Any) -> None:
        """Bind the aggregate lifecycle owner after composition is complete."""
        if composition is None:
            raise ValueError("composition must not be None.")
        if self._composition is not None and self._composition is not composition:
            raise RuntimeError("SLDCanvasSurface already has a composition owner.")
        self._composition = composition

    def clear_project_state(self) -> None:
        """Clear project-scoped presentation state while retaining reusable services."""
        composition = self._composition
        if composition is not None:
            composition.clear_project_state()
        else:
            self.clear_document()

    def dispose_canvas(self) -> None:
        """Permanently release the aggregate canvas resources."""
        composition = self._composition
        if composition is not None:
            composition.dispose()
        else:
            self.clear_document()

    def present_document(self, document: SLDDocument) -> SLDCanvasSnapshot:
        """Present the Application-authoritative SLD document on this surface."""
        if not isinstance(document, SLDDocument):
            raise TypeError("document must be an SLDDocument.")
        snapshot = self._sld_canvas_projection.project(document.model, document.annotation_blocks)
        self._sld_canvas_render_system.synchronize(snapshot)
        self._document_id = document.document_id
        self._sld_canvas_snapshot = snapshot
        return snapshot

    def present_composite_document(
        self,
        document: SLDDocument,
        draft_snapshot: DraftSLDCanvasSnapshot,
    ) -> CompositeSLDCanvasSnapshot:
        """Present committed and DraftNetwork state as one complete canvas snapshot."""
        if not isinstance(document, SLDDocument):
            raise TypeError("document must be an SLDDocument.")
        if not isinstance(draft_snapshot, DraftSLDCanvasSnapshot):
            raise TypeError("draft_snapshot must be a DraftSLDCanvasSnapshot.")
        committed = self._sld_canvas_projection.project(document.model, document.annotation_blocks)
        composite = CompositeSLDCanvasSnapshot(committed=committed, draft=draft_snapshot)
        self._sld_canvas_render_system.synchronize(composite)
        self._document_id = document.document_id
        self._sld_canvas_snapshot = committed
        return composite

    def clear_document(self) -> None:
        """Clear canonical SLD graphics without mutating Core/Application state."""
        self._sld_canvas_render_system.clear()
        self.reset_document_state()

    def reset_document_state(self) -> None:
        """Reset surface metadata after the renderer has independently cleared its scene."""
        self._document_id = None
        self._sld_canvas_snapshot = SLDCanvasSnapshot(nodes=(), connections=())
        self.clear_comparison_document()

    def set_comparison_documents(
        self,
        documents: Iterable[SLDDocument],
        *,
        active_document_id: str | None,
    ) -> None:
        if self._comparison_viewport is not None:
            self._comparison_viewport.set_available_documents(
                documents,
                active_document_id=active_document_id,
            )
            if self._comparison_splitter is not None:
                self._comparison_splitter.setSizes([700, 400])

    def clear_comparison_document(self) -> None:
        if self._comparison_viewport is not None:
            self._comparison_viewport.clear_document()
            self._comparison_viewport.hide()

    def dispose_comparison_viewport(self) -> None:
        if self._comparison_viewport is not None:
            self._comparison_viewport.hide()
            self._comparison_viewport.dispose()
            self._comparison_viewport = None

    @property
    def document_id(self) -> str | None:
        return self._document_id

    @property
    def sld_canvas_snapshot(self) -> SLDCanvasSnapshot:
        return self._sld_canvas_snapshot

    def set_document_title(self, title: str | None) -> None:
        """Update only the visible document-tab title from projected application state."""
        value = str(title).strip() if title is not None else ""
        display = f"{value} SLD (Main)" if value else "SLD (Main)"
        self._title_label.setText(display)


@dataclass
class CanvasComposition:
    """Fully composed Canvas viewport and interaction services."""

    view: GraphicsView
    surface: SLDCanvasSurface
    scene: GridScene
    selection_manager: SelectionManager
    grid_system: GridSystem
    interaction_manager: InteractionManager
    navigation_controller: NavigationController
    coordinate_system: CoordinateSystem
    snap_system: SnapSystem
    preview_layer: PreviewLayer
    application: Any
    sld_controller: SLDController
    sld_canvas_projection: SLDCanvasProjection
    sld_canvas_render_system: SLDCanvasRenderSystem
    route_edit_controller: SLDRouteEditController
    selection_projection: SelectionProjectionCoordinator
    interaction_contract: CanvasInteractionContract
    draft_sld_projection: DraftSLDProjection | None = None
    _disposed: bool = False

    def __post_init__(self) -> None:
        self.surface.bind_composition(self)

    @staticmethod
    def _run_cleanup(label: str, operation: Any, failures: list[tuple[str, BaseException]]) -> None:
        try:
            operation()
        except BaseException as exc:
            failures.append((label, exc))
            _LOGGER.exception("Canvas composition cleanup failed: %s", label)

    def _clear_project_state(self, *, clear_sld_graphics: bool) -> None:
        if self._disposed:
            return
        failures: list[tuple[str, BaseException]] = []
        tool_manager = getattr(self.interaction_manager, "tool_manager", None)
        cancel = getattr(tool_manager, "cancel", None)
        if callable(cancel):
            self._run_cleanup("active tool interaction", cancel, failures)
        self._run_cleanup("route edit state", self.route_edit_controller.cancel, failures)
        self._run_cleanup("selection state", self.selection_manager.clear, failures)
        self._run_cleanup("preview graphics", self.preview_layer.clear, failures)
        if clear_sld_graphics:
            self._run_cleanup("SLD graphics and snap registrations", self.surface.clear_document, failures)
            self._run_cleanup("read-only comparison graphics", self.surface.clear_comparison_document, failures)
        if failures:
            _LOGGER.warning("Canvas project-state clear completed with %d cleanup failure(s): %s", len(failures), ", ".join(label for label, _ in failures))

    def clear_project_state(self) -> None:
        """Clear project/document presentation state without disposing shared services."""
        self._clear_project_state(clear_sld_graphics=True)

    def dispose(self) -> None:
        """Idempotently release canvas-owned resources, never shared Application/ToolManager."""
        if self._disposed:
            return
        # Let renderer.dispose() own the one graphics/snap clear on this path;
        # project-state cleanup still cancels interaction and clears selection
        # and preview state without invoking renderer.clear() a second time.
        self._clear_project_state(clear_sld_graphics=False)
        failures: list[tuple[str, BaseException]] = []
        if self.draft_sld_projection is not None:
            self._run_cleanup(
                "draft SLD projection",
                self.draft_sld_projection.dispose,
                failures,
            )
        steps = (
            ("renderer subscriptions and graphics", self.sld_canvas_render_system.dispose),
            ("read-only comparison renderer", self.surface.dispose_comparison_viewport),
            ("selection projection subscription", self.selection_projection.dispose),
            ("interaction manager", self.interaction_manager.dispose),
            ("surface zoom observer", self.surface.detach_navigation_listener),
            ("navigation controller", self.navigation_controller.dispose),
            ("preview layer", self.preview_layer.dispose),
            ("snap system", self.snap_system.dispose),
            ("graphics view references", self.view.dispose),
        )
        for label, operation in steps:
            self._run_cleanup(label, operation, failures)
        self._run_cleanup("surface document metadata", self.surface.reset_document_state, failures)
        self.surface._composition = None
        self._disposed = True
        if failures:
            _LOGGER.warning("Canvas disposal completed with %d cleanup failure(s): %s", len(failures), ", ".join(label for label, _ in failures))

    @property
    def widget(self) -> QWidget:
        return self.surface


class CanvasComposer:
    """Application-level constructor for the Canvas service graph."""

    def prepare(self, *, controller: Controller) -> CanvasCompositionPreparation:
        """Create the shared Canvas interaction dependencies."""
        if controller is None:
            raise ValueError("controller must not be None.")

        selection_manager = SelectionManager()
        grid_system = GridSystem()
        scene = GridScene()
        # The visible grid and all snap/coordinate services must share one
        # geometry authority. A second scene-owned GridSystem lets the drawn
        # grid drift from the grid used for placement and connection snapping.
        scene.set_grid_system(grid_system)
        snap_system = SnapSystem(
            controller=controller,
            grid_system=grid_system,
            scene=scene,
        )
        try:
            preview_layer = PreviewLayer(scene=scene)
        except BaseException:
            # SnapSystem is already acquired even though the preparation
            # aggregate has not yet been returned to the composition root.
            try:
                snap_system.dispose()
            except BaseException:
                _LOGGER.exception("Canvas preparation rollback failed: snap system")
            raise

        return CanvasCompositionPreparation(
            selection_manager=selection_manager,
            grid_system=grid_system,
            scene=scene,
            snap_system=snap_system,
            preview_layer=preview_layer,
        )

    def compose(
        self,
        *,
        controller: Controller,
        tool_manager: ToolManager,
        preparation: CanvasCompositionPreparation,
        sld_controller: SLDController,
        parent: Optional[QWidget] = None,
        workspace_id: str = "sld",
        discipline: str | None = None,
        properties_panel: Any = None,
        sld_canvas_projection: SLDCanvasProjection | None = None,
        sld_canvas_render_system: SLDCanvasRenderSystem | None = None,
        comparison_viewport: ReadOnlySLDViewport | None = None,
    ) -> CanvasComposition:
        """Compose the canvas and unwind every acquired resource on failure."""
        cleanup_stack: list[tuple[str, Any]] = []
        if comparison_viewport is not None:
            if not isinstance(comparison_viewport, ReadOnlySLDViewport):
                raise TypeError("comparison_viewport must be a ReadOnlySLDViewport or None.")
            cleanup_stack.append(("read-only comparison viewport", comparison_viewport.dispose))
        if isinstance(preparation, CanvasCompositionPreparation):
            # Preparation is part of this composition transaction: it was
            # created specifically for this compose call and must not survive
            # a failure before CanvasComposition becomes the lifecycle owner.
            for label, resource in (
                ("prepared snap system", preparation.snap_system),
                ("prepared preview layer", preparation.preview_layer),
            ):
                dispose = getattr(resource, "dispose", None)
                if callable(dispose):
                    cleanup_stack.append((label, dispose))
        try:
            return self._compose(
                controller=controller,
                tool_manager=tool_manager,
                preparation=preparation,
                sld_controller=sld_controller,
                parent=parent,
                workspace_id=workspace_id,
                discipline=discipline,
                properties_panel=properties_panel,
                sld_canvas_projection=sld_canvas_projection,
                sld_canvas_render_system=sld_canvas_render_system,
                comparison_viewport=comparison_viewport,
                _cleanup_stack=cleanup_stack,
            )
        except BaseException:
            for label, operation in reversed(cleanup_stack):
                try:
                    operation()
                except BaseException:
                    _LOGGER.exception("Canvas composition rollback failed: %s", label)
            raise

    def _compose(
        self,
        *,
        controller: Controller,
        tool_manager: ToolManager,
        preparation: CanvasCompositionPreparation,
        sld_controller: SLDController,
        parent: Optional[QWidget] = None,
        workspace_id: str = "sld",
        discipline: str | None = None,
        properties_panel: Any = None,
        sld_canvas_projection: SLDCanvasProjection | None = None,
        sld_canvas_render_system: SLDCanvasRenderSystem | None = None,
        comparison_viewport: ReadOnlySLDViewport | None = None,
        _cleanup_stack: list[tuple[str, Any]],
    ) -> CanvasComposition:
        """Construct one complete Canvas service graph.

        The canonical SLDController is injected by the composition root,
        never discovered indirectly from UI/workspace owners. Its shared
        interaction authorities must match the prepared Canvas dependencies.
        """
        if controller is None:
            raise ValueError("controller must not be None.")
        if tool_manager is None:
            raise ValueError("tool_manager must not be None.")
        if not isinstance(preparation, CanvasCompositionPreparation):
            raise TypeError("preparation must be CanvasCompositionPreparation.")
        if not isinstance(sld_controller, SLDController):
            raise TypeError("sld_controller must be the canonical SLDController.")
        if not isinstance(sld_canvas_projection, SLDCanvasProjection):
            raise TypeError("sld_canvas_projection must be an SLDCanvasProjection.")
        if not isinstance(sld_canvas_render_system, SLDCanvasRenderSystem):
            raise TypeError("sld_canvas_render_system must be an SLDCanvasRenderSystem.")

        application = tool_manager.application
        interaction_contract = CanvasInteractionContract()
        if application is None:
            raise ValueError("tool_manager must retain the canonical Application.")
        if sld_controller.application is not application:
            raise ValueError("SLDController and ToolManager must share the canonical Application.")

        selection_manager = preparation.selection_manager
        grid_system = preparation.grid_system
        scene = preparation.scene
        snap_system = preparation.snap_system
        preview_layer = preparation.preview_layer

        if sld_controller.state.selection_manager is not selection_manager:
            raise ValueError("SLDController must use the prepared canonical SelectionManager.")
        if sld_controller.state.tool_manager is not tool_manager:
            raise ValueError("SLDController must use the canonical ToolManager.")
        if tool_manager.selection_manager is not selection_manager:
            raise ValueError("ToolManager must use the prepared SelectionManager.")
        if tool_manager.snap_system is not snap_system:
            raise ValueError("ToolManager must use the prepared SnapSystem.")
        if tool_manager.preview_layer is not preview_layer:
            raise ValueError("ToolManager must use the prepared PreviewLayer.")
        if sld_canvas_render_system.snap_system is not snap_system:
            raise ValueError("SLDCanvasRenderSystem must use the same canonical SnapSystem as ToolManager and the active Canvas scene.")

        # Route editing is composed exactly once from the canonical controller
        # explicitly supplied by the application composition root. This method
        # must not rediscover or replace that dependency.
        route_edit_controller = SLDRouteEditController(sld_controller)
        _cleanup_stack.append(("route edit controller", route_edit_controller.cancel))
        _cleanup_stack.append(("renderer subscriptions and graphics", sld_canvas_render_system.dispose))
        sld_canvas_render_system.bind_route_edit_controller(route_edit_controller)
        if sld_canvas_render_system.selection_manager is not None and sld_canvas_render_system.selection_manager is not selection_manager:
            raise ValueError("SLDCanvasRenderSystem must use the prepared SelectionManager.")
        sld_canvas_render_system.bind_selection_manager(selection_manager)

        view = GraphicsView(
            controller=controller,
            tool_manager=tool_manager,
            scene=scene,
            parent=parent,
        )
        _cleanup_stack.append(("graphics view references", view.dispose))
        coordinate_system = CoordinateSystem(view=view, grid_system=grid_system)
        input_adapter = MouseEventAdapter(view=view, scene=scene)
        interaction_manager = InteractionManager(
            view=view,
            controller=controller,
            tool_manager=tool_manager,
            coordinate_system=coordinate_system,
            snap_system=snap_system,
            preview_layer=preview_layer,
            selection_manager=selection_manager,
            workspace_id=workspace_id,
            discipline=discipline or workspace_id,
            input_adapter=input_adapter,
        )
        _cleanup_stack.append(("interaction manager", interaction_manager.dispose))
        navigation_controller = NavigationController(view=view)
        _cleanup_stack.append(("navigation controller", navigation_controller.dispose))
        sld_canvas_render_system.bind_navigation_controller(navigation_controller)

        view.bind_services(
            interaction_manager=interaction_manager,
            navigation_controller=navigation_controller,
        )
        draft_sld_projection = DraftSLDProjection(application=application)
        # Until CanvasComposition is returned, the compose rollback stack is
        # the temporary owner of every acquired canvas resource.
        _cleanup_stack.append(("draft SLD projection", draft_sld_projection.dispose))

        surface = SLDCanvasSurface(
            view=view,
            navigation_controller=navigation_controller,
            sld_canvas_projection=sld_canvas_projection,
            sld_canvas_render_system=sld_canvas_render_system,
            comparison_viewport=comparison_viewport,
            parent=parent,
        )
        _cleanup_stack.append(("surface zoom observer", surface.detach_navigation_listener))
        selection_manager.set_scene(scene)

        selection_projection = SelectionProjectionCoordinator(
            selection_manager=selection_manager,
            application=application,
            properties_panel=properties_panel,
        )
        _cleanup_stack.append(("selection projection subscription", selection_projection.dispose))

        return CanvasComposition(
            view=view,
            surface=surface,
            scene=scene,
            selection_manager=selection_manager,
            grid_system=grid_system,
            interaction_manager=interaction_manager,
            navigation_controller=navigation_controller,
            coordinate_system=coordinate_system,
            snap_system=snap_system,
            preview_layer=preview_layer,
            application=application,
            sld_controller=sld_controller,
            sld_canvas_projection=sld_canvas_projection,
            sld_canvas_render_system=sld_canvas_render_system,
            route_edit_controller=route_edit_controller,
            draft_sld_projection=draft_sld_projection,
            selection_projection=selection_projection,
            interaction_contract=interaction_contract,
        )


    def bind_selection_projection(
        self,
        *,
        composition: CanvasComposition,
        properties_panel: Any,
    ) -> SelectionProjectionCoordinator:
        """Bind the real PropertiesPanel after panel plugin composition."""
        if not isinstance(composition, CanvasComposition):
            raise TypeError("composition must be CanvasComposition.")
        if properties_panel is None:
            raise ValueError("properties_panel must not be None.")
        coordinator = composition.selection_projection
        coordinator.set_properties_panel(properties_panel)
        return coordinator


__all__ = [
    "CanvasCompositionPreparation",
    "CanvasComposition",
    "SLDCanvasSurface",
    "ReadOnlySLDViewport",
    "CanvasComposer",
]
