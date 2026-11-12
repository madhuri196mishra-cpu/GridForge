# Author: Subhendu Mishra
"""Canonical measurement-channel provisioning boundary.

Application resolves the configured physical secondary terminal. Core
validates that the terminal belongs to the instrument and is a secondary
terminal before constructing the logical MeasurementChannel.
"""
from __future__ import annotations

from typing import Any

from core.model import EndpointReference
from core.measurement.measurement_channel import MeasurementChannel, MeasurementSignalType
from core.measurement.measurement_source import PhysicalMeasurementSource
from core.measurement.measurement_source_reference import MeasurementSourceReference


_SECONDARY_TERMINAL_ROLES = {
    "CT": frozenset({"S1", "S2"}),
    "PT": frozenset({"secondary_a", "secondary_b"}),
    "CVT": frozenset({"X1", "X2"}),
}


class MeasurementProvisioning:
    """Create logical channels from explicitly configured instrument terminals."""

    @staticmethod
    def provision_channel(
        *,
        source: Any,
        source_terminal: Any,
        source_terminal_reference: EndpointReference,
        channel_id: str,
        signal_type: MeasurementSignalType,
        name: str = "",
        **channel_kwargs: Any,
    ) -> MeasurementChannel:
        """Provision one channel from an authoritative physical secondary terminal."""
        if not isinstance(source_terminal_reference, EndpointReference):
            raise TypeError("source_terminal_reference must be an EndpointReference.")
        if not source_terminal_reference.is_terminal:
            raise ValueError("source_terminal_reference must identify a terminal.")
        if source is None or not hasattr(source, "id"):
            raise TypeError("source must expose a stable id.")
        source_id = getattr(source, "id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source.id must be a non-empty string.")
        if source_terminal_reference.equipment_id != source_id.strip():
            raise ValueError("source_terminal_reference equipment identity must match source.id.")
        if source_terminal is None:
            raise TypeError("source_terminal must be an authoritative Core Terminal.")
        if getattr(source_terminal, "owner", None) is not source:
            raise ValueError("source_terminal does not belong to the supplied source equipment.")
        terminal_role = getattr(source_terminal, "role", None)
        if terminal_role != source_terminal_reference.terminal_role:
            raise ValueError("source_terminal role must match source_terminal_reference.terminal_role.")

        source_type = str(getattr(source, "element_type", type(source).__name__)).strip().upper()
        expected_roles = _SECONDARY_TERMINAL_ROLES.get(source_type)
        if expected_roles is not None and terminal_role not in expected_roles:
            raise ValueError(
                f"{source_type} measurement channels must be provisioned from a secondary terminal; "
                f"received {terminal_role!r}."
            )

        return MeasurementChannel(
            id=channel_id,
            signal_type=signal_type,
            name=name,
            source=source,
            source_terminal=source_terminal_reference,
            **channel_kwargs,
        )


    @staticmethod
    def provision_physical_source_channel(
        *,
        source: PhysicalMeasurementSource,
        source_reference: MeasurementSourceReference,
        source_installation_id: str,
        signal_type: MeasurementSignalType,
        channel_id: str,
        name: str = "",
        **channel_kwargs: Any,
    ) -> MeasurementChannel:
        """Provision a channel from a project-resolved physical source interface.

        Unlike terminal-backed provisioning, this path deliberately carries no
        electrical Terminal or EndpointReference. Application must resolve and
        validate the source installation against the active project before
        calling this Core constructor.
        """
        if not isinstance(source, PhysicalMeasurementSource):
            raise TypeError("source must be a PhysicalMeasurementSource.")
        if not isinstance(source_reference, MeasurementSourceReference):
            raise TypeError("source_reference must be a MeasurementSourceReference.")
        if source.source_id != source_reference.source_id:
            raise ValueError("Physical source identity does not match its source reference.")
        if source.source_family != source_reference.source_family:
            raise ValueError("Physical source family does not match its source reference.")
        interface = source.interface(source_reference.interface_id)
        if not isinstance(source_installation_id, str) or not source_installation_id.strip() or source_installation_id != source_installation_id.strip():
            raise ValueError("source_installation_id must be a canonical non-empty string.")
        if not isinstance(signal_type, MeasurementSignalType):
            raise TypeError("signal_type must be a MeasurementSignalType.")
        unit = channel_kwargs.get("unit")
        if not isinstance(unit, str) or unit.strip().casefold() != interface.unit.casefold():
            raise ValueError("Measurement channel unit must match the physical source interface unit.")
        return MeasurementChannel(
            id=channel_id,
            signal_type=signal_type,
            name=name,
            source=source,
            source_terminal=None,
            source_reference=source_reference,
            source_installation_id=source_installation_id,
            **channel_kwargs,
        )


__all__ = ["MeasurementProvisioning"]
