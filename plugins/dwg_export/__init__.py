"""Optional GridForge engineering plugin for backend-neutral DWG export."""
from .drawing_plan import (
    DrawingEntity, DrawingPlan, SymbolProfile,
    drawing_plan_to_dict, serialize_drawing_plan,
)
from .service import DWGExportService, ExportResult
from .symbol_registry import SymbolDefinition, SymbolRegistry
from .symbol_catalogue import (
    SymbolReviewEvidence, build_reviewed_symbol_registry, symbol_geometry_digest,
    convert_sld_catalogue_primitives, SymbolCatalogueEntry,
    assemble_unreviewed_symbol_definitions, SymbolCatalogueCoverage,
    symbol_catalogue_coverage,
)
from .source_snapshot import ApplicationDrawingSnapshot, DrawingPlanFactory
from .application_adapter import ApplicationDrawingSnapshotAdapter
from .sld_projection_adapter import SLDProjectionDrawingSnapshotAdapter
from .control_projection_adapter import ControlProjectionDrawingSnapshotAdapter
from .control_entity_builder import ControlDrawingEntityBuilder, ControlDrawingLayout
from .coordinate_transform import SLDCoordinateTransform
from .control_coordinate_transform import ControlCoordinateTransform
from .sld_entity_builder import SLDNodeEntityBuilder
from .validation import DrawingPlanValidationError, validate_drawing_plan
from .backends import ODAFileConverterBackend
from .plugin import DWGExportConfiguration, DWGExportPlugin, PLUGIN_DESCRIPTOR

__all__ = [
    "DrawingEntity", "DrawingPlan", "SymbolProfile", "drawing_plan_to_dict",
    "serialize_drawing_plan", "DWGExportService", "ExportResult",
    "DrawingPlanValidationError", "validate_drawing_plan", "SymbolDefinition",
    "SymbolRegistry", "SymbolReviewEvidence", "build_reviewed_symbol_registry",
    "symbol_geometry_digest", "convert_sld_catalogue_primitives",
    "SymbolCatalogueEntry", "assemble_unreviewed_symbol_definitions",
    "SymbolCatalogueCoverage", "symbol_catalogue_coverage",
    "ApplicationDrawingSnapshot", "DrawingPlanFactory",
    "ApplicationDrawingSnapshotAdapter", "SLDProjectionDrawingSnapshotAdapter", "ControlProjectionDrawingSnapshotAdapter", "ControlDrawingEntityBuilder", "ControlDrawingLayout", "SLDCoordinateTransform", "ControlCoordinateTransform",
    "SLDNodeEntityBuilder", "ODAFileConverterBackend",
    "DWGExportConfiguration", "DWGExportPlugin", "PLUGIN_DESCRIPTOR",
]
