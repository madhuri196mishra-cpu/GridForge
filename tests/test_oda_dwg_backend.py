from pathlib import Path

import pytest

from plugins.dwg_export.backends import ODAFileConverterBackend


def test_oda_backend_requires_explicit_existing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ODAFileConverterBackend(tmp_path / "missing-converter")


def test_oda_backend_rejects_non_executable_on_posix(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("not a real converter", encoding="utf-8")
    executable.chmod(0o600)
    if __import__("os").name != "nt":
        with pytest.raises(PermissionError):
            ODAFileConverterBackend(executable)


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_oda_backend_rejects_non_finite_or_non_positive_timeout(tmp_path: Path, timeout_seconds: float) -> None:
    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(ValueError, match="finite positive"):
        ODAFileConverterBackend(executable, timeout_seconds=timeout_seconds)


def test_oda_backend_rejects_boolean_timeout(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(TypeError, match="finite positive"):
        ODAFileConverterBackend(executable, timeout_seconds=True)


def test_oda_backend_rejects_non_numeric_timeout(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(TypeError, match="finite positive"):
        ODAFileConverterBackend(executable, timeout_seconds="120")  # type: ignore[arg-type]


def test_oda_backend_rejects_unsupported_target_version_before_conversion(tmp_path: Path) -> None:
    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    plan = DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(),
    )
    with pytest.raises(ValueError, match="Unsupported ODA target version"):
        backend.export(plan, tmp_path / "drawing.dwg", target_version="ACAD2030")


def test_oda_backend_rejects_non_dwg_destination_without_export_service(tmp_path: Path) -> None:
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    plan = DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(),
    )

    with pytest.raises(ValueError, match="extension must be .dwg"):
        backend.export(plan, tmp_path / "drawing.dxf")

    assert not (tmp_path / "drawing.dxf").exists()


def test_oda_backend_validates_plan_when_called_without_export_service(tmp_path: Path) -> None:
    from plugins.dwg_export import DrawingEntity, DrawingPlan, SymbolProfile
    from plugins.dwg_export.validation import DrawingPlanValidationError

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    invalid = DrawingEntity(
        "invalid-line", "LINE", "SLD", {"start": [0, 0], "end": [0, 0]}, {},
    )
    plan = DrawingPlan(
        project_id="p", export_id="e", source_revision="r",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",), entities=(invalid,),
    )

    with pytest.raises(DrawingPlanValidationError, match="distinct endpoints"):
        backend.export(plan, tmp_path / "drawing.dwg")

    assert not (tmp_path / "drawing.dwg").exists()



def test_oda_backend_rejects_registry_content_drift_even_when_versions_match(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, DrawingPlan, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    builder_definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="reviewer", reviewed_on="2026-11-03", review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    backend_definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 6}, {}),),
        definition_version="1.0.0", review_status="reviewed",
        reviewer="reviewer", reviewed_on="2026-11-03", review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    builder_registry = SymbolRegistry((builder_definition,), catalogue_version="1.0.0")
    backend_registry = SymbolRegistry((backend_definition,), catalogue_version="1.0.0")
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]},
        {
            "symbol_profile": SymbolProfile.ANSI_IEEE.value,
            "symbol_definition_id": "breaker",
            "symbol_definition_version": "1.0.0",
            "symbol_definition_units": "mm",
            "symbol_definition_base_point": (0.0, 0.0),
            "symbol_source_units": builder_definition.source_units,
            "symbol_unit_to_mm": builder_definition.unit_to_mm,
            "symbol_unit_mapping_reference": builder_definition.unit_mapping_reference,
            "symbol_terminal_anchors": dict(builder_definition.terminal_anchors),
            "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
            "symbol_review_status": "reviewed",
            "symbol_reviewer": "reviewer",
            "symbol_reviewed_on": "2026-11-03",
            "symbol_review_reference": "review:1",
            "symbol_standard_reference": "standard-reference:IEEE-reviewed-source",
            "symbol_registry_schema_version": 1,
            "symbol_catalogue_version": "1.0.0",
            "symbol_registry_fingerprint": builder_registry.fingerprint,
        },
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=backend_registry)
    with pytest.raises(ValueError, match="registry fingerprint"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)



