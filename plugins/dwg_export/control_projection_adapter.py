"""Capture the authoritative Application Control program for a separate drawing.

The adapter never reads ControlCanvas or SLD topology. It accepts only the
Application's immutable ControlProgramReadModel and stamps the capture with
project revision and activation-generation guards.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from core.application.services.control_service import ControlProgramReadModel

from .drawing_plan import DrawingEntity, SymbolProfile
from .source_snapshot import ApplicationDrawingSnapshot


class ControlProjectionDrawingSnapshotAdapter:
    """Capture one revision-consistent Control program read model."""

    def capture(
        self,
        application: object,
        *,
        project_id: str,
        symbol_profile: SymbolProfile = SymbolProfile.ANSI_IEEE,
        entity_builder: Callable[[ControlProgramReadModel, SymbolProfile], tuple[DrawingEntity, ...]],
    ) -> ApplicationDrawingSnapshot:
        if application is None or not callable(getattr(application, "read_control", None)):
            raise TypeError("application must expose read_control().")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id must be a non-empty string.")
        if not hasattr(application, "revision"):
            raise TypeError("application must expose its revision.")
        lifecycle = getattr(application, "project_lifecycle", None)
        if lifecycle is None or not all(
            hasattr(lifecycle, name) for name in ("context", "activation_generation")
        ):
            raise TypeError("application must expose project_lifecycle context and activation_generation.")
        if not callable(entity_builder):
            raise TypeError("entity_builder must be callable.")

        requested_project = project_id.strip()
        context = lifecycle.context
        if context is None or context.project_id != requested_project:
            raise RuntimeError("requested project does not match active Application project")
        generation_before = lifecycle.activation_generation
        revision_before = application.revision
        read_model = application.read_control()
        if not isinstance(read_model, ControlProgramReadModel):
            raise TypeError("Application.read_control() must return ControlProgramReadModel.")
        if read_model.project_id != requested_project:
            raise RuntimeError("Control read model project identity does not match active project.")
        if not read_model.program_id.strip():
            raise RuntimeError("Control read model has no stable program identity.")

        profile = symbol_profile if isinstance(symbol_profile, SymbolProfile) else SymbolProfile(symbol_profile)
        entities = tuple(entity_builder(read_model, profile))
        if any(not isinstance(entity, DrawingEntity) for entity in entities):
            raise TypeError("entity_builder must return only DrawingEntity values.")

        revision_after = application.revision
        if revision_before != revision_after:
            raise RuntimeError("Application revision changed during Control drawing snapshot capture; retry the export.")
        if (
            application.project_lifecycle is not lifecycle
            or lifecycle.context is not context
            or lifecycle.activation_generation != generation_before
            or context.project_id != requested_project
        ):
            raise RuntimeError("Application project identity or activation changed during Control drawing snapshot capture; retry the export.")

        return ApplicationDrawingSnapshot(
            project_id=requested_project,
            export_id=str(uuid4()),
            source_revision=str(revision_before),
            source_kind="control_program_read_model",
            source_id=read_model.program_id,
            symbol_profile=profile,
            drawing_types=("control",),
            entities=entities,
            units="mm",
            coordinate_system="control-ladder-local",
        )


__all__ = ["ControlProjectionDrawingSnapshotAdapter"]
