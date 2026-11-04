"""Versioned, reviewed CAD-neutral symbol definitions for DWG export.

Symbol geometry is presentation only. Review metadata records provenance; it
is not a claim of standards certification or electrical authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Iterable

from .drawing_plan import DrawingEntity, DrawingPlan, SymbolProfile, drawing_plan_to_dict

_SYMBOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SUPPORTED_PRIMITIVES = frozenset({"LINE", "CIRCLE", "ARC", "TEXT", "LWPOLYLINE"})


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    """One profile-specific reusable symbol, expressed as local-space primitives."""

    symbol_id: str
    profile: SymbolProfile
    primitives: tuple[DrawingEntity, ...]
    base_point: tuple[float, float] = (0.0, 0.0)
    description: str = ""
    definition_version: str = "1.0.0"
    review_status: str = "unreviewed"
    reviewer: str | None = None
    reviewed_on: str | None = None
    review_reference: str | None = None
    standard_reference: str | None = None
    units: str = "mm"

    def __post_init__(self) -> None:
        if not isinstance(self.symbol_id, str) or not _SYMBOL_ID.fullmatch(self.symbol_id):
            raise ValueError("symbol_id must be a stable identifier (1–128 safe characters)")
        try:
            profile = self.profile if isinstance(self.profile, SymbolProfile) else SymbolProfile(self.profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "profile", profile)
        if not isinstance(self.definition_version, str) or not _VERSION.fullmatch(self.definition_version):
            raise ValueError("definition_version must use numeric MAJOR.MINOR.PATCH form")
        if not isinstance(self.units, str) or self.units.strip() != "mm":
            raise ValueError("symbol definition units must be explicitly supported as mm")
        object.__setattr__(self, "units", self.units.strip())
        if self.review_status not in {"unreviewed", "reviewed"}:
            raise ValueError("review_status must be unreviewed or reviewed")
        for name in ("reviewer", "reviewed_on", "review_reference", "standard_reference"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when supplied")
            if value is not None:
                object.__setattr__(self, name, value.strip())
        if self.review_status == "reviewed":
            if not all((self.reviewer, self.reviewed_on, self.review_reference, self.standard_reference)):
                raise ValueError(
                    "reviewed definitions require reviewer, reviewed_on, review_reference, and standard_reference"
                )
            try:
                date.fromisoformat(self.reviewed_on)
            except ValueError as exc:
                raise ValueError("reviewed_on must be an ISO YYYY-MM-DD date") from exc
        elif any((self.reviewer, self.reviewed_on, self.review_reference)):
            raise ValueError("unreviewed definitions cannot carry completed review provenance")
        primitives = tuple(self.primitives)
        if not primitives:
            raise ValueError("symbol definitions require at least one primitive")
        if any(not isinstance(item, DrawingEntity) for item in primitives):
            raise TypeError("primitives must contain DrawingEntity values")
        if any(item.kind.upper() not in _SUPPORTED_PRIMITIVES for item in primitives):
            raise ValueError("symbol primitives may contain only LINE, CIRCLE, ARC, TEXT, or LWPOLYLINE")
        if len({item.entity_id for item in primitives}) != len(primitives):
            raise ValueError("symbol primitive entity_id values must be unique within a definition")
        # Block primitives bypass top-level plan validation; reuse its contract.
        from .validation import validate_drawing_plan
        from .drawing_plan import DrawingPlan
        validate_drawing_plan(DrawingPlan(
            project_id="symbol-definition",
            export_id=f"symbol:{self.symbol_id}:{self.definition_version}",
            source_revision="symbol-registry",
            symbol_profile=profile,
            drawing_types=("symbol",),
            entities=primitives,
        ))
        point = tuple(self.base_point)
        if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point):
            raise ValueError("base_point must contain exactly two numeric coordinates")
        if any(not math.isfinite(float(v)) for v in point):
            raise ValueError("base_point coordinates must be finite")
        object.__setattr__(self, "base_point", (float(point[0]), float(point[1])))
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")

    @property
    def block_name(self) -> str:
        """Stable CAD block name scoped to profile, ID and definition version."""
        identity = f"{self.profile.value}:{self.symbol_id}:{self.definition_version}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
        return f"GF_{self.profile.value.upper()}_{digest}"


class SymbolRegistry:
    """Immutable, versioned lookup of reviewed profile-specific definitions."""

    def __init__(
        self,
        definitions: Iterable[SymbolDefinition] = (),
        *,
        schema_version: int = 1,
        catalogue_version: str = "1.0.0",
    ) -> None:
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
            raise ValueError("unsupported symbol registry schema_version")
        if not isinstance(catalogue_version, str) or not _VERSION.fullmatch(catalogue_version):
            raise ValueError("catalogue_version must use numeric MAJOR.MINOR.PATCH form")
        by_key: dict[tuple[SymbolProfile, str], SymbolDefinition] = {}
        by_block: dict[str, tuple[SymbolProfile, str]] = {}
        for definition in definitions:
            if not isinstance(definition, SymbolDefinition):
                raise TypeError("registry definitions must be SymbolDefinition values")
            key = (definition.profile, definition.symbol_id)
            if key in by_key:
                raise ValueError(f"duplicate symbol definition for {definition.profile.value}:{definition.symbol_id}")
            if definition.block_name in by_block and by_block[definition.block_name] != key:
                raise ValueError(f"block-name collision for {definition.block_name}")
            by_key[key] = definition
            by_block[definition.block_name] = key
        self._schema_version = schema_version
        self._catalogue_version = catalogue_version
        self._definitions = MappingProxyType(by_key)

    @property
    def schema_version(self) -> int:
        return self._schema_version

    @property
    def catalogue_version(self) -> str:
        return self._catalogue_version

    @property
    def fingerprint(self) -> str:
        """Return a deterministic digest of registry content, not just its version labels."""
        definitions = []
        for definition in self.definitions():
            plan = DrawingPlan(
                project_id="symbol-registry",
                export_id=f"{definition.profile.value}:{definition.symbol_id}:{definition.definition_version}",
                source_revision="registry-fingerprint",
                symbol_profile=definition.profile,
                drawing_types=("symbol",),
                entities=definition.primitives,
            )
            definitions.append({
                "symbol_id": definition.symbol_id,
                "profile": definition.profile.value,
                "definition_version": definition.definition_version,
                "review_status": definition.review_status,
                "reviewer": definition.reviewer,
                "reviewed_on": definition.reviewed_on,
                "review_reference": definition.review_reference,
                "standard_reference": definition.standard_reference,
                "description": definition.description,
                "base_point": list(definition.base_point),
                "units": definition.units,
                "drawing_plan": drawing_plan_to_dict(plan),
            })
        payload = json.dumps({
            "schema_version": self.schema_version,
            "catalogue_version": self.catalogue_version,
            "definitions": definitions,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def resolve(self, symbol_id: str, profile: SymbolProfile) -> SymbolDefinition:
        try:
            profile = profile if isinstance(profile, SymbolProfile) else SymbolProfile(profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc
        try:
            definition = self._definitions[(profile, symbol_id)]
        except KeyError as exc:
            raise KeyError(f"no symbol definition registered for {profile.value}:{symbol_id}") from exc
        if definition.review_status != "reviewed":
            raise ValueError(
                f"symbol definition {profile.value}:{symbol_id}@{definition.definition_version} "
                "is registered but not reviewed; export is blocked"
            )
        return definition

    def __len__(self) -> int:
        return len(self._definitions)

    def definitions(self) -> tuple[SymbolDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions, key=lambda item: (item[0].value, item[1])))


__all__ = ["SymbolDefinition", "SymbolRegistry"]
