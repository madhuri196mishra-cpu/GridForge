# GGOV1 PID sub-block

`GGOV1PIDGovernor` is a local continuous-time implementation of the PSS/E-derived
GGOV1 frequency-control PID sub-block and supervisory power reset path. It is
not a complete GGOV1 governor and must not be represented as a validated
plant-level gas-turbine model.

## Implemented sub-block

Four states are exposed to the host integrator:

1. Electrical-power measurement lag.
2. Derivative-filter input state.
3. Governor integral contribution.
4. Supervisory power-controller reset contribution.

The error summing junction follows the OpenIPSL PSS/E-derived
`PIDGovernor.mo` topology:

`e_raw = P_REF + reset - SPEED - R * selected_feedback`

The selected feedback is the existing `GGOV1Parameters.droop_feedback_signal()`
contract: `Rselect=1` electrical power, `0` none (isochronous),
`-1` valve stroke, and `-2` governor output. Electrical-power feedback uses
the filtered measurement state. The speed dead-zone is applied before the
configured minimum/maximum error limiter.

The PID output is the sum of proportional, integral and filtered derivative
terms. The derivative path uses the first-order filtered derivative transfer
`Kdgov*s/(1 + Tdgov*s)`. The supervisory reset path integrates
`Kimw * (PMW_SET - filtered_PELEC)` and blocks outward integration at
`±1.1*R`, matching the referenced bounded integrator topology.

Initialization follows the OpenIPSL reference structure: filtered electrical
power starts at its operating-point value; derivative filter state starts at
the initialized error (zero derivative output); governor integral contribution
starts at `(Pm0 + Dm)/Kturb + Wfnl`; supervisory reset starts at zero.
By default `Pm0` is taken from the supplied electrical power, as in the
reference implementation. A caller may supply an explicit mechanical-power
value when it has already reconciled the study base and operating point.

## Deliberate boundary

This block does **not** implement the GGOV1 actuator, fuel/turbine dynamics,
transport delay, temperature/load limiter, acceleration limiter, low-value
selector, or tracking/bumpless transfer among competing controllers. The
governor integrator is not given a fabricated anti-windup or outer-limiter
tracking path. It does not own a clock, persist state, mutate Core, or connect
itself to the transient DAE. Its inputs must already be expressed on the
correct model base; turbine MW rating versus generator MVA conversion remains
the caller's explicit responsibility.

The derivative gain may be zero with a zero derivative time constant; a
nonzero derivative gain requires a positive filter time constant. A zero
electrical-power time constant is treated as an algebraic measurement bypass.

## References

- OpenIPSL PSS/E-derived source:
  https://github.com/OpenIPSL/OpenIPSL/blob/master/OpenIPSL/Electrical/Controls/PSSE/TG/BaseClasses/GGOV1/PIDGovernor.mo
- OpenIPSL selector source:
  https://github.com/OpenIPSL/OpenIPSL/blob/master/OpenIPSL/Electrical/Controls/PSSE/TG/BaseClasses/GGOV1/R_select.mo
- OpenIPSL GGOV1 component documentation:
  https://doc.openipsl.org/files/doc-200/OpenIPSL_Electrical_Controls_PSSE_TG_BaseClasses_GGOV1.html
- NERC, *Application Guide for Modeling Turbine-Governor and Active Power-Frequency Controls in Interconnection-Wide Stability Studies* (2019), Appendix C:
  https://www.nerc.com/globalassets/who-we-are/standing-committees/rstc/ppmvtf/reliability_guideline-application_guide_for_turbine-governor_modeling-2019-05-07-CLEAN.pdf

OpenIPSL is used here as a transparent implementation reference, not as a
claim of equivalence to every vendor's GGOV1 variant. Plant/OEM data and
disturbance-based validation are still required.


## Low-value selector tracking hook

`GGOV1LowValueSelector` is a separate stateless block. It selects the minimum
of the active governor, acceleration and combined load/temperature fuel
requests. It returns a selected-minus-requested tracking error for every
active source. This allows each controller's own integrator to track the
active fuel request while it is not selected.

The PID sub-block accepts `selected_fuel_command_pu` and
`tracking_time_constant_s` together in `derivatives()`. The governor
integral derivative receives the tracking correction
`(selected_fuel_command - raw_PID_output) / Ttrack`. Ttrack is an explicit
caller-supplied value: no universal plant-independent tracking constant is
assumed. If both values are omitted, the standalone PID behavior is unchanged.
This hook does not add tracking to controllers that have not yet been
implemented, and does not claim the full GGOV1 limiter network is complete.


## Acceleration, load and temperature sub-block boundaries

The acceleration controller follows the PSS/E/OpenIPSL discrete block:
the `Ta` derivative filter estimates `d(SPEED)/dt`, then
`FSRA = FSR + Ka * DELT * (ASET - d(SPEED)/dt)`. It has one filtered-speed
state, not an FSRA integrator. The caller supplies the currently selected
`FSR` command and the model sample interval `DELT`; this is the feedback
tracking path. `Ta` must be positive.

