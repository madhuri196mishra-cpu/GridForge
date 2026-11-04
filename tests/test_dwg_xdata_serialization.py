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


class _XRecord:
    def __init__(self):
        self.tags = []

    def extend(self, tags):
        self.tags.extend(tags)


class _Dictionary:
    def __init__(self):
        self.values = {}

    def __contains__(self, key):
        return key in self.values

    def get_required_dict(self, key):
        if key not in self.values:
            self.values[key] = _Dictionary()
        return self.values[key]

    def add_xrecord(self, key):
        record = _XRecord()
        self.values[key] = record
        return record

    def __getitem__(self, key):
        return self.values[key]


class _Document:
    def __init__(self):
        self.appids = _AppIds()
        self.rootdict = _Dictionary()


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


def test_xdata_rejects_payloads_that_exceed_safe_entity_limit():
    document = _Document()
    cad_entity = _CadEntity()
    entity = DrawingEntity(
        "equipment-1", "TEXT", "LABEL", {"text": "x"},
        {"large": "x" * 15001},
    )

    with pytest.raises(ValueError, match="15,000-byte"):
        ODAFileConverterBackend._attach_identity(document, cad_entity, entity)


def test_xrecord_chunks_preserve_unicode_and_stay_within_string_limit():
    payload = "电力设备" * 1200
    chunks = ODAFileConverterBackend._xrecord_chunks(payload)

    assert "".join(chunks) == payload
    assert all(len(chunk) <= 2000 for chunk in chunks)


def test_drawing_level_manifest_is_stored_even_for_empty_drawings():
    document = _Document()
    manifest = {
        "manifest_schema_version": 1,
        "drawing_plan_sha256": "empty-plan-digest",
        "entity_count": 0,
    }

    digest = ODAFileConverterBackend._attach_export_manifest(document, manifest)

    record = document.rootdict["GRIDFORGE"]["EXPORT_MANIFEST"]
    payload = "".join(value for code, value in record.tags if code == 1)
    assert payload == __import__("json").dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert digest == __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()


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
    expected_digest = __import__("hashlib").sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert metadata["gridforge_export_manifest_sha256"] == expected_digest
    assert "gridforge_export_manifest" not in metadata


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


