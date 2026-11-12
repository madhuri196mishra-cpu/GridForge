"""Lifecycle adapter for GridForge's universal generator dynamics extension.

Application composition owns plugin registration and supplies the sampled
transient solver/adapter factory. Application StudyService owns scheduling,
cancellation, provenance and result persistence. This plugin creates no
parallel service locator, scheduler, solver, command manager or event bus.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from core.plugin_sdk import (
    SUPPORTED_PLUGIN_API_VERSION,
    GridForgePlugin,
    HostPluginContext,
    PluginDescriptor,
    validate_plugin_descriptor,
)
from .study_handler import GeneratorTransientStudyHandler
from plugins.electrical_dynamics.composition import require_electrical_dynamics_composition

GENERATOR_TRANSIENT_STUDY_TYPE = "generator_transient"

PLUGIN_DESCRIPTOR = PluginDescriptor(
    plugin_id="gridforge.plugin.generator_dynamics",
    version="0.1.0",
    api_version=SUPPORTED_PLUGIN_API_VERSION,
    display_name="Universal Generator Dynamics and Instrumentation",
    capabilities=frozenset({
        "generator_catalogue",
        "synchronous_machine_dynamics",
        "prime_mover_models",
        "reduced_hydro_governor_model",
        "ggov1_parameter_contract",
        "ggov1_pid_subblock",
        "ggov1_valve_actuator_subblock",
        "ggov1_turbine_fuel_path_subblock",
        "ggov1_low_value_selector",
        "ggov1_control_coordinator",
        "ggov1_common_fuel_command_limiter",
        "ggov1_operating_point_reconciliation",
        "ggov1_sampled_execution_adapter",
        "ggov1_study_service_registration",
        "ggov1_host_transient_solver_handoff",
        "ggov1_accepted_step_transaction",
        "ggov1_dynamic_state_layout",
        "ggov1_pid_tracking_input",
        "ggov1_acceleration_limiter",
        "ggov1_exhaust_temperature_load_limiter",
        "ggov1_temperature_measurement_conditioner",
        "excitation_and_governor_configuration",
        "instrumentation_templates",
        "measurement_channel_provisioning",
        "cpu_gpu_compute_policy",
        "npu_inference_policy",
    }),
)


class GeneratorDynamicsPlugin(GridForgePlugin):
    """Explicitly activated extension with reversible Application registrations."""

    descriptor: ClassVar[PluginDescriptor] = PLUGIN_DESCRIPTOR

    @property
    def plugin_id(self) -> str:
        return PLUGIN_DESCRIPTOR.plugin_id

    def __init__(self) -> None:
        self._unhooks: list[Any] = []
        self._initialized = False

    def initialize(self, context: HostPluginContext) -> None:
        if self._initialized or self._unhooks:
            raise RuntimeError("GeneratorDynamicsPlugin is already active or awaiting cleanup")
        if context is None or getattr(context, "application", None) is None:
            raise TypeError("initialize requires a canonical GridForge plugin context")
        metadata = getattr(context, "metadata", {})
        services = getattr(context, "services", None)
        if not isinstance(metadata, Mapping) or not isinstance(services, Mapping):
            raise TypeError("plugin context metadata and services must be mappings")
        supported = metadata.get(
            "host_supported_plugin_api_versions",
            frozenset({SUPPORTED_PLUGIN_API_VERSION}),
        )
        if isinstance(supported, str) or not isinstance(supported, (tuple, list, set, frozenset)):
            raise TypeError("host plugin API versions must be a collection of strings")
        if any(not isinstance(version, str) or not version.strip() for version in supported):
            raise TypeError("host plugin API versions must contain non-empty strings")
        validate_plugin_descriptor(
            self.descriptor,
            supported_api_versions=frozenset(supported),
            expected_plugin_id=self.plugin_id,
        )

        application = context.application
        composition = require_electrical_dynamics_composition(services)
        if composition is None:
            raise RuntimeError(
                "Generator dynamics activation requires the Application-owned "
                "'electrical_dynamics_composition' service; no parallel service is created."
            )
        register_extension = getattr(composition, "register_generator_extension", None)
        if not callable(register_extension):
            raise TypeError(
                "electrical_dynamics_composition must expose register_generator_extension"
            )

        # Model-specific sampled adapters are created per StudyRequest, not
        # retained globally across machines or study cases.
        solver = getattr(composition, "generator_transient_solver", None)
        adapter_factory = getattr(composition, "create_generator_sampled_adapter", None)
        if solver is None or not callable(getattr(solver, "solve_generator_transient", None)):
            raise RuntimeError(
                "electrical_dynamics_composition must expose the host-owned "
                "generator_transient_solver.solve_generator_transient(...) capability"
            )
        if not callable(adapter_factory):
            raise RuntimeError(
                "electrical_dynamics_composition must expose "
                "create_generator_sampled_adapter(request=..., execution_context=...)"
            )

        study_service = getattr(application, "study_service", None)
        study_register = getattr(study_service, "register", None)
        study_unregister = getattr(study_service, "unregister", None)
        if not callable(study_register) or not callable(study_unregister):
            raise RuntimeError(
                "Application.study_service must support identity-safe register/unregister"
            )

        try:
            extension_token = register_extension(plugin=self)
            if not callable(extension_token):
                raise TypeError(
                    "register_generator_extension must return a callable teardown"
                )
            self._unhooks.append(extension_token)

            handler = GeneratorTransientStudyHandler(solver, adapter_factory)

            def release_study_handler() -> None:
                # StudyService refuses removal while this study type is active;
                # retain this handle so plugin shutdown can be retried later.
                study_unregister(GENERATOR_TRANSIENT_STUDY_TYPE, handler)

            # Record rollback ownership before registration in case a host
            # registration partially succeeds and then raises.
            self._unhooks.append(release_study_handler)
            study_register(GENERATOR_TRANSIENT_STUDY_TYPE, handler)
        except BaseException as initialization_error:
            cleanup_errors: list[BaseException] = []
            failed_unhooks: list[Any] = []
            for unhook in reversed(self._unhooks):
                try:
                    unhook()
                except BaseException as exc:
                    cleanup_errors.append(exc)
                    failed_unhooks.append(unhook)
            self._unhooks = list(reversed(failed_unhooks))
            if cleanup_errors:
                self._initialized = True
                details = "; ".join(
                    f"{type(error).__name__}: {error}" for error in cleanup_errors
                )
                raise RuntimeError(
                    "Generator Dynamics initialization failed and rollback remains pending: "
                    + details
                ) from initialization_error
            raise

        self._initialized = True

    def shutdown(self) -> None:
        """Unregister study handling before releasing the host composition hook."""
        errors: list[BaseException] = []
        failed_unhooks: list[Any] = []
        for unhook in reversed(self._unhooks):
            try:
                unhook()
            except BaseException as exc:
                errors.append(exc)
                failed_unhooks.append(unhook)
        self._unhooks = list(reversed(failed_unhooks))
        if errors:
            self._initialized = True
            details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
            raise RuntimeError("Generator Dynamics shutdown incomplete: " + details) from errors[0]
        self._initialized = False


__all__ = [
    "GeneratorDynamicsPlugin",
    "PLUGIN_DESCRIPTOR",
    "GENERATOR_TRANSIENT_STUDY_TYPE",
]
