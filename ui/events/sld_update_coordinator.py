# ============================================================
# File: ui/events/sld_update_coordinator.py
# GridForge V2 — SLD Update Coordinator
# Author: Subhendu Mishra
# ============================================================

"""Coordinate Application semantic events into the active SLD projection."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import json
from typing import Any

from core.application.application import Application
from core.application.read_models import NetworkReadModel
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
    InstrumentationWiringChanged,
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
        InstrumentationWiringChanged,
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
        documents_provider: Callable[[], Iterable[SLDDocument]] | None = None,
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
        if documents_provider is not None and not callable(documents_provider):
            raise TypeError("documents_provider must be callable or None")
        self._documents_provider = documents_provider
        self._background_fingerprints: dict[str, str] = {}
        self._background_diagnostics: dict[str, tuple[dict[str, object], ...]] = {}
        self._background_network_revision: object | None = None
        self._background_project_id: object | None = None
        self._background_network_fingerprint: str | None = None
        self._background_reconciliation_error: Exception | None = None
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

    @property
    def background_reconciliation_diagnostics(self) -> dict[str, tuple[dict[str, object], ...]]:
        """Return read-only audit results keyed by persistent SLD document ID."""
        return dict(self._background_diagnostics)

    @property
    def background_reconciliation_error(self) -> Exception | None:
        """Return a failure from enumerating/auditing background SLD documents."""
        return self._background_reconciliation_error

    @staticmethod
    def _document_topology_fingerprint(document: SLDDocument, network_fingerprint: str) -> str:
        model = document.model
        nodes = sorted(
            (str(node.node_id), str(node.equipment_id or ""))
            for node in model.nodes
        )
        connections = []
        for connection in model.connections:
            def endpoint_identity(endpoint: Any) -> tuple[str, str, str] | None:
                if endpoint is None:
                    return None
                kind_value = getattr(
                    getattr(endpoint, "kind", None),
                    "value",
                    getattr(endpoint, "kind", None),
                )
                kind = str(kind_value or "").strip().lower()
                if kind == "equipment":
                    return (
                        "terminal",
                        str(getattr(endpoint, "equipment_id", "") or ""),
                        str(getattr(endpoint, "terminal_role", "") or ""),
                    )
                if kind == "bus":
                    return (
                        "bus",
                        str(getattr(endpoint, "bus_id", "") or ""),
                        str(getattr(endpoint, "attachment_id", "") or ""),
                    )
                return None

            endpoints = [
                endpoint_identity(getattr(connection, "source_endpoint", None)),
                endpoint_identity(getattr(connection, "target_endpoint", None)),
            ]
            endpoints.sort(key=repr)
            properties = getattr(connection, "properties", {})
            connections.append((
                str(connection.connection_id),
                str(properties.get("core_connection_id", "") or ""),
                str(properties.get("connection_kind", "") or ""),
                str(connection.source_node_id),
                str(connection.target_node_id),
                endpoints,
            ))
        payload = {
            "network": network_fingerprint,
            "nodes": nodes,
            "connections": sorted(connections, key=repr),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def reconcile_background_documents(self) -> dict[str, tuple[dict[str, object], ...]]:
        """Audit every SLD document when its own or Core topology fingerprint changes.

        This is read-only: it does not switch Application presentation or mutate
        a background document. The explicit presentation argument ensures that
        each audit compares the correct document's companions to one Core snapshot.
        """
        if self._disposed or self._documents_provider is None:
            return dict(self._background_diagnostics)
        try:
            documents = tuple(self._documents_provider())
            lifecycle_context = self._application.project_lifecycle.context
            project_id = getattr(lifecycle_context, "project_id", None)
            revision = self._application.revision
            read_model: NetworkReadModel | None = None
            network_fingerprint = self._background_network_fingerprint
            if (
                network_fingerprint is None
                or revision != self._background_network_revision
                or project_id != self._background_project_id
            ):
                read_model = self._application.read_network()
                if not isinstance(read_model, NetworkReadModel):
                    raise TypeError("Application read_network() must return NetworkReadModel")
                network_fingerprint = read_model.structural_fingerprint
                self._background_network_revision = revision
                self._background_project_id = project_id
                self._background_network_fingerprint = network_fingerprint
        except Exception as exc:
            self._background_reconciliation_error = exc
            return dict(self._background_diagnostics)

        live_document_ids = {
            document.document_id
            for document in documents
            if isinstance(document, SLDDocument)
        }
        for document_id in tuple(self._background_fingerprints):
            if document_id not in live_document_ids:
                self._background_fingerprints.pop(document_id, None)
                self._background_diagnostics.pop(document_id, None)

        for document in documents:
            if not isinstance(document, SLDDocument):
                continue
            document_id = document.document_id
            fingerprint = self._document_topology_fingerprint(document, network_fingerprint)
            if self._background_fingerprints.get(document_id) == fingerprint:
                continue
            try:
                if read_model is None:
                    read_model = self._application.read_network()
                    if not isinstance(read_model, NetworkReadModel):
                        raise TypeError("Application read_network() must return NetworkReadModel")
                    network_fingerprint = read_model.structural_fingerprint
                    self._background_network_revision = self._application.revision
                    self._background_project_id = getattr(
                        self._application.project_lifecycle.context,
                        "project_id",
                        None,
                    )
                    self._background_network_fingerprint = network_fingerprint
                    fingerprint = self._document_topology_fingerprint(document, network_fingerprint)
                diagnostics = self._synchronizer.reconcile_simple_wires(
                    read_model,
                    presentation=document,
                )
            except Exception as exc:
                self._background_diagnostics[document_id] = ({
                    "code": "BACKGROUND_RECONCILIATION_FAILED",
                    "message": str(exc),
                },)
                continue
            self._background_fingerprints[document_id] = fingerprint
            self._background_diagnostics[document_id] = diagnostics

        self._background_reconciliation_error = None
        return dict(self._background_diagnostics)


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
                    and event.payload.get("presentation_operation") in {
                        "network_commit_draft",
                        "selection_delete_batch",
                    }
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
                if event.payload.get("presentation_operation") in {
                    "network_commit_draft",
                    "selection_delete_batch",
                }:
                    # Aggregate Core/SLD mutations publish a paired network
                    # event and SLD presentation event. Reconcile the Core
                    # projection now, but defer canvas refresh until the latter
                    # has also synchronized the document-scoped draft projection.
                    self._last_reconciliation_error = None
                    return
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
        self._documents_provider = None
        self._background_fingerprints.clear()
        self._background_diagnostics.clear()
        self._background_network_revision = None
        self._background_project_id = None
        self._background_network_fingerprint = None
        self._background_reconciliation_error = None
        self._last_reconciliation_error = None
        self._disposed = True


__all__ = ["SLDUpdateCoordinator", "CanvasRefresh"]
