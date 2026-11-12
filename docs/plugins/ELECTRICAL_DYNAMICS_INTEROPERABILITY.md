# GridForge Electrical Dynamics Interoperability

**Implementation repository:** `madhuri196mishra-cpu/GridForge`  
**Integration branch:** `electrical-dynamics-unified-current`  
**Source baseline:** latest `generator-dynamics-plugin` files reconciled at 12 November 2026; Git history remains diverged and requires a merge-base reconciliation before any PR merge.  
**Status:** Shared Core terminal-injection contracts and reduced ZIP load adapter introduced; coupled network consumption, motor dynamic adapters and Application composition remain integration gates.

## Objective

Coordinate Generator Dynamics, Motor Dynamics and Load Dynamics as independently installable engineering extensions participating in common network studies. Interoperability must not create a second equipment identity, measurement channel, study scheduler, event bus, persistence store, command manager, topology or result authority.

## Ownership

| Concern | Sole authority | Plugin responsibility |
|---|---|---|
| Equipment identity and physical terminals | Core `Generator`, `Motor`, `Load` | Reference canonical IDs; add family-specific parameter contracts |
| Network topology and terminal injections | Core / existing network model contracts | Supply model equations/contributions through approved adapters |
| Measurement channels and instrument sources | Core measurement contracts and Application provisioning | Declare logical point templates; request canonical provisioning |
| Study request, scheduling, cancellation and provenance | Application `StudyService` | Register typed handlers and return detached results |
| Read models and result projections | Application | Supply the motor read-model projection through the shared host composition; plugins publish detached results and never clear Application-owned stores on shutdown |
| Project transactions and persistence | Application services | Supply versioned plugin configuration DTOs to host-owned persistence |
| Numerical backend capability and selection | `core.numerics.backend` | Declare workload needs; do not probe devices or own runtimes |
| UI, SLD projection and read models | Existing UI/Application projections | Provide plugin-specific presentation adapters; no direct Core mutation |

## Shared contracts

`plugins/electrical_dynamics/contracts.py` introduces:
- `DynamicsEquipmentBinding`: immutable reference to a canonical Core `EquipmentType` and equipment ID; it stores no equipment object.
- `MeasurementPointContract`: normalized read-only view of existing plugin point DTOs. It is not a MeasurementChannel and stores no value, timestamp, quality or physical source.
- `DynamicsModelCapability`: family/model readiness, declared study types, numerical workloads and required point identifiers.
- `validate_unique_equipment_participants`: rejects repeated canonical equipment identities in a coupled participant list.

Each extension exposes an `interoperability.py` adapter to normalize its existing catalogue and instrumentation definitions. The adapters do not rewrite plugin-specific physics or claim that catalogue coverage equals executable model coverage.

## No-double-counting rule

A physical machine must contribute one authoritative terminal injection to a coupled network solution. A Core `Motor` using its motor dynamic model must not also be represented as an independent static Core `Load` for the same physical motor. If a reduced-order static representation is selected instead, it replaces—not supplements—the detailed motor injection for that study. The Application study assembler must make this model-selection decision explicit and reject ambiguous duplicate representations before numerical execution.

The shared participant validator rejects repeated identical Core equipment identities. Cross-type physical-asset equivalence (for example, an independently created Motor and Load intended to represent the same machine) requires an explicit host-owned asset mapping; plugins must not infer identity from names, ratings or terminal proximity.

## Measurement interoperability

Generator, motor and load point definitions are normalized to one metadata shape while preserving each plugin's family-specific applicability. Actual channel provisioning continues through canonical Core `MeasurementChannel`, `MeasurementProvisioning` and Application measurement services. Point templates are not live signals. No plugin may synthesize a physical CT/PT/CVT, secondary terminal, quality state, timestamp or channel sample.

## Study and solver interoperability

Each plugin retains its specialist model equations and existing study handler:
- `generator_transient`
- `motor_transient`
- `load_transient`

