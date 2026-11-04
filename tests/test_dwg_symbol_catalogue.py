from dataclasses import replace

import pytest

from plugins.dwg_export import (
    DrawingEntity,
    SymbolDefinition,
    SymbolProfile,
    SymbolReviewEvidence,
    build_reviewed_symbol_registry,
    symbol_geometry_digest,
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
