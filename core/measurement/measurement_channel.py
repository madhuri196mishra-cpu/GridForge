# Author: Subhendu Mishra 
"""
GridForge Measurement Channel
=============================

File:
    core/measurement/measurement_channel.py

Purpose
-------
Defines the canonical GridForge V2 logical measurement-channel
abstraction.

A MeasurementChannel represents a logical signal made available by
the measurement architecture to consuming systems such as:

    - protection
    - metering
    - control
    - SCADA
    - synchronization
    - monitoring
    - analysis

Architectural position
----------------------

    Physical Instrument
        |
        +-- CT
        +-- PT
        +-- CVT
        |
        v
    Measurement Interface
        |
        v
    MeasurementChannel
        |
        +-- RelayInput
        +-- Metering
        +-- Control
        +-- SCADA
        +-- Monitoring
        +-- Analysis

The MeasurementChannel is a CORE GridForge V2 abstraction.

It represents the logical signal contract.

It does NOT model the physical instrument itself.

Physical equipment remains authoritative for:

    - equipment identity
    - nameplate data
    - ratio
    - burden
    - accuracy class
    - saturation characteristics
    - physical terminals
    - equipment state

Those responsibilities remain in the appropriate model/domain
layers.

Design principles
-----------------

1. A measurement channel owns the logical signal state.
2. Physical source equipment remains authoritative for physical
   equipment state.
3. A channel stores references to source objects; it does not copy
   their state.
4. Consumers must consume channels rather than directly reading
   CT/PT/CVT implementation details.
5. Measurement generation and transformation are separate from
   logical channel storage.
6. Protection must be able to determine whether a signal is usable
   without knowing how the physical instrument produced it.
7. The channel must support scalar measurements, phasors and future
   engineering signal types without coupling to a solver.
8. The channel must remain independent of:
       - protection
       - simulation
       - network topology
       - numerical solvers
       - GUI
       - breaker control

Value semantics
---------------

The channel distinguishes:

    raw_value
        Value supplied by the measurement-generation interface.

    engineering_value
        Value exposed to consumers after channel-level scaling and
        polarity.

The channel therefore has one unambiguous conversion:

    engineering_value =
        raw_value * scale * polarity

Measurement-generation plugins are responsible for physical
instrument transformations such as CT/PT/CVT ratios, burden,
saturation, filtering and instrument error.

The channel does not recreate those physical calculations.

Quality and availability
------------------------

These are intentionally separate.

    available
        Whether the signal path currently exists/is available.

    quality
        Whether the available value is considered reliable.

A signal may therefore be:

    available=True
    quality=INVALID

or:

    available=False
    quality=UNKNOWN

A consumer should normally use ``is_usable`` rather than assuming
that a numerical value is valid merely because one exists.

Future protection architecture
------------------------------

Protection functions consume MeasurementChannel through RelayInput:

    CT/PT/CVT
        |
        v
    MeasurementChannel
        |
        v
    RelayInput
        |
        v
    RelayBase
        |
        +-- Overcurrent
        +-- Directional
        +-- Distance
        +-- Differential
        +-- Voltage
        +-- Frequency
        +-- future functions

MeasurementChannel must therefore remain a stable low-level
contract.

Copyright © 2026 Subhendu Mishra
All Rights Reserved.
Proprietary and confidential.
"""

from __future__ import annotations

from enum import Enum
from math import isfinite
from typing import Any, Optional

from core.model import EndpointReference


# =====================================================================
# SIGNAL TYPE
# =====================================================================


class MeasurementSignalType(Enum):
    """
    Canonical GridForge measurement signal classifications.
    """

    CURRENT = "CURRENT"
    VOLTAGE = "VOLTAGE"
    POWER = "POWER"
    FREQUENCY = "FREQUENCY"
    PHASE_ANGLE = "PHASE_ANGLE"
    IMPEDANCE = "IMPEDANCE"
    DIGITAL = "DIGITAL"
    TEMPERATURE = "TEMPERATURE"
    CUSTOM = "CUSTOM"


# =====================================================================
# PHASE / SEQUENCE
# =====================================================================


