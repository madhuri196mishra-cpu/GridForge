import pytest

from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry


def _definition(symbol_id="breaker", profile=SymbolProfile.ANSI_IEEE):
    primitive = DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {})
    return SymbolDefinition(
        symbol_id, profile, (primitive,), base_point=(0, 0),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="gridforge-review", reviewed_on="2026-11-03",
        review_reference="review-record:GF-SYMBOL-001",
    )


def test_registry_resolves_by_symbol_id_and_profile():
    ansi = _definition()
    iec = _definition(profile=SymbolProfile.IEC_60617)
    registry = SymbolRegistry((ansi, iec))
    assert registry.resolve("breaker", SymbolProfile.ANSI_IEEE) is ansi
    assert registry.resolve("breaker", SymbolProfile.IEC_60617) is iec
    assert ansi.block_name != iec.block_name


def test_block_names_are_stable_and_safe_for_cad_names():
    first = _definition("breaker/main")
    second = _definition("breaker/main")
    assert first.block_name == second.block_name
    assert first.block_name.startswith("GF_ANSI_IEEE_")


def test_registry_rejects_duplicate_profile_symbol_pairs():
    with pytest.raises(ValueError, match="duplicate symbol"):
        SymbolRegistry((_definition(), _definition()))


def test_registry_fails_closed_for_unregistered_profile():
    registry = SymbolRegistry((_definition(),))
    with pytest.raises(KeyError, match="no symbol definition"):
        registry.resolve("breaker", SymbolProfile.IEC_60617)


def test_symbol_definition_rejects_nested_insert_or_unknown_primitives():
    bad = DrawingEntity("nested", "INSERT", "SYMBOL", {"symbol_id": "other", "insert": [0, 0]}, {})
    with pytest.raises(ValueError, match="only LINE"):
        SymbolDefinition("composite", SymbolProfile.ANSI_IEEE, (bad,))


def test_symbol_definition_requires_finite_two_dimensional_base_point():
    primitive = DrawingEntity("body", "LINE", "SYMBOL", {"start": [0, 0], "end": [1, 1]}, {})
    with pytest.raises(ValueError, match="base_point"):
        SymbolDefinition("breaker", SymbolProfile.ANSI_IEEE, (primitive,), base_point=(0, float("inf")))


def test_symbol_definition_rejects_invalid_primitive_geometry() -> None:
    invalid_circle = DrawingEntity(
        "body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 0}, {},
    )
    with pytest.raises(ValueError, match="radius must be positive"):
        SymbolDefinition("invalid-circle", SymbolProfile.ANSI_IEEE, (invalid_circle,))



def test_registry_exposes_explicit_schema_and_catalogue_versions():
    registry = SymbolRegistry((_definition(),), schema_version=1, catalogue_version="2.3.0")
    assert registry.schema_version == 1
    assert registry.catalogue_version == "2.3.0"


def test_registry_rejects_unreviewed_definitions_at_resolution():
    definition = SymbolDefinition(
        "draft-breaker", SymbolProfile.ANSI_IEEE,
        (_definition().primitives[0],),
    )
    registry = SymbolRegistry((definition,))
    with pytest.raises(ValueError, match="not reviewed"):
        registry.resolve("draft-breaker", SymbolProfile.ANSI_IEEE)


def test_reviewed_definition_requires_complete_review_provenance():
    with pytest.raises(ValueError, match="require reviewer"):
        SymbolDefinition(
            "incomplete-review", SymbolProfile.ANSI_IEEE,
            (_definition().primitives[0],), review_status="reviewed",
        )


def test_definition_version_changes_stable_block_identity():
    first = _definition()
    second = SymbolDefinition(
        first.symbol_id, first.profile, first.primitives,
        definition_version="2.0.0", review_status="reviewed",
        reviewer="gridforge-review", reviewed_on="2026-11-03",
        review_reference="review-record:GF-SYMBOL-002",
    )
    assert first.block_name != second.block_name


def test_registry_rejects_unknown_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        SymbolRegistry((), schema_version=2)



def test_registry_fingerprint_is_deterministic_and_tracks_definition_content():
    first = SymbolRegistry((_definition(),), catalogue_version="1.0.0")
    equivalent = SymbolRegistry((_definition(),), catalogue_version="1.0.0")
    changed = SymbolRegistry((SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 6}, {}),),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="gridforge-review", reviewed_on="2026-11-03",
        review_reference="review-record:GF-SYMBOL-001",
    ),), catalogue_version="1.0.0")
    assert first.fingerprint == equivalent.fingerprint
    assert first.fingerprint != changed.fingerprint
