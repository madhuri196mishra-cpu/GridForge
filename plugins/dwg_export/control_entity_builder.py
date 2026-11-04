"""Build independent ladder-drawing entities from the Application Control read model.

Coordinates are local millimetres. They are a deterministic drawing layout,
not physical equipment coordinates or an assertion about runtime wiring routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

from core.application.services.control_service import ControlProgramReadModel

from .drawing_plan import DrawingEntity, SymbolProfile
from .symbol_registry import SymbolRegistry


@dataclass(frozen=True, slots=True)
class ControlDrawingLayout:
    """Explicit CAD layout dimensions, independent of the Qt canvas geometry."""

    origin: tuple[float, float] = (0.0, 0.0)
    component_pitch_mm: float = 120.0
    rung_pitch_mm: float = 100.0
    rail_width_mm: float = 900.0
    rail_extension_mm: float = 30.0

    def __post_init__(self) -> None:
        if len(self.origin) != 2 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value))
            for value in self.origin
        ):
            raise ValueError("origin must contain two finite millimetre coordinates")
        for name in ("component_pitch_mm", "rung_pitch_mm", "rail_width_mm", "rail_extension_mm"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        object.__setattr__(self, "origin", (float(self.origin[0]), float(self.origin[1])))


class ControlDrawingEntityBuilder:
    """Translate persisted rung order, component positions and signal links."""

    def __init__(
        self,
        registry: SymbolRegistry,
        *,
        symbol_ids: Mapping[str, str],
        layout: ControlDrawingLayout = ControlDrawingLayout(),
    ) -> None:
        if not isinstance(registry, SymbolRegistry):
            raise TypeError("registry must be a DWG SymbolRegistry")
        if not isinstance(symbol_ids, Mapping) or not symbol_ids:
            raise ValueError("symbol_ids must explicitly map Control component types to reviewed symbol IDs")
        normalized = {str(key).strip(): str(value).strip() for key, value in symbol_ids.items()}
        if any(not key or not value for key, value in normalized.items()):
            raise ValueError("symbol_ids cannot contain empty component types or symbol IDs")
        if len(normalized) != len(symbol_ids):
            raise ValueError("symbol_ids component types must be unique after normalization")
        if not isinstance(layout, ControlDrawingLayout):
            raise TypeError("layout must be a ControlDrawingLayout")
        self._registry = registry
        self._symbol_ids = normalized
        self._layout = layout

    def build(
        self,
        read_model: ControlProgramReadModel,
        profile: SymbolProfile,
    ) -> tuple[DrawingEntity, ...]:
        if not isinstance(read_model, ControlProgramReadModel):
            raise TypeError("read_model must be a ControlProgramReadModel")
        try:
            profile = profile if isinstance(profile, SymbolProfile) else SymbolProfile(profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc

        rungs = {rung.rung_id: rung for rung in read_model.rungs}
        if len(rungs) != len(read_model.rungs):
            raise ValueError("Control read model contains duplicate rung identities")
        components = {component.component_id: component for component in read_model.components}
        if len(components) != len(read_model.components):
            raise ValueError("Control read model contains duplicate component identities")

        positions: dict[str, tuple[float, float]] = {}
        anchors: dict[tuple[str, str, str], tuple[float, float]] = {}
        entities: list[DrawingEntity] = []
        ox, oy = self._layout.origin

        for rung in sorted(read_model.rungs, key=lambda item: (item.order, item.rung_id)):
            if isinstance(rung.order, bool) or not isinstance(rung.order, int) or rung.order < 0:
                raise ValueError(f"Control rung {rung.rung_id!r} has an invalid order")
            y = oy + rung.order * self._layout.rung_pitch_mm
            entities.append(DrawingEntity(
                entity_id=f"control-rung:{rung.rung_id}",
                kind="LINE",
                layer="CONTROL_RUNG" if rung.enabled else "CONTROL_RUNG_DISABLED",
                geometry={"start": (ox, y), "end": (ox + self._layout.rail_width_mm, y)},
                metadata={"rung_id": rung.rung_id, "rung_order": rung.order, "enabled": rung.enabled,
                          "source_kind": "persisted_control_rung"},
            ))

        if read_model.rungs:
            orders = [rung.order for rung in read_model.rungs]
            top = oy + min(orders) * self._layout.rung_pitch_mm - self._layout.rail_extension_mm
            bottom = oy + max(orders) * self._layout.rung_pitch_mm + self._layout.rail_extension_mm
            for rail, x in (("left", ox), ("right", ox + self._layout.rail_width_mm)):
                entities.append(DrawingEntity(
                    entity_id=f"control-rail:{rail}",
                    kind="LINE",
                    layer="CONTROL_RAILS",
                    geometry={"start": (x, top), "end": (x, bottom)},
                    metadata={"rail": rail, "source_kind": "control_ladder_layout"},
                ))

        for component in sorted(read_model.components, key=lambda item: item.component_id):
            rung = rungs.get(component.rung_id) if component.rung_id is not None else None
            if rung is None:
                rung = next((item for item in read_model.rungs if component.component_id in item.component_ids), None)
            if rung is None:
                raise ValueError(f"Control component {component.component_id!r} has no persisted rung placement")
            position = component.position
            if position is None:
                position = rung.positions.get(component.component_id)
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                raise ValueError(f"Control component {component.component_id!r} has no valid persisted position")
            if component.component_id not in rung.component_ids and component.component_id not in rung.positions:
                raise ValueError(f"Control rung {rung.rung_id!r} does not contain component {component.component_id!r}")
            if component.component_type not in self._symbol_ids:
                raise ValueError(f"No explicit Control symbol mapping for component type {component.component_type!r}")
            definition = self._registry.resolve(self._symbol_ids[component.component_type], profile)
            x = ox + position * self._layout.component_pitch_mm
            y = oy + rung.order * self._layout.rung_pitch_mm
            positions[component.component_id] = (x, y)

            required_anchors = {
                *(f"input:{name}" for name in component.inputs),
                *(f"output:{name}" for name in component.outputs),
            }
            missing = sorted(required_anchors.difference(definition.terminal_anchors))
            if missing:
                raise ValueError(
                    f"Control symbol {definition.symbol_id!r} lacks required port anchors: {', '.join(missing)}"
                )
            for port_name in component.inputs:
                local_x, local_y = definition.terminal_anchors[f"input:{port_name}"]
                anchors[(component.component_id, "input", port_name)] = (x + local_x, y + local_y)
            for port_name in component.outputs:
                local_x, local_y = definition.terminal_anchors[f"output:{port_name}"]
                anchors[(component.component_id, "output", port_name)] = (x + local_x, y + local_y)

            entities.append(DrawingEntity(
                entity_id=f"control-component:{component.component_id}",
                kind="INSERT",
                layer="CONTROL_COMPONENTS",
                geometry={"symbol_id": definition.symbol_id, "insert": (x, y), "scale": (1.0, 1.0, 1.0), "rotation": 0.0},
                metadata={
                    "control_component_id": component.component_id,
                    "component_type": component.component_type,
                    "rung_id": rung.rung_id,
                    "rung_order": rung.order,
                    "position": position,
                    "enabled": rung.enabled,
                    "component_configuration": dict(component.configuration),
                    "symbol_definition_version": definition.definition_version,
                    "symbol_registry_schema_version": self._registry.schema_version,
                    "symbol_catalogue_version": self._registry.catalogue_version,
                    "symbol_registry_fingerprint": self._registry.fingerprint,
                    "symbol_terminal_anchors": {name: definition.terminal_anchors[name] for name in sorted(definition.terminal_anchors)},
                    "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
                    "source_project_id": read_model.project_id,
                    "source_program_id": read_model.program_id,
                    "source_kind": "persisted_control_component",
                },
            ))

        seen_connections: set[tuple[str, str, str, str]] = set()
        for connection in sorted(
            read_model.connections,
            key=lambda item: (item.source_component, item.source_output, item.target_component, item.target_input),
        ):
            identity = (connection.source_component, connection.source_output, connection.target_component, connection.target_input)
            if identity in seen_connections:
                raise ValueError(f"Duplicate Control signal connection {identity!r}")
            seen_connections.add(identity)
            source = anchors.get((connection.source_component, "output", connection.source_output))
            target = anchors.get((connection.target_component, "input", connection.target_input))
            if source is None or target is None:
                raise ValueError(f"Control connection {identity!r} references an unknown component port")
            if source == target:
                raise ValueError(f"Control connection {identity!r} has zero-length display geometry")
            entities.append(DrawingEntity(
                entity_id="control-connection:" + ":".join(identity),
                kind="LWPOLYLINE",
                layer="CONTROL_SIGNAL_CONNECTIONS",
                geometry={"points": (source, target), "closed": False},
                metadata={
                    "source_component_id": connection.source_component,
                    "source_output": connection.source_output,
                    "target_component_id": connection.target_component,
                    "target_input": connection.target_input,
                    "route_geometry_source": "logical_connection_display_chord",
                    "source_kind": "persisted_control_signal_connection",
                    "source_project_id": read_model.project_id,
                    "source_program_id": read_model.program_id,
                },
            ))

        return tuple(sorted(entities, key=lambda item: item.entity_id))


__all__ = ["ControlDrawingLayout", "ControlDrawingEntityBuilder"]
