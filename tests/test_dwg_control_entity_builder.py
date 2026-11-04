from __future__ import annotations

import pytest

from core.application.services.control_service import (
    ControlComponentReadModel,
    ControlConnectionReadModel,
    ControlProgramReadModel,
    LadderRungReadModel,
)
from plugins.dwg_export import (
    ControlDrawingEntityBuilder,
    ControlDrawingLayout,
    DrawingEntity,
    SymbolDefinition,
    SymbolProfile,
    SymbolRegistry,
)


def _registry(*, omit_target_input: bool = False) -> SymbolRegistry:
    primitive = DrawingEntity("body", "LINE", "SYMBOL", {"start": (-8, 0), "end": (8, 0)}, {})
    contact_anchors = {"output:out": (8, 0)}
    if not omit_target_input:
        contact_anchors["input:in"] = (-8, 0)
    contact = SymbolDefinition(
        symbol_id="control.contact",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,),
        review_status="reviewed",
        reviewer="control-symbol-review",
        reviewed_on="2026-11-04",
        review_reference="review-record:control-contact",
        standard_reference="standard-reference:control-contact",
        terminal_anchors=contact_anchors,
    )
    coil = SymbolDefinition(
        symbol_id="control.coil",
        profile=SymbolProfile.ANSI_IEEE,
        primitives=(primitive,),
        review_status="reviewed",
        reviewer="control-symbol-review",
        reviewed_on="2026-11-04",
        review_reference="review-record:control-coil",
        standard_reference="standard-reference:control-coil",
        terminal_anchors={"input:in": (-8, 0)},
    )
    return SymbolRegistry((contact, coil))


def _read_model() -> ControlProgramReadModel:
    return ControlProgramReadModel(
        project_id="project-1",
        program_id="ladder-main",
        schema_version=1,
        rungs=(
            LadderRungReadModel("rung-1", 0, True, ("contact-1", "coil-1"), {"contact-1": 0, "coil-1": 2}),
        ),
        components=(
            ControlComponentReadModel(
                "contact-1", "normally_open_contact", (), ("out",), {}, {},
                rung_id="rung-1", position=0,
            ),
            ControlComponentReadModel(
                "coil-1", "coil", ("in",), (), {}, {},
                rung_id="rung-1", position=2,
            ),
        ),
        connections=(ControlConnectionReadModel("contact-1", "out", "coil-1", "in"),),
        dependencies=(),
    )


def test_control_builder_uses_persisted_rung_positions_and_signal_connections() -> None:
    builder = ControlDrawingEntityBuilder(
        _registry(),
        symbol_ids={"normally_open_contact": "control.contact", "coil": "control.coil"},
        layout=ControlDrawingLayout(origin=(10, 20), component_pitch_mm=100, rung_pitch_mm=80),
    )

    entities = builder.build(_read_model(), SymbolProfile.ANSI_IEEE)
    by_id = {entity.entity_id: entity for entity in entities}

    assert by_id["control-component:contact-1"].geometry["insert"] == (10.0, 20.0)
    assert by_id["control-component:coil-1"].geometry["insert"] == (210.0, 20.0)
    assert by_id["control-rung:rung-1"].geometry == {"start": (10.0, 20.0), "end": (910.0, 20.0)}
    connection = by_id["control-connection:contact-1:out:coil-1:in"]
    assert connection.geometry["points"] == ((18.0, 20.0), (202.0, 20.0))
    assert connection.metadata["route_geometry_source"] == "logical_connection_display_chord"
    assert connection.metadata["source_kind"] == "persisted_control_signal_connection"
    assert all(entity.layer.startswith("CONTROL_") for entity in entities)


def test_control_builder_fails_closed_for_unmapped_component_type() -> None:
    builder = ControlDrawingEntityBuilder(
        _registry(),
        symbol_ids={"coil": "control.coil"},
    )
    with pytest.raises(ValueError, match="No explicit Control symbol mapping"):
        builder.build(_read_model(), SymbolProfile.ANSI_IEEE)


def test_control_builder_fails_closed_when_symbol_lacks_port_anchor() -> None:
    builder = ControlDrawingEntityBuilder(
        _registry(omit_target_input=True),
        symbol_ids={"normally_open_contact": "control.contact", "coil": "control.coil"},
    )
    with pytest.raises(ValueError, match="lacks required port anchors"):
        builder.build(_read_model(), SymbolProfile.ANSI_IEEE)


def test_control_layout_rejects_non_positive_or_non_finite_dimensions() -> None:
    with pytest.raises(ValueError, match="component_pitch_mm"):
        ControlDrawingLayout(component_pitch_mm=0)
    with pytest.raises(ValueError, match="origin"):
        ControlDrawingLayout(origin=(0.0, float("nan")))
