# Generator Dynamics Plugin

Initial engineering foundation for GridForge V2's universal generator plugin.

## Scope

- Diesel and gas engines, steam and gas turbines, hydro, wind (DFIG and
  full-converter), synchronous, induction, permanent-magnet, inverter-based,
  fuel-cell and microturbine families.
- Immutable nameplate and model parameter contracts.
- Synchronous-machine d/q transient and subtransient parameter validation.
- AVR/exciter, governor/actuator and prime-mover parameter contracts.
- Executable generic first-order AVR, TGOV1-style governor/turbine lag, and washout/lead-lag PSS models implementing the existing DynamicPlugin equation contract.
- Technology-specific instrumentation point templates.
- Explicit physical instrument/secondary-terminal mapping through Core's
  MeasurementProvisioning and canonical MeasurementChannel.
- Capability-gated CPU/GPU/NPU workload selection using Core's shared
  BackendCapability and select_backend policy.

## Measurement point rule

Templates are definitions, not live signals. Every point must be explicitly
mapped to a physical source instrument, its authoritative Core terminal, and
an EndpointReference. The Core provisioning boundary validates instrument
identity and secondary terminal ownership. Project-managed sample updates
remain owned by MeasurementChannelService.

The initial point catalogue includes three-phase current and voltage,
line-to-line voltage, neutral current, MW/MVAr/MVA, power factor, frequency,
phase angle, speed, torque, vibration, bearing/stator temperatures, field
voltage/current, governor reference/actuator, synchronizing quantities,
breaker/trip status, and family-specific fuel, pressure, exhaust and
converter DC-link points.

## Compute policy

- CPU is the reference/fallback backend.
- GPU may execute only workloads its runtime adapter explicitly declares.
- NPU is eligible only for generator condition/anomaly inference.
- No hardware discovery, runtime import, device assumption or AI-based
  protection/trip decision is performed by this package.
- Existing shared numerical policy remains authoritative for compatibility,
  precision, fallback and diagnostics.

## Dynamic controller model boundary

The first controller set is explicitly identified as generic first-order AVR,
TGOV1-style governor/turbine lag and washout/lead-lag PSS. They expose local
initial-state, derivative and output equations through the existing
DynamicPlugin contract. They do not own integration or simulation time.

These controller equations are not yet wired into the transient DAE. The
current DAESolver state layout is machine-only; the transient network solver
also consumes that exact machine-state vector, while the classical machine
uses constant Efd and the DAE accepts a fixed mechanical-power map. A correct
integration requires a single augmented global state layout, time-stage
controller evaluation, governor-to-mechanical-power coupling, AVR-to-internal
emf/network-source coupling, and a consistent algebraic solution strategy for
terminal-voltage feedback. Merely calling controller derivatives beside the
existing solver would create unsynchronized state and is intentionally not
implemented.

## Parameterized IEEE TGOV1 steam-governor block

A separate `TGOV1SteamGovernor` implementation is provided rather than
presenting the generic droop/turbine-lag block as a complete TGOV1 model.
Its two states are valve position and reheater/turbine power. On the connected
machine per-unit base, the model evaluates:

- Governor/valve command: `g = (P_ref - speed_deviation) / R - valve`
- Valve derivative: `dvalve/dt = g / T1`, with hard outward-limit blocking
- Turbine derivative: `dPt/dt = (valve + T2*dvalve/dt - Pt) / T3`
- Mechanical output: `Pm = Pt - Dt*speed_deviation`

The initialization routine derives the equilibrium valve and turbine states
from the requested mechanical power and speed, and rejects a supplied power
reference inconsistent with that equilibrium. The model requires the caller
to supply the reference and speed at each derivative/output evaluation.

This implementation is a focused TGOV1 block, not a claim that all steam
turbine/governor variants are represented. Turbine rating/base conversion,
deadband variants, nonlinear valve characteristics, boiler dynamics, and
model-specific limiter extensions remain explicit future work. The generic
droop block remains separately available and must not be identified as IEEE
TGOV1 in engineering studies.

Reference model documentation used for the parameter/equation structure:
- ORNL GridKit TGOV1 model: https://github.com/ORNL/GridKit/blob/develop/GridKit/Model/PhasorDynamics/Governor/Tgov1/README.md
- Dynawo TGOV1 model documentation:
  https://build.openmodelica.org/Documentation/Dynawo.Electrical.Controls.Machines.Governors.Standard.Steam.TGov1.html
- PowerWorld TGOV1 model parameter reference:
  https://www.powerworld.com/WebHelp/Content/TransientModels_HTML/Governor%20TGOV1%20and%20TGOV1D.htm

## Integration boundary

GeneratorDynamicsPlugin is activated only by the canonical
EngineeringPluginHost and requires the Application composition root to
supply generator_dynamics_composition.register_generator_extension.
This first milestone intentionally does not wire a fake composition service
or bypass the Application command/study/persistence lifecycle. UI, commands,
study handlers, persistent equipment configuration and result projections
are subsequent integration work.

## Current status

This is a foundation milestone, not a complete generator simulator or a
claim of standards certification. Family-specific electrical machine dynamic equations, validated machine
parameter sources, instrument-specific CT/PT/CVT error/saturation models,
protection function wiring, DAE-integrated control execution, persistence
migration, SLD editors and runtime-specific accelerator adapters remain open.

## Hydro and legacy gas-turbine governor model selection

HYGOV is a distinct hydro governor/penstock model, not a generic droop
controller. Its published parameter contract includes permanent and temporary
droop, governor and filter time constants, gate-servo time constant, gate
velocity and position limits, water starting time, turbine gain and damping,
and no-load flow. Its turbine/penstock response is operating-point dependent;
the linearized penstock transfer function depends on water time constant,
initial flow and initial head. A future implementation must preserve the
nonlinear water-flow/head relationship or state clearly which linearization
and operating point it uses. It must not silently replace HYGOV with the
existing generic two-lag governor.

The legacy PSS/E GAST model is also a distinct model, with governor/valve,
combustion-chamber/turbine and exhaust/load-limit dynamics. It is retained as
a compatibility model only if implemented; it must not be selected as the
default model for a modern digitally controlled gas turbine. NERC's 2017
modeling notification describes GAST as a simplified early model, notes that
it omits important heavy-duty gas-turbine physics, and recommends transition
to a more representative GGOV1 model after verification. Any future GAST
implementation must therefore be explicitly labeled legacy, preserve its
ambient-temperature load limit and low-value-gate behavior, and require
plant-specific verification. A modern gas-turbine controller should be
represented by a separately parameterized model such as GGOV1 only after its
full state/limiter contract and machine-base conversions are implemented.

