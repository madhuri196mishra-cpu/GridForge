"""Typed extension contracts aligned with GridForge's existing host lifecycle.

These are protocols and metadata only. They do not create a second context
implementation, plugin manager, lifecycle scheduler, event bus, or study runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

SUPPORTED_PLUGIN_API_VERSION = "V2.0"


@dataclass(frozen=True)
class PluginDescriptor:
    """Optional, declarative compatibility metadata for a plugin."""

    plugin_id: str
    version: str
    api_version: str = SUPPORTED_PLUGIN_API_VERSION
    display_name: str = ""
    capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("plugin_id", "version", "api_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        prefix = "gridforge.plugin."
        suffix = self.plugin_id[len(prefix):] if self.plugin_id.startswith(prefix) else ""
        if not suffix or any(not part for part in suffix.split(".")):
            raise ValueError(
                "plugin_id must use 'gridforge.plugin.' followed by a non-empty identifier"
            )
        if not isinstance(self.display_name, str):
            raise TypeError("display_name must be a string")
        if not isinstance(self.capabilities, frozenset):
            raise TypeError("capabilities must be a frozenset of strings")
        if any(not isinstance(item, str) or not item.strip() for item in self.capabilities):
            raise ValueError("capabilities must contain non-empty strings")


def validate_plugin_descriptor(
    descriptor: PluginDescriptor,
    *,
    supported_api_versions: frozenset[str] = frozenset({SUPPORTED_PLUGIN_API_VERSION}),
    expected_plugin_id: str | None = None,
) -> None:
    """Reject malformed or incompatible SDK metadata before activation.

    This is a pure validation primitive. The canonical host lifecycle owner
    must call it before activation; the SDK does not discover or load plugins.
    """
    if not isinstance(descriptor, PluginDescriptor):
        raise TypeError("descriptor must be a PluginDescriptor")
    if not isinstance(supported_api_versions, frozenset):
        raise TypeError("supported_api_versions must be a frozenset")
    if not supported_api_versions:
        raise ValueError("supported_api_versions cannot be empty")
    if any(not isinstance(version, str) or not version.strip() for version in supported_api_versions):
        raise ValueError("supported_api_versions must contain non-empty strings")
    if expected_plugin_id is not None:
        if not isinstance(expected_plugin_id, str) or not expected_plugin_id.strip():
            raise ValueError("expected_plugin_id must be a non-empty string")
        if descriptor.plugin_id != expected_plugin_id:
            raise ValueError(
                f"Plugin descriptor identity mismatch: expected {expected_plugin_id!r}, "
                f"got {descriptor.plugin_id!r}"
            )
    if descriptor.api_version not in supported_api_versions:
        supported = ", ".join(sorted(supported_api_versions))
        raise RuntimeError(
            f"Plugin {descriptor.plugin_id!r} requires SDK API "
            f"{descriptor.api_version!r}; host supports: {supported}"
        )


@runtime_checkable
class HostPluginContext(Protocol):
    """Structural view of the canonical host-created PluginContext."""

    application: Any
    project_context: Any
    activation_generation: int
    services: Mapping[str, Any]
    metadata: Mapping[str, Any]


@runtime_checkable
class GridForgePlugin(Protocol):
    """Lifecycle shape used by the current GridForge plugin host."""

    @property
    def plugin_id(self) -> str:
        """Return the stable namespaced plugin identifier."""
        ...

    def initialize(self, context: HostPluginContext) -> None:
        """Initialize using the host-created context and host-owned services."""
        ...

    def shutdown(self) -> None:
        """Release plugin-owned registrations and resources."""
        ...
