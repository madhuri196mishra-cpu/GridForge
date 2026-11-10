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
            "symbol_terminal_anchor_positions": {
                "from": (10.0, 20.0), "to": (12.0, 24.0),
            },
            "symbol_terminal_anchor_positions_coordinate_system": "sld-document-local",
        },
    )

    transformed = _transform().transform_entities((insert,))[0]

    assert transformed.geometry["insert"] == (
        100.0 + 2.0 * (10.0 * 0.8660254037844387 - 20.0 * 0.5),
        200.0 + 2.0 * (10.0 * 0.5 + 20.0 * 0.8660254037844387),
    )
    assert transformed.geometry["scale"] == (1.5, 0.75, 1.0)
    assert transformed.geometry["rotation"] == 45.0
    assert transformed.metadata["symbol_definition_base_point"] == (2.0, 3.0)
    assert transformed.metadata["symbol_terminal_anchors"] == anchors
    assert transformed.metadata["symbol_terminal_anchor_coordinate_system"] == "symbol_local_mm"
    assert transformed.metadata["symbol_terminal_anchor_positions"] == {
        "from": _transform().transform_point((10.0, 20.0)),
        "to": _transform().transform_point((12.0, 24.0)),
    }
    assert transformed.metadata["symbol_terminal_anchor_positions_coordinate_system"] == "world-mm"
    assert transformed.metadata["coordinate_transform"]["target_units"] == "mm"
