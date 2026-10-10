# GridForge DWG Export

This package defines the Application-owned boundary for exporting engineering drawings. It is not a second engineering model and does not mutate Core.

## Current implementation

- Immutable drawing-plan DTOs with stable GridForge entity IDs.
- `ApplicationDrawingSnapshot` and `DrawingPlanFactory` contracts that assemble a validated plan from detached, immutable Application-level data. They do not access Core, Qt, QGraphics, or renderer objects.
- Fail-closed validation for duplicate IDs, supported primitive kinds, finite numeric geometry, and required INSERT/TEXT properties.
- An Application service that validates plans and enforces declared backend formats/results.
- A validated, profile-specific `SymbolRegistry` with stable deterministic block names, local-space vector primitives, base points, and fail-closed resolution by symbol ID plus ANSI/IEEE or IEC 60617 profile.
- An optional `ODAFileConverterBackend` that creates a temporary DXF using `ezdxf`, resolves `INSERT` entities through the injected symbol registry, converts the drawing to native DWG using the separately installed ODA File Converter, checks the target DWG signature, and atomically replaces the destination only after successful conversion.
- ODA executable path must be explicitly configured; the backend does not resolve a converter from PATH implicitly.
- Install GridForge's optional Python dependency with `pip install -e '.[cad-export]'` and install ODA File Converter separately. This does not require IngeCAD.

## Important limitations

This is an initial vertical slice, not a complete production export workflow. The current ODA backend supports LINE, CIRCLE, ARC, TEXT, LWPOLYLINE and registry-backed INSERT entities. The registry is a rendering contract only: it does not supply electrical meaning, wiring, or operational state. Registry definitions must be injected by the caller; this slice does not yet ship a standards-reviewed symbol catalogue. The backend currently supports millimetre units and a fixed set of target versions. The snapshot contract is present, but concrete adapters from the repository’s actual Application read models and approved SLD projections have not yet been connected; no current UI or Core model should be passed directly. Full SLD, control wiring, and protection/measurement plan builders, round-trip tests, and actual converter integration tests remain outstanding. Do not advertise the complete export feature as production-ready until those gates pass.

## Architecture rules

- Build drawing plans from authoritative Application read models and approved presentation projections.
- Keep CAD SDK imports out of Core and DTO/validation modules.
- IngeCAD remains optional; the standalone path must work without it.
- DWG and DXF are distinct formats; never label DXF bytes as DWG.
- Stable GridForge IDs must be persisted as drawing metadata where supported; CAD handles are not authoritative IDs.
- Unsupported primitives or conversion failures must fail closed. Never silently omit engineering entities.
- SLD geometry is a layout hint, never electrical truth. Logical signal mappings must not be invented as physical control wiring.
