# GridForge V2 — Shared Plugin SDK Architecture Amendment

**Status:** Reuse/ownership principles frozen as an amendment; the minimal SDK implementation in PR #678 is provisional and its concrete API remains unfrozen pending reference-plugin reconciliation.
**Authority:** Subordinate to the GridForge V2 Core/Application/UI boundaries and the existing Plugin Platform contract.
**Scope:** Reusable infrastructure for future domain, study, integration, and UI plugins.
**Change control:** Reconcile this amendment against the current repository before implementation. Do not create a parallel plugin framework or duplicate an existing authoritative service.

## 1. Decision

GridForge plugins MUST reuse shared platform and Application infrastructure for common behavior. A plugin MUST NOT reimplement lifecycle orchestration, registry mechanics, study scheduling, cancellation plumbing, canonical event delivery, common result publication, configuration mechanics, capability discovery, diagnostics, or UI projection lifecycle when an authoritative shared contract/service already exists.

Plugins own their domain-specific behavior: domain models where permitted, engineering equations/algorithms, input interpretation, result schemas, specialized integrations, and plugin-specific presentation.

This amendment is intended to reduce duplication and complexity without introducing a large universal base class or a second Application layer.

## 2. Non-negotiable boundaries

- **Core** remains authoritative, headless, and independent of plugins, Qt, QGraphics, SLD, and UI controllers.
- **Application** owns commands, controlled mutation, transactions/history, study orchestration, cancellation/status, canonical events, and authoritative read/result publication contracts.
- **Plugin Platform / SDK** provides stable public extension contracts and lifecycle/registration orchestration. It composes existing Application services; it does not clone them.
- **UI plugins** are presentation and interaction adapters. They cannot mutate Core directly or make graphics the source of engineering truth.
- **Domain plugins** call public Core/Application contracts and cannot access private internals or bypass validation.
- Qt access remains behind `ui.core.qt`; no Qt or graphics objects cross into Core.
- All user-originated mutation follows the established path: UI → Controller → Command → Application → Core → Event → Projection → UI.

## 3. Shared SDK capability areas

The SDK should expose small, typed, documented contracts for these capability areas, reusing existing owners wherever present:

1. **Identity and descriptors** — stable plugin ID, version/API compatibility, plugin kind, declared capabilities, dependencies, and metadata.
2. **Lifecycle** — Discover → Validate → Load → Resolve Dependencies → Register → Activate → Use → Deactivate → Unload. Lifecycle state has one owner; registration and cleanup are deterministic and idempotent where possible.
3. **Registration** — explicit registries with a single owner, deterministic lookup, conflict detection, and explicit unregister/rollback behavior.
4. **Study integration** — an adapter contract that conforms to the existing `StudyService` callable boundary (`StudyRequest`, `StudyExecutionContext`, `StudyCancellationToken`) and delegates scheduling, cancellation, status, error classification, event emission, and result registration to that canonical Application service. Do not introduce a second study scheduler or a parallel handler lifecycle.
5. **Events** — use `ApplicationEventBus` and its existing `subscribe`/`unsubscribe` contract, with lifecycle-bound cleanup. Plugins must not create parallel event buses. UI/plugin-specific notification mechanisms may adapt canonical events for presentation but must not become an engineering-truth event source.
6. **Results and read models** — use the existing `StudyResult` and `StudyService` result-registration contract as the canonical study outcome. Plugin-specific result/read models may project a successfully stored result, preserving project/study/generation/revision identity. Failed or cancelled studies must not publish successful results. Do not create a competing general-purpose result store.
7. **Configuration** — typed schemas, defaults, validation, versioning/migration hooks, and explicit persistence ownership.
8. **Compute capabilities** — a common backend interface and capability report for CPU/GPU/NPU or other accelerators. CPU is the required portable baseline when the algorithm supports it; accelerators are optional and capability-gated, with deterministic fallback/reporting. Do not advertise a backend merely because a device or inference provider exists. NPU support requires a suitable implementation for the actual computation and validation.
9. **Diagnostics** — structured error codes, actionable details, backend selection/fallback explanations, and lifecycle failure reporting.
10. **UI projection contracts** — optional, read-only view-model/projection hooks that refresh from canonical successful events and read models. UI construction remains separate from domain computation.

