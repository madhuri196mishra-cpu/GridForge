"""Direct DWG backend using ezdxf plus the standalone ODA File Converter.

This does not require IngeCAD. The converter executable must be explicitly
configured by the caller; it is never discovered from PATH implicitly.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from ..drawing_plan import DrawingEntity, DrawingPlan
from ..service import ExportResult


_DWG_SIGNATURES = {
    "ACAD2018": b"AC1032",
    "ACAD2013": b"AC1027",
    "ACAD2010": b"AC1024",
    "ACAD2007": b"AC1021",
    "ACAD2004": b"AC1018",
    "ACAD2000": b"AC1015",
    "ACAD12": b"AC1009",
}


class ODAFileConverterBackend:
    """Export plan primitives to DXF and convert to native DWG via ODA.

    The executable path is explicit for security and reproducibility. Only the
    listed target versions are accepted; output is verified before atomic replace.
    """
    backend_id = "oda-file-converter"
    supported_formats = frozenset({"dwg"})

    def __init__(self, executable: str | Path, *, timeout_seconds: float = 120.0) -> None:
        path = Path(executable).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"ODA File Converter executable not found: {path}")
        if not os.access(path, os.X_OK) and os.name != "nt":
            raise PermissionError(f"ODA File Converter is not executable: {path}")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._executable = path
        self._timeout = float(timeout_seconds)

    @staticmethod
    def _add_entity(msp: Any, entity: DrawingEntity) -> None:
        g = entity.geometry
        kind = entity.kind.upper()
        if kind == "LINE":
            msp.add_line(tuple(g["start"]), tuple(g["end"]), dxfattribs={"layer": entity.layer})
        elif kind == "CIRCLE":
            msp.add_circle(tuple(g["center"]), float(g["radius"]), dxfattribs={"layer": entity.layer})
        elif kind == "ARC":
            msp.add_arc(tuple(g["center"]), float(g["radius"]), float(g["start_angle"]), float(g["end_angle"]), dxfattribs={"layer": entity.layer})
        elif kind == "TEXT":
            text = msp.add_text(str(g["text"]), dxfattribs={"layer": entity.layer, "height": float(g.get("height", 2.5))})
            text.set_placement(tuple(g["insert"]))
            if g.get("rotation") is not None:
                text.dxf.rotation = float(g["rotation"])
        elif kind == "LWPOLYLINE":
            points = g.get("points")
            if not isinstance(points, (list, tuple)) or len(points) < 2:
                raise ValueError(f"LWPOLYLINE {entity.entity_id} requires at least two points")
            msp.add_lwpolyline(points, close=bool(g.get("closed", False)), dxfattribs={"layer": entity.layer})
        elif kind == "INSERT":
            raise ValueError(f"INSERT {entity.entity_id} cannot be exported until a validated block-definition registry is provided")
        else:
            raise ValueError(f"Unsupported primitive {entity.kind!r} for ODA backend")

    @staticmethod
    def _attach_identity(doc: Any, entity: DrawingEntity) -> None:
        doc.appids.add("GRIDFORGE")
        # Entity identity is XDATA, not a CAD handle. Keep metadata JSON-safe.
        import json
        payload = json.dumps(dict(entity.metadata), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        # Resolve the generated entity by stable identity after it was appended.
        candidates = [item for item in doc.modelspace() if item.dxf.layer == entity.layer]
        if not candidates:
            raise RuntimeError(f"Failed to locate generated entity {entity.entity_id}")
        target = candidates[-1]
        target.set_xdata("GRIDFORGE", [(1000, entity.entity_id), (1000, payload[:250])])

    def export(self, plan: DrawingPlan, destination: Path, *, target_version: str | None = None) -> ExportResult:
        try:
            import ezdxf
        except ImportError as exc:
            raise RuntimeError("Direct DWG export requires the optional 'cad-export' dependencies (ezdxf)") from exc

        version = (target_version or "ACAD2018").upper()
        if version not in _DWG_SIGNATURES:
            raise ValueError(f"Unsupported ODA target version {version!r}; supported: {', '.join(sorted(_DWG_SIGNATURES))}")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="gridforge-dwg-") as temp:
            root = Path(temp)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            dxf_path = source_dir / "gridforge_export.dxf"
            doc = ezdxf.new("R2018")
            doc.header["$INSUNITS"] = 4  # millimetres; drawing plan currently uses mm.
            for entity in plan.entities:
                if entity.layer not in doc.layers:
                    doc.layers.new(entity.layer)
                self._add_entity(doc.modelspace(), entity)
                self._attach_identity(doc, entity)
            doc.header["$PROJECTNAME"] = plan.project_id[:255]
            doc.saveas(dxf_path)
            command = [str(self._executable), str(source_dir), str(output_dir), version, "DWG", "0", "0", "*.dxf"]
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=self._timeout, shell=False)
            generated = output_dir / "gridforge_export.dwg"
            if completed.returncode != 0:
                raise RuntimeError(f"ODA File Converter failed ({completed.returncode}): {(completed.stderr or completed.stdout)[-2000:]}")
            if not generated.is_file() or generated.stat().st_size < 6:
                raise RuntimeError("ODA File Converter did not produce a non-empty DWG output")
            with generated.open("rb") as stream:
                signature = stream.read(6)
            expected = _DWG_SIGNATURES[version]
            if signature != expected:
                raise RuntimeError(f"DWG signature mismatch for {version}: expected {expected!r}, got {signature!r}")
            # Keep failed conversions from replacing an existing valid export.
            staging = destination.with_name(destination.name + ".gridforge-tmp")
            try:
                staging.write_bytes(generated.read_bytes())
                os.replace(staging, destination)
            finally:
                if staging.exists():
                    staging.unlink()
        return ExportResult(destination, "dwg", len(plan.entities), self.backend_id)
