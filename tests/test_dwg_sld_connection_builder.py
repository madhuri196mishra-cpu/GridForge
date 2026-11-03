from __future__ import annotations

import pytest

from core.application.sld_export_snapshot import (
    SLDConnectionExportSnapshot, SLDExportSnapshot, SLDNodeExportSnapshot,
    SLDSymbolPlacementSnapshot,
)
from core.application.dwg_export import DrawingEntity, SLDNodeEntityBuilder, SymbolDefinition, SymbolProfile, SymbolRegistry


def _registry() -> SymbolRegistry:
    primitive = DrawingEntity("symbol-line", "LINE", "SYMBOL", {"start": (0, 0), "end": (1, 0)}, {})
    return SymbolRegistry((SymbolDefinition(
        symbol_id="generic", profile=SymbolProfile.ANSI_IEEE, primitives=(primitive,),
    ),))


def _node(node_id: str, x: float, y: float) -> SLDNodeExportSnapshot:
    return SLDNodeExportSnapshot(
        node_id=node_id, equipment_id=None, x=x, y=y, properties={},
        symbol=SLDSymbolPlacementSnapshot(
            symbol_instance_id=f"instance-{node_id}", symbol_definition_id="generic",
            representation_id="symbol", visible=True, scale=1, rotation_degrees=0,
        ),
    )


def _plan(route_points: tuple[tuple[float, float], ...]) -> SLDExportSnapshot:
    connection = SLDConnectionExportSnapshot(
        connection_id="wire-9", source_node_id="a", target_node_id="b",
        source_endpoint={"terminal_id": "terminal-a"},
        target_endpoint={"terminal_id": "terminal-b"},
        route_points=route_points, connection_kind="SIMPLE_WIRE",
        presentation_owner="engineer", projection_source="persisted",
        properties={"core_connection_id": "core-wire-9"},
    )
    return SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-1", nodes=(_node("a", 0, 0), _node("b", 100, 50)),
        connections=(connection,),
    )


def test_explicit_connection_route_is_preserved_without_topology_inference() -> None:
    entities = SLDNodeEntityBuilder(_registry()).build(
        _plan(((1, 2), (30, 2), (30, 45))), profile=SymbolProfile.ANSI_IEEE,
    )
    entity = next(item for item in entities if item.kind == "LWPOLYLINE")
    assert entity.kind == "LWPOLYLINE"
    assert entity.geometry["points"] == ((0.0, 0.0), (1.0, 2.0), (30.0, 2.0), (30.0, 45.0), (100.0, 50.0))
    assert entity.metadata["gridforge_connection_id"] == "wire-9"
    assert entity.metadata["properties"]["core_connection_id"] == "core-wire-9"


def test_unrouted_explicit_connection_uses_positions_only_as_display_endpoints() -> None:
    entities = SLDNodeEntityBuilder(_registry()).build(
        _plan(()), profile=SymbolProfile.ANSI_IEEE,
    )
    entity = next(item for item in entities if item.kind == "LWPOLYLINE")
    assert entity.geometry["points"] == ((0.0, 0.0), (100.0, 50.0))


def test_connection_with_missing_endpoint_node_fails_closed() -> None:
    plan = _plan(())
    connection = SLDConnectionExportSnapshot(
        connection_id="bad-wire", source_node_id="a", target_node_id="missing",
        source_endpoint=None, target_endpoint=None, route_points=(),
        connection_kind="SIMPLE_WIRE", presentation_owner="engineer",
        projection_source="persisted",
    )
    broken = SLDExportSnapshot(
        project_id=plan.project_id, document_id=plan.document_id,
        source_revision=plan.source_revision, nodes=plan.nodes, connections=(connection,),
    )
    with pytest.raises(ValueError, match="references a missing node"):
        SLDNodeEntityBuilder(_registry()).build(broken, profile=SymbolProfile.ANSI_IEEE)
