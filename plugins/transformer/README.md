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
and a deterministic instrumentation catalogue. Channel definitions validate
canonical identifiers, enum types, optional winding/sensor/source metadata,
positive integer sample periods, and finite numeric engineering limits.
Samples require timezone-aware timestamps, typed quality, finite numeric or
boolean values, non-empty provenance and units, and non-negative integer
sequence numbers. Snapshots enforce equipment scope and typed, non-duplicate
observations. This is the first implementation slice, not a claim of complete
live SCADA/IEC 61850/IEC 60870-5-104/Modbus integration, thermal ageing
validation, protection integration, or GPU/NPU solver acceleration. Those
require explicit adapter and Application-level integration work.


## Application measurement binding boundary

`measurement_binding.py` adds an explicit, fail-closed binding contract from
a transformer catalogue point to an already provisioned canonical Core
`MeasurementChannel`. The resolver validates point/equipment identity, uniqueness, signal type,
physical instrument family, canonical source-terminal identity, non-empty
unit equality, and phase. It never creates a Core channel, changes its source,
publishes a sample, or bypasses the Application's signed producer/clock checks.

Direct electrical binding supports scalar voltage from a PT/CVT and
current from a CT through canonical terminal-backed channels. A separate
winding-temperature path supports an installed analog physical source
interface (for example, a winding RTD or fiber-optic temperature output).
That path requires the active project instrumentation collection and checks
the source/interface identity, exact installation, transformer identity,
winding identity, quantity, unit and phase. It does not treat a thermal
estimate as a physical sensor. Frequency, phase angle, derived power/energy,
power factor, oil, DGA, insulation, bushing, mechanical, OLTC diagnostic,
and protection-indication points still require dedicated producer models or
Application adapters. A Core signal enum alone is not proof that a physical
source can produce a quantity.


## Snapshot validation

`validate_transformer_snapshot(snapshot, catalogue)` checks every observation
against its declared transformer channel before the caller passes it to an
Application-owned ingestion path. It verifies equipment and point identity,
unit equality, sample chronology, counter/digital/analog value shape, and
configured numeric limits. It does not write, publish, persist, or authorize
telemetry; those remain Application responsibilities.

## Project-owned physical instrumentation records

The Core source/interface/installation value contracts are now grouped by
`ProjectInstrumentationCollection`, and the canonical `.gridforge` project
payload is schema 6. Schema 5 projects migrate deterministically to an empty
instrumentation collection; existing CT/PT/CVT electrical-terminal channel
definitions are unchanged. Project save/load serializes and reconstructs the
collection, and Application activation installs it as project-scoped state
with rollback alongside the other project authorities.

Each installation is checked at persistence and candidate-activation
boundaries for project identity, source/interface existence, monitored
equipment existence, and monitored equipment type. Instrumentation remains
separate from `Network` membership and electrical `Terminal` connectivity.
No MeasurementChannel is provisioned by this collection, and it does not
create producer identities or accept telemetry samples.

**Winding-point boundary:** Core now exposes immutable `TransformerWinding` records with stable winding ID, sequence, role, optional rated voltage and MVA, connection, phase roles, neutral and terminal metadata. The canonical Transformer DTO configuration schema is version 2; schema-1 DTOs remain readable and legacy Transformers expose a compatibility HV/LV winding view. Explicit winding collections must include unique HV and LV records, and their HV/LV connection, phase, neutral and terminal declarations must exactly agree with the established Core solver configuration. Additional tertiary and higher-order records can be commissioned for physical instrumentation by exact Core winding ID. These records do not add electrical terminals, network edges, or a multi-terminal solver model. The canonical Create/Update Transformer commands carry typed winding collections. Updates replace the complete collection atomically; the Application transaction journal restores the exact prior explicit collection on rollback, and the Network topology revision is invalidated when the winding inventory changes. Empty replacement clears explicit records and returns the model to its legacy HV/LV compatibility view. HV/LV record metadata remains consistent with the existing two-terminal solver configuration; tertiary and higher-order records do not create electrical terminals or solver ports.


**Canonical telemetry catalogue entry point:** `build_transformer_channel_catalogue_for_core(transformer, profile, ...)` validates that the plugin profile equipment ID and ordered winding IDs exactly match the authoritative Core `Transformer.windings` collection before generating channel definitions. This prevents a plugin profile from inventing or silently renaming winding points. The lower-level `build_transformer_channel_catalogue(equipment_id, winding_ids, ...)` remains available for explicit catalogue construction, but does not itself prove Core identity.


## Physical source-interface projection

`build_transformer_measurement_source_interfaces(catalogue, channel_ids)`
projects a caller-selected set of physical sensor channel definitions into
Core `MeasurementSourceInterface` value objects. The caller must group
selected points by the actual instrument being described; the helper does
not invent a physical source ID, assert that a device is installed, or add
records to `ProjectInstrumentationCollection`. It rejects missing points,
points without declared sensor types, and estimated/derived quantities so a
computed value cannot be misrepresented as a physical instrument output.
The returned interfaces can be included in the existing Core
`PhysicalMeasurementSource` and project-owned installation workflow.

## Application provisioning from physical source interfaces

