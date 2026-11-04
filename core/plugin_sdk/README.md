# GridForge V2 Plugin SDK

The SDK provides typed contracts and a narrowly scoped engineering-plugin lifecycle host. The engineering host is separate from the UI PluginManager; it does not discover plugins or create Application services, a scheduler, event bus, study framework, or result store.

## Public contracts

- `PluginDescriptor`: declarative identity, plugin version, SDK API version, display name, and immutable capabilities.
- `HostPluginContext`: structural protocol for the host-created context shape consumed by plugins (`application`, `services`, and `metadata`).
- `EngineeringPluginContext`: immutable, UI-independent context constructed by the engineering host.
- `EngineeringPluginHost`: explicit activation/deactivation owner with descriptor/API preflight, duplicate-ID rejection, failed-initialization rollback, reverse-order shutdown, and retained ownership when rollback cleanup fails.
- `GridForgePlugin`: matches the current lifecycle entry points `plugin_id`, `initialize(context)`, and `shutdown()`.
- `validate_plugin_descriptor(...)`: pure validation for descriptor type, expected plugin identity, and supported API versions.
- `SUPPORTED_PLUGIN_API_VERSION`: the SDK API level currently declared as `V2.0`.
- `core.numerics`: shared numerical problem, CPU reference ODE integration, and capability-based backend dispatch.

## Ownership rules

1. Core owns domain truth and headless reusable numerical primitives.
2. Application owns study execution, cancellation, canonical events, transactions, and authoritative result publication.
3. Plugins own domain-specific models, equations, adapters, and result interpretation.
4. UI integrations remain projections and must not be imported by Core or the SDK.
5. Reuse the canonical host context and lifecycle. Do not introduce parallel
   plugin managers, schedulers, event buses, study frameworks, or result stores.
6. Accelerator adapters advertise concrete workload and precision support. CPU
   remains the reference/fallback; GPU/NPU are selected only when an adapter
   declares that the requested workload is supported.

## Compatibility validation

`validate_plugin_descriptor` rejects malformed descriptors, identity mismatches,
empty/invalid supported-version sets, and unsupported API versions without side
effects. A host activation owner must provide its supported versions through
`PluginContext.metadata["host_supported_plugin_api_versions"]` when it needs a
compatibility policy different from the SDK default. The protocol explicitly
includes `metadata` because Motor Dynamics reads this field before activation.

The engineering host validates descriptors and API compatibility before invoking plugin code. Its supported API versions are authoritative and are supplied to the immutable context as host-owned metadata. Motor Dynamics also validates its own descriptor as a defensive local guard. The host tracks successfully activated plugins and separately retains plugins whose failed initialization rollback could not finish. `pending_cleanup_plugin_ids` exposes that state, `deactivate(plugin_id)` retries its shutdown, and `shutdown_all()` retries both active shutdown and pending cleanup. A plugin ID awaiting cleanup cannot be activated again. Activation is explicit: callers construct plugin instances and supply approved services. No automatic discovery or UI manager reuse is provided.

The canonical composition root in main.py explicitly creates EngineeringPluginHost, activates MotorDynamicsPlugin with the existing Application-owned motor_dynamics_composition service, and calls shutdown_all() during startup-failure cleanup and normal teardown. The engineering context and host remain UI-independent. The main composition root separately creates the optional Motor Dynamics dashboard, attaches it to the Study workspace sidebar, and closes it before the headless result publisher during teardown.

Example:

```python
from core.plugin_sdk import (
    SUPPORTED_PLUGIN_API_VERSION,
    PluginDescriptor,
    validate_plugin_descriptor,
)

descriptor = PluginDescriptor(
    plugin_id="gridforge.plugin.example",
    version="2.0.0",
)
validate_plugin_descriptor(
    descriptor,
    supported_api_versions=frozenset({SUPPORTED_PLUGIN_API_VERSION}),
    expected_plugin_id="gridforge.plugin.example",
)
```

## Lifecycle example

```python
from core.plugin_sdk import EngineeringPluginHost
from plugins.motor_dynamics.plugin import MotorDynamicsPlugin

host = EngineeringPluginHost(application)
host.activate(
    MotorDynamicsPlugin(),
    services={
        "motor_dynamics_composition": application.motor_dynamics_composition,
    },
)
# On application shutdown:
host.shutdown_all()
```

The host receives already-created plugin instances and explicit service mappings. Application remains the owner of command composition, StudyService, and ApplicationEventBus. UI composition plugins continue to use the existing UI loader/manager/registry; engineering plugins do not.

## Status and verification boundary

This PR adds the shared contract surface, pure compatibility validation, and
first activation-boundary adoption in Motor Dynamics. It does not introduce a
generic engineering-plugin discovery/loader subsystem. Source changes were
reviewed against the existing UI plugin contract and Application-owned study
and event services. No tests, CI, syntax checks, imports, numerical comparisons,
or hardware validation were run.


## Packaging and startup verification

The installed distribution includes the `plugins*` package namespace in addition to `core*` and `ui*`. The startup verification inventory includes engineering plugin source files and passes the composition root's host/dashboard lifecycle handles into teardown. The current PR's last observed workflow run failed during `import main` because the installed package omitted the `plugins` namespace; the packaging correction is committed, but its outcome remains pending the next automatic workflow run.


## Second engineering-plugin consumer — DWG Export (2026-11-03)

The optional `plugins.dwg_export` package is a second, materially different consumer of the existing `GridForgePlugin` / `PluginDescriptor` / `EngineeringPluginHost` lifecycle. It is a headless integration plugin, not a UI composition plugin. It receives an explicit `dwg_export_configuration` service containing an ODA executable path and a reviewed `SymbolRegistry`; activation fails closed if that configuration or executable is unavailable.

This adoption demonstrates that the existing lifecycle contract can host a non-study CAD integration without a second manager, loader, registry, event bus, or Application service owner. The plugin is not automatically discovered or activated. The current `main.py` composition root continues to activate Motor Dynamics only; DWG activation remains opt-in until an approved ODA installation and reviewed catalogue are configured. The DWG plugin's focused lifecycle tests have been added but were not run during this source-only change. The SDK API remains provisional pending test/CI and lifecycle verification.