Those specialist handlers are not themselves a coupled network solver. The canonical solver-facing contracts already exist in Core: `DynamicStateComponent`, `DynamicComponentInputs` and `DynamicComponentEvaluation`; `DynamicSystemStateLayout`; `DynamicComponentBinding` plus `evaluate_dynamic_components`; and `evaluate_composed_dynamic_derivatives`. The existing component evaluation now carries typed, phase-resolved `DynamicTerminalInjection` values with an explicit per-unit ampere base for each terminal and an equipment-to-network positive-injection sign convention. Core also provides `aggregate_dynamic_terminal_injections`, which converts each contribution through physical amperes onto the host-declared network current base and deterministically sums by terminal/phase; it does not resolve topology or solve the network. The Load plugin also exposes `evaluate_zip_terminal_injections`, a pure algebraic adapter that maps ZIP demand into branch-current contributions using host-supplied terminal/phase mapping and matching per-branch voltage and power bases. Phase shares must sum to one. Every branch requires explicit source and return terminals and emits equal-and-opposite physical ampere currents, converted separately to each terminal's declared per-unit current base. This preserves nodal current balance for single-phase, three-phase and DC branch mappings. The host supplies oriented branch voltages and matching bases. This reduced ZIP adapter does not stand in for detailed motor, converter, furnace or phase-domain device models. Bindings retain generator-to-machine mechanical handoffs while allowing motor/load state components to bind directly to canonical Core EquipmentType/equipment IDs and an explicit set of Core terminals. `DynamicComponentBinding.terminal_ids` permits paired branch contributions, and `terminal_bus_ids` must map every bound terminal to its actual Core bus; multi-terminal bindings cannot inherit the primary bus. The host supplies oriented branch terminal voltages derived from the mapped topology. These voltages are not assumed equal to an individual bus potential, which is essential for line-line, delta and DC branches. The shared plugin package re-exports these exact Core contracts; it does not define a parallel dynamic-component, state-layout or derivative-assembly protocol. The generator plugin now adapts its FirstOrderAVR through `FirstOrderAVRStateComponent` and coordinates the excitation snapshot, stage network solve, terminal projection and canonical derivative assembly through `evaluate_generator_dynamic_stage`. The existing derivative assembly coordinates the machine derivative callback with controller-state derivatives and generator mechanical-power handoffs from one coherent network snapshot. The derivative assembly can return motor/load terminal-current contributions from registered state components alongside machine/controller derivatives; family-specific motor/load dynamic adapters are still pending. The terminal contributions are detached and validated but are not yet consumed by the network solver; the `DAESolver`/`MultiMachineSystem` still do not own the complete composed network/integration step. Core now defines `DynamicNetworkSolveRequest` and the Application-supplied `DynamicNetworkSolver` protocol in `core.solver.dynamics.network_coupling`: each request carries the machine trial state, stage time, topology revision, already-aggregated terminal/phase currents on host network bases, and the exact generator field-voltage handoff. `validate_dynamic_network_solution` rejects unverified or unaccepted solutions and requires exact stage-time and topology-revision agreement using the existing `AlgebraicNetworkSolution` quality record. This is the injection-aware solver boundary reused by the generator augmented-stage path. Core now also provides `solve_dynamic_network_stage`: an explicit fixed-point coordinator that repeatedly calls the Application-supplied injection-aware network solver, evaluates all device terminal currents against that network solution, aggregates them on host-declared current bases, and checks per-terminal/phase current consistency using absolute-plus-relative tolerances. It returns only after both the network solution is accepted/quality-verified for the exact time and topology revision and the device-current residual is within tolerance; otherwise it fails closed at the iteration limit. The coordinator is now wired into the generator stage evaluator and opt-in `GeneratorAugmentedDAEAdapter` through an explicit `coupled_network_solver`, topology revision and host network current bases. Each trial stage recomputes AVR excitation, projects the trial network solution into oriented terminal voltages, evaluates registered continuous components for their currents, iterates the network/current fixed point, and only then assembles machine and component derivatives from the converged solution. The augmented archive records topology, current residual, iteration count and canonical algebraic quality evidence. The legacy generator callback remains an exclusive alternative; callers select exactly one solver path. The callback must be side-effect-free. Legacy `DAESolver`, Application lifecycle/persistence ownership and end-to-end numerical validation remain open; family-specific motor/load models and host terminal-voltage/current-base mappings remain authoritative. The augmented adapter coordinates the network solution, composed derivatives and accepted integration boundary in one solver-owned step; its observer remains a host callback, and Application must still bind that callback to the canonical StudyService result/provenance transaction. It must not run three independent simulations and combine plots afterward.