def test_oda_backend_rejects_insert_without_registry_provenance(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        review_status="reviewed", reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=SymbolRegistry((definition,)))
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]}, {},
    )
    with pytest.raises(ValueError, match="missing required symbol registry provenance"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)


def test_oda_backend_rejects_symbol_base_point_provenance_drift(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        base_point=(2.0, 3.0), review_status="reviewed", reviewer="reviewer",
        reviewed_on="2026-11-03", review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    registry = SymbolRegistry((definition,))
    metadata = {
        "symbol_profile": SymbolProfile.ANSI_IEEE.value,
        "symbol_definition_id": definition.symbol_id,
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": (0.0, 0.0),
        "symbol_source_units": definition.source_units,
        "symbol_unit_to_mm": definition.unit_to_mm,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": dict(definition.terminal_anchors),
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": definition.standard_reference,
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]}, metadata,
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    with pytest.raises(ValueError, match="symbol_definition_base_point"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)



def test_oda_backend_rejects_symbol_definition_id_drift(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        review_status="reviewed", reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    registry = SymbolRegistry((definition,))
    metadata = {
        "symbol_profile": SymbolProfile.ANSI_IEEE.value,
        "symbol_definition_id": "disconnect-switch",
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": definition.base_point,
        "symbol_source_units": definition.source_units,
        "symbol_unit_to_mm": definition.unit_to_mm,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": dict(definition.terminal_anchors),
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": definition.standard_reference,
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]}, metadata,
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    with pytest.raises(ValueError, match="symbol_definition_id"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)


def test_oda_backend_rejects_standard_reference_provenance_drift(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "CIRCLE", "SYMBOL", {"center": [0, 0], "radius": 5}, {}),),
        review_status="reviewed", reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:1", standard_reference="standard-reference:IEEE-reviewed-source",
    )
    registry = SymbolRegistry((definition,))
    metadata = {
        "symbol_profile": SymbolProfile.ANSI_IEEE.value,
        "symbol_definition_id": definition.symbol_id,
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": definition.base_point,
        "symbol_source_units": definition.source_units,
        "symbol_unit_to_mm": definition.unit_to_mm,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": dict(definition.terminal_anchors),
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": "different-standard-reference",
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:n1", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": "breaker", "insert": [0, 0]}, metadata,
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    with pytest.raises(ValueError, match="symbol_standard_reference"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)


def test_oda_backend_rejects_source_unit_mapping_provenance_drift(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "inch-source-symbol", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "LINE", "SYMBOL", {"start": (0, 0), "end": (25.4, 0)}, {}),),
        source_units="inch", unit_to_mm=25.4,
        unit_mapping_reference="reviewed-unit-map:inch-to-mm",
        review_status="reviewed", reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:unit-map", standard_reference="standard-reference:reviewed-symbol",
    )
    registry = SymbolRegistry((definition,))
    metadata = {
        "symbol_profile": definition.profile.value,
        "symbol_definition_id": definition.symbol_id,
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": definition.base_point,
        "symbol_source_units": "inch",
        "symbol_unit_to_mm": 1.0,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": {},
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": definition.standard_reference,
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:unit-drift", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": definition.symbol_id, "insert": (0, 0)}, metadata,
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    with pytest.raises(ValueError, match="symbol_unit_to_mm"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)


def test_oda_backend_rejects_terminal_anchor_provenance_drift(tmp_path: Path):
    from plugins.dwg_export import DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    definition = SymbolDefinition(
        "breaker", SymbolProfile.ANSI_IEEE,
        (DrawingEntity("body", "LINE", "SYMBOL", {"start": (0, 0), "end": (1, 0)}, {}),),
        terminal_anchors={"from": (0, 0), "to": (1, 0)},
        review_status="reviewed", reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:anchors", standard_reference="standard-reference:reviewed-symbol",
    )
    registry = SymbolRegistry((definition,))
    metadata = {
        "symbol_profile": definition.profile.value,
        "symbol_definition_id": definition.symbol_id,
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": definition.base_point,
        "symbol_source_units": definition.source_units,
        "symbol_unit_to_mm": definition.unit_to_mm,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": {"from": (0.0, 0.0), "to": (2.0, 0.0)},
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": definition.standard_reference,
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:anchor-drift", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": definition.symbol_id, "insert": (0, 0)}, metadata,
    )
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    with pytest.raises(ValueError, match="symbol_terminal_anchors"):
        backend._add_insert(type("Doc", (), {})(), entity, set(), SymbolProfile.ANSI_IEEE)


