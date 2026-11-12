# ============================================================
# File: ui/sld/__init__.py
# GridForge V2 — SLD Presentation Subsystem
# Author: Subhendu Mishra
# ============================================================
"""GridForge V2 — SLD presentation subsystem.

SLD is the first-class electrical visual projection/editing surface.
It owns presentation semantics and layout coordination, but not the
authoritative Core electrical model or Qt canvas mechanics.

Exports are lazy because SLDDocument participates in the workspace/document
boundary and eager package imports otherwise create an import-time cycle.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "SLDModel": ("ui.sld.sld_model", "SLDModel"),
    "SLDNode": ("ui.sld.sld_model", "SLDNode"),
    "SLDConnection": ("ui.sld.sld_model", "SLDConnection"),
    "SLDAnnotationBlock": ("ui.sld.sld_model", "SLDAnnotationBlock"),
    "SLDPresentationLayer": ("ui.sld.sld_model", "SLDPresentationLayer"),
    "SLDDocument": ("ui.sld.sld_document", "SLDDocument"),
    "SLDState": ("ui.sld.sld_state", "SLDState"),
    "SLDController": ("ui.sld.sld_controller", "SLDController"),
    "SLDLayout": ("ui.sld.sld_layout", "SLDLayout"),
    "SLDPlacement": ("ui.sld.sld_layout", "SLDPlacement"),
    "SLDProjection": ("ui.sld.sld_projection", "SLDProjection"),
    "SLDProjectionManager": ("ui.sld.sld_projection_manager", "SLDProjectionManager"),
    "SLDReadSynchronizer": ("ui.sld.sld_read_synchronizer", "SLDReadSynchronizer"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = list(_EXPORTS)

from .simple_wire_inspector import SimpleWireInspector
from .simple_wire_selection import delete_selected_simple_wire

__all__ = [*globals().get('__all__', ()), "SimpleWireInspector", "delete_selected_simple_wire"]

from .instrumentation_wiring_projection import (
    SLDPhysicalWiringSegment, SLDPhysicalWiringRecord,
    SLDInstrumentationWiringProjection, SLDInstrumentationWiringAdapter,
)
__all__ = [*__all__, "SLDPhysicalWiringSegment", "SLDPhysicalWiringRecord", "SLDInstrumentationWiringProjection", "SLDInstrumentationWiringAdapter"]
