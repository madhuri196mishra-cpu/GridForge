"""Direct DWG backend using ezdxf plus the standalone ODA File Converter.

The converter executable must be explicitly configured; it is never discovered
from PATH implicitly. This backend does not require IngeCAD.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from ..drawing_plan import DrawingEntity, DrawingPlan
from ..service import ExportResult
from ..symbol_registry import SymbolRegistry

_DWG_SIGNATURES = {
    "ACAD2018": b"AC1032", "ACAD2013": b"AC1027", "ACAD2010": b"AC1024",
    "ACAD2007": b"AC1021", "ACAD2004": b"AC1018", "ACAD2000": b"AC1015",
    "ACAD12": b"AC1009",
}


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


class ODAFileConverterBackend:
    """Create a DXF staging drawing and convert it to a verified native DWG."""

    backend_id = "oda-file-converter"
    supported_formats = frozenset({"dwg"})

    def __init__(
        self,
        executable: str | Path,
        *,
        timeout_seconds: float = 120.0,
        symbol_registry: SymbolRegistry | None = None,
    ) -> None:
        path = Path(executable).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"ODA File Converter executable not found: {path}")
        if not os.access(path, os.X_OK) and os.name != "nt":
            raise PermissionError(f"ODA File Converter is not executable: {path}")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if symbol_registry is not None and not isinstance(symbol_registry, SymbolRegistry):
            raise TypeError("symbol_registry must be a SymbolRegistry")
        self._executable = path
        self._timeout = float(timeout_seconds)
        self._symbols = symbol_registry or SymbolRegistry()

    @staticmethod
    def _add_entity(msp: Any, entity: DrawingEntity) -> Any:
        g = entity.geometry
        kind = entity.kind.upper()
        attrs = {"layer": entity.layer}
        if kind == "LINE":
            return msp.add_line(tuple(g["start"]), tuple(g["end"]), dxfattribs=attrs)
        if kind == "CIRCLE":
            return msp.add_circle(tuple(g["center"]), float(g["radius"]), dxfattribs=attrs)
        if kind == "ARC":
            return msp.add_arc(tuple(g["center"]), float(g["radius"]), float(g["start_angle"]), float(g["end_angle"]), dxfattribs=attrs)
        if kind == "TEXT":
            text = msp.add_text(str(g["text"]), dxfattribs={**attrs, "height": float(g.get("height", 2.5))})
            text.set_placement(tuple(g["insert"]))
            if g.get("rotation") is not None:
                text.dxf.rotation = float(g["rotation"])
            return text
        if kind == "LWPOLYLINE":
            points = g.get("points")
            if not isinstance(points, (list, tuple)) or len(points) < 2:
                raise ValueError(f"LWPOLYLINE {entity.entity_id} requires at least two points")
            return msp.add_lwpolyline(points, close=bool(g.get("closed", False)), dxfattribs=attrs)
        raise ValueError(f"Unsupported primitive {entity.kind!r} for ODA backend")

    def _add_insert(self, doc: Any, entity: DrawingEntity, defined_blocks: set[str], profile: Any) -> Any:
        g = entity.geometry
        symbol_id = g.get("symbol_id")
        if not isinstance(symbol_id, str) or not symbol_id.strip():
            raise ValueError(f"INSERT {entity.entity_id} requires geometry.symbol_id")
        definition = self._symbols.resolve(symbol_id, profile)
        if definition.block_name not in defined_blocks:
            block = doc.blocks.new(name=definition.block_name, base_point=definition.base_point)
            for primitive in definition.primitives:
                if primitive.layer not in doc.layers:
                    doc.layers.new(primitive.layer)
                self._add_entity(block, primitive)
            defined_blocks.add(definition.block_name)
        insert = g.get("insert")
        if not isinstance(insert, (list, tuple)) or len(insert) not in (2, 3):
            raise ValueError(f"INSERT {entity.entity_id} requires a 2D or 3D geometry.insert point")
        scale = g.get("scale", (1.0, 1.0, 1.0))
        if isinstance(scale, (int, float)) and not isinstance(scale, bool):
            scale = (float(scale), float(scale), float(scale))
        if not isinstance(scale, (list, tuple)) or len(scale) not in (2, 3):
            raise ValueError(f"INSERT {entity.entity_id} scale must be a scalar or 2D/3D tuple")
        if len(scale) == 2:
            scale = (*scale, 1.0)
        scale = tuple(float(v) for v in scale)
        if any(not __import__("math").isfinite(v) or v == 0 for v in scale):
            raise ValueError(f"INSERT {entity.entity_id} scale values must be finite and non-zero")
        rotation = float(g.get("rotation", 0.0))
        if not __import__("math").isfinite(rotation):
            raise ValueError(f"INSERT {entity.entity_id} rotation must be finite")
        return doc.modelspace().add_blockref(
            definition.block_name,
            tuple(insert),
            dxfattribs={"layer": entity.layer, "xscale": scale[0], "yscale": scale[1], "zscale": scale[2], "rotation": rotation},
        )

    @staticmethod
    def _attach_identity(doc: Any, cad_entity: Any, entity: DrawingEntity) -> None:
        if "GRIDFORGE" not in doc.appids:
            doc.appids.add("GRIDFORGE")
        payload = json.dumps(_plain_json(entity.metadata), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        chunks = [payload[index:index + 240] for index in range(0, len(payload), 240)] or ["{}"]
        cad_entity.set_xdata("GRIDFORGE", [(1000, entity.entity_id), *[(1000, chunk) for chunk in chunks]])

    def export(self, plan: DrawingPlan, destination: Path, *, target_version: str | None = None) -> ExportResult:
        try:
            import ezdxf
        except ImportError as exc:
            raise RuntimeError("Direct DWG export requires the optional 'cad-export' dependency (ezdxf)") from exc
        version = (target_version or "ACAD2018").upper()
        if version not in _DWG_SIGNATURES:
            raise ValueError(f"Unsupported ODA target version {version!r}; supported: {', '.join(sorted(_DWG_SIGNATURES))}")
        if plan.units != "mm":
            raise ValueError(f"ODA backend currently supports millimetre units only, not {plan.units!r}")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gridforge-dwg-") as temp:
            root = Path(temp)
            source_dir, output_dir = root / "source", root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            dxf_path = source_dir / "gridforge_export.dxf"
            doc = ezdxf.new("R2018")
            doc.header["$INSUNITS"] = 4
            doc.header["$PROJECTNAME"] = plan.project_id[:255]
            defined_blocks: set[str] = set()
            for entity in plan.entities:
                if entity.layer not in doc.layers:
                    doc.layers.new(entity.layer)
                if entity.kind.upper() == "INSERT":
                    cad_entity = self._add_insert(doc, entity, defined_blocks, plan.symbol_profile)
                else:
                    cad_entity = self._add_entity(doc.modelspace(), entity)
                self._attach_identity(doc, cad_entity, entity)
            doc.saveas(dxf_path)
            command = [str(self._executable), str(source_dir), str(output_dir), version, "DWG", "0", "0", "*.dxf"]
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=self._timeout, shell=False)
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "no converter diagnostic")[-2000:]
                raise RuntimeError(f"ODA File Converter failed ({completed.returncode}): {detail}")
            generated = output_dir / "gridforge_export.dwg"
            if not generated.is_file() or generated.stat().st_size < 6:
                raise RuntimeError("ODA File Converter did not produce a non-empty DWG output")
            with generated.open("rb") as stream:
                signature = stream.read(6)
            if signature != _DWG_SIGNATURES[version]:
                raise RuntimeError(f"DWG signature mismatch for {version}: expected {_DWG_SIGNATURES[version]!r}, got {signature!r}")
            staging = destination.with_name(destination.name + ".gridforge-tmp")
            try:
                staging.write_bytes(generated.read_bytes())
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    staging.unlink()
        return ExportResult(destination, "dwg", len(plan.entities), self.backend_id)
