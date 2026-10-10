# GridForge DWG Export

This package defines the Application-owned boundary for exporting engineering drawings. It is not a second engineering model and does not mutate Core.

## Current implementation

- Immutable drawing-plan DTOs with stable GridForge entity IDs.
- Fail-closed validation for duplicate IDs, supported primitive kinds, finite numeric geometry, and required INSERT/TEXT properties.
- An Application service that validates plans and enforces declared backend formats/results.
- An optional `ODAFileConverterBackend` that creates a temporary DXF using `ezdxf`, converts it to native DWG using the separately installed ODA File Converter, verifies the target DWG signature, and atomically replaces the destination only after successful conversion.
- ODA executable path must be explicitly configured; the backend does not resolve a converter from PATH implicitly.
- Install GridForge's optional Python dependency with `pip install -e '.[cad-export]'` and install ODA File Converter separately. This does not require IngeCAD.

## Important limitations

This is an initial vertical slice, not a complete production export workflow. The current ODA backend supports LINE, CIRCLE, ARC, TEXT and LWPOLYLINE. INSERT/block symbols are deliberately rejected until the shared, validated symbol/block registry is implemented. It currently supports millimetre units and a fixed set of target versions. Full SLD, control wiring, and protection/measurement plan builders, symbol-profile mapping, round-trip tests, and actual converter integration tests remain outstanding. Do not advertise the complete export feature as production-ready until those gates pass.

## Architecture rules

- Build drawing plans from authoritative Application read models and approved presentation projections.
- Keep CAD SDK imports out of Core and DTO/validation modules.
- IngeCAD remains optional; the standalone path must work without it.
- DWG and DXF are distinct formats; never label DXF bytes as DWG.
- Stable GridForge IDs must be persisted as drawing metadata where supported; CAD handles are not authoritative IDs.
- Unsupported primitives or conversion failures must fail closed. Never silently omit engineering entities.
- SLD geometry is a layout hint, never electrical truth. Logical signal mappings must not be invented as physical control wiring.