These are capability areas, not a mandate to create one file or class per bullet. First reuse existing authoritative services and contracts; extract a shared abstraction only where a real repeated requirement is confirmed.

## 4. Plugin kinds and existing plugin subsystems

GridForge already distinguishes the broader `plugins/` extension namespace from `ui/plugins/`, which owns UI composition. The current UI plugin architecture has an explicit `Plugin` contract and separates `PluginLoader`, `PluginRegistry`, `PluginManager`, `PluginStateStore`, and `PluginContext` responsibilities. This amendment does **not** replace that architecture or collapse UI lifecycle and domain/study execution into one manager.

Use a small set of capability contracts rather than one oversized base class:

- **Domain/study capability:** supplies domain algorithms and/or a callable adapted to the canonical StudyService contract.
- **Integration capability:** adapts an external format, device, or service through approved Application contracts.
- **UI/presentation capability:** contributes views, inspectors, dashboards, tools, or actions through the existing `ui/plugins/` composition contracts.
- **Equipment/model capability:** contributes only through established Core model contracts; it cannot duplicate Core topology or validation.

A plugin package may provide more than one capability, but each capability must have a clear owner and dependency direction. Reuse the existing UI plugin manager for UI composition; do not route headless engineering calculations through UI lifecycle machinery. Do not introduce another manager/registry for a capability already owned by an existing subsystem.

## 5. Dependency direction and ownership

```text
Core domain contracts ← Application services (commands, studies, events, results)
                                ↑
                  domain/study plugin adapters

Existing UI composition: PluginManager → PluginRegistry/PluginStateStore
                                      → PluginContext → UI plugin

UI plugin/projection → public Application commands, read models, and events
```

The SDK is a **thin public contract/adaptation layer**, not a new architectural tier that owns a second copy of Application services. Its runtime location must be decided only after auditing existing ownership and import dependencies. Core must never depend on the SDK or concrete plugins. UI presentation dependencies must not leak into headless study/domain code. The existing UI lifecycle remains distinct from the Application StudyService lifecycle.

## 6. New-plugin implementation rule

A new plugin should contain only its descriptor/registration contribution, domain-specific inputs and validation, domain calculation or integration, typed result mapping, and genuinely plugin-specific UI. Shared lifecycle, dispatch, event cleanup, cancellation, status, diagnostics, and publication are supplied by existing services/SDK contracts.

Illustrative shape only (not a prescribed runtime API):

```python
class ExampleStudyHandler:
    study_type = "example_study"

    def execute(self, request, context):
        inputs = self.validate_domain_inputs(request)
        result = self.run_domain_calculation(inputs, context)
        return self.build_typed_result(result)
```

The actual method names and signatures must match the audited canonical StudyService contract; do not copy this illustrative snippet as production code without reconciliation.

## 7. Complexity and reuse rules

- Prefer composition and small protocols over deep inheritance or a universal base class.
- Do not duplicate a service merely to make a plugin self-contained.
- Do not create a second registry, lifecycle manager, study scheduler, event bus, result store, or configuration store where an authoritative owner already exists.
- Avoid speculative abstractions: promote shared code after confirming at least one real cross-plugin reuse case and a stable contract.
- Keep plugin dependencies explicit, versioned, and validated before activation.
- Fail registration on conflicting canonical identifiers or incompatible contracts; never silently choose based on import order.
- Cleanup must be scoped to resources actually registered by that plugin and must not unregister a replacement owned by another component.
- Preserve source-level and runtime verification honesty: document unverified integration, numerical correctness, and hardware capability explicitly.

## 8. Migration strategy

