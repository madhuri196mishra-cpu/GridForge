from __future__ import annotations

import pytest

from core.application.sld_export_snapshot import (
    SLDConnectionExportSnapshot, SLDExportSnapshot, SLDNodeExportSnapshot,
    SLDSymbolPlacementSnapshot,
)
from plugins.dwg_export import DrawingEntity, SLDNodeEntityBuilder, SymbolDefinition, SymbolProfile, SymbolRegistry


def _registry() -> SymbolRegistry:
    primitive = DrawingEntity("symbol-line", "LINE", "SYMBOL", {"start": (0, 0), "end": (1, 0)}, {})
    return SymbolRegistry((SymbolDefinition(
        symbol_id="generic", profile=SymbolProfile.ANSI_IEEE, primitives=(primitive,),
        terminal_anchors={"from": (-10.0, 0.0), "to": (10.0, 0.0)},
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
        route_points=route_points, route_routing_mode="manual",
        route_ownership="engineer", connection_kind="SIMPLE_WIRE",
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
    assert entity.geometry["points"] == (
        (0.0, 0.0), (1.0, 2.0), (30.0, 2.0), (30.0, 45.0), (100.0, 50.0),
    )
    assert entity.metadata["route_ownership"] == "engineer"
    assert entity.metadata["route_routing_mode"] == "manual"
    assert entity.metadata["route_geometry_source"] == "persisted_route"
    assert entity.metadata["gridforge_connection_id"] == "wire-9"
    assert entity.metadata["persisted_connection_properties"]["core_connection_id"] == "core-wire-9"
    assert entity.metadata["core_connection_id"] == "core-wire-9"


def test_single_authored_bend_is_preserved_between_both_connection_endpoints() -> None:
    entities = SLDNodeEntityBuilder(_registry()).build(
        _plan(((40, 20),)), profile=SymbolProfile.ANSI_IEEE,
    )
    entity = next(item for item in entities if item.kind == "LWPOLYLINE")
    assert entity.geometry["points"] == ((0.0, 0.0), (40.0, 20.0), (100.0, 50.0))
    assert entity.metadata["route_geometry_source"] == "persisted_route"


def test_unrouted_explicit_connection_uses_positions_only_as_display_endpoints() -> None:
    entities = SLDNodeEntityBuilder(_registry()).build(
        _plan(()), profile=SymbolProfile.ANSI_IEEE,
    )
    entity = next(item for item in entities if item.kind == "LWPOLYLINE")
    assert entity.geometry["points"] == ((0.0, 0.0), (100.0, 50.0))
    assert entity.metadata["route_geometry_source"] == "node_position_display_fallback"


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



def test_connection_snapshot_rejects_endpoint_node_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="source_endpoint node_id"):
        SLDConnectionExportSnapshot(
            connection_id="bad-endpoint", source_node_id="a", target_node_id="b",
            source_endpoint={"node_id": "other", "terminal_id": "t1"},
            target_endpoint=None, route_points=(),
        )


@pytest.mark.parametrize("route_points", [((1,),), ((1, 2, 3),), ((True, 2),)])
def test_connection_snapshot_rejects_malformed_route_points(route_points) -> None:
    with pytest.raises(ValueError, match="route point"):
        SLDConnectionExportSnapshot(
            connection_id="bad-route", source_node_id="a", target_node_id="b",
            source_endpoint=None, target_endpoint=None, route_points=route_points,
        )


def test_equipment_connection_endpoints_resolve_reviewed_terminal_anchor_offsets() -> None:
    source = SLDNodeExportSnapshot(
        node_id="source", equipment_id="equipment-source", x=0, y=0,
        properties={},
        symbol=SLDSymbolPlacementSnapshot(
            symbol_instance_id="source-instance", symbol_definition_id="generic",
            representation_id="symbol", visible=True, scale=1.5,
            rotation_degrees=90, properties={"mirror_x": True},
        ),
    )
    target = SLDNodeExportSnapshot(
        node_id="target", equipment_id="equipment-target", x=100, y=50,
        properties={},
        symbol=SLDSymbolPlacementSnapshot(
            symbol_instance_id="target-instance", symbol_definition_id="generic",
            representation_id="symbol", visible=True, scale=2.0,
            rotation_degrees=0,
        ),
    )
    connection = SLDConnectionExportSnapshot(
        connection_id="equipment-wire",
        source_node_id="source",
        target_node_id="target",
        source_endpoint={
            "kind": "equipment", "node_id": "source",
            "equipment_id": "equipment-source", "terminal_role": "from",
            "bus_id": None, "attachment_id": None,
        },
        target_endpoint={
            "kind": "equipment", "node_id": "target",
            "equipment_id": "equipment-target", "terminal_role": "to",
            "bus_id": None, "attachment_id": None,
        },
        route_points=((50.0, 20.0),),
    )
    snapshot = SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-1", nodes=(source, target),
        connections=(connection,),
    )

    entity = next(
        item for item in SLDNodeEntityBuilder(_registry()).build(
            snapshot, profile=SymbolProfile.ANSI_IEEE,
        )
        if item.kind == "LWPOLYLINE"
    )

    assert entity.geometry["points"] == (
        (0.0, 0.0), (50.0, 20.0), (100.0, 50.0),
    )
    # source FROM (-10, 0), mirror-X, scale 1.5, rotation 90 -> (0, 15)
    assert entity.metadata["source_terminal_anchor_offset_mm"] == (0.0, 15.0)
    # target TO (10, 0), scale 2, rotation 0 -> (20, 0)
    assert entity.metadata["target_terminal_anchor_offset_mm"] == (20.0, 0.0)
