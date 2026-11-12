# ============================================================
# File: ui/events/control_update_coordinator.py
# GridForge V2 — Control Update Coordinator
# Author: Subhendu Mishra
# ============================================================
"""Coordinate Control Application read refreshes into the Control Canvas.

This adapter consumes semantic Control Application events, asks the public
Application facade for the immutable Control read model, and projects that
snapshot into the Control Canvas. It never mutates Core state.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.application.application import Application
from core.application.events import ApplicationEvent, ProjectClosed, ProjectLoaded, InstrumentationWiringChanged
from core.application.control_events import (
    ControlComponentCreated,
    ControlComponentRemoved,
    ControlComponentUpdated,
    ControlConnectionCreated,
    ControlConnectionRemoved,
    ControlDependencyCreated,
    ControlDependencyRemoved,
    ControlExecutionCompleted,
    ControlExecutionFailed,
    ControlExecutionStarted,
    ControlProgramChanged,
    ControlStateChanged,
)


CanvasRefresh = Callable[[], None]

_CONTROL_EVENTS = (
    ControlComponentCreated,
    ControlComponentUpdated,
    ControlComponentRemoved,
    ControlConnectionCreated,
    ControlConnectionRemoved,
    ControlDependencyCreated,
    ControlDependencyRemoved,
    ControlProgramChanged,
    ControlStateChanged,
    ControlExecutionStarted,
    ControlExecutionCompleted,
    ControlExecutionFailed,
    ProjectLoaded,
    ProjectClosed,
    InstrumentationWiringChanged,
)


class ControlUpdateCoordinator:
    """Apply authoritative Application Control changes to the open canvas."""

    event_types = _CONTROL_EVENTS

    def __init__(
        self,
        *,
        application: Application,
        canvas: Any,
        canvas_refresh: CanvasRefresh,
    ) -> None:
        if not isinstance(application, Application):
            raise TypeError("application must be an Application")
        if canvas is None or not callable(getattr(canvas, "project", None)):
            raise TypeError("canvas must expose a project(read_model) callable")
        if not callable(canvas_refresh):
            raise TypeError("canvas_refresh must be callable")

        self._application = application
        self._canvas = canvas
        self._canvas_refresh = canvas_refresh

    def refresh(self, event: ApplicationEvent) -> None:
        """Refresh the Control projection after a relevant Application fact."""
        if not isinstance(event, ApplicationEvent):
            raise TypeError("event must be an ApplicationEvent")
        if not isinstance(event, _CONTROL_EVENTS):
            return

        if isinstance(event, ProjectClosed):
            reset = getattr(self._canvas, "reset_scene", None)
            if callable(reset):
                reset()
            else:
                self._canvas.clear()
            self._canvas_refresh()
            return

        try:
            read_model = self._application.read_control()
        except RuntimeError:
            reset = getattr(self._canvas, "reset_scene", None)
            if callable(reset):
                reset()
            else:
                self._canvas.clear()
            self._canvas_refresh()
            return

        self._canvas.project(read_model)
        self._canvas_refresh()

    def refresh_current(self) -> None:
        """Project the currently active Control read model, or clear when inactive."""
        try:
            self._canvas.project(self._application.read_control())
        except RuntimeError:
            reset = getattr(self._canvas, "reset_scene", None)
            if callable(reset):
                reset()
            else:
                self._canvas.clear()
        self._canvas_refresh()

    def dispose(self) -> None:
        """Release coordinator-owned presentation references."""
        self._canvas_refresh = lambda: None
        self._canvas = None


__all__ = ["ControlUpdateCoordinator", "CanvasRefresh"]
