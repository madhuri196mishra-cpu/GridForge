"""Evidence-gated ingestion for profile-specific DWG symbol catalogues.

This module is deliberately independent of the UI symbol package. It validates
review records supplied by an authorized catalogue-curation workflow; it does
not certify standards compliance or manufacture symbol geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from types import MappingProxyType
from datetime import date
import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from typing import Any, Iterable, Mapping

from .drawing_plan import DrawingEntity, DrawingPlan, SymbolProfile, serialize_drawing_plan
from .symbol_registry import SymbolDefinition, SymbolRegistry

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass(frozen=True, slots=True)
class SymbolReviewEvidence:
    """Traceable source and review evidence for one profile-specific definition."""

    profile: SymbolProfile
    symbol_id: str
    standard_authority: str
    standard_id: str
    edition: str
    symbol_reference: str
    source_uri: str
    license_reference: str
    reviewer: str
    reviewed_on: str
    review_reference: str
    geometry_sha256: str

    def __post_init__(self) -> None:
        try:
            profile = self.profile if isinstance(self.profile, SymbolProfile) else SymbolProfile(self.profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "profile", profile)
        for field_name in (
            "symbol_id", "standard_authority", "standard_id", "edition",
            "symbol_reference", "source_uri", "license_reference", "reviewer",
            "reviewed_on", "review_reference", "geometry_sha256",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value.strip())
        if not _SYMBOL_REFERENCE.fullmatch(self.symbol_id):
            raise ValueError("symbol_id must be a stable canonical identifier")
        if not _SYMBOL_REFERENCE.fullmatch(self.symbol_reference):
            raise ValueError("symbol_reference must be an explicit standard symbol identifier")
        parsed_source = urlsplit(self.source_uri)
        if parsed_source.scheme != "https" or not parsed_source.hostname or (parsed_source.username or parsed_source.password):
            raise ValueError("source_uri must identify an HTTPS source with a hostname")
        if not _SHA256.fullmatch(self.geometry_sha256):
            raise ValueError("geometry_sha256 must be a lowercase SHA-256 digest")
        try:
            date.fromisoformat(self.reviewed_on)
        except ValueError as exc:
            raise ValueError("reviewed_on must be an ISO YYYY-MM-DD date") from exc

    @property
    def standard_reference(self) -> str:
        """Canonical standard locator stored in the legacy registry field."""
        return f"{self.standard_authority}:{self.standard_id}:{self.edition}#{self.symbol_reference}"


def symbol_geometry_digest(definition: SymbolDefinition) -> str:
    """Hash canonical local geometry, base point and units for review binding."""
    if not isinstance(definition, SymbolDefinition):
        raise TypeError("definition must be a SymbolDefinition")
    plan = DrawingPlan(
        project_id="symbol-review",
        export_id=f"{definition.profile.value}:{definition.symbol_id}:{definition.definition_version}",
        source_revision="symbol-review-evidence",
        symbol_profile=definition.profile,
        drawing_types=("symbol",),
        entities=definition.primitives,
    )
    payload = serialize_drawing_plan(plan).encode("utf-8")
    anchors = json.dumps(
        {name: list(definition.terminal_anchors[name]) for name in sorted(definition.terminal_anchors)},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    envelope = (
        f"{definition.units}|{definition.base_point[0]:.17g}|"
        f"{definition.base_point[1]:.17g}|"
    ).encode("ascii") + (
        f"{definition.source_units}|{definition.unit_to_mm:.17g}|"
        f"{definition.unit_mapping_reference or ''}|"
    ).encode("utf-8") + anchors + b"|" + payload
    return hashlib.sha256(envelope).hexdigest()



def convert_sld_catalogue_primitives(
    symbol_id: str,
    primitives: Iterable[Mapping[str, Any]],
    *,
    source_units: str,
    unit_to_mm: float = 1.0,
    unit_mapping_reference: str | None = None,
    layer: str = "SYMBOL",
) -> tuple[DrawingEntity, ...]:
    """Convert detached LINE, CIRCLE and RECT records into CAD entities."""
    if not isinstance(symbol_id, str) or not _SYMBOL_REFERENCE.fullmatch(symbol_id):
        raise ValueError("symbol_id must be a stable canonical identifier")
    if not isinstance(source_units, str) or not source_units.strip():
        raise ValueError("source_units must be explicitly declared")
    if isinstance(unit_to_mm, bool) or not isinstance(unit_to_mm, (int, float)):
        raise TypeError("unit_to_mm must be numeric")
    factor = float(unit_to_mm)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("unit_to_mm must be finite and positive")
    if source_units.strip() == "mm":
        if factor != 1.0 or unit_mapping_reference is not None:
            raise ValueError("mm input requires factor 1 and no unit mapping reference")
    elif not isinstance(unit_mapping_reference, str) or not unit_mapping_reference.strip():
        raise ValueError("non-mm input requires a reviewed unit mapping reference")
    if not isinstance(layer, str) or not layer.strip():
        raise ValueError("layer must be a non-empty CAD layer name")

    converted = []
    for index, source in enumerate(primitives):
        if not isinstance(source, Mapping):
            raise TypeError(f"primitive {index} must be a mapping")
        kind = source.get("kind")
        if not isinstance(kind, str):
            raise ValueError(f"primitive {index} requires a string kind")
        normalized = kind.strip().lower()
        entity_id = f"{symbol_id}:primitive:{index:03d}"
        metadata = {"source_primitive_kind": normalized}
        if normalized == "line":
            if set(source) != {"kind", "x1", "y1", "x2", "y2"}:
                raise ValueError(f"line primitive {index} has unsupported or missing fields")
            values = tuple(source[key] for key in ("x1", "y1", "x2", "y2"))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise TypeError(f"line primitive {index} coordinates must be numeric")
            if not all(math.isfinite(float(v)) for v in values):
                raise ValueError(f"line primitive {index} coordinates must be finite")
            geometry = {
                "start": (values[0] * factor, values[1] * factor),
                "end": (values[2] * factor, values[3] * factor),
            }
            entity_kind = "LINE"
        elif normalized == "circle":
            if set(source) != {"kind", "cx", "cy", "r"}:
                raise ValueError(f"circle primitive {index} has unsupported or missing fields")
            values = tuple(source[key] for key in ("cx", "cy", "r"))
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise TypeError(f"circle primitive {index} values must be numeric")
            if not all(math.isfinite(float(v)) for v in values) or values[2] <= 0:
                raise ValueError(f"circle primitive {index} values must be finite with positive radius")
            geometry = {
                "center": (values[0] * factor, values[1] * factor),
                "radius": values[2] * factor,
            }
            entity_kind = "CIRCLE"
        elif normalized == "rect":
            if set(source) != {"kind", "x", "y", "width", "height"}:
                raise ValueError(f"rect primitive {index} has unsupported or missing fields")
            x, y, width, height = (source[key] for key in ("x", "y", "width", "height"))
            values = (x, y, width, height)
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise TypeError(f"rect primitive {index} values must be numeric")
            if not all(math.isfinite(float(v)) for v in values) or width <= 0 or height <= 0:
                raise ValueError(f"rect primitive {index} bounds must be finite with positive dimensions")
            geometry = {
                "points": (
                    (x * factor, y * factor),
                    ((x + width) * factor, y * factor),
                    ((x + width) * factor, (y + height) * factor),
                    (x * factor, (y + height) * factor),
                ),
                "closed": True,
            }
            entity_kind = "LWPOLYLINE"
            metadata["source_rectangle"] = {"x": x, "y": y, "width": width, "height": height}
        else:
            raise ValueError(f"unsupported SLD catalogue primitive kind: {kind!r}")
        if source_units.strip() != "mm":
            metadata.update({
                "source_units": source_units.strip(),
                "unit_to_mm": factor,
                "unit_mapping_reference": unit_mapping_reference,
            })
        converted.append(DrawingEntity(entity_id, entity_kind, layer.strip(), geometry, metadata))
    if not converted:
        raise ValueError("symbol catalogue entry must contain at least one primitive")
    return tuple(converted)


@dataclass(frozen=True, slots=True)
class SymbolCatalogueEntry:
    """Detached profile-specific source record; never implicitly review-approved."""

    symbol_id: str
    profile: SymbolProfile
    source_units: str
    primitives: tuple[Mapping[str, Any], ...]
    base_point: tuple[float, float]
    terminal_anchors: Mapping[str, tuple[float, float]]
    unit_to_mm: float = 1.0
    unit_mapping_reference: str | None = None
    description: str = ""
    definition_version: str = "1.0.0"

    def __post_init__(self) -> None:
        try:
            profile = self.profile if isinstance(self.profile, SymbolProfile) else SymbolProfile(self.profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "profile", profile)
        if not isinstance(self.symbol_id, str) or not _SYMBOL_REFERENCE.fullmatch(self.symbol_id):
            raise ValueError("symbol_id must be a stable canonical identifier")
        if not isinstance(self.source_units, str) or not self.source_units.strip():
            raise ValueError("source_units must be explicit")
        if isinstance(self.unit_to_mm, bool) or not isinstance(self.unit_to_mm, (int, float)):
            raise TypeError("unit_to_mm must be numeric")
        factor = float(self.unit_to_mm)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("unit_to_mm must be finite and positive")
        if self.source_units.strip() == "mm":
            if factor != 1.0 or self.unit_mapping_reference is not None:
                raise ValueError("mm entries require factor 1 and no mapping reference")
        elif not isinstance(self.unit_mapping_reference, str) or not self.unit_mapping_reference.strip():
            raise ValueError("non-mm entries require a reviewed unit mapping reference")
        primitives = tuple(self.primitives)
        if not primitives or any(not isinstance(item, Mapping) for item in primitives):
            raise ValueError("primitives must be a non-empty sequence of mapping records")
        point = tuple(self.base_point)
        if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point):
            raise ValueError("base_point must contain two numeric coordinates")
        if not all(math.isfinite(float(v)) for v in point):
            raise ValueError("base_point coordinates must be finite")
        if not isinstance(self.terminal_anchors, Mapping):
            raise TypeError("terminal_anchors must be a mapping")
        anchors = {}
        for name, coordinates in self.terminal_anchors.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("terminal anchor names must be non-empty")
            values = tuple(coordinates)
            if len(values) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
                raise ValueError(f"terminal anchor {name!r} must have two numeric coordinates")
            if not all(math.isfinite(float(v)) for v in values):
                raise ValueError(f"terminal anchor {name!r} coordinates must be finite")
            anchors[name.strip()] = (float(values[0]) * factor, float(values[1]) * factor)
        if len(anchors) != len(self.terminal_anchors):
            raise ValueError("terminal anchor names must be unique after normalization")
        object.__setattr__(self, "source_units", self.source_units.strip())
        object.__setattr__(self, "unit_to_mm", factor)
        object.__setattr__(self, "primitives", tuple(MappingProxyType(dict(item)) for item in primitives))
        object.__setattr__(self, "base_point", (float(point[0]) * factor, float(point[1]) * factor))
        object.__setattr__(self, "terminal_anchors", MappingProxyType(anchors))


def assemble_unreviewed_symbol_definitions(
    entries: Iterable[SymbolCatalogueEntry],
) -> tuple[SymbolDefinition, ...]:
    """Assemble CAD definitions from detached records, always unreviewed."""
    definitions = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, SymbolCatalogueEntry):
            raise TypeError("entries must contain SymbolCatalogueEntry values")
        key = (entry.profile, entry.symbol_id)
        if key in seen:
            raise ValueError(f"duplicate catalogue entry for {entry.profile.value}:{entry.symbol_id}")
        seen.add(key)
        primitives = convert_sld_catalogue_primitives(
            entry.symbol_id,
            entry.primitives,
            source_units=entry.source_units,
            unit_to_mm=entry.unit_to_mm,
            unit_mapping_reference=entry.unit_mapping_reference,
        )
        definitions.append(SymbolDefinition(
            symbol_id=entry.symbol_id,
            profile=entry.profile,
            primitives=primitives,
            base_point=entry.base_point,
            description=entry.description,
            definition_version=entry.definition_version,
            review_status="unreviewed",
            units="mm",
            terminal_anchors=entry.terminal_anchors,
            source_units=entry.source_units,
            unit_to_mm=entry.unit_to_mm,
            unit_mapping_reference=entry.unit_mapping_reference,
        ))
    return tuple(sorted(definitions, key=lambda item: (item.profile.value, item.symbol_id)))


@dataclass(frozen=True, slots=True)
class SymbolCatalogueCoverage:
    """Coverage report; it does not independently validate review evidence."""

    required: tuple[tuple[str, str], ...]
    present: tuple[tuple[str, str], ...]
    missing: tuple[tuple[str, str], ...]
    unreviewed: tuple[tuple[str, str], ...]

    @property
    def export_candidate(self) -> bool:
        return not self.missing and not self.unreviewed


def symbol_catalogue_coverage(
    definitions: Iterable[SymbolDefinition],
    *,
    required_symbol_ids: Iterable[str],
) -> SymbolCatalogueCoverage:
    """Report missing and unreviewed profile slots without certifying evidence."""
    if isinstance(required_symbol_ids, str):
        raise TypeError("required_symbol_ids must be an iterable of IDs, not one string")
    required_ids = tuple(required_symbol_ids)
    if not required_ids or any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in required_ids
    ) or len(set(required_ids)) != len(required_ids):
        raise ValueError("required_symbol_ids must contain unique canonical IDs")
    required = tuple(
        (profile.value, symbol_id)
        for profile in SymbolProfile
        for symbol_id in required_ids
    )
    by_key = {}
    for definition in definitions:
        if not isinstance(definition, SymbolDefinition):
            raise TypeError("definitions must contain SymbolDefinition values")
        key = (definition.profile.value, definition.symbol_id)
        if key not in required:
            raise ValueError(f"definition uses non-canonical symbol ID: {key[1]}")
        if key in by_key:
            raise ValueError(f"duplicate symbol definition for {key[0]}:{key[1]}")
        by_key[key] = definition
    present = tuple(key for key in required if key in by_key)
    missing = tuple(key for key in required if key not in by_key)
    unreviewed = tuple(key for key in present if by_key[key].review_status != "reviewed")
    return SymbolCatalogueCoverage(required, present, missing, unreviewed)

def build_reviewed_symbol_registry(
    definitions: Iterable[SymbolDefinition],
    evidence_records: Iterable[SymbolReviewEvidence],
    *,
    required_symbol_ids: Iterable[str],
    require_complete: bool = True,
    catalogue_version: str = "1.0.0",
) -> SymbolRegistry:
    """Build a registry only when every definition has matching review evidence.

    Completeness is measured as every required canonical ID for both supported
    profiles. Partial registries are useful for staged curation only when the
    caller explicitly sets require_complete=False; normal export still fails
    closed for any missing profile/ID pair.
    """
    definitions = tuple(definitions)
    evidence_records = tuple(evidence_records)
    if isinstance(required_symbol_ids, str):
        raise TypeError("required_symbol_ids must be an iterable of IDs, not one string")
    required_ids = tuple(required_symbol_ids)
    if not required_ids or any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in required_ids
    ):
        raise ValueError("required_symbol_ids must contain canonical non-empty IDs")
    if len(set(required_ids)) != len(required_ids):
        raise ValueError("required_symbol_ids must not contain duplicates")
    if any(not isinstance(item, SymbolDefinition) for item in definitions):
        raise TypeError("definitions must contain SymbolDefinition values")
    if any(not isinstance(item, SymbolReviewEvidence) for item in evidence_records):
        raise TypeError("evidence_records must contain SymbolReviewEvidence values")

    required_id_set = set(required_ids)
    evidence_by_key: dict[tuple[SymbolProfile, str], SymbolReviewEvidence] = {}
    for evidence in evidence_records:
        key = (evidence.profile, evidence.symbol_id)
        if key in evidence_by_key:
            raise ValueError(f"duplicate review evidence for {evidence.profile.value}:{evidence.symbol_id}")
        evidence_by_key[key] = evidence

    definitions_by_key: dict[tuple[SymbolProfile, str], SymbolDefinition] = {}
    for definition in definitions:
        key = (definition.profile, definition.symbol_id)
        if definition.symbol_id not in required_id_set:
            raise ValueError(f"definition uses non-canonical symbol ID: {definition.symbol_id}")
        if key in definitions_by_key:
            raise ValueError(f"duplicate symbol definition for {definition.profile.value}:{definition.symbol_id}")
        definitions_by_key[key] = definition
        evidence = evidence_by_key.get(key)
        if evidence is None:
            raise ValueError(f"missing review evidence for {definition.profile.value}:{definition.symbol_id}")
        if definition.review_status != "reviewed":
            raise ValueError(f"definition {definition.profile.value}:{definition.symbol_id} is not reviewed")
        expected = {
            "reviewer": evidence.reviewer,
            "reviewed_on": evidence.reviewed_on,
            "review_reference": evidence.review_reference,
            "standard_reference": evidence.standard_reference,
        }
        for field_name, value in expected.items():
            if getattr(definition, field_name) != value:
                raise ValueError(
                    f"definition {definition.profile.value}:{definition.symbol_id} "
                    f"does not match evidence field {field_name}"
                )
        if symbol_geometry_digest(definition) != evidence.geometry_sha256:
            raise ValueError(
                f"definition geometry digest does not match review evidence for "
                f"{definition.profile.value}:{definition.symbol_id}"
            )

    orphaned = set(evidence_by_key) - set(definitions_by_key)
    if orphaned:
        profile, symbol_id = sorted(orphaned, key=lambda item: (item[0].value, item[1]))[0]
        raise ValueError(f"orphan review evidence for {profile.value}:{symbol_id}")

    expected_keys = {
        (profile, symbol_id)
        for profile in SymbolProfile
        for symbol_id in required_ids
    }
    missing = expected_keys - set(definitions_by_key)
    if require_complete and missing:
        profile, symbol_id = sorted(missing, key=lambda item: (item[0].value, item[1]))[0]
        raise ValueError(f"incomplete symbol catalogue: missing {profile.value}:{symbol_id}")

    return SymbolRegistry(definitions, catalogue_version=catalogue_version)


__all__ = [
    "SymbolReviewEvidence",
    "symbol_geometry_digest",
    "convert_sld_catalogue_primitives",
    "SymbolCatalogueEntry",
    "assemble_unreviewed_symbol_definitions",
    "SymbolCatalogueCoverage",
    "symbol_catalogue_coverage",
    "build_reviewed_symbol_registry",
]
