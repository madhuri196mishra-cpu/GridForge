import pytest

from plugins.dwg_export import DrawingEntity
from plugins.dwg_export.backends.oda_file_converter import ODAFileConverterBackend


class _AppIds:
    def __init__(self):
        self.values = set()

    def __contains__(self, value):
        return value in self.values

    def add(self, value):
        self.values.add(value)


class _Document:
    def __init__(self):
        self.appids = _AppIds()


class _CadEntity:
    def __init__(self):
        self.xdata = None

    def set_xdata(self, appid, tags):
        self.xdata = (appid, tags)


def test_xdata_chunks_respect_utf8_byte_limit_without_splitting_characters():
    payload = "电力设备" * 100
    chunks = ODAFileConverterBackend._utf8_xdata_chunks(payload)

    assert "".join(chunks) == payload
    assert all(len(chunk.encode("utf-8")) <= 240 for chunk in chunks)


def test_xdata_identity_attaches_unicode_metadata_in_safe_chunks():
    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity("equipment-1", "TEXT", "LABEL", {"text": "设备"}, {"label": "保护继电器" * 80})

    ODAFileConverterBackend._attach_identity(document, cad_entity, entity)

    assert cad_entity.xdata[0] == "GRIDFORGE"
    chunks = [value for code, value in cad_entity.xdata[1] if code == 1000]
    assert chunks[0] == "equipment-1"
    assert all(len(chunk.encode("utf-8")) <= 240 for chunk in chunks)
    assert "".join(chunks[1:]) == __import__("json").dumps(
        {"label": "保护继电器" * 80}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def test_xdata_rejects_entity_ids_that_exceed_safe_utf8_length():
    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity("继" * 100, "TEXT", "LABEL", {"text": "x"}, {})

    try:
        ODAFileConverterBackend._attach_identity(document, cad_entity, entity)
    except ValueError as exc:
        assert "safe DXF XDATA string length" in str(exc)
    else:
        raise AssertionError("oversized XDATA entity ID must be rejected")



def test_xdata_includes_top_level_plan_provenance():
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity("sld-node:n1", "TEXT", "LABEL", {"text": "x"}, {})
    plan = DrawingPlan(
        project_id="project-1", export_id="export-1", source_revision="revision-8",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(entity,), source_kind="sld_export_snapshot",
        source_id="document-9", source_document_id="document-9",
    )

    ODAFileConverterBackend._attach_identity(document, cad_entity, entity, plan)

    import json
    chunks = [value for code, value in cad_entity.xdata[1] if code == 1000][1:]
    metadata = json.loads("".join(chunks))
    assert metadata["source_project_id"] == "project-1"
    assert metadata["source_revision"] == "revision-8"
    assert metadata["source_kind"] == "sld_export_snapshot"
    assert metadata["source_document_id"] == "document-9"


def test_xdata_rejects_entity_metadata_conflicting_with_plan_provenance():
    from plugins.dwg_export import DrawingPlan, SymbolProfile
    import pytest

    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity(
        "sld-node:n1", "TEXT", "LABEL", {"text": "x"},
        {"source_project_id": "wrong-project"},
    )
    plan = DrawingPlan(
        project_id="project-1", export_id="export-1", source_revision="revision-8",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(entity,), source_kind="sld_export_snapshot",
        source_id="document-9", source_document_id="document-9",
    )
    with pytest.raises(ValueError, match="conflicts with drawing-plan provenance"):
        ODAFileConverterBackend._attach_identity(document, cad_entity, entity, plan)



def test_xdata_carries_deterministic_export_manifest():
    import json
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity("equipment-1", "TEXT", "LABEL", {"text": "x"}, {})
    plan = DrawingPlan(
        project_id="project-1", export_id="export-42", source_revision="revision-8",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(entity,), source_kind="sld_export_snapshot",
        source_id="document-9", source_document_id="document-9",
    )
    manifest = {
        "manifest_schema_version": 1,
        "drawing_plan_sha256": "plan-digest",
        "symbol_registry_fingerprint": "registry-digest",
        "coordinate_mapping_fingerprint": "mapping-digest",
    }

    ODAFileConverterBackend._attach_identity(
        document, cad_entity, entity, plan, manifest,
    )

    chunks = [value for code, value in cad_entity.xdata[1] if code == 1000][1:]
    metadata = json.loads("".join(chunks))
    assert metadata["gridforge_export_manifest"] == manifest


def test_export_manifest_binds_plan_registry_and_single_coordinate_mapping(tmp_path):
    import hashlib
    import json
    from plugins.dwg_export import (
        DrawingEntity, DrawingPlan, SLDCoordinateTransform,
        SymbolProfile, SymbolRegistry,
    )
    from plugins.dwg_export.drawing_plan import serialize_drawing_plan

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    transform = SLDCoordinateTransform(
        mapping_id="site-map", mapping_version="2.1.0",
        source_coordinate_system="sld-document-local", source_units="sld-unit",
        millimetres_per_source_unit=2.0, world_origin_x_mm=100.0,
        world_origin_y_mm=200.0, rotation_degrees=30.0,
        reviewer="reviewer", reviewed_on="2026-11-04",
        review_reference="review:site-map",
    )
    source = DrawingEntity(
        "sld-connection:c1", "LWPOLYLINE", "WIRE",
        {"points": ((0.0, 0.0), (1.0, 1.0))},
        {},
    )
    transformed = transform.transform_entities((source,))[0]
    plan = DrawingPlan(
        project_id="project-1", export_id="export-42", source_revision="revision-8",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(transformed,), source_kind="sld_export_snapshot",
        source_id="document-9", source_document_id="document-9",
        coordinate_system="world", units="mm",
    )
    registry = SymbolRegistry()
    manifest = backend._build_export_manifest(plan, registry, "ACAD2018")
    mapping_json = json.dumps(
        dict(transformed.metadata["coordinate_transform"]),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    assert manifest["drawing_plan_sha256"] == hashlib.sha256(
        serialize_drawing_plan(plan).encode("utf-8")
    ).hexdigest()
    assert manifest["symbol_registry_fingerprint"] == registry.fingerprint
    assert manifest["coordinate_mapping_fingerprint"] == hashlib.sha256(
        mapping_json.encode("utf-8")
    ).hexdigest()
    assert manifest["coordinate_mapping"]["mapping_id"] == "site-map"
    assert manifest["target_version"] == "ACAD2018"


def test_export_manifest_rejects_mixed_coordinate_mapping_provenance(tmp_path):
    from plugins.dwg_export import DrawingEntity, DrawingPlan, SymbolProfile, SymbolRegistry

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    def mapping(mapping_id):
        return {
            "mapping_id": mapping_id,
            "mapping_version": "1.0.0",
            "source_coordinate_system": "sld-document-local",
            "source_units": "sld-unit",
            "target_coordinate_system": "world",
            "target_units": "mm",
            "millimetres_per_source_unit": 1.0,
            "world_origin_mm": (0.0, 0.0),
            "rotation_degrees": 0.0,
            "reviewer": "reviewer",
            "reviewed_on": "2026-11-04",
            "review_reference": "review:coordinate-map",
        }

    first = DrawingEntity(
        "line-1", "LINE", "WIRE", {"start": (0, 0), "end": (1, 1)},
        {"coordinate_transform": mapping("map-a")},
    )
    second = DrawingEntity(
        "line-2", "LINE", "WIRE", {"start": (1, 1), "end": (2, 2)},
        {"coordinate_transform": mapping("map-b")},
    )
    plan = DrawingPlan(
        project_id="project-1", export_id="export-42", source_revision="revision-8",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(first, second), source_kind="sld_export_snapshot",
        source_id="document-9", source_document_id="document-9",
    )
    with pytest.raises(ValueError, match="conflicting coordinate mapping provenance"):
        backend._build_export_manifest(plan, SymbolRegistry(), "ACAD2018")
