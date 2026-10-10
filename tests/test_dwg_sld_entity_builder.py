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