1. Audit current plugin and Application implementations and map repeated responsibilities to their authoritative owners.
2. Reconcile contracts with `core/application/study.py`, `core/application/event_bus.py`, `core/application/events.py`, the existing `ui/plugins/` lifecycle contract, plugin contexts/registries, read models, and projection infrastructure.
3. Maintain a reuse matrix: responsibility, current owner/API, proven duplicate call sites, gap, proposed adapter, migration owner, and compatibility impact.
4. Extract only genuinely missing shared behavior. Prefer adapting existing services over moving or renaming them.
5. Refactor Motor Dynamics as the first study-plugin reference only after reconciling its current feature-branch assumptions with the canonical base API. In particular, the audited `main` StudyService exposes `register()` but does not currently expose `unregister()`; any lifecycle-safe unregister addition must be separately reconciled and explicitly implemented rather than assumed to exist.
6. Apply the abstraction to a second, materially different plugin before freezing concrete SDK class names and method signatures.
7. Publish a minimal plugin template only after the contracts are stable.
8. Keep the architecture principles frozen after merge, while treating concrete SDK APIs and file placement as a later, evidence-driven design decision subject to controlled amendment.

Do not expand the scope of an unrelated feature PR solely to add the SDK. Prefer a focused architecture PR, then migrate Motor Dynamics in a follow-up or an explicitly scoped follow-on PR.

## 9. Freeze boundary and acceptance criteria

### Frozen now: architectural principles

- No parallel or competing plugin lifecycle/registry/study/event/result infrastructure.
- One authoritative owner per shared responsibility; SDK contracts adapt existing owners.
- Core remains headless; Application owns study execution, controlled mutation, canonical events, and study result registration; UI remains projection/interaction.
- Existing UI plugin lifecycle remains distinct from the headless StudyService lifecycle.
- Plugin dependencies, registration conflicts, rollback, and teardown must have explicit semantics in the subsystem that owns them.
- Study success/failure/cancellation and publication of plugin-specific projections must preserve canonical result identity/status.
- CPU fallback and optional accelerator capabilities must be explicit; unsupported GPU/NPU paths are reported rather than implied.
- Architecture and Modification Register are updated together.

### Not frozen yet: concrete SDK API and implementation layout

- Exact package path, public class names, protocol method signatures, descriptor schema, and versioning mechanism.
- Whether a missing common service should be added or an existing service extended.
- The claim that one generic plugin base contract should cover every plugin kind.

These remain open until the current APIs are mapped, Motor Dynamics is reconciled against them, and a second distinct plugin demonstrates the abstraction. No tests/CI are run unless explicitly authorized; any later verification is reported precisely.

## 10. Relationship to existing architecture

This amendment freezes reuse and ownership principles only. It does not supersede the established Core/Application/UI boundaries, the current `ui/plugins/` contract, or the separate `plugins/` engineering-extension namespace. It is not permission to create a parallel plugin framework.

PR #678 introduces a deliberately small, **provisional** contract surface (`PluginDescriptor`, `GridForgePlugin`, and `HostPluginContext`) plus descriptor validation. This is an implementation trial, not a freeze of the public API. The engineering-plugin namespace has no separate generic loader/manager established by this PR; do not route engineering plugins through the UI composition manager. Motor Dynamics validates its declared descriptor at the start of activation and may consume an explicit host-supported API-version set from context metadata. Broader host-wide enforcement, adoption by a second distinct plugin, and runtime verification remain open before the API can be frozen.


## 11. Static reconciliation findings (2026-10-27)

The following repository contracts were inspected directly:

- `core/application/study.py`: `StudyService` is the canonical study execution owner. Its handler is currently a callable accepting `StudyRequest`, `StudyExecutionContext`, and `StudyCancellationToken`. It stores typed `StudyResult` outcomes and emits started/completed/cancelled/failed Application events. The audited `main` version exposes `register()`, identity-aware `unregister()`, `execute()`, `cancel()`, `get_result()`, and `results()`.
- `core/application/event_bus.py`: `ApplicationEventBus` already provides synchronous event publication plus `subscribe()` and `unsubscribe()`. The SDK must reuse this bus.
- `ui/plugins/plugin_contract.py` and `ui/plugins/README.md`: UI composition already has a small protocol and separate loader, registry, manager, state-store, and context owners. Do not replace or merge these lifecycle roles with the study-handler contract.
- `contract.md` and the architecture reconciliation matrix distinguish the broader engineering extension namespace from UI composition. Historical authority statements in the reconciliation matrix must be treated as chronology where superseded, not copied as current repository authority.
- Motor Dynamics uses the existing `StudyService.register()/unregister()` lifecycle and `ApplicationEventBus.subscribe()/unsubscribe()` contract. Static source inspection confirms these method names exist on the current `main` branch; runtime behavior and teardown ordering still require runtime verification.

