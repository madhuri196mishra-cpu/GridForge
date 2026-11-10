"""Build CAD-neutral SLD entities from detached persisted SLD snapshots."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

from core.application.sld_export_snapshot import SLDExportSnapshot

from .drawing_plan import DrawingEntity, SymbolProfile
from .symbol_registry import SymbolRegistry


def _symbol_local_to_sld(
    point: tuple[float, float],
    *,
    scale: float,
    rotation_degrees: float,
    mirror_x: bool,
    mirror_y: bool,
) -> tuple[float, float]:
    """Apply the same local reflection, scale and rotation as EquipmentItem."""
    x = float(point[0]) * scale * (-1.0 if mirror_x else 1.0)
    y = float(point[1]) * scale * (-1.0 if mirror_y else 1.0)
    angle = math.radians(rotation_degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    return (x * cosine - y * sine, x * sine + y * cosine)


def _terminal_anchor_offset(
    node: object,
    endpoint: Mapping[str, object] | None,
    *,
    registry: SymbolRegistry,
    profile: SymbolProfile,
) -> tuple[float, float] | None:
    """Resolve an explicit equipment endpoint to its transformed local-mm anchor."""
    if endpoint is None or endpoint.get("kind") != "equipment":
        return None
    node_id = getattr(node, "node_id", None)
    if endpoint.get("node_id") != node_id:
        raise ValueError("SLD equipment endpoint node identity does not match its connection node")
    equipment_id = endpoint.get("equipment_id")
    if getattr(node, "equipment_id", None) != equipment_id:
        raise ValueError("SLD equipment endpoint identity does not match its persisted node")
    symbol = getattr(node, "symbol", None)
    if symbol is None:
        raise ValueError(
            f"SLD equipment endpoint {equipment_id!r} has no persisted symbol placement"
        )
    definition = registry.resolve(symbol.symbol_definition_id, profile)
    terminal_role = endpoint.get("terminal_role")
    if not isinstance(terminal_role, str) or not terminal_role.strip():
        raise ValueError("SLD equipment endpoint requires an explicit terminal role")
    anchor_name = terminal_role.strip()
    if anchor_name not in definition.terminal_anchors:
        matches = [
            name for name in definition.terminal_anchors
            if name.casefold() == anchor_name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Reviewed symbol {definition.symbol_id!r} has no unique terminal anchor "
                f"for endpoint role {anchor_name!r}"
            )
        anchor_name = matches[0]
    return _symbol_local_to_sld(
        definition.terminal_anchors[anchor_name],
        scale=symbol.scale,
        rotation_degrees=symbol.rotation_degrees,
        mirror_x=bool(symbol.properties.get("mirror_x", False)),
        mirror_y=bool(symbol.properties.get("mirror_y", False)),
    )


class SLDNodeEntityBuilder:
    """Translate stable SLD identities and authored presentation into CAD entities."""

    def __init__(self, registry: SymbolRegistry) -> None:
        if not isinstance(registry, SymbolRegistry):
            raise TypeError("registry must be a DWG SymbolRegistry")
        self._registry = registry

    def build(
        self,
        snapshot: SLDExportSnapshot,
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
            mirror_x = bool(symbol.properties.get("mirror_x", False))
            mirror_y = bool(symbol.properties.get("mirror_y", False))
            signed_x_scale = symbol.scale * (-1.0 if mirror_x else 1.0)
            signed_y_scale = symbol.scale * (-1.0 if mirror_y else 1.0)

            # Symbol definition geometry and anchors are local millimetres.
            # Resolve instance reflection/scale/rotation as millimetre vectors;
            # the later document-to-world mapping rotates these vectors but
            # must not apply the document-unit scale to symbol dimensions.
            base_point_offset_mm = _symbol_local_to_sld(
                definition.base_point,
                scale=symbol.scale,
                rotation_degrees=symbol.rotation_degrees,
                mirror_x=mirror_x,
                mirror_y=mirror_y,
            )
            terminal_anchor_offsets_mm = {
                name: _symbol_local_to_sld(
                    anchor,
                    scale=symbol.scale,
                    rotation_degrees=symbol.rotation_degrees,
                    mirror_x=mirror_x,
                    mirror_y=mirror_y,
                )
                for name, anchor in sorted(definition.terminal_anchors.items())
            }
            entities.append(DrawingEntity(
                entity_id=f"sld-node:{node.node_id}",
                kind="INSERT",
                layer="SLD_SYMBOLS",
                geometry={
                    "symbol_id": definition.symbol_id,
                    "insert": (float(node.x), float(node.y)),
                    "scale": (signed_x_scale, signed_y_scale, 1.0),
                    "rotation": symbol.rotation_degrees,
                },
                metadata={
                    "gridforge_node_id": node.node_id,
                    "equipment_id": node.equipment_id,
                    "symbol_instance_id": symbol.symbol_instance_id,
                    "symbol_definition_id": symbol.symbol_definition_id,
                    "symbol_definition_version": definition.definition_version,
                    "symbol_definition_units": definition.units,
                    "symbol_source_units": definition.source_units,
                    "symbol_unit_to_mm": definition.unit_to_mm,
                    "symbol_unit_mapping_reference": definition.unit_mapping_reference,
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
                    "symbol_terminal_anchors": {
                        name: definition.terminal_anchors[name]
                        for name in sorted(definition.terminal_anchors)
                    },
                    "symbol_terminal_anchor_coordinate_system": "symbol_local_mm",
                    "symbol_base_point_offset_mm": base_point_offset_mm,
                    "symbol_base_point_offset_coordinate_system": "sld-symbol-instance-mm-vector",
                    "symbol_terminal_anchor_offsets_mm": terminal_anchor_offsets_mm,
                    "symbol_terminal_anchor_offsets_coordinate_system": "sld-symbol-instance-mm-vector",
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
            # Persisted route points are interior bends, not endpoints. The
            # connection's stable source/target node identities define the
            # endpoints; include both positions around the authored bends.
            # When no bends were authored, the node-to-node chord is an
            # explicitly identified display fallback for this persisted edge.
            has_authored_route = bool(connection.route_points)
            candidates = (
                ((source.x, source.y), *tuple(connection.route_points), (target.x, target.y))
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
            source_anchor_offset = _terminal_anchor_offset(
                source, connection.source_endpoint,
                registry=self._registry, profile=profile,
            )
            target_anchor_offset = _terminal_anchor_offset(
                target, connection.target_endpoint,
                registry=self._registry, profile=profile,
            )
            connection_metadata = {
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
            }
            if source_anchor_offset is not None:
                connection_metadata["source_terminal_anchor_offset_mm"] = source_anchor_offset
                connection_metadata["source_terminal_anchor_offset_coordinate_system"] = "sld-symbol-instance-mm-vector"
            if target_anchor_offset is not None:
                connection_metadata["target_terminal_anchor_offset_mm"] = target_anchor_offset
                connection_metadata["target_terminal_anchor_offset_coordinate_system"] = "sld-symbol-instance-mm-vector"
            entities.append(DrawingEntity(
                entity_id=f"sld-connection:{connection.connection_id}",
                kind="LWPOLYLINE",
                layer=layer,
                geometry={"points": tuple(points), "closed": False},
                metadata=connection_metadata,
            ))
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
