"""Core-owned physical measurement source and interface value objects.

These records describe instrumentation independently of electrical Network
equipment and Terminal connectivity. They do not publish samples, resolve
project ownership, or provision MeasurementChannels.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_TOKEN = re.compile(r"[a-z][a-z0-9_.-]*\\Z")
_SIGNAL_KINDS = frozenset({"analog", "digital", "counter", "diagnostic"})


def _text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string.")
    if not value or value != value.strip():
        raise ValueError(f"{field} must be a canonical non-empty string.")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} cannot contain control characters.")
    return value


def _token(value: Any, field: str) -> str:
    result = _text(value, field)
    if not _TOKEN.fullmatch(result):
        raise ValueError(f"{field} must be a lowercase canonical token.")
    return result


def _finite_optional(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number or None.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number.")
    return number


@dataclass(frozen=True, slots=True)
class MeasurementSourceInterface:
    """One physical instrument output with an explicit engineering meaning."""

    interface_id: str
    quantity: str
    unit: str
    signal_kind: str
    phase: str | None = None
    winding_id: str | None = None
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "interface_id", _text(self.interface_id, "interface_id"))
        object.__setattr__(self, "quantity", _token(self.quantity, "quantity"))
        object.__setattr__(self, "unit", _text(self.unit, "unit"))
        kind = _token(self.signal_kind, "signal_kind")
        if kind not in _SIGNAL_KINDS:
            raise ValueError(f"Unsupported measurement source signal_kind: {kind!r}.")
        object.__setattr__(self, "signal_kind", kind)
        for field in ("phase", "winding_id"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, field))
        minimum = _finite_optional(self.minimum, "minimum")
        maximum = _finite_optional(self.maximum, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum cannot exceed maximum.")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "interface_id": self.interface_id, "quantity": self.quantity,
            "unit": self.unit, "signal_kind": self.signal_kind,
            "phase": self.phase, "winding_id": self.winding_id,
            "minimum": self.minimum, "maximum": self.maximum,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "MeasurementSourceInterface":
        if not isinstance(data, Mapping):
            raise TypeError("Measurement source interface must be a mapping.")
        expected = {"interface_id", "quantity", "unit", "signal_kind", "phase",
                    "winding_id", "minimum", "maximum"}
        if set(data) != expected:
            raise ValueError("Measurement source interface fields are not canonical.")
        return cls(**{key: data[key] for key in expected})


@dataclass(frozen=True, slots=True)
class PhysicalMeasurementSource:
    """Stable Core identity and declared interfaces of a physical instrument."""

    source_id: str
    source_family: str
    interfaces: tuple[MeasurementSourceInterface, ...]
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    calibration_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id"))
        object.__setattr__(self, "source_family", _token(self.source_family, "source_family"))
        for field in ("manufacturer", "model", "serial_number", "calibration_reference"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _text(value, field))
        interfaces = tuple(self.interfaces)
        if not interfaces or any(not isinstance(item, MeasurementSourceInterface) for item in interfaces):
            raise TypeError("A physical measurement source requires typed interfaces.")
        ids = [item.interface_id for item in interfaces]
        if len(ids) != len(set(ids)):
            raise ValueError("Physical measurement source contains duplicate interface IDs.")
        object.__setattr__(self, "interfaces", interfaces)

    def interface(self, interface_id: str) -> MeasurementSourceInterface:
        for item in self.interfaces:
            if item.interface_id == interface_id:
                return item
        raise KeyError(f"Measurement source {self.source_id!r} has no interface {interface_id!r}.")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "source_family": self.source_family,
            "manufacturer": self.manufacturer, "model": self.model,
            "serial_number": self.serial_number,
            "calibration_reference": self.calibration_reference,
            "interfaces": [item.to_mapping() for item in self.interfaces],
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PhysicalMeasurementSource":
        if not isinstance(data, Mapping):
            raise TypeError("Physical measurement source must be a mapping.")
        expected = {"source_id", "source_family", "manufacturer", "model",
                    "serial_number", "calibration_reference", "interfaces"}
        if set(data) != expected:
            raise ValueError("Physical measurement source fields are not canonical.")
        raw = data["interfaces"]
        if not isinstance(raw, (list, tuple)):
            raise TypeError("Physical measurement source interfaces must be an array.")
        return cls(
            source_id=data["source_id"], source_family=data["source_family"],
            manufacturer=data["manufacturer"], model=data["model"],
            serial_number=data["serial_number"],
            calibration_reference=data["calibration_reference"],
            interfaces=tuple(MeasurementSourceInterface.from_mapping(item) for item in raw),
        )


@dataclass(frozen=True, slots=True)
class MeasurementSourceInstallation:
    """Project-declared physical installation/monitoring association.

    monitored_point_id is a domain point such as a transformer winding ID;
    it is not a Core Terminal or electrical topology edge.
    """

    installation_id: str
    source_id: str
    interface_id: str
    monitored_equipment_id: str
    monitored_equipment_type: str
    monitored_point_id: str
    installation_location: str | None = None

    def __post_init__(self) -> None:
        for field in ("installation_id", "source_id", "interface_id",
                      "monitored_equipment_id", "monitored_point_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self, "monitored_equipment_type",
            _token(self.monitored_equipment_type, "monitored_equipment_type"),
        )
        if self.installation_location is not None:
            object.__setattr__(
                self, "installation_location",
                _text(self.installation_location, "installation_location"),
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id, "source_id": self.source_id,
            "interface_id": self.interface_id,
            "monitored_equipment_id": self.monitored_equipment_id,
            "monitored_equipment_type": self.monitored_equipment_type,
            "monitored_point_id": self.monitored_point_id,
            "installation_location": self.installation_location,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "MeasurementSourceInstallation":
        if not isinstance(data, Mapping):
            raise TypeError("Measurement source installation must be a mapping.")
        expected = {"installation_id", "source_id", "interface_id",
                    "monitored_equipment_id", "monitored_equipment_type",
                    "monitored_point_id", "installation_location"}
        if set(data) != expected:
            raise ValueError("Measurement source installation fields are not canonical.")
        return cls(**{key: data[key] for key in expected})


__all__ = [
    "MeasurementSourceInstallation",
    "MeasurementSourceInterface",
    "PhysicalMeasurementSource",
]