Decision:
    Freeze the reuse/ownership principles only. PR #678 is a provisional
    implementation trial, not a freeze of SDK names/signatures or a claim of
    generic host-wide enforcement. Keep the API open until a reuse matrix,
    a second distinct plugin, and the required runtime verification are complete.

Verification boundary:
    Static source inspection of current `main` and the PR branch only. No tests,
    CI, syntax checks, imports, runtime, numerical, or hardware validation were run.


## 12. Second-plugin and activation-owner audit (2026-10-27)

Direct inspection of the current `main` tree identified the following:

- `plugins/motor/induction_motor.py` is an engineering model with motor equations and no `plugin_id` / `initialize(context)` / `shutdown()` host lifecycle. It is not a valid second lifecycle-plugin reference for the current SDK protocol.
- `plugins/dynamics/base.py` defines the `DynamicPlugin` equation/state interface (`initial_state`, `derivatives`, `output`, and related model methods), not a host activation lifecycle. It is a distinct domain contract, but applying `GridForgePlugin` to it would conflate model participation in the solver with installation/activation of an extension.
- `core/application/bootstrap.py` constructs the existing `MotorDynamicsComposition` service for Application command composition, but this alone is not a generic engineering-plugin discovery/activation owner.
- `ui/plugins/plugin_contract.py` and the UI manager/registry are explicitly for UI composition. They must not be repurposed to enforce engineering-plugin descriptors.

The source audit therefore does **not** support claiming that the SDK has been reconciled against a second host-lifecycle plugin or that host-wide compatibility enforcement exists. The safe correction is to preserve this as an explicit integration gap, rather than add a speculative loader or falsely adapt a domain model to an installation lifecycle.

Contract consistency correction:

- `HostPluginContext` now explicitly declares `metadata: Mapping[str, Any]` in addition to `application` and `services`, matching the fields read by the Motor Dynamics activation adapter.
- SDK documentation now distinguishes the established UI composition loader from the currently unestablished engineering-plugin activation owner.

Next acceptance gate before freezing the SDK API: identify or explicitly approve the owner for engineering-plugin discovery/activation, then reconcile a genuinely distinct plugin that implements that same lifecycle. Until then, descriptor validation inside Motor Dynamics is a local activation guard only, not host-wide policy enforcement.

Verification boundary: direct source inspection and documentation correction only. No tests, CI, syntax/import checks, runtime lifecycle verification, or hardware checks were run.


## 13. Shared-responsibility reuse matrix (2026-10-27)

This matrix records the current source-backed ownership decision for the SDK proposal. It is a reconciliation aid, not a declaration that all capability areas already have a unified plugin API.