class MeasurementPhase(Enum):
    """
    Phase or sequence designation for a measurement signal.
    """

    NONE = "NONE"

    A = "A"
    B = "B"
    C = "C"
    N = "N"

    AB = "AB"
    BC = "BC"
    CA = "CA"

    THREE_PHASE = "THREE_PHASE"

    POSITIVE_SEQUENCE = "POSITIVE_SEQUENCE"
    NEGATIVE_SEQUENCE = "NEGATIVE_SEQUENCE"
    ZERO_SEQUENCE = "ZERO_SEQUENCE"


# =====================================================================
# QUALITY
# =====================================================================


class MeasurementQuality(Enum):
    """
    Logical signal-quality state.

    GOOD
        Signal is valid for normal engineering consumption.

    SUSPECT
        Signal exists but may not be sufficiently reliable for all
        consumers.

    INVALID
        Signal is explicitly invalid.

    UNKNOWN
        Signal quality has not been established.
    """

    GOOD = "GOOD"
    SUSPECT = "SUSPECT"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


# =====================================================================
# VALIDITY REASON
# =====================================================================


class MeasurementValidity(Enum):
    """
    Canonical diagnostic reason for measurement usability.

    This is deliberately separate from MeasurementQuality.

    Quality describes the signal condition.

    Validity describes why a consumer can or cannot use it.
    """

    VALID = "VALID"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_QUALITY = "INVALID_QUALITY"
    SUSPECT_QUALITY = "SUSPECT_QUALITY"
    UNKNOWN_QUALITY = "UNKNOWN_QUALITY"
    STALE = "STALE"
    NONFINITE = "NONFINITE"


# =====================================================================
# MEASUREMENT CHANNEL
# =====================================================================


