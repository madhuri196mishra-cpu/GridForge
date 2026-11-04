# GridForge DWG Export Plugin

This optional engineering plugin owns CAD drawing plans, validation, symbol definitions, backend adapters, and export orchestration. It implements GridForge's existing `core.plugin_sdk` lifecycle contract; it does not add a plugin manager, command manager, event bus, engineering model, or Core mutation path.

## Plugin activation

`DWGExportPlugin` is activated explicitly through `EngineeringPluginHost`. The host supplies a `DWGExportConfiguration` in `EngineeringPluginContext.services['dwg_export_configuration']`; that configuration must name an installed ODA File Converter executable and an explicit `SymbolRegistry`. There is no default executable, implicit PATH lookup, default reviewed catalogue, or automatic activation. The plugin exposes its configured `DWGExportService` to its caller and releases its references on shutdown. It does not register Application commands or mutate Core.

```python
from core.plugin_sdk import EngineeringPluginHost
from plugins.dwg_export import DWGExportConfiguration, DWGExportPlugin, SymbolRegistry

plugin = DWGExportPlugin()
host = EngineeringPluginHost(application)
host.activate(
    plugin,
    services={
        "dwg_export_configuration": DWGExportConfiguration(
            executable=oda_executable,
            symbol_registry=SymbolRegistry(reviewed_definitions),
        )
    },
)
# A caller may use plugin.export_service with a validated DrawingPlan.
# host.deactivate(plugin.plugin_id) owns teardown.
```

The plugin is an engineering plugin, not a `ui.plugins` composition plugin. UI adapters may call it through the existing plugin host/context boundary; the CAD implementation remains outside `core` and `ui`.

## Implemented foundation

- Immutable, versioned `DrawingPlan` and `DrawingEntity` DTOs with stable GridForge entity IDs, canonical entity ordering and deterministic JSON serialization.
- Plugin-level plan validation and backend format/result checks, including project/source/revision/document provenance returned with each export result.
- Top-level DrawingPlan serialization carries source kind, source ID and (for SLD) an explicit source document ID; the export service rejects backend results that lose or alter that provenance.
- Schema-versioned, catalogue-versioned `SymbolRegistry` definitions with stable IDs, definition versions, explicit base points and vector primitives. A deterministic content fingerprint detects builder/backend registry drift even when version labels match.
- Export resolution is fail-closed for unreviewed symbols. Reviewed definitions require reviewer, ISO review date, review-evidence reference and standard reference; these are recorded provenance fields, not authenticated evidence or certification.
- An optional ODA File Converter backend using `ezdxf` for DXF staging and a separately installed, explicitly configured converter for DWG output.
- Stable entity identity and bounded metadata are written as GRIDFORGE XDATA. The complete deterministic export manifest is stored once in the DXF drawing root dictionary as a GRIDFORGE/EXPORT_MANIFEST XRECORD, so empty drawings also carry plan, registry, coordinate-mapping, backend and target-version provenance. Each entity XDATA carries the manifest SHA-256 rather than duplicating the full manifest. Entity XDATA payloads above the safe 15,000-byte budget fail closed. The manifest binds the canonical drawing-plan SHA-256, exact symbol-registry fingerprint/schema/catalogue, one consistent reviewed coordinate-mapping fingerprint and its details, source revision/document, backend and target DWG version. SLD plans with entities fail closed if mapping provenance is missing, incomplete, mixed with untransformed entities, or conflicting. The XRECORD is present in the staging DXF; preservation of XRECORD/XDATA through ODA conversion and third-party DWG conversion/editing is not independently validated.
- ODA publication uses unique staging files, a timeout, process status checks, DWG signature checks and atomic replacement.
- The optional Python extra is installed with `pip install -e '.[dwg-export]'`; ODA File Converter is installed and configured separately. Neither dependency is required by the base GridForge installation.

## SLD snapshot boundary

The renderer-independent DTOs live in `core.application.sld_export_snapshot`. They contain project/document identity, source revision, coordinate-system and unit labels, stable node/equipment IDs, symbol identity/profile inputs, placement/transforms, engineering attributes/units, explicit connection identity/endpoints, authored route points and persisted annotations.

`ui.canvas.sld_export_snapshot_adapter.SLDExportSnapshotCapture` is the lifecycle-side adapter. It reads the active `SLDDocument` through the existing `DocumentManager`, uses `SLDCanvasProjection` to detach persisted presentation, and checks project/document identity, Application revision and serialized document content before and after capture. The plugin consumes only the detached DTO and does not import UI packages.

**Consistency limitation:** these checks are fail-closed before/after guards, not an atomic snapshot or a lock. Capture must be invoked from the serialized workspace/document lifecycle operation. The current implementation does not prove atomicity against concurrent mutation outside that serialization boundary. If the revision, active document, project, or serialized SLD document changes during capture, export is rejected.