| Responsibility | Existing authoritative owner / contract | SDK/plugin action | Gap or acceptance condition |
|---|---|---|---|
| Engineering equations and model state | Domain packages such as `plugins/motor/`, `plugins/transformer/`, and `plugins/dynamics/base.py` | Keep equations and model-specific state in the domain package; do not force model classes to implement an installation lifecycle | A domain-model contract is not evidence of plugin discovery or activation |
| Application mutation / command registration | `core/application/services/motor_dynamics_service.py` and the Application command composition path | Register through the host-supplied composition adapter; retain its teardown callback | Confirm the host context wiring and rollback at runtime |
| Study execution and cancellation | `core/application/study.py`: `StudyService`, `StudyRequest`, `StudyExecutionContext`, `StudyCancellationToken`, `StudyResult` | Adapt plugin study work to the existing callable handler boundary; do not create a second scheduler or result store | Verify activation teardown cannot unregister an active study type; the service rejects that operation |
| Canonical event delivery | `core/application/event_bus.py`: `ApplicationEventBus.subscribe()/unsubscribe()` | Subscribe to canonical events and release exact callback identities on shutdown | Runtime ordering, partial-registration rollback, and event publication failure behavior remain to be checked |
| UI composition lifecycle | `ui/plugins/plugin_contract.py` and the existing UI loader/registry/manager/state-store | Use only for UI composition plugins | Must not be used as the engineering-plugin activation manager |
| Descriptor / API compatibility | Provisional `core/plugin_sdk/contracts.py` | Validate descriptor at a known activation boundary | No source-backed generic engineering-plugin activation owner currently enforces compatibility before all plugins activate |
| Engineering-plugin discovery and activation | No canonical generic owner established by the current source audit | Do not introduce an unapproved parallel manager; keep the gap explicit | Requires an architecture decision naming the owner, entry point, context construction, preflight order, rollback, and unload semantics |
| Accelerator dispatch | Existing numerical/compute implementation and its concrete backend adapters | Advertise only implemented workloads; CPU fallback remains required where applicable | GPU/NPU claims require backend-specific capability and numerical/runtime evidence; manifest metadata alone is not proof |
| Plugin API freeze | No second distinct host-lifecycle plugin identified in the current `plugins/` tree | Keep SDK names/signatures provisional | First establish activation ownership, then reconcile a second plugin of the same lifecycle kind |

### Approved activation-owner decision (2026-10-27)

The architecture owner approved a **separate, narrowly scoped engineering-plugin host** for PR #678. This host is distinct from the UI PluginManager and is not a discovery framework.

- core/plugin_sdk/host.py owns explicit activation, descriptor/API preflight, duplicate-ID rejection, context construction, rollback on initialization failure, reverse-order shutdown, and retryable ownership when shutdown fails.
- The caller explicitly supplies plugin instances and approved services. Automatic discovery, dependency resolution, background scheduling, and a second service container are out of scope.
- EngineeringPluginContext is immutable and UI-independent. It reuses the canonical Application reference and existing Application-owned motor_dynamics_composition, StudyService, and ApplicationEventBus.
- The host does not create or own Application services, a study scheduler, an event bus, a result store, or a UI lifecycle manager.
- Earlier draft note (superseded by section 20): dashboard creation was deferred. Current integration keeps engineering activation headless and lets the UI composition root create and attach the dashboard explicitly.
- Host availability is not the same as startup integration: the composition root must explicitly instantiate and activate selected engineering plugins and own host shutdown. This PR does not claim that main.py now activates Motor Dynamics automatically.

## 14. Source-backed activation-path reconciliation (2026-10-27)

### Confirmed existing contracts

- `ui/plugins/plugin_context.py` defines the concrete `PluginContext` used by the existing UI composition system. It freezes `services` and `metadata` as mapping proxies and carries the canonical Application reference.
- `ui/plugins/plugin_manager.py`, `plugin_loader.py`, `plugin_registry.py`, and `plugin_state.py` form an explicit UI lifecycle subsystem. Its defaults are canvas, menu, panels, toolbar, status, and shell; they do not declare `gridforge.plugin.motor_dynamics`.
- `core/application/bootstrap.py` creates and owns `application.motor_dynamics_composition`, which is an Application command-registration adapter. The source does not itself construct a `PluginContext`, discover engineering plugin descriptors, or activate `MotorDynamicsPlugin`.
- `ui/bootstrap/presentation_bootstrap.py` validates and synchronizes the presentation/Application boundary; it does not create the motor plugin context or provide an engineering-plugin activation call.
- `MotorDynamicsPlugin.initialize()` expects `context.services["motor_dynamics_composition"]`, but no canonical host wiring path was found in the inspected composition files that inserts that service and activates this engineering plugin.
- `core/application/context.py` explicitly says ApplicationContext does not manage plugins and is intentionally not a service locator.

### Decision and correction

