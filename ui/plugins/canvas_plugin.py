"""
GridForge V2
============

File:
    ui/plugins/canvas_plugin.py

Purpose
-------
Canvas plugin lifecycle boundary for an application-composed SLD canvas.

Architectural role
------------------
CanvasPlugin consumes an application-owned CanvasComposition and injected SLD
presentation services. It does not construct Canvas services, own the Canvas
scene, or dispose shared Canvas services. SLD projection and graphics
realization remain presentation-only.

Author:
    Subhendu Mishra
"""

from __future__ import annotations

from typing import Optional

from ui.core.qt import QGraphicsScene, QWidget

from ui.canvas.canvas_composition import CanvasComposition
from ui.canvas.graphics_view import GraphicsView
from ui.canvas.sld_canvas_projection import SLDCanvasProjection, SLDCanvasSnapshot, CompositeSLDCanvasSnapshot
from ui.canvas.sld_canvas_render_system import RenderDiagnostic, SLDCanvasRenderSystem
from ui.plugins.plugin_context import PluginContext
from ui.sld.sld_document import SLDDocument


class CanvasPlugin:
    """GridForge canvas plugin consuming an application-owned composition."""

    plugin_id = "canvas"

    def __init__(self) -> None:
        """Construct the plugin without runtime dependencies.

        Dependencies are supplied only through ``initialize(context)`` as
        required by the PluginLoader contract.
        """
        self._context: Optional[PluginContext] = None
        self._composition: Optional[CanvasComposition] = None
        self._initialized = False
        self._sld_canvas_snapshot: Optional[SLDCanvasSnapshot | CompositeSLDCanvasSnapshot] = None
        self._workspace_surface: Optional[QWidget] = None
        self._sld_canvas_render_system: Optional[SLDCanvasRenderSystem] = None

    @property
    def context(self) -> Optional[PluginContext]:
        return self._context

    def set_context(self, context: PluginContext) -> None:
        if not isinstance(context, PluginContext):
            raise TypeError("context must be PluginContext.")
        if self._initialized:
            raise RuntimeError("CanvasPlugin context cannot be changed after initialization.")
        self._context = context

    def set_workspace_surface(self, surface: QWidget) -> None:
        """Bind an existing presentation surface host for workspace switching."""
        if not isinstance(surface, QWidget):
            raise TypeError("surface must be QWidget.")
        if self._initialized:
            raise RuntimeError("Workspace surface cannot change after CanvasPlugin initialization.")
        self._workspace_surface = surface

    @property
    def composition(self) -> Optional[CanvasComposition]:
        return self._composition

    def set_composition(self, composition: CanvasComposition) -> None:
        if not isinstance(composition, CanvasComposition):
            raise TypeError("composition must be CanvasComposition.")
        if self._initialized:
            raise RuntimeError("CanvasPlugin composition cannot be changed after initialization.")
        if self._composition is not None and self._composition is not composition:
            raise RuntimeError("CanvasPlugin already has a CanvasComposition.")
        self._composition = composition

    def initialize(self, context: Optional[PluginContext] = None) -> bool:
        if self._initialized:
            return True
        if context is not None:
            self.set_context(context)
        self._validate_context()
        previous_context = self._context
        previous_render_system = self._sld_canvas_render_system
        previous_snapshot = self._sld_canvas_snapshot
        try:
            if self._composition is None:
                raise RuntimeError("CanvasPlugin requires an application-composed CanvasComposition.")

            composed_render_system = getattr(self._composition, "sld_canvas_render_system", None)
            context_render_system = self._context.sld_canvas_render_system
            if composed_render_system is None:
                raise RuntimeError("CanvasComposition must own the canonical SLDCanvasRenderSystem.")
            if composed_render_system is not context_render_system:
                raise RuntimeError("PluginContext render system must be the CanvasComposition render system.")
            self._sld_canvas_render_system = composed_render_system
            if not isinstance(self._sld_canvas_render_system, SLDCanvasRenderSystem):
                raise TypeError("CanvasComposition.sld_canvas_render_system must be an SLDCanvasRenderSystem.")
            if self._sld_canvas_render_system.scene is not self.require_scene():
                raise RuntimeError("SLD canvas render system must target the CanvasComposition scene.")

            self.synchronize_sld()
            self._initialized = True
            return True
        except BaseException as exc:
            compensation_error = None
            try:
                if self._sld_canvas_render_system is not None:
                    self._sld_canvas_render_system.clear()
                    if previous_snapshot is not None:
                        self._sld_canvas_render_system.synchronize(previous_snapshot)
            except BaseException as cleanup_exc:
                compensation_error = cleanup_exc
            self._sld_canvas_render_system = previous_render_system
            self._sld_canvas_snapshot = previous_snapshot
            self._context = previous_context
            self._initialized = False
            if compensation_error is not None:
                raise ExceptionGroup(
                    "CanvasPlugin initialization and presentation compensation failed.",
                    [exc, compensation_error],
                ) from exc
            raise

    def _validate_context(self) -> None:
        if self._context is None:
            raise RuntimeError("CanvasPlugin context is unavailable.")
        self._context.validate(required=(
            "controller",
            "application",
            "tool_manager",
            "sld_canvas_projection",
            "sld_canvas_render_system",
        ))

    def _active_sld_document(self) -> SLDDocument:
        application = self._context.application
        document = getattr(application, "presentation", None)
        if isinstance(document, SLDDocument):
            return document
        raise RuntimeError(
            "CanvasPlugin has no Application-authoritative active SLD document."
        )

    def synchronize_sld(self) -> SLDCanvasSnapshot | CompositeSLDCanvasSnapshot:
        if self._context is None:
            raise RuntimeError("CanvasPlugin context is unavailable.")
        projection = getattr(self._composition, "sld_canvas_projection", None)
        render_system = getattr(self._composition, "sld_canvas_render_system", None)
        if projection is None or render_system is None:
            raise RuntimeError("CanvasComposition SLD projection/render dependencies are unavailable.")
        if projection is not self._context.sld_canvas_projection or render_system is not self._context.sld_canvas_render_system:
            raise RuntimeError("PluginContext SLD dependencies do not match CanvasComposition.")
        if projection is None or render_system is None:
            raise RuntimeError("SLD canvas projection dependencies are unavailable.")
        surface = getattr(self._composition, "surface", None)
        if surface is None:
            raise RuntimeError("CanvasComposition must expose the canonical SLDCanvasSurface.")
        application_document = getattr(self._context.application, "presentation", None)
        draft_projection = getattr(self._composition, "draft_sld_projection", None)
        if draft_projection is None:
            raise RuntimeError("CanvasComposition must own the canonical DraftSLDProjection.")
        draft_snapshot = draft_projection.project()

        if application_document is None:
            # The committed ProjectClosed boundary already clears the aggregate's
            # project-scoped interaction/selection/preview/renderer state. Keep
            # this read-side reconciliation safe when called independently by
            # resetting only surface metadata; synchronize(empty) removes any
            # remaining realized items without issuing a second renderer.clear().
            reset_document_state = getattr(surface, "reset_document_state", None)
            if not callable(reset_document_state):
                raise RuntimeError("Canonical SLDCanvasSurface cannot reset document metadata.")
            reset_document_state()
            snapshot = CompositeSLDCanvasSnapshot(
                committed=SLDCanvasSnapshot(nodes=(), connections=()),
                draft=draft_snapshot,
            )
            render_system.synchronize(snapshot)
        else:
            document = self._active_sld_document()
            present = getattr(surface, "present_composite_document", None)
            if not callable(present):
                raise RuntimeError("Canonical SLDCanvasSurface cannot present a composite SLD document.")
            snapshot = present(document, draft_snapshot)
        self._sld_canvas_snapshot = snapshot
        return snapshot

    def refresh_canvas(self) -> SLDCanvasSnapshot | CompositeSLDCanvasSnapshot:
        """Rebuild the visible SLD realization from authoritative Application state.

        This is intentionally a reconciliation operation, not a scene cache
        refresh. Repeated calls are idempotent because the complete composite
        snapshot is projected anew from SLDDocument + DraftNetwork.
        """
        return self.synchronize_sld()

    @property
    def sld_canvas_snapshot(self) -> Optional[SLDCanvasSnapshot | CompositeSLDCanvasSnapshot]:
        return self._sld_canvas_snapshot

    @property
    def sld_canvas_render_system(self) -> Optional[SLDCanvasRenderSystem]:
        return self._sld_canvas_render_system

    @property
    def render_diagnostics(self) -> tuple[RenderDiagnostic, ...]:
        """Expose the latest presentation diagnostics to application/UI consumers."""
        if self._sld_canvas_render_system is None:
            return ()
        return self._sld_canvas_render_system.render_diagnostics

    @property
    def widget(self) -> Optional[QWidget]:
        if self._workspace_surface is not None:
            return self._workspace_surface
        return self._composition.widget if self._composition is not None else None

    def require_view(self) -> GraphicsView:
        if self._composition is None:
            raise RuntimeError("CanvasPlugin has no CanvasComposition.")
        if not isinstance(self._composition.view, GraphicsView):
            raise TypeError("CanvasComposition view must be a GraphicsView.")
        return self._composition.view

    def require_scene(self) -> QGraphicsScene:
        scene = self._composition.scene if self._composition is not None else None
        if scene is None:
            raise RuntimeError("CanvasPlugin has no CanvasComposition scene.")
        return scene

    @property
    def initialized(self) -> bool:
        return self._initialized

    def shutdown(self) -> None:
        self._sld_canvas_render_system = None
        self._sld_canvas_snapshot = None
        self._initialized = False


def create_canvas_plugin() -> CanvasPlugin:
    """Construct a context-free CanvasPlugin."""
    return CanvasPlugin()


__all__ = ["CanvasPlugin", "create_canvas_plugin"]
