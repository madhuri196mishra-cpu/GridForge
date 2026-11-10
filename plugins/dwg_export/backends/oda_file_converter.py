"""Direct DWG backend using ezdxf plus the standalone ODA File Converter.

The converter executable must be explicitly configured; it is never discovered
from PATH implicitly. This backend does not require IngeCAD.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from pathlib import Path
import subprocess
import shutil
import tempfile
from typing import Any

from ..drawing_plan import DrawingEntity, DrawingPlan, serialize_drawing_plan
from ..control_coordinate_transform import ControlCoordinateTransform
from ..service import ExportResult
from ..symbol_registry import SymbolRegistry
from ..validation import validate_drawing_plan

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
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be a finite positive number")
        timeout_value = float(timeout_seconds)
        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        if symbol_registry is not None and not isinstance(symbol_registry, SymbolRegistry):
            raise TypeError("symbol_registry must be a SymbolRegistry")
        self._executable = path
        self._timeout = timeout_value
        self._symbols = symbol_registry if symbol_registry is not None else SymbolRegistry()

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
        required_provenance = (
            "symbol_profile",
            "symbol_definition_id",
            "symbol_definition_version",
            "symbol_definition_units",
            "symbol_definition_base_point",
            "symbol_source_units",
            "symbol_unit_to_mm",
            "symbol_unit_mapping_reference",
            "symbol_terminal_anchors",
            "symbol_terminal_anchor_coordinate_system",
            "symbol_review_status",
            "symbol_reviewer",
            "symbol_reviewed_on",
            "symbol_review_reference",
            "symbol_standard_reference",
            "symbol_registry_schema_version",
            "symbol_catalogue_version",
            "symbol_registry_fingerprint",
        )
        missing = tuple(name for name in required_provenance if name not in entity.metadata)
        if missing:
            raise ValueError(
                f"INSERT {entity.entity_id} is missing required symbol registry provenance: "
                f"{', '.join(missing)}"
            )

        expected_values = {
            "symbol_profile": profile.value,
            "symbol_definition_id": definition.symbol_id,
            "symbol_definition_version": definition.definition_version,
            "symbol_definition_units": definition.units,
            "symbol_definition_base_point": definition.base_point,
            "symbol_source_units": definition.source_units,
            "symbol_unit_to_mm": definition.unit_to_mm,
            "symbol_unit_mapping_reference": definition.unit_mapping_reference,
            "symbol_terminal_anchors": {
                name: definition.terminal_anchors[name]
                for name in sorted(definition.terminal_anchors)
            },
            "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
            "symbol_review_status": definition.review_status,
            "symbol_reviewer": definition.reviewer,
            "symbol_reviewed_on": definition.reviewed_on,
            "symbol_review_reference": definition.review_reference,
            "symbol_standard_reference": definition.standard_reference,
            "symbol_registry_schema_version": self._symbols.schema_version,
            "symbol_catalogue_version": self._symbols.catalogue_version,
            "symbol_registry_fingerprint": self._symbols.fingerprint,
        }
        for field_name, expected in expected_values.items():
            actual = entity.metadata[field_name]
            if field_name == "symbol_definition_base_point" and isinstance(actual, list):
                actual = tuple(actual)
            if actual != expected:
                raise ValueError(
                    f"INSERT {entity.entity_id} symbol provenance mismatch for {field_name}: "
                    f"expected {expected!r}, received {actual!r}"
                )
        if definition.block_name not in defined_blocks:
            # DXF BLOCK base_point is the local origin used by INSERT. Keep
            # primitives in symbol-local mm. SLDCoordinateTransform has already
            # mapped the node origin and applied the rotated millimetre base-point
            # offset to geometry.insert, matching EquipmentItem's local origin.
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
        if any(not math.isfinite(v) or v == 0 for v in scale):
            raise ValueError(f"INSERT {entity.entity_id} scale values must be finite and non-zero")
        rotation = float(g.get("rotation", 0.0))
        if not math.isfinite(rotation):
            raise ValueError(f"INSERT {entity.entity_id} rotation must be finite")
        return doc.modelspace().add_blockref(
            definition.block_name,
            tuple(insert),
            dxfattribs={"layer": entity.layer, "xscale": scale[0], "yscale": scale[1], "zscale": scale[2], "rotation": rotation},
        )

    @staticmethod
    def _utf8_xdata_chunks(
        payload: str, *, max_bytes: int = 240, max_total_bytes: int = 15000,
    ) -> list[str]:
        """Split XDATA strings safely and reject payloads near the 16 KB entity limit."""
        encoded_size = len(payload.encode("utf-8"))
        if encoded_size > max_total_bytes:
            raise ValueError(
                "Entity metadata exceeds the safe 15,000-byte GRIDFORGE XDATA payload limit; "
                "move large data to a drawing-level XRECORD"
            )
        chunks: list[str] = []
        current: list[str] = []
        current_bytes = 0
        for character in payload:
            size = len(character.encode("utf-8"))
            if size > max_bytes:
                raise ValueError("Metadata contains a character too large for a DXF XDATA string")
            if current and current_bytes + size > max_bytes:
                chunks.append("".join(current))
                current = []
                current_bytes = 0
            current.append(character)
            current_bytes += size
        if current:
            chunks.append("".join(current))
        return chunks or ["{}"]

    @staticmethod
    def _xrecord_chunks(payload: str, *, max_chars: int = 2000) -> list[str]:
        """Split JSON into DXF XRECORD string tags without splitting Unicode characters."""
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        return [
            payload[index:index + max_chars]
            for index in range(0, len(payload), max_chars)
        ] or ["{}"]

    @staticmethod
    def _attach_export_manifest(doc: Any, artifact_manifest: Mapping[str, Any]) -> str:
        """Persist one drawing-level manifest in the root dictionary, including empty plans."""
        if not hasattr(doc, "rootdict"):
            raise TypeError("DXF document must expose rootdict for drawing-level provenance")
        payload = json.dumps(
            _plain_json(artifact_manifest), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        manifest_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        gridforge_dict = doc.rootdict.get_required_dict("GRIDFORGE")
        if "EXPORT_MANIFEST" in gridforge_dict:
            raise ValueError("DXF document already contains a GRIDFORGE export manifest")
        record = gridforge_dict.add_xrecord("EXPORT_MANIFEST")
        record.extend([(1, chunk) for chunk in ODAFileConverterBackend._xrecord_chunks(payload)])
        return manifest_digest

    @staticmethod
    def _build_export_manifest(
        plan: DrawingPlan, symbol_registry: SymbolRegistry, target_version: str,
    ) -> dict[str, Any]:
        """Build deterministic artifact provenance copied into each entity's XDATA."""
        mapping_records: dict[str, Any] = {}
        entities_with_mapping = 0
        for entity in plan.entities:
            mapping = entity.metadata.get("coordinate_transform")
            if mapping is None:
                continue
            if not isinstance(mapping, Mapping):
                raise ValueError(
                    f"Entity {entity.entity_id!r} has invalid coordinate transform provenance"
                )
            common_required = {
                "mapping_id", "mapping_version", "source_coordinate_system",
                "source_units", "target_coordinate_system", "target_units",
                "rotation_degrees", "reviewer", "reviewed_on", "review_reference",
            }
            if mapping.get("source_coordinate_system") == "control-ladder-local":
                required = common_required | {
                    "world_origin_x_mm", "world_origin_y_mm", "mapping_fingerprint",
                }
                try:
                    expected_mapping_fingerprint = ControlCoordinateTransform.fingerprint_from_provenance(mapping)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Entity {entity.entity_id!r} has invalid Control coordinate mapping provenance"
                    ) from exc
                if mapping.get("mapping_fingerprint") != expected_mapping_fingerprint:
                    raise ValueError(
                        f"Entity {entity.entity_id!r} Control coordinate mapping fingerprint mismatch"
                    )
            else:
                required = common_required | {
                    "millimetres_per_source_unit", "world_origin_mm",
                }
            missing = required - set(mapping)
            if missing:
                raise ValueError(
                    f"Entity {entity.entity_id!r} has incomplete coordinate transform provenance: "
                    f"{', '.join(sorted(missing))}"
                )
            if (
                mapping["target_coordinate_system"] != plan.coordinate_system
                or mapping["target_units"] != plan.units
                or mapping["target_coordinate_system"] != "world"
                or mapping["target_units"] != "mm"
            ):
                raise ValueError(
                    f"Entity {entity.entity_id!r} coordinate mapping target does not match "
                    "the drawing plan's world/mm declaration"
                )
            canonical = json.dumps(
                _plain_json(mapping), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            mapping_records[fingerprint] = _plain_json(mapping)
            entities_with_mapping += 1

        if mapping_records and entities_with_mapping != len(plan.entities):
            raise ValueError("Drawing plan mixes transformed and untransformed entities")
        if len(mapping_records) > 1:
            raise ValueError("Drawing plan contains conflicting coordinate mapping provenance")
        if plan.source_kind == "sld_export_snapshot" and plan.entities and not mapping_records:
            raise ValueError("SLD export plan is missing reviewed coordinate mapping provenance")

        mapping_fingerprint = next(iter(mapping_records), None)
        mapping_details = mapping_records.get(mapping_fingerprint) if mapping_fingerprint else None
        plan_digest = hashlib.sha256(serialize_drawing_plan(plan).encode("utf-8")).hexdigest()
        return {
            "manifest_schema_version": 1,
            "export_id": plan.export_id,
            "project_id": plan.project_id,
            "source_revision": plan.source_revision,
            "source_kind": plan.source_kind,
            "source_id": plan.source_id,
            "source_document_id": plan.source_document_id,
            "drawing_plan_sha256": plan_digest,
            "symbol_profile": plan.symbol_profile.value,
            "symbol_registry_schema_version": symbol_registry.schema_version,
            "symbol_catalogue_version": symbol_registry.catalogue_version,
            "symbol_registry_fingerprint": symbol_registry.fingerprint,
            "coordinate_mapping_fingerprint": mapping_fingerprint,
            "coordinate_mapping": mapping_details,
            "backend_id": ODAFileConverterBackend.backend_id,
            "target_version": target_version,
            "entity_count": len(plan.entities),
        }

    @staticmethod
    def _attach_identity(
        doc: Any, cad_entity: Any, entity: DrawingEntity, plan: DrawingPlan | None = None,
        artifact_manifest: Mapping[str, Any] | None = None,
    ) -> None:
        entity_id = entity.entity_id
        if len(entity_id.encode("utf-8")) > 240:
            raise ValueError(f"Drawing entity ID {entity_id!r} exceeds the safe DXF XDATA string length")
        if "GRIDFORGE" not in doc.appids:
            doc.appids.add("GRIDFORGE")
        metadata = dict(entity.metadata)
        if plan is not None:
            source_fields = {
                "source_project_id": plan.project_id,
                "source_revision": plan.source_revision,
                "source_kind": plan.source_kind,
                "source_id": plan.source_id,
                "source_document_id": plan.source_document_id,
            }
            for field_name, expected in source_fields.items():
                existing = metadata.get(field_name)
                if existing is not None and existing != expected:
                    raise ValueError(
                        f"Entity {entity.entity_id!r} metadata conflicts with drawing-plan "
                        f"provenance for {field_name}"
                    )
                if expected is not None:
                    metadata[field_name] = expected
        if artifact_manifest is not None:
            manifest = _plain_json(artifact_manifest)
            canonical_manifest = json.dumps(
                manifest, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
            manifest_digest = hashlib.sha256(canonical_manifest.encode("utf-8")).hexdigest()
            existing_manifest = metadata.pop("gridforge_export_manifest", None)
            if existing_manifest is not None and existing_manifest != manifest:
                raise ValueError(
                    f"Entity {entity.entity_id!r} metadata conflicts with export manifest"
                )
            existing_digest = metadata.get("gridforge_export_manifest_sha256")
            if existing_digest is not None and existing_digest != manifest_digest:
                raise ValueError(
                    f"Entity {entity.entity_id!r} metadata conflicts with export manifest digest"
                )
            metadata["gridforge_export_manifest_sha256"] = manifest_digest
        payload = json.dumps(_plain_json(metadata), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        chunks = ODAFileConverterBackend._utf8_xdata_chunks(payload)
        cad_entity.set_xdata("GRIDFORGE", [(1000, entity_id), *[(1000, chunk) for chunk in chunks]])

    @staticmethod
    def _canonical_dxf_value(value: Any) -> Any:
        """Convert ezdxf attribute values into deterministic JSON-compatible values."""
        if isinstance(value, Mapping):
            return {
                str(key): ODAFileConverterBackend._canonical_dxf_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (tuple, list)):
            return [ODAFileConverterBackend._canonical_dxf_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            return {"bytes_hex": value.hex()}
        try:
            return [
                ODAFileConverterBackend._canonical_dxf_value(item)
                for item in value
            ]
        except TypeError as exc:
            raise TypeError(
                f"Unsupported DXF attribute value for round-trip comparison: {type(value).__name__}"
            ) from exc

    @classmethod
    def _geometry_roundtrip_snapshot(
        cls, document: Any, *, bind_modelspace_identity: bool = False,
    ) -> dict[str, Any]:
        """Capture drawing geometry, optionally binding modelspace records to GridForge IDs."""
        volatile_attributes = {"handle", "owner"}

        def attributes(entity: Any) -> dict[str, Any]:
            return {
                str(name): cls._canonical_dxf_value(value)
                for name, value in sorted(entity.dxfattribs().items())
                if name not in volatile_attributes
            }

        def entity_record(
            entity: Any, *, require_gridforge_identity: bool = False,
        ) -> dict[str, Any]:
            entity_type = entity.dxftype()
            record = {
                "type": entity_type,
                "attributes": attributes(entity),
            }
            if require_gridforge_identity:
                get_xdata = getattr(entity, "get_xdata", None)
                if not callable(get_xdata):
                    raise RuntimeError(
                        "Round-trip modelspace entity does not expose GridForge identity XDATA"
                    )
                try:
                    identity_tags = [
                        tag for tag in get_xdata("GRIDFORGE") if tag.code == 1000
                    ]
                except Exception as exc:
                    raise RuntimeError(
                        "Round-trip modelspace entity lost GridForge identity XDATA"
                    ) from exc
                if not identity_tags or not str(identity_tags[0].value).strip():
                    raise RuntimeError(
                        "Round-trip modelspace entity has no GridForge identity XDATA"
                    )
                record["gridforge_entity_id"] = str(identity_tags[0].value)
            if entity_type == "LWPOLYLINE":
                record["vertices"] = cls._canonical_dxf_value(
                    entity.get_points("xyseb")
                )
            elif entity_type == "POLYLINE":
                record["vertices"] = [
                    attributes(vertex) for vertex in entity.vertices
                ]
            attached_attributes = getattr(entity, "attribs", ())
            if attached_attributes:
                record["attached_attributes"] = [
                    entity_record(attribute) for attribute in attached_attributes
                ]
            return record

        def ordered_entities(
            layout: Any, *, require_gridforge_identity: bool = False,
        ) -> list[dict[str, Any]]:
            records = [
                entity_record(entity, require_gridforge_identity=require_gridforge_identity)
                for entity in layout
            ]
            return sorted(
                records,
                key=lambda record: json.dumps(
                    record, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False,
                ),
            )

        blocks = []
        for layout in document.blocks:
            blocks.append({
                "name": layout.name,
                "definition": attributes(layout.block),
                "entities": ordered_entities(layout),
            })
        blocks.sort(key=lambda record: record["name"].casefold())

        layers = [
            attributes(layer)
            for layer in document.layers
        ]
        layers.sort(key=lambda record: str(record.get("name", "")).casefold())
        return {
            "insunits": document.header.get("$INSUNITS"),
            "layers": layers,
            "modelspace": ordered_entities(
                document.modelspace(),
                require_gridforge_identity=bind_modelspace_identity,
            ),
            "blocks": blocks,
        }

    def _verify_dwg_metadata_roundtrip(
        self,
        generated_dwg: Path,
        verification_root: Path,
        target_version: str,
        plan: DrawingPlan,
        manifest_digest: str,
        source_document: Any,
    ) -> None:
        """Re-open ODA's DWG as DXF and verify geometry, manifest and entity XDATA survived."""
        source_dir = verification_root / "roundtrip-source"
        output_dir = verification_root / "roundtrip-output"
        source_dir.mkdir()
        output_dir.mkdir()
        source_dwg = source_dir / generated_dwg.name
        shutil.copy2(generated_dwg, source_dwg)
        command = [
            str(self._executable), str(source_dir), str(output_dir),
            target_version, "DXF", "0", "0", "*.dwg",
        ]
        completed = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=self._timeout, shell=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no converter diagnostic")[-2000:]
            raise RuntimeError(
                f"ODA DWG-to-DXF metadata verification failed ({completed.returncode}): {detail}"
            )
        roundtrip_dxf = output_dir / f"{generated_dwg.stem}.dxf"
        if not roundtrip_dxf.is_file() or roundtrip_dxf.stat().st_size == 0:
            raise RuntimeError("ODA did not produce the DXF required for DWG metadata verification")
        try:
            import ezdxf
        except ImportError as exc:
            raise RuntimeError("DWG metadata verification requires ezdxf") from exc
        document = ezdxf.readfile(roundtrip_dxf)
        source_geometry = self._geometry_roundtrip_snapshot(
            source_document, bind_modelspace_identity=True,
        )
        converted_geometry = self._geometry_roundtrip_snapshot(
            document, bind_modelspace_identity=True,
        )
        if converted_geometry != source_geometry:
            raise RuntimeError(
                "Converted DWG geometry, block definitions, layers or drawing units "
                "differ from the source DXF"
            )
        gridforge_dict = document.rootdict.get("GRIDFORGE")
        if gridforge_dict is None:
            raise RuntimeError("Converted DWG lost the GRIDFORGE root dictionary")
        manifest_record = gridforge_dict.get("EXPORT_MANIFEST")
        if manifest_record is None:
            raise RuntimeError("Converted DWG lost the GRIDFORGE export manifest XRECORD")
        manifest_payload = "".join(
            str(tag.value) for tag in manifest_record.tags if tag.code == 1
        )
        if not manifest_payload:
            raise RuntimeError("Converted DWG export manifest XRECORD is empty")
        actual_digest = hashlib.sha256(manifest_payload.encode("utf-8")).hexdigest()
        if actual_digest != manifest_digest:
            raise RuntimeError("Converted DWG export manifest XRECORD digest mismatch")
        try:
            manifest = json.loads(manifest_payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Converted DWG export manifest is not valid JSON") from exc
        expected_manifest = self._build_export_manifest(plan, self._symbols, target_version)
        if manifest != _plain_json(expected_manifest):
            raise RuntimeError(
                "Converted DWG export manifest contents do not match the drawing plan and registry"
            )
        expected_ids = {entity.entity_id for entity in plan.entities}
        observed_ids: set[str] = set()
        for cad_entity in document.modelspace():
            try:
                tags = cad_entity.get_xdata("GRIDFORGE")
            except (AttributeError, ezdxf.DXFError):
                continue
            values = [str(tag.value) for tag in tags if tag.code == 1000]
            if not values:
                continue
            entity_id = values[0]
            if entity_id in observed_ids:
                raise RuntimeError(f"Converted DWG contains duplicate GridForge entity identity {entity_id!r}")
            observed_ids.add(entity_id)
            metadata_payload = "".join(values[1:])
            try:
                metadata = json.loads(metadata_payload)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Converted DWG metadata for entity {entity_id!r} is not valid JSON"
                ) from exc
            if metadata.get("gridforge_export_manifest_sha256") != manifest_digest:
                raise RuntimeError(
                    f"Converted DWG entity {entity_id!r} lost or changed its manifest digest"
                )
            source_fields = {
                "source_project_id": plan.project_id,
                "source_revision": plan.source_revision,
                "source_kind": plan.source_kind,
                "source_id": plan.source_id,
                "source_document_id": plan.source_document_id,
            }
            expected_metadata = dict(entity.metadata)
            for field_name, expected in source_fields.items():
                existing = expected_metadata.get(field_name)
                if existing is not None and existing != expected:
                    raise RuntimeError(
                        f"Drawing-plan entity {entity_id!r} has conflicting {field_name} provenance"
                    )
                if expected is not None:
                    expected_metadata[field_name] = expected
            expected_metadata.pop("gridforge_export_manifest", None)
            existing_digest = expected_metadata.get("gridforge_export_manifest_sha256")
            if existing_digest is not None and existing_digest != manifest_digest:
                raise RuntimeError(
                    f"Drawing-plan entity {entity_id!r} has conflicting export manifest digest"
                )
            expected_metadata["gridforge_export_manifest_sha256"] = manifest_digest
            if metadata != _plain_json(expected_metadata):
                raise RuntimeError(
                    f"Converted DWG entity {entity_id!r} metadata differs from the drawing plan"
                )
        if observed_ids != expected_ids:
            missing = sorted(expected_ids - observed_ids)
            unexpected = sorted(observed_ids - expected_ids)
            raise RuntimeError(
                "Converted DWG entity identity set mismatch; "
                f"missing={missing!r}, unexpected={unexpected!r}"
            )

    @staticmethod
    def _publish_atomically(generated: Path, destination: Path) -> None:
        """Publish a completed DWG using unique same-directory staging."""
        staging_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.gridforge-",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as staging:
                staging_path = Path(staging.name)
                with generated.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        staging.write(chunk)
                staging.flush()
                os.fsync(staging.fileno())
            os.replace(staging_path, destination)
            staging_path = None
        finally:
            if staging_path is not None:
                try:
                    staging_path.unlink()
                except FileNotFoundError:
                    pass

    def export(self, plan: DrawingPlan, destination: Path, *, target_version: str | None = None) -> ExportResult:
        # The backend is also a public callable implementation of the export
        # protocol; enforce the same contract when it is used without
        # DWGExportService, before importing optional CAD dependencies or
        # creating output directories/files.
        validate_drawing_plan(plan)
        try:
            import ezdxf
        except ImportError as exc:
            raise RuntimeError("Direct DWG export requires the optional 'dwg-export' dependency (ezdxf)") from exc
        version = (target_version or "ACAD2018").upper()
        if version not in _DWG_SIGNATURES:
            raise ValueError(f"Unsupported ODA target version {version!r}; supported: {', '.join(sorted(_DWG_SIGNATURES))}")
        if plan.units != "mm":
            raise ValueError(f"ODA backend currently supports millimetre units only, not {plan.units!r}")
        if plan.coordinate_system != "world":
            raise ValueError(
                "ODA backend currently accepts only the explicit 'world' coordinate system; "
                f"received {plan.coordinate_system!r}"
            )
        destination = Path(destination)
        if destination.suffix.lower() != ".dwg":
            raise ValueError("ODA backend destination extension must be .dwg")
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
            artifact_manifest = self._build_export_manifest(plan, self._symbols, version)
            manifest_digest = self._attach_export_manifest(doc, artifact_manifest)
            defined_blocks: set[str] = set()
            for entity in plan.entities:
                if entity.layer not in doc.layers:
                    doc.layers.new(entity.layer)
                if entity.kind.upper() == "INSERT":
                    cad_entity = self._add_insert(doc, entity, defined_blocks, plan.symbol_profile)
                else:
                    cad_entity = self._add_entity(doc.modelspace(), entity)
                self._attach_identity(doc, cad_entity, entity, plan, artifact_manifest)
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
            self._verify_dwg_metadata_roundtrip(
                generated, root, version, plan, manifest_digest, doc,
            )
            self._publish_atomically(generated, destination)
        return ExportResult(
            destination, "dwg", len(plan.entities), self.backend_id,
            project_id=plan.project_id,
            source_revision=plan.source_revision,
            source_kind=plan.source_kind,
            source_id=plan.source_id,
            source_document_id=plan.source_document_id,
        )
