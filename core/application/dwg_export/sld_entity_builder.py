"""Build CAD-neutral SLD entities from persisted symbol presentation and the export symbol registry.

This builder serializes only authored SLD presentation. It does not create electrical
connections from node coordinates and deliberately fails when a symbol definition
has no explicit profile-specific CAD mapping.
"""
from __future__ import annotations

from ui.canvas.sld_canvas_projection import SLDCanvasSnapshot

from .drawing_plan import DrawingEntity, SymbolProfile
from .symbol_registry import SymbolRegistry


class SLDNodeEntityBuilder:
    """Translate persisted node presentation into stable CAD INSERT entities."""

    def __init__(self, registry: SymbolRegistry) -> None:
        if not isinstance(registry, SymbolRegistry):
            raise TypeError("registry must be a DWG SymbolRegistry")
        self._registry = registry

    def build(
        self,
        projection: SLDCanvasSnapshot,
        *,
        profile: SymbolProfile,
        include_hidden: bool = False,
    ) -> tuple[DrawingEntity, ...]:
        if not isinstance(projection, SLDCanvasSnapshot):
            raise TypeError("projection must be an SLDCanvasSnapshot")
        try:
            profile = profile if isinstance(profile, SymbolProfile) else SymbolProfile(profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc

        entities: list[DrawingEntity] = []
        for node in projection.nodes:
            presentation = node.presentation
            if presentation is None:
                raise ValueError(
                    f"SLD node {node.node_id!r} has no persisted symbol presentation; "
                    "refusing to guess a CAD symbol"
                )
            if not include_hidden and not presentation.visible:
                continue

            # definition_id is the persisted link to the symbol definition. Resolve
            # that exact identity; never infer a symbol from equipment_id or node name.
            symbol = self._registry.resolve(presentation.definition_id, profile)
            entity_id = f"sld-node:{node.node_id}"
            entities.append(DrawingEntity(
                entity_id=entity_id,
                kind="INSERT",
                layer="SLD_SYMBOLS",
                geometry={
                    "symbol_id": symbol.symbol_id,
                    "insert": (float(node.x), float(node.y)),
                    "scale": float(presentation.scale),
                    "rotation": float(presentation.rotation),
                },
                metadata={
                    "gridforge_node_id": node.node_id,
                    "equipment_id": node.equipment_id,
                    "symbol_instance_id": presentation.symbol_id,
                    "symbol_definition_id": presentation.definition_id,
                    "representation_id": presentation.representation_id,
                    "symbol_profile": profile.value,
                    "source_kind": "persisted_sld_presentation",
                },
            ))
        entities.extend(self.build_connections(projection))
        return tuple(entities)

    @staticmethod
    def build_connections(projection: SLDCanvasSnapshot) -> tuple[DrawingEntity, ...]:
        """Export explicit persisted SLD connection geometry without inventing topology."""
        if not isinstance(projection, SLDCanvasSnapshot):
            raise TypeError("projection must be an SLDCanvasSnapshot")
        nodes = {node.node_id: node for node in projection.nodes}
        entities: list[DrawingEntity] = []
        for connection in projection.connections:
            source = nodes.get(connection.source_node_id)
            target = nodes.get(connection.target_node_id)
            if source is None or target is None:
                raise ValueError(
                    f"SLD connection {connection.connection_id!r} references a missing projected node"
                )
            # A persisted route is authoritative presentation. For an un-routed
            # explicit connection, node coordinates provide only its visible line
            # endpoints; the existence/meaning of the connection comes from the
            # persisted SLDConnection, never from geometric proximity.
            points = tuple(connection.route.points)
            if len(points) < 2:
                points = ((float(source.x), float(source.y)), (float(target.x), float(target.y)))
            kind = connection.connection_kind or "unspecified"
            layer = "SLD_CONNECTIONS" if kind == "unspecified" else f"SLD_{kind.upper()}"
            entities.append(DrawingEntity(
                entity_id=f"sld-connection:{connection.connection_id}",
                kind="LWPOLYLINE",
                layer=layer,
                geometry={"points": points, "closed": False},
                metadata={
                    "gridforge_connection_id": connection.connection_id,
                    "source_node_id": connection.source_node_id,
                    "target_node_id": connection.target_node_id,
                    "connection_kind": connection.connection_kind,
                    "presentation_owner": connection.presentation_owner,
                    "projection_source": connection.projection_source,
                    "route_ownership": connection.route.ownership,
                    "routing_mode": connection.route.routing_mode,
                    "core_connection_id": connection.properties.get("core_connection_id"),
                    "source_kind": "persisted_sld_connection",
                },
            ))
        return tuple(entities)


__all__ = ["SLDNodeEntityBuilder"]
