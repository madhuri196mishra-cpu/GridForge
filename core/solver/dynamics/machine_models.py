"""
GridForge Dynamic Machine Models
================================

Synchronous-machine models for transient-stability simulation.

V2 Responsibilities
-------------------

This module provides:

- machine identity;
- machine parameters;
- dynamic state definition;
- initialization;
- internal-emf calculation;
- terminal-current calculation;
- electrical-power calculation;
- machine differential equations.

This module does NOT provide:

- numerical integration;
- network solution;
- event scheduling;
- protection;
- AVR;
- governor;
- PSS;
- simulation orchestration.

The machine model is therefore a pure engineering model consumed by
the dynamic simulation layer.

Classical machine model
-----------------------

The V2 baseline is the classical second-order synchronous-machine
model:

    E' = Efd ∠ δ

    I = (E' - V) / (j Xd')

    S = V I*

Rotor equations:

    dδ/dt = ω

    dω/dt = (Pm - Pe - Dω) / (2H)

Dynamic state:

    x = [δ, ω]

Mechanical power Pm is an INPUT to the model.

It is intentionally NOT stored as a machine dynamic state.

Future governor/turbine/control implementations may provide Pm
through the dynamic simulation/control composition layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .swing_equation import SwingEquation


# ======================================================================
# ERRORS
# ======================================================================


class MachineModelError(
    RuntimeError
):
    """Base exception for machine-model errors."""


class MachineParameterError(
    MachineModelError
):
    """Raised when machine parameters are invalid."""


class MachineStateError(
    MachineModelError
):
    """Raised when machine state is invalid."""


# ======================================================================
# MACHINE PARAMETERS
# ======================================================================


@dataclass(frozen=True)
class ClassicalMachineParameters:
    """
    Parameters of the classical synchronous-machine model.

    Parameters
    ----------
    H:
        Inertia constant [s].

    Xd_prime:
        Direct-axis transient reactance Xd' [pu].

    D:
        Damping coefficient.

    Efd:
        Internal emf magnitude [pu].

    initial_delta:
        Initial rotor angle [rad].

    initial_omega:
        Initial speed deviation [pu].
    """

    H: float

    Xd_prime: float

    D: float = 0.0

    Efd: float = 1.0

    initial_delta: float = 0.0

    initial_omega: float = 0.0

    def __post_init__(
        self,
    ) -> None:

        if not np.isfinite(
            self.H
        ) or self.H <= 0.0:

            raise MachineParameterError(
                "H must be finite and "
                "greater than zero."
            )

        if not np.isfinite(
            self.Xd_prime
        ) or self.Xd_prime <= 0.0:

            raise MachineParameterError(
                "Xd_prime must be finite "
                "and greater than zero."
            )

        if not np.isfinite(
            self.D
        ) or self.D < 0.0:

            raise MachineParameterError(
                "D must be finite and "
                "non-negative."
            )

        if not np.isfinite(
            self.Efd
        ) or self.Efd < 0.0:

            raise MachineParameterError(
                "Efd must be finite and "
                "non-negative."
            )

        if not np.isfinite(
            self.initial_delta
        ):

            raise MachineParameterError(
                "initial_delta must "
                "be finite."
            )

        if not np.isfinite(
            self.initial_omega
        ):

            raise MachineParameterError(
                "initial_omega must "
                "be finite."
            )


# ======================================================================
# ELECTRICAL OUTPUT
# ======================================================================


@dataclass(frozen=True)
class MachineElectricalOutput:
    """
    Electrical output calculated at a machine terminal.

    Attributes
    ----------
    active_power:
        Electrical active power Pe [pu].

    reactive_power:
        Electrical reactive power Qe [pu].

    current:
        Machine terminal current injection [pu].

    terminal_voltage:
        Complex terminal voltage [pu].

    internal_emf:
        Complex internal emf [pu].
    """

    active_power: float

    reactive_power: float

    current: complex

    terminal_voltage: complex

    internal_emf: complex

    @property
    def apparent_power(
        self,
    ) -> complex:
        """Return complex apparent power S = P + jQ."""

        return complex(
            self.active_power,
            self.reactive_power,
        )


# ======================================================================
# CLASSICAL SYNCHRONOUS MACHINE
# ======================================================================


class ClassicalSynchronousMachine:
    """
    Classical second-order synchronous-machine model.

    State
    -----

        [delta, omega]

    where:

        delta = rotor electrical angle [rad]
        omega = speed deviation [pu]

    The machine delegates rotor physics to ``SwingEquation`` and does
    not perform numerical integration.

    Parameters
    ----------
    machine_id:
        Unique machine identifier.

    bus_id:
        Electrical bus identifier.

    H:
        Inertia constant [s].

    Xd_prime:
        Direct-axis transient reactance [pu].

    D:
        Damping coefficient.

    Efd:
        Internal emf magnitude [pu].

    initial_delta:
        Initial rotor angle [rad].

    initial_omega:
        Initial speed deviation [pu].
    """

    STATE_SIZE = 2

    def __init__(
        self,
        machine_id: str,
        bus_id: str,
        H: float,
        Xd_prime: float,
        *,
        D: float = 0.0,
        Efd: float = 1.0,
        initial_delta: float = 0.0,
        initial_omega: float = 0.0,
    ) -> None:

        if not str(
            machine_id
        ).strip():

            raise ValueError(
                "machine_id cannot "
                "be empty."
            )

        if not str(
            bus_id
        ).strip():

            raise ValueError(
                "bus_id cannot "
                "be empty."
            )

        self._machine_id = (
            str(machine_id)
        )

        self._bus_id = (
            str(bus_id)
        )

        self.parameters = (
            ClassicalMachineParameters(
                H=H,
                Xd_prime=Xd_prime,
                D=D,
                Efd=Efd,
                initial_delta=initial_delta,
                initial_omega=initial_omega,
            )
        )

        self._swing_equation = (
            SwingEquation(
                H=self.parameters.H,
                D=self.parameters.D,
            )
        )

    # ==================================================================
    # IDENTITY
    # ==================================================================

    @property
    def machine_id(
        self,
    ) -> str:
        """Return unique machine identifier."""

        return self._machine_id

    @property
    def bus_id(
        self,
    ) -> str:
        """Return connected bus identifier."""

        return self._bus_id

    # ==================================================================
    # PARAMETERS
    # ==================================================================

    @property
    def H(
        self,
    ) -> float:
        """Return inertia constant."""

        return self.parameters.H

    @property
    def Xd_prime(
        self,
    ) -> float:
        """Return transient reactance."""

        return self.parameters.Xd_prime

    @property
    def D(
        self,
    ) -> float:
        """Return damping coefficient."""

        return self.parameters.D

    @property
    def Efd(
        self,
    ) -> float:
        """Return internal emf magnitude."""

        return self.parameters.Efd

    @property
    def swing_equation(
        self,
    ) -> SwingEquation:
        """Return the machine's rotor-equation model."""

        return self._swing_equation

    @property
    def state_size(
        self,
    ) -> int:
        """Number of dynamic states."""

        return self.STATE_SIZE

    # ==================================================================
    # INITIAL STATE
    # ==================================================================

    def initial_state(
        self,
        terminal_voltage: complex,
        electrical_power: Any = None,
        mechanical_power: float | None = None,
        *,
        time: float = 0.0,
    ) -> np.ndarray:
        """
        Return the initial machine dynamic state.

        The initial rotor angle may be inferred from a supplied complex
        operating-point power.

        Parameters
        ----------
        terminal_voltage:
            Initial terminal voltage phasor.

        electrical_power:
            Initial complex electrical power, if available.

        mechanical_power:
            Initial mechanical input. It is validated but is not stored
            in the machine.

        time:
            Initialization time. Reserved for future initialization
            models.
        """

        del time

        voltage = complex(
            terminal_voltage
        )

        self._validate_complex(
            voltage,
            "terminal_voltage",
        )

        if mechanical_power is not None:

            self._validate_scalar(
                mechanical_power,
                "mechanical_power",
            )

        delta = (
            self.parameters.initial_delta
        )

        omega = (
            self.parameters.initial_omega
        )

        apparent_power = (
            self._extract_complex_power(
                electrical_power
            )
        )

        if (
            apparent_power is not None
            and abs(voltage) > 1e-12
        ):

            current = np.conj(
                apparent_power
                / voltage
            )

            internal_emf = (
                voltage
                + 1j
                * self.Xd_prime
                * current
            )

            if abs(
                internal_emf
            ) > 1e-12:

                delta = float(
                    np.angle(
                        internal_emf
                    )
                )

        state = np.array(
            [
                delta,
                omega,
            ],
            dtype=float,
        )

        return self.validate_state(
            state
        )

    # ==================================================================
    # INTERNAL EMF
    # ==================================================================

    def internal_emf(
        self,
        state: np.ndarray,
        *,
        field_voltage: float | None = None,
    ) -> complex:
        """
        Calculate internal emf behind transient reactance.

            E' = Efd exp(jδ)
        """

        state = self.validate_state(
            state
        )

        delta = state[0]
        if field_voltage is not None and (
            isinstance(field_voltage, bool)
            or not isinstance(field_voltage, (int, float))
        ):
            raise MachineModelError("field_voltage must be a real numeric value")
        efd = self.Efd if field_voltage is None else float(field_voltage)
        self._validate_scalar(efd, "field_voltage")
        if efd < 0.0:
            raise MachineModelError("field_voltage must be non-negative")

        return complex(
            efd
            * np.exp(
                1j * delta
            )
        )

    # ==================================================================
    # CURRENT
    # ==================================================================

    def current_injection(
        self,
        state: np.ndarray,
        terminal_voltage: complex,
        *,
        field_voltage: float | None = None,
        time: float = 0.0,
    ) -> complex:
        """
        Calculate machine current injection.

            I = (E' - V) / (jXd')

        Positive current denotes injection from the machine into the
        network.
        """

        del time

        state = self.validate_state(
            state
        )

        voltage = complex(
            terminal_voltage
        )

        self._validate_complex(
            voltage,
            "terminal_voltage",
        )

        emf = (
            self.internal_emf(
                state,
                field_voltage=field_voltage,
            )
        )

        return (
            emf - voltage
        ) / (
            1j * self.Xd_prime
        )

    # ==================================================================
    # ELECTRICAL POWER
    # ==================================================================

    def electrical_output(
        self,
        state: np.ndarray,
        terminal_voltage: complex,
        *,
        field_voltage: float | None = None,
        time: float = 0.0,
    ) -> MachineElectricalOutput:
        """
        Calculate terminal electrical output.

            S = V I*
        """

        del time

        voltage = complex(
            terminal_voltage
        )

        current = (
            self.current_injection(
                state=state,
                terminal_voltage=voltage,
                field_voltage=field_voltage,
            )
        )

        apparent_power = (
            voltage
            * np.conj(
                current
            )
        )

        emf = (
            self.internal_emf(
                state,
                field_voltage=field_voltage,
            )
        )

        return MachineElectricalOutput(
            active_power=float(
                apparent_power.real
            ),
            reactive_power=float(
                apparent_power.imag
            ),
            current=current,
            terminal_voltage=voltage,
            internal_emf=emf,
        )

    def electrical_power(
        self,
        state: np.ndarray,
        terminal_voltage: complex,
        *,
        field_voltage: float | None = None,
        time: float = 0.0,
    ) -> float:
        """
        Return electrical active power Pe [pu].
        """

        output = (
            self.electrical_output(
                state=state,
                terminal_voltage=terminal_voltage,
                field_voltage=field_voltage,
                time=time,
            )
        )

        return output.active_power

    # ==================================================================
    # DIFFERENTIAL EQUATIONS
    # ==================================================================

    def derivatives(
        self,
        state: np.ndarray,
        terminal_voltage: complex,
        mechanical_power: float,
        *,
        electrical_power: float | None = None,
        field_voltage: float | None = None,
        time: float = 0.0,
    ) -> np.ndarray:
        """
        Evaluate machine dynamic derivatives.

        Parameters
        ----------
        state:
            Machine dynamic state [delta, omega].

        terminal_voltage:
            Complex terminal voltage [pu].

        mechanical_power:
            Mechanical input Pm [pu].

        electrical_power:
            Electrical active output Pe [pu].

            If omitted, Pe is calculated from the terminal voltage.

        time:
            Simulation time.

        Returns
        -------
        numpy.ndarray
            [d_delta/dt, d_omega/dt]

        Important
        ---------
        ``mechanical_power`` is an explicit input.

        It is NOT stored in the machine model and is NOT a dynamic
        state of the classical machine.
        """

        state = self.validate_state(
            state
        )

        self._validate_scalar(
            mechanical_power,
            "mechanical_power",
        )

        if electrical_power is None:

            Pe = self.electrical_power(
                state=state,
                terminal_voltage=(
                    terminal_voltage
                ),
                field_voltage=field_voltage,
                time=time,
            )

        else:

            Pe = float(
                electrical_power
            )

            self._validate_scalar(
                Pe,
                "electrical_power",
            )

        result = (
            self.swing_equation.derivative_vector(
                state=state,
                Pm=float(
                    mechanical_power
                ),
                Pe=Pe,
            )
        )

        if not np.all(
            np.isfinite(result)
        ):

            raise MachineModelError(
                "Machine derivative "
                "calculation produced "
                "non-finite values."
            )

        return result

    # ==================================================================
    # STATE VALIDATION
    # ==================================================================

    def validate_state(
        self,
        state: np.ndarray,
    ) -> np.ndarray:
        """
        Validate and return a machine state vector.

        Required ordering:

            [delta, omega]
        """

        values = np.asarray(
            state,
            dtype=float,
        )

        if values.ndim != 1:

            raise MachineStateError(
                "Machine state must be "
                "one-dimensional."
            )

        if values.size != (
            self.STATE_SIZE
        ):

            raise MachineStateError(
                "Classical machine state "
                "must contain exactly "
                "[delta, omega]."
            )

        if not np.all(
            np.isfinite(values)
        ):

            raise MachineStateError(
                "Machine state contains "
                "non-finite values."
            )

        return values

    # ==================================================================
    # HELPERS
    # ==================================================================

    @staticmethod
    def _validate_scalar(
        value: float,
        name: str,
    ) -> None:

        try:

            numeric = float(
                value
            )

        except (
            TypeError,
            ValueError,
        ) as exc:

            raise MachineModelError(
                f"{name} must be numeric."
            ) from exc

        if not np.isfinite(
            numeric
        ):

            raise MachineModelError(
                f"{name} must be finite."
            )

    @staticmethod
    def _validate_complex(
        value: complex,
        name: str,
    ) -> None:

        if not (
            np.isfinite(
                value.real
            )
            and np.isfinite(
                value.imag
            )
        ):

            raise MachineModelError(
                f"{name} must contain "
                "finite real and "
                "imaginary parts."
            )

    @staticmethod
    def _extract_complex_power(
        value: Any,
    ) -> complex | None:
        """
        Extract complex power from supported operating-point formats.
        """

        if value is None:

            return None

        if isinstance(
            value,
            complex,
        ):

            result = value

        elif hasattr(
            value,
            "apparent_power",
        ):

            result = complex(
                value.apparent_power
            )

        elif hasattr(
            value,
            "active_power",
        ):

            result = complex(
                float(
                    value.active_power
                ),
                float(
                    getattr(
                        value,
                        "reactive_power",
                        0.0,
                    )
                ),
            )

        elif isinstance(
            value,
            dict,
        ):

            if "active_power" in value:

                result = complex(
                    float(
                        value[
                            "active_power"
                        ]
                    ),
                    float(
                        value.get(
                            "reactive_power",
                            0.0,
                        )
                    ),
                )

            elif "P" in value:

                result = complex(
                    float(
                        value["P"]
                    ),
                    float(
                        value.get(
                            "Q",
                            0.0,
                        )
                    ),
                )

            else:

                return None

        elif np.isscalar(
            value
        ):

            result = complex(
                float(value),
                0.0,
            )

        else:

            return None

        if not (
            np.isfinite(
                result.real
            )
            and np.isfinite(
                result.imag
            )
        ):

            raise MachineModelError(
                "Electrical power "
                "contains non-finite "
                "values."
            )

        return result


# ======================================================================
# LEGACY NAME
# ======================================================================

# Temporary compatibility alias. It does NOT reintroduce the old
# DynamicGenerator implementation or its embedded AVR/Governor/PSS
# architecture.

DynamicGenerator = (
    ClassicalSynchronousMachine
)


__all__ = [
    "MachineModelError",
    "MachineParameterError",
    "MachineStateError",
    "ClassicalMachineParameters",
    "MachineElectricalOutput",
    "ClassicalSynchronousMachine",
    "DynamicGenerator",
]