def test_oda_roundtrip_verifier_checks_manifest_and_entity_xdata(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    plan = DrawingPlan(
        project_id="project-1", export_id="export-1", source_revision="revision-1",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(DrawingEntity("entity-1", "TEXT", "LABEL", {"text": "x"}, {}),),
    )
    manifest = backend._build_export_manifest(plan, backend._symbols, "ACAD2018")
    payload = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    class _Tag:
        def __init__(self, code, value):
            self.code = code
            self.value = value

    class _CadEntity:
        def get_xdata(self, appid):
            assert appid == "GRIDFORGE"
            return [
                _Tag(1000, "entity-1"),
                _Tag(1000, json.dumps({"gridforge_export_manifest_sha256": digest})),
            ]

    record = SimpleNamespace(tags=[_Tag(1, payload)])
    gridforge_dict = SimpleNamespace(get=lambda key: record if key == "EXPORT_MANIFEST" else None)
    document = SimpleNamespace(
        rootdict=SimpleNamespace(get=lambda key: gridforge_dict if key == "GRIDFORGE" else None),
        modelspace=lambda: [_CadEntity()],
    )
    fake_ezdxf = SimpleNamespace(
        readfile=lambda path: document,
        DXFError=type("DXFError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "ezdxf", fake_ezdxf)

    generated = tmp_path / "gridforge_export.dwg"
    generated.write_bytes(b"AC1032-dwg")

    def _convert(command, **kwargs):
        output_dir = Path(command[2])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "gridforge_export.dxf").write_text("roundtrip", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(
        "plugins.dwg_export.backends.oda_file_converter.subprocess.run", _convert,
    )
    backend._verify_dwg_metadata_roundtrip(
        generated, tmp_path / "verification", "ACAD2018", plan, digest,
    )


def test_oda_roundtrip_verifier_rejects_entity_metadata_drift(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    plan = DrawingPlan(
        project_id="project-1", export_id="export-1", source_revision="revision-1",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(DrawingEntity("entity-1", "TEXT", "LABEL", {"text": "x"}, {"label": "expected"}),),
    )
    manifest = backend._build_export_manifest(plan, backend._symbols, "ACAD2018")
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    class _Tag:
        def __init__(self, code, value):
            self.code = code
            self.value = value

    class _CadEntity:
        def get_xdata(self, appid):
            return [
                _Tag(1000, "entity-1"),
                _Tag(1000, json.dumps({
                    "label": "tampered",
                    "gridforge_export_manifest_sha256": digest,
                })),
            ]

    record = SimpleNamespace(tags=[_Tag(1, payload)])
    gridforge_dict = SimpleNamespace(get=lambda key: record if key == "EXPORT_MANIFEST" else None)
    document = SimpleNamespace(
        rootdict=SimpleNamespace(get=lambda key: gridforge_dict if key == "GRIDFORGE" else None),
        modelspace=lambda: [_CadEntity()],
    )
    monkeypatch.setitem(sys.modules, "ezdxf", SimpleNamespace(
        readfile=lambda path: document,
        DXFError=type("DXFError", (Exception,), {}),
    ))
    generated = tmp_path / "gridforge_export.dwg"
    generated.write_bytes(b"AC1032-dwg")

    def _convert(command, **kwargs):
        output_dir = Path(command[2])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "gridforge_export.dxf").write_text("roundtrip", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(
        "plugins.dwg_export.backends.oda_file_converter.subprocess.run", _convert,
    )
    with pytest.raises(RuntimeError, match="metadata differs from the drawing plan"):
        backend._verify_dwg_metadata_roundtrip(
            generated, tmp_path / "verification", "ACAD2018", plan, digest,
        )


def test_oda_roundtrip_verifier_rejects_manifest_provenance_drift(tmp_path, monkeypatch):
    import hashlib
    import json
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from plugins.dwg_export import DrawingPlan, SymbolProfile

    executable = tmp_path / "converter"
    executable.write_text("placeholder", encoding="utf-8")
    executable.chmod(0o700)
    backend = ODAFileConverterBackend(executable)
    plan = DrawingPlan(
        project_id="project-1", export_id="export-1", source_revision="revision-1",
        symbol_profile=SymbolProfile.ANSI_IEEE, drawing_types=("sld",),
        entities=(DrawingEntity("entity-1", "TEXT", "LABEL", {"text": "x"}, {}),),
    )
    wrong_manifest = backend._build_export_manifest(plan, backend._symbols, "ACAD2018")
    wrong_manifest["project_id"] = "other-project"
    payload = json.dumps(wrong_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    class _Tag:
        def __init__(self, code, value):
            self.code = code
            self.value = value

    class _CadEntity:
        def get_xdata(self, appid):
            return [
                _Tag(1000, "entity-1"),
                _Tag(1000, json.dumps({"gridforge_export_manifest_sha256": digest})),
            ]

    record = SimpleNamespace(tags=[_Tag(1, payload)])
    gridforge_dict = SimpleNamespace(get=lambda key: record if key == "EXPORT_MANIFEST" else None)
    document = SimpleNamespace(
        rootdict=SimpleNamespace(get=lambda key: gridforge_dict if key == "GRIDFORGE" else None),
        modelspace=lambda: [_CadEntity()],
    )
    monkeypatch.setitem(sys.modules, "ezdxf", SimpleNamespace(
        readfile=lambda path: document,
        DXFError=type("DXFError", (Exception,), {}),
    ))
    generated = tmp_path / "gridforge_export.dwg"
    generated.write_bytes(b"AC1032-dwg")

    def _convert(command, **kwargs):
        output_dir = Path(command[2])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "gridforge_export.dxf").write_text("roundtrip", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(
        "plugins.dwg_export.backends.oda_file_converter.subprocess.run", _convert,
    )
    with pytest.raises(RuntimeError, match="manifest contents do not match"):
        backend._verify_dwg_metadata_roundtrip(
            generated, tmp_path / "verification", "ACAD2018", plan, digest,
        )
