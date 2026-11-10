from __future__ import annotations

from plugins.dwg_export import DrawingEntity, SLDCoordinateTransform


def _transform() -> SLDCoordinateTransform:
    return SLDCoordinateTransform(
        mapping_id="site-map",
        mapping_version="1.0.0",
        source_coordinate_system="sld-document-local",
        source_units="sld-unit",
        millimetres_per_source_unit=2.0,
        world_origin_x_mm=100.0,
        world_origin_y_mm=200.0,
        rotation_degrees=30.0,
        reviewer="reviewer",
        reviewed_on="2026-11-04",
        review_reference="review:site-map",
    )


def test_arc_rotation_normalizes_angles_across_zero_without_reversing_sweep() -> None:
    arc = DrawingEntity(
        "arc-1",
        "ARC",
        "SYMBOL",
        {"center": (1.0, 2.0), "radius": 3.0, "start_angle": 350.0, "end_angle": 10.0},
        {},
    )

    transformed = _transform().transform_entities((arc,))[0]

    assert transformed.geometry["start_angle"] == 20.0
    assert transformed.geometry["end_angle"] == 40.0
    assert transformed.geometry["radius"] == 6.0


def test_polyline_orientation_is_preserved_by_positive_uniform_mapping() -> None:
    polyline = DrawingEntity(
        "polyline-1",
        "LWPOLYLINE",
        "WIRE",
        {"points": ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)), "closed": True},
        {},
    )

    transformed = _transform().transform_entities((polyline,))[0]
    points = transformed.geometry["points"]

    def signed_area(vertices):
        return sum(
            vertices[index][0] * vertices[(index + 1) % len(vertices)][1]
            - vertices[(index + 1) % len(vertices)][0] * vertices[index][1]
            for index in range(len(vertices))
        ) / 2.0

    assert signed_area(points) > 0.0
    assert abs(signed_area(points)) == 8.0
    assert transformed.geometry["closed"] is True


def test_insert_transform_maps_only_instance_position_and_rotation() -> None:
    anchors = {"from": (-28.0, 0.0), "to": (28.0, 0.0)}
    insert = DrawingEntity(
        "insert-1",
        "INSERT",
        "SLD_SYMBOLS",
        {
            "symbol_id": "breaker",
            "insert": (10.0, 20.0),
            "scale": (1.5, 0.75, 1.0),
            "rotation": 15.0,
        },
        {
            "symbol_definition_base_point": (2.0, 3.0),
            "symbol_terminal_anchors": anchors,
            "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
            "symbol_base_point_offset_mm": (2.0, 3.0),
            "symbol_base_point_offset_coordinate_system": "sld-symbol-instance-mm-vector",
            "symbol_terminal_anchor_offsets_mm": {
                "from": (-3.0, -4.0), "to": (5.0, 6.0),
            },
            "symbol_terminal_anchor_offsets_coordinate_system": "sld-symbol-instance-mm-vector",
        },
    )

    transformed = _transform().transform_entities((insert,))[0]

    world_node = _transform().transform_point((10.0, 20.0))
    world_base_offset = _transform().transform_vector((2.0, 3.0))
    assert transformed.geometry["insert"] == (
        world_node[0] + world_base_offset[0],
        world_node[1] + world_base_offset[1],
    )
    assert transformed.geometry["scale"] == (1.5, 0.75, 1.0)
    assert transformed.geometry["rotation"] == 45.0
    assert transformed.metadata["symbol_definition_base_point"] == (2.0, 3.0)
    assert transformed.metadata["symbol_terminal_anchors"] == anchors
    assert transformed.metadata["symbol_terminal_anchor_coordinate_system"] == "symbol_local_mm"
    assert transformed.metadata["symbol_terminal_anchor_offsets_mm"] == {
        "from": (-3.0, -4.0), "to": (5.0, 6.0),
    }
    assert transformed.metadata["symbol_terminal_anchor_positions"] == {
        name: (
            world_node[0] + _transform().transform_vector(offset)[0],
            world_node[1] + _transform().transform_vector(offset)[1],
        )
        for name, offset in {"from": (-3.0, -4.0), "to": (5.0, 6.0)}.items()
    }
    assert transformed.metadata["symbol_terminal_anchor_positions_coordinate_system"] == "world-mm"
    assert transformed.metadata["symbol_base_point_offset_world_mm"] == world_base_offset
    assert transformed.metadata["coordinate_transform"]["target_units"] == "mm"


