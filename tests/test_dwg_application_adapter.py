from __future__ import annotations

import pytest
from types import SimpleNamespace

from core.application.read_models import NetworkReadModel
from plugins.dwg_export import (
    ApplicationDrawingSnapshotAdapter,
    DrawingEntity,
    SymbolProfile,
)


class FakeApplication:
    def __init__(self, *, mutate_revision: bool = False) -> None:
        self._revision = 7
        self.mutate_revision = mutate_revision
        self.project_lifecycle = SimpleNamespace(
            context=SimpleNamespace(project_id="project-1"),
            activation_generation=2,
        )

    @property
    def revision(self) -> int:
        value = self._revision
        if self.mutate_revision:
            self._revision += 1
        return value

    def read_network(self) -> NetworkReadModel:
        return NetworkReadModel(elements=(), simple_wires=())


def _entity_builder(model: NetworkReadModel) -> tuple[DrawingEntity, ...]:
    assert isinstance(model, NetworkReadModel)
    return (DrawingEntity("gf-bus-1", "CIRCLE", "EQUIPMENT", {"center": (0, 0), "radius": 2}, {"source": "read-model"}),)


def test_adapter_captures_detached_snapshot_from_application_read_model() -> None:
    adapter = ApplicationDrawingSnapshotAdapter(FakeApplication(), project_id="project-1")
    snapshot = adapter.capture_network(
        source_id="network-1",
        drawing_types=("sld",),
        symbol_profile=SymbolProfile.ANSI_IEEE,
        entity_builder=_entity_builder,
    )

    assert snapshot.project_id == "project-1"
    assert snapshot.source_revision == "7"
    assert snapshot.source_kind == "network_read_model"
    assert snapshot.entities[0].entity_id == "gf-bus-1"


def test_adapter_rejects_revision_change_during_capture() -> None:
    adapter = ApplicationDrawingSnapshotAdapter(FakeApplication(mutate_revision=True), project_id="project-1")

    with pytest.raises(RuntimeError, match="revision changed"):
        adapter.capture_network(
            source_id="network-1",
            drawing_types=("sld",),
            symbol_profile=SymbolProfile.ANSI_IEEE,
            entity_builder=_entity_builder,
        )


def test_adapter_rejects_non_application_read_facade() -> None:
    with pytest.raises(TypeError, match="read_network"):
        ApplicationDrawingSnapshotAdapter(object(), project_id="project-1")



def test_adapter_rejects_project_activation_during_capture() -> None:
    application = FakeApplication()
    adapter = ApplicationDrawingSnapshotAdapter(application, project_id="project-1")

    def activate_during_build(model: NetworkReadModel) -> tuple[DrawingEntity, ...]:
        application.project_lifecycle.activation_generation += 1
        return _entity_builder(model)

    with pytest.raises(RuntimeError, match="project identity or activation changed"):
        adapter.capture_network(
            source_id="network-1",
            drawing_types=("sld",),
            symbol_profile=SymbolProfile.ANSI_IEEE,
            entity_builder=activate_during_build,
        )


def test_adapter_rejects_mismatched_project_identity() -> None:
    application = FakeApplication()
    application.project_lifecycle.context.project_id = "project-2"
    adapter = ApplicationDrawingSnapshotAdapter(application, project_id="project-1")

    with pytest.raises(RuntimeError, match="does not match active Application project"):
        adapter.capture_network(
            source_id="network-1",
            drawing_types=("sld",),
            symbol_profile=SymbolProfile.ANSI_IEEE,
            entity_builder=_entity_builder,
        )


from core.application.services.control_service import ControlProgramReadModel
from plugins.dwg_export import ControlProjectionDrawingSnapshotAdapter


class FakeControlApplication(FakeApplication):
    def read_control(self) -> ControlProgramReadModel:
        return ControlProgramReadModel(
            project_id="project-1",
            program_id="ladder-main",
            schema_version=1,
            rungs=(),
            components=(),
            connections=(),
            dependencies=(),
        )


def _control_entity_builder(model: ControlProgramReadModel, profile: SymbolProfile) -> tuple[DrawingEntity, ...]:
    assert isinstance(model, ControlProgramReadModel)
    assert profile is SymbolProfile.ANSI_IEEE
    return (
        DrawingEntity(
            "control-component-1",
            "INSERT",
            "CONTROL_COMPONENT",
            {"symbol_id": "control.contact.no", "insert": (0, 0)},
            {"source_component_id": "contact-1", "drawing_type": "control"},
        ),
    )


def test_control_adapter_captures_its_own_program_snapshot_and_provenance() -> None:
    adapter = ControlProjectionDrawingSnapshotAdapter()
    snapshot = adapter.capture(
        FakeControlApplication(),
        project_id="project-1",
        symbol_profile=SymbolProfile.ANSI_IEEE,
        entity_builder=_control_entity_builder,
    )

    assert snapshot.project_id == "project-1"
    assert snapshot.source_revision == "7"
    assert snapshot.source_kind == "control_program_read_model"
    assert snapshot.source_id == "ladder-main"
    assert snapshot.drawing_types == ("control",)
    assert snapshot.coordinate_system == "control-ladder-local"
    assert snapshot.units == "mm"
    assert snapshot.entities[0].entity_id == "control-component-1"


def test_control_adapter_rejects_revision_change_during_capture() -> None:
    adapter = ControlProjectionDrawingSnapshotAdapter()
    with pytest.raises(RuntimeError, match="revision changed"):
        adapter.capture(
            FakeControlApplication(mutate_revision=True),
            project_id="project-1",
            entity_builder=_control_entity_builder,
        )


def test_control_adapter_rejects_project_activation_during_capture() -> None:
    application = FakeControlApplication()
    adapter = ControlProjectionDrawingSnapshotAdapter()

    def activate_during_build(model: ControlProgramReadModel, profile: SymbolProfile):
        application.project_lifecycle.activation_generation += 1
        return _control_entity_builder(model, profile)

    with pytest.raises(RuntimeError, match="identity or activation changed"):
        adapter.capture(
            application,
            project_id="project-1",
            entity_builder=activate_during_build,
        )


def test_control_adapter_rejects_mismatched_project_identity() -> None:
    application = FakeControlApplication()
    application.project_lifecycle.context.project_id = "project-2"
    with pytest.raises(RuntimeError, match="does not match active Application project"):
        ControlProjectionDrawingSnapshotAdapter().capture(
            application,
            project_id="project-1",
            entity_builder=_control_entity_builder,
        )