Power-flow demand aggregation must likewise count each physical injection once. The ZIP adapter is a reduced-order algebraic representation; it must replace rather than supplement the detailed motor model for the same physical motor.

## Compute interoperability

All three plugins resolve the same `PluginContext.services["electrical_dynamics_composition"]` host object. Its protocol exposes the existing plugin-specific registration hooks and sampled solver adapters; the object is supplied and owned by Application, not constructed by plugins. All three plugins reuse `core.numerics.backend` and its capability records. CPU remains the reproducible reference path when supplied. GPU execution is allowed only for workloads and precision modes explicitly advertised by the runtime capability. NPU is restricted to inference workloads such as forecasting or condition/anomaly inference; it is not an authoritative protection decision-maker or an assumed physical differential-equation solver.

## Readiness honesty

- Generator family adapters report `host_solver_required`: controller and machine components exist, but the Application host must supply the sampled generator transient solver.
- Motor family adapters map existing family profile maturity, including the current wound-field synchronous, hysteresis/torque-angle, universal-series, single-phase and converter-fed family additions. Experimental solver families are labelled experimental; catalogued-only families advertise no executable transient study.
- Load family adapters expose ZIP demand equations as experimental model data; no power-flow or transient study is advertised as integrated until the Application solver composition consumes the model.

No family is labelled reference-validated or field-validated by this integration contract.

## Shared composition boundary

`plugins/electrical_dynamics/composition.py` defines the common host protocol. Generator, motor and load activation now resolve the same `electrical_dynamics_composition` key. The protocol groups their existing extension registration, motor service, Application-owned motor read-model projection, and per-request sampled solver adapter capabilities into one host-supplied object. It creates no new Application service and owns no service lifecycle. The Application composition root still must implement and supply this object, including `motor_read_models`; the motor plugin no longer instantiates or clears a competing result projection store.

## Remaining integration gates

1. Route both dynamic-component and algebraic ZIP `DynamicTerminalInjection` contributions into the solver-owned network algebraic stage. Use Core current-base aggregation, resolve terminal-to-bus topology through Core, and preserve phase mapping and accepted/rejected-stage consistency. Do not introduce a second component, state-layout or derivative-assembly protocol.
2. Establish explicit physical-asset equivalence mapping so a detailed motor cannot also be counted as a static load.
3. Implement global coupled dynamic state layout and stage-synchronous evaluation across generator, motor, load and network algebraic equations.
4. Connect the shared point metadata to actual canonical MeasurementChannel provisioning and source-terminal validation.
5. Add typed transactional configuration/persistence and SLD/editor/read-model projections through existing host composition.
6. Define reference cases and independently verify each claimed family/model and CPU/GPU numerical equivalence. Keep NPU inference-only.
7. Add focused source and runtime validation only after the architecture and composition are complete; this branch's present work is source-level foundation, not simulation validation.

## Verification boundary

The branch is a source-level interoperability foundation. It has not been activated in the Application composition root; no coupled solver, cross-plugin study execution, tests, CI, startup, GUI, hardware probing or commissioning has been performed. It is not merged to `main`.