The existing Application `MeasurementChannelService.activate()` path can
reconstruct a logical channel from a project-owned physical source interface
without fabricating an electrical `EndpointReference` or Core `Terminal`.
A persisted measurement definition identifies the canonical
`source_reference` mapping (`kind`, `source_family`, `source_id`,
`interface_id`) and its exact `source_installation_id`. The candidate
`ProjectInstrumentationCollection` is validated against the candidate
Network before definitions are reconstructed; the source and interface are
then resolved only from that candidate collection.

Physical-source channel definitions require an explicit `producer_id`,
trusted `timestamp_domain`, and positive `stale_after` duration. The
interface's declared quantity, unit, phase and signal kind govern the Core
channel classification and unit. Temperature interfaces must be analog;
digital outputs cannot claim continuous analog quantities, while counter
and diagnostic outputs remain `CUSTOM` until dedicated Core signal
classifications exist. A declared `signal_type`, if present, must agree.
One installation may provision at most one logical MeasurementChannel in an
activation. Existing producer attestation, project activation generation,
trusted clock, monotonic sequence and Application sample ingress remain
mandatory; source provisioning does not grant a producer permission to
publish.

Electrical CT/PT/CVT channels continue to use their canonical terminal-backed
provisioning path. Physical sensor channels use the source-interface reference
and installation identity only; the two reference domains are mutually
exclusive. This is logical channel provisioning, not proof that hardware is
connected, calibrated, commissioned, or currently producing valid samples.

## Physical interface engineering-range enforcement

A physical source interface's optional `minimum` and `maximum` bounds are
carried into the immutable Core `MeasurementChannel` configuration when the
Application provisions that channel. The bounds are not accepted as
producer-supplied sample metadata and cannot be changed after project channel
activation.

At trusted `MeasurementChannelService.publish_sample()` ingress, the
existing producer attestation is verified first; the sample is then checked
against the channel's declared physical-interface bounds before any channel
state is updated or a sample-published handler is invoked. Values outside
either configured bound are rejected without changing the last accepted
sample. Complex values are rejected when scalar bounds are configured because
they cannot be ordered against a scalar engineering interval. Interfaces with
no declared bounds retain the existing finite-scalar/complex validation.

The limits are interpreted in the physical interface's declared unit; this
does not apply additional conversion or calibration. Instrument scaling,
ratio correction, calibration uncertainty and alarm thresholds remain separate engineering
responsibilities; source diagnostic codes are carried on the canonical
MeasurementChannel sample state as described below.

## Transformer quality and diagnostics on the canonical MeasurementChannel

Transformer telemetry quality is not retained only in plugin snapshots.
`to_core_measurement_quality()` maps the transformer source quality into
Core's canonical `MeasurementQuality`:
- GOOD → GOOD
- UNCERTAIN → SUSPECT
- BAD → INVALID
- NOT_CONNECTED → NOT_CONNECTED (also unavailable)
- STALE → STALE
- SUBSTITUTED → SUBSTITUTED (suspect for consumer validity)
- OUT_OF_RANGE → OUT_OF_RANGE (invalid for consumer validity)

Each `TelemetrySample` can carry canonical `diagnostic_codes` such as
sensor/device diagnostic identifiers, DGA condition flags, partial-discharge
alarms, bushing-monitor flags, or OLTC condition flags. The producer includes
the same codes in the signed Application `MeasurementSampleAttestation`.
After signature verification, `MeasurementChannelService.publish_sample()`
copies those codes and the Core quality/availability state into the
authoritative `MeasurementChannel` sample state. They are therefore visible
to all consumers of that same channel, alongside its value, timestamp and
sequence. Diagnostic codes are immutable sample metadata, are updated only
through the existing Application service capability, and are covered by the
producer attestation; the plugin cannot write them directly.

The channel's existing `validity()` contract maps NOT_CONNECTED to
UNAVAILABLE, STALE to STALE, OUT_OF_RANGE/INVALID to INVALID_QUALITY, and
SUBSTITUTED/SUSPECT to SUSPECT_QUALITY. This exposes both the source's
specific quality and the consumer-facing validity result without creating a
second quality or measurement-channel authority.


## Attested transformer sensor snapshot handoff

`publish_transformer_sensor_snapshot(snapshot, catalogue, attestations, publish_sample=...)`+
is the plugin-side adapter from an immutable transformer telemetry snapshot
to the existing Application `MeasurementChannelService.publish_sample()`
ingress. It validates the complete snapshot against the catalogue, requires
exactly one attestation per sample, and requires the attested channel ID,
sequence, Core quality, availability, diagnostics, and UTC Unix timestamp to
match the telemetry sample before forwarding it.

The caller must pass the canonical Application publication callable and
attestations already produced by the deployment's trusted producer adapter.
The plugin does not create signatures, producer identities, project IDs,
activation generations, clock domains, or trusted timestamps. Application
still verifies the signature and active project/generation, checks the trusted
clock and monotonic sequence, validates source bounds, and owns the only
authoritative channel update path. The helper rejects estimated/derived points
and points without a declared physical sensor type.

This handoff is a software integration boundary, not a device driver or
commissioning workflow. RTD/fiber-optic protocol acquisition, device
certificate provisioning, calibration evidence, wiring inspection, and
site acceptance remain deployment responsibilities.
