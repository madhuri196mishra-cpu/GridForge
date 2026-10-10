# GridForge DWG Export

This package is the Application-owned, backend-neutral boundary for engineering drawing export. It is not a second engineering model and must not mutate Core.

## Rules

- Build drawing plans from authoritative Application read models and presentation projections.
- Validate a complete plan before any backend writes output.
- Keep CAD SDK imports out of Core and out of DTO/validation modules.
- IngeCAD is optional and is not a prerequisite for standalone export.
- DWG and DXF are distinct formats. Never label DXF bytes as DWG.
- Stable GridForge IDs must be persisted as drawing metadata where supported; CAD handles are not authoritative IDs.
- Fail closed on unsupported primitives rather than silently dropping entities.

## Scope status

This README establishes the intended module boundary only. Direct DWG export is not operational until a native DWG backend and a production drawing-plan builder are implemented and validated against target DWG versions, reopen/round-trip fixtures, and failure-atomicity checks.
