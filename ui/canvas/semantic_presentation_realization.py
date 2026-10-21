# ============================================================
# File: ui/canvas/semantic_presentation_realization.py
# GridForge V2 — Semantic Presentation Realization
# Author: Subhendu Mishra
# ============================================================
"""Resolve SLD semantics into immutable renderer-neutral presentation data."""

from __future__ import annotations

from dataclasses import dataclass

from ui.equipment.equipment_registry import EquipmentRegistry
from ui.equipment.symbol.symbol_base import SymbolBase
from ui.equipment.symbol.symbol_factory import SymbolFactory
from ui.equipment.symbol.symbol_registry import SymbolRegistry
from ui.sld.sld_equipment_identity import equipment_type_for_semantic
from ui.sld.sld_vocabulary import SLD_SEMANTIC_TYPES, semantic_type

from .sld_canvas_projection import SLDCanvasNode


@dataclass(frozen=True, slots=True)
class PresentationSelection:
    """Immutable resolved presentation identity."""

    semantic_type: str
    equipment_type: str
    symbol_id: str
    representation_id: str = "symbol"
    symbol_instance: SymbolBase | None = None

    def __post_init__(self) -> None:
        for name in ("semantic_type", "equipment_type", "symbol_id", "representation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


class SemanticPresentationRealization:
    """Orchestrate canonical semantic, equipment, and symbol authorities."""

    def __init__(self, equipment_registry: EquipmentRegistry, symbol_registry: SymbolRegistry) -> None:
        if not isinstance(equipment_registry, EquipmentRegistry):
            raise TypeError("equipment_registry must be an EquipmentRegistry")
        if not isinstance(symbol_registry, SymbolRegistry):
            raise TypeError("symbol_registry must be a SymbolRegistry")
        self._equipment_registry = equipment_registry
        self._symbol_registry = symbol_registry
        # Validate the complete advertised SLD vocabulary at composition time.
        # This keeps the vocabulary, EquipmentRegistry identity bridge, and
        # SymbolRegistry in lockstep instead of discovering missing coverage
        # only after a persisted node reaches the canvas renderer.
        for semantic in SLD_SEMANTIC_TYPES:
            equipment_type = equipment_type_for_semantic(semantic)
            definition = self._equipment_registry.require(equipment_type)
            self._symbol_registry.require(definition.symbol_id)
        # Use the canonical SymbolFactory with the already-composed registry.
        # The factory creates only renderer-neutral instances; SymbolRegistry
        # remains the sole symbol-definition authority.
        self._symbol_factory = SymbolFactory(symbol_registry)

    @property
    def equipment_registry(self) -> EquipmentRegistry:
        return self._equipment_registry

    @property
    def symbol_registry(self) -> SymbolRegistry:
        return self._symbol_registry

    @property
    def symbol_factory(self) -> SymbolFactory:
        """Return the canonical factory backed by the composed SymbolRegistry."""
        return self._symbol_factory

    def realize(self, node: SLDCanvasNode) -> PresentationSelection:
        if not isinstance(node, SLDCanvasNode):
            raise TypeError("node must be an SLDCanvasNode")
        element_type = node.properties.get("element_type")
        canonical_semantic = semantic_type(element_type)
        equipment_type = equipment_type_for_semantic(canonical_semantic)
        definition = self._equipment_registry.require(equipment_type)

        if node.presentation is None:
            symbol_instance = self._symbol_factory.create(definition.symbol_id)
        else:
            symbol_instance = node.presentation

        # EquipmentDefinition is the semantic-to-symbol authority. A
        # persisted presentation may retain transforms/visibility, but it may
        # not redirect a valid equipment node to another symbol definition.
        if symbol_instance.symbol_id != definition.symbol_id:
            raise ValueError(
                f"SLD canonical symbol mismatch for {equipment_type!r}: "
                f"equipment definition requires {definition.symbol_id!r}, "
                f"presentation supplied {symbol_instance.symbol_id!r}"
            )
        if symbol_instance.definition_id != definition.symbol_id:
            raise ValueError(
                f"SLD symbol definition identity mismatch for {equipment_type!r}: "
                f"{symbol_instance.definition_id!r} != {definition.symbol_id!r}"
            )
        if symbol_instance.representation_id != "symbol":
            raise ValueError(
                f"Unsupported SLD symbol representation: "
                f"{symbol_instance.representation_id!r}"
            )

        self._symbol_registry.require(symbol_instance.symbol_id)
        return PresentationSelection(
            semantic_type=canonical_semantic,
            equipment_type=equipment_type,
            symbol_id=symbol_instance.symbol_id,
            representation_id=symbol_instance.representation_id,
            symbol_instance=SymbolBase.from_dict(symbol_instance.to_dict()),
        )


__all__ = ["PresentationSelection", "SemanticPresentationRealization"]
