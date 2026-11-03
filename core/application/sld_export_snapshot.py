"""Detached, renderer-independent SLD export snapshot contracts.

These DTOs carry persisted presentation and engineering references across the
UI/Application boundary. They are not an electrical authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


def _freeze(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise TypeError(f"{path} keys must be non-empty strings")
            result[key] = _freeze(item, f"{path}.{key}")
        return MappingProxyType(result)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, f"{path}[{index}]") for index, item in enumerate(value))
    raise TypeError(f"{path} contains unsupported value {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class SLDSymbolPlacementSnapshot:
    symbol_instance_id: str
    symbol_definition_id: str
    representation_id: str
    visible: bool
    scale: float
    rotation_degrees: float
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("symbol_instance_id", "symbol_definition_id", "representation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.visible, bool):
            raise TypeError("visible must be boolean")
        for name in ("scale", "rotation_degrees"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or (name == "scale" and value <= 0):
                raise ValueError(f"{name} is outside its valid numeric range")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "properties", _freeze(self.properties, "symbol.properties"))


@dataclass(frozen=True, slots=True)
class SLDNodeExportSnapshot:
    node_id: str
    equipment_id: str | None
    x: float
    y: float
    properties: Mapping[str, Any]
    symbol: SLDSymbolPlacementSnapshot | None
    engineering_attributes: Mapping[str, Any] = field(default_factory=dict)
    engineering_units: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise ValueError("node_id must be non-empty")
        object.__setattr__(self, "node_id", self.node_id.strip())
        if self.equipment_id is not None and (not isinstance(self.equipment_id, str) or not self.equipment_id.strip()):
            raise ValueError("equipment_id must be non-empty when supplied")
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError(f"SLD node {self.node_id!r} has non-finite placement")
        object.__setattr__(self, "properties", _freeze(self.properties, "node.properties"))
        object.__setattr__(self, "engineering_attributes", _freeze(self.engineering_attributes, "node.engineering_attributes"))
        object.__setattr__(self, "engineering_units", _freeze(self.engineering_units, "node.engineering_units"))
        if self.symbol is not None and not isinstance(self.symbol, SLDSymbolPlacementSnapshot):
            raise TypeError("symbol must be SLDSymbolPlacementSnapshot or None")


@dataclass(frozen=True, slots=True)
class SLDConnectionExportSnapshot:
    connection_id: str
    source_node_id: str
    target_node_id: str
    source_endpoint: Mapping[str, Any] | None
    target_endpoint: Mapping[str, Any] | None
    route_points: tuple[tuple[float, float], ...]
    connection_kind: str | None
    presentation_owner: str | None
    projection_source: str | None
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("connection_id", "source_node_id", "target_node_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value.strip())
        for name in ("source_endpoint", "target_endpoint"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze(value, name))
        points = tuple((float(point[0]), float(point[1])) for point in self.route_points)
        if any(not (math.isfinite(x) and math.isfinite(y)) for x, y in points):
            raise ValueError(f"connection {self.connection_id!r} has non-finite route geometry")
        object.__setattr__(self, "route_points", points)
        object.__setattr__(self, "properties", _freeze(self.properties, "connection.properties"))


@dataclass(frozen=True, slots=True)
class SLDAnnotationExportSnapshot:
    annotation_id: str
    node_id: str
    x: float
    y: float
    text: str
    visible: bool

    def __post_init__(self) -> None:
        for name in ("annotation_id", "node_id", "text"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        if not self.annotation_id.strip() or not self.node_id.strip():
            raise ValueError("annotation_id and node_id must be non-empty")
        object.__setattr__(self, "x", float(self.x))
        object.__setattr__(self, "y", float(self.y))
        if not (math.isfinite(self.x) and math.isfinite(self.y)):
            raise ValueError("annotation coordinates must be finite")
        if not isinstance(self.visible, bool):
            raise TypeError("annotation visible must be boolean")


@dataclass(frozen=True, slots=True)
class SLDExportSnapshot:
    project_id: str
    document_id: str
    source_revision: str
    nodes: tuple[SLDNodeExportSnapshot, ...]
    connections: tuple[SLDConnectionExportSnapshot, ...]
    annotations: tuple[SLDAnnotationExportSnapshot, ...] = ()
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    coordinate_system: str = "sld-document-local"
    units: str = "sld-unit"

    def __post_init__(self) -> None:
        for name in ("project_id", "document_id", "source_revision", "snapshot_id", "coordinate_system", "units"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value.strip())
        nodes, connections, annotations = tuple(self.nodes), tuple(self.connections), tuple(self.annotations)
        if any(not isinstance(item, SLDNodeExportSnapshot) for item in nodes):
            raise TypeError("nodes must contain SLDNodeExportSnapshot values")
        if any(not isinstance(item, SLDConnectionExportSnapshot) for item in connections):
            raise TypeError("connections must contain SLDConnectionExportSnapshot values")
        if any(not isinstance(item, SLDAnnotationExportSnapshot) for item in annotations):
            raise TypeError("annotations must contain SLDAnnotationExportSnapshot values")
        if len({item.node_id for item in nodes}) != len(nodes):
            raise ValueError("SLD snapshot contains duplicate node IDs")
        if len({item.connection_id for item in connections}) != len(connections):
            raise ValueError("SLD snapshot contains duplicate connection IDs")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "connections", connections)
        object.__setattr__(self, "annotations", annotations)


__all__ = [
    "SLDSymbolPlacementSnapshot", "SLDNodeExportSnapshot",
    "SLDConnectionExportSnapshot", "SLDAnnotationExportSnapshot",
    "SLDExportSnapshot",
]
