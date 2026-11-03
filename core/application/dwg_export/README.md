# GridForge DWG Export Foundation

This package defines an Application-owned, backend-neutral drawing-plan boundary.
It does not create a second engineering model and does not mutate Core.

## Implemented foundation

- Immutable, versioned `DrawingPlan` and `DrawingEntity` DTOs with stable GridForge entity IDs.
- Application-level plan validation and backend format/result checks.
- Profile-aware `SymbolRegistry` definitions with stable IDs, explicit base points and vector primitives.
- An optional ODA File Converter backend using `ezdxf` for DXF staging and a separately installed, explicitly configured converter for DWG output.
- Stable entity identity and metadata are written as GRIDFORGE XDATA where supported.
- ODA publication uses unique staging files, a timeout, process status checks, DWG signature checks and atomic replacement.
- The optional Python extra is installed with `pip install -e '.[cad-export]'`; ODA File Converter is installed and configured separately. Neither dependency is required by the base GridForge installation.

## SLD snapshot boundary

The renderer-independent DTOs live in `core.application.sld_export_snapshot`. They contain project/document identity, source revision, coordinate-system and unit labels, stable node/equipment IDs, symbol identity/profile inputs, placement/transforms, engineering attributes/units, explicit connection identity/endpoints, authored route points and persisted annotations.

`ui.canvas.sld_export_snapshot_adapter.SLDExportSnapshotCapture` is the lifecycle-side adapter. It reads the active `SLDDocument` through the existing `DocumentManager`, uses `SLDCanvasProjection` to detach persisted presentation, and checks project/document identity, Application revision and serialized document content before and after capture. The Application export modules consume only the detached DTO and do not import UI packages.

**Consistency limitation:** these checks are fail-closed before/after guards, not an atomic snapshot or a lock. Capture must be invoked from the serialized workspace/document lifecycle operation. The current implementation does not prove atomicity against concurrent mutation outside that serialization boundary. If the revision, active document, project, or serialized SLD document changes during capture, export is rejected.

SLD coordinates are currently labelled `sld-unit` in the `sld-document-local` coordinate system. They are not silently treated as millimetres. The ODA backend currently accepts millimetres only, so a verified coordinate/unit mapping is still required before this SLD snapshot can be converted by that backend.

## Supported CAD primitives

The current backend supports LINE, CIRCLE, ARC, TEXT, LWPOLYLINE and registry-backed INSERT. A drawing plan declares both its coordinate-system identifier and units; the ODA backend currently accepts only coordinate_system=`world` with units=`mm`. Other coordinate systems require an explicit, reviewed transformation before export. Geometry validation is performed before backend writes, including primitive-specific field sets and CAD layer-name constraints. Unsupported primitives and missing symbol definitions fail closed. INSERT geometry.insert is the target location of the symbol definition base point; the registry definition supplies the local block base point, and scale/rotation are applied by the CAD INSERT transform.

Persisted symbol instances are represented as INSERT entities. Explicit persisted SLD connections are represented as polylines. A persisted connection ID establishes the relationship; geometric proximity, overlap and snapping are never used to infer connectivity. Authored route points are preserved, and node positions are used as display endpoints only for an already-persisted connection that has no authored route. Transient study-result annotations are not exported as persisted document annotations.

## Symbol profiles and standards

The registry is versioned/profile-aware and supports ANSI/IEEE and IEC 60617 profile keys. It is a rendering/export mapping, not an electrical authority. Only explicitly registered and reviewed definitions are usable. No complete catalogue is shipped, and the presence of a profile key does not establish standards compliance. Add reviewed symbols incrementally without changing the drawing-plan or backend contract.

## ODA configuration and limitations

Pass an explicit executable path when constructing `ODAFileConverterBackend`; it does not resolve an arbitrary executable from PATH. The converter is invoked without a shell and with a bounded timeout. A successful exit alone is insufficient: the expected DWG file and version signature are checked before publication.

The implementation currently recognizes target versions ACAD12, ACAD2000, ACAD2004, ACAD2007, ACAD2010, ACAD2013 and ACAD2018. Actual compatibility depends on the separately installed ODA File Converter build and platform. Signature checking is not a full DWG parser or interoperability test. Round-trip fidelity, external CAD application compatibility and preservation of all metadata through third-party editing are not established.

## Explicitly unsupported

- No complete SLD export workflow is claimed until the coordinate/unit mapping and production composition-root integration are completed.
- Control drawing plans are unsupported until authoritative Control read models and persisted drawing semantics are available. Control logic/interlocks are never inferred from SLD wiring.
- Protection drawing plans are unsupported until authoritative Protection read models and persisted CT/PT, relay, zone and trip-target relationships are available. None are inferred from drawing proximity.
- No physical wiring, logical control relationship, protection relationship, junction or terminal is invented.
- No complete standards catalogue, DWG round-trip compatibility or production readiness is claimed.

DXF is only the intermediate representation used by the ODA backend. A DXF file is never reported as a DWG deliverable.
