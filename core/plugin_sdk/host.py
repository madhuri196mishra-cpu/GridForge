"""Explicit lifecycle owner for engineering plugins.

This host does not discover plugins, create Application services, or manage UI
composition. The application composition root supplies plugin instances and
approved dependencies explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import (
    SUPPORTED_PLUGIN_API_VERSION,
    GridForgePlugin,
    validate_plugin_descriptor,
)


@dataclass(frozen=True, slots=True)
class EngineeringPluginContext:
    """Immutable, UI-independent context built by the engineering host."""

    application: Any
    services: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Optional defaults preserve the historical direct-construction signature;
    # EngineeringPluginHost always supplies a live project snapshot and generation.
    project_context: Any = None
    activation_generation: int = 0

    def __post_init__(self) -> None:
        if self.application is None:
            raise ValueError("application is required")
        if not isinstance(self.services, Mapping):
            raise TypeError("services must be a mapping")
        if isinstance(self.activation_generation, bool) or not isinstance(self.activation_generation, int) or self.activation_generation < 0:
            raise ValueError("activation_generation must be a non-negative integer")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


class EngineeringPluginHost:
    """Own explicit engineering-plugin activation, rollback, and shutdown.

    Discovery and construction remain the responsibility of the caller. The
    host validates identity/API compatibility before invoking plugin code.
    Successfully activated plugins and plugins awaiting rollback cleanup are
    both retained until their shutdown completes successfully.
    """

    def __init__(
        self,
        application: Any,
        *,
        supported_api_versions: frozenset[str] = frozenset({SUPPORTED_PLUGIN_API_VERSION}),
    ) -> None:
        if application is None:
            raise ValueError("application is required")
        if not isinstance(supported_api_versions, frozenset) or not supported_api_versions:
            raise TypeError("supported_api_versions must be a non-empty frozenset")
        if any(not isinstance(item, str) or not item.strip() for item in supported_api_versions):
            raise ValueError("supported_api_versions must contain non-empty strings")
        self._application = application
        self._supported_api_versions = supported_api_versions
        self._active: dict[str, GridForgePlugin] = {}
        # Failed initialization whose rollback also failed remains owned and
        # retryable, but is not reported as successfully active.
        self._pending_cleanup: dict[str, GridForgePlugin] = {}
        # One ownership ledger preserves reverse lifecycle order across both
        # active plugins and partially initialized plugins awaiting cleanup.
        self._ownership_order: list[str] = []

    @property
    def active_plugin_ids(self) -> tuple[str, ...]:
        """Return successfully activated plugin IDs in activation order."""
        return tuple(plugin_id for plugin_id in self._ownership_order if plugin_id in self._active)

    @property
    def pending_cleanup_plugin_ids(self) -> tuple[str, ...]:
        """Return plugin IDs whose failed activation rollback needs retry."""
        return tuple(self._pending_cleanup)

    def activate(
        self,
        plugin: GridForgePlugin,
        *,
        services: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Activate one explicitly supplied plugin, rolling back failed setup."""
        if not isinstance(services, Mapping):
            raise TypeError("services must be a mapping")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")

        plugin_id = getattr(plugin, "plugin_id", None)
        descriptor = getattr(plugin, "descriptor", None)
        if not isinstance(plugin_id, str) or not plugin_id.strip() or plugin_id != plugin_id.strip():
            raise ValueError("plugin.plugin_id must be a canonical non-empty string")
        if plugin_id in self._active or plugin_id in self._pending_cleanup:
            raise RuntimeError(
                f"Engineering plugin {plugin_id!r} is already active or awaiting cleanup"
            )
        validate_plugin_descriptor(
            descriptor,
            supported_api_versions=self._supported_api_versions,
            expected_plugin_id=plugin_id,
        )
        initialize = getattr(plugin, "initialize", None)
        shutdown = getattr(plugin, "shutdown", None)
        if not callable(initialize) or not callable(shutdown):
            raise TypeError("Engineering plugins must provide callable initialize() and shutdown()")

        context_metadata = dict(metadata or {})
        if "host_supported_plugin_api_versions" in context_metadata:
            advertised = context_metadata["host_supported_plugin_api_versions"]
            if advertised != self._supported_api_versions:
                raise ValueError(
                    "metadata host_supported_plugin_api_versions is host-owned and cannot be overridden"
                )
        context_metadata["host_supported_plugin_api_versions"] = self._supported_api_versions
        lifecycle = getattr(self._application, "project_lifecycle", None)
        project_context = getattr(lifecycle, "context", None)
        activation_generation = getattr(lifecycle, "activation_generation", None)
        if project_context is None:
            raise RuntimeError(
                "Engineering plugin activation requires an active Application project context."
            )
        if isinstance(activation_generation, bool) or not isinstance(activation_generation, int) or activation_generation < 1:
            raise RuntimeError(
                "Engineering plugin activation requires a valid Application project activation generation."
            )
        context = EngineeringPluginContext(
            application=self._application,
            project_context=project_context,
            activation_generation=activation_generation,
            services=services,
            metadata=context_metadata,
        )

        try:
            initialize(context)
        except BaseException as initialization_error:
            try:
                shutdown()
            except BaseException as rollback_error:
                # The plugin may still own registrations/resources. Do not lose
                # the only lifecycle handle: shutdown_all()/deactivate() can retry.
                self._pending_cleanup[plugin_id] = plugin
                self._ownership_order.append(plugin_id)
                raise RuntimeError(
                    f"Engineering plugin {plugin_id!r} failed activation and rollback; "
                    f"cleanup is pending: {type(rollback_error).__name__}: {rollback_error}"
                ) from initialization_error
            raise

        self._active[plugin_id] = plugin
        self._ownership_order.append(plugin_id)

    def deactivate(self, plugin_id: str) -> None:
        """Deactivate an active plugin or retry cleanup after failed activation."""
        if not isinstance(plugin_id, str) or not plugin_id.strip():
            raise ValueError("plugin_id must be a non-empty string")

        pending = self._pending_cleanup.get(plugin_id)
        if pending is not None:
            pending.shutdown()
            del self._pending_cleanup[plugin_id]
            self._ownership_order.remove(plugin_id)
            return

        plugin = self._active.get(plugin_id)
        if plugin is None:
            raise KeyError(f"Engineering plugin {plugin_id!r} is not active or pending cleanup")
        plugin.shutdown()
        del self._active[plugin_id]
        self._ownership_order.remove(plugin_id)

    def shutdown_all(self) -> None:
        """Shut down in reverse activation order and retry pending rollback cleanup."""
        errors: list[BaseException] = []
        for plugin_id in reversed(tuple(self._ownership_order)):
            try:
                self.deactivate(plugin_id)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            details = "; ".join(f"{type(item).__name__}: {item}" for item in errors)
            raise RuntimeError(f"Engineering plugin host shutdown incomplete: {details}") from errors[0]


__all__ = ["EngineeringPluginContext", "EngineeringPluginHost"]
