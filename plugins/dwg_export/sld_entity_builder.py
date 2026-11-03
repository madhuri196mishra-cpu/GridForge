"""Build CAD-neutral SLD entities from detached persisted SLD snapshots."""
from __future__ import annotations

import re

from core.application.sld_export_snapshot import SLDExportSnapshot

from .drawing_plan import DrawingEntity, SymbolProfile
from .symbol_registry import SymbolRegistry


class SLDNodeEntityBuilder:
    """Translate stable SLD identities and authored presentation into CAD entities."""

    def __init__(self, registry: SymbolRegistry) -> None:
        if not isinstance(registry, SymbolRegistry):
            raise TypeError("registry must be a DWG SymbolRegistry")
        self._registry = registry

    def build(
        self,
        snapshot: SLDExportSnapshot,
        *,
        profile: SymbolProfile,
        include_hidden: bool = False,
    ) -> tuple[DrawingEntity, ...]:
        if not isinstance(snapshot, SLDExportSnapshot):
            raise TypeError("snapshot must be an SLDExportSnapshot")
        try:
            profile = profile if isinstance(profile, SymbolProfile) else SymbolProfile(profile)
        except (TypeError, ValueError) as exc:
            raise ValueError("profile must be ansi_ieee or iec_60617") from exc

        entities: list[DrawingEntity] = []
        nodes = {node.node_id: node for node in snapshot.nodes}
        for node in sorted(snapshot.nodes, key=lambda item: item.node_id):
            symbol = node.symbol
            if symbol is None:
                raise ValueError(
                    f"SLD node {node.node_id!r} has no persisted symbol definition; "
                    "refusing to infer a CAD symbol"
                )
            if not include_hidden and not symbol.visible:
                continue
            definition = self._registry.resolve(symbol.symbol_definition_id, profile)
            entities.append(DrawingEntity(
                entity_id=f"sld-node:{node.node_id}",
                kind="INSERT",
                layer="SLD_SYMBOLS",
                geometry={
                    "symbol_id": definition.symbol_id,
                    "insert": (node.x, node.y),
                    "scale": symbol.scale,
                    "rotation": symbol.rotation_degrees,
                },
                metadata={
                    "gridforge_node_id": node.node_id,
                    "equipment_id": node.equipment_id,
                    "symbol_instance_id": symbol.symbol_instance_id,
                    "symbol_definition_id": symbol.symbol_definition_id,
                    "symbol_definition_version": definition.definition_version,
                    "symbol_review_status": definition.review_status,
                    "symbol_reviewer": definition.reviewer,
                    "symbol_reviewed_on": definition.reviewed_on,
                    "symbol_review_reference": definition.review_reference,
                    "symbol_standard_reference": definition.standard_reference,
                    "symbol_registry_schema_version": self._registry.schema_version,
                    "symbol_catalogue_version": self._registry.catalogue_version,
                    "symbol_registry_fingerprint": self._registry.fingerprint,
                    "representation_id": symbol.representation_id,
                    "symbol_profile": profile.value,
                    "symbol_definition_base_point": definition.base_point,
                    "symbol_instance_properties": dict(symbol.properties),
                    "presentation_properties": dict(node.properties),
                    "engineering_attributes": dict(node.engineering_attributes),
                    "engineering_units": dict(node.engineering_units),
                    "source_project_id": snapshot.project_id,
                    "source_document_id": snapshot.document_id,
                    "source_revision": snapshot.source_revision,
                    "source_kind": "persisted_sld_presentation",
                },
            ))

        for connection in sorted(snapshot.connections, key=lambda item: item.connection_id):
            source = nodes.get(connection.source_node_id)
            target = nodes.get(connection.target_node_id)
            if source is None or target is None:
                raise ValueError(
                    f"SLD connection {connection.connection_id!r} references a missing node"
                )
            # The persisted connection establishes connectivity. Preserve its
            # authored route verbatim; do not prepend node positions or fabricate
            # bends. A direct display chord is used only when the persisted route
            # contains no points, and is explicitly identified as a fallback.
            has_authored_route = bool(connection.route_points)
            candidates = (
                tuple(connection.route_points)
                if has_authored_route
                else ((source.x, source.y), (target.x, target.y))
            )
            points: list[tuple[float, float]] = []
            for point in candidates:
                normalized = (float(point[0]), float(point[1]))
                if not points or normalized != points[-1]:
                    points.append(normalized)
            if len(points) < 2:
                raise ValueError(
                    f"SLD connection {connection.connection_id!r} has no usable persisted route geometry"
                )
            kind = connection.connection_kind or "unspecified"
            normalized_kind = re.sub(r'[<>/\\":;?*|=,\s]+', "_", kind.upper()).strip("._")
            layer = "SLD_CONNECTIONS" if not normalized_kind or normalized_kind == "UNSPECIFIED" else f"SLD_{normalized_kind}"[:255]
            entities.append(DrawingEntity(
                entity_id=f"sld-connection:{connection.connection_id}",
                kind="LWPOLYLINE",
                layer=layer,
                geometry={"points": tuple(points), "closed": False},
                metadata={
                    "gridforge_connection_id": connection.connection_id,
                    "source_node_id": connection.source_node_id,
                    "target_node_id": connection.target_node_id,
                    "source_endpoint": None if connection.source_endpoint is None else dict(connection.source_endpoint),
                    "target_endpoint": None if connection.target_endpoint is None else dict(connection.target_endpoint),
                    "connection_kind": connection.connection_kind,
                    "route_routing_mode": connection.route_routing_mode,
                    "route_ownership": connection.route_ownership,
                    "route_geometry_source": "persisted_route" if has_authored_route else "node_position_display_fallback",
                    "presentation_owner": connection.presentation_owner,
                    "projection_source": connection.projection_source,
                    "persisted_connection_properties": dict(connection.properties),
                    "core_connection_id": connection.properties.get("core_connection_id"),
                    "source_project_id": snapshot.project_id,
                    "source_document_id": snapshot.document_id,
                    "source_revision": snapshot.source_revision,
                    "source_kind": "persisted_sld_connection",
                },
            ))

        for annotation in sorted(snapshot.annotations, key=lambda item: item.annotation_id):
            if not annotation.visible or not annotation.text:
                continue
            if "\n" in annotation.text or "\r" in annotation.text:
                raise ValueError(f"SLD annotation {annotation.annotation_id!r} is multiline; TEXT export cannot preserve it")
            if annotation.node_id not in nodes:
                raise ValueError(f"SLD annotation {annotation.annotation_id!r} references a missing node")
            entities.append(DrawingEntity(
                entity_id=f"sld-annotation:{annotation.annotation_id}",
                kind="TEXT",
                layer="SLD_ANNOTATIONS",
                geometry={"insert": (annotation.x, annotation.y), "text": annotation.text, "height": 2.5},
                metadata={
                    "gridforge_annotation_id": annotation.annotation_id,
                    "node_id": annotation.node_id,
                    "source_project_id": snapshot.project_id,
                    "source_document_id": snapshot.document_id,
                    "source_revision": snapshot.source_revision,
                },
            ))
        return tuple(sorted(entities, key=lambda item: item.entity_id))


__all__ = ["SLDNodeEntityBuilder"]
