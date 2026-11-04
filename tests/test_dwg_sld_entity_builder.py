from __future__ import annotations

import pytest

from core.application.sld_export_snapshot import (
    SLDExportSnapshot, SLDNodeExportSnapshot, SLDSymbolPlacementSnapshot,
)
from plugins.dwg_export import (
    DrawingEntity, SLDNodeEntityBuilder, SymbolDefinition, SymbolProfile, SymbolRegistry,
)


def _registry() -> SymbolRegistry:
    primitive = DrawingEntity("primitive-1", "LINE", "SYMBOL", {
        "start": (-1, 0), "end": (1, 0),
    }, {})
    return SymbolRegistry((SymbolDefinition(
        symbol_id="breaker", profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,), definition_version="1.0.0",
        review_status="reviewed", reviewer="gridforge-review",
        reviewed_on="2026-11-03",
        review_reference="review-record:GF-SYMBOL-SLD-001",
        standard_reference="standard-reference:IEEE-reviewed-source",
        terminal_anchors={"from": (-28, 0), "to": (28, 0)},
    ),))


def _node(symbol_id: str = "breaker") -> SLDNodeExportSnapshot:
    return SLDNodeExportSnapshot(
        node_id="node-7", equipment_id="equipment-7", x=120, y=240,
        properties={"source": "persisted"},
        symbol=SLDSymbolPlacementSnapshot(
            symbol_instance_id="instance-7", symbol_definition_id=symbol_id,
            representation_id="symbol", visible=True, scale=1.5,
            rotation_degrees=90, properties={"authored": "yes"},
        ),
        engineering_attributes={"rated_voltage_kv": 11.0},
        engineering_units={"rated_voltage_kv": "kV"},
    )


def test_sld_node_builder_uses_detached_symbol_identity_and_transform() -> None:
    snapshot = SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-42", nodes=(_node(),), connections=(),
    )
    entities = SLDNodeEntityBuilder(_registry()).build(snapshot, profile=SymbolProfile.ANSI_IEEE)
    assert len(entities) == 1
    entity = entities[0]
    assert entity.kind == "INSERT"
    assert entity.entity_id == "sld-node:node-7"
    assert entity.geometry["symbol_id"] == "breaker"
    assert entity.geometry["insert"] == (120.0, 240.0)
    assert entity.geometry["scale"] == 1.5
    assert entity.geometry["rotation"] == 90.0
    assert entity.metadata["equipment_id"] == "equipment-7"
    assert entity.metadata["engineering_units"]["rated_voltage_kv"] == "kV"
    assert entity.metadata["source_revision"] == "revision-42"
    assert entity.metadata["symbol_definition_version"] == "1.0.0"
    assert entity.metadata["symbol_definition_units"] == "mm"
    assert entity.metadata["symbol_review_status"] == "reviewed"
    assert entity.metadata["symbol_terminal_anchors"] == {"from": (-28.0, 0.0), "to": (28.0, 0.0)}
    assert entity.metadata["symbol_registry_schema_version"] == 1
    assert entity.metadata["symbol_catalogue_version"] == "1.0.0"
    assert entity.metadata["symbol_registry_fingerprint"] == _registry().fingerprint


def test_sld_node_builder_fails_closed_when_symbol_is_unmapped() -> None:
    snapshot = SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-42", nodes=(_node("unknown-symbol"),), connections=(),
    )
    with pytest.raises(KeyError, match="unknown-symbol"):
        SLDNodeEntityBuilder(_registry()).build(snapshot, profile=SymbolProfile.ANSI_IEEE)


def test_sld_node_builder_rejects_missing_persisted_symbol() -> None:
    node = SLDNodeExportSnapshot(
        node_id="node-1", equipment_id=None, x=0, y=0, properties={}, symbol=None,
    )
    snapshot = SLDExportSnapshot(
        project_id="project-1", document_id="document-1",
        source_revision="revision-42", nodes=(node,), connections=(),
    )
    with pytest.raises(ValueError, match="no persisted symbol definition"):
        SLDNodeEntityBuilder(_registry()).build(snapshot, profile=SymbolProfile.ANSI_IEEE)
