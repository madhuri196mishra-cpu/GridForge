from __future__ import annotations

from core.application.dwg_export import DrawingEntity, SLDNodeEntityBuilder, SymbolProfile
from ui.canvas.sld_canvas_projection import SLDCanvasConnection, SLDCanvasNode, SLDCanvasSnapshot
from ui.sld.sld_model import SLDRoute


def test_explicit_sld_connection_route_is_exported_without_topology_inference() -> None:
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("a", "equipment-a", 0, 0, {}, None),
            SLDCanvasNode("b", "equipment-b", 100, 50, {}, None),
        ),
        connections=(
            SLDCanvasConnection(
                connection_id="wire-9",
                source_node_id="a",
                target_node_id="b",
                source_endpoint=None,
                target_endpoint=None,
                route=SLDRoute(routing_mode="manual", ownership="engineer", points=((1, 2), (30, 2), (30, 45))),
                connection_kind="SIMPLE_WIRE",
                presentation_owner="engineer",
                projection_source="persisted",
                properties={"core_connection_id": "core-wire-9"},
            ),
        ),
    )

    entities = SLDNodeEntityBuilder.build_connections(projection)

    assert len(entities) == 1
    entity = entities[0]
    assert entity.kind == "LWPOLYLINE"
    assert entity.geometry["points"] == ((1.0, 2.0), (30.0, 2.0), (30.0, 45.0))
    assert entity.metadata["gridforge_connection_id"] == "wire-9"
    assert entity.metadata["core_connection_id"] == "core-wire-9"
    assert entity.metadata["route_ownership"] == "engineer"


def test_unrouted_explicit_sld_connection_uses_node_positions_only_for_display() -> None:
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("a", None, 3, 4, {}, None),
            SLDCanvasNode("b", None, 8, 9, {}, None),
        ),
        connections=(
            SLDCanvasConnection("connection-1", "a", "b", None, None, SLDRoute(), None, None, None, {}),
        ),
    )
    entity = SLDNodeEntityBuilder.build_connections(projection)[0]
    assert entity.geometry["points"] == ((3.0, 4.0), (8.0, 9.0))
    assert entity.metadata["source_kind"] == "persisted_sld_connection"


def test_sld_connection_with_missing_node_fails_closed() -> None:
    projection = SLDCanvasSnapshot(
        nodes=(SLDCanvasNode("a", None, 0, 0, {}, None),),
        connections=(
            SLDCanvasConnection("connection-1", "a", "missing", None, None, SLDRoute(), None, None, None, {}),
        ),
    )
    import pytest
    with pytest.raises(ValueError, match="missing projected node"):
        SLDNodeEntityBuilder.build_connections(projection)


def test_connection_kind_is_normalized_for_cad_layer_name() -> None:
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("a", None, 0, 0, {}, None),
            SLDCanvasNode("b", None, 10, 10, {}, None),
        ),
        connections=(
            SLDCanvasConnection(
                "connection-2", "a", "b", None, None, SLDRoute(),
                'wire;bad/name:*', None, None, {},
            ),
        ),
    )
    entity = SLDNodeEntityBuilder.build_connections(projection)[0]
    assert entity.layer == "SLD_WIRE_BAD_NAME"
    assert entity.metadata["connection_kind"] == 'wire;bad/name:*'
