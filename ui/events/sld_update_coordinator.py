# ============================================================
# File: ui/events/sld_update_coordinator.py
# GridForge V2 — SLD Update Coordinator
# Author: Subhendu Mishra
# ============================================================

"""Coordinate Application semantic events into the active SLD projection."""

from __future__ import annotations

from collections.abc import Callable

from core.application.application import Application
from core.application.events import (
    ApplicationEvent,
    ElementCreated,
    ElementRemoved,
    ElementUpdated,
    NetworkChanged,
    NetworkCommitted,
    ProjectLoaded,
    ProjectClosed,
    ProtectionChanged,
    SLDPresentationChanged,
    TopologyChanged,
    DraftChanged,
)

from ui.sld.sld_read_synchronizer import SLDReadSynchronizer
from ui.sld.sld_document import SLDDocument
from ui.canvas.draft_sld_projection import DraftSLDProjection

CanvasRefresh = Callable[[], None]
ProjectStateClear = Callable[[], None]


class SLDUpdateCoordinator:
    """Project Application read state into domain-scoped SLD projections."""

    event_types = (
        ElementCreated,
        ElementUpdated,
        ElementRemoved,
        TopologyChanged,
        NetworkChanged,
        NetworkCommitted,
        ProtectionChanged,
        SLDPresentationChanged,
        DraftChanged,
        ProjectLoaded,
        ProjectClosed,
    )

    def __init__(
        self,
        *,
        application: Application,
        synchronizer: SLDReadSynchronizer,
        canvas_refresh: CanvasRefresh,
        draft_projection: DraftSLDProjection | None = None,
        project_state_clear: ProjectStateClear | None = None,
    ) -> None:
        if not isinstance(application, Application):
            raise TypeError("application must be an Application")
        if not isinstance(synchronizer, SLDReadSynchronizer):
            raise TypeError("synchronizer must be an SLDReadSynchronizer")
        if not callable(canvas_refresh):
            raise TypeError("canvas_refresh must be callable")
        if synchronizer.application is not None and synchronizer.application is not application:
            raise RuntimeError("SLDReadSynchronizer is already attached to another Application")
        if synchronizer.application is None:
            synchronizer.attach_application(application)
        self._application = application
        self._synchronizer = synchronizer
        # Reuse the synchronizer-owned projection manager. The coordinator
        # must never construct or retain a second projection authority.
        self._projection_manager = synchronizer.projection_manager
        self._canvas_refresh = canvas_refresh
        if project_state_clear is not None and not callable(project_state_clear):
            raise TypeError("project_state_clear must be callable or None")
        self._project_state_clear = project_state_clear
        if draft_projection is not None and not isinstance(draft_projection, DraftSLDProjection):
            raise TypeError("draft_projection must be a DraftSLDProjection or None")
        self._draft_projection = draft_projection
        self._document: SLDDocument | None = None
        self._disposed = False
        self._last_reconciliation_error: Exception | None = None

    @property
    def document(self) -> SLDDocument | None:
        """Return the currently bound presentation document."""
        if self._disposed:
            return None
        presentation = self._application.presentation
        if isinstance(presentation, SLDDocument):
            self._document = presentation
        return self._document

    def bind_document(self, document: SLDDocument) -> None:
        """Explicitly bind a newly active SLD document."""
        if self._disposed:
            raise RuntimeError("SLDUpdateCoordinator has been disposed")
        if not isinstance(document, SLDDocument):
            raise TypeError("document must be an SLDDocument")
        self._document = document

    def detach_document(self) -> None:
        """Drop the cached presentation reference during project close."""
        self._document = None


    @property
    def last_reconciliation_error(self) -> Exception | None:
        """Return the most recent reconciliation failure, if any."""
        return self._last_reconciliation_error

    def _refresh_canvas(self) -> None:
        """Refresh the canvas while keeping failures visible to reconciliation diagnostics."""
        try:
            self._canvas_refresh()
        except Exception as exc:
            self._last_reconciliation_error = exc
            raise

    def reconcile_current_state(self) -> None:
        """Reconcile projections from current Application state without replaying events.

        This is the deterministic bootstrap path for projections that become ready
        after ProjectLoaded has already been emitted.
        """
        if self._disposed:
            return
        presentation = self._application.presentation
        if not isinstance(presentation, SLDDocument):
            self.detach_document()
            return
        self.bind_document(presentation)
        self._synchronizer.synchronize_network(presentation, self._application.read_network())
        self._synchronizer.synchronize_protection(presentation, self._application.read_protection())
        self._last_reconciliation_error = None
        self._refresh_canvas()

    def refresh(self, event: ApplicationEvent) -> None:
        """Refresh read-only projections after an authoritative Application fact."""
        if self._disposed:
            return
        if not isinstance(event, ApplicationEvent):
            raise TypeError("event must be an ApplicationEvent")

        if isinstance(event, DraftChanged):
            try:
                if self._draft_projection is not None:
                    self._draft_projection.refresh(event)
                self._refresh_canvas()
            except Exception as exc:
                # Keep draft-triggered rendering failures observable through
                # the same reconciliation status exposed for committed SLD
                # refreshes; the event router may log and isolate this error.
                self._last_reconciliation_error = exc
                raise
            self._last_reconciliation_error = None
            return

        if isinstance(event, ProjectClosed):
            # ProjectClosed is the authoritative successful transition boundary.
            # Clear canvas-owned transient state before refreshing the empty
            # Application presentation; the later workspace notification must
            # not perform this project-boundary cleanup a second time.
            if self._project_state_clear is not None:
                self._project_state_clear()
            self._projection_manager.clear()
            self.detach_document()
            self._refresh_canvas()
            return

        if isinstance(event, ProjectLoaded):
            # ProjectLoaded is published only after the Application activation
            # transaction commits. Clear the prior project's canvas resources
            # here, before reconciling or presenting the newly active document.
            # Failed activations publish no ProjectLoaded event and therefore
            # retain the previous canvas for rollback.
            if self._project_state_clear is not None:
                self._project_state_clear()
            presentation = self._application.presentation
            if isinstance(presentation, SLDDocument):
                self.bind_document(presentation)

        document = self.document
        if document is None:
            return

        try:
            if isinstance(event, ProtectionChanged):
                self._synchronizer.synchronize_protection(
                    document,
                    self._application.read_protection(),
                )
            elif isinstance(event, SLDPresentationChanged):
                # The SLD service already owns committed presentation mutation.
                # Draft→Core commit also clears/restores Application draft state;
                # reconcile that projection at the same presentation boundary so
                # the canvas never combines committed state with a stale draft
                # snapshot (including commit undo/redo).
                if (
                    self._draft_projection is not None
                    and event.payload.get("presentation_operation") == "network_commit_draft"
                ):
                    self._draft_projection.project()
                self._refresh_canvas()
                return
            elif isinstance(event, (ElementCreated, ElementUpdated, ElementRemoved)):
                element_type = str(event.payload.get("element_type", "")).strip().upper()
                element_id = event.payload.get("element_id")
                if not isinstance(element_id, str) or not element_id:
                    raise ValueError("Element lifecycle event must contain a canonical element_id.")

                if element_type == "RELAY":
                    self._synchronizer.synchronize_protection(
                        document,
                        self._application.read_protection(),
                    )
                else:
                    self._synchronizer.synchronize_network(
                        document,
                        self._application.read_network(),
                    )
            elif isinstance(event, (TopologyChanged, NetworkChanged, NetworkCommitted)):
                self._synchronizer.synchronize_network(
                    document,
                    self._application.read_network(),
                )
            elif isinstance(event, ProjectLoaded):
                # Project activation crosses both Application read domains.
                # Reconcile each authoritative read model directly; do not
                # synthesize another event bus or rebuild network state from
                # protection changes.
                self._synchronizer.synchronize_network(
                    document,
                    self._application.read_network(),
                )
                self._synchronizer.synchronize_protection(
                    document,
                    self._application.read_protection(),
                )
            else:
                return
        except Exception as exc:
            self._last_reconciliation_error = exc
            raise
        self._last_reconciliation_error = None
        self._refresh_canvas()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._canvas_refresh = lambda: None
        self._last_reconciliation_error = None
        self._disposed = True


__all__ = ["SLDUpdateCoordinator", "CanvasRefresh"]