`GGOV1LoadLimiter` implements the PSS/E-derived OpenIPSL `LoadLimiter.mo`
path. It forms `TREF = Wfnl + LDREF / Kturb`, computes
`eT = TREF - TEXM`, integrates `Kiload * eT`, and produces
`FSRT = min(1.0, Kpload * eT + integral)`. The integral operating-point
initialization is `(PELEC + Dm) / Kturb + Wfnl`. The LoadLimiter itself
applies only the upper 1.0 pu clamp; the common `[Vmin, Vmax]` clamp belongs
after the three-way low-value selector.

`GGOV1TurbineFuelPath.temperature_proxy_input()` computes net fuel flow
multiplied by the PSS/E `Dm_select(speed)` factor. The separate
`GGOV1TemperatureMeasurement` block cascades the `(1+s*Tsa)/(1+s*Tsb)`
temperature-detection lead/lag with `1/(1+s*Tfload)` and produces `TEXM`.
This is a calculated model proxy, not a physical thermocouple model or an
OEM firing-temperature map.

The three requests `FSRN`, `FSRA`, and `FSRT` feed
`GGOV1ControlCoordinator`. It applies the minimum selector followed by the
common `[Vmin, Vmax]` clamp and returns an immutable decision. Pass its
`actuator_command_pu` to the actuator and acceleration limiter, and pass the
same value as `selected_fuel_command_pu` to PID/load-limiter derivative calls
when their explicit tracking inputs are enabled. The decision also exposes
final-command-minus-request errors for each active controller. The
coordinator owns no dynamic state, clock or integration schedule.

`calculate_ggov1_operating_point()` derives a common initial FSR and valve
target, Flag-dependent fuel flow, temperature-proxy input, Rselect-consistent
PID power reference, initial governor-output feedback, and the initial
PID/load-limiter integral targets. It reports two residuals: integral
alignment (mechanical versus electrical power initialization) and the
temperature/load limiter's initial output versus the common FSR.
Pass `load_limiter_enabled=False` when that path is disabled;
`require_balanced()` checks both residuals when it is enabled. The helper
rejects an initial FSR outside `[Vmin, Vmax]`, and above 1.0 pu when enabled.
It does not replace each block's `initial_state()` or own solver state.

The acceleration limiter is a sampled feedback path. Use the previous
selected FSR and host-supplied positive `DELT` to calculate the current
`FSRA`; then select and clamp the three requests. Feed the final command to
the actuator and back as the next sample's FSR input. The host owns sample
ordering and time; this must not be wired as an instantaneous algebraic loop. `Rup` and
`Rdown` remain unimplemented source parameters in the OpenIPSL reference
path and are not interchangeable with actuator `Ropen` and `Rclose`.

These are reference-derived sub-blocks, not a complete integrated GGOV1
plant model. Vendor-specific thermal maps, ambient compensation, host scheduler/DELT ordering, delay execution, DAE integration and plant
disturbance validation remain open.


## Stateless sampled execution composition

`GGOV1SampledExecutionAdapter` evaluates one complete local block handoff
from an immutable `GGOV1SampleStates` snapshot and
`GGOV1SampleInputs`. It reads actual valve stroke from the solver-owned
actuator state, computes the fuel-derived temperature proxy and TEXM,
calculates PID and load/temperature requests, and calculates FSRA using the
previous sample's selected FSR plus the caller's positive DELT. It then
performs the low-value selection and common valve clamp. The final bounded
command is passed to PID and load-limiter tracking derivatives and to the
actuator derivative. Turbine and temperature derivatives are returned in the
same immutable derivative bundle.

The adapter is intentionally stateless. It does not advance DELT, mutate
state vectors, integrate derivatives, own a DAE, or persist history. The host
must apply returned derivatives with its chosen solver and supply the next
state snapshot. It must also supply the explicit governor-output feedback
signal for Rselect=-2 and delayed fuel flow when Teng is positive. This
makes those dependencies visible at the composition boundary rather than
creating a private feedback loop or delay queue.

This adapter establishes a single-sample composition contract; it is not a
complete Application execution integration or a plant-validated GGOV1
simulation. The canonical Application composition root and transient solver
must still decide when to call it, how to reconcile algebraic feedback, how
to integrate all states, and how to handle solver rollback/rejected steps.

## Application StudyService lifecycle boundary

The plugin registers `generator_transient` with the canonical Application
StudyService and removes that exact handler on shutdown. StudyService remains
the sole scheduler, cancellation authority, provenance validator, result
persister and event publisher. The plugin-side handler delegates to a
host-provided solver capability and requests a fresh sampled GGOV1 adapter
for each study.

The Application composition service must provide
`generator_transient_solver.solve_generator_transient(...)` and
`create_generator_sampled_adapter(request=..., execution_context=...)`.
The solver owns numerical integration, accepted/rejected-step handling,
rollback and partial-result retention. These capabilities are explicit
activation requirements; this change does not invent a solver or claim DAE
integration is complete. The actual Application composition root must still
supply and wire these dependencies before the study can run.