The existing UI PluginManager remains limited to UI composition. The Application's MotorDynamicsComposition remains a command-registration adapter. The approved engineering lifecycle owner is the separate, explicitly invoked EngineeringPluginHost in core/plugin_sdk/host.py; neither existing component is repurposed as that host.

### Implemented host contract and remaining integration work

1. The host receives the canonical Application instance and builds an immutable context from explicitly approved services; Core's ApplicationContext remains unchanged.
2. Descriptor identity/API compatibility and duplicate IDs are rejected before plugin side effects.
3. Failed initialization invokes plugin shutdown for rollback. If rollback shutdown also fails, the host retains the plugin in a pending-cleanup registry, exposes that state, blocks reactivation of the same ID, and retries cleanup through `deactivate()` or `shutdown_all()`. Both failure contexts remain observable.
4. Successful activation is tracked by plugin ID. Shutdown is reverse activation order; a failed shutdown retains ownership so it can be retried.
5. Motor Dynamics receives the existing Application-owned motor_dynamics_composition adapter. Study execution and event delivery reuse Application's canonical services.
6. Engineering activation remains headless. The composition root owns optional dashboard creation and attaches it to the Study workspace sidebar after the engineering plugin is activated.
7. Plugin discovery remains explicit; main.py constructs and activates Motor Dynamics through the host. This does not prove a second plugin implementation.
8. The SDK remains provisional until host integration is source-audited and another genuine lifecycle plugin adopts the contract.

### Freeze gate

The owner decision and initial host implementation are now recorded. Keep PR #678 in draft until the host's failure semantics are audited, the selected plugin is explicitly wired through a composition root with matching shutdown ownership, and a second genuine lifecycle plugin reconciles the shared contract. This host implementation does not claim startup activation is already wired.


## 15. Approved engineering-plugin host implementation (2026-10-27)

The owner approved a separate, narrowly scoped engineering-plugin host. The implementation is in core/plugin_sdk/host.py and exported from core/plugin_sdk/__init__.py.

### Ownership and lifecycle

- Explicit activation only: the caller constructs the plugin and supplies approved services; no automatic discovery or parallel UI registry.
- Preflight: canonical plugin ID, descriptor ID agreement, supported API version, and duplicate active ID are checked before calling plugin code.
- Context: EngineeringPluginContext freezes service and metadata mappings and exposes the canonical Application instance.
- Rollback: if initialize() fails, the host attempts shutdown(); if shutdown also fails, the plugin remains tracked in pending cleanup so teardown can be retried.
- Shutdown: reverse activation order; failed deactivation remains tracked and can be retried. Failed activation rollback is tracked separately, blocks same-ID reactivation, and is retried by `shutdown_all()`.
- Reuse: the supplied service mapping must pass the existing application.motor_dynamics_composition; plugin code uses Application's existing study service and event bus.
- UI boundary: the engineering host has no Qt imports. The main composition root explicitly imports and attaches the Motor Dynamics dashboard to the Study workspace sidebar; this UI subscription is torn down before the engineering publisher.

### Explicit non-goals and remaining acceptance gates

The host does not discover/import plugins, construct Application services, provide dependency injection beyond its explicit context, or automatically activate Motor Dynamics in main.py. A composition root must opt in and own shutdown_all(). Before PR #678 can leave draft, audit the host's failure semantics and wire the selected plugin through one explicit composition root without duplicating service ownership; then reconcile a second non-UI lifecycle plugin. No tests or CI are implied by this documentation.


## 16. Failed activation rollback ownership correction (2026-10-27)

Source review found that the first host implementation surfaced an initialization-plus-rollback failure but did not retain the plugin instance after rollback shutdown failed. That left no host-owned handle through which a later cleanup attempt could be made, contradicting the retryable-lifecycle contract.

Correction in PR #678:

- Track rollback-failed plugin instances separately from successfully active plugins.
- Expose pending cleanup IDs without reporting those plugins as successfully active.
- Reject activation of a plugin ID that is active or awaiting cleanup.
- Let `deactivate(plugin_id)` retry pending cleanup and let `shutdown_all()` retry both active shutdown and pending rollback cleanup.
- Remove pending ownership only after shutdown returns successfully; failed cleanup remains tracked.

