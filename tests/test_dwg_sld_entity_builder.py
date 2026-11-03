from __future__ import annotations

import pytest

from core.application.dwg_export import (
    DrawingEntity, SLDNodeEntityBuilder, SymbolDefinition, SymbolProfile, SymbolRegistry,
)
from ui.canvas.sld_canvas_projection import SLDCanvasNode, SLDCanvasSnapshot
from ui.equipment.symbol.symbol_base import SymbolBase


def _registry() -> SymbolRegistry:
    primitive = DrawingEntity("primitive-1", "LINE", "SYMBOL", {
        "x1": -1, "y1": 0, "x2": 1, "y2": 0,
    }, {})
    return SymbolRegistry((SymbolDefinition(
        symbol_id="breaker",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,),
    ),))


def test_sld_node_builder_uses_persisted_symbol_identity_and_transform() -> None:
    presentation = SymbolBase(
        "instance-7", "breaker", scale=1.5, rotation=90,
        properties={"authored": "yes"},
    )
    projection = SLDCanvasSnapshot(
        nodes=(SLDCanvasNode("node-7", "equipment-7", 120, 240, {}, presentation),),
        connections=(),
    )

    entities = SLDNodeEntityBuilder(_registry()).build(
        projection, profile=SymbolProfile.ANSI_IEEE,
    )

    assert len(entities) == 1
    entity = entities[0]
    assert entity.kind == "INSERT"
    assert entity.entity_id == "sld-node:node-7"
    assert entity.geometry["symbol_id"] == "breaker"
    assert entity.geometry["insert"] == (120.0, 240.0)
    assert entity.geometry["scale"] == 1.5
    assert entity.geometry["rotation"] == 90.0
    assert entity.metadata["gridforge_node_id"] == "node-7"
    assert entity.metadata["equipment_id"] == "equipment-7"


def test_sld_node_builder_fails_closed_when_persisted_symbol_is_unmapped() -> None:
    presentation = SymbolBase("instance-1", "unknown-symbol")
    projection = SLDCanvasSnapshot(
        nodes=(SLDCanvasNode("node-1", None, 0, 0, {}, presentation),),
        connections=(),
    )
    with pytest.raises(KeyError, match="unknown-symbol"):
        SLDNodeEntityBuilder(_registry()).build(
            projection, profile=SymbolProfile.ANSI_IEEE,
        )


def test_sld_node_builder_rejects_nodes_without_persisted_presentation() -> None:
    projection = SLDCanvasSnapshot(
        nodes=(SLDCanvasNode("node-1", None, 0, 0, {}, None),),
        connections=(),
    )
    with pytest.raises(ValueError, match="no persisted symbol presentation"):
        SLDNodeEntityBuilder(_registry()).build(
            projection, profile=SymbolProfile.ANSI_IEEE,
        )


@pytest.mark.parametrize(
    ("route_points", "expected"),
    [
        ((), ((10.0, 20.0), (90.0, 80.0))),
        (((50.0, 20.0),), ((10.0, 20.0), (50.0, 20.0), (90.0, 80.0))),
        (
            ((50.0, 20.0), (50.0, 80.0), (70.0, 80.0)),
            ((10.0, 20.0), (50.0, 20.0), (50.0, 80.0), (70.0, 80.0), (90.0, 80.0)),
        ),
    ],
)
def test_sld_connection_builder_preserves_endpoints_around_route_bends(
    route_points: tuple[tuple[float, float], ...],
    expected: tuple[tuple[float, float], ...],
) -> None:
    from ui.canvas.sld_canvas_projection import SLDCanvasConnection
    from ui.sld.sld_model import SLDRoute

    connection = SLDCanvasConnection(
        connection_id="connection-1",
        source_node_id="source",
        target_node_id="target",
        source_endpoint=None,
        target_endpoint=None,
        route=SLDRoute(points=route_points),
        connection_kind="wire",
        presentation_owner="engineer",
        projection_source="persisted",
        properties={},
    )
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("source", None, 10, 20, {}, None),
            SLDCanvasNode("target", None, 90, 80, {}, None),
        ),
        connections=(connection,),
    )

    entities = SLDNodeEntityBuilder.build_connections(projection)

    assert len(entities) == 1
    assert entities[0].geometry["points"] == expected
    assert entities[0].geometry["points"][0] == (10.0, 20.0)
    assert entities[0].geometry["points"][-1] == (90.0, 80.0)


def test_sld_connection_builder_removes_only_consecutive_duplicate_points() -> None:
    from ui.canvas.sld_canvas_projection import SLDCanvasConnection
    from ui.sld.sld_model import SLDRoute

    connection = SLDCanvasConnection(
        connection_id="connection-duplicate",
        source_node_id="source",
        target_node_id="target",
        source_endpoint=None,
        target_endpoint=None,
        route=SLDRoute(points=((10, 20), (50, 20), (50, 20), (10, 20))),
        connection_kind="wire",
        presentation_owner="engineer",
        projection_source="persisted",
        properties={},
    )
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("source", None, 10, 20, {}, None),
            SLDCanvasNode("target", None, 90, 80, {}, None),
        ),
        connections=(connection,),
    )

    entity = SLDNodeEntityBuilder.build_connections(projection)[0]

    assert entity.geometry["points"] == (
        (10.0, 20.0), (50.0, 20.0), (10.0, 20.0), (90.0, 80.0),
    )


def test_sld_connection_builder_rejects_geometry_without_distinct_endpoints() -> None:
    from ui.canvas.sld_canvas_projection import SLDCanvasConnection
    from ui.sld.sld_model import SLDRoute

    connection = SLDCanvasConnection(
        connection_id="connection-zero-length",
        source_node_id="source",
        target_node_id="target",
        source_endpoint=None,
        target_endpoint=None,
        route=SLDRoute(),
        connection_kind="wire",
        presentation_owner="engineer",
        projection_source="persisted",
        properties={},
    )
    projection = SLDCanvasSnapshot(
        nodes=(
            SLDCanvasNode("source", None, 10, 20, {}, None),
            SLDCanvasNode("target", None, 10, 20, {}, None),
        ),
        connections=(connection,),
    )

    with pytest.raises(ValueError, match="no distinct source-to-target presentation geometry"):
        SLDNodeEntityBuilder.build_connections(projection)
