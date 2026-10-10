"""Validated, CAD-neutral symbol definitions for DWG export.

Symbol geometry is presentation only. It must not encode or mutate electrical
connectivity, device state, or protection/control semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from .drawing_plan import DrawingEntity, SymbolProfile

_SYMBOL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SUPPORTED_PRIMITIVES = frozenset({"LINE", "CIRCLE", "ARC", "TEXT", "LWPOLYLINE"})


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    """One profile-specific reusable symbol, expressed as local-space primitives."""

    symbol_id: str
    profile: SymbolProfile
    primitives: tuple[DrawingEntity, ...]
    base_point: tuple[float, float] = (0.0, 0.0)
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.symbol_id, str) or not _SYMBOL_ID.fullmatch(self.symbol_id):
            raise ValueError("symbol_id must be a stable identifier (1–128 safe characters)")
        try:
            profile = self.profile if isinstance(self.profile, SymbolProfile) else SymbolProfile(self.profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc
        object.__setattr__(self, "profile", profile)
        primitives = tuple(self.primitives)
        if not primitives:
            raise ValueError("symbol definitions require at least one primitive")
        if any(not isinstance(item, DrawingEntity) for item in primitives):
            raise TypeError("primitives must contain DrawingEntity values")
        if any(item.kind.upper() not in _SUPPORTED_PRIMITIVES for item in primitives):
            raise ValueError("symbol primitives may contain only LINE, CIRCLE, ARC, TEXT, or LWPOLYLINE")
        if len({item.entity_id for item in primitives}) != len(primitives):
            raise ValueError("symbol primitive entity_id values must be unique within a definition")
        # Symbol block primitives bypass the top-level DrawingPlan entity list,
        # so validate them here using the same canonical geometry contract.
        from .validation import validate_drawing_plan
        from .drawing_plan import DrawingPlan
        validate_drawing_plan(DrawingPlan(
            project_id="symbol-definition",
            export_id=f"symbol:{self.symbol_id}",
            source_revision="symbol-registry",
            symbol_profile=profile,
            drawing_types=("symbol",),
            entities=primitives,
        ))
        point = tuple(self.base_point)
        if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in point):
            raise ValueError("base_point must contain exactly two numeric coordinates")
        import math
        if any(not math.isfinite(float(v)) for v in point):
            raise ValueError("base_point coordinates must be finite")
        object.__setattr__(self, "base_point", (float(point[0]), float(point[1])))
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")

    @property
    def block_name(self) -> str:
        """Stable CAD block name independent of export order and CAD handles."""
        digest = hashlib.sha256(f"{self.profile.value}:{self.symbol_id}".encode("utf-8")).hexdigest()[:16].upper()
        return f"GF_{self.profile.value.upper()}_{digest}"


class SymbolRegistry:
    """Immutable lookup of profile-specific symbol definitions."""

    def __init__(self, definitions: Iterable[SymbolDefinition] = ()) -> None:
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
        self._definitions = by_key

    def resolve(self, symbol_id: str, profile: SymbolProfile) -> SymbolDefinition:
        try:
            profile = profile if isinstance(profile, SymbolProfile) else SymbolProfile(profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc
        try:
            return self._definitions[(profile, symbol_id)]
        except KeyError as exc:
            raise KeyError(f"no symbol definition registered for {profile.value}:{symbol_id}") from exc

    def __len__(self) -> int:
        return len(self._definitions)

    def definitions(self) -> tuple[SymbolDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions, key=lambda item: (item[0].value, item[1])))
