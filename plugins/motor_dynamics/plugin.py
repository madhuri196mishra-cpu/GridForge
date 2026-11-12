"""GridForge V2 lifecycle adapter for the motor-dynamics extension.

Author: Subhendu Mishra

Host command registration and topology mutations are delegated to the explicit
electrical_dynamics_composition host object supplied through PluginContext.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from .commands import (
    ADD_DRAFT_MOTOR_PLACEMENT,
    CONNECT_MOTOR_TO_BUS,
    MotorPlacementCommandHandler,
)
if TYPE_CHECKING:
    from .dashboard import MotorTelemetryDashboardWidget
from .persistence import MotorPluginPersistenceHandler
from plugins.electrical_dynamics.composition import require_electrical_dynamics_composition
from .study_handler import MotorTransientResultPublisher, MotorTransientStudyHandler
from core.application.events import StudyCancelled, StudyCompleted, StudyFailed, StudyStarted
from core.plugin_sdk import (
    SUPPORTED_PLUGIN_API_VERSION,
    GridForgePlugin,
    HostPluginContext,
    PluginDescriptor,
    validate_plugin_descriptor,
)


PLUGIN_DESCRIPTOR = PluginDescriptor(
    plugin_id="gridforge.plugin.motor_dynamics",
    version="2.0.0",
    api_version=SUPPORTED_PLUGIN_API_VERSION,
    display_name="Multi-Physics Dynamic Motor Extension",
    capabilities=frozenset({
        "commands", "sld_graphics", "study_dashboard", "persistence",
        "transient_solver", "cpu_transient_simulation", "optional_cuda_batch_simulation",
        "motor_family_catalog", "motor_nameplate_validation", "family_specific_instrumentation",
        "canonical_measurement_channel_provisioning", "pmsm_dq_cpu_transient",
        "bldc_trapezoidal_cpu_transient", "synrm_dq_cpu_transient",
        "srm_phase_inductance_cpu_transient", "dc_excitation_cpu_transient",
        "single_phase_rms_envelope_cpu_transient", "universal_series_commutator_cpu_transient",
        "wound_field_synchronous_damper_cpu_transient", "hysteresis_torque_angle_cpu_transient",
    }),
)


def _aggregate_base_exceptions(message: str, errors: list[BaseException]) -> BaseException:
    """Aggregate lifecycle failures while retaining Python 3.10 compatibility."""
    try:
        group_type = BaseExceptionGroup
    except NameError:  # BaseExceptionGroup was introduced in Python 3.11.
        error = RuntimeError(
            message + ": " + "; ".join(f"{type(item).__name__}: {item}" for item in errors)
        )
        error.errors = tuple(errors)  # type: ignore[attr-defined]
        return error
    return group_type(message, errors)


class MotorDynamicsPlugin(GridForgePlugin):
    # Discoverable before instance activation by an explicit host adapter.
    descriptor: ClassVar[PluginDescriptor] = PLUGIN_DESCRIPTOR

    @property
    def plugin_id(self) -> str:
        return "gridforge.plugin.motor_dynamics"

    def __init__(self) -> None:
        self.application: Any = None
        self.context: Any = None
        self.read_models: Any = None
        self.persistence: MotorPluginPersistenceHandler | None = None
        self.dashboard: MotorTelemetryDashboardWidget | None = None
        self._unhooks: list[Any] = []
        self._initialized = False
        self._ready = False

    def initialize(self, context: HostPluginContext) -> None:
        """Initialize from GridForge's canonical PluginContext."""
        if self._initialized:
            if self._ready:
                return
            raise RuntimeError(
                "A previous initialization rollback is still pending; call shutdown() "
                "successfully before retrying initialize()."
            )
        if context is None or not hasattr(context, "application"):
            raise TypeError("MotorDynamicsPlugin.initialize requires a GridForge PluginContext")

        # Compatibility is checked before the plugin registers commands,
        # studies, or event callbacks. The host may explicitly advertise its
        # supported API versions in PluginContext.metadata. Older hosts that
        # do not publish this metadata use the SDK's declared compatibility
        # set, rather than bypassing descriptor identity/version validation.
        context_metadata = getattr(context, "metadata", {})
        if not isinstance(context_metadata, Mapping):
            raise TypeError("PluginContext.metadata must be a mapping")
        supported_versions = context_metadata.get(
            "host_supported_plugin_api_versions",
            frozenset({SUPPORTED_PLUGIN_API_VERSION}),
        )
        if isinstance(supported_versions, str):
            raise TypeError("host_supported_plugin_api_versions must be a collection of version strings")
        if not isinstance(supported_versions, (tuple, list, set, frozenset)):
            raise TypeError("host_supported_plugin_api_versions must be a collection of version strings")
        if any(not isinstance(version, str) or not version.strip() for version in supported_versions):
            raise TypeError("host_supported_plugin_api_versions must contain only non-empty strings")
        supported_versions = frozenset(supported_versions)
        validate_plugin_descriptor(
            self.descriptor,
            supported_api_versions=supported_versions,
            expected_plugin_id=self.plugin_id,
        )

        application = context.application
        if application is None:
            raise RuntimeError("PluginContext.application is required")

        services = getattr(context, "services", None)
        if not isinstance(services, Mapping):
            raise TypeError("PluginContext.services must be a mapping")
        composition = require_electrical_dynamics_composition(services)
        if composition is None:
            raise RuntimeError(
                "Motor dynamics cannot be activated until PluginContext.services "
                "provides 'electrical_dynamics_composition' with transactional motor "
                "services and command registration."
            )

        register = getattr(composition, "register_motor_extension", None)
        if not callable(register):
            raise TypeError(
                "electrical_dynamics_composition must expose register_motor_extension"
            )

        # Keep command registration and domain service responsibilities distinct.
        # The composition adapter owns host wiring; motor_service owns operations.
        motor_service = getattr(composition, "motor_service", None)
        if motor_service is None:
            raise RuntimeError(
                "electrical_dynamics_composition must expose a separate 'motor_service'; "
                "the registration adapter itself is not a domain service."
            )

        read_models = getattr(composition, "motor_read_models", None)
        required_read_model_methods = (
            "publish", "get_motor_id_for_study",
            "get_simulation_summary", "get_raw_channel_vector",
        )
        missing_read_model_methods = [
            name for name in required_read_model_methods
            if not callable(getattr(read_models, name, None))
        ]
        if missing_read_model_methods:
            raise RuntimeError(
                "electrical_dynamics_composition.motor_read_models must be the "
                "Application-owned motor projection and expose: "
                + ", ".join(missing_read_model_methods)
            )

        handler = MotorPlacementCommandHandler(motor_service)
        token = None
        dashboard = None
        persistence = None
        study_unhook = None
        publisher = None
        event_bus = None
        try:
            # Do not publish partially initialized state on the plugin instance.
            # If any subsequent setup fails, undo the registration.
            token = register(
                plugin=self,
                command_handlers={
                    ADD_DRAFT_MOTOR_PLACEMENT: handler,
                    CONNECT_MOTOR_TO_BUS: handler,
                },
            )
            if not callable(token):
                raise TypeError(
                    "register_motor_extension must return a callable teardown "
                    "for the registered motor command handlers."
                )

            persistence = MotorPluginPersistenceHandler(application)
            event_bus = getattr(application, "event_bus", None) or getattr(context, "event_bus", None)
            study_service = getattr(application, "study_service", None)
            study_register = getattr(study_service, "register", None)
            study_unregister = getattr(study_service, "unregister", None)
            subscribe = getattr(event_bus, "subscribe", None) if event_bus is not None else None
            unsubscribe = getattr(event_bus, "unsubscribe", None) if event_bus is not None else None
            if not callable(study_register) or not callable(study_unregister):
                raise RuntimeError("Application.study_service must support lifecycle-owned register/unregister")
            if not callable(subscribe) or not callable(unsubscribe):
                raise RuntimeError("Motor transient studies require the canonical Application event bus")
            study_handler = MotorTransientStudyHandler()

            def release_study_handler() -> None:
                # Identity-safe unregister cannot remove a replacement handler.
                study_unregister("motor_transient", study_handler)

            # Own rollback before registration: a service that partially registers
            # and then raises still gets an identity-safe unregister attempt.
            study_unhook = release_study_handler
            study_register("motor_transient", study_handler)
            publisher = MotorTransientResultPublisher(application, read_models)

            def release_publisher() -> None:
                # Close first: even if an event callback is already queued, it
                # cannot publish after plugin teardown begins.
                publisher.close()
                errors: list[BaseException] = []
                for event_type, callback in (
                    (StudyCompleted, publisher),
                    (StudyStarted, publisher.on_started),
                    (StudyFailed, publisher.on_terminal),
                    (StudyCancelled, publisher.on_terminal),
                ):
                    try:
                        unsubscribe(event_type, callback)
                    except BaseException as exc:
                        errors.append(exc)
                if errors:
                    raise _aggregate_base_exceptions("Motor study publisher teardown failed", errors) from None

            # Install rollback ownership before the first subscription because
            # any individual subscribe operation may fail after earlier ones
            # have already registered callbacks.
            study_unhook_original = study_unhook
            def release_study_integration() -> None:
                errors: list[BaseException] = []
                try:
                    release_publisher()
                except BaseException as exc:
                    errors.append(exc)
                try:
                    study_unhook_original()
                except BaseException as exc:
                    errors.append(exc)
                if errors:
                    raise _aggregate_base_exceptions("Motor study integration teardown failed", errors) from None
            study_unhook = release_study_integration

            # Register the publisher before the dashboard subscriber so the
            # read-model snapshot exists when the dashboard handles completion.
            subscribe(StudyStarted, publisher.on_started)
            subscribe(StudyCompleted, publisher)
            subscribe(StudyFailed, publisher.on_terminal)
            subscribe(StudyCancelled, publisher.on_terminal)
            parent = getattr(context, "parent", None) or getattr(context, "root_widget", None)
            if parent is not None and event_bus is not None:
                # Import Qt presentation only when the host explicitly supplies
                # a UI parent; engineering activation remains UI-independent.
                from .dashboard import MotorTelemetryDashboardWidget

                dashboard = MotorTelemetryDashboardWidget(
                    read_models,
                    event_bus=event_bus,
                    parent=parent,
                )
        except BaseException as initialization_error:
            cleanup_errors: list[BaseException] = []
            dashboard_retained = False
            failed_token = None

            if dashboard is not None:
                try:
                    dashboard.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    dashboard_retained = True

            if callable(study_unhook):
                try:
                    study_unhook()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    self._unhooks.append(study_unhook)

            if callable(token):
                try:
                    token()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    failed_token = token

            if not cleanup_errors:
                raise

            # Publish only the handles that could not be released. The normal
            # initialization path remains atomic, while failed rollback remains
            # owned and retryable through shutdown().
            self.context = context
            self.application = application
            self.persistence = persistence
            self.dashboard = dashboard if dashboard_retained else None
            if failed_token is not None:
                self._unhooks.append(failed_token)
            self._initialized = True
            self._ready = False
            raise _aggregate_base_exceptions(
                "Motor Dynamics plugin initialization failed and rollback was incomplete",
                [initialization_error, *cleanup_errors],
            ) from None

        self.context = context
        self.application = application
        self.persistence = persistence
        self.read_models = read_models
        # Shutdown unwinds in reverse order: close/unregister study
        # publication before removing the command registration.
        self._unhooks.append(token)
        if callable(study_unhook):
            self._unhooks.append(study_unhook)
        self.dashboard = dashboard
        self._initialized = True
        self._ready = True

    def shutdown(self) -> None:
        """Release plugin resources without discarding failed teardown handles.

        A failed unhook or dashboard close remains attached to the plugin so a
        caller can diagnose the failure and retry shutdown. Context and service
        references are cleared only after all owned resources are released.
        """
        errors: list[BaseException] = []
        failed_unhooks: list[Any] = []
        for unhook in reversed(self._unhooks):
            try:
                unhook()
            except BaseException as exc:
                errors.append(exc)
                failed_unhooks.append(unhook)
        # Preserve original registration order while retaining only failed
        # teardown handles; successful unhooks must not run a second time.
        self._unhooks = list(reversed(failed_unhooks))

        if self.dashboard is not None:
            try:
                self.dashboard.close()
            except BaseException as exc:
                errors.append(exc)
            else:
                self.dashboard = None

        if errors:
            self._initialized = True
            self._ready = False
            raise _aggregate_base_exceptions("Motor Dynamics plugin shutdown encountered failures", errors) from None

        # The Application owns the shared read-model lifetime; plugin shutdown
        # only releases its subscribers and presentation references.
        self._initialized = False
        self._ready = False
        self.persistence = None
        self.read_models = None
        self.application = None
        self.context = None


__all__ = ["MotorDynamicsPlugin", "PLUGIN_DESCRIPTOR"]
