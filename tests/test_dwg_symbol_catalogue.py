from dataclasses import replace

import pytest

from plugins.dwg_export import (
    DrawingEntity,
    SymbolDefinition,
    SymbolProfile,
    SymbolReviewEvidence,
    build_reviewed_symbol_registry,
    symbol_geometry_digest,
    convert_sld_catalogue_primitives,
    SymbolCatalogueEntry,
    assemble_unreviewed_symbol_definitions,
)


def _reviewed_definition(symbol_id="breaker", profile=SymbolProfile.ANSI_IEEE, radius=5):
    primitive = DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": radius}, {})
    evidence_values = {
        "reviewer": "engineering-reviewer",
        "reviewed_on": "2026-11-04",
        "review_reference": f"review-record:{profile.value}:{symbol_id}",
        "standard_reference": f"authority:standard:edition#symbol-{symbol_id}",
    }
    definition = SymbolDefinition(
        symbol_id, profile, (primitive,), base_point=(0, 0),
        definition_version="1.0.0", review_status="reviewed",
        reviewer=evidence_values["reviewer"],
        reviewed_on=evidence_values["reviewed_on"],
        review_reference=evidence_values["review_reference"],
        standard_reference=evidence_values["standard_reference"],
    )
    evidence = SymbolReviewEvidence(
        profile=profile,
        symbol_id=symbol_id,
        standard_authority="authority",
        standard_id="standard",
        edition="edition",
        symbol_reference=f"symbol-{symbol_id}",
        source_uri="https://standards.example.invalid/source",
        license_reference="license-review:approved-source",
        reviewer=evidence_values["reviewer"],
        reviewed_on=evidence_values["reviewed_on"],
        review_reference=evidence_values["review_reference"],
        geometry_sha256=symbol_geometry_digest(definition),
    )
    return definition, evidence


def test_review_evidence_binds_profile_id_and_geometry():
    definition, evidence = _reviewed_definition()
    registry = build_reviewed_symbol_registry(
        (definition,), (evidence,), required_symbol_ids=("breaker",), require_complete=False,
    )
    assert registry.resolve("breaker", SymbolProfile.ANSI_IEEE) is definition


def test_review_evidence_rejects_geometry_drift():
    definition, evidence = _reviewed_definition(radius=5)
    changed, _ = _reviewed_definition(radius=6)
    with pytest.raises(ValueError, match="geometry digest"):
        build_reviewed_symbol_registry(
            (changed,), (evidence,), required_symbol_ids=("breaker",), require_complete=False,
        )


def test_review_evidence_rejects_profile_or_reference_mismatch():
    definition, evidence = _reviewed_definition()
    wrong_profile = replace(evidence, profile=SymbolProfile.IEC_60617)
    with pytest.raises(ValueError, match="missing review evidence"):
        build_reviewed_symbol_registry(
            (definition,), (wrong_profile,), required_symbol_ids=("breaker",), require_complete=False,
        )


def test_complete_catalogue_requires_both_profiles_for_every_id():
    definition, evidence = _reviewed_definition()
    with pytest.raises(ValueError, match="incomplete symbol catalogue"):
        build_reviewed_symbol_registry(
            (definition,), (evidence,), required_symbol_ids=("breaker",), require_complete=True,
        )


def test_review_evidence_requires_explicit_source_and_license_provenance():
    definition, _ = _reviewed_definition()
    with pytest.raises(ValueError, match="source_uri"):
        SymbolReviewEvidence(
            profile=definition.profile,
            symbol_id=definition.symbol_id,
            standard_authority="authority",
            standard_id="standard",
            edition="edition",
            symbol_reference="symbol-breaker",
            source_uri="http://insecure.example.invalid/source",
            license_reference="license:approved",
            reviewer="reviewer",
            reviewed_on="2026-11-04",
            review_reference="review-record:1",
            geometry_sha256=symbol_geometry_digest(definition),
        )

