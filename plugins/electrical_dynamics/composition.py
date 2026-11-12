"""Shared host composition protocol for all electrical-dynamics extensions.

The protocol describes one Application-supplied composition object. It does
not instantiate services or own lifecycle; Application remains the owner.
Plugin-specific capabilities are validated by each plugin at activation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ElectricalDynamicsComposition(Protocol):
    """Single host adapter consumed by generator, motor and load plugins."""

    # Generator extension and sampled transient execution.
    def register_generator_extension(self, *, plugin: Any) -> Callable[[], None]: ...
    generator_transient_solver: Any
    def create_generator_sampled_adapter(self, *, request: Any, execution_context: Any) -> Any: ...

    # Motor command registration and canonical domain service.
    def register_motor_extension(
        self, *, plugin: Any, command_handlers: Mapping[str, Any]
    ) -> Callable[[], None]: ...
    motor_service: Any

    # Load sampled transient execution.
    load_transient_solver: Any
    def create_load_sampled_adapter(self, *, request: Any, execution_context: Any) -> Any: ...


def require_electrical_dynamics_composition(services: Mapping[str, Any]) -> Any:
    """Resolve the one shared Application composition key without creating fallback services."""
    if not isinstance(services, Mapping):
        raise TypeError("PluginContext.services must be a mapping")
    composition = services.get("electrical_dynamics_composition")
    if composition is None:
        raise RuntimeError(
            "Electrical dynamics activation requires the single host-supplied "
            "'electrical_dynamics_composition'; plugin-specific composition services "
            "are not created."
        )
    return composition


__all__ = ["ElectricalDynamicsComposition", "require_electrical_dynamics_composition"]
