# GridForge Electrical Dynamics Interoperability

**Implementation repository:** `madhuri196mishra-cpu/GridForge`  
**Integration branch:** `electrical-dynamics-integration`  
**Baseline:** `generator-dynamics-plugin` (preserves the 143-commit generator/motor line)  
**Status:** Shared plugin contracts and family adapters introduced; coupled solver and Application composition remain integration gates.

## Objective

Coordinate Generator Dynamics, Motor Dynamics and Load Dynamics as independently installable engineering extensions participating in common network studies. Interoperability must not create a second equipment identity, measurement channel, study scheduler, event bus, persistence store, command manager, topology or result authority.

## Ownership

| Concern | Sole authority | Plugin responsibility |
|---|---|---|
| Equipment identity and physical terminals | Core `Generator`, `Motor`, `Load` | Reference canonical IDs; add family-specific parameter contracts |
| Network topology and terminal injections | Core / existing network model contracts | Supply model equations/contributions through approved adapters |
| Measurement channels and instrument sources | Core measurement contracts and Application provisioning | Declare logical point templates; request canonical provisioning |
| Study request, scheduling, cancellation and provenance | Application `StudyService` | Register typed handlers and return detached results |
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

Those specialist handlers are not themselves a coupled network solver. The canonical solver-facing contracts already exist in Core: `DynamicStateComponent`, `DynamicComponentInputs` and `DynamicComponentEvaluation`; `DynamicSystemStateLayout`; `DynamicComponentBinding` plus `evaluate_dynamic_components`; and `evaluate_composed_dynamic_derivatives`. The shared plugin package re-exports these exact Core contracts; it does not define a parallel dynamic-component, state-layout or derivative-assembly protocol. The existing derivative assembly coordinates the machine derivative callback with controller-state derivatives and generator mechanical-power handoffs from one coherent network snapshot. It does not yet produce motor/load terminal current or power injections, and the `DAESolver`/`MultiMachineSystem` still do not own the complete composed network/integration step. A future solver/Application integration must extend the existing Core evaluation result with explicit electrical terminal injection contributions for dynamic motors and loads, while preserving generator mechanical-power handoffs. It must coordinate network solution, composed derivatives and accepted/rejected integration stages in one solver-owned step, then persist one StudyService result/provenance envelope. It must not run three independent simulations and combine plots afterward.

Power-flow demand aggregation must likewise count each physical injection once. ZIP demand is a supported reduced-order load representation; it is not a substitute for detailed motor, converter, furnace or unbalanced phase-domain equations.

## Compute interoperability

All three plugins resolve the same `PluginContext.services["electrical_dynamics_composition"]` host object. Its protocol exposes the existing plugin-specific registration hooks and sampled solver adapters; the object is supplied and owned by Application, not constructed by plugins. All three plugins reuse `core.numerics.backend` and its capability records. CPU remains the reproducible reference path when supplied. GPU execution is allowed only for workloads and precision modes explicitly advertised by the runtime capability. NPU is restricted to inference workloads such as forecasting or condition/anomaly inference; it is not an authoritative protection decision-maker or an assumed physical differential-equation solver.

## Readiness honesty

- Generator family adapters report `host_solver_required`: controller and machine components exist, but the Application host must supply the sampled generator transient solver.
- Motor family adapters map existing family profile maturity. Experimental solver families are labelled experimental; catalogued-only families advertise no executable transient study.
- Load family adapters expose ZIP demand equations as experimental model data; no power-flow or transient study is advertised as integrated until the Application solver composition consumes the model.

No family is labelled reference-validated or field-validated by this integration contract.

## Shared composition boundary

`plugins/electrical_dynamics/composition.py` defines the common host protocol. Generator, motor and load activation now resolve the same `electrical_dynamics_composition` key. The protocol groups their existing extension registration, motor service, and per-request sampled solver adapter capabilities into one host-supplied object. It creates no new Application service and owns no service lifecycle. The Application composition root still must implement and supply this object.

## Remaining integration gates

1. Wire the existing Core `DynamicStateComponent`, `DynamicSystemStateLayout`, `evaluate_dynamic_components` and `evaluate_composed_dynamic_derivatives` into the solver-owned accepted/rejected integration stages. Extend the existing evaluation contract with typed terminal injection contributions for motors and loads; preserve generator mechanical-power handoff, one network snapshot and one integration clock. Do not introduce a second component, state-layout or derivative-assembly protocol.
2. Establish explicit physical-asset equivalence mapping so a detailed motor cannot also be counted as a static load.
3. Implement global coupled dynamic state layout and stage-synchronous evaluation across generator, motor, load and network algebraic equations.
4. Connect the shared point metadata to actual canonical MeasurementChannel provisioning and source-terminal validation.
5. Add typed transactional configuration/persistence and SLD/editor/read-model projections through existing host composition.
6. Define reference cases and independently verify each claimed family/model and CPU/GPU numerical equivalence. Keep NPU inference-only.
7. Add focused source and runtime validation only after the architecture and composition are complete; this branch's present work is source-level foundation, not simulation validation.

## Verification boundary

The branch is a source-level interoperability foundation. It has not been activated in the Application composition root; no coupled solver, cross-plugin study execution, tests, CI, startup, GUI, hardware probing or commissioning has been performed. It is not merged to `main`.