References for model selection and parameter boundaries:
- NERC, *Gas Turbine Governor Modeling* (2017), Appendix B and recommendations:
  https://www.nerc.com/comm/PC/NERCModelingNotifications/Gas_Turbine_Governor_Modeling.pdf
- ORNL GridKit HYGOV parameter contract and documented limitations:
  https://gridkit.readthedocs.io/en/latest/GridKit/Model/PhasorDynamics/Governor/HYGOV/README.html
- OpenIPSL HYGOV model parameters and connectors:
  https://build.openmodelica.org/Documentation/OpenIPSL.Electrical.Controls.PSSE.TG.HYGOV.html
- Argonne, *Hydro Turbine-Governor Simulation Models in PSS/E* (2013), including
  the operating-point-dependent penstock linearization:
  https://www.osti.gov/servlets/purl/1098022

These model-selection notes do not claim full HYGOV, GAST or GGOV1 plant-model
equivalence. The plugin contains a reduced nonlinear HYGOV block and a
GGOV1 frequency-control PID/reset sub-block; neither is a complete plant
model. Model-specific hydro and gas-turbine actuator, turbine/fuel and limiter
paths remain separate implementation milestones. No controller is connected
to the transient DAE until the canonical augmented-state contract is available.

## Reduced nonlinear HYGOV hydro-governor block

`HYGOVReducedHydroGovernor` is the first executable hydro-governor milestone. It is deliberately named **reduced**: it implements a permanent-droop PI governor, first-order error filter, gate servo with position and velocity limits, and a nonlinear inelastic single-penstock water column. The parameter contract is explicit about per-unit quantities and seconds.

With speed deviation Δω, reference Pref, filtered error xf, integrator xi, gate g, flow q and head H, the implemented equations are:

- `e = Pref - Δω - R*g`
- `d(xf)/dt = (e - xf)/Tf`
- `c = xi + xf/r`; `d(xi)/dt = xf/(r*Tr)`
- Gate command is clamped to `[GMIN, GMAX]`; `dg/dt = (clamped_command - g)/Tg`, bounded by `±VELM`. Motion farther through a hard gate-position limit is blocked, as is integrator motion that drives further into command saturation.
- `H = (q/g)^2`; `dq/dt = (Hdam - H)/Tw` for an open gate. At the closed-gate, zero-flow point the model uses nominal head.
- `Pm = At*H*(q - qNL) - Dturb*Δω*g`