def test_oda_insert_preserves_local_base_point_and_instance_transform(tmp_path: Path):
    from plugins.dwg_export import (
        DrawingEntity, SymbolDefinition, SymbolProfile, SymbolRegistry,
    )

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    primitive = DrawingEntity(
        "body", "LINE", "SYMBOL", {"start": (2, 3), "end": (12, 3)}, {},
    )
    definition = SymbolDefinition(
        "offset-symbol", SymbolProfile.ANSI_IEEE, (primitive,),
        base_point=(2.0, 3.0), review_status="reviewed",
        reviewer="reviewer", reviewed_on="2026-11-03",
        review_reference="review:offset-symbol",
        standard_reference="standard-reference:reviewed-symbol",
    )
    registry = SymbolRegistry((definition,))

    class _Block:
        def __init__(self, name, base_point):
            self.name = name
            self.base_point = base_point
            self.entities = []

        def add_line(self, start, end, dxfattribs):
            self.entities.append((start, end, dxfattribs))
            return self.entities[-1]

    class _Blocks:
        def __init__(self):
            self.created = []

        def new(self, name, base_point):
            block = _Block(name, base_point)
            self.created.append(block)
            return block

    class _Modelspace:
        def __init__(self):
            self.insert = None

        def add_blockref(self, name, insert, dxfattribs):
            self.insert = (name, insert, dxfattribs)
            return self.insert

    class _Layers(set):
        def new(self, name):
            self.add(name)

    class _Doc:
        def __init__(self):
            self.blocks = _Blocks()
            self._modelspace = _Modelspace()
            self.layers = _Layers()

        def modelspace(self):
            return self._modelspace

    doc = _Doc()
    backend = ODAFileConverterBackend(executable, symbol_registry=registry)
    metadata = {
        "symbol_profile": SymbolProfile.ANSI_IEEE.value,
        "symbol_definition_id": definition.symbol_id,
        "symbol_definition_version": definition.definition_version,
        "symbol_definition_units": definition.units,
        "symbol_definition_base_point": definition.base_point,
        "symbol_source_units": definition.source_units,
        "symbol_unit_to_mm": definition.unit_to_mm,
        "symbol_unit_mapping_reference": definition.unit_mapping_reference,
        "symbol_terminal_anchors": dict(definition.terminal_anchors),
        "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
        "symbol_review_status": definition.review_status,
        "symbol_reviewer": definition.reviewer,
        "symbol_reviewed_on": definition.reviewed_on,
        "symbol_review_reference": definition.review_reference,
        "symbol_standard_reference": definition.standard_reference,
        "symbol_registry_schema_version": registry.schema_version,
        "symbol_catalogue_version": registry.catalogue_version,
        "symbol_registry_fingerprint": registry.fingerprint,
    }
    entity = DrawingEntity(
        "sld-node:offset", "INSERT", "SLD_SYMBOLS",
        {"symbol_id": definition.symbol_id, "insert": (100, 200),
         "scale": (2.0, 0.5, 1.0), "rotation": 90.0},
        metadata,
    )

    backend._add_insert(doc, entity, set(), SymbolProfile.ANSI_IEEE)

    block = doc.blocks.created[0]
    assert block.base_point == (2.0, 3.0)
    assert block.entities[0][0:2] == ((2, 3), (12, 3))
    name, insert, attributes = doc._modelspace.insert
    assert name == definition.block_name
    assert insert == (100, 200)
    assert attributes["xscale"] == 2.0
    assert attributes["yscale"] == 0.5
    assert attributes["zscale"] == 1.0
    assert attributes["rotation"] == 90.0
