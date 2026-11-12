"""RTD producer normalization and commissioning-evidence contract.

This module accepts compensated resistance observations from an external
acquisition driver. It performs IEC 60751 platinum RTD conversion and emits
ordinary transformer TelemetrySample values. It does not open hardware
connections, authenticate producers, issue signatures, or publish Core state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Iterable

from core.application.instrumentation import ProjectInstrumentationCollection

from .telemetry import (
    ChannelDefinition,
    MeasurementQuality,
    TelemetrySample,
    TransformerQuantity,
)


_IEC60751_A = 3.9083e-3
_IEC60751_B = -5.775e-7
_IEC60751_C = -4.183e-12
_MIN_TEMPERATURE_C = -200.0
_MAX_TEMPERATURE_C = 850.0


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime.")


@dataclass(frozen=True, slots=True)
class RTDConfiguration:
    """Declared RTD identity and winding association for one Core interface."""

    source_id: str
    interface_id: str
    installation_id: str
    equipment_id: str
    winding_id: str
    nominal_resistance_ohms: int = 100
    wiring: str = "4-wire"
    lead_compensation_verified: bool = False

    def __post_init__(self) -> None:
        for field in ("source_id", "interface_id", "installation_id", "equipment_id", "winding_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{field} must be a canonical non-empty string.")
        if self.nominal_resistance_ohms not in (100, 1000):
            raise ValueError("Only IEC 60751 Pt100 and Pt1000 nominal elements are supported.")
        if self.wiring not in {"2-wire", "3-wire", "4-wire"}:
            raise ValueError("RTD wiring must be 2-wire, 3-wire, or 4-wire.")
        if not isinstance(self.lead_compensation_verified, bool):
            raise TypeError("lead_compensation_verified must be bool.")


@dataclass(frozen=True, slots=True)
class RTDCommissioningEvidence:
    """External commissioning record reference bound to the installed RTD."""

    commissioning_record_id: str
    source_id: str
    installation_id: str
    calibration_reference: str
    verified_by: str
    commissioned_at: datetime
    calibration_valid_until: datetime

    def __post_init__(self) -> None:
        for field in (
            "commissioning_record_id", "source_id", "installation_id",
            "calibration_reference", "verified_by",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{field} must be a canonical non-empty string.")
        _aware(self.commissioned_at, "commissioned_at")
        _aware(self.calibration_valid_until, "calibration_valid_until")
        if self.calibration_valid_until <= self.commissioned_at:
            raise ValueError("Calibration validity must extend beyond commissioning.")


@dataclass(frozen=True, slots=True)
class RTDObservation:
    """One compensated RTD element-resistance observation from a driver."""

    resistance_ohms: float
    timestamp: datetime
    sequence: int
    quality: MeasurementQuality = MeasurementQuality.GOOD
    diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.resistance_ohms, bool) or not isinstance(self.resistance_ohms, (int, float)):
            raise TypeError("resistance_ohms must be numeric.")
        if not isfinite(self.resistance_ohms) or self.resistance_ohms <= 0:
            raise ValueError("resistance_ohms must be finite and positive.")
        _aware(self.timestamp, "timestamp")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer.")
        if not isinstance(self.quality, MeasurementQuality):
            raise TypeError("quality must be MeasurementQuality.")
        codes = tuple(self.diagnostic_codes)
        if any(not isinstance(code, str) or not code.strip() or code != code.strip() for code in codes):
            raise ValueError("diagnostic_codes must contain canonical non-empty strings.")
        if len(set(codes)) != len(codes):
            raise ValueError("diagnostic_codes cannot contain duplicates.")
        object.__setattr__(self, "diagnostic_codes", codes)


def resistance_to_iec60751_temperature(
    resistance_ohms: float,
    *,
    nominal_resistance_ohms: int = 100,
) -> float:
    """Convert compensated Pt100/Pt1000 resistance using IEC 60751 CVD.

    Negative temperatures are solved by bounded bisection. Input resistance
    must already be compensated for lead resistance by the acquisition driver.
    """
    if isinstance(resistance_ohms, bool) or not isinstance(resistance_ohms, (int, float)):
        raise TypeError("resistance_ohms must be numeric.")
    if not isfinite(resistance_ohms) or resistance_ohms <= 0:
        raise ValueError("resistance_ohms must be finite and positive.")
    if nominal_resistance_ohms not in (100, 1000):
        raise ValueError("nominal_resistance_ohms must be 100 or 1000.")
    r0 = float(nominal_resistance_ohms)
    ratio = float(resistance_ohms) / r0

    def cvd(temperature: float) -> float:
        base = 1.0 + _IEC60751_A * temperature + _IEC60751_B * temperature * temperature
        if temperature < 0:
            base += _IEC60751_C * (temperature - 100.0) * temperature ** 3
        return base

    low_ratio = cvd(_MIN_TEMPERATURE_C)
    high_ratio = cvd(_MAX_TEMPERATURE_C)
    if ratio < low_ratio or ratio > high_ratio:
        raise ValueError("RTD resistance is outside the IEC 60751 -200 to 850 degC range.")
    if ratio >= 1.0:
        discriminant = _IEC60751_A ** 2 - 4.0 * _IEC60751_B * (1.0 - ratio)
        if discriminant < 0:
            raise ValueError("RTD resistance cannot be converted using IEC 60751 coefficients.")
        temperature = (-_IEC60751_A + discriminant ** 0.5) / (2.0 * _IEC60751_B)
        if not _MIN_TEMPERATURE_C <= temperature <= _MAX_TEMPERATURE_C:
            raise ValueError("Converted RTD temperature is outside the IEC 60751 range.")
        return temperature

    low, high = _MIN_TEMPERATURE_C, 0.0
    for _ in range(80):
        middle = (low + high) / 2.0
        if cvd(middle) < ratio:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def adapt_rtd_observation(
    configuration: RTDConfiguration,
    observation: RTDObservation,
    catalogue: Iterable[ChannelDefinition],
    instrumentation: ProjectInstrumentationCollection,
    commissioning: RTDCommissioningEvidence,
) -> TelemetrySample:
    """Validate the commissioned Core installation and emit a normalized sample."""
    if not isinstance(configuration, RTDConfiguration):
        raise TypeError("configuration must be RTDConfiguration.")
    if not isinstance(observation, RTDObservation):
        raise TypeError("observation must be RTDObservation.")
    if not isinstance(instrumentation, ProjectInstrumentationCollection):
        raise TypeError("instrumentation must be ProjectInstrumentationCollection.")
    if not isinstance(commissioning, RTDCommissioningEvidence):
        raise TypeError("commissioning must be RTDCommissioningEvidence.")

    points = tuple(catalogue)
    matching = [point for point in points if point.channel_id == configuration.interface_id]
    if len(matching) != 1:
        raise ValueError("RTD interface must resolve to exactly one transformer telemetry catalogue point.")
    point = matching[0]
    if (
        point.equipment_id != configuration.equipment_id
        or point.quantity is not TransformerQuantity.WINDING_TEMPERATURE
        or point.winding_id != configuration.winding_id
        or point.signal_kind.value != "analog"
    ):
        raise ValueError("RTD configuration does not match a winding-temperature analog catalogue point.")
    if point.unit.casefold() != "degc":
        raise ValueError("RTD winding-temperature catalogue point must use degC.")
    if not configuration.lead_compensation_verified:
        raise ValueError("RTD lead-resistance compensation must be verified during commissioning.")

    source = instrumentation.source(configuration.source_id)
    installation = next(
        (item for item in instrumentation.installations
         if item.installation_id == configuration.installation_id),
        None,
    )
    if installation is None:
        raise ValueError("RTD installation is not present in the active project instrumentation.")
    interface = source.interface(configuration.interface_id)
    if (
        source.source_family != "rtd"
        or not source.serial_number
        or not source.calibration_reference
        or installation.source_id != configuration.source_id
        or installation.interface_id != configuration.interface_id
        or installation.monitored_equipment_id != configuration.equipment_id
        or installation.monitored_equipment_type != "transformer"
        or installation.monitored_point_id != configuration.winding_id
        or interface.quantity != TransformerQuantity.WINDING_TEMPERATURE.value
        or interface.signal_kind != "analog"
        or interface.unit.casefold() != "degc"
        or interface.winding_id != configuration.winding_id
    ):
        raise ValueError("RTD source, interface, serial identity, or installation does not match the declared winding.")
    if (
        commissioning.source_id != source.source_id
        or commissioning.installation_id != installation.installation_id
        or commissioning.calibration_reference != source.calibration_reference
    ):
        raise ValueError("RTD commissioning evidence does not match the installed source and Core calibration reference.")
    if not commissioning.commissioned_at <= observation.timestamp <= commissioning.calibration_valid_until:
        raise ValueError("RTD observation falls outside the commissioned calibration-validity interval.")

    temperature = resistance_to_iec60751_temperature(
        observation.resistance_ohms,
        nominal_resistance_ohms=configuration.nominal_resistance_ohms,
    )
    if point.minimum is not None and temperature < point.minimum:
        raise ValueError("Converted RTD temperature is below the catalogue engineering range.")
    if point.maximum is not None and temperature > point.maximum:
        raise ValueError("Converted RTD temperature exceeds the catalogue engineering range.")
    return TelemetrySample(
        channel_id=point.channel_id,
        equipment_id=point.equipment_id,
        value=temperature,
        timestamp=observation.timestamp,
        quality=observation.quality,
        source=source.source_id,
        sequence=observation.sequence,
        unit=point.unit,
        diagnostic_codes=observation.diagnostic_codes,
    )


__all__ = [
    "RTDConfiguration",
    "RTDCommissioningEvidence",
    "RTDObservation",
    "resistance_to_iec60751_temperature",
    "adapt_rtd_observation",
]
