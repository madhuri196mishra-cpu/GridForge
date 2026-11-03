"""Lifecycle-boundary adapter producing detached Application SLD export snapshots.

Invoke this adapter from the serialized workspace/document lifecycle operation.
The identity/revision checks below are fail-closed consistency guards, not an
atomic lock; concurrent mutation is not made impossible by this adapter.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Mapping

from core.application.sld_export_snapshot import (
    SLDAnnotationExportSnapshot, SLDConnectionExportSnapshot,
    SLDExportSnapshot, SLDNodeExportSnapshot, SLDSymbolPlacementSnapshot,
)
from ui.canvas.sld_canvas_projection import SLDCanvasProjection
from ui.sld.sld_document import SLDDocument
from ui.sld.sld_model import SLDModel
from ui.workspace.document_manager import DocumentManager


def _plain_mapping(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return dict(result)
    if is_dataclass(value):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    names = ("node_id", "terminal_id", "endpoint_id", "junction_id", "role", "kind")
    result = {name: getattr(value, name) for name in names if hasattr(value, name)}
    return result or None


class SLDExportSnapshotCapture:
    """Capture the active persisted SLD document without exposing UI objects to Application."""

    def capture(
        self, application: object, document_manager: DocumentManager,
        projection: SLDCanvasProjection, *, project_id: str, document_id: str,
    ) -> SLDExportSnapshot:
        if not all(hasattr(application, name) for name in ("revision", "project", "presentation")):
            raise TypeError("application must expose revision, project, and presentation")
        if not isinstance(document_manager, DocumentManager):
            raise TypeError("document_manager must be DocumentManager")
        if not isinstance(projection, SLDCanvasProjection):
            raise TypeError("projection must be SLDCanvasProjection")
        active = document_manager.active_document
        if not isinstance(active, SLDDocument) or active.document_id != document_id:
            raise RuntimeError("requested SLD document is not the active registered document")
        model = active.model
        if not isinstance(model, SLDModel):
            raise RuntimeError("active SLD document has no valid persisted SLDModel")
        if application.presentation is not active:
            raise RuntimeError("Application presentation does not match active SLD document")
        context = application.project
        if context is None or context.project_id != project_id:
            raise RuntimeError("requested project does not match active Application project")
        if active.project_id is not None and active.project_id != project_id:
            raise RuntimeError("active SLD document belongs to another project")

        revision_before = str(application.revision)
        active_id_before = document_manager.active_document_id
        state_before = active.to_dict()
        projected = projection.project(model, annotation_blocks=active.annotation_blocks)

        nodes = []
        for node in projected.nodes:
            symbol = node.presentation
            symbol_snapshot = None if symbol is None else SLDSymbolPlacementSnapshot(
                symbol_instance_id=symbol.symbol_id,
                symbol_definition_id=symbol.definition_id,
                representation_id=symbol.representation_id,
                visible=symbol.visible, scale=symbol.scale,
                rotation_degrees=symbol.rotation, properties=dict(symbol.properties),
            )
            read_model = node.read_model
            attributes = {} if read_model is None else dict(read_model.attributes)
            units = {} if read_model is None else {
                item.parameter_id: item.unit
                for item in read_model.engineering_parameters if item.unit is not None
            }
            nodes.append(SLDNodeExportSnapshot(
                node_id=node.node_id, equipment_id=node.equipment_id,
                x=node.x, y=node.y, properties=dict(node.properties),
                symbol=symbol_snapshot, engineering_attributes=attributes,
                engineering_units=units,
            ))

        connections = tuple(SLDConnectionExportSnapshot(
            connection_id=item.connection_id,
            source_node_id=item.source_node_id, target_node_id=item.target_node_id,
            source_endpoint=_plain_mapping(item.source_endpoint),
            target_endpoint=_plain_mapping(item.target_endpoint),
            route_points=tuple(tuple(point) for point in item.route.points),
            connection_kind=item.connection_kind,
            presentation_owner=item.presentation_owner,
            projection_source=item.projection_source,
            properties=dict(item.properties),
        ) for item in projected.connections)

        # Only document-persisted annotation blocks are exportable. Projection
        # overlays from transient study results are intentionally excluded.
        persisted_annotation_ids = {item.block_id for item in active.annotation_blocks}
        annotations = tuple(SLDAnnotationExportSnapshot(
            annotation_id=item.block_id, node_id=item.node_id,
            x=item.x, y=item.y, text=item.text, visible=item.visible,
        ) for item in projected.annotations if item.block_id in persisted_annotation_ids)

        snapshot = SLDExportSnapshot(
            project_id=project_id, document_id=document_id,
            source_revision=revision_before, nodes=tuple(nodes),
            connections=connections, annotations=annotations,
            coordinate_system="sld-document-local", units="sld-unit",
        )
        if str(application.revision) != revision_before:
            raise RuntimeError("Application revision changed during SLD export snapshot capture")
        if document_manager.active_document_id != active_id_before:
            raise RuntimeError("active document changed during SLD export snapshot capture")
        if document_manager.get(document_id) is not active or application.presentation is not active:
            raise RuntimeError("active document identity changed during SLD export snapshot capture")
        if active.model is not model or active.to_dict() != state_before:
            raise RuntimeError("persisted SLD document changed during export snapshot capture")
        if application.project is not context or context.project_id != project_id:
            raise RuntimeError("active project identity changed during SLD export snapshot capture")
        return snapshot


__all__ = ["SLDExportSnapshotCapture"]
