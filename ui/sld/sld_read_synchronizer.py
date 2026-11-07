# ============================================================
# File: ui/sld/sld_read_synchronizer.py
# GridForge V2 — SLD Read Synchronizer
# Author: Subhendu Mishra
# ============================================================
"""Read-only synchronization from Application read models to ephemeral SLD projections.

Persistent SLD state is deliberately absent from this module. Persistent
creation/update/delete and semantic binding reconciliation are owned by the
Application pre-commit path and SLDService. This synchronizer only refreshes
projection state from immutable Application read models.
"""

from __future__ import annotations

from typing import Any, Mapping

from core.application.read_models import ElementReadModel, NetworkReadModel, ProtectionReadModel

from .sld_projection import SLDProjection
from .sld_projection_manager import SLDProjectionManager
from .sld_read_adapter import SLDReadAdapter
from .sld_vocabulary import semantic_type


class SLDReadSynchronizer:
    """Synchronize immutable Application read state into ephemeral SLD projections."""

    def __init__(self, projection_manager: SLDProjectionManager, application: Any = None) -> None:
        if not isinstance(projection_manager, SLDProjectionManager):
            raise TypeError("projection_manager must be an SLDProjectionManager")
        self._projection_manager = projection_manager
        self._read_adapter = SLDReadAdapter()
        self._application = application
        self._reconciliation_diagnostics: tuple[dict[str, Any], ...] = ()

    @property
    def projection_manager(self) -> SLDProjectionManager:
        return self._projection_manager

    @property
    def application(self) -> Any:
        return self._application

    def attach_application(self, application: Any) -> None:
        if application is None:
            raise TypeError("application must not be None")
        self._application = application

    def detach_application(self) -> Any:
        application = self._application
        self._application = None
        return application

    @property
    def reconciliation_diagnostics(self) -> tuple[dict[str, Any], ...]:
        """Return deterministic Core/SLD Simple Wire reconciliation diagnostics."""
        return self._reconciliation_diagnostics

    def reconcile_simple_wires(
        self,
        read_model: NetworkReadModel | None = None,
        *,
        presentation: Any | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Audit Core Simple Wires against the supplied SLD presentation.

        When no presentation is supplied, preserve the canonical active-document
        behavior. Callers reconciling a non-active document must pass that
        document explicitly; this method never changes Application presentation.
        """
        if read_model is None:
            read_model = self._require_application().read_network()
        if not isinstance(read_model, NetworkReadModel):
            raise TypeError("read_model must be a NetworkReadModel")
        if presentation is None:
            presentation = getattr(self._require_application(), "presentation", None)
        model = getattr(presentation, "model", None)
        connections = tuple(getattr(model, "connections", ())) if model is not None else ()
        core_ids = {wire.connection_id for wire in read_model.simple_wires}
        sld_projection = {}
        diagnostics = []
        for connection in connections:
            if str(getattr(connection, "properties", {}).get("connection_kind", "")).upper() != "SIMPLE_WIRE":
                continue
            core_id = getattr(connection, "properties", {}).get("core_connection_id")
            if not core_id:
                diagnostics.append({"code": "MISSING_CORE_CONNECTION_ID", "presentation_id": connection.connection_id})
                continue
            core_id = str(core_id)
            if core_id in sld_projection:
                diagnostics.append({"code": "DUPLICATE_PRESENTATION_COMPANION", "connection_id": core_id})
            sld_projection[core_id] = connection
        for core_id in sorted(core_ids - set(sld_projection)):
            diagnostics.append({"code": "CORE_WIRE_WITHOUT_SLD_COMPANION", "connection_id": core_id})
        for core_id in sorted(set(sld_projection) - core_ids):
            diagnostics.append({"code": "SLD_WIRE_WITHOUT_CORE", "connection_id": core_id, "presentation_id": sld_projection[core_id].connection_id})
        self._reconciliation_diagnostics = tuple(diagnostics)
        return self._reconciliation_diagnostics

    def synchronize_network_from_application(self) -> tuple[SLDProjection, ...]:
        return self.synchronize_network(None, self._require_application().read_network())

    def synchronize_protection_from_application(self) -> tuple[SLDProjection, ...]:
        return self.synchronize_protection(None, self._require_application().read_protection())

    def synchronize_element_from_application(self, element_type: str, object_id: str) -> SLDProjection:
        return self.synchronize_element(self._require_application().read_element(element_type, object_id))

    def synchronize_network(self, presentation: Any, read_model: NetworkReadModel, *, initial_positions: Mapping[str, tuple[float, float]] | None = None) -> tuple[SLDProjection, ...]:
        """Refresh only the NETWORK projection registry; never mutate persistent SLD state."""
        if not isinstance(read_model, NetworkReadModel):
            raise TypeError("read_model must be a NetworkReadModel")
        adapted = self._read_adapter.network(read_model)
        self.reconcile_simple_wires(adapted, presentation=presentation)
        projections = self._projection_manager.project_network(adapted)
        self._projection_manager.reconcile_network(frozenset(e.object_id for e in adapted.elements))
        return projections

    def synchronize_protection(self, presentation: Any, read_model: ProtectionReadModel) -> tuple[SLDProjection, ...]:
        """Refresh only the PROTECTION projection registry; never mutate persistent SLD state."""
        if not isinstance(read_model, ProtectionReadModel):
            raise TypeError("read_model must be a ProtectionReadModel")
        adapted = self._read_adapter.protection(read_model)
        projections = self._projection_manager.project_protection(adapted)
        self._projection_manager.reconcile_protection(frozenset(e.object_id for e in adapted.elements))
        return projections

    def synchronize_element(self, read_model: ElementReadModel) -> SLDProjection:
        if not isinstance(read_model, ElementReadModel):
            raise TypeError("read_model must be an ElementReadModel")
        if semantic_type(read_model.element_type) == "RELAY":
            raise ValueError("Relay presentation belongs to the protection projection domain.")
        return self._projection_manager.project_network_element(self._read_adapter.element(read_model))

    def synchronize_protection_element(self, read_model: ElementReadModel) -> SLDProjection:
        if not isinstance(read_model, ElementReadModel):
            raise TypeError("read_model must be an ElementReadModel")
        return self._projection_manager.project_protection_element(self._read_adapter.element(read_model))

    def clear(self) -> None:
        self._projection_manager.clear()

    def _require_application(self) -> Any:
        if self._application is None:
            raise RuntimeError("SLD Application read facade is not configured")
        return self._application


__all__ = ["SLDReadSynchronizer"]
