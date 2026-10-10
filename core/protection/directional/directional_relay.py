"""
GridForge Directional Protection Function
=========================================

File:
    core/protection/directional/directional_relay.py

Purpose
-------
Implements the baseline directional overcurrent protection function
for GridForge V2.

The function combines:

    1. Current pickup.
    2. Polarizing voltage/current angular relationship.
    3. Forward/reverse discrimination.
    4. ProtectionDecision generation.

Architectural Position
----------------------

    CT / PT / CVT
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
          v
    DirectionalRelay
          |
          v
    ProtectionDecision
          |
          v
    ProtectionSystem
          |
          v
    Protection Output Layer
          |
          v
      BreakerManager

Important V2 Principle
----------------------
This class represents ONE directional protection function / element.

It is not the physical Relay model.

A physical GridForge Relay may contain multiple protection
functions, for example:

    50/51    Overcurrent
    67       Directional overcurrent
    21       Distance
    50BF     Breaker failure

Each function is independently executable and may share the same
authoritative MeasurementChannel / RelayInput infrastructure.

Responsibilities
----------------
This module is responsible for:

    - consuming a configured current RelayInput;
    - validating current magnitude;
    - evaluating current pickup;
    - evaluating directional discrimination;
    - determining forward/reverse direction;
    - producing ProtectionDecision objects;
    - maintaining algorithm-specific diagnostic state.

It does NOT:

    - create CTs/PTs/CVTs;
    - create MeasurementChannels;
    - calculate fault current;
    - access Network topology;
    - perform load flow;
    - perform short-circuit calculations;
    - coordinate multiple protection functions;
    - operate breakers;
    - modify the authoritative Relay model;
    - schedule protection events;
    - own a simulation clock.

Directional Evaluation
----------------------
The baseline directional criterion uses:

    angle_difference = V_angle - I_angle

The result is compared against the configured forward reference
angle:

    reference_difference =
        normalize(angle_difference - forward_angle)

Forward operation occurs when:

    abs(reference_difference) <= tolerance

Otherwise the element is classified as REVERSE.

This is intentionally a baseline directional characteristic.

Future extensions may introduce:

    - memory polarization;
    - positive-sequence polarization;
    - negative-sequence directional elements;
    - zero-sequence directional elements;
    - voltage-polarized directional overcurrent;
    - current-polarized directional logic;
    - IEC directional overcurrent coordination;
    - configurable maximum-torque-angle characteristics.

Phase-Angle Boundary
--------------------
The authoritative MeasurementChannel remains responsible for
measurement magnitude/scaling.

The locked Relay model does not own voltage/current phase angles.

Therefore the baseline directional phase angles are supplied through
ProtectionContext metadata.

Expected context metadata:

    {
        "voltage_angle": <degrees>,
        "current_angle": <degrees>,
    }

This keeps phase-angle evaluation inputs explicit and avoids adding
duplicated phasor-angle state to the Relay model.

Timing Boundary
---------------
This function is instantaneous at the directional-element level.

It does not own a simulation clock.

ProtectionContext supplies the evaluation timestamp when available.

Event scheduling and physical breaker operation belong to higher
protection/simulation/output layers.

Copyright © 2026 Subhendu Mishra
All Rights Reserved.
Proprietary and confidential.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from core.protection.context import ProtectionContext
from core.protection.decision import ProtectionDecision
from core.protection.relay_base import RelayBase


# =====================================================================
# CONSTANTS
# =====================================================================

FUNCTION_CODE = "67"
FUNCTION_NAME = "DIRECTIONAL OVERCURRENT"

CURRENT_INPUT = "current"
VOLTAGE_INPUT = "voltage"

DEFAULT_FORWARD_ANGLE = 90.0
DEFAULT_TOLERANCE = 90.0

ANGLE_RANGE = 360.0

# A sealed, immutable evaluation-cycle snapshot. The private seal prevents
# ordinary ProtectionContext metadata from impersonating Application-resolved
# measurement provenance.
_DIRECTIONAL_SNAPSHOT_SEAL = object()


@dataclass(frozen=True, slots=True)
class _AuthoritativeDirectionalSnapshot:
    _seal: object
    element_id: str
    voltage_value: complex
    current_value: complex
    voltage_angle: float
    current_angle: float
    voltage_channel_id: str
    current_channel_id: str
    voltage_channel_identity: int
    current_channel_identity: int
    sample_timestamp: float
    project_id: str
    activation_generation: int
    configuration_identity: int
    runtime_identity: int


def _create_authoritative_directional_snapshot(
    *,
    element_id: str,
    voltage_value: complex,
    current_value: complex,
    voltage_angle: float,
    current_angle: float,
    voltage_channel_id: str,
    current_channel_id: str,
    voltage_channel_identity: int,
    current_channel_identity: int,
    sample_timestamp: float,
    project_id: str,
    activation_generation: int,
    configuration_identity: int,
    runtime_identity: int,
) -> _AuthoritativeDirectionalSnapshot:
    """Create a sealed snapshot after Application validates channel provenance."""
    return _AuthoritativeDirectionalSnapshot(
        _seal=_DIRECTIONAL_SNAPSHOT_SEAL,
        element_id=element_id,
        voltage_value=voltage_value,
        current_value=current_value,
        voltage_angle=float(voltage_angle),
        current_angle=float(current_angle),
        voltage_channel_id=voltage_channel_id,
        current_channel_id=current_channel_id,
        voltage_channel_identity=voltage_channel_identity,
        current_channel_identity=current_channel_identity,
        sample_timestamp=float(sample_timestamp),
        project_id=project_id,
        activation_generation=activation_generation,
        configuration_identity=configuration_identity,
        runtime_identity=runtime_identity,
    )




# =====================================================================
# DIRECTIONAL SETTINGS
# =====================================================================


@dataclass(frozen=True)
class DirectionalProtectionSettings:
    """
    Immutable settings for one directional protection function.

    Parameters
    ----------
    pickup:
        Current pickup magnitude in the engineering convention of
        the assigned current RelayInput.

    forward_angle:
        Forward reference angle in degrees.

    tolerance:
        Permitted angular deviation from the forward reference angle.

    Notes
    -----
    Pickup is owned by the directional protection function.

    It is deliberately not read from relay.pickup because the
    authoritative physical Relay model is not the protection-function
    configuration container.
    """

    pickup: float
    forward_angle: float = DEFAULT_FORWARD_ANGLE
    tolerance: float = DEFAULT_TOLERANCE

    def __post_init__(self) -> None:
        pickup = float(self.pickup)
        forward_angle = float(self.forward_angle)
        tolerance = float(self.tolerance)

        if not math.isfinite(pickup) or pickup <= 0.0:
            raise ValueError(
                "pickup must be finite and positive."
            )

        if not math.isfinite(forward_angle):
            raise ValueError(
                "forward_angle must be finite."
            )

        if (
            not math.isfinite(tolerance)
            or not 0.0 <= tolerance <= 180.0
        ):
            raise ValueError(
                "tolerance must be finite and between "
                "0 and 180 degrees."
            )

        object.__setattr__(
            self,
            "pickup",
            pickup,
        )
        object.__setattr__(
            self,
            "forward_angle",
            forward_angle,
        )
        object.__setattr__(
            self,
            "tolerance",
            tolerance,
        )


# =====================================================================
# DIRECTIONAL PROTECTION FUNCTION
# =====================================================================


class DirectionalRelay(RelayBase):
    """
    GridForge V2 directional overcurrent protection function.

    This class represents an ANSI 67 protection element, not the
    physical Relay device.

    Parameters
    ----------
    relay:
        Authoritative physical Relay model.

    relay_inputs:
        Mapping containing the required current RelayInput.

        Required name:

            "current"

    settings:
        DirectionalProtectionSettings instance.

    element_id:
        Stable identity of this protection-function instance.

        This is distinct from the physical Relay identity.

    enabled:
        Local function enable state.

    blocked:
        Static protection-function block state.

    Notes
    -----
    Voltage/current phase angles are obtained from ProtectionContext
    metadata because the locked Relay model does not own phase-angle
    state.
    """

    FUNCTION_CODE = FUNCTION_CODE
    FUNCTION_NAME = FUNCTION_NAME

    CURRENT_INPUT = CURRENT_INPUT
    VOLTAGE_INPUT = VOLTAGE_INPUT

    # ================================================================
    # INITIALIZATION
    # ================================================================

    def __init__(
        self,
        relay: Any,
        *,
        element_id: str,
        relay_inputs: Mapping[str, Any] | None = None,
        settings: DirectionalProtectionSettings,
        enabled: bool = True,
        blocked: bool = False,
    ) -> None:

        if not isinstance(
            settings,
            DirectionalProtectionSettings,
        ):
            raise TypeError(
                "settings must be a "
                "DirectionalProtectionSettings instance."
            )

        super().__init__(
            relay=relay,
            element_id=element_id,
            function_code=self.FUNCTION_CODE,
            function_name=self.FUNCTION_NAME,
            relay_inputs=relay_inputs,
            settings={
                "pickup": settings.pickup,
                "forward_angle": settings.forward_angle,
                "tolerance": settings.tolerance,
            },
            enabled=enabled,
            blocked=blocked,
        )

        self.settings = settings

        # --------------------------------------------------------------
        # Algorithm-specific diagnostic state.
        # --------------------------------------------------------------

        self._direction: str | None = None

        self._last_timestamp: float | None = None

        self._last_current: complex | None = None

        self._last_voltage_angle: float | None = None
        self._last_current_angle: float | None = None
        self._last_angle_difference: float | None = None
        self._last_reference_difference: float | None = None

        self._last_pickup: bool = False

        self._last_decision: ProtectionDecision | None = None

        self.require_inputs(
            self.CURRENT_INPUT,
            self.VOLTAGE_INPUT,
        )

    # ================================================================
    # SETTINGS
    # ================================================================

    @property
    def pickup(self) -> float:
        """Return configured current pickup."""

        return self.settings.pickup

    @property
    def forward_angle(self) -> float:
        """Return configured forward reference angle."""

        return self.settings.forward_angle

    @property
    def tolerance(self) -> float:
        """Return configured directional angular tolerance."""

        return self.settings.tolerance

    # ================================================================
    # MEASUREMENT
    # ================================================================

    def current_signal(self) -> Any:
        """
        Return the current engineering signal from RelayInput.

        MeasurementChannel remains the authoritative measurement
        infrastructure. The directional element consumes the
        resulting RelayInput value only.
        """

        relay_input = self.get_input(
            self.CURRENT_INPUT
        )

        if hasattr(relay_input, "engineering_value"):
            return relay_input.engineering_value

        return relay_input.value

    # ----------------------------------------------------------------

    @staticmethod
    def _complex_measurement(
        value: Any,
        *,
        name: str,
    ) -> complex:
        """
        Convert and validate a measurement as a finite complex value.
        """

        if isinstance(value, bool):
            raise TypeError(
                f"{name} measurement cannot be bool."
            )

        try:
            value = complex(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} measurement must be numeric."
            ) from exc

        if not (
            math.isfinite(value.real)
            and math.isfinite(value.imag)
        ):
            raise ValueError(
                f"{name} measurement must be finite."
            )

        return value

    # ----------------------------------------------------------------

    def current_value(self) -> complex:
        """
        Return the validated current measurement.

        Complex current values are accepted because the measurement
        subsystem may expose a phasor.

        Pickup uses:

            |I|
        """

        return self._complex_measurement(
            self.current_signal(),
            name="Current",
        )

    # ================================================================
    # CURRENT PICKUP
    # ================================================================

    def check_pickup(
        self,
        current: complex | None = None,
    ) -> bool:
        """
        Evaluate the directional-element current pickup criterion.

        Criterion:

            |I| >= pickup
        """

        if not self.operational:
            return False

        if current is None:
            current = self.current_value()

        return abs(current) >= self.pickup

    # ================================================================
    # ANGLE NORMALIZATION
    # ================================================================

    @staticmethod
    def normalize_angle(
        angle: float,
    ) -> float:
        """
        Normalize an angle to:

            [-180, 180)
        """

        try:
            angle = float(angle)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Angle must be numeric."
            ) from exc

        if not math.isfinite(angle):
            raise ValueError(
                "Angle must be finite."
            )

        return (
            (angle + 180.0) % ANGLE_RANGE
        ) - 180.0

    # ================================================================
    # DIRECTIONAL DISCRIMINATION
    # ================================================================

    def direction_from_angles(
        self,
        *,
        voltage_angle: float,
        current_angle: float,
    ) -> str:
        """
        Determine forward/reverse direction.

        The baseline characteristic is:

            Δθ = θV - θI

            reference_error =
                normalize(Δθ - forward_angle)

        Forward operation occurs when:

            |reference_error| <= tolerance
        """

        try:
            voltage_angle = float(voltage_angle)
            current_angle = float(current_angle)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Voltage and current angles must be numeric."
            ) from exc

        if not math.isfinite(voltage_angle):
            raise ValueError(
                "voltage_angle must be finite."
            )

        if not math.isfinite(current_angle):
            raise ValueError(
                "current_angle must be finite."
            )

        angle_difference = self.normalize_angle(
            voltage_angle - current_angle
        )

        reference_difference = self.normalize_angle(
            angle_difference - self.forward_angle
        )

        self._last_voltage_angle = voltage_angle
        self._last_current_angle = current_angle
        self._last_angle_difference = angle_difference
        self._last_reference_difference = (
            reference_difference
        )

        if abs(reference_difference) <= self.tolerance:
            direction = "FORWARD"
        else:
            direction = "REVERSE"

        self._direction = direction

        return direction

    # ----------------------------------------------------------------

    def check_direction(
        self,
        *,
        voltage_angle: float,
        current_angle: float,
    ) -> str:
        """
        Public directional discriminator.

        Phase angles are evaluation inputs and are not written into
        the authoritative Relay model.
        """

        return self.direction_from_angles(
            voltage_angle=voltage_angle,
            current_angle=current_angle,
        )

    # ================================================================
    # CONTEXT ANGLES
    # ================================================================

    def _authoritative_snapshot(
        self,
        context: ProtectionContext | None,
    ) -> _AuthoritativeDirectionalSnapshot:
        """Require an Application-sealed, element-specific phasor snapshot."""
        metadata = getattr(context, "metadata", None) if context is not None else None
        if not isinstance(metadata, Mapping):
            raise ValueError("ProtectionContext metadata is missing authoritative directional samples.")
        snapshots = metadata.get("directional_measurement_snapshots_by_element")
        if not isinstance(snapshots, Mapping):
            raise ValueError(
                "ANSI 67 requires an Application-created authoritative measurement snapshot; "
                "caller-supplied angles or provenance are not accepted."
            )
        snapshot = snapshots.get(self.element_id)
        if not isinstance(snapshot, _AuthoritativeDirectionalSnapshot):
            raise ValueError(f"No authoritative measurement snapshot exists for element {self.element_id!r}.")
        if snapshot._seal is not _DIRECTIONAL_SNAPSHOT_SEAL:
            raise ValueError("Directional measurement snapshot seal is invalid.")
        if snapshot.element_id != self.element_id:
            raise ValueError("Directional measurement snapshot element identity mismatch.")
        if not snapshot.project_id or not snapshot.voltage_channel_id or not snapshot.current_channel_id:
            raise ValueError("Directional measurement snapshot is missing project/channel provenance.")
        if snapshot.voltage_channel_id == snapshot.current_channel_id:
            raise ValueError("Directional voltage and current channel identities must be distinct.")
        if snapshot.voltage_channel_identity == snapshot.current_channel_identity:
            raise ValueError("Directional voltage and current channel objects must be distinct.")
        if not math.isfinite(snapshot.sample_timestamp):
            raise ValueError("Directional measurement snapshot timestamp is non-finite.")
        for name, value in (
            ("voltage", snapshot.voltage_value),
            ("current", snapshot.current_value),
        ):
            if not isinstance(value, complex) or not (
                math.isfinite(value.real) and math.isfinite(value.imag)
            ):
                raise ValueError(f"Authoritative {name} sample is not a finite complex phasor.")
            if abs(value) == 0.0:
                raise ValueError(f"Authoritative {name} phasor has no defined angle.")
        if not math.isfinite(snapshot.voltage_angle) or not math.isfinite(snapshot.current_angle):
            raise ValueError("Authoritative directional phasor angles must be finite.")
        return snapshot

    # ================================================================
    # EVALUATION
    # ================================================================

    def evaluate(
        self,
        context: ProtectionContext | None = None,
    ) -> ProtectionDecision:
        """
        Evaluate one directional protection cycle.

        The evaluation performs:

            1. operational-state validation;
            2. current acquisition through RelayInput;
            3. current pickup;
            4. polarizing-angle acquisition from context;
            5. forward/reverse discrimination;
            6. ProtectionDecision generation.

        The function does NOT:

            - modify Relay trip state;
            - call Relay.set_trip();
            - call trip();
            - operate a breaker;
            - schedule a trip event.

        Directional operation is instantaneous at this function
        boundary. Timing/event scheduling belongs to the higher-level
        protection/simulation architecture.
        """

        timestamp = self._context_time(
            context
        )

        self._validate_timestamp_order(
            timestamp
        )

        self._last_timestamp = timestamp

        # --------------------------------------------------------------
        # Operational gate
        # --------------------------------------------------------------

        if not self.operational:

            self._clear_runtime_state()

            if self.blocked:
                decision = ProtectionDecision.blocked_decision(
                    relay_id=self.relay_id,
                    function_code=self.FUNCTION_CODE,
                    function_id=self.element_id,
                    reason=(
                        "Directional protection function "
                        "is blocked."
                    ),
                    timestamp=timestamp,
                )
            else:
                decision = ProtectionDecision.no_operation(
                    relay_id=self.relay_id,
                    function_code=self.FUNCTION_CODE,
                    function_id=self.element_id,
                    reason=self._inactive_reason(),
                    timestamp=timestamp,
                )

            self._last_decision = decision

            return decision

        # --------------------------------------------------------------
        # Current measurement
        # --------------------------------------------------------------

        try:
            snapshot = self._authoritative_snapshot(context)
            current = self._complex_measurement(
                snapshot.current_value,
                name="Authoritative directional current",
            )

        except (TypeError, ValueError) as exc:

            self._clear_runtime_state()

            decision = ProtectionDecision.invalid(
                relay_id=self.relay_id,
                function_code=self.FUNCTION_CODE,
                function_id=self.element_id,
                reason=(
                    "Invalid directional current measurement: "
                    f"{exc}"
                ),
                timestamp=timestamp,
                metadata={
                    "current_input": self.CURRENT_INPUT,
                },
            )

            self._last_decision = decision

            return decision

        self._last_current = current

        # --------------------------------------------------------------
        # Pickup
        # --------------------------------------------------------------

        try:
            pickup = self.check_pickup(
                current
            )

        except (TypeError, ValueError) as exc:

            self._clear_runtime_state()

            decision = ProtectionDecision.invalid(
                relay_id=self.relay_id,
                function_code=self.FUNCTION_CODE,
                function_id=self.element_id,
                reason=(
                    "Invalid directional pickup evaluation: "
                    f"{exc}"
                ),
                timestamp=timestamp,
                metadata={
                    "current": current,
                    "current_magnitude": abs(current),
                    "pickup_setting": self.pickup,
                },
            )

            self._last_decision = decision

            return decision

        self._last_pickup = pickup

        # --------------------------------------------------------------
        # Pickup absent
        # --------------------------------------------------------------

        if not pickup:

            self._direction = None

            decision = ProtectionDecision.no_operation(
                relay_id=self.relay_id,
                function_code=self.FUNCTION_CODE,
                function_id=self.element_id,
                reason=(
                    "Directional overcurrent pickup "
                    "criterion is not satisfied."
                ),
                timestamp=timestamp,
                metadata={
                    "current": current,
                    "current_magnitude": abs(current),
                    "pickup_setting": self.pickup,
                    "direction": None,
                },
            )

            self._last_decision = decision

            return decision

        # --------------------------------------------------------------
        # Directional phase-angle evaluation
        # --------------------------------------------------------------

        try:

            # Both pickup and angular discrimination use the same immutable
            # samples validated for this evaluation cycle.
            voltage_angle = snapshot.voltage_angle
            current_angle = snapshot.current_angle

            direction = self.direction_from_angles(
                voltage_angle=voltage_angle,
                current_angle=current_angle,
            )

        except (TypeError, ValueError) as exc:

            self._direction = None

            decision = ProtectionDecision.invalid(
                relay_id=self.relay_id,
                function_code=self.FUNCTION_CODE,
                function_id=self.element_id,
                reason=(
                    "Invalid directional phase-angle "
                    f"evaluation: {exc}"
                ),
                timestamp=timestamp,
                metadata={
                    "current": current,
                    "current_magnitude": abs(current),
                    "pickup": pickup,
                },
            )

            self._last_decision = decision

            return decision

        metadata = {
            "current": current,
            "current_magnitude": abs(current),
            "pickup": pickup,
            "pickup_setting": self.pickup,
            "voltage_angle": voltage_angle,
            "current_angle": current_angle,
            "angle_difference": (
                self._last_angle_difference
            ),
            "reference_difference": (
                self._last_reference_difference
            ),
            "forward_angle": self.forward_angle,
            "tolerance": self.tolerance,
            "direction": direction,
        }

        # --------------------------------------------------------------
        # Reverse direction
        # --------------------------------------------------------------

        if direction != "FORWARD":

            decision = ProtectionDecision.no_operation(
                relay_id=self.relay_id,
                function_code=self.FUNCTION_CODE,
                function_id=self.element_id,
                reason=(
                    "Current pickup is present but the measured "
                    "direction is REVERSE."
                ),
                timestamp=timestamp,
                metadata=metadata,
            )

            self._last_decision = decision

            return decision

        # --------------------------------------------------------------
        # Forward operation
        # --------------------------------------------------------------

        decision = ProtectionDecision.trip(
            relay_id=self.relay_id,
            function_code=self.FUNCTION_CODE,
            function_id=self.element_id,
            reason=(
                "Directional overcurrent pickup and "
                "FORWARD directional criterion are satisfied."
            ),
            timestamp=timestamp,
            operating_time=0.0,
            metadata=metadata,
        )

        self._last_decision = decision

        return decision

    # ================================================================
    # TIMING
    # ================================================================

    @staticmethod
    def _context_time(
        context: ProtectionContext | None,
    ) -> float | None:
        """
        Return the evaluation timestamp from ProtectionContext.
        """

        if context is None:
            return None

        try:
            timestamp = context.time
        except AttributeError as exc:
            raise TypeError(
                "context must provide a 'time' attribute."
            ) from exc

        return DirectionalRelay._validate_timestamp(
            timestamp
        )

    # ----------------------------------------------------------------

    def _validate_timestamp_order(
        self,
        timestamp: float | None,
    ) -> None:
        """
        Ensure stateful evaluation timestamps do not move backwards.
        """

        if timestamp is None:
            return

        previous = self._last_timestamp

        if (
            previous is not None
            and timestamp < previous
        ):
            raise ValueError(
                "Protection evaluation timestamp cannot move "
                "backwards."
            )

    # ----------------------------------------------------------------

    @staticmethod
    def _validate_timestamp(
        timestamp: float,
    ) -> float:
        """
        Validate a protection evaluation timestamp.
        """

        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "timestamp must be numeric."
            ) from exc

        if not math.isfinite(timestamp):
            raise ValueError(
                "timestamp must be finite."
            )

        return timestamp

    # ================================================================
    # STATE
    # ================================================================

    def _clear_runtime_state(self) -> None:
        """
        Clear transient directional-function state.

        The authoritative Relay is never modified.
        """

        self._direction = None

        self._last_current = None

        self._last_voltage_angle = None
        self._last_current_angle = None
        self._last_angle_difference = None
        self._last_reference_difference = None

        self._last_pickup = False

    # ----------------------------------------------------------------

    def _inactive_reason(self) -> str:
        """
        Return a diagnostic explanation for inactive evaluation.
        """

        if not self.enabled:
            return (
                "Directional protection function is disabled."
            )

        if self.blocked:
            return (
                "Directional protection function is blocked."
            )

        if not bool(
            getattr(
                self.relay,
                "operational",
                True,
            )
        ):
            return (
                "Authoritative relay is not operational."
            )

        return (
            "Directional protection function is not operational."
        )

    # ================================================================
    # RESET
    # ================================================================

    def reset(self) -> None:
        """
        Reset transient directional-function state.

        Protection settings and authoritative physical Relay state
        remain unchanged.
        """

        super().reset()

        self._direction = None
        self._last_timestamp = None

        self._last_current = None

        self._last_voltage_angle = None
        self._last_current_angle = None
        self._last_angle_difference = None
        self._last_reference_difference = None

        self._last_pickup = False
        self._last_decision = None

    # ================================================================
    # DIAGNOSTICS
    # ================================================================

    @property
    def direction(self) -> str | None:
        """Return the last directional classification."""

        return self._direction

    @property
    def last_current(self) -> complex | None:
        """Return the last sampled current."""

        return self._last_current

    @property
    def last_voltage_angle(self) -> float | None:
        """Return the last evaluated voltage angle."""

        return self._last_voltage_angle

    @property
    def last_current_angle(self) -> float | None:
        """Return the last evaluated current angle."""

        return self._last_current_angle

    @property
    def last_angle_difference(self) -> float | None:
        """Return the normalized V-I angle difference."""

        return self._last_angle_difference

    @property
    def last_reference_difference(self) -> float | None:
        """Return the normalized difference from forward reference."""

        return self._last_reference_difference

    @property
    def last_pickup(self) -> bool:
        """Return the pickup result from the most recent evaluation."""

        return self._last_pickup

    @property
    def last_timestamp(self) -> float | None:
        """Return the most recent evaluation timestamp."""

        return self._last_timestamp

    @property
    def last_decision(self) -> ProtectionDecision | None:
        """Return the most recent ProtectionDecision."""

        return self._last_decision

    # ================================================================
    # STATUS
    # ================================================================

    def status(self) -> dict[str, Any]:
        """
        Return diagnostic status for the directional function.

        This is not the authoritative persistence representation.
        """

        result = super().status()

        result.update(
            {
                "function": "DIRECTIONAL_OVERCURRENT",
                "function_code": self.FUNCTION_CODE,
                "pickup": self.pickup,
                "current": self._last_current,
                "current_magnitude": (
                    abs(self._last_current)
                    if self._last_current is not None
                    else None
                ),
                "forward_angle": self.forward_angle,
                "tolerance": self.tolerance,
                "voltage_angle": self._last_voltage_angle,
                "current_angle": self._last_current_angle,
                "angle_difference": self._last_angle_difference,
                "reference_difference": (
                    self._last_reference_difference
                ),
                "direction": self._direction,
                "last_pickup": self._last_pickup,
                "last_timestamp": self._last_timestamp,
                "last_decision": (
                    self._last_decision.to_dict()
                    if self._last_decision is not None
                    else None
                ),
            }
        )

        return result

    # ================================================================
    # REPRESENTATION
    # ================================================================

    def __repr__(self) -> str:
        """Developer-friendly representation."""

        relay_id = getattr(
            self.relay,
            "id",
            self.relay_id,
        )

        return (
            f"<DirectionalRelay "
            f"relay={relay_id!r}, "
            f"element={self.element_id!r}, "
            f"direction={self._direction!r}, "
            f"pickup={self._last_pickup}>"
        )


# =====================================================================
# PUBLIC API
# =====================================================================

__all__ = [
    "DirectionalProtectionSettings",
    "DirectionalRelay",
]
