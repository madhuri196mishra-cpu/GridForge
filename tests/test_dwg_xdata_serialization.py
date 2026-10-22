from core.application.dwg_export import DrawingEntity
from core.application.dwg_export.backends.oda_file_converter import ODAFileConverterBackend


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
