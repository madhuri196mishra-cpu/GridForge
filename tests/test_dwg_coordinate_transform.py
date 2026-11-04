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