def test_polyline_terminal_anchor_offsets_are_rotated_but_not_document_scaled() -> None:
    polyline = DrawingEntity(
        "wire-anchors", "LWPOLYLINE", "WIRE",
        {"points": ((10.0, 20.0), (40.0, 20.0), (100.0, 50.0))},
        {
            "source_terminal_anchor_offset_mm": (3.0, 4.0),
            "source_terminal_anchor_offset_coordinate_system": "sld-symbol-instance-mm-vector",
            "target_terminal_anchor_offset_mm": (-1.0, 2.0),
            "target_terminal_anchor_offset_coordinate_system": "sld-symbol-instance-mm-vector",
        },
    )

    transformed = _transform().transform_entities((polyline,))[0]
    source_node = _transform().transform_point((10.0, 20.0))
    source_offset = _transform().transform_vector((3.0, 4.0))
    target_node = _transform().transform_point((100.0, 50.0))
    target_offset = _transform().transform_vector((-1.0, 2.0))

    assert transformed.geometry["points"][0] == (
        source_node[0] + source_offset[0],
        source_node[1] + source_offset[1],
    )
    assert transformed.geometry["points"][1] == _transform().transform_point((40.0, 20.0))
    assert transformed.geometry["points"][-1] == (
        target_node[0] + target_offset[0],
        target_node[1] + target_offset[1],
    )
    assert transformed.metadata["source_terminal_anchor_position_world_mm"] == transformed.geometry["points"][0]
    assert transformed.metadata["target_terminal_anchor_position_world_mm"] == transformed.geometry["points"][-1]
    assert transformed.metadata["terminal_anchor_positions_coordinate_system"] == "world-mm"


def test_equipment_route_endpoints_follow_transformed_terminal_anchors() -> None:
    route = DrawingEntity(
        "wire-anchors",
        "LWPOLYLINE",
        "SLD_CONNECTIONS",
        {"points": ((10.0, 20.0), (15.0, 25.0), (30.0, 40.0)), "closed": False},
        {
            "source_terminal_anchor_offset_mm": (0.0, -15.0),
            "source_terminal_anchor_offset_mm_coordinate_system": "sld-symbol-instance-mm-vector",
            "target_terminal_anchor_offset_mm": (20.0, 0.0),
            "target_terminal_anchor_offset_mm_coordinate_system": "sld-symbol-instance-mm-vector",
        },
    )

    transformed = _transform().transform_entities((route,))[0]
    points = transformed.geometry["points"]
    world_source = _transform().transform_point((10.0, 20.0))
    world_target = _transform().transform_point((30.0, 40.0))
    source_offset = _transform().transform_vector((0.0, -15.0))
    target_offset = _transform().transform_vector((20.0, 0.0))

    assert points[0] == (
        world_source[0] + source_offset[0],
        world_source[1] + source_offset[1],
    )
    assert points[1] == _transform().transform_point((15.0, 25.0))
    assert points[-1] == (
        world_target[0] + target_offset[0],
        world_target[1] + target_offset[1],
    )
    assert transformed.metadata["source_terminal_anchor_position_world_mm"] == points[0]
    assert transformed.metadata["target_terminal_anchor_position_world_mm"] == points[-1]
    assert transformed.metadata["terminal_anchor_positions_coordinate_system"] == "world-mm"


def test_equipment_route_rejects_ambiguous_terminal_anchor_offset_coordinates() -> None:
    route = DrawingEntity(
        "wire-ambiguous-anchor",
        "LWPOLYLINE",
        "SLD_CONNECTIONS",
        {"points": ((0.0, 0.0), (10.0, 10.0)), "closed": False},
        {
            "source_terminal_anchor_offset_mm": (0.0, 5.0),
            "source_terminal_anchor_offset_mm_coordinate_system": "sld-document-local",
        },
    )

    import pytest

    with pytest.raises(ValueError, match="unsupported coordinate system"):
        _transform().transform_entities((route,))
