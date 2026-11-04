"""Evidence-gated ingestion for profile-specific DWG symbol catalogues.

This module is deliberately independent of the UI symbol package. It validates
review records supplied by an authorized catalogue-curation workflow; it does
not certify standards compliance or manufacture symbol geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from typing import Iterable, Mapping

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
    ).encode("ascii") + anchors + b"|" + payload
    return hashlib.sha256(envelope).hexdigest()



def convert_sld_catalogue_primitives(
    symbol_id: str,
    primitives: Iterable[Mapping[str, Any]],
    *,
    source_units: str,
    layer: str = "SYMBOL",
) -> tuple[DrawingEntity, ...]:
    """Convert detached SLD primitive mappings to CAD-neutral entities.

    RECT is represented as a closed LWPOLYLINE using the exact x/y/width/height
    local bounds. Source units must explicitly be millimetres. Text primitives
    are rejected because their SLD bounding-box alignment has no proven
    equivalent in the current CAD TEXT contract. This function does not import
    UI code or mark converted geometry as reviewed.
    """
    if not isinstance(symbol_id, str) or not _SYMBOL_REFERENCE.fullmatch(symbol_id):
        raise ValueError("symbol_id must be a stable canonical identifier")
    if not isinstance(source_units, str) or source_units.strip() != "mm":
        raise ValueError("source_units must be explicitly declared as mm; logical SLD units need a reviewed conversion first")
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
            geometry = {"start": (source["x1"], source["y1"]), "end": (source["x2"], source["y2"])}
            entity_kind = "LINE"
        elif normalized == "circle":
            if set(source) != {"kind", "cx", "cy", "r"}:
                raise ValueError(f"circle primitive {index} has unsupported or missing fields")
            geometry = {"center": (source["cx"], source["cy"]), "radius": source["r"]}
            entity_kind = "CIRCLE"
        elif normalized == "rect":
            if set(source) != {"kind", "x", "y", "width", "height"}:
                raise ValueError(f"rect primitive {index} has unsupported or missing fields")
            x, y, width, height = (source[key] for key in ("x", "y", "width", "height"))
            values = (x, y, width, height)
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                raise TypeError(f"rect primitive {index} values must be numeric")
            if not all(math.isfinite(float(value)) for value in values) or width <= 0 or height <= 0:
                raise ValueError(f"rect primitive {index} bounds must be finite with positive dimensions")
            geometry = {
                "points": ((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
                "closed": True,
            }
            entity_kind = "LWPOLYLINE"
            metadata["source_rectangle"] = {"x": x, "y": y, "width": width, "height": height}
        else:
            raise ValueError(f"unsupported SLD catalogue primitive kind: {kind!r}")
        converted.append(DrawingEntity(entity_id, entity_kind, layer.strip(), geometry, metadata))
    if not converted:
        raise ValueError("symbol catalogue entry must contain at least one primitive")
    return tuple(converted)


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
    "build_reviewed_symbol_registry",
]