SLD coordinates are captured as `sld-unit` in the `sld-document-local` coordinate system and are never silently treated as millimetres. `SLDProjectionDrawingSnapshotAdapter.capture()` now requires an explicit `SLDCoordinateTransform`. The transform declares the exact source coordinate system and unit, a positive uniform millimetres-per-source-unit factor, world-origin translation, planar rotation, reviewer, ISO review date, and review-evidence reference. A source declaration mismatch, missing evidence, non-finite value, or unsupported primitive fails closed. It transforms symbol insertion points and rotations, connection route points, annotation positions/heights, and supported CAD primitive geometry, while recording mapping provenance on every transformed entity. Symbol INSERT scales remain dimensionless and unchanged; registry block primitives and base points remain local millimetre geometry and are not transformed with the SLD document. The resulting export snapshot is labelled `world` / `mm`; the source SLD document and Core remain unchanged. No site-specific mapping or conversion factor is bundled, so a real reviewed mapping must be supplied by the host before any production SLD export can be enabled.

## Supported CAD primitives

The current backend supports LINE, CIRCLE, ARC, TEXT, LWPOLYLINE and registry-backed INSERT. A drawing plan declares both its coordinate-system identifier and units; the ODA backend currently accepts only coordinate_system=`world` with units=`mm`. Other coordinate systems require an explicit, reviewed transformation before export. Geometry validation is performed before backend writes, including primitive-specific field sets and CAD layer-name constraints. Unsupported primitives and missing symbol definitions fail closed. INSERT geometry.insert is the target location of the symbol definition base point; the registry definition supplies the local block base point, and scale/rotation are applied by the CAD INSERT transform.

Persisted symbol instances are represented as INSERT entities. Explicit persisted SLD connections are represented as polylines. A persisted connection ID establishes the relationship; geometric proximity, overlap and snapping are never used to infer connectivity. Persisted route points are interior bends: the exported polyline includes the source node position, every authored bend in order, and the target node position. Consecutive duplicate coordinates are removed without changing non-consecutive return-path points. If no route points were authored, node positions form an explicitly identified display chord for that already-persisted connection. Transient study-result annotations are not exported as persisted document annotations.

## Symbol profiles and standards

The registry has an explicit schema version and catalogue version; each symbol has a semantic definition version. It supports ANSI/IEEE and IEC 60617 profile keys. Definitions are keyed by profile and stable symbol ID, and their CAD block names are stable for a particular definition version. Export resolution rejects unreviewed definitions. A reviewed definition must carry a reviewer, ISO review date, review-evidence reference and standard reference; both references are recorded provenance only and are not authenticated or certified. SLD INSERT metadata records the definition, registry-schema and catalogue versions; the ODA backend rejects a registry whose versions do not match the versions captured by the builder. Review status does not certify conformance to ANSI/IEEE, IEC 60617 or any other standard. No complete catalogue is shipped and no standards-conformance assessment is claimed. Add definitions only after their source geometry and review evidence are supplied.

## ODA configuration and limitations

Pass an explicit executable path when constructing `ODAFileConverterBackend`; it does not resolve an arbitrary executable from PATH. The converter is invoked without a shell and with a bounded timeout. A successful exit alone is insufficient: the expected DWG file and version signature are checked before publication.

The implementation currently recognizes target versions ACAD12, ACAD2000, ACAD2004, ACAD2007, ACAD2010, ACAD2013 and ACAD2018. Actual compatibility depends on the separately installed ODA File Converter build and platform. Signature checking is not a full DWG parser or interoperability test. Round-trip fidelity, external CAD application compatibility and preservation of all metadata through third-party editing are not established.

## Explicitly unsupported

- No complete SLD export workflow is claimed until a reviewed coordinate/unit mapping and a real UI/application caller are supplied. The SLD capture adapter, plugin builder, plan factory and backend are separate contracts; no production user action is wired to orchestrate them yet.
- Control drawing plans are unsupported until authoritative Control read models and persisted drawing semantics are available. Control logic/interlocks are never inferred from SLD wiring.
- Protection drawing plans are unsupported until authoritative Protection read models and persisted CT/PT, relay, zone and trip-target relationships are available. None are inferred from drawing proximity.
- No physical wiring, logical control relationship, protection relationship, junction or terminal is invented.
- No complete standards catalogue, DWG round-trip compatibility or production readiness is claimed.

DXF is only the intermediate representation used by the ODA backend. A DXF file is never reported as a DWG deliverable.


## Plugin boundary

All CAD-specific implementation lives under `plugins.dwg_export`. `core.application.sld_export_snapshot` remains the renderer-independent Application DTO contract, and `ui.canvas.sld_export_snapshot_adapter` remains the lifecycle-side capture adapter. Core does not import the DWG plugin. The plugin does not import Qt or infer electrical meaning from graphics.
