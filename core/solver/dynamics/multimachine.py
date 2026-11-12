"""
GridForge Multi-Machine Dynamic System
======================================

Coordinates multiple dynamic machine models for transient-stability
simulation.

Responsibilities
----------------
- Register dynamic machine models.
- Maintain deterministic machine ordering.
- Define the global dynamic-state layout.
- Pack machine states into one global vector.
- Unpack a global vector into machine-local states.
- Evaluate machine electrical outputs.
- Evaluate machine differential equations.

Non-responsibilities
--------------------
This module does NOT:

- perform numerical integration;
- solve the network;
- construct Y-bus;
- process simulation events;
- implement AVR/GOV/PSS;
- implement protection;
- own the simulation time loop.

The numerical integrator is provided by ``integrator.py``.

The network/algebraic solution is provided by the DAE/network layer.

The machine equations are supplied by ``machine_models.py``.

Global state
------------

For N classical machines:

    x =
    [
        δ1, ω1,
        δ2, ω2,
        ...
        δN, ωN
    ]

The layout is deterministic and is derived from machine order.

For future higher-order machine models, each machine may expose a
different ``state_size`` while the global vector remains contiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np

from .machine_models import (
    ClassicalSynchronousMachine,
    MachineElectricalOutput,
    MachineModelError,
)


# ======================================================================
# ERRORS
# ======================================================================


class MultiMachineError(
    RuntimeError
):
    """Base exception for multi-machine errors."""


class DuplicateMachineError(
    MultiMachineError
):
    """Raised when a machine ID is duplicated."""


class UnknownMachineError(
    MultiMachineError
):
    """Raised when a machine cannot be found."""


class StateLayoutError(
    MultiMachineError
):
    """Raised when the global state layout is invalid."""


# ======================================================================
# STATE SLICE
# ======================================================================


@dataclass(frozen=True)
class MachineStateSlice:
    """
    Location of one machine's state inside the global state vector.

    Attributes
    ----------
    machine_id:
        Machine identifier.

    start:
        Inclusive global-vector index.

    stop:
        Exclusive global-vector index.
    """

    machine_id: str
    start: int
    stop: int

    @property
    def size(
        self,
    ) -> int:
        """Number of states belonging to the machine."""

        return self.stop - self.start


# ======================================================================
# MACHINE ENTRY
# ======================================================================


@dataclass(frozen=True)
class MachineEntry:
    """
    Registered machine and its global-state location.
    """

    machine: ClassicalSynchronousMachine

    state_slice: MachineStateSlice


# ======================================================================
# MULTI-MACHINE SYSTEM
# ======================================================================


class MultiMachineSystem:
    """
    Container and evaluator for multiple dynamic machines.

    Parameters
    ----------
    machines:
        Optional iterable of machine models.

    Notes
    -----
    Machine ordering is deterministic and preserved in the order of
    registration.

    The global state vector is generated from the registered machine
    models rather than from hard-coded assumptions about δ, ω, Efd,
    Pm, or PSS states.
    """

    def __init__(
        self,
        machines: Iterable[
            ClassicalSynchronousMachine
        ] | None = None,
    ) -> None:

        self._machines: list[
            ClassicalSynchronousMachine
        ] = []

        self._entries: dict[
            str,
            MachineEntry,
        ] = {}

        self._state_size = 0

        if machines is not None:

            for machine in machines:

                self.add_machine(
                    machine
                )

    # ==================================================================
    # MACHINE REGISTRATION
    # ==================================================================

    def add_machine(
        self,
        machine: ClassicalSynchronousMachine,
    ) -> None:
        """
        Register a dynamic machine.

        Machine IDs must be unique.
        """

        if not hasattr(
            machine,
            "machine_id",
        ):

            raise MultiMachineError(
                "Machine must expose "
                "machine_id."
            )

        machine_id = str(
            machine.machine_id
        )

        if machine_id in self._entries:

            raise DuplicateMachineError(
                f"Machine '{machine_id}' "
                "is already registered."
            )

        if not hasattr(
            machine,
            "state_size",
        ):

            raise MultiMachineError(
                f"Machine '{machine_id}' "
                "does not expose "
                "state_size."
            )

        state_size = int(
            machine.state_size
        )

        if state_size <= 0:

            raise StateLayoutError(
                f"Machine '{machine_id}' "
                "has invalid state size."
            )

        state_slice = (
            MachineStateSlice(
                machine_id=machine_id,
                start=self._state_size,
                stop=(
                    self._state_size
                    + state_size
                ),
            )
        )

        entry = MachineEntry(
            machine=machine,
            state_slice=state_slice,
        )

        self._machines.append(
            machine
        )

        self._entries[
            machine_id
        ] = entry

        self._state_size += state_size

    def remove_machine(
        self,
        machine_id: str,
    ) -> None:
        """
        Remove a machine and rebuild the global state layout.
        """

        machine_id = str(
            machine_id
        )

        if machine_id not in self._entries:

            raise UnknownMachineError(
                f"Unknown machine "
                f"'{machine_id}'."
            )

        self._machines = [
            machine
            for machine in self._machines
            if (
                machine.machine_id
                != machine_id
            )
        ]

        self._rebuild_layout()

    # ==================================================================
    # ACCESS
    # ==================================================================

    @property
    def machines(
        self,
    ) -> tuple[
        ClassicalSynchronousMachine,
        ...,
    ]:
        """Return registered machines."""

        return tuple(
            self._machines
        )

    @property
    def machine_count(
        self,
    ) -> int:
        """Return number of registered machines."""

        return len(
            self._machines
        )

    @property
    def state_size(
        self,
    ) -> int:
        """Return total global dynamic-state size."""

        return self._state_size

    @property
    def state_layout(
        self,
    ) -> tuple[
        MachineStateSlice,
        ...,
    ]:
        """Return global state layout in machine order."""

        return tuple(
            self._entries[
                machine.machine_id
            ].state_slice
            for machine in self._machines
        )

    def get_machine(
        self,
        machine_id: str,
    ) -> ClassicalSynchronousMachine:
        """Return a machine by ID."""

        try:

            return self._entries[
                str(machine_id)
            ].machine

        except KeyError as exc:

            raise UnknownMachineError(
                f"Unknown machine "
                f"'{machine_id}'."
            ) from exc

    def get_state_slice(
        self,
        machine_id: str,
    ) -> MachineStateSlice:
        """Return the global state slice for a machine."""

        try:

            return self._entries[
                str(machine_id)
            ].state_slice

        except KeyError as exc:

            raise UnknownMachineError(
                f"Unknown machine "
                f"'{machine_id}'."
            ) from exc

    # ==================================================================
    # STATE PACKING
    # ==================================================================

    def pack_states(
        self,
        machine_states: Mapping[
            str,
            np.ndarray,
        ],
    ) -> np.ndarray:
        """
        Pack machine-local states into one global vector.

        Parameters
        ----------
        machine_states:
            Mapping:

                machine_id -> local state vector

        Returns
        -------
        numpy.ndarray
            Global dynamic state vector.
        """

        x = np.zeros(
            self._state_size,
            dtype=float,
        )

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            if machine_id not in (
                machine_states
            ):

                raise StateLayoutError(
                    f"Missing state for "
                    f"machine '{machine_id}'."
                )

            local_state = (
                machine.validate_state(
                    machine_states[
                        machine_id
                    ]
                )
            )

            state_slice = (
                self.get_state_slice(
                    machine_id
                )
            )

            if local_state.size != (
                state_slice.size
            ):

                raise StateLayoutError(
                    f"State size mismatch "
                    f"for machine "
                    f"'{machine_id}'."
                )

            x[
                state_slice.start:
                state_slice.stop
            ] = local_state

        return x

    def unpack_states(
        self,
        state: np.ndarray,
    ) -> dict[
        str,
        np.ndarray,
    ]:
        """
        Split a global state vector into machine-local states.
        """

        x = self.validate_global_state(
            state
        )

        result: dict[
            str,
            np.ndarray,
        ] = {}

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            state_slice = (
                self.get_state_slice(
                    machine_id
                )
            )

            result[
                machine_id
            ] = x[
                state_slice.start:
                state_slice.stop
            ].copy()

        return result

    def pack_current_states(
        self,
        states: Mapping[
            str,
            np.ndarray,
        ],
    ) -> np.ndarray:
        """
        Alias for explicit current-state packing.
        """

        return self.pack_states(
            states
        )

    # ==================================================================
    # INITIALIZATION
    # ==================================================================

    def initial_state(
        self,
        terminal_voltages: Mapping[
            str,
            complex,
        ],
        electrical_powers: Mapping[
            str,
            object,
        ] | None = None,
        mechanical_powers: Mapping[
            str,
            float,
        ] | None = None,
    ) -> np.ndarray:
        """
        Build the global initial dynamic state.

        Parameters
        ----------
        terminal_voltages:
            Mapping:

                bus_id -> complex terminal voltage

        electrical_powers:
            Optional mapping:

                machine_id -> electrical power

        mechanical_powers:
            Optional mapping:

                machine_id -> mechanical power
        """

        electrical_powers = (
            {}
            if electrical_powers is None
            else electrical_powers
        )

        mechanical_powers = (
            {}
            if mechanical_powers is None
            else mechanical_powers
        )

        local_states: dict[
            str,
            np.ndarray
        ] = {}

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            bus_id = machine.bus_id

            if bus_id not in (
                terminal_voltages
            ):

                raise MultiMachineError(
                    f"Missing terminal "
                    f"voltage for bus "
                    f"'{bus_id}'."
                )

            local_states[
                machine_id
            ] = machine.initial_state(
                terminal_voltage=(
                    terminal_voltages[
                        bus_id
                    ]
                ),
                electrical_power=(
                    electrical_powers.get(
                        machine_id
                    )
                ),
                mechanical_power=(
                    mechanical_powers.get(
                        machine_id
                    )
                ),
            )

        return self.pack_states(
            local_states
        )

    # ==================================================================
    # ELECTRICAL OUTPUTS
    # ==================================================================

    def electrical_outputs(
        self,
        state: np.ndarray,
        terminal_voltages: Mapping[
            str,
            complex
        ],
        *,
        field_voltages: Mapping[str, float] | None = None,
    ) -> dict[
        str,
        MachineElectricalOutput
    ]:
        """
        Calculate electrical output of every machine.
        """

        local_states = (
            self.unpack_states(
                state
            )
        )
        field_voltages = self._validate_field_voltages(field_voltages)

        outputs: dict[
            str,
            MachineElectricalOutput
        ] = {}

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            bus_id = machine.bus_id

            if bus_id not in (
                terminal_voltages
            ):

                raise MultiMachineError(
                    f"Missing terminal "
                    f"voltage for bus "
                    f"'{bus_id}'."
                )

            outputs[
                machine_id
            ] = machine.electrical_output(
                state=local_states[
                    machine_id
                ],
                terminal_voltage=(
                    terminal_voltages[
                        bus_id
                    ]
                ),
                field_voltage=field_voltages.get(machine_id),
            )

        return outputs

    def electrical_powers(
        self,
        state: np.ndarray,
        terminal_voltages: Mapping[
            str,
            complex
        ],
        *,
        field_voltages: Mapping[str, float] | None = None,
    ) -> dict[
        str,
        float
    ]:
        """
        Return active electrical power Pe for every machine.
        """

        outputs = (
            self.electrical_outputs(
                state,
                terminal_voltages,
                field_voltages=field_voltages,
            )
        )

        return {
            machine_id:
            output.active_power
            for (
                machine_id,
                output
            ) in outputs.items()
        }

    # ==================================================================
    # DIFFERENTIAL EQUATIONS
    # ==================================================================

    def derivatives(
        self,
        state: np.ndarray,
        terminal_voltages: Mapping[
            str,
            complex
        ],
        mechanical_powers: Mapping[
            str,
            float
        ],
        *,
        machine_connection_states: Mapping[str, bool] | None = None,
        field_voltages: Mapping[str, float] | None = None,
        time: float = 0.0,
    ) -> np.ndarray:
        """
        Evaluate the global machine differential equations.
        """

        x = self.validate_global_state(
            state
        )
        machine_ids = {machine.machine_id for machine in self._machines}
        if machine_connection_states is not None:
            if set(machine_connection_states) != machine_ids:
                raise MultiMachineError(
                    "Machine connection-state identities must match exactly the registered dynamic machines."
                )
            if any(type(value) is not bool for value in machine_connection_states.values()):
                raise MultiMachineError("Machine connection states must be explicit bool values.")
        field_voltages = self._validate_field_voltages(field_voltages)

        local_states = (
            self.unpack_states(
                x
            )
        )

        derivative = np.zeros(
            self._state_size,
            dtype=float,
        )

        electrical_outputs = (
            self.electrical_outputs(
                state=x,
                terminal_voltages=(
                    terminal_voltages
                ),
                field_voltages=field_voltages,
            )
        )

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            if machine_id not in (
                mechanical_powers
            ):

                raise MultiMachineError(
                    f"Missing mechanical "
                    f"power for machine "
                    f"'{machine_id}'."
                )

            local_derivative = (
                machine.derivatives(
                    state=local_states[
                        machine_id
                    ],
                    terminal_voltage=(
                        terminal_voltages[
                            machine.bus_id
                        ]
                    ),
                    mechanical_power=(
                        mechanical_powers[
                            machine_id
                        ]
                    ),
                    electrical_power=(
                        electrical_outputs[machine_id].active_power
                        if machine_connection_states is None or machine_connection_states[machine_id]
                        else 0.0
                    ),
                    field_voltage=field_voltages.get(machine_id),
                    time=time,
                )
            )

            state_slice = (
                self.get_state_slice(
                    machine_id
                )
            )

            derivative[
                state_slice.start:
                state_slice.stop
            ] = local_derivative

        if not np.all(
            np.isfinite(
                derivative
            )
        ):

            raise MultiMachineError(
                "Global derivative "
                "contains non-finite "
                "values."
            )

        return derivative

    def _validate_field_voltages(
        self,
        field_voltages: Mapping[str, float] | None,
    ) -> dict[str, float]:
        """Validate optional AVR outputs keyed by canonical machine identity."""
        if field_voltages is None:
            return {}
        if not isinstance(field_voltages, Mapping):
            raise MultiMachineError("field_voltages must be a machine-to-voltage mapping")
        values: dict[str, float] = {}
        for machine_id, value in field_voltages.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise MultiMachineError("field-voltage keys must be non-empty machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise MultiMachineError(f"field voltage for {machine_id!r} must be numeric")
            numeric = float(value)
            if not np.isfinite(numeric) or numeric < 0.0:
                raise MultiMachineError(f"field voltage for {machine_id!r} must be finite and non-negative")
            values[machine_id] = numeric
        known_machine_ids = {machine.machine_id for machine in self._machines}
        unknown = set(values) - known_machine_ids
        if unknown:
            raise MultiMachineError(
                f"Field-voltage inputs reference unknown machines: {sorted(unknown)}"
            )
        return values

    # ==================================================================
    # VALIDATION
    # ==================================================================

    def validate_global_state(
        self,
        state: np.ndarray,
    ) -> np.ndarray:
        """
        Validate the complete global dynamic-state vector.
        """

        x = np.asarray(
            state,
            dtype=float,
        )

        if x.ndim != 1:

            raise StateLayoutError(
                "Global state must be "
                "one-dimensional."
            )

        if x.size != (
            self._state_size
        ):

            raise StateLayoutError(
                "Global state size "
                f"must be {self._state_size}; "
                f"received {x.size}."
            )

        if not np.all(
            np.isfinite(x)
        ):

            raise StateLayoutError(
                "Global state contains "
                "non-finite values."
            )

        return x

    # ==================================================================
    # INTERNAL
    # ==================================================================

    def _rebuild_layout(
        self,
    ) -> None:
        """
        Rebuild state slices after machine removal.
        """

        self._entries.clear()

        offset = 0

        for machine in self._machines:

            machine_id = (
                machine.machine_id
            )

            state_size = int(
                machine.state_size
            )

            state_slice = (
                MachineStateSlice(
                    machine_id=machine_id,
                    start=offset,
                    stop=(
                        offset
                        + state_size
                    ),
                )
            )

            self._entries[
                machine_id
            ] = MachineEntry(
                machine=machine,
                state_slice=state_slice,
            )

            offset += state_size

        self._state_size = offset


# ======================================================================
# CONVENIENCE FACTORY
# ======================================================================


def create_multimachine_system(
    machines: Iterable[
        ClassicalSynchronousMachine
    ],
) -> MultiMachineSystem:
    """
    Construct a MultiMachineSystem from an iterable of machines.
    """

    return MultiMachineSystem(
        machines=machines
    )


__all__ = [
    "MultiMachineError",
    "DuplicateMachineError",
    "UnknownMachineError",
    "StateLayoutError",
    "MachineStateSlice",
    "MachineEntry",
    "MultiMachineSystem",
    "create_multimachine_system",
]
