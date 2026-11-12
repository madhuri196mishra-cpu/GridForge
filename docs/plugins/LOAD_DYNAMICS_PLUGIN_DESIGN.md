# GridForge Load Dynamics Plugin

**Implementation branch:** `load-dynamics-plugin`  
**Implementation repository:** `madhuri196mishra-cpu/GridForge`  
**Baseline:** `main` at branch creation, 12 November 2026  
**Status:** Initial domain contracts and canonical StudyService adapter committed; host numerical composition and project activation remain integration gates.

## Purpose

Provide one engineering extension for electrical demand models across residential, commercial, industrial, process, rotating-machine, converter-fed, thermal, traction, EV-charging, data-centre and station-auxiliary applications. The extension enriches the existing Core `Load`; it does not replace the authoritative equipment, terminal, topology, or positive-consumption power convention.

## Coverage catalogue

- **Rotating machinery:** induction and synchronous motors, PMSM, BLDC, DC motors, pumps, fans, compressors, conveyors and traction auxiliaries.
- **Building and commercial:** lighting, HVAC, chillers, refrigeration, heat pumps, elevators and aggregated building demand.
- **Power electronics:** VFD-fed drives, rectifiers, SMPS, UPS, EVSE, battery chargers and converter-fed process loads.
- **Heavy industry:** arc/induction/resistance furnaces, welding, electrolysers, rolling/process loads and cyclic industrial duty.
- **Distributed and domestic:** residential appliances, cooking/heating, smart loads, flexible demand and feeder aggregates.
- **Station/DC services:** transformer cooling, station service, battery rooms, DC auxiliaries and control/protection supply loads.

The catalogue is extensible; a family label is not proof that a corresponding numerical model has been implemented.

## Initial source implementation

- Immutable family, connection, nameplate, induction-motor, ZIP and demand-profile contracts.
- ZIP active and reactive fractions are independent and each set must sum to unity. The voltage input is a positive per-unit magnitude; output retains kW/kvar demand convention.
- Demand profiles use strictly increasing seconds and piecewise-linear interpolation, clamped at both ends.
- Family-filtered instrumentation templates cover phase/neutral current, phase/line voltage, P/Q/S/PF, frequency, energy, harmonic distortion, unbalance, flicker, status, motor thermal/mechanical, converter DC-link, battery and process points.
- Instrumentation templates are logical point definitions only. Physical CT/PT/CVT/transducer identity, secondary terminal, source provenance, timestamp, quality, freshness and channel activation must be supplied through the canonical measurement path. No instruments or channel values are fabricated.
- Backend selection reuses `core.numerics.backend`. CPU remains the portable baseline when supplied; GPU is workload/capability gated; NPU is restricted to forecast/condition inference and is not advertised as a physical transient solver.
- `LoadTransientStudyHandler` adapts to the existing Application `StudyService` contract. The host supplies the actual solver and per-request sampled-adapter factory.

## Architecture constraints

1. Core `Load` remains the sole authoritative equipment object and retains its existing single-terminal and positive-consumption semantics.
2. Plugin catalogue records are detached model/configuration contracts, not a second topology or persistent equipment authority.
3. Application `StudyService` owns scheduling, cancellation, status, provenance, events and canonical result persistence.
4. The plugin does not create a scheduler, event bus, command manager, result store, measurement registry, project lifecycle, or UI plugin manager.
5. Physical instrument provisioning must use canonical Core measurement contracts and Application-owned source/terminal resolution.
6. UI inspectors, dashboards, SLD symbols and editing commands require separate Application/UI composition work; no UI-to-Core mutation is permitted.

## Required next integration gates

- Audit and implement the Application-owned `load_dynamics_composition` that provides a validated load transient solver and creates a fresh sampled adapter from each detached StudyRequest/StudyExecutionContext.
- Define the supported load-family model-to-network mapping for power flow, short circuit and phase-domain studies; do not silently substitute a ZIP model for motor, converter, arc or unbalanced physics.
- Add transactional Application commands and versioned project persistence for plugin-specific load configuration only after the authoritative ownership/activation path is traced.
- Provision physical measurement channels through actual CT/PT/CVT/transducer secondary terminals and connect measurement consumers without a plugin-owned registry.
- Integrate load model selection and parameter inspection through existing UI plugin composition and SLD equipment projection contracts.
- Establish numerical reference cases for each claimed model family and verify CPU/GPU equivalence; NPU remains inference-only unless a workload-specific validated implementation exists.

## Verification boundary

Source-level implementation and GitHub fetch-back only. No tests, CI, imports, startup, GUI interaction, numerical simulation, hardware probing, or commissioning verification are claimed. The branch is not merged to `main`.
