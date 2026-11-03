from __future__ import annotations

import pytest

from core.application.read_models import NetworkReadModel
from core.application.dwg_export import (
    ApplicationDrawingSnapshotAdapter,
    DrawingEntity,
    SymbolProfile,
)


class FakeApplication:
    def __init__(self, *, mutate_revision: bool = False) -> None:
        self._revision = 7
        self.mutate_revision = mutate_revision

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
