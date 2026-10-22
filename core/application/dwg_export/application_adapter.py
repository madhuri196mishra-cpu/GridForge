'''Concrete adapter from the public Application read facade to DWG snapshots.

This adapter consumes only Application read-side DTOs. Layout-aware SLD export
still needs a separate adapter over the approved immutable SLD boundary.
'''
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from core.application.read_models import NetworkReadModel

from .drawing_plan import DrawingEntity, SymbolProfile
from .source_snapshot import ApplicationDrawingSnapshot


class ApplicationDrawingSnapshotAdapter:
    '''Capture one revision-consistent network read and detach export entities.

    entity_builder receives the immutable NetworkReadModel returned by
    Application.read_network(). It must return DrawingEntity values only;
    coordinates and symbol geometry are supplied by a presentation adapter,
    not inferred from electrical topology.
    '''

    def __init__(self, application: object, *, project_id: str) -> None:
        if application is None or not callable(getattr(application, "read_network", None)):
            raise TypeError("application must expose read_network().")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id must be a non-empty string.")
        if not hasattr(application, "revision"):
            raise TypeError("application must expose its revision.")
        self._application = application
        self._project_id = project_id.strip()

    def capture_network(
        self,
        *,
        source_id: str,
        drawing_types: tuple[str, ...],
        symbol_profile: SymbolProfile,
        entity_builder: Callable[[NetworkReadModel], tuple[DrawingEntity, ...]],
    ) -> ApplicationDrawingSnapshot:
        '''Build a detached snapshot, rejecting revision changes during capture.'''
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string.")
        if not callable(entity_builder):
            raise TypeError("entity_builder must be callable.")

        revision_before = self._application.revision
        read_model = self._application.read_network()
        if not isinstance(read_model, NetworkReadModel):
            raise TypeError("Application.read_network() must return NetworkReadModel.")
        entities = tuple(entity_builder(read_model))
        revision_after = self._application.revision
        if revision_before != revision_after:
            raise RuntimeError("Application revision changed during DWG snapshot capture; retry the export.")
        return ApplicationDrawingSnapshot(
            project_id=self._project_id,
            export_id=str(uuid4()),
            source_revision=str(revision_before),
            source_kind="network_read_model",
            source_id=source_id.strip(),
            symbol_profile=symbol_profile,
            drawing_types=tuple(drawing_types),
            entities=entities,
        )


__all__ = ["ApplicationDrawingSnapshotAdapter"]