class MeasurementChannel:
    """
    Canonical GridForge V2 logical measurement channel.

    Parameters
    ----------
    id:
        Globally unique logical channel identifier.

    signal_type:
        MeasurementSignalType describing the signal domain.

    name:
        Human-readable channel name.

    source:
        Physical measurement source reference, typically CT/PT/CVT
        or another measurement-producing interface.

    source_terminal:
        Optional source-side measurement terminal/interface.

    phase:
        Phase or sequence designation.

    unit:
        Engineering unit exposed by the channel.

    nominal_value:
        Nominal/reference engineering magnitude.

    scale:
        Channel-level conversion multiplier.

    polarity:
        +1.0 for normal polarity or -1.0 for reversed polarity.

    available:
        Whether the logical signal path is currently available.

    quality:
        Current signal-quality classification.

    raw_value:
        Value supplied by the measurement-generation layer.

    timestamp:
        Optional simulation/event/sample timestamp.

    sample_sequence:
        Optional monotonically increasing sample/update identifier.

    stale_after:
        Optional maximum permitted signal age in the same time units
        as timestamp.

    Notes
    -----
    The channel stores references to physical sources.

    It does not own or duplicate source-equipment state.

    The channel is not a measurement simulator.
    """

    # =================================================================
    # INITIALIZATION
    # =================================================================

    def __init__(
        self,
        id: str,
        signal_type: MeasurementSignalType,
        name: str = "",
        source: Any = None,
        source_terminal: Any = None,
        phase: MeasurementPhase = MeasurementPhase.NONE,
        unit: str = "",
        nominal_value: float = 0.0,
        scale: float = 1.0,
        polarity: float = 1.0,
        available: bool = True,
        quality: MeasurementQuality = MeasurementQuality.UNKNOWN,
        raw_value: float | complex = 0.0,
        timestamp: Optional[float] = None,
        timestamp_domain: Optional[str] = None,
        sample_sequence: Optional[int] = None,
        stale_after: Optional[float] = None,
        source_reference: Any = None,
        source_installation_id: Optional[str] = None,
    ) -> None:

        self._validate_id(id)

        self.id = id
        self.name = str(name)
        self._sample_update_authority = None
        self.producer_id = None

        if not isinstance(
            signal_type,
            MeasurementSignalType,
        ):
            raise TypeError(
                "signal_type must be a MeasurementSignalType."
            )

        self.signal_type = signal_type

        if not isinstance(
            phase,
            MeasurementPhase,
        ):
            raise TypeError(
                "phase must be a MeasurementPhase."
            )

        self.phase = phase

        if source is not None:
            self._validate_reference(
                source,
                "source",
            )

        if source_terminal is not None:
            if not isinstance(source_terminal, EndpointReference) or not source_terminal.is_terminal:
                raise TypeError("source_terminal must be a canonical terminal EndpointReference.")
        if source_reference is not None:
            from core.measurement.measurement_source_reference import MeasurementSourceReference
            if not isinstance(source_reference, MeasurementSourceReference):
                raise TypeError("source_reference must be a MeasurementSourceReference.")
            if source_terminal is not None:
                raise ValueError("A channel cannot use both an electrical terminal and a physical source interface.")
            if source is None or getattr(source, "source_id", None) != source_reference.source_id:
                raise ValueError("Physical source reference identity must match the supplied source.")
            if getattr(source, "source_family", None) != source_reference.source_family:
                raise ValueError("Physical source family must match the supplied source reference.")
            if not isinstance(source_installation_id, str) or not source_installation_id.strip() or source_installation_id != source_installation_id.strip():
                raise ValueError("Physical source channels require a canonical source_installation_id.")
        elif source_installation_id is not None:
            raise ValueError("source_installation_id requires a physical source_reference.")

        self.source = source
        self.source_terminal = source_terminal
        self.source_reference = source_reference
        self.source_installation_id = source_installation_id

        if not isinstance(unit, str):
            raise TypeError(
                "unit must be a string."
            )

        self.unit = unit.strip()

        self.nominal_value = float(
            nominal_value
        )

        self.scale = float(
            scale
        )

        self.polarity = float(
            polarity
        )

        self._validate_engineering_configuration()

        if not isinstance(
            quality,
            MeasurementQuality,
        ):
            raise TypeError(
                "quality must be a MeasurementQuality."
            )

        self.available = bool(
            available
        )

        self.quality = quality

        self.raw_value = self._validate_value(
            raw_value
        )

        self.timestamp = self._validate_timestamp(
            timestamp
        )
        self.timestamp_domain = self._validate_timestamp_domain(timestamp_domain)

        self.sample_sequence = (
            self._validate_sample_sequence(
                sample_sequence
            )
        )

        self.stale_after = self._validate_stale_after(
            stale_after
        )

    _SAMPLE_STATE_FIELDS = frozenset({
        "raw_value", "timestamp", "timestamp_domain",
        "sample_sequence", "quality", "available",
    })
    # A project-managed channel's physical source and engineering contract
    # must not be mutable through the channel object exposed to consumers.
    _CONFIGURATION_FIELDS = frozenset({
        "id", "signal_type", "source", "source_terminal", "source_reference", "source_installation_id", "phase", "unit",
        "nominal_value", "scale", "polarity", "stale_after", "producer_id",
    })

    def __setattr__(self, name: str, value: Any) -> None:
        authority = getattr(self, "_sample_update_authority", None)
        if authority is not None and name in self._SAMPLE_STATE_FIELDS:
            write_authority = getattr(self, "_sample_write_authority", None)
            if write_authority is not authority:
                raise PermissionError(
                    "Project-managed measurement sample state is writable only through MeasurementChannelService."
                )
        if authority is not None and name in self._CONFIGURATION_FIELDS:
            raise PermissionError(
                "Project-managed measurement channel configuration is immutable after activation."
            )
        object.__setattr__(self, name, value)

    def _bind_sample_update_authority(self, authority: Any) -> None:
        if authority is None:
            raise ValueError("A non-null sample update authority is required.")
        if self._sample_update_authority not in (None, authority):
            raise RuntimeError("MeasurementChannel is already owned by another sample service.")
        self._sample_update_authority = authority

    # =================================================================
    # VALIDATION
    # =================================================================

    @staticmethod
    def _validate_id(
        value: str,
    ) -> None:
        """
        Validate channel identity.
        """

        if not isinstance(
            value,
            str,
        ):
            raise TypeError(
                "MeasurementChannel id must be a string."
            )

        if not value.strip():
            raise ValueError(
                "MeasurementChannel id cannot be empty."
            )

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_reference(
        value: Any,
        field_name: str,
    ) -> None:
        """
        Validate a referenced GridForge object.

        The channel intentionally does not impose a concrete source
        equipment class.
        """

        if not hasattr(
            value,
            "id",
        ):
            raise TypeError(
                f"{field_name} must expose an 'id' attribute."
            )

        object_id = getattr(
            value,
            "id",
        )

        if not isinstance(
            object_id,
            str,
        ):
            raise TypeError(
                f"{field_name}.id must be a string."
            )

        if not object_id.strip():
            raise ValueError(
                f"{field_name}.id cannot be empty."
            )

    # -----------------------------------------------------------------

    def _validate_engineering_configuration(
        self,
    ) -> None:
        """
        Validate engineering configuration.
        """

        if (
            not isfinite(
                self.nominal_value
            )
            or self.nominal_value < 0.0
        ):
            raise ValueError(
                "nominal_value must be finite and non-negative."
            )

        if not isfinite(
            self.scale
        ):
            raise ValueError(
                "scale must be finite."
            )

        if (
            not isfinite(
                self.polarity
            )
            or self.polarity not in (
                -1.0,
                1.0,
            )
        ):
            raise ValueError(
                "polarity must be either +1.0 or -1.0."
            )

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_value(
        value: float | complex,
    ) -> float | complex:
        """
        Validate a scalar or complex measurement value.
        """

        if isinstance(
            value,
            bool,
        ):
            raise TypeError(
                "Measurement value cannot be bool."
            )

        if isinstance(
            value,
            complex,
        ):

            if (
                not isfinite(value.real)
                or not isfinite(value.imag)
            ):
                raise ValueError(
                    "Complex measurement value must be finite."
                )

            return value

        try:
            value = float(value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise TypeError(
                "Measurement value must be numeric."
            ) from exc

        if not isfinite(value):
            raise ValueError(
                "Measurement value must be finite."
            )

        return value

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_timestamp(
        timestamp: Optional[float],
    ) -> Optional[float]:
        """
        Validate an optional timestamp.
        """

        if timestamp is None:
            return None

        timestamp = float(
            timestamp
        )

        if not isfinite(timestamp):
            raise ValueError(
                "timestamp must be finite."
            )

        return timestamp

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_timestamp_domain(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("timestamp_domain must be a canonical non-empty string.")
        return value

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_sample_sequence(
        sequence: Optional[int],
    ) -> Optional[int]:
        """
        Validate an optional sample/update sequence number.
        """

        if sequence is None:
            return None

        if isinstance(
            sequence,
            bool,
        ):
            raise TypeError(
                "sample_sequence cannot be bool."
            )

        sequence = int(
            sequence
        )

        if sequence < 0:
            raise ValueError(
                "sample_sequence cannot be negative."
            )

        return sequence

    # -----------------------------------------------------------------

    @staticmethod
    def _validate_stale_after(
        value: Optional[float],
    ) -> Optional[float]:
        """
        Validate optional signal-staleness threshold.
        """

        if value is None:
            return None

        value = float(
            value
        )

        if (
            not isfinite(value)
            or value < 0.0
        ):
            raise ValueError(
                "stale_after must be finite and >= 0."
            )

        return value

    # =================================================================
    # SOURCE ASSOCIATION
    # =================================================================

    def set_source(
        self,
        source: Any,
    ) -> None:
        """
        Associate the channel with a measurement source.

        The source itself is not modified.
        """

        self._validate_reference(
            source,
            "source",
        )

        self.source = source

    # -----------------------------------------------------------------

    def set_source_terminal(
        self,
        terminal: Any,
    ) -> None:
        """
        Associate the channel with a source measurement terminal.
        """

        if not isinstance(terminal, EndpointReference) or not terminal.is_terminal:
            raise TypeError(
                "source_terminal must be a canonical terminal EndpointReference."
            )

        self.source_terminal = terminal

    # =================================================================
    # VALUE ACCESS
    # =================================================================

    @property
    def value(
        self,
    ) -> float | complex:
        """
        Return the raw channel value.

        ``value`` is retained as a compatibility alias for
        ``raw_value``.
        """

        return self.raw_value

    # -----------------------------------------------------------------

    @value.setter
    def value(
        self,
        value: float | complex,
    ) -> None:
        self.raw_value = self._validate_value(
            value
        )

    # -----------------------------------------------------------------

    @property
    def engineering_value(
        self,
    ) -> float | complex:
        """
        Return the consumer-facing engineering value.

        Conversion:

            engineering_value =
                raw_value * scale * polarity
        """

        return (
            self.raw_value
            * self.scale
            * self.polarity
        )

    # -----------------------------------------------------------------

    @property
    def signal(
        self,
    ) -> float | complex:
        """
        Return the current engineering signal.

        This property is intentionally convenient for RelayInput and
        other consumers.
        """

        return self.engineering_value

    # =================================================================
    # MEASUREMENT UPDATE
    # =================================================================

    def update(
        self,
        value: float | complex,
        *,
        _authority: Any = None,
        timestamp: Optional[float] = None,
        timestamp_domain: Optional[str] = None,
        quality: Optional[MeasurementQuality] = None,
        available: Optional[bool] = None,
        sample_sequence: Optional[int] = None,
    ) -> None:
        """Update one sample; project-managed channels require their service capability."""
        authority = self._sample_update_authority
        if authority is not None and _authority is not authority:
            raise PermissionError(
                "Project-managed MeasurementChannels must be updated through MeasurementChannelService.publish_sample()."
            )
        validated_value = self._validate_value(value)
        validated_timestamp = self._validate_timestamp(timestamp) if timestamp is not None else None
        validated_domain = self._validate_timestamp_domain(timestamp_domain) if timestamp is not None else None
        if authority is not None and (
            timestamp is None or validated_domain is None or sample_sequence is None
        ):
            raise ValueError(
                "Project-managed samples require timestamp, timestamp_domain and sample_sequence."
            )
        if quality is not None and not isinstance(quality, MeasurementQuality):
            raise TypeError("quality must be a MeasurementQuality.")
        validated_sequence = self._validate_sample_sequence(sample_sequence) if sample_sequence is not None else None
        self._sample_write_authority = authority
        try:
            self.raw_value = validated_value
            if timestamp is not None:
                self.timestamp = validated_timestamp
                self.timestamp_domain = validated_domain
            if quality is not None:
                self.quality = quality
            if available is not None:
                self.available = bool(available)
            if sample_sequence is not None:
                self.sample_sequence = validated_sequence
        finally:
            self._sample_write_authority = None

    # =================================================================
    # SIGNAL QUALITY / VALIDITY
    # =================================================================

    def validity(
        self,
        *,
        current_time: Optional[float] = None,
    ) -> MeasurementValidity:
        """
        Determine the current logical validity state.

        ``current_time`` is optional because the channel does not
        own a simulation clock.
        """

        if not self.available:
            return MeasurementValidity.UNAVAILABLE

        if (
            self.quality
            == MeasurementQuality.INVALID
        ):
            return MeasurementValidity.INVALID_QUALITY

        if (
            self.quality
            == MeasurementQuality.SUSPECT
        ):
            return MeasurementValidity.SUSPECT_QUALITY

        if (
            self.quality
            == MeasurementQuality.UNKNOWN
        ):
            return MeasurementValidity.UNKNOWN_QUALITY

        if (
            current_time is not None
            and self.stale_after is not None
            and self.timestamp is not None
        ):

            current_time = float(
                current_time
            )

            if not isfinite(
                current_time
            ):
                raise ValueError(
                    "current_time must be finite."
                )

            age = (
                current_time
                - self.timestamp
            )

            if age > self.stale_after:
                return MeasurementValidity.STALE

        return MeasurementValidity.VALID

    # -----------------------------------------------------------------

    def is_valid_at(
        self,
        current_time: Optional[float] = None,
    ) -> bool:
        """
        Return whether the channel is valid at the supplied time.
        """

        return (
            self.validity(
                current_time=current_time
            )
            == MeasurementValidity.VALID
        )

    # -----------------------------------------------------------------

    @property
    def is_valid(
        self,
    ) -> bool:
        """
        Return basic signal validity without staleness evaluation.
        """

        return (
            self.validity()
            == MeasurementValidity.VALID
        )

    # -----------------------------------------------------------------

    @property
    def is_usable(
        self,
    ) -> bool:
        """
        Consumer-facing alias for basic signal validity.
        """

        return self.is_valid

    # =================================================================
    # AVAILABILITY
    # =================================================================

    def set_available(
        self,
        available: bool,
    ) -> None:
        """
        Set logical signal availability.
        """

        self.available = bool(
            available
        )

    # -----------------------------------------------------------------

    def set_quality(
        self,
        quality: MeasurementQuality,
    ) -> None:
        """
        Set signal quality.
        """

        if not isinstance(
            quality,
            MeasurementQuality,
        ):
            raise TypeError(
                "quality must be a MeasurementQuality."
            )

        self.quality = quality

    # =================================================================
    # SOURCE INFORMATION
    # =================================================================

    @property
    def source_id(
        self,
    ) -> str | None:
        """
        Return the source equipment identifier.
        """

        if self.source is None:
            return None

        return getattr(self.source, "id", getattr(self.source, "source_id", None))

    # -----------------------------------------------------------------

    @property
    def source_terminal_id(
        self,
    ) -> str | None:
        """
        Return the source-terminal identifier.
        """

        if self.source_terminal is None:
            return None

        return str(self.source_terminal)

    # =================================================================
    # SIGNAL INFORMATION
    # =================================================================

    @property
    def signal_name(
        self,
    ) -> str:
        """
        Return the canonical signal type identifier.
        """

        return self.signal_type.value

    # =================================================================
    # RESET
    # =================================================================

    def reset(
        self,
        value: float | complex = 0.0,
        *,
        quality: MeasurementQuality = (
            MeasurementQuality.UNKNOWN
        ),
        available: bool = True,
        timestamp: Optional[float] = None,
        sample_sequence: Optional[int] = None,
    ) -> None:
        """
        Reset signal state while retaining channel configuration.
        """

        self.raw_value = self._validate_value(
            value
        )

        if not isinstance(
            quality,
            MeasurementQuality,
        ):
            raise TypeError(
                "quality must be a MeasurementQuality."
            )

        self.quality = quality
        self.available = bool(
            available
        )

        self.timestamp = self._validate_timestamp(
            timestamp
        )

        self.sample_sequence = (
            self._validate_sample_sequence(
                sample_sequence
            )
        )

    # =================================================================
    # DIAGNOSTICS
    # =================================================================

    def diagnostics(
        self,
        *,
        current_time: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Return structured measurement diagnostics.
        """

        validity = self.validity(
            current_time=current_time
        )

        return {
            "id": self.id,
            "name": self.name,
            "signal_type": self.signal_type.value,
            "phase": self.phase.value,
            "unit": self.unit,
            "nominal_value": self.nominal_value,
            "scale": self.scale,
            "polarity": self.polarity,
            "raw_value": self.raw_value,
            "engineering_value": self.engineering_value,
            "available": self.available,
            "quality": self.quality.value,
            "validity": validity.value,
            "usable": (
                validity
                == MeasurementValidity.VALID
            ),
            "timestamp": self.timestamp,
            "sample_sequence": self.sample_sequence,
            "stale_after": self.stale_after,
            "source": self.source_id,
            "source_terminal": self.source_terminal_id,
        }

    # -----------------------------------------------------------------

    def summary(
        self,
    ) -> dict[str, Any]:
        """
        Return channel summary.

        This method intentionally excludes time-relative staleness.
        """

        return self.diagnostics()

    # =================================================================
    # REPRESENTATION
    # =================================================================

    def __repr__(
        self,
    ) -> str:
        """
        Return a concise developer-facing representation.
        """

        return (
            f"<MeasurementChannel "
            f"id={self.id!r}, "
            f"type={self.signal_type.value}, "
            f"phase={self.phase.value}, "
            f"value={self.engineering_value!r}, "
            f"quality={self.quality.value}, "
            f"available={self.available}>"
        )


# =====================================================================
# PUBLIC API
# =====================================================================


__all__ = [
    "MeasurementSignalType",
    "MeasurementPhase",
    "MeasurementQuality",
    "MeasurementValidity",
    "MeasurementChannel",
]

