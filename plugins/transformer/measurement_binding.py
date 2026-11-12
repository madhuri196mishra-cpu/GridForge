"""Explicit binding contract between transformer points and Core channels.

This module does not create or own MeasurementChannel objects. It validates
an Application-supplied mapping from a transformer telemetry point to an
already provisioned canonical Core MeasurementChannel. Source equipment,
terminal identity, producer trust, persistence and publication remain owned
by Core/Application.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .telemetry import ChannelDefinition, TransformerQuantity


@dataclass(frozen=True, slots=True)
class TransformerMeasurementBinding:
    """Application-authored association to an existing Core channel."""

    equipment_id: str
    telemetry_channel_id: str
    measurement_channel_id: str

    def __post_init__(self) -> None:
        for name in ("equipment_id", "telemetry_channel_id", "measurement_channel_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a canonical non-empty string.")


# The canonical Core MeasurementChannel supports these signal classes.
# Complex quantities such as power are deliberately not inferred from a
# single current/voltage point; they require an explicit Application model.
_SIGNAL_COMPATIBILITY = {
    TransformerQuantity.VOLTAGE: frozenset({"VOLTAGE"}),
    TransformerQuantity.CURRENT: frozenset({"CURRENT"}),
    TransformerQuantity.FREQUENCY: frozenset({"FREQUENCY"}),
    TransformerQuantity.PHASE_ANGLE: frozenset({"PHASE_ANGLE"}),
    TransformerQuantity.ACTIVE_POWER: frozenset({"POWER"}),
    TransformerQuantity.REACTIVE_POWER: frozenset({"POWER"}),
    TransformerQuantity.APPARENT_POWER: frozenset({"POWER"}),
    TransformerQuantity.POWER_FACTOR: frozenset({"CUSTOM"}),
    TransformerQuantity.ACTIVE_ENERGY: frozenset({"CUSTOM"}),
    TransformerQuantity.REACTIVE_ENERGY: frozenset({"CUSTOM"}),
}


def resolve_transformer_measurement_bindings(
    catalogue: Iterable[ChannelDefinition],
    bindings: Iterable[TransformerMeasurementBinding],
    measurement_channels: Mapping[str, Any],
) -> tuple[tuple[ChannelDefinition, Any], ...]:
    """Resolve explicit plugin-point bindings against canonical Core channels.

    Missing, duplicate, cross-equipment, unit-incompatible, or signal-
    incompatible associations fail closed. Unbound catalogue points are
    permitted because a transformer may not have every optional sensor.
    """
    catalogue_items = tuple(catalogue)
    points = {point.channel_id: point for point in catalogue_items}
    if len(points) != len(catalogue_items):
        raise ValueError("Transformer telemetry catalogue contains duplicate channel ids.")
    if not isinstance(measurement_channels, Mapping):
        raise TypeError("measurement_channels must be the Application channel mapping.")

    resolved: list[tuple[ChannelDefinition, Any]] = []
    seen_points: set[str] = set()
    seen_core_channels: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, TransformerMeasurementBinding):
            raise TypeError("bindings must contain TransformerMeasurementBinding values.")
        if binding.telemetry_channel_id in seen_points:
            raise ValueError(f"Transformer point is bound more than once: {binding.telemetry_channel_id}")
        if binding.measurement_channel_id in seen_core_channels:
            raise ValueError(f"Core measurement channel is bound more than once: {binding.measurement_channel_id}")
        point = points.get(binding.telemetry_channel_id)
        if point is None:
            raise KeyError(f"Transformer telemetry point not found: {binding.telemetry_channel_id}")
        if point.equipment_id != binding.equipment_id:
            raise ValueError("Binding equipment identity does not match the telemetry point.")
        channel = measurement_channels.get(binding.measurement_channel_id)
        if channel is None:
            raise KeyError(f"Core MeasurementChannel not found: {binding.measurement_channel_id}")
        signal_type = getattr(getattr(channel, "signal_type", None), "value", None)
        allowed = _SIGNAL_COMPATIBILITY.get(point.quantity)
        if allowed is None:
            raise ValueError(
                f"{point.quantity.value} requires a dedicated Application producer/model adapter; "
                "it cannot be bound as a direct scalar channel."
            )
        if signal_type not in allowed:
            raise ValueError(
                f"{point.quantity.value} is incompatible with Core signal type {signal_type!r}."
            )
        core_unit = str(getattr(channel, "unit", "")).strip()
        if core_unit and point.unit and core_unit.casefold() != point.unit.casefold():
            raise ValueError(
                f"Unit mismatch for {point.channel_id}: plugin={point.unit!r}, Core={core_unit!r}."
            )
        seen_points.add(binding.telemetry_channel_id)
        seen_core_channels.add(binding.measurement_channel_id)
        resolved.append((point, channel))
    return tuple(resolved)


__all__ = [
    "TransformerMeasurementBinding",
    "resolve_transformer_measurement_bindings",
]
