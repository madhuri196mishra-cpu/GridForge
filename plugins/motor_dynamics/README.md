# GridForge Motor Dynamics Extension

**Plugin ID:** `gridforge.plugin.motor_dynamics`  
**Version:** `2.0.0`  
**API contract:** `V2.0`  
**Status:** Experimental extension with a CPU dq induction-motor transient solver, optional Numba-CUDA batch dispatch, and a lifecycle-registered `motor_transient` StudyService adapter. Source integration is present; end-to-end execution, numerical reference-case validation, and accelerator hardware validation remain outstanding.

The Motor Dynamics extension adds an optional induction-motor workflow to GridForge: draft placement and Bus connection, a motor symbol projection, simulation-result read models, telemetry charts, signal diagnostics, optional ONNX inference, and versioned plugin-data persistence.

It is designed to integrate with GridForge's Application/Core architecture rather than introduce a separate electrical network. In particular, a motor is modeled as a **one-terminal injection/load connected to a Bus attachment**. It is not a series element, and placing it must not split or rewrite an existing wire.

> **Implementation status matters:** `transient_solver.py` now implements a headless CPU reference solver for coupled induction-motor dq electrical states, rotor mechanics, and one lumped winding-temperature state using fixed-step RK4. An optional Numba-CUDA batch kernel assigns independent motor trajectories to GPU threads and uses a flattened motor-major/step-major/state-minor buffer. This is a defined first engineering model, not a validated high-fidelity digital twin: magnetic saturation, unbalance, switching converters, separate stator/rotor thermal nodes, and parameter/reference-case validation are not yet covered. CPU is always the fallback. NPU acceleration is not currently used for this recurrence-based solver; the existing QNN/Vitis AI/OpenVINO providers are for optional ONNX inference, not proof of NPU physics execution. The `motor_transient` handler is registered through the plugin lifecycle. It returns a detached result envelope; the read-model publisher consumes the canonical `StudyCompleted` event and retrieves the stored successful `StudyResult` before publishing telemetry. This source path has not been exercised end to end. Actual CPU/GPU numerical and hardware validation remain outstanding.

## Contents