This correction does not add discovery, another manager, or duplicate Application services. It does not wire the host into `main.py` or claim runtime verification.

Verification boundary: focused source review and GitHub textual re-fetch only. No tests, CI, syntax/import checks, or runtime lifecycle checks were run.


## 16. Composition-root integration (2026-10-27)

The approved engineering-plugin host is now wired into the application lifecycle in `main.py`.

- The composition root constructs `EngineeringPluginHost` with the canonical Application instance and explicitly activates `MotorDynamicsPlugin`.
- Activation receives only the existing `application.motor_dynamics_composition` adapter through the explicit services mapping; it does not construct a second command registry, study service, event bus, or result store.
- The host is stored as a startup resource. Startup-failure cleanup shuts it down after UI plugins, matching reverse construction order. Normal application shutdown also calls `shutdown_all()` after UI lifecycle/manager teardown.
- The engineering context remains UI-independent. The composition root separately creates the optional telemetry dashboard and attaches it to the Study workspace sidebar; dashboard creation and event subscription are owned by the UI composition root, not the engineering host.
- The existing UI PluginManager remains unchanged and is not used to activate Motor Dynamics.

### Remaining freeze gate

Direct tree inspection still finds no second genuine non-UI engineering plugin with the same `plugin_id / initialize(context) / shutdown()` lifecycle. The domain model packages under `plugins/dynamics/` and `plugins/transformer/` are not installation-lifecycle plugins and must not be force-fit to the SDK. Therefore the host activation path is integrated, but the public SDK API remains provisional until a second real lifecycle plugin can be reconciled and the lifecycle is verified. No tests, CI, syntax/import, or runtime checks were run.


## 17. Failed-activation cleanup ownership (2026-10-27)

A lifecycle review found that the first host implementation surfaced a rollback failure but did not retain the partially initialized plugin for a later cleanup retry. The host now records that plugin in a separate pending-cleanup collection and includes it in reverse-order shutdown attempts, while `active_plugin_ids` reports only successful activations. Duplicate activation is rejected while cleanup remains pending. This preserves the distinction between active and cleanup-pending lifecycle state without adding another manager or service. No runtime validation was run.


## 18. Unified lifecycle ownership order (2026-10-27)

A follow-up audit found that separate active and pending-cleanup collections could cause `shutdown_all()` to process failed-activation cleanup after plugins activated earlier than it, violating strict reverse lifecycle order. The host now records successful activation and cleanup-pending ownership in one ordered ledger. `shutdown_all()` traverses that ledger in reverse, attempting every owned plugin and retaining entries whose shutdown fails. The active-ID view filters the same ledger to successful activations only. This changes lifecycle bookkeeping only; it adds no manager or service. No runtime verification was run.


## 19. Numerical capability and result metadata validation (2026-10-27)

A static contract audit identified two permissive boundaries: backend capability records did not validate the runtime types of their availability/workload/precision fields, and the frozen motor result dataclass exposed a mutable diagnostics mapping. The numerical dispatcher now validates capability value types, non-empty workload/precision sets, unique capability names, and a valid non-duplicated backend preference tuple before selection. `MotorSimulationResult` copies diagnostics into a read-only outer mapping. Nested values are not claimed to be deeply immutable. These checks prevent malformed adapter declarations and accidental top-level metadata rewriting; they do not establish numerical correctness or hardware execution. No tests, CI, syntax/import, or runtime checks were run.


## 20. Dashboard composition and public bootstrap compatibility (2026-10-27)

The source audit identified two integration issues and corrected them in PR #678:

- The optional Motor Dynamics dashboard is now created by the UI composition root and attached to the Study editor's sidebar region. It consumes the plugin-owned read model and canonical Application event bus; it does not own engineering state. Startup-failure cleanup and normal shutdown close the dashboard before the engineering result publisher is shut down, preventing the presentation subscriber from outliving its source.
- Adding the engineering host had changed the public `build_application()` return tuple from six elements to seven. The established six-element return contract is restored. The engineering host and dashboard are retained on the returned MainWindow as private composition-root lifecycle handles; `main()` validates and uses those handles for teardown.

