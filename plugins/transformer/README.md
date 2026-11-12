# Transformer Dynamics Plugin

The transformer plugin extends the authoritative Core Transformer with
transformer-specific engineering capabilities. It does not create a second
equipment identity, topology, measurement authority, protection engine, or
simulation orchestrator.

## Supported transformer families

The profile taxonomy covers power, distribution, autotransformer, current and
potential instrument transformers, isolation, grounding, zigzag,
phase-shifting, furnace, rectifier, converter, traction, high-frequency,
test, and special-purpose transformers. Winding identifiers are explicit and
support single-, two-, three-, and multi-winding capability configurations.

Insulation systems include mineral oil, natural ester, synthetic ester,
silicone fluid, dry air, cast resin, and gas insulated construction. Cooling
metadata includes AN/AF, ONAN/ONAF, OFAF/OFWF, ODAF/ODWF, KNAN/KNAF, and
descriptive natural/forced methods.

The family profile is descriptive capability metadata. It does not make the
current two-terminal Core Transformer represent a multi-terminal network
device. Any multi-terminal electrical representation requires a separate,
explicit Core/Application integration design.

## Instrumentation and channel catalogue

Use `build_transformer_channel_catalogue(equipment_id, winding_ids, ...)`
to generate deterministic channel definitions. The catalogue includes:

- Per-winding phase A/B/C voltage, current, and phase-angle channels.
- Per-winding active/reactive/apparent power and power factor.
- Winding, oil, ambient, core, and estimated hot-spot temperature.
- Frequency and active/reactive energy.
- Oil level, pressure, flow, moisture, dielectric strength, acidity,
  inhibitor, furan, and breather moisture.
- Dissolved hydrogen, methane, ethane, ethylene, acetylene, carbon monoxide,
  carbon dioxide, oxygen, and nitrogen.
- Partial discharge, insulation power factor, capacitance, bushing leakage,
  and insulation/winding resistance diagnostics.
- Neutral, ground, and core-ground current.
- OLTC position, motor current, operation count, transition time, and
  dynamic contact resistance.
- Fan/pump state and runtime, cooling stage, and gas pressure.
- Buchholz, pressure relief, sudden pressure, differential, thermal, trip,
  and breaker status indications.

Optional groups allow deployments to omit DGA, OLTC, partial-discharge, or
bushing-monitoring channels when those sensors are not installed. The
catalogue describes available points; it does not claim a sensor exists.

## Telemetry contract

- `ChannelDefinition` carries stable channel/equipment identity, quantity,
  engineering unit, subsystem, phase/winding identity, sensor type, source
  tag, sampling interval, and optional engineering range.
- `TelemetrySample` carries a finite value, timezone-aware timestamp,
  explicit quality, source provenance, sequence, and unit.
- `TelemetrySnapshot` is an immutable equipment-scoped batch and rejects
  cross-equipment samples and duplicate observations.
- Estimated quantities remain explicitly distinguishable from analog,
  digital, and counter signals.

The plugin defines metadata and typed envelopes only. It does not acquire,
authenticate, persist, aggregate, alarm on, or dispatch telemetry. Those
responsibilities remain with Application measurement services and deployment
connectors. The current Application MeasurementChannelService authoring path
is explicitly CT-secondary-specific; do not route transformer catalogue
points through that CT-only path or invent a parallel plugin-owned
measurement registry. A generic Application measurement-channel lifecycle
and trusted producer adapter are required before live transformer telemetry
can be commissioned.

## GPU/NPU and dynamics boundary

CPU remains the deterministic reference path. Future GPU/NPU acceleration
must be optional, capability-detected, numerically cross-checked against CPU,
and must not change engineering results or make the plugin depend on a
specific accelerator vendor. NPU use is suitable for supported inference or
diagnostic workloads only; it is not assumed to support arbitrary numerical
solvers.

Generator Dynamics, Motor Dynamics, Load Dynamics, and Transformer Dynamics
must exchange typed engineering/model interfaces while continuing to use
the same Core equipment identities and Application-owned lifecycle,
measurement, command, and simulation orchestration.

## Current implementation status

This branch adds family/construction profiles, typed telemetry contracts,
and a deterministic instrumentation catalogue. It is the first implementation
slice, not a claim of complete live SCADA/IEC 61850/IEC 60870-5-104/Modbus
integration, thermal ageing validation, protection integration, or
GPU/NPU solver acceleration. Those require explicit adapter and
Application-level integration work.


## Application measurement binding boundary

`measurement_binding.py` adds an explicit, fail-closed binding contract from
a transformer catalogue point to an already provisioned canonical Core
`MeasurementChannel`. The resolver validates point/equipment identity, uniqueness, signal type,
physical instrument family, canonical source-terminal identity, non-empty
unit equality, and phase. It never creates a Core channel, changes its source,
publishes a sample, or bypasses the Application's signed producer/clock checks.

Only direct scalar voltage from a PT/CVT and current from a CT are currently
bindable. Frequency, phase angle, derived power/energy, power factor, thermal,
oil, DGA, insulation, bushing, mechanical, OLTC diagnostic, and
protection-indication points require dedicated physical source models and/or
Application producer adapters. They are intentionally rejected by this
direct-binding resolver. A Core signal enum by itself is not proof that the
physical source can produce that quantity.
An Application integration must additionally prove that the selected
instrument channel is actually associated with the stated transformer in
the active project topology; this plugin resolver does not claim to prove
that relationship.