This corresponds to the nonlinear inelastic penstock relationship and turbine power form documented in GridKit; parameter names are cross-checked against OpenIPSL and OPAL-RT documentation. References: GridKit HYGOV equations and limitations (https://gridkit.readthedocs.io/en/latest/GridKit/Model/PhasorDynamics/Governor/HYGOV/README.html), OpenIPSL HYGOV parameter contract (https://build.openmodelica.org/Documentation/iPSL.Electrical.Controls.PSSE.TG.HYGOV.html), and OPAL-RT HYGOV parameter definitions (https://opal-rt.atlassian.net/wiki/spaces/PDOCHS/pages/150276051/HYGOV).

### Explicit reduced-model limitations

This is **not a drop-in claim of complete PSS/E or IEEE PES-TR1 HYGOV equivalence**. This first block uses a linear gate-to-flow characteristic (`P_GV(g)=g`) and fixed nominal dam head; it does not yet model the full gate characteristic curve, head-dependent dam/tailrace conditions, deadband/backlash variants, Kaplan blade servo, or turbine-rating conversion. Initialization requires a steady operating point satisfying `q = g * sqrt(Hdam)` and `Pref = Δω + R*g`; it sets the filtered error to zero and the integrator to the requested gate position for a bumpless initial command. Plant-specific parameter validation and comparison against a trusted reference implementation are still required.

The class follows the existing `DynamicPlugin` local-state contract only. It does not own a clock, mutate Core, or connect itself to the transient DAE.


## GGOV1 gas-turbine governor parameter contract

\`GGOV1Parameters\` is the validated, immutable parameter contract for the next gas-turbine governor implementation milestone. It captures the distinct turbine MW rating, droop feedback selector (\`Rselect\`), fuel-speed selector (\`Flag\`), PID governor gains, electrical-power measurement lag, governor error and valve limits, actuator time/rate limits, turbine lead-lag and transport constants, no-load fuel flow, load limiter, plant power controller, acceleration limiter, speed deadband, temperature-detection lead/lag, and load-limit slew rates.

The parameter contract is accompanied by `GGOV1PIDGovernor`, an executable PSS/E-derived frequency-control PID and supervisory reset sub-block. The plugin descriptor advertises both `ggov1_parameter_contract` and `ggov1_pid_subblock`, but deliberately does not advertise a complete `ggov1_executable` capability. The block is not a complete GGOV1 governor and must not be selected as a full plant model. The PID sub-block exposes local initialization, derivatives and output through the existing DynamicPlugin contract; it is not connected to the transient DAE. The separate `GGOV1ValveActuator` implements the first-order `Tact` response with asymmetric `Ropen`/`Rclose` slew limits and `Vmin`/`Vmax` position stops. It accepts the already selected and limited fuel-stroke command. The separate `GGOV1LowValueSelector` now provides the minimum-selection and tracking-error contract, while detailed OEM calibration and integrated plant-level control remain open.

The turbine rating is explicit, positive, and is not inferred from generator MVA. \`system_to_turbine_power_base_ratio()\` exposes the numerical system-base/turbine-rating ratio; the caller must still apply the correct MW/MVA and per-unit convention for the connected machine and study. No hidden base conversion is performed. The selector contract follows the NERC PSS/E definition: \`Rselect\` values 1, 0, -1 and -2 select electrical power, no feedback, valve stroke and governor output respectively; \`Flag\` is binary. Parameter checks enforce ordered error/valve limits, positive opening and negative closing rates, and the turbine lead/lag zero-time consistency rule. Mechanical damping is finite but intentionally not constrained to be positive because published manufacturer data can use a negative value.

The implemented PID sub-block follows the PSS/E-derived OpenIPSL topology for selected droop feedback, filtered electrical-power measurement, dead-zone/error limiting, proportional/integral/filtered-derivative control, bounded supervisory reset integration, and operating-point initialization. It deliberately does not claim the complete GGOV1 behavior: valve actuator rate/position dynamics, fuel/turbine response, load-controller tracking, low-value selection among governor/load/acceleration/temperature limiters, OEM-specific temperature maps and ambient compensation, complete limiter scheduling, optional transport delay, and integrated DAE execution remain open. The block is not connected to the transient DAE. See `GGOV1_PID.md` for the state and equation boundary.

NERC describes GGOV1 as a general-purpose PID governor with valve actuation, fuel-system dynamics, exhaust-temperature load limiting, load control, acceleration limiting, and governor deadband. It also emphasizes plant-specific verification, correct turbine MW rating, and avoiding confusion between turbine rating and generator MVA base. Manufacturer default values are starting points, not universal settings. References:

- NERC, *Gas Turbine Governor Modeling* (2017), Appendix A and parameter/base guidance: https://www.nerc.com/comm/PC/NERCModelingNotifications/Gas_Turbine_Governor_Modeling.pdf
- NERC, *Application Guide for Modeling Turbine-Governor and Active Power-Frequency Controls in Interconnection-Wide Stability Studies* (2019), Appendix C: https://www.nerc.com/globalassets/who-we-are/standing-committees/rstc/ppmvtf/reliability_guideline-application_guide_for_turbine-governor_modeling-2019-05-07-CLEAN.pdf
- OpenIPSL GGOV1 parameter/model package documentation: https://build.openmodelica.org/Documentation/iPSL.Electrical.Controls.PSSE.TG.GGOV1.GGOV1.html
- ORNL GridKit GGOV1 equations and explicit implementation caveats: https://github.com/ORNL/GridKit/blob/develop/GridKit/Model/PhasorDynamics/Governor/GGOV1/README.md

ORNL GridKit currently marks multiple GGOV1 damping, fuel-flow, PID, limiter, temperature and transport-delay paths incomplete or unverified. Its equations are therefore useful for cross-checking signal/parameter names, but are not treated here as sufficient proof of a complete reference implementation. Plant/OEM data and disturbance-based model verification remain mandatory before study use.


**Selector-source reconciliation:** NERC's PSS/E GGOV1 table defines `Rselect` as `1 = Pelec`, `0 = no feedback`, `-1 = fuel-valve stroke`, and `-2 = governor output`. The ORNL GridKit explanatory equations currently document a different mapping for the non-default values and omit selector 0 in its validation set. The executable selector helpers now follow the NERC/PSS/E mapping. The PID and supervisory reset sub-block are executable locally; the low-value selector and explicit PID tracking input are also available as separate contracts. Acceleration/load/temperature limiter dynamics and tracking for limiter controllers remain incomplete. The GGOV1D deadband variant is also kept distinct from GGOV1 rather than silently applied to both.


The selector helpers have deliberately narrow responsibilities: \`droop_feedback_signal()\` returns Pelec for Rselect=1, zero for isochronous Rselect=0, valve stroke for -1, and governor output for -2; \`fuel_speed_multiplier()\` returns shaft speed for Flag=1 and unity for Flag=0. \`GGOV1PIDGovernor\` uses the selected feedback in its speed-error summing junction and implements the local PID and supervisory reset states. It does not implement fuel/turbine dynamics or the outer low-value selector. The NERC block diagram also shows controller tracking during low-value-select transfers; that behavior remains a required part of full GGOV1 integration rather than being approximated by the selector helpers.


### GGOV1 fuel-valve actuator sub-block

`GGOV1ValveActuator` contributes one solver-owned state, actual valve position.
Its continuous-time response is the first-order actuator request
`(bounded_command - valve_position) / Tact`, limited to the configured
negative closing and positive opening rates. Position stops prevent further
motion beyond `Vmin` and `Vmax`; an out-of-range numerical trial state may
move back toward the admissible interval. Initialization clamps the supplied
initial position to the configured bounds.

The input is explicitly the post-selector, post-limiter fuel-stroke request.
This block does not choose between the speed, temperature and acceleration
controllers, and does not implement controller tracking during selector
transfers. It is a valve actuator sub-block, not a complete GGOV1 turbine
model and is not yet coupled to the transient DAE.

### GGOV1 turbine fuel-to-mechanical-power sub-block

`GGOV1TurbineFuelPath` adds the turbine fuel response after the actual valve
stroke. `Flag=0` makes fuel flow equal to valve stroke; `Flag=1` multiplies
valve stroke by absolute shaft speed in per-unit. The no-load fuel flow
`Wfnl` is subtracted before the turbine gain. For `Tb > 0`, one solver-owned
state represents the lagged net fuel flow and the output applies the
`(1+s*Tc)/(1+s*Tb)` lead-lag response. `Tb=Tc=0` is an algebraic bypass.
Mechanical output is `Kturb * turbine_response - Dm * speed_deviation`; this
uses a linear damping correction and is not a substitute for every
manufacturer-specific speed-dependent capability curve.

`Teng` is deliberately not implemented with an internal queue or private
clock. When `Teng > 0`, the caller must supply `delayed_fuel_flow_pu` from a
solver-owned transport-delay facility at every evaluation; initialization
requires an explicit initial fuel-flow value. When `Teng=0`, the delay input
is not used. This keeps all integration history and time ownership in the
host solver. The block does not implement the outer low-value selector,
limiter tracking, exhaust-temperature/acceleration/load limiting, or DAE
coupling. It is a GGOV1 turbine/fuel response sub-block, not a complete
GGOV1 model.


### GGOV1 low-value selector and governor tracking

`GGOV1LowValueSelector` selects the smallest active fuel request from the
speed governor, acceleration limiter and combined load/temperature limiter.
Disabled limiter inputs are omitted; the governor request is mandatory.
Equal values use a stable source priority so the selected-source identity is
repeatable. The immutable result includes each active controller's
selected-minus-requested tracking error.

The selector does not fabricate acceleration or load/temperature controller
outputs. Those controllers must calculate their own requests on the same
fuel-command base before selection. The combined load/temperature candidate
must itself already reflect the applicable plant-specific limiter logic.

`GGOV1PIDGovernor.derivatives()` accepts the selected fuel command and an
explicit positive tracking time constant together. It adds
(selected command - raw governor PID output) / tracking time to the governor
integral derivative. If either input is omitted, the existing standalone PID
integrator equation is retained. This makes limiter tracking an explicit
solver/composition input rather than embedding an undocumented universal
constant. The selector and tracking hook do not implement the missing
acceleration, load, or exhaust-temperature controller dynamics, nor do they
make the complete GGOV1 plant model executable.




### GGOV1 acceleration, load and temperature paths

The acceleration controller follows the PSS/E/OpenIPSL block topology. Its
single solver-owned state filters machine speed deviation with the `Ta`
derivative filter. The algebraic acceleration request is
`FSRA = FSR + Ka * DELT * (ASET - d(SPEED)/dt)`. The host supplies the
currently selected `FSR` command and the model's discrete sample interval
`DELT`; this feedback is the reference tracking path. `Ta` must be positive.

`GGOV1LoadLimiter` follows OpenIPSL `LoadLimiter.mo`:
`TREF = Wfnl + LDREF / Kturb`, `eT = TREF - TEXM`,
`d(integral)/dt = Kiload * eT`, and
`FSRT = min(1.0, Kpload * eT + integral)`. The integral starts from
`(PELEC + Dm) / Kturb + Wfnl`. The block's upper-only 1.0 pu clamp matches
that reference component; the common Vmin/Vmax clamp is applied after the
three-way low-value selector in the outer GGOV1 path.

`GGOV1TurbineFuelPath.temperature_proxy_input()` supplies the pre-filter
temperature proxy: net fuel flow multiplied by the PSS/E `Dm_select(speed)`
factor. `GGOV1TemperatureMeasurement` then cascades the `(1+s*Tsa)/(1+s*Tsb)`
temperature-detection lead/lag with the `1/(1+s*Tfload)` lag to produce
`TEXM`, which feeds the load limiter. This is the model's calculated
temperature proxy, not a thermocouple measurement or OEM firing-temperature
map.

The three candidate requests—governor `FSRN`, acceleration `FSRA`, and
temperature/load `FSRT`—feed `GGOV1ControlCoordinator`. It applies the
low-value selector followed by the common `[Vmin, Vmax]` clamp and returns an
immutable `GGOV1ControlDecision`. Its `actuator_command_pu` is the one final
command to pass to `GGOV1ValveActuator.derivatives()` and back to the
acceleration limiter. Pass the same command as `selected_fuel_command_pu`
to PID and load-limiter derivatives when enabling their explicit tracking
inputs. The decision exposes final-command-minus-request tracking errors for
each active candidate. The coordinator owns no dynamic state or solver clock.
 
 `calculate_ggov1_operating_point()` provides a pure shared initialization
 calculation from initial electrical/mechanical power at nominal shaft speed. It
 derives the common initial FSR, actual valve target, Flag-dependent fuel
 flow, modeled temperature-proxy input, and Rselect-consistent PID power
 reference for zero initial governor error, and the initial governor-output
 feedback value. It also reports the PID/load-PI
 integral alignment residual and the load-limiter output residual. Set `load_limiter_enabled=False` when that path is disabled. Call
`require_balanced()` when it is enabled and all initialized paths must agree.
The helper rejects an initial FSR outside common valve limits, and above the
1.0 pu ceiling when the load limiter is enabled. The host still calls each block's own `initial_state()`;
 the helper owns no solver state or lifecycle.

 The acceleration limiter is sampled: calculate FSRA using the previous
 selected FSR feedback and the host's positive `DELT`, then perform the
 three-way selection and common clamp. Feed the resulting command to the
 actuator and use it as the FSR feedback for the next acceleration sample.
 The host owns sample ordering and the DELT clock; do not evaluate this
 feedback loop as an instantaneous algebraic cycle. `Rup`/`Rdown` are retained as model data
but are not applied by the OpenIPSL GGOV1 reference implementation used
here; they must not be confused with actuator `Ropen`/`Rclose`.

These are reference-derived sub-blocks, not a complete integrated GGOV1
plant model. Vendor-specific thermal maps, ambient compensation, transport
delay execution, complete tracking composition, DAE integration and
disturbance-based validation remain open.


### GGOV1 sampled execution adapter

`GGOV1SampledExecutionAdapter` composes the existing PID, acceleration,
temperature, load-limiter, low-value selector, common FSR bounds, valve
actuator and turbine/fuel blocks for one explicitly requested host sample.
The immutable `GGOV1SampleStates` is a snapshot of solver-owned local state
vectors; `GGOV1SampleInputs` carries external measurements, the host sample
interval, previous-sample selected FSR feedback, and the explicit
`Rselect=-2` governor-output feedback input. The returned
`GGOV1SampleEvaluation` contains algebraic requests, the common command,
local derivative vectors, TEXM proxy and mechanical-power output.

The sample order is fixed: read actual valve position from the actuator state;
derive the pre-filter temperature proxy and TEXM; calculate governor and
load/temperature requests; calculate acceleration request using the previous
sample's selected FSR and host DELT; perform three-way selection and common
valve bounds; then calculate PID/load tracking, actuator and other continuous
state derivatives using the current final command. The actuator derivative
does not change the state snapshot during this call. The host applies all
returned derivatives through its own integration scheme and supplies the
updated state and next DELT/previous FSR on the next call.

This is a block-composition adapter, not an Application scheduler or a
transient DAE integration. It does not own state, clock, event loop, command
manager, transport-delay history, persistence, or service registry. For
`Teng > 0`, the host must supply delayed fuel flow from its own delay
facility. The governor-output feedback input is deliberately explicit rather
than hiding a circular or algebraic feedback dependency. All blocks must use
the same GGOV1 parameter contract and explicit positive tracking constants.
The adapter does not certify OEM equivalence or plant-level model validity.

### Application StudyService registration boundary

The plugin now registers the study type `generator_transient` with the
existing Application-owned `StudyService` and unregisters it by handler
identity during shutdown. StudyService therefore remains the only owner of
study scheduling, cancellation tokens, provenance checks, terminal status,
durable StudyResult persistence and lifecycle events. The plugin creates no
second scheduler or result store.

The host-supplied `generator_dynamics_composition` must expose both
`generator_transient_solver.solve_generator_transient(...)` and
`create_generator_sampled_adapter(request=..., execution_context=...)`.
The adapter factory is called per StudyRequest so different generator
machines/studies do not share parameterized controller instances. The solver
receives the canonical StudyRequest, detached StudyExecutionContext,
StudyCancellationToken and sampled adapter. It owns numerical integration,
sample acceptance, rejected-step rollback, partial trajectory evidence and
solver-specific delay handling.

This establishes the plugin-side registration and lifecycle contract. The
Application composition root still needs to provide that solver capability
and adapter factory; without them activation fails closed. This branch does
not claim that a production transient solver or DAE integrator has been wired
or validated.

### Current host-integration blocker

Source reconciliation of the existing Application composition root and DAE
solver shows that plugin-side StudyService registration is not sufficient to
claim a runnable GGOV1 transient study. The current bootstrap creates the
motor composition adapter but does not yet construct a generator composition
service. The existing transient-stability runner constructs a
MultiMachineSystem of classical synchronous machines and supplies a static
mechanical-power mapping to DAESolver. It has no accepted-step callback for
the discrete GGOV1 DELT/previous-FSR handoff, nor a registered controller-state
layout. Calling the sampled adapter from ordinary RK4 derivative stages would
incorrectly repeat a discrete sample at intermediate stages and could make
rejected-step rollback non-deterministic.

Therefore this milestone adds the reversible StudyService registration
contract only. It deliberately does not register GGOV1 into the existing DAE
runner or invent a second solver. A production integration requires an
explicit solver-owned dynamic-state extension and accepted-step/rejected-step
protocol before the Application bootstrap can supply the required composition
solver and per-study adapter factory.

### Accepted-step transaction protocol

`GGOV1AcceptedStepCheckpoint` and `GGOV1StepAttempt` define a detached
commit/reject protocol for the discrete acceleration feedback boundary.
The host begins an attempt from its committed sample index, simulation time
and previous selected FSR. It evaluates continuous solver stages against that
unchanged checkpoint, then calls `accept()` only after the complete step is
accepted. A rejected attempt calls `reject()` and receives the exact original
checkpoint; its candidate FSR and sample index are discarded.

This protocol prevents RK4 intermediate stages or failed Newton iterations
from advancing the discrete GGOV1 DELT/FSR feedback. It is a value contract,
not an integrator hook: the host solver remains responsible for step retries,
adaptive step size, event boundaries, and committing accepted state vectors.
The current DAE/TransientStability solver has no generalized dynamic-state
extension or accepted-step participant API, so this plugin contract is not
yet wired into that solver and does not claim runnable generator DAE support.

### Solver-owned GGOV1 state-vector layout

`GGOV1DynamicStateLayout` provides a frozen contiguous mapping for the six
continuous block-state groups: PID, acceleration filter, temperature
conditioning, load limiter, actuator, and turbine. The host can construct it
from initial block snapshots, pack them into its global vector, unpack
intermediate vectors for derivative evaluation, and pack returned derivatives
back into the same slice order. Every operation validates vector dimensions
and finite values; it does not retain or mutate solver state.

This is the plugin-side layout contract only. The existing `MultiMachineSystem`
accepts classical synchronous machines and currently owns the entire DAE state
vector; `DAESolver` delegates directly to that vector and the static mechanical-
power map. A production extension still needs a Core-owned generalized dynamic
state registry and machine/controller derivative composition, plus the
accepted-step participant semantics described above. No generator states are
currently appended to the production DAE vector by this change.


### GGOV1-to-Core generator identity and mechanical-power handoff

`GGOV1GeneratorBinding` associates one controller component with an existing
Core generator machine ID, bus ID and authoritative terminal ID. The turbine
MW rating and study system MVA base are explicit and independent; generator
MVA is not silently substituted for turbine rating.

`GGOV1MechanicalPowerHandoff` is an immutable, time- and accepted-sample-
stamped value. It converts turbine-base mechanical power to system-base pu
using:

`P_m,system(pu) = P_m,turbine(pu) * turbine_rating(MW) / system_base(MVA)`

The handoff is a value contract only. A future Core integration must verify
that the machine, bus and terminal identities still belong to the active
study snapshot, then consume the mechanical-power value through the canonical
solver input path. This plugin contract does not mutate a Core machine, own
machine identity, replace the DAE mechanical-power map, or register a second
machine. It also does not solve the remaining generalized-state problem:
Core still needs one global state-layout and derivative-composition protocol
that preserves classical-machine behavior and accepted/rejected-step
transaction semantics before this handoff can be wired into production DAE
execution.


### Core dynamic-state component protocol (contract milestone)

The Core dynamics package now defines and exports a structural
`DynamicStateComponent` protocol with immutable `DynamicComponentInputs`
and `DynamicComponentEvaluation` values. It separates non-electrical
controller state from canonical electrical-machine identity and carries
terminal voltages by terminal ID, machine electrical/mechanical powers by
machine ID, derivative contributions, and optional mechanical-power outputs
for canonical machine IDs.

The contract requires referentially transparent evaluation: a component may
not advance time, mutate its state, dispatch events, or commit sampled
feedback while a solver evaluates a derivative. Inputs and returned
power maps are defensively frozen and numerical values are checked for
finiteness. A component's stable ID and positive state dimension are
structural requirements for a future host registration/layout owner.

This milestone intentionally defines and exports the Core boundary only.
`MultiMachineSystem` still registers `ClassicalSynchronousMachine` objects
and owns the active global vector; `DAESolver` still delegates to that
machine system. The new protocol is not yet consumed by either class, does
not append GGOV1 state to a production trajectory, and does not alter the
classical API. A coordinated follow-on must add a global component layout,
derivative assembly, validated machine/component identity binding, and
accepted-step commit/reject participants together. It must also solve the
electrical feedback coupling (AVR terminal-voltage/internal-emf interaction)
before claiming a full generator transient model.

### Core composed dynamic-state layout (contract milestone)

Core now exports `DynamicSystemStateLayout` and
`DynamicStateBlockSlice`. The layout composes the existing ordered
`MultiMachineSystem.state_layout` as the unchanged leading machine segment,
then appends validated `DynamicStateComponent` state blocks in explicit
registration order. Stable identities, positive component dimensions,
contiguous offsets, exact component-state keys, and finite vector dimensions
are checked before packing. Unpacking returns detached read-only state
snapshots, and derivative assembly uses the same frozen offsets.

This is a compatibility-oriented composition value, not a solver registration
or execution path. `MultiMachineSystem` still validates and evaluates its
machine-only vector; `DAESolver` still delegates state validation, algebraic
network solving and machine derivatives to that class. Passing the composed
vector directly to the current DAE solver is therefore unsupported. The new
layout does not change solver APIs, add component callbacks to RK4/Newton
stages, couple AVR output into the network source, or commit GGOV1 sampled
feedback. A future integration must make the network solver, machine
derivatives, component derivatives, event boundaries and accepted/rejected
step participants consume one common augmented-state protocol atomically.

### Core dynamic-component derivative assembly boundary

Core now exports `DynamicComponentBinding`,
`DynamicComponentBatchEvaluation` and
`evaluate_dynamic_components()`. Each registered extension is bound to one
existing machine ID and one authoritative terminal ID. Evaluation verifies
that component identities and state dimensions match the composed global
layout, that the bound machine and terminal exist in the study snapshot, and
that the machine has an electrical-power input. Each component receives its
detached local state and the same immutable study input snapshot.

The returned component derivative blocks are keyed by component ID and
mechanical-power outputs are keyed by canonical machine ID. A component may
only produce mechanical power for its bound machine; multiple controllers
driving one machine are rejected until an explicit engineering aggregation
policy exists. No Core machine is mutated and no static mechanical-power map
is silently overwritten.

This is a pure component-evaluation boundary, not a complete DAE derivative
assembler. The host must still sequence algebraic network solution, electrical
machine output evaluation, component evaluation, mechanical-power handoff,
and machine derivative evaluation consistently. AVR terminal-voltage and
internal-emf feedback is not yet solved by this contract. The current
`DAESolver` still consumes only the existing `MultiMachineSystem` vector
and fixed mechanical-power mapping; neither the new layout nor this evaluator
is wired into production integration. Discrete GGOV1 sample feedback remains
owned by the accepted-step transaction boundary, never by a derivative call.

### Coordinated machine/controller derivative assembly

Core now exports `evaluate_composed_dynamic_derivatives()` and the immutable
`DynamicSystemDerivativeAssembly` result. The component binding explicitly
carries component ID, canonical machine ID, canonical bus ID and authoritative
terminal ID. The coordinated boundary checks that the bus belongs to the
declared machine through the supplied machine-to-bus identity snapshot, and
that the terminal voltage equals the solved voltage for that bus within a
strict numerical tolerance. This prevents terminal IDs from being silently
treated as bus IDs.

The differential-side phase order is explicit:

1. The caller solves the algebraic network for the current machine state and
   intermediate integrator time.
2. The caller derives terminal voltages and machine electrical-power inputs
   from that same network/state snapshot and creates immutable
   `DynamicComponentInputs`.
3. Core packs the unchanged machine-state segment with the component states.
4. Each continuous component is evaluated without mutation against that
   detached state and input snapshot.
5. Component mechanical-power outputs replace the base mechanical-power
   input only for their explicitly bound machine. They are not added to it;
   untouched machines retain the base input.
6. The existing machine derivative callback receives the same bus-voltage
   snapshot, effective mechanical-power mapping, connection-state snapshot
   and simulation time.
7. Core assembles machine and component derivatives into the same frozen
   global state layout.

The boundary rejects incomplete machine maps, inconsistent base-power
snapshots, mismatched machine/bus/terminal identities, inconsistent
terminal-versus-bus voltage snapshots, duplicate dynamic drivers and
non-finite or dimensionally invalid derivatives. The machine callback remains
the existing machine-equation authority; this boundary does not reimplement
swing equations or mutate machine objects.

This is now a reusable differential-side composition function, but it is
still not wired into `DAESolver` or `TransientStabilityRunner`. The current
DAE solver validates a machine-only state vector and calls the existing
machine derivative API directly. The caller must also solve the algebraic
network and construct the matching terminal/electrical-power input snapshot
before invoking this function. AVR excitation/internal-emf feedback and
network re-solution coupling remain unresolved; this contract does not claim
a closed-loop generator model. Sampled GGOV1 acceleration feedback must be
held constant through every integrator stage and committed only by the
accepted-step transaction owner.

### AVR field-voltage handoff to the synchronous-machine model

The existing classical synchronous-machine model now accepts an optional
per-machine `field_voltage` input when calculating internal EMF, current
injection and terminal electrical output. `MultiMachineSystem` exposes the
same optional `field_voltages` mapping on `electrical_outputs()`,
`electrical_powers()` and `derivatives()`. Values are finite,
non-negative pu quantities keyed by canonical machine ID; unknown machine
IDs, booleans, negative values and non-finite values are rejected. Machines
without an explicit entry retain their configured static `Efd` parameter.

This is the machine-side excitation input seam only. It does not yet connect
the three-state `FirstOrderAVR` state to the machine system or to the
network solver. The existing `DAESolver` network callback still receives
only `(state, time)`, so a production AVR must provide one consistent
field-voltage snapshot to both network current-injection assembly and
machine electrical-output/derivative evaluation. Re-solving the algebraic
network when AVR field voltage changes is mandatory; passing the override
only to rotor electrical power would create an inconsistent DAE snapshot.
No such callback or DAE integration is claimed by this increment.

The plugin-side `build_generator_excitation_snapshot()` now provides the
pre-network half of this boundary. It reads the state-owned third output of
each bound `FirstOrderAVR`, validates one-to-one AVR/component/machine
identity, and returns an immutable, time-stamped machine-to-field-voltage
mapping. The same snapshot is intended to be passed to the network's machine
current-injection assembly and to `MultiMachineSystem` electrical-output and
derivative calls. The stage-scoped network adapter described below now passes this
snapshot to a host-supplied network callback without changing the Core DAE
callback. AVR derivatives still need to be evaluated from the resulting
terminal-voltage solution at the same integrator stage time. Therefore the
complete AVR–network closed loop remains an integration task.

### Stage-scoped AVR excitation network adapter

The plugin now exports `GeneratorNetworkStageRequest`,
`GeneratorNetworkStageSolution` and
`solve_generator_network_stage()`. A request combines a detached,
read-only machine-state vector with one immutable
`GeneratorExcitationSnapshot`. The adapter calls the host-supplied network
solver with exactly three stage inputs: machine state, the snapshot's
integrator-stage timestamp, and its immutable machine-ID-to-field-voltage
mapping. The returned solution retains that timestamp, field-voltage map and
AVR component provenance alongside the solved bus voltages.

This prevents a host adapter from accidentally solving the network with one
AVR field-voltage map and later presenting a different map as the source of
the machine-side electrical equations. The host must use the returned
`field_voltages_pu` unchanged when evaluating machine electrical outputs and
derivatives for that same stage, and must derive authoritative terminal
voltages from the same network solution.

This is a plugin-side stage adapter, not a change to the existing
`DAESolver`. It does not append AVR states to the machine-only DAE vector,
construct the AVR state from RK4 intermediate vectors, project bus voltages
onto Core terminal identities, or evaluate AVR derivatives. The current
transient runner still does not invoke this adapter. Full closed-loop
execution therefore remains blocked on a Core-owned augmented-state
protocol and a coordinated DAE/network composition path; legacy
`network_solver(state, time)` callers remain unchanged.

### First-order AVR dynamic-state component

`FirstOrderAVRStateComponent` adapts the existing three-state
`FirstOrderAVR` to Core's `DynamicStateComponent` protocol. It exposes a
stable component identity and state dimension, initializes from the bound
authoritative terminal voltage, and evaluates the AVR derivative from the
same-stage terminal-voltage input. Its field-voltage output is read from the
component state for the pre-network excitation snapshot. The adapter returns
no mechanical-power handoff because an AVR changes excitation, not prime-mover
mechanical power.

Register the adapter in `DynamicSystemStateLayout` and bind it to the
canonical machine, bus and terminal using the shared
`DynamicComponentBinding`. For each integrator stage, unpack the AVR state,
build the excitation snapshot, solve the algebraic network with that snapshot,
construct `DynamicComponentInputs` from the resulting terminal voltages,
then evaluate the composed derivative assembly. This establishes the
component-level phase order without changing `DAESolver` or implying that
the production transient runner already performs these steps.


### Coordinated generator stage evaluation

`evaluate_generator_dynamic_stage()` coordinates one complete continuous
stage without taking ownership of the integrator. It extracts machine and
component states from `DynamicSystemStateLayout`, validates that each AVR
adapter is the exact registered component and matches its canonical machine
and terminal binding, builds the immutable AVR excitation snapshot, and
passes that snapshot to the network solver. The host then projects solved bus
voltages to authoritative terminal IDs and calculates electrical active
power from that same bus/excitation solution. Finally, Core's existing
composed derivative assembly evaluates all registered components and machine
equations and returns the assembled derivative vector.

The supplied machine derivative callback receives the network solution's
exact `field_voltages_pu` map as a required keyword argument; its host adapter
must forward that map to `MultiMachineSystem.derivatives()`. The terminal
voltage projector and electrical-power evaluator remain host-supplied because
the plugin must not invent Core terminal topology or duplicate electrical
equations. The stage result retains excitation provenance, network result,
component inputs and derivative assembly together.

This function is an explicit host composition operation, not an automatic
change to `DAESolver` or `TransientStabilityRunner`. The integrator must call
it independently for every actual stage state/time (including RK4 midpoint
states); the host must pass the returned derivative vector to the integrator
and retain its accepted/rejected-step policy. Discrete controller feedback,
event commits and result persistence remain outside this pure stage call.


### Opt-in augmented DAE integration adapter

`GeneratorAugmentedDAEAdapter` is the explicit integration bridge for hosts
that are ready to run the composed state vector. It accepts the canonical
Core `MultiMachineSystem` and `Integrator`, the frozen
`DynamicSystemStateLayout`, registered extension components and bindings,
and the host's network/terminal-projection callbacks. Construction verifies
that the machine prefix of the composed layout exactly matches the canonical
machine identities and offsets.

The caller creates the initial augmented vector with
`DynamicSystemStateLayout.pack(machine_state, component_states)`. The
adapter's `derivatives(state, time)` is directly compatible with the Core
Integrator callback. Its `step(state, time, dt)` delegates to that same
Integrator, so every RK4 intermediate state/time and every trapezoidal
Newton evaluation gets a fresh generator stage evaluation. After the
integrator accepts a state, the adapter evaluates the final-time algebraic
stage and returns it with the accepted augmented state.

The adapter reuses `MultiMachineSystem.electrical_powers()` and
`MultiMachineSystem.derivatives()`, forwarding the same AVR field-voltage
map through the canonical `field_voltages` keyword. It samples optional
machine connection state at each derivative stage. It does not alter
`DAESolver`, change legacy machine-only study behavior, own Core equipment,
or commit discrete events/controller feedback. A production study host must
explicitly select this augmented adapter and provide the authoritative
terminal projection and network callback; this is not automatic transient
runner wiring.


### Augmented initial-state construction from the study operating point

`GeneratorAugmentedDAEAdapter.initialize_state(machine_state,
operating_point_inputs)` packs the canonical machine state with each
registered component's own `initial_state(inputs)` result. The host must
supply a solved and validated initial operating point: terminal voltages keyed
by Core terminal IDs, electrical active powers keyed by canonical machine
IDs, and the exact configured mechanical-power inputs on the study base.
The adapter checks complete machine-power coverage and exact component/layout
identity before packing. It does not invent controller states or solve an
initial power flow.

This is intentionally separate from the existing
`GeneratorTransientStudyHandler`: that StudyService handler currently
requires the host's `GGOV1SampledExecutionAdapter` and delegates to the
host-owned `solve_generator_transient` capability. The augmented DAE adapter
is not silently substituted into that sampled contract. Production use
requires a host composition/solver implementation that explicitly accepts
the augmented-state derivative/step contract and returns the usual
provenance-bound study result.


### Algebraic network convergence evidence

The stage network callback may return either the legacy bus-voltage mapping
or `GeneratorNetworkSolveResult`. The structured result carries
`GeneratorNetworkConvergenceEvidence`: solver identity, iteration count,
residual norm, declared tolerance and explicit convergence status. The stage
solution retains this evidence, and the accepted augmented step exposes it
through `final_stage.network.convergence`. An explicit non-converged result
fails the stage closed. A legacy mapping remains accepted for compatibility,
but its convergence evidence is `None`; the adapter does not fabricate a
residual or claim convergence when the host solver has not supplied evidence.


The augmented adapter accepts `require_network_convergence_evidence=True`. In strict mode, a legacy voltage-only mapping is rejected because it cannot substantiate algebraic convergence. The default remains `False` for compatibility with existing host callbacks; production transient-study composition should enable strict mode once its network solver reports structured evidence.

### Per-step derivative-stage evidence

Each successful `GeneratorAugmentedDAEAdapter.step()` now returns the immutable
`derivative_stages` tuple on `GeneratorAugmentedStepResult`. It retains the
network/excitation/component/derivative evidence for every derivative callback
actually requested by Core's Integrator during that step, including RK4 trial
stages and trapezoidal Newton/finite-difference evaluations. The separate
`final_stage` is evaluated at the accepted state and end time.

The derivative-stage tuple is diagnostic solver-evaluation evidence, not a
trajectory of accepted states and not a checkpoint stream. It is collected in
a step-local buffer, so the adapter has no shared mutable trace and concurrent
step calls do not overwrite one another. Only the returned accepted state and
its final algebraic stage are eligible for a host's accepted-step checkpoint;
Application StudyService remains the owner of cancellation, terminal result
persistence and lifecycle events. The current archive allowlist does not
reconstruct plugin-specific stage-result dataclasses. For durable evidence,
call `GeneratorAugmentedStepResult.to_archive_payload()` and place the returned
versioned primitive mapping in the Application-owned `StudyResult.value` (or
its result payload). This keeps Core's archive independent of plugin imports
while retaining accepted state, final algebraic evidence, per-stage excitation,
network convergence, terminal/power inputs and derivative contributions.
The payload labels derivative entries as solver evaluations, not accepted
trajectory points; hosts must persist accepted-step checkpoints separately and
must not promote trial evaluations to committed states.


### Explicit StudyService dispatch for augmented DAE execution

The registered generator_transient handler now preserves sampled GGOV1 as its
default execution mode and supports an explicit augmented_dae mode selected
through the immutable StudyRequest configuration. Application composition may
optionally provide generator_augmented_transient_solver with
solve_generator_augmented_transient(...) and
create_generator_augmented_adapter(...). The adapter factory must return the
canonical GeneratorAugmentedDAEAdapter; the host solver receives that adapter,
the original StudyRequest, execution context and StudyCancellationToken.

An explicit augmented_dae request fails closed when the optional host solver
or adapter factory is absent or malformed. It never falls back to sampled
GGOV1. Cancellation is rechecked after adapter construction and immediately
before handing execution to the host. The sampled_ggov1 path and its required
host solver/factory remain unchanged.

This is an execution-selection and typed handoff boundary, not the missing
host execution loop. The host implementation must still check cancellation
between accepted steps, use the augmented adapter's accepted-step observer
only after a successful numerical step, transact sampled GGOV1 discrete
feedback against the committed checkpoint, retain partial-result provenance,
and return a detached result payload compatible with StudyService and its
configured StudyResult persister. The plugin does not implement a second
scheduler, checkpoint store, integrator, event publisher or persistence path.


### Host augmented-study execution contract and integration blocker

A source-level reconciliation of the current branch confirms that the opt-in
`augmented_dae` dispatch is a typed handoff only. The repository currently
contains no concrete Application composition implementation of
`generator_augmented_transient_solver.solve_generator_augmented_transient(...)`
or `create_generator_augmented_adapter(...)`; the shared
`ElectricalDynamicsComposition` protocol declares only the sampled generator
capabilities. Consequently, selecting `execution_mode="augmented_dae"` in a
production StudyRequest currently fails closed unless an external host supplies
both capabilities. This is intentional and must not be represented as a
working end-to-end study path.

The existing ownership boundaries were checked before considering an
implementation:
- `core/application/study.py` owns handler dispatch, cancellation-token
  linearization, terminal StudyResult construction, configured result
  persistence and lifecycle events. It accepts one handler return value or
  exception.partial_result; it is not a numerical step loop.
- `core/solver/dynamics/dae_solver.py` exposes a machine-only one-step
  `DAESolver.step` over its legacy state and derivative API. It does not
  consume the composed state layout, fixed-point terminal-current coupling,
  AVR/GGOV1 state blocks or the plugin augmented adapter.
- `GeneratorAugmentedDAEAdapter.step` advances exactly one numerical interval
  and exposes an accepted-step observer. It deliberately does not own time,
  cancellation, sampled-controller checkpoint state, study result assembly
  or persistence.
- `GGOV1StepAttempt` is a detached accept/reject value object. It does not
  own the committed checkpoint or coordinate it with numerical step acceptance.

A concrete host implementation must therefore be added at the existing
Application composition/solver boundary (not as a plugin-owned scheduler or
second persistence service). Before it can be called complete, that host must:
1. Build the per-study adapter from the immutable StudyRequest and detached
   StudyExecutionContext, resolving the exact machine, equipment, topology
   snapshot/revision, model parameters, operating point, component bindings,
   terminal voltage projection, network solver and current bases from
   Application-owned state.
2. Validate the request's time interval, integration step policy, initial
   composed state and model/controller configuration before the first step.
3. Drive repeated one-step calls, checking StudyCancellationToken at every
   accepted-step boundary; never publish RK/Newton trial evaluations as
   accepted checkpoints.
4. Keep the committed GGOV1 checkpoint in the host's study-local execution
   state. Begin each candidate transaction from that checkpoint, evaluate
   using the prior committed FSR feedback, and call accept only after the
   numerical step and final algebraic stage have succeeded. Rejected steps
   must retain the exact prior checkpoint.
5. Return a detached, versioned primitive payload containing accepted states,
   times, final algebraic/network quality, coupling residuals, controller
   checkpoint history, provenance and an explicit completed/cancelled/failed
   trajectory status. On an exception after an accepted step, attach the
   latest payload as `partial_result` so StudyService can persist it.
6. Leave terminal status, durable archive writes and lifecycle events to the
   existing StudyService and its configured result persister. The host solver
   must not write archives, publish duplicate lifecycle events or create a
   second cancellation/result registry.

Until this host boundary exists and is statically reconciled with the actual
Application composition root and callers, augmented DAE remains an opt-in
extension contract rather than a production-ready generator transient mode.
No new Application service or speculative composition root is introduced by
this documentation correction.


### Canonical Application composition reconciliation

The actual Application root is `core/application/bootstrap.py:create_application`.
It constructs the canonical `StudyService`, configures the durable
`StudyResultArchive`, attaches project lifecycle/revision services, and
registers the existing `transient_stability` study handler. Its dynamic-study
path is currently the legacy `PreparedTransientStability →
MultiMachineSystem → TransientNetworkRuntime → DAESolver →
TransientStabilitySolver → TransientStabilityAnalysis` path.

The root currently attaches only
`application.motor_dynamics_composition = MotorDynamicsComposition(application)`.
That object provides the motor draft domain service and transactional command
handler registration. It does not implement the shared
`ElectricalDynamicsComposition` contract, does not expose
`motor_read_models`, and does not provide generator or load transient solvers
or their per-request adapter factories. It is therefore not a valid substitute
for the `electrical_dynamics_composition` required by the three plugins.

The plugin dependency chain is also explicit: `PluginContext.services` is an
immutable injected mapping; `PluginManager.set_context(s)` stores contexts
supplied by its caller and does not build Application services. The inspected
canonical plugin context/manager code contains no automatic bridge from
`application.motor_dynamics_composition` to
`services["electrical_dynamics_composition"]`. Thus generator, motor and load
plugin initialization fails closed unless an external caller manually injects
a complete shared composition object. The previous augmented DAE handoff
finding is a subset of this larger composition gap.

Required correction is one Application-owned electrical dynamics composition
created at the canonical Application root and passed through the existing
PluginContext service injection point. It must reuse the existing
`StudyService`, extension-handler registry, project snapshots and result
archive; it must not create a second scheduler, command registry, equipment
authority, result store or persistence path. Its required capabilities must
be added only when their concrete host implementations exist. In particular,
the motor simulation projection needs an Application-owned read-model boundary
rather than an implicitly plugin-owned store, and generator/load solver
capabilities must not be advertised as available before implemented.

No speculative composition object or false solver implementation was added
during this reconciliation. The integration is now precisely localized to
Application composition plus the caller that constructs per-plugin
`PluginContext.services`.


#### Concrete startup call-site confirmation

The production startup path in `main.py` confirms the mismatch at both
injection sites. The EngineeringPluginHost activation passes only
`services["motor_dynamics_composition"]`, while
`MotorDynamicsPlugin.initialize` now resolves only
`services["electrical_dynamics_composition"]`. Later, the UI
`PluginContext` is constructed with the same legacy key. The shared
composition key is therefore not merely absent from the Application root; the
current startup caller still supplies the old key at both plugin-context
boundaries. Renaming that key alone is insufficient because the attached
`MotorDynamicsComposition` also lacks the required Application-owned
`motor_read_models` and all generator/load solver capabilities.