def test_rectangle_primitive_becomes_closed_polyline_without_geometry_drift():
    entities = convert_sld_catalogue_primitives(
        "breaker",
        ({"kind": "rect", "x": -10, "y": -9, "width": 20, "height": 18},),
        source_units="mm",
    )
    assert len(entities) == 1
    assert entities[0].kind == "LWPOLYLINE"
    assert entities[0].geometry["closed"] is True
    assert entities[0].geometry["points"] == ((-10, -9), (10, -9), (10, 9), (-10, 9))
    assert entities[0].metadata["source_rectangle"] == {"x": -10, "y": -9, "width": 20, "height": 18}


def test_catalogue_conversion_rejects_unsupported_or_ambiguous_primitive():
    with pytest.raises(ValueError, match="unsupported SLD catalogue primitive"):
        convert_sld_catalogue_primitives("relay", ({"kind": "bezier", "points": []},), source_units="mm")


def test_terminal_anchors_are_part_of_reviewed_geometry_digest():
    primitive = DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {})
    first = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE, (primitive,), terminal_anchors={"from": (-1, 0)},
    )
    changed = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE, (primitive,), terminal_anchors={"from": (-2, 0)},
    )
    assert symbol_geometry_digest(first) != symbol_geometry_digest(changed)

def test_catalogue_conversion_requires_explicit_millimetre_source_units():
    with pytest.raises(ValueError, match="source_units must be explicitly declared as mm"):
        convert_sld_catalogue_primitives(
            "breaker",
            ({"kind": "rect", "x": 0, "y": 0, "width": 2, "height": 3},),
            source_units="sld-unit",
        )


def test_catalogue_conversion_rejects_text_without_proven_alignment_mapping():
    with pytest.raises(ValueError, match="unsupported SLD catalogue primitive"):
        convert_sld_catalogue_primitives(
            "generator",
            ({"kind": "text", "x": -10, "y": -9, "width": 20, "height": 18, "text": "G"},),
            source_units="mm",
        )

def test_detached_catalogue_entry_assembles_unreviewed_mm_definition_with_mapping_provenance():
    entry = SymbolCatalogueEntry(
        symbol_id="breaker",
        profile=SymbolProfile.ANSI_IEEE,
        source_units="logical-unit",
        unit_to_mm=2.0,
        unit_mapping_reference="engineering-unit-review:U-17",
        primitives=({"kind": "rect", "x": -5, "y": -4, "width": 10, "height": 8},),
        base_point=(1, 2),
        terminal_anchors={"from": (-5, 0), "to": (5, 0)},
    )
    (definition,) = assemble_unreviewed_symbol_definitions((entry,))
    assert definition.review_status == "unreviewed"
    assert definition.units == "mm"
    assert definition.source_units == "logical-unit"
    assert definition.unit_to_mm == 2.0
    assert definition.unit_mapping_reference == "engineering-unit-review:U-17"
    assert definition.base_point == (2.0, 4.0)
    assert definition.terminal_anchors == {"from": (-10.0, 0.0), "to": (10.0, 0.0)}
    assert definition.primitives[0].geometry["points"] == ((-10, -8), (10, -8), (10, 8), (-10, 8))


def test_detached_catalogue_assembly_rejects_missing_unit_mapping_evidence():
    with pytest.raises(ValueError, match="reviewed unit mapping reference"):
        SymbolCatalogueEntry(
            symbol_id="breaker",
            profile=SymbolProfile.ANSI_IEEE,
            source_units="logical-unit",
            primitives=({"kind": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 0},),
            base_point=(0, 0),
            terminal_anchors={},
        )


def test_detached_catalogue_assembly_rejects_profile_duplicate():
    entry = SymbolCatalogueEntry(
        symbol_id="breaker",
        profile=SymbolProfile.ANSI_IEEE,
        source_units="mm",
        primitives=({"kind": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 0},),
        base_point=(0, 0),
        terminal_anchors={},
    )
    with pytest.raises(ValueError, match="duplicate catalogue entry"):
        assemble_unreviewed_symbol_definitions((entry, entry))