These are source-level integration corrections. The dashboard is not runtime-verified, and no syntax/import, application-startup, shutdown, or event-order check was run.

### Still open: public SDK freeze

No second genuine non-UI engineering plugin with the same installation lifecycle exists in the inspected plugin tree. Domain model classes must not be adapted to this lifecycle merely to satisfy a checklist. The SDK API remains provisional until a real second lifecycle consumer or an explicitly approved alternative conformance criterion is available. CPU reference implementation and capability-gated CUDA batch execution are present in source; no numerical reference comparison or accelerator hardware validation is claimed. NPU execution is not advertised for the state-recurrent motor integration kernel because no compatible implementation/runtime contract is established.


## 21. Installed package and startup-verifier integration (2026-10-27)

The automatically triggered startup-verification workflow found that importing `main` from the installed editable package could not resolve `plugins.motor_dynamics.plugin`: setuptools package discovery included only `core*` and `ui*`, while the engineering extension namespace was not declared as a package. The PR now adds `plugins/__init__.py` and includes `plugins*` in package discovery. The startup verifier's syntax/source inventory now includes `plugins/` and passes the retained engineering host/dashboard handles to lifecycle cleanup. `_shutdown_components` also accepts omitted optional host/dashboard arguments for compatibility with existing callers.

The same workflow's DraftNetwork regression job failed on a missing `canonical_contract` argument and invalid current-transformer measurement-terminal definitions. Direct SHA comparison confirms `core/application/creation_schema.py`, `core/application/bootstrap.py`, and `tests/core/application/test_draft_core_commit_boundary.py` are byte-for-byte the same on `main` and this branch. Those failures predate this PR branch's changes and are not changed here; they require a separate focused correction rather than an unrelated mutation inside the SDK PR.

Verification boundary: workflow logs establish the observed failures. The package/source correction has been committed but must be checked by the next automatic workflow run. Do not claim the workflow passes until a new run confirms it.


## 22. Second non-UI engineering-plugin consumer — DWG Export (2026-11-03)

Static reconciliation of PR #19 confirms that `plugins/dwg_export` now implements the existing engineering lifecycle contract through `GridForgePlugin`, `PluginDescriptor`, and `EngineeringPluginHost`. It is materially distinct from Motor Dynamics: it owns CAD drawing-plan validation, reviewed symbol definitions, and an optional ODA File Converter backend rather than a study handler, motor command registration, or telemetry publisher.

The plugin reuses the canonical `EngineeringPluginHost`; it does not use `ui.plugins.PluginManager`, create a second discovery/activation framework, or add a Core dependency on CAD. The host still requires explicit instance activation and supplied services. DWG requires the explicit `dwg_export_configuration` service with an installed ODA executable and a `SymbolRegistry`. No such production configuration or complete reviewed symbol catalogue is currently supplied by `main.py`, so the composition root does not auto-activate DWG. This is intentional fail-closed optional behavior, not evidence of an enabled production export action.

The renderer-independent SLD snapshot remains in `core.application.sld_export_snapshot`; lifecycle capture remains in the UI adapter; CAD-specific implementation is owned by `plugins.dwg_export`. The plugin's import path loads no `ezdxf` module at package import time; the optional dependency is imported only during backend export. The ODA executable is validated during explicit plugin activation.

### Acceptance status

- Second distinct non-UI lifecycle consumer: implemented in source.
- Reuse of canonical host activation/rollback/teardown contract: statically inspected; focused activation/teardown test added.
- Automatic discovery, activation, or UI action: intentionally not added; no configuration source or reviewed catalogue exists.
- Runtime lifecycle, syntax/import, tests, CI, and actual ODA conversion: not run. The SDK remains provisional until its documented verification gates are completed.

No Core, Application, or UI lifecycle owner was duplicated. No changes were made to the established UI plugin manager.