- [Goals and scope](#goals-and-scope)
- [Architecture and ownership](#architecture-and-ownership)
- [How the workflow functions](#how-the-workflow-functions)
- [Module guide](#module-guide)
- [Diagnostics and inference](#diagnostics-and-inference)
- [CPU, GPU, and NPU policy](#cpu-gpu-and-npu-policy)
- [Persistence](#persistence)
- [Requirements and integration](#requirements-and-integration)
- [Current limitations](#current-limitations)
- [Engineering principles](#engineering-principles)

## Goals and scope

The extension is intended to provide a foundation for:

1. Representing a motor as normal GridForge electrical equipment.
2. Placing and connecting motor drafts through the canonical Application command path.
3. Keeping electrical connectivity and engineering data in Core/Application-owned structures, not in graphics items.
4. Publishing immutable simulation summaries and channel vectors to UI consumers.
5. Displaying current and winding-temperature telemetry after a study-completion event.
6. Calculating preliminary signal features from sampled motor-current data.
7. Optionally running an ONNX model through available ONNX Runtime execution providers.
8. Persisting motor configuration records using an explicit schema version.
9. Integrating a callable CPU transient solver and optional CUDA batch solver with the application-owned study lifecycle; the adapter and completion publisher are now present, subject to end-to-end verification.

This is an **integration scaffold**, not a claim that all motor-analysis capabilities are complete. Numerical motor equations, coupled thermal/mechanical/electrical state integration, validated machine parameter sets, solver convergence controls, and reference-case verification remain separate engineering work.

## Architecture and ownership

The extension follows GridForge's normal separation of concerns:

| Layer | Responsibility |
| --- | --- |
| Core | Authoritative electrical objects, terminals, topology, and validated domain state. |
| Application | Commands, transactions, draft mutation, creation contracts, and controlled commit/import operations. |
| Motor plugin | Plugin lifecycle, typed command adapters, canvas interaction adapters, diagnostics, read models, and presentation integration. |
| UI/SLD | Projection and interaction only; graphics do not define electrical truth. |
| Optional inference runtime | Executes a supplied ONNX model when a compatible ONNX Runtime provider is available. |

The intended mutation path is:

`Canvas tool → immutable motor command → Application CommandManager → motor service → DraftNetwork → normal draft validation/commit → Core`

The UI does not directly create authoritative Core topology. The motor graphics item is a projection and must not be treated as a source of connectivity or electrical parameters.

### Electrical model

A motor has one terminal with the canonical terminal role `terminal`. Its connection is represented as a `DraftConnection` of kind `simple_wire` between that motor terminal and a Bus attachment endpoint. The attachment identity is currently validated against the canonical form `attachment-0` through `attachment-8`.

The motor connection workflow deliberately does **not**:

- insert the motor into an existing wire;
- split an existing wire to make room for the motor;
- infer network connectivity from screen coordinates or graphics;
- bypass `DraftNetwork` validation or the normal commit path.

The Bus may be a draft Bus or an existing Core Bus, as resolved by the Application-owned motor draft service. Final authoritative topology still depends on the host application's canonical draft validation and commit lifecycle.

## How the workflow functions

### 1. Plugin activation

The plugin entry point is `MotorDynamicsPlugin` in `plugin.py`. The host calls `initialize(context)` with GridForge's `PluginContext`.

Initialization requires:

- a non-null `context.application`;
- `context.services["motor_dynamics_composition"]`;
- a composition object exposing `register_motor_extension(...)`;
- a separate `motor_service` supplied by that composition object.

The plugin registers the two motor command handlers through the composition adapter and requires a callable teardown token. It then prepares persistence support and, when a parent widget and event bus are available, a telemetry dashboard. If initialization fails partway through, it attempts to close any created dashboard and undo command registration before propagating the failure.

The plugin is opt-in: its manifest and implementation do not by themselves guarantee that the host's default plugin configuration activates it.

### 2. Placement preview and draft creation

`MotorPlacementTool` in `tools.py` handles canvas interaction:

1. On pointer movement, it records the current scene position and asks the view to update a temporary motor preview, when the view supports that hook.
2. On pointer release, it creates a motor draft ID and optionally asks the host view whether the pointer is over a Bus attachment.
3. It dispatches an `AddDraftMotorPlacementCommand` through the supplied Application command-dispatch callback.
4. If a Bus attachment was hit and placement succeeded, it dispatches a separate `ConnectMotorToBusCommand`.
5. Cancelling clears the temporary preview and local draft selection state.

Placement coordinates must be finite numbers. The placement command accepts an optional mapping of motor parameters; it does not silently accept arbitrary non-mapping payloads.

Placement and connection are separate commands. If the connection command fails after placement, the draft motor may remain available for correction; the tool does not mutate Core topology directly.

### 3. Command validation and dispatch

`commands.py` defines two immutable command-envelope types:

- `AddDraftMotorPlacementCommand` — motor ID, finite scene coordinates, and optional parameter mapping.
- `ConnectMotorToBusCommand` — motor ID, Bus ID, and canonical Bus attachment ID.

Both use GridForge's `Command` envelope and preserve command/correlation/causation IDs where supplied. The handler adapter checks that the command type matches the expected concrete command class, that a GridForge `Transaction` is supplied, and that the motor service returns an `ApplicationResult`.

The handler does not own transaction commit or rollback. The `CommandManager` owns that lifecycle, and the motor service is expected to record undo operations on the provided transaction before mutating the draft.

### 4. Application-owned draft service

`service.py` re-exports `MotorDraftService` and `MotorDynamicsComposition` from `core.application.services.motor_dynamics_service`. Keeping the implementation in the Application layer allows the headless service to be constructed without importing UI modules.

For placement, `MotorDraftService`:

1. Requires an active Application `DraftNetwork`.
2. Validates the motor ID and finite coordinates.
3. Resolves the trusted `motor` creation contract from the Application's creation-contract registry.
4. Validates the supplied parameter names against the contract's declared parameter IDs.
5. Builds a `DraftEquipment` record with the contract's terminal, parameter, endpoint, and creation metadata.
6. Records a draft snapshot as the transaction's undo action.
7. Adds the equipment to `DraftNetwork`.

For Bus connection, it validates the IDs and attachment syntax, verifies that the referenced draft equipment is a motor and that a draft Bus is actually a Bus, constructs a `DraftConnection` from the motor's `terminal` endpoint to the Bus attachment, asks `DraftNetwork` to validate the candidate connection, records an undo snapshot, and then adds the connection.

`MotorDynamicsComposition` is the Application-owned registration adapter. It validates the plugin ID and the exact expected command-handler set, registers a handler factory through the Application extension API, and returns an idempotent teardown callback. The callback uses the expected factory identity so a stale teardown cannot intentionally unregister a newer factory that reuses the same plugin ID.

### 5. SLD symbol projection

`graphics.py` defines `SLDMotorGraphicsItem`, a Qt graphics item that draws a simple motor symbol and allows selection/movement in the graphics scene. It accesses the read-model store to obtain a simulation summary and can tint the symbol based on the summary's fault text.

Qt types are imported only through GridForge's `ui.core.qt` boundary.

The symbol is **not** the motor domain object. Its position, color, and graphics state are presentation information; authoritative electrical identity, terminal contracts, and connectivity must come from Core/Application data.

### 6. Result publication and read models

`read_models.py` provides `MotorSimulationReadModelStore` and the frozen `SimulationSummary` data class. The store is intended as a safe hand-off between a future solver/result producer and UI projections.

On publication, it:

- validates the motor ID, positive finite sample period, status, and channel mapping;
- converts each real numeric channel into a copied, flattened `float64` array;
- rejects non-finite samples;
- marks stored arrays read-only;
- replaces the complete prior channel snapshot for that motor, rather than mixing old and new channels;
- publishes a summary containing status, sample count, sample period, channel names, and optional fault information.

An `RLock` protects store access. Channel getters return read-only views; the store does not expose ownership of a solver's mutable arrays.

The read model is infrastructure for results, not a source of electrical network truth. `MotorSimulationResult.channels(...)` derives telemetry arrays from solver state. The study handler returns a detached result envelope; the canonical `StudyService` stores the successful `StudyResult` and emits `StudyCompleted`, after which the result publisher verifies the stored result and publishes the derived channels to the read model. A failed or cancelled study must not publish a successful snapshot.

### 7. Telemetry dashboard

`dashboard.py` defines `MotorTelemetryDashboardWidget`. It subscribes to GridForge's canonical `StudyCompleted` event when an event bus is available.

The event callback emits a Qt signal; chart updates are performed through that signal bridge so widget mutation is marshalled to the widget's Qt thread. When the event payload identifies a motor and the read model contains a summary, the dashboard looks for these channels:

- `stator_current_a` — displayed as the stator inrush/current profile;
- `winding_temperature_c` — displayed as winding temperature dynamics.

For large vectors, the dashboard uses a stride to limit the displayed point count to approximately 1,500 samples. It adjusts chart axes from the displayed values. Closing the widget unsubscribes from the event bus.

These charts display published result data. They do not calculate motor current or temperature themselves, and the presence of the widget does not mean a transient solver is available.

## Module guide

| File | Purpose |
| --- | --- |
| `manifest.json` | Plugin identity, API version, capabilities, runtime requirements, and inference-provider policy. |
| `plugin.py` | Plugin lifecycle and registration/teardown coordination. |
| `commands.py` | Typed placement/connection commands and CommandManager handler adapter. |
| `service.py` | Compatibility exports for the Application-owned motor service and composition adapter. |
| `tools.py` | Canvas placement preview and command dispatch. |
| `graphics.py` | SLD motor symbol projection. |
| `transient_solver.py` | CPU RK4 induction-motor dq electrical/mechanical/lumped-thermal transient solver; optional Numba-CUDA batch execution; flat contiguous state buffer and derived telemetry channels. |
| `read_models.py` | Thread-safe simulation summaries and read-only channel snapshots. |
| `dashboard.py` | `StudyCompleted`-driven current and temperature charts. |
| `diagnostics.py` | Preliminary current-signal feature extraction and diagnostic labels. |
| `npu_inference.py` | Optional ONNX Runtime provider selection, input validation, and CPU fallback. |
| `persistence.py` | Versioned serialization and validation-first import delegation. |

## Diagnostics and inference

### Signal diagnostics

`diagnostics.py` defines `MotorAIDiagnosticNode` and the immutable `DiagnosticVerdict` result.

The current implementation performs preliminary signal analysis on a sampled current vector:

1. Validates the input window and sample period.
2. Uses the second half of the supplied window as a simple stable-tail approximation and removes its mean.
3. Calculates a periodogram.
4. Estimates the fundamental in a band around the configured supply frequency (50 Hz by default).
5. Estimates harmonic distortion from selected harmonic bands.
6. Optionally estimates sideband energy around frequencies derived from the supplied slip frequency.
7. Emits a preliminary label such as `nominal`, `harmonic_distortion_review`, or `bearing_or_rotor_review`, along with numerical features and a heuristic confidence value.

These labels are **screening heuristics**, not a validated fault classifier. The current implementation does not establish a diagnosis, prove a bearing/rotor defect, or replace protection engineering. Thresholds, frequency resolution, sensor bandwidth, load/speed conditions, noise sensitivity, and representative labelled datasets require engineering validation before operational use.

### Optional ONNX inference

`npu_inference.py` defines `MotorNPUDiagnosticNode`. It loads a user-supplied ONNX model using ONNX Runtime and chooses from installed providers in this priority order:

1. `QNNExecutionProvider`
2. `VitisAIExecutionProvider`
3. `OpenVINOExecutionProvider`
4. `TensorrtExecutionProvider`
5. `CUDAExecutionProvider`
6. `DmlExecutionProvider`
7. `CPUExecutionProvider`

Provider availability is only a capability signal. The adapter tries candidate session initialization in priority order and records initialization failures. If inference fails on a selected non-CPU provider, it attempts a CPU-only session and records runtime fallback diagnostics. If CPU inference also fails, the adapter raises an error rather than silently returning fabricated output.

Input validation is intentionally strict:

- feature values must be finite, real numeric values;
- model input dimensions must meet the adapter's supported shape rules;
- values are not silently padded or truncated;
- floating-point ONNX input types are supported by the adapter;
- quantized integer inputs require an explicit model-specific quantization contract and are rejected without one.

A successfully created session does not prove that the model is compatible with every provider or that every graph operation executes on the accelerator. Device selection, model compatibility, actual execution placement, and numerical equivalence must be verified in the target environment. No model is bundled or claimed to be trained/validated by this plugin.

## CPU, GPU, and NPU policy

CPU is mandatory and defines the reference implementation. The transient API is in `transient_solver.py`:

- `MotorParameters` validates SI machine constants and the inductance matrix.
- `solve_transient_cpu(...)` integrates stator/rotor flux-linkage states, rotor speed, and lumped winding temperature with classical RK4. It accepts constant or sample-aligned dq voltage profiles using zero-order hold.
- `solve_transient_batch(..., backend="cpu"|"cuda"|"auto")` solves independent motors. The optional Numba-CUDA kernel maps one motor trajectory to each CUDA thread and stores state in one flat contiguous buffer. `auto` only considers CUDA for batches of at least eight motors; unavailable or failed CUDA attempts fall back to CPU and record the reason in result diagnostics.
- `MotorSimulationResult.channels(...)` derives current magnitude/components, torque, speed, slip, and winding-temperature channels for the read-model boundary.
- `cuda_available()` performs optional capability detection without making Numba a mandatory import for CPU operation.

The first solver model is deliberately bounded: balanced squirrel-cage induction motor, synchronous dq reference frame, linear inductances, constant load torque, and one lumped thermal node. It is not yet a validated high-fidelity model. It does not model magnetic saturation, unbalanced phase faults, PWM switching, separate stator/rotor thermal nodes, or load-torque curves. Choose the integration step based on the fastest electrical time constant and validate against trusted reference cases before using results for engineering decisions.

**NPU policy:** the solver's time recurrence has a dependency between consecutive integration steps and is not currently mapped to an NPU kernel. QNN, Vitis AI, and other ONNX Runtime NPU providers remain applicable to optional learned diagnostics/inference, not this physics integration. Do not report NPU motor-physics acceleration unless a compatible kernel/runtime is implemented and validated. CPU remains available if GPU support is absent or fails.

Provider discovery alone does not prove model compatibility or that a workload executes on a specific accelerator. Hardware capability, actual execution placement, and numerical equivalence require runtime verification.

## Persistence

`persistence.py` uses `SCHEMA_VERSION = 1` and stores plugin data as a mapping with this shape:

```json
{
  "schema_version": 1,
  "motors": [
    {
      "motor_id": "motor-example",
      "parameters": {}
    }
  ]
}
```

This is an illustrative structure; actual motor records are exported from the Application's motor registry when that registry exposes `export_records()`.

Import validates the schema version, list structure, unique non-empty motor IDs, and parameter-object type before delegating mutation to the Application's `import_motor_records_atomic` API. The persistence adapter does not serialize Qt graphics objects or solver buffers and does not directly mutate Core objects. Non-empty imports fail explicitly if the Application does not provide the atomic import service.

## Requirements and integration

The manifest declares Python `>=3.10`, with NumPy and SciPy for numerical helpers. ONNX Runtime is optional and is required only when the ONNX inference adapter is instantiated. The dashboard uses GridForge's `ui.core.qt` boundary and PySide6-backed Qt abstractions.

### Host integration checklist

The host application must provide all of the following for activation and meaningful use:

1. Register/enable the `gridforge.plugin.motor_dynamics` plugin through the host's plugin configuration.
2. Supply a valid `PluginContext` with `application`.
3. Supply `context.services["motor_dynamics_composition"]` with the required `register_motor_extension` method and separate `motor_service`.
4. Ensure the Application creation-contract registry contains a valid `motor` contract, including its terminal and parameter contracts.
5. Provide a compatible canvas view with optional preview and Bus-attachment hit-testing hooks if interactive placement is desired.
6. Route tool commands through the Application command-dispatch mechanism.
7. Publish simulation channels into `MotorSimulationReadModelStore` before expecting the dashboard to display them.
8. For inference, supply a compatible ONNX model and install/configure an ONNX Runtime build with the desired execution provider and its device dependencies.
9. For persistence import, provide the Application-owned atomic motor-record import API.

This README documents the current source design. It is not a claim that these host integration points have all been assembled or verified in a running application.

## Current limitations

- The extension remains experimental and opt-in.
- A preliminary induction-motor transient solver is present, but it is not a validated high-fidelity or certified study engine.
- The current solver integrates coupled electrical, mechanical, and lumped-thermal states with fixed-step RK4; equation correctness, numerical stability, and reference-case accuracy still require independent engineering validation.
- The optional Numba/CUDA implementation accelerates only the supported independent-motor constant-input batch path; it is not a general flattened 3D solver.
- NPU provider discovery applies to optional ONNX inference, not motor physics.
- Diagnostic labels and confidence are heuristic and not validated for protection or maintenance decisions.
- ONNX model compatibility and actual accelerator execution are not established by provider discovery alone.
- GUI thread behavior, Qt Charts availability, plugin activation, end-to-end draft commit, persistence import, and hardware execution require runtime verification.

Until these gaps are closed and validated, treat outputs as development/engineering aids rather than certified study results or protection decisions.

## Engineering principles

- **One source of electrical truth:** Core owns domain and topology truth.
- **Controlled mutation:** Application commands and transactions own draft changes and lifecycle.
- **Draft before commit:** placement/connectivity use the canonical `DraftNetwork` workflow.
- **No topology from graphics:** screen position and symbol geometry never determine electrical connectivity.
- **Read-only presentation:** UI consumers receive snapshots, not solver-owned mutable arrays.
- **Optional acceleration:** CPU remains the baseline; GPU/NPU are capability-dependent.
- **Explicit failure:** unavailable services, invalid model contracts, and failed inference are surfaced rather than silently ignored.
- **No overclaiming:** provider availability and a visible dashboard are not proof of a validated solver.

## Project links

- [GridForge repository](https://github.com/SubhenduMishra29/GridForge)
- [Motor Dynamics extension source](https://github.com/SubhenduMishra29/GridForge/tree/feature/motor-dynamics-extension-v2/plugins/motor_dynamics)
- [Pull request #674](https://github.com/SubhenduMishra29/GridForge/pull/674)

**Author:** Subhendu Mishra


## Expanded motor-family catalogue and instrumentation

The family catalogue now includes three-phase squirrel-cage and wound-rotor induction; single-phase split-phase, capacitor-start, permanent-split-capacitor, capacitor-start/capacitor-run and shaded-pole; wound-field synchronous, PMSM/IPM, BLDC, synchronous reluctance and switched reluctance; DC shunt, series, compound, separately excited and permanent-magnet; universal; variable-reluctance, PM and hybrid stepper; AC/DC servo; linear induction/synchronous; traction induction/PMSM; hysteresis and torque motors.

The immutable family profiles and validated SI nameplate contract distinguish family recognition from solver availability. The existing transient solver remains an experimental induction dq model. Other family profiles are catalogued and instrumentable, but their family-specific transient equations and reference-case validation remain open engineering work. Wound-rotor external resistance, single-phase auxiliary windings/capacitors, synchronous excitation, PMSM/BLDC commutation, reluctance torque, DC commutation, stepper holding/detent torque, servo loops and linear thrust are not claimed as implemented solvers.

Family-aware logical instrumentation points cover phase/armature voltage and current, power and frequency; speed, position and torque; vibration axes, bearing/winding/cooling temperatures; insulation resistance/leakage, shaft voltage and bearing current; field excitation; DC link and inverter telemetry; encoder/resolver/Hall feedback; starting capacitors and switches; brush/commutator condition; and linear thrust/air-gap/position.

Provisioning uses Core MeasurementProvisioning and requires explicit source equipment, authoritative Core terminals and EndpointReference for every configured point. Required electrical points must be supplied; optional points may be omitted. No physical sensor, CT/PT, terminal, topology or synthetic measurement channel is fabricated.

CPU remains the deterministic engineering reference. CUDA is limited to its declared supported induction batch workload. ONNX NPU/GPU providers remain optional diagnostic inference only and must never become the authority for physics state, protection trips or interlocks.


## PMSM transient model (experimental)

`solve_pmsm_transient_cpu(...)` adds a separate CPU/RK4 PMSM model in rotor-aligned dq coordinates. It supports surface and interior PM parameterization through independent d/q inductances and permanent-magnet flux linkage, temperature-adjusted stator resistance, electromagnetic/reluctance torque, rotor acceleration and electrical position, lumped winding thermal response, and zero-order-held d-axis voltage, q-axis voltage and load-torque profiles.

The solver returns immutable state/input arrays and derives stator current, dq currents, torque, rotor speed, electrical position/frequency, input power, copper loss and mechanical output power. It uses the shared Core numerical ODE engine. It does not reuse the induction CUDA kernel: no PMSM GPU workload is declared.

The model is experimental and is not a switching inverter or protection model. It omits PWM, current-loop control, magnetic saturation, iron and magnet losses, demagnetisation, field weakening, voltage/current limits and multi-node thermal networks. Validate parameter identification, numerical step selection and reference cases before engineering use. The traction-PM profile references this same simplified PMSM dq model only; traction-specific thermal, inverter and field-weakening physics remain unimplemented.

BLDC is deliberately not marked as solved by this PMSM model. Six-step trapezoidal back-EMF and commutation timing require a distinct phase-domain model.


## BLDC phase-domain transient model (experimental)

`solve_bldc_transient_cpu(...)` adds a separate three-phase brushless-DC model. It integrates two independent phase currents and derives the third from the floating-neutral constraint ia + ib + ic = 0. Rotor position drives three 120-degree-shifted trapezoidal phase back-EMF waveforms. The model includes phase resistance/inductance, electromagnetic torque, rotor acceleration, temperature-adjusted copper loss and one lumped winding thermal node.

The averaged inverter interface accepts a DC bus voltage and three signed normalized phase pole-voltage duty commands in [-1, 1]. Inputs may be scalar or sample-aligned profiles and use zero-order hold. Result channels include phase currents/back-EMFs, current RMS proxy, electromagnetic torque, rotor speed, electrical position/frequency, Hall-sector estimate, DC bus voltage/current, copper loss and mechanical output power. The Hall sector is derived from rotor electrical angle; it is not a simulated Hall sensor circuit.

This is an experimental CPU/RK4 reference model. It does not simulate PWM carrier switching, dead time, semiconductor drops, commutation controller state, current-loop dynamics, field weakening, magnetic saturation, demagnetisation, iron loss or multi-node thermal behaviour. The six-step waveform is an averaged phase-domain approximation; controller-generated duty profiles must supply the intended commutation sequence. The BLDC profile is not represented as a PMSM dq model. No BLDC GPU/NPU physics capability is declared.


BLDC instrumentation also exposes optional inverter commutation-sector and phase-duty telemetry, Hall state, and per-phase back-EMF points. These are provisioned only when actual drive/source endpoints are configured; the plugin does not fabricate sensor channels.


## Synchronous-reluctance motor (SynRM) transient model (experimental)

`solve_synrm_transient_cpu(...)` implements a rotor-aligned dq current model with d/q inductance saliency, reluctance torque, mechanical inertia/friction and a lumped winding thermal state. It accepts d/q stator-voltage and load-torque profiles and uses the shared Core RK4 engine. This linear model requires Ld > Lq. It excludes magnetic saturation, iron loss, cross-saturation, PWM switching and inverter current-loop control.

## Switched-reluctance motor (SRM) transient model (experimental)

`solve_srm_transient_cpu(...)` implements a three-phase averaged phase-domain model. Each phase has rotor-position-dependent inductance, an approximate current-saturation correction, phase voltage input, copper loss and phase torque contribution T = sum(0.5 * i_phase^2 * dL_phase/dtheta). The cosine inductance profile is a reduced-order analytical approximation, not measured nonlinear flux-linkage maps. Asymmetric-bridge switching, current chopping, demagnetisation paths, hysteresis, mutual coupling and detailed saturation are not modeled.

Both are experimental CPU reference solvers using the shared Core numerical RK4 engine. No GPU/NPU capability is declared for these state-recurrent models.


## Brushed DC motor transient model (experimental)

`solve_dc_motor_transient_cpu(...)` provides an excitation-explicit averaged model for separately excited, shunt, series, compound and PM brushed DC machines. The armature circuit couples to rotor back-EMF and electromagnetic torque. Separately excited fields have an independent RL supply; shunt and compound shunt fields follow the armature terminal voltage. Series-field current equals armature current and its copper resistance is included in the armature path. Compound winding polarity is selected as cumulative or differential. Flux uses a bounded tanh saturation approximation, with separate armature and field thermal states and temperature-adjusted resistance.

Sample-aligned armature voltage, field voltage and load-torque profiles produce armature/field/series current, flux, back-EMF, torque, speed, position, copper losses, electrical/mechanical power and winding temperatures. CPU/RK4 uses the shared Core numerical engine.

The reduced-order model omits commutator segmentation, brush contact drop/arcing, interpoles, compensating windings, armature reaction, magnetic hysteresis, detailed converter switching and spatial thermal networks. Validate parameter identification and operating envelopes before engineering use. No GPU/NPU capability is declared.


## Wound-rotor induction motor extension

The shared induction dq model accepts a non-negative fixed `external_rotor_resistance_ohm` in addition to rotor winding resistance. This enables comparative starting/torque-speed studies for a selected external resistance setting and publishes rotor current magnitude, total rotor-circuit resistance and rotor copper loss. The wound-rotor family is mapped to the existing induction dq solver.

This is not a slip-ring starter sequence or resistor-bank thermal model: resistance is fixed for each run; brush/slip-ring contact, per-phase resistor switching, separate rotor/stator thermal nodes and unbalanced rotor circuits remain outside the current fidelity boundary.


## Single-phase induction motor family model (experimental)

A reduced-order RMS-envelope model is available for split-phase, capacitor-start, permanent-split-capacitor, capacitor-start/run and shaded-pole families. It tracks main and auxiliary winding current envelopes, auxiliary phase displacement, a starting-winding cutout approximation for split-phase/capacitor-start variants, torque estimate, rotor speed/position and lumped winding temperature.

This is a comparative reduced-order envelope model, not a cycle-resolved double-revolving-field equivalent circuit. It omits spatial air-gap fields, rotor slot harmonics, magnetic saturation, detailed capacitor switching, centrifugal-switch mechanics, shading-coil eddy-current field solution, inverter switching and separate winding thermal nodes. Validate against manufacturer test data before engineering use. CPU/RK4 only; no GPU/NPU capability is declared.


## Universal series-wound commutator motor (experimental)

The universal motor profile has a distinct series-field CPU model for AC and DC supplies. Armature and series-field current are the same state, with separate resistance and inductance contributions, a saturating field-flux approximation, electromagnetic torque, back EMF, speed/position, copper losses and lumped winding temperature. AC input is represented by an averaged rectified voltage envelope; DC uses its supplied voltage directly.

This is not a cycle-resolved AC commutator model. Brush contact drop, commutation overlap, arcing, interpoles, armature reaction, magnetic hysteresis, field weakening and detailed thermal networks are omitted. The model is CPU/RK4 only and must be validated against manufacturer data before engineering use.


## Wound-field synchronous motor (experimental)

The wound-field synchronous profile has a separate CPU rotor-dq envelope solver with independently supplied stator d/q voltages and rotor DC field voltage. It tracks d/q stator currents, field current, d/q damper-current states, rotor angle and speed, synchronous-speed error, torque, copper losses and lumped winding temperature. Damper current states currently decay through their resistive-inductive time constants; induced damper EMF and full mutual flux-linkage matrix dynamics are not yet represented.

This is an experimental averaged model, not a validated pull-in, synchronising, hunting or transient-stability model. It omits saturation, cross-saturation, iron loss, exciter/AVR dynamics, brushless exciter, detailed damper cage coupling, stator terminal-network coupling and PWM switching. CPU/RK4 only.


## Hysteresis synchronous motor (experimental)

A dedicated CPU torque-angle envelope model is available for the hysteresis motor family. It exposes rated RMS voltage, frequency, pole pairs, pull-out torque, inertia, friction and a rotor-loss fraction; outputs include rotor speed/angle, load angle, torque, synchronous-speed error, pull-out torque margin and rotor thermal estimate.

This is not a material hysteresis model: it does not resolve rotor ring magnetisation, hysteresis-loop energy, asynchronous starting/pull-in, detailed air-gap fields, voltage/frequency transients or validated synchronising dynamics. The pull-out margin is a static voltage-scaled torque-capacity estimate, not a dynamic stability certificate. CPU/RK4 only.


## AC servo family: instrumentation and model boundary

The AC-servo catalogue now distinguishes synchronous permanent-magnet and induction servo technology in its engineering description and defines drive-side feedback points separately from motor-terminal metering. Optional points cover DC-link measurements, d/q current references and feedback, encoder position/speed, position following error, speed/torque commands, inverter temperature, holding-brake state, drive-ready state, and drive-fault state. The host must bind each configured point to a real source and canonical Core terminal; no encoder, drive, brake, or instrument is fabricated by the plugin.

**Dynamic model status:** this work does not claim an AC-servo transient solver. A synchronous PM servo requires a servo-specific control/feedback model around its machine equations; an induction servo requires its own rotor-flux and vector-control model. The existing general PMSM and induction solvers are not silently substituted. Resolver/encoder quantisation, current/speed/position loops, PWM/dead-time, brake sequencing, drive protection and validated servo tuning remain outside the implemented model.


## Synchronous-PM AC servo cascade

An experimental CPU/RK4 model now integrates cascaded position-P, speed-PI and d/q-current-PI control around the PMSM rotor-dq equations. It includes bounded speed/current/voltage commands, conditional anti-windup, encoder position quantisation, mechanical position/speed, electromagnetic torque, winding temperature, copper loss and regenerative mechanical power reporting. Use `PMSMServoParameters` and `solve_pmsm_servo_transient_cpu`; the model is separate from the open-loop PMSM solver.

This is an averaged control-envelope model, not a validated drive commissioning or safety model. PWM switching/dead-time, inverter DC-link energy dynamics, discrete controller delays, resolver dynamics, torque ripple, STO/brake safety sequencing and validated gain tuning are omitted. The AC-servo family still includes induction servo variants, which require a distinct rotor-flux-oriented servo model and are not covered by this synchronous-PM implementation. GPU/NPU acceleration is not claimed for this stateful cascade.


## Induction AC servo rotor-flux FOC (experimental)

A distinct CPU/RK4 induction-servo model is available through InductionServoParameters and solve_induction_servo_transient_cpu. It uses a reduced-order rotor-flux-oriented induction-machine envelope with a rotor-flux state, position-P / speed-PI / d-q-current-PI cascade, encoder quantisation, current and voltage limits, conditional anti-windup, mechanical position/speed, torque, copper-loss and lumped winding-temperature outputs. This is not an alias for the PMSM servo solver.

The model is an experimental averaged-control approximation. It does not resolve PWM/inverter switching or dead-time, discrete controller delay, rotor-flux observer error, field weakening, DC-link energy, detailed saturation/cross-saturation, parameter identification, safety-rated STO/brake sequencing or validated drive commissioning. Use measured drive feedback and independently validate controller tuning before engineering decisions. CPU/RK4 only; no GPU/NPU capability is claimed.


## Brushed-DC servo cascade (experimental)

DCServoParameters and solve_dc_servo_transient_cpu provide an averaged position-P / speed-PI / armature-current-PI cascade around the existing DC excitation model. Explicit machine excitation configuration remains required. Outputs include position/speed, current and voltage commands, torque, power and winding thermal channels. CPU/RK4 only; PWM/chopper switching, brush commutation, armature reaction, field weakening, discrete timing, brake sequencing and safety-rated commissioning are not modelled. Not validated for drive commissioning; no GPU/NPU capability is claimed.


## Stepper transient model (experimental)

StepperParameters and solve_stepper_transient_cpu provide a two-phase averaged current-regulated model for variable-reluctance, permanent-magnet and hybrid stepper families. The solver integrates phase currents, rotor speed/position and lumped winding temperature; channel projections include phase current/voltage and references, following error, electromagnetic/detent torque, copper loss and mechanical output power. It uses the shared Core ODE engine and CPU/RK4 only.

The model uses sinusoidal microstep current references and a reduced-order reluctance/PM torque approximation. It does not resolve PWM/chopper switching, measured nonlinear phase flux-linkage maps, detailed tooth geometry, magnetic saturation, microstep nonlinearity, resonance/pull-out curves, acceleration-profile generation, encoder dynamics, automatic missed-step detection or safety-rated drive behaviour. No GPU/NPU capability is claimed for this solver.


## Linear motor transient models (experimental)

Linear induction and linear synchronous families have separate translational models; neither is mapped to a rotary torque/speed solver.

- `LinearInductionParameters` / `solve_linear_induction_transient_cpu`: travelling-field dq equivalent circuit, primary/secondary flux and current, slip relative to translator velocity, electromagnetic thrust, translator position/speed, copper loss and lumped winding temperature.
- `LinearSynchronousParameters` / `solve_linear_synchronous_transient_cpu`: PM d/q current dynamics, pole-pitch spatial frequency, electromagnetic thrust, translator position/speed, copper loss and lumped winding temperature.

Both use the shared Core ODE engine with CPU/RK4. These are reduced-order engineering study models, not validated commissioning models. Linear induction omits longitudinal/transverse end effects, deep-bar/skin effect, saturation and secondary segmentation. Linear synchronous omits segmented magnet fields, end effects, cogging detail, PWM/dead-time, inverter and DC-link dynamics. Air-gap is provisioned as measured/configured telemetry, not calculated by either solver. No GPU/NPU acceleration is claimed for these solvers.


## Voice-coil linear actuator (experimental)

The `VOICE_COIL` family uses a distinct translational actuator model rather than the induction or synchronous travelling-field solver. `VoiceCoilParameters` and `solve_voice_coil_transient_cpu` model coil resistance/inductance, force constant, velocity back-EMF, moving mass, suspension stiffness/damping, external load, stroke and a lumped winding thermal state. The result exposes coil current, force, position, velocity, back-EMF, stroke utilization, end-stop reaction and copper loss.

The implementation is an averaged lumped R-L electromechanical model on the shared Core CPU/RK4 engine. It does not resolve magnetic saturation, eddy currents, flexure modes, spatial field nonuniformity, amplifier switching or a detailed thermal network. No GPU/NPU acceleration is claimed.
