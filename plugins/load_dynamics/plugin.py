"""Explicit engineering-plugin lifecycle adapter for load dynamics."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from core.application.study import StudyService
from core.plugin_sdk import (
    SUPPORTED_PLUGIN_API_VERSION, GridForgePlugin, HostPluginContext,
    PluginDescriptor, validate_plugin_descriptor,
)
from .study_handler import LoadTransientStudyHandler

LOAD_TRANSIENT_STUDY_TYPE = "load_transient"

PLUGIN_DESCRIPTOR = PluginDescriptor(
    plugin_id="gridforge.plugin.load_dynamics",
    version="0.1.0",
    api_version=SUPPORTED_PLUGIN_API_VERSION,
    display_name="Universal Electrical Load Dynamics and Instrumentation",
    capabilities=frozenset({
        "load_family_catalogue", "zip_voltage_dependency", "demand_profiles",
        "induction_motor_parameters", "load_instrumentation_templates",
        "measurement_channel_point_catalogue", "load_transient_study_adapter",
        "cpu_numerical_baseline", "capability_gated_gpu",
        "npu_inference_only",
    }),
)


class LoadDynamicsPlugin(GridForgePlugin):
    """Register load studies with canonical Application services; own no scheduler."""

    descriptor: ClassVar[PluginDescriptor] = PLUGIN_DESCRIPTOR

    @property
    def plugin_id(self) -> str:
        return PLUGIN_DESCRIPTOR.plugin_id

    def __init__(self) -> None:
        self._unhooks: list[Any] = []
        self._initialized = False

    def initialize(self, context: HostPluginContext) -> None:
        if self._initialized or self._unhooks:
            raise RuntimeError("LoadDynamicsPlugin is already active or awaiting cleanup")
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
        study_service = getattr(application, "study_service", None)
        if not isinstance(study_service, StudyService):
            raise RuntimeError("Application.study_service must be the canonical StudyService")
        composition = services.get("load_dynamics_composition")
        if composition is None:
            raise RuntimeError(
                "Load dynamics activation requires the host-supplied "
                "'load_dynamics_composition'; the plugin does not create a solver."
            )
        solver = getattr(composition, "load_transient_solver", None)
        adapter_factory = getattr(composition, "create_load_sampled_adapter", None)
        handler = LoadTransientStudyHandler(solver, adapter_factory)

        def release_handler() -> None:
            study_service.unregister(LOAD_TRANSIENT_STUDY_TYPE, handler)

        self._unhooks.append(release_handler)
        try:
            study_service.register(LOAD_TRANSIENT_STUDY_TYPE, handler)
        except BaseException:
            try:
                release_handler()
            except BaseException:
                self._initialized = True
                raise RuntimeError("Load dynamics registration failed and cleanup remains pending")
            self._unhooks.clear()
            raise
        self._initialized = True

    def shutdown(self) -> None:
        errors: list[BaseException] = []
        failed: list[Any] = []
        for unhook in reversed(self._unhooks):
            try:
                unhook()
            except BaseException as exc:
                errors.append(exc)
                failed.append(unhook)
        self._unhooks = list(reversed(failed))
        if errors:
            self._initialized = True
            raise RuntimeError("Load dynamics shutdown incomplete") from errors[0]
        self._initialized = False


__all__ = ["LoadDynamicsPlugin", "PLUGIN_DESCRIPTOR", "LOAD_TRANSIENT_STUDY_TYPE"]
