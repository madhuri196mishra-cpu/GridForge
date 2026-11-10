# ============================================================
# File: ui/workspace/project_workspace_adapter.py
# GridForge V2 — Project Workspace Application Adapter
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging

from core.application import Application
from core.application.project import ProjectContext
from core.application.project_transition import ProjectTransitionDecision, ProjectTransitionRequired
from core.application.project_presentation import ProjectPresentationCollection
from ui.sld.sld_document import SLDDocument
from .document import Document
from .project import Project
from .project_workspace import ProjectWorkspaceLifecycle, ProjectWorkspaceState
from .view_manager import ViewRecord
from .viewport_preference_store import ViewportPreferenceStore


@dataclass(frozen=True, slots=True)
class ProjectWorkspaceChanged:
    """Immutable UI update emitted after a successful engineer-visible transition."""

    operation: str
    state: ProjectWorkspaceState
    project_id: str


WorkspaceUpdateHandler = Callable[[ProjectWorkspaceChanged], None]
_logger = logging.getLogger(__name__)
PresentationActivationBridge = Callable[[Document | None], None]


class ProjectWorkspaceApplicationAdapter:
    """Bridge Application lifecycle success into the coordinated UI workspace transaction."""

    def __init__(
        self,
        application: Application,
        lifecycle: ProjectWorkspaceLifecycle,
        *,
        default_symbol_presentation_factory: Callable[[str], Mapping[str, object]] | None = None,
        viewport_preference_store: ViewportPreferenceStore | None = None,
    ) -> None:
        if not isinstance(application, Application): raise TypeError("application must be an Application.")
        if not isinstance(lifecycle, ProjectWorkspaceLifecycle): raise TypeError("lifecycle must be a ProjectWorkspaceLifecycle.")
        if default_symbol_presentation_factory is not None and not callable(default_symbol_presentation_factory):
            raise TypeError("default_symbol_presentation_factory must be callable or None.")
        self._application = application
        self._lifecycle = lifecycle
        self._default_symbol_presentation_factory = default_symbol_presentation_factory
        if viewport_preference_store is not None and not isinstance(viewport_preference_store, ViewportPreferenceStore):
            raise TypeError("viewport_preference_store must be a ViewportPreferenceStore or None.")
        self._viewport_preference_store = viewport_preference_store
        self._handlers: list[WorkspaceUpdateHandler] = []
        self._presentation_activation_bridge: PresentationActivationBridge | None = None
        self._document_transition_guard: Callable[[], None] | None = None
        self._project_transition_guard: Callable[[], Callable[[], None] | None] | None = None
        self._save_as_path_provider: Callable[[object], str | None] | None = None
        self._transition_decision_provider: Callable[[object], ProjectTransitionDecision | str] | None = None
        self._pending_presentation_collection: ProjectPresentationCollection | None = None
        self._application.configure_workspace_dirty_provider(
            lambda: bool(self._lifecycle.documents.dirty_document_ids)
        )

    @property
    def application(self) -> Application: return self._application
    @property
    def lifecycle(self) -> ProjectWorkspaceLifecycle: return self._lifecycle
    @property
    def project_lifecycle(self): return self._application.project_lifecycle
    @property
    def is_dirty(self) -> bool:
        return bool(self._application.is_dirty or self._lifecycle.documents.dirty_document_ids)

    @property
    def active_document_id(self) -> str | None:
        return self._lifecycle.documents.active_document_id

    @property
    def documents(self):
        return self._lifecycle.documents
    @property
    def state(self) -> ProjectWorkspaceState: return self._lifecycle.state

    def configure_presentation_activation_bridge(self, bridge: PresentationActivationBridge) -> None:
        """Bind the UI-level presentation bridge used during project activation."""
        if not callable(bridge):
            raise TypeError("bridge must be callable.")
        self._presentation_activation_bridge = bridge

    def configure_recovery_activation(self) -> None:
        """Install the complete presentation/workspace contract for staged recovery.

        This configures the existing canonical deserializers, collection registry,
        snapshot provider, and SLD/workspace activation bridge without opening a
        project or publishing a workspace transition.
        """
        self._configure_presentation_transaction(
            open_existing=True,
            activate_workspace=False,
        )

    def publish_recovered_project(self, context: ProjectContext) -> ProjectWorkspaceState:
        """Publish an already-committed Application recovery into UI projections.

        Application.open_project() activates the configured lifecycle callbacks,
        but does not pass through this adapter's _run_transition() publication
        boundary. Recovery calls this method only after that activation succeeds.
        """
        if not isinstance(context, ProjectContext):
            raise TypeError("context must be a ProjectContext.")
        active_context = self._application.project_lifecycle.context
        if active_context is None or active_context.project_id != context.project_id:
            raise RuntimeError("Recovered project context is not Application-active.")
        state = self._lifecycle.state
        if state.project is None or state.project.project_id != context.project_id:
            raise RuntimeError("Recovered project is not adopted by the workspace lifecycle.")
        if state.document is not self._application.presentation:
            raise RuntimeError("Recovered workspace document is not the Application-active presentation.")
        self._publish("open", state, context.project_id)
        return state

    def configure_document_transition_guard(self, guard: Callable[[], None]) -> None:
        if not callable(guard):
            raise TypeError("guard must be callable.")
        self._document_transition_guard = guard

    def configure_project_transition_guard(
        self,
        guard: Callable[[], Callable[[], None] | None],
    ) -> None:
        """Bind interaction cleanup and optional recovery for failed transitions."""
        if not callable(guard):
            raise TypeError("guard must be callable.")
        self._project_transition_guard = guard

    def _guard_project_transition(self) -> Callable[[], None] | None:
        guard = self._project_transition_guard
        if guard is None:
            return None
        recovery = guard()
        if recovery is not None and not callable(recovery):
            raise TypeError("project transition guard must return a callable recovery or None.")
        return recovery

    def _restore_interaction_after_failure(
        self,
        recovery: Callable[[], None] | None,
        *,
        operation: str,
    ) -> None:
        """Restore safe interaction, then reconcile projections from restored Application state."""
        if recovery is not None:
            try:
                recovery()
            except BaseException:
                _logger.exception(
                    "Interaction recovery failed after workspace transition rollback: operation=%s",
                    operation,
                )

        state = self._lifecycle.state
        if self._application.presentation is not state.document:
            _logger.error(
                "Skipping rollback projection reconciliation because Application and workspace "
                "presentations disagree: operation=%s application_document=%r workspace_document=%r",
                operation,
                getattr(self._application.presentation, "document_id", None),
                getattr(state.document, "document_id", None),
            )
            return

        project_id = state.project.project_id if state.project is not None else ""
        self._publish(f"rollback_{operation}", state, project_id)

    def configure_save_as_path_provider(self, provider: Callable[[object], str | None]) -> None:
        """Bind the existing UI Save As path-selection workflow for unsaved projects."""
        if not callable(provider):
            raise TypeError("provider must be callable.")
        self._save_as_path_provider = provider

    def configure_transition_decision_provider(
        self,
        provider: Callable[[object], ProjectTransitionDecision | str],
    ) -> None:
        """Bind the shared SAVE/DISCARD/CANCEL provider for project transitions."""
        if not callable(provider):
            raise TypeError("provider must be callable.")
        self._transition_decision_provider = provider

    def _resolve_transition_decision(
        self,
        decision: ProjectTransitionDecision | str | None,
    ) -> ProjectTransitionDecision | str | None:
        if not self.is_dirty or decision is not None:
            return decision
        if self._transition_decision_provider is None:
            raise ProjectTransitionRequired(
                "The project or workspace documents are dirty; a SAVE, DISCARD, or CANCEL decision is required."
            )
        return self._transition_decision_provider(self._application.project_lifecycle.context)

    def _prepare_workspace_transition(
        self,
        decision: ProjectTransitionDecision | str | None,
    ) -> ProjectTransitionDecision | str | None:
        resolved = self._resolve_transition_decision(decision)
        if not self.is_dirty or self._is_cancel(resolved):
            return resolved
        normalized = (
            resolved if isinstance(resolved, ProjectTransitionDecision)
            else ProjectTransitionDecision(str(resolved).strip().lower())
        )
        if normalized is ProjectTransitionDecision.SAVE:
            context = self._application.project_lifecycle.context
            if context is None:
                raise RuntimeError("Cannot save a project transition without an active project.")
            if context.path is None:
                if self._save_as_path_provider is None:
                    raise RuntimeError("Save As path selection is not configured for this project transition.")
                path = self._save_as_path_provider(context)
                if not path:
                    return ProjectTransitionDecision.CANCEL
                self._application.save_project_as(path)
            else:
                self._application.save_project()
        return normalized

    def subscribe(self, handler: WorkspaceUpdateHandler) -> None:
        if not callable(handler): raise TypeError("handler must be callable.")
        if handler not in self._handlers: self._handlers.append(handler)

    def unsubscribe(self, handler: WorkspaceUpdateHandler) -> None:
        try: self._handlers.remove(handler)
        except ValueError: return

    def _configure_presentation_transaction(
        self,
        document: Document | None = None,
        *,
        open_existing: bool,
        activate_workspace: bool,
    ) -> None:
        def factory(context: ProjectContext) -> Document:
            if document is not None:
                if document.project_id not in (None, context.project_id):
                    raise ValueError("presentation document belongs to a different project.")
                return document
            return SLDDocument(
                document_id=f"{context.project_id}:sld",
                name=f"{context.name} SLD",
                project_id=context.project_id,
                default_symbol_presentation_factory=self._default_symbol_presentation_factory,
            )

        def serializer(value: Document):
            to_dict = getattr(value, "to_dict", None)
            if not callable(to_dict):
                raise TypeError("presentation document must provide to_dict().")
            return to_dict()

        def collection_serializer(collection: ProjectPresentationCollection):
            documents = []
            for item in collection.documents:
                to_dict = getattr(item, "to_dict", None)
                if not callable(to_dict):
                    raise TypeError("project document must provide to_dict().")
                documents.append(to_dict())
            return {
                "schema": 1,
                "documents": documents,
                "active_document_id": collection.active_document_id,
            }

        def deserialize_document(data):
            return SLDDocument.from_dict(
                data,
                default_symbol_presentation_factory=self._default_symbol_presentation_factory,
            )

        def collection_deserializer(data):
            if not isinstance(data, dict):
                raise TypeError("presentation collection payload must be a mapping.")
            documents = []
            for item in data.get("documents", ()):
                documents.append(deserialize_document(item))
            return ProjectPresentationCollection.ordered(
                documents,
                data.get("active_document_id"),
            )

        def collection_activator(context, collection):
            previous = self._pending_presentation_collection
            self._pending_presentation_collection = collection
            return lambda: setattr(self, "_pending_presentation_collection", previous)

        def activate(context: ProjectContext | None, presentation: object | None):
            return self._activate_workspace_presentation(
                context,
                presentation,
                open_existing=open_existing,
                activate_workspace=activate_workspace,
            )

        self._application.configure_project_presentation_contract(
            factory=factory,
            serializer=serializer,
            deserializer=deserialize_document,
        )
        self._application.project_lifecycle.configure_presentation_collection_contract(
            serializer=collection_serializer,
            deserializer=collection_deserializer,
            activator=collection_activator,
        )
        self._application.project_lifecycle.configure_presentation_collection_snapshot_provider(
            self.snapshot_presentation_collection
        )
        self._application.configure_presentation_activator(activate)

    def snapshot_presentation_collection(self) -> ProjectPresentationCollection:
        """Return an immutable snapshot of the complete live DocumentManager collection."""
        documents = tuple(self._lifecycle.documents.documents())
        return ProjectPresentationCollection.ordered(
            documents,
            self._lifecycle.documents.active_document_id,
        )

    def _ensure_restored_sld_views(self, documents: tuple[Document, ...]) -> None:
        """Ensure every restored SLD document has its canonical logical SLD view."""
        for document in documents:
            if not isinstance(document, SLDDocument):
                raise TypeError(
                    f"Restored presentation document must be an SLDDocument: {document!r}"
                )
            view_id = f"{document.document_id}:sld"
            existing = self._lifecycle.views.get(view_id)
            if existing is None:
                self._lifecycle.add_view(
                    ViewRecord(
                        view_id=view_id,
                        document_id=document.document_id,
                        view_type="sld",
                        viewport=(
                            self._viewport_preference_store.load(view_id)
                            if self._viewport_preference_store is not None
                            else None
                        ),
                    )
                )
                continue
            if existing.document_id != document.document_id or existing.view_type != "sld":
                raise ValueError(
                    f"Canonical SLD view {view_id!r} is bound to "
                    f"{existing.document_id!r}/{existing.view_type!r}."
                )
            if self._viewport_preference_store is not None:
                saved_viewport = self._viewport_preference_store.load(view_id)
                if saved_viewport is not None:
                    existing.viewport = saved_viewport

    def _activate_workspace_presentation(
        self,
        context: ProjectContext | None,
        presentation: object | None,
        *,
        open_existing: bool,
        activate_workspace: bool,
    ):
        snapshot = self._lifecycle.capture_transition_state()
        previous_document = next(
            (
                document
                for document in snapshot.documents
                if document.document_id == snapshot.active_document_id
            ),
            None,
        )
        try:
            if context is None:
                self._lifecycle.close_project()
                active_document: Document | None = None
            else:
                if not isinstance(presentation, Document):
                    raise RuntimeError("Application transition did not provide a workspace Document.")
                collection = self._pending_presentation_collection
                self._pending_presentation_collection = None
                self._lifecycle.activate_project_transition(
                    self._to_ui_project(context),
                    document=presentation,
                    activate_workspace=activate_workspace,
                    open_existing=open_existing,
                )
                if collection is not None:
                    ordered = tuple(collection.documents)
                    for document_item in ordered:
                        if document_item.document_id != presentation.document_id:
                            self._lifecycle.documents.register(document_item)
                    for index, document_item in enumerate(ordered):
                        self._lifecycle.documents.move(document_item.document_id, index, mark_dirty=False)
                    self._ensure_restored_sld_views(ordered)
                    active_id = collection.active_document_id
                    if active_id is None:
                        raise ValueError("Restored presentation collection has no active_document_id.")
                    self._lifecycle.documents.activate(active_id)
                else:
                    ordered = (presentation,)
                    self._ensure_restored_sld_views(ordered)
                    self._lifecycle.documents.activate(presentation.document_id)

                active_document = self._lifecycle.documents.active_document
                if active_document is None:
                    raise RuntimeError("Restored active document is not registered.")
                if self._application.presentation is not active_document:
                    self._application.activate_presentation(active_document)
                self._lifecycle.views.activate(f"{active_document.document_id}:sld")

            if self._presentation_activation_bridge is not None:
                self._presentation_activation_bridge(active_document)

            def rollback() -> None:
                self._lifecycle.restore_last_state(snapshot)
                if self._presentation_activation_bridge is not None:
                    self._presentation_activation_bridge(previous_document)

            return rollback
        except BaseException as primary:
            # Lifecycle and bridge restoration are independent: failure in one
            # must not prevent the other from recovering its own state.
            recovery: list[tuple[str, Callable[[], None]]] = [
                ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(snapshot)),
            ]
            if previous_document is not None and self._application.presentation is not previous_document:
                recovery.append(
                    ("Application presentation restoration", lambda: self._application.activate_presentation(previous_document))
                )
            if self._presentation_activation_bridge is not None:
                recovery.append(
                    ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous_document))
                )
            self._recover_independently(primary, tuple(recovery))
            raise

    @staticmethod
    def _is_cancel(decision: ProjectTransitionDecision | str | None) -> bool:
        return (
            decision is ProjectTransitionDecision.CANCEL
            or (
                isinstance(decision, str)
                and decision.strip().lower() == ProjectTransitionDecision.CANCEL.value
            )
        )

    def _run_transition(
        self,
        *,
        operation: str,
        configure: Callable[[], None],
        transition: Callable[[], ProjectContext | None],
        decision: ProjectTransitionDecision | str | None,
    ) -> ProjectContext | None:
        # CANCEL is resolved before any lifecycle configuration is touched.
        if self._is_cancel(decision):
            current = self._application.project_lifecycle.context
            if current is None:
                raise RuntimeError("Cancel cannot leave the Application without an active project.")
            return current

        configuration = self._application.project_lifecycle.capture_presentation_configuration()
        previous_context = self._application.project_lifecycle.context
        previous_project_id = getattr(previous_context, "project_id", None)
        try:
            configure()
            context = transition()
        except BaseException:
            # Only failures before the Application transition commits may restore
            # its prior presentation contract. Publication is post-commit and
            # must never roll the Application back independently of its lifecycle.
            self._application.project_lifecycle.restore_presentation_configuration(configuration)
            raise

        # Application owns the authoritative transition result. Publish only
        # after success, including a successful close whose new context is None.
        # Keep publication outside the rollback boundary: handlers synchronize
        # projections after commit and cannot safely undo the committed project.
        if self._is_cancel(decision):
            return context
        if context is not None:
            event_project_id = context.project_id
        elif self._lifecycle.project is not None:
            event_project_id = self._lifecycle.project.project_id
        else:
            event_project_id = previous_project_id or ""
        self._publish(operation, self._lifecycle.state, event_project_id)
        return context

    def new_project(
        self,
        name: str = "Untitled Project",
        *,
        project_id: str | None = None,
        document: Document | None = None,
        activate_workspace: bool = True,
        decision: ProjectTransitionDecision | str | None = None,
    ) -> ProjectContext:
        decision = self._prepare_workspace_transition(decision)
        if self._is_cancel(decision):
            current = self._application.project_lifecycle.context
            if current is None:
                raise RuntimeError("Cancel cannot leave the Application without an active project.")
            return current
        recovery = self._guard_project_transition()
        try:
            context = self._run_transition(
                operation="new",
                configure=lambda: self._configure_presentation_transaction(
                    document,
                    open_existing=False,
                    activate_workspace=activate_workspace,
                ),
                transition=lambda: self._application.new_project(name, project_id=project_id, decision=decision),
                decision=decision,
            )
        except BaseException:
            self._restore_interaction_after_failure(recovery, operation="new")
            raise
        if context is None:
            raise RuntimeError("New project transition did not return a project context.")
        return context

    def open_project(
        self,
        path: str,
        *,
        activate_workspace: bool = True,
        decision: ProjectTransitionDecision | str | None = None,
    ) -> ProjectContext:
        decision = self._prepare_workspace_transition(decision)
        if self._is_cancel(decision):
            current = self._application.project_lifecycle.context
            if current is None:
                raise RuntimeError("Cancel cannot leave the Application without an active project.")
            return current
        recovery = self._guard_project_transition()
        try:
            context = self._run_transition(
                operation="open",
                configure=lambda: self._configure_presentation_transaction(
                    open_existing=True,
                    activate_workspace=activate_workspace,
                ),
                transition=lambda: self._application.open_project(path, decision=decision),
                decision=decision,
            )
        except BaseException:
            self._restore_interaction_after_failure(recovery, operation="open")
            raise
        if context is None:
            raise RuntimeError("Open project transition did not return a project context.")
        return context

    @staticmethod
    def _recover_independently(primary: BaseException, operations: tuple[tuple[str, Callable[[], None]], ...]) -> None:
        """Attempt every independent recovery step and retain diagnostics on the primary error."""
        failures: list[tuple[str, BaseException]] = []
        for label, operation in operations:
            try:
                operation()
            except BaseException as recovery_error:
                failures.append((label, recovery_error))
        if failures:
            add_note = getattr(primary, "add_note", None)
            if callable(add_note):
                for label, recovery_error in failures:
                    add_note(
                        f"Recovery step {label!r} failed: "
                        f"{type(recovery_error).__name__}: {recovery_error}"
                    )

    def activate_document(
        self,
        document_id: str,
        *,
        _reconcile_rollback: bool = True,
        _run_transition_guard: bool = True,
        _publish_change: bool = True,
    ) -> ProjectWorkspaceState:
        """Atomically activate one canonical workspace document and presentation."""
        document = self._lifecycle.documents.require(document_id)
        if not isinstance(document, SLDDocument):
            raise TypeError("Only SLDDocument activation is supported by the SLD document lifecycle.")
        snapshot = self._lifecycle.capture_transition_state()
        previous = self._lifecycle.document
        try:
            if _run_transition_guard and self._document_transition_guard is not None:
                self._document_transition_guard()
            self._lifecycle.activate_document_id(document_id)
            self._application.activate_presentation(document)
            if self._presentation_activation_bridge is not None:
                self._presentation_activation_bridge(document)
            if _publish_change:
                self._publish("activate_document", self._lifecycle.state, self._lifecycle.project.project_id)
            return self._lifecycle.state
        except BaseException as primary:
            recovery: list[tuple[str, Callable[[], None]]] = [
                ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(snapshot)),
            ]
            if previous is not None:
                recovery.append(
                    ("Application presentation restoration", lambda: self._application.activate_presentation(previous))
                )
                if self._presentation_activation_bridge is not None:
                    recovery.append(
                        ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous))
                    )
            self._recover_independently(primary, tuple(recovery))
            if _reconcile_rollback:
                self._restore_interaction_after_failure(None, operation="activate_document")
            raise

    def replace_document(self, document: SLDDocument) -> ProjectWorkspaceState:
        """Replace one registered SLD document through the Application presentation boundary."""
        if not isinstance(document, SLDDocument):
            raise TypeError("Only SLDDocument replacement is supported by the SLD document lifecycle.")
        existing = self._lifecycle.documents.require(document.document_id)
        if not isinstance(existing, SLDDocument):
            raise TypeError("Only an existing SLDDocument can be replaced by this adapter.")
        snapshot = self._lifecycle.capture_transition_state()
        previous = self._lifecycle.document
        try:
            if self._document_transition_guard is not None:
                self._document_transition_guard()
            self._lifecycle.replace_document(document)
            self._application.activate_presentation(document)
            if self._presentation_activation_bridge is not None:
                self._presentation_activation_bridge(document)
            self._publish("replace_document", self._lifecycle.state, self._lifecycle.project.project_id)
            return self._lifecycle.state
        except BaseException as primary:
            recovery: list[tuple[str, Callable[[], None]]] = [
                ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(snapshot)),
            ]
            if previous is not None:
                recovery.append(
                    ("Application presentation restoration", lambda: self._application.activate_presentation(previous))
                )
            if self._presentation_activation_bridge is not None:
                recovery.append(
                    ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous))
                )
            self._recover_independently(primary, tuple(recovery))
            self._restore_interaction_after_failure(None, operation="replace_document")
            raise

    def new_sld_document(self, name: str | None = None) -> SLDDocument:
        """Create/register/activate a real SLD document through the canonical manager."""
        project = self._lifecycle.project
        if project is None:
            raise RuntimeError("Cannot create an SLD document without an active project.")
        previous = self._lifecycle.document
        snapshot = self._lifecycle.capture_transition_state()
        try:
            if self._document_transition_guard is not None:
                self._document_transition_guard()
        except BaseException:
            self._restore_interaction_after_failure(None, operation="new_sld_document_guard")
            raise
        document: SLDDocument | None = None
        try:
            document_name = name or f"SLD-{len(tuple(self._lifecycle.documents.documents())) + 1:02d}"
            document = self._lifecycle.create_document(
                "sld",
                name=document_name,
                factory=lambda document_id, project_id, item_name: SLDDocument(
                    document_id=document_id,
                    project_id=project_id,
                    name=item_name,
                ),
            )
            self._lifecycle.add_view(ViewRecord(
                view_id=f"{document.document_id}:sld",
                document_id=document.document_id,
                view_type="sld",
            ))
            self.activate_document(
                document.document_id,
                _reconcile_rollback=False,
                _run_transition_guard=False,
            )
            return document
        except BaseException as primary:
            recovery: list[tuple[str, Callable[[], None]]] = []
            if document is not None and self._lifecycle.documents.get(document.document_id) is not None:
                recovery.append(
                    ("new-document removal", lambda: self._lifecycle.remove_document(document.document_id))
                )
            recovery.append(
                ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(snapshot))
            )
            if previous is not None:
                recovery.append(
                    ("Application presentation restoration", lambda: self._application.activate_presentation(previous))
                )
                if self._presentation_activation_bridge is not None:
                    recovery.append(
                        ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous))
                    )
            self._recover_independently(primary, tuple(recovery))
            self._restore_interaction_after_failure(None, operation="new_sld_document")
            raise

    def close_document(
        self,
        document_id: str,
        *,
        decision: str = "cancel",
    ) -> ProjectWorkspaceState:
        """Close exactly one SLD document with save/discard/cancel semantics."""
        document = self._lifecycle.documents.require(document_id)
        if not isinstance(document, SLDDocument):
            raise TypeError("Only SLDDocument documents can be closed here.")
        decision = str(decision).strip().lower()
        if decision not in {"save", "discard", "cancel"}:
            raise ValueError("decision must be save, discard, or cancel.")
        if decision == "cancel":
            return self._lifecycle.state
        # Reject an impossible close before invoking Save/Save As. The active
        # project must retain one SLD document, so a request that cannot commit
        # must not produce persistence side effects or clear dirty state.
        if len(self._lifecycle.documents) <= 1:
            raise RuntimeError("The active project must retain at least one SLD document.")
        try:
            if self._document_transition_guard is not None:
                self._document_transition_guard()
        except BaseException:
            self._restore_interaction_after_failure(None, operation="close_document_guard")
            raise
        if self._lifecycle.documents.is_dirty(document_id):
            if decision == "save":
                previous_active = self._lifecycle.document
                previous_snapshot = self._lifecycle.capture_transition_state()
                context = self._application.project_lifecycle.context
                save_path = getattr(context, "path", None) if context is not None else None
                if save_path is None:
                    if self._save_as_path_provider is None:
                        raise RuntimeError(
                            "Cannot close a dirty document without a project path; "
                            "configure the existing Save As path-selection workflow."
                        )
                    save_path = self._save_as_path_provider(context)
                    if save_path is None or not str(save_path).strip():
                        # Save As was cancelled; do not close or clean the document.
                        return self._lifecycle.state
                try:
                    if previous_active is not document:
                        self.activate_document(
                            document_id,
                            _reconcile_rollback=False,
                            _run_transition_guard=False,
                            _publish_change=False,
                        )
                    if context is not None and getattr(context, "path", None) is not None:
                        self._application.save_project()
                    else:
                        self._application.save_project_as(str(save_path))
                    # Saving a non-active document is an internal step of this
                    # close transaction, not a separately committed workspace
                    # transition. Restore the original active document silently;
                    # the eventual close event is the single public projection
                    # synchronization for the whole operation.
                    if previous_active is not None and previous_active is not document:
                        self.activate_document(
                            previous_active.document_id,
                            _reconcile_rollback=False,
                            _run_transition_guard=False,
                            _publish_change=False,
                        )
                except BaseException as primary:
                    recovery: list[tuple[str, Callable[[], None]]] = [
                        ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(previous_snapshot)),
                    ]
                    if previous_active is not None:
                        recovery.append(
                            ("Application presentation restoration", lambda: self._application.activate_presentation(previous_active))
                        )
                        if self._presentation_activation_bridge is not None:
                            recovery.append(
                                ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous_active))
                            )
                    self._recover_independently(primary, tuple(recovery))
                    self._restore_interaction_after_failure(None, operation="close_document_save")
                    raise
            elif decision == "discard":
                # Discard authorizes removal; do not clear dirty state before
                # the close transaction commits. If removal or activation fails,
                # the still-open document must remain dirty.
                pass

        snapshot = self._lifecycle.capture_transition_state()
        previous = self._lifecycle.document
        try:
            self._lifecycle.remove_document(document_id)
            active = self._lifecycle.document
            if active is not None:
                self._application.activate_presentation(active)
                if self._presentation_activation_bridge is not None:
                    self._presentation_activation_bridge(active)
            self._publish("close_document", self._lifecycle.state, self._lifecycle.project.project_id)
            return self._lifecycle.state
        except BaseException as primary:
            recovery: list[tuple[str, Callable[[], None]]] = [
                ("workspace lifecycle restoration", lambda: self._lifecycle.restore_last_state(snapshot)),
            ]
            if previous is not None:
                recovery.append(
                    ("Application presentation restoration", lambda: self._application.activate_presentation(previous))
                )
                if self._presentation_activation_bridge is not None:
                    recovery.append(
                        ("presentation bridge restoration", lambda: self._presentation_activation_bridge(previous))
                    )
            self._recover_independently(primary, tuple(recovery))
            self._restore_interaction_after_failure(None, operation="close_document")
            raise
       
    def is_document_dirty(self, document_id: str) -> bool:
        return self._lifecycle.documents.is_dirty(document_id)

    def save_project(self) -> ProjectContext:
        """Persist the complete project package through Application."""
        return self._application.save_project()

    def save_project_as(self, path: str) -> ProjectContext:
        """Persist through the Application facade using a UI-resolved Save As path."""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("A non-empty Save As path is required.")
        return self._application.save_project_as(path)

    def close_project(
        self,
        *,
        decision: ProjectTransitionDecision | str | None = None,
    ) -> ProjectContext | None:
        decision = self._prepare_workspace_transition(decision)
        if self._is_cancel(decision):
            return self._application.project_lifecycle.context
        recovery = self._guard_project_transition()
        try:
            return self._run_transition(
                operation="close",
                configure=lambda: self._configure_presentation_transaction(
                    open_existing=False,
                    activate_workspace=False,
                ),
                transition=lambda: self._application.close_project(decision=decision),
                decision=decision,
            )
        except BaseException:
            self._restore_interaction_after_failure(recovery, operation="close")
            raise

    def _publish(self, operation: str, state: ProjectWorkspaceState, project_id: str) -> None:
        """Notify every subscriber without letting one projection failure starve others."""
        event = ProjectWorkspaceChanged(operation=operation, state=state, project_id=project_id)
        for handler in tuple(self._handlers):
            try:
                handler(event)
            except Exception:
                # The Application transition is already committed. A subscriber
                # failure must be visible for diagnosis, but must not prevent
                # remaining subscribers from synchronizing their projections or
                # escape as if the project transaction itself had failed.
                _logger.exception(
                    "Project workspace change handler failed after commit: "
                    "operation=%s project_id=%s handler=%r",
                    operation,
                    project_id,
                    handler,
                )

    @staticmethod
    def _to_ui_project(context: ProjectContext) -> Project:
        return Project(
            project_id=context.project_id,
            name=context.name,
            metadata={"path": str(context.path) if context.path is not None else None},
        )


__all__ = ["ProjectWorkspaceApplicationAdapter", "ProjectWorkspaceChanged", "WorkspaceUpdateHandler"]
