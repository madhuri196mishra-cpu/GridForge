# ============================================================
# File: core/model/grid.py
# GridForge V2 — Grid Source Model
# Author: Subhendu Mishra
# ============================================================

"""
GridForge V2 — Grid Source Model
================================

Authoritative external-grid / utility-source electrical model.

Architecture
------------

    ElectricalObject
          +
       Injection
          |
          v
         Grid
          |
          v
       Terminal
          |
          v
       Endpoint
          |
          v
       Network

Grid owns:

    - source identity
    - nominal voltage
    - frequency
    - voltage magnitude / angle
    - P/Q injection
    - short-circuit data
    - sequence impedance data
    - grounding state
    - operating state
    - optional engineering extensions
    - exactly one authoritative Terminal

Grid does NOT own:

    - Network topology
    - Bus collections
    - global graph state
    - Y-bus construction
    - power-flow solving
    - short-circuit solving
    - SLD geometry
    - UI state

Terminal Contract
-----------------

Grid owns exactly one authoritative Terminal.

Terminal owns endpoint connectivity.

Canonical operations are:

    connect_endpoint(endpoint)
        -> Terminal.attach(endpoint)

    disconnect_endpoint()
        -> Terminal.detach()

Endpoint state is never duplicated inside Grid.

Power Convention
----------------

Grid is an injection source:

    P > 0 -> active power injected
    Q > 0 -> reactive power injected

An out-of-service Grid contributes:

    P = 0
    Q = 0

Grounding and sequence-impedance data remain engineering
parameters and do not constitute network topology.

Copyright © 2026 Subhendu Mishra
All Rights Reserved.
"""

from __future__ import annotations

import math
from typing import Any

from .base import ElectricalObject
from .injection import Injection
from .terminal import Terminal


class Grid(ElectricalObject, Injection):
    """
    External electrical grid / utility source.

    Positive P/Q values represent injection into the network.
    """

    TYPE = "GRID"

    def __init__(
        self,
        id: str,
        *,
        endpoint: Any = None,
        terminal: Terminal | None = None,
        bus: Any = None,
        name: str = "",
        nominal_voltage_kv: float = 0.0,
        frequency_hz: float = 50.0,
        voltage_pu: float = 1.0,
        angle_deg: float = 0.0,
        p_mw: float = 0.0,
        q_mvar: float = 0.0,
        short_circuit_mva: float | None = None,
        x_over_r: float | None = None,
        z1_pu: complex | None = None,
        z2_pu: complex | None = None,
        z0_pu: complex | None = None,
        impedance_base_mva: float | None = None,
        impedance_base_voltage_kv: float | None = None,
        neutral_grounding_impedance_ohm: complex | None = None,
        in_service: bool = True,
        grounded: bool = True,
    ) -> None:
        """
        Construct an external Grid source.

        Parameters
        ----------
        id:
            Stable GridForge object identifier.

        endpoint:
            Initial electrical endpoint.

        terminal:
            Optional pre-created authoritative Terminal.

        bus:
            Compatibility alias for the initial endpoint.

        name:
            Human-readable source name.

        nominal_voltage_kv:
            Nominal source voltage in kV.

        frequency_hz:
            System frequency in Hz.

        voltage_pu:
            Source voltage magnitude in per-unit.

        angle_deg:
            Source voltage angle in degrees.

        p_mw:
            Active-power injection in MW.

        q_mvar:
            Reactive-power injection in MVAr.

        short_circuit_mva:
            Optional short-circuit level in MVA.

        x_over_r:
            Optional X/R ratio.

        z1_pu:
            Positive-sequence impedance.

        z2_pu:
            Negative-sequence impedance.

        z0_pu:
            Zero-sequence impedance.

        in_service:
            Whether the source is operationally in service.

        grounded:
            Whether the source is grounded.

        neutral_grounding_impedance_ohm:
            Explicit external-source neutral grounding impedance. Required
            by phase-domain short-circuit preparation together with the
            physical N terminal and GROUND_ELECTRODE graph connection.

        Notes
        -----
        ``endpoint`` and ``bus`` are aliases for initial endpoint
        selection only. Endpoint state is subsequently owned by
        Terminal.

        An externally supplied Terminal must already belong to this
        Grid. Terminal ownership is not mutated by Grid.
        """

        ElectricalObject.__init__(
            self,
            id=id,
            name=name,
        )

        # ========================================================
        # ENDPOINT COMPATIBILITY
        # ========================================================

        if (
            endpoint is not None
            and bus is not None
            and endpoint is not bus
        ):
            raise ValueError(
                f"Grid '{self.id}' received both endpoint and "
                "bus with different values."
            )

        if endpoint is None:
            endpoint = bus

        # ========================================================
        # ELECTRICAL PARAMETERS
        # ========================================================

        self.nominal_voltage_kv = (
            self._validate_non_negative(
                nominal_voltage_kv,
                "nominal_voltage_kv",
            )
        )

        self.frequency_hz = (
            self._validate_positive(
                frequency_hz,
                "frequency_hz",
            )
        )

        self.voltage_pu = (
            self._validate_positive(
                voltage_pu,
                "voltage_pu",
            )
        )

        self.angle_deg = (
            self._validate_finite(
                angle_deg,
                "angle_deg",
            )
        )

        self.p_mw = (
            self._validate_finite(
                p_mw,
                "p_mw",
            )
        )

        self.q_mvar = (
            self._validate_finite(
                q_mvar,
                "q_mvar",
            )
        )

        self.short_circuit_mva = (
            self._validate_optional_positive(
                short_circuit_mva,
                "short_circuit_mva",
            )
        )

        self.x_over_r = (
            self._validate_optional_positive(
                x_over_r,
                "x_over_r",
            )
        )

        self.z1_pu = (
            self._validate_optional_impedance(
                z1_pu,
                "z1_pu",
            )
        )

        self.z2_pu = (
            self._validate_optional_impedance(
                z2_pu,
                "z2_pu",
            )
        )

        self.z0_pu = (
            self._validate_optional_impedance(
                z0_pu,
                "z0_pu",
            )
        )
        # Per-unit source impedances retain their original equipment base.
        self.impedance_base_mva = self._validate_optional_positive(
            impedance_base_mva, "impedance_base_mva"
        )
        self.impedance_base_voltage_kv = self._validate_optional_positive(
            impedance_base_voltage_kv, "impedance_base_voltage_kv"
        )
        self.neutral_grounding_impedance_ohm = self._validate_optional_impedance(
            neutral_grounding_impedance_ohm, "neutral_grounding_impedance_ohm"
        )

        # ========================================================
        # OPERATING STATE
        # ========================================================

        self.in_service = self._validate_bool(
            in_service,
            "in_service",
        )

        self.grounded = self._validate_bool(
            grounded,
            "grounded",
        )

        # ========================================================
        # AUTHORITATIVE PHYSICAL TERMINAL
        # ========================================================

        if terminal is None:
            self._terminal = Terminal(
                owner=self,
                role="terminal",
            )
        else:
            if not isinstance(
                terminal,
                Terminal,
            ):
                raise TypeError(
                    "terminal must be a Terminal."
                )

            if terminal.owner is not self:
                raise ValueError(
                    f"Grid '{self.id}' terminal owner "
                    "must be this Grid."
                )

            if terminal.role != "terminal":
                raise ValueError(
                    "Grid terminal role must be 'terminal'."
                )

            self._terminal = terminal

        # ========================================================
        # INITIAL ENDPOINT
        # ========================================================

        if endpoint is not None:
            self.connect_endpoint(
                endpoint
            )

        # ========================================================
        # OPTIONAL ENGINEERING EXTENSIONS
        # ========================================================

        self._extensions: dict[str, Any] = {}

        # ========================================================
        # COMMON MODEL VALIDATION
        # ========================================================

        self.validate()

    # ============================================================
    # IDENTITY
    # ============================================================

    @property
    def element_type(self) -> str:
        """Return the canonical GridForge element type."""

        return self.TYPE

    # ============================================================
    # TERMINAL
    # ============================================================

    @property
    def terminal(self) -> Terminal:
        """
        Return the authoritative physical Terminal.
        """

        return self._terminal

    @property
    def terminals(self) -> tuple[Terminal, ...]:
        """
        Return the Grid's physical terminal collection.

        Grid has exactly one terminal.
        """

        return (
            self._terminal,
        )

    # ============================================================
    # CONNECTIVITY
    # ============================================================

    @property
    def endpoint(self) -> Any | None:
        """
        Return the authoritative physical endpoint.

        Terminal.endpoint is the sole source of endpoint truth.
        """

        return self._terminal.endpoint

    @property
    def bus(self) -> Any | None:
        """
        Compatibility accessor for the historical bus API.

        Bus state is derived from Terminal and is never stored
        independently by Grid.
        """

        return self._terminal.endpoint

    @bus.setter
    def bus(
        self,
        value: Any,
    ) -> None:
        """
        Compatibility setter routed through the canonical
        Terminal API.
        """

        self.connect_endpoint(
            value
        )

    @property
    def is_connected(self) -> bool:
        """Return whether the Grid terminal has an endpoint."""

        return self._terminal.is_connected

    def connect_endpoint(
        self,
        endpoint: Any,
    ) -> None:
        """
        Attach the Grid terminal to an electrical endpoint.

        This modifies Terminal-local connectivity only.

        It does not directly modify global Network topology.
        """

        if endpoint is None:
            raise ValueError(
                f"Grid '{self.id}' endpoint cannot be None."
            )

        self._terminal.attach(
            endpoint
        )

    def disconnect_endpoint(self) -> None:
        """
        Detach the Grid terminal.

        This modifies Terminal-local connectivity only.
        """

        self._terminal.detach()

    # ============================================================
    # OPERATING STATE
    # ============================================================

    @property
    def is_in_service(self) -> bool:
        """Return whether the Grid source is in service."""

        return self.in_service

    @property
    def is_out_of_service(self) -> bool:
        """Return whether the Grid source is out of service."""

        return not self.in_service

    @property
    def is_available(self) -> bool:
        """Return whether the source is available for injection."""

        return self.in_service

    def put_in_service(self) -> None:
        """Place the Grid source in service."""

        self.in_service = True

    def take_out_of_service(self) -> None:
        """Take the Grid source out of service."""

        self.in_service = False

    def set_in_service(
        self,
        value: bool,
    ) -> None:
        """Set Grid operational state."""

        self.in_service = self._validate_bool(
            value,
            "in_service",
        )

    # ============================================================
    # SERVICE COMPATIBILITY ALIASES
    # ============================================================

    def connect(self) -> None:
        """
        Compatibility alias for putting the Grid source in service.

        This does NOT connect the Terminal.
        """

        self.put_in_service()

    def disconnect(self) -> None:
        """
        Compatibility alias for taking the Grid source out of
        service.

        This does NOT disconnect the Terminal.
        """

        self.take_out_of_service()

    def close(self) -> None:
        """Compatibility alias for put_in_service()."""

        self.put_in_service()

    def trip(self) -> None:
        """Compatibility alias for take_out_of_service()."""

        self.take_out_of_service()

    # ============================================================
    # INJECTION CONTRACT
    # ============================================================

    def get_power(self) -> tuple[float, float]:
        """
        Return Grid network injection.

        Positive P/Q represent injection into the network.

        An out-of-service Grid contributes zero injection.
        """

        if not self.in_service:
            return (
                0.0,
                0.0,
            )

        return (
            self.p_mw,
            self.q_mvar,
        )

    def set_power(
        self,
        p_mw: float,
        q_mvar: float,
    ) -> None:
        """Set Grid active/reactive power injection."""

        self.p_mw = self._validate_finite(
            p_mw,
            "p_mw",
        )

        self.q_mvar = self._validate_finite(
            q_mvar,
            "q_mvar",
        )

    @property
    def active_power_mw(self) -> float:
        """Return configured active-power injection."""

        return self.p_mw

    @property
    def reactive_power_mvar(self) -> float:
        """Return configured reactive-power injection."""

        return self.q_mvar

    @property
    def active_power_injection_mw(self) -> float:
        """Return effective active-power injection."""

        return self.get_power()[0]

    @property
    def reactive_power_injection_mvar(self) -> float:
        """Return effective reactive-power injection."""

        return self.get_power()[1]

    # ============================================================
    # VOLTAGE
    # ============================================================

    def set_voltage(
        self,
        voltage_pu: float,
        angle_deg: float = 0.0,
    ) -> None:
        """
        Set source voltage magnitude and angle.
        """

        self.voltage_pu = (
            self._validate_positive(
                voltage_pu,
                "voltage_pu",
            )
        )

        self.angle_deg = (
            self._validate_finite(
                angle_deg,
                "angle_deg",
            )
        )

    # ============================================================
    # SEQUENCE IMPEDANCE
    # ============================================================

    def set_sequence_impedances(
        self,
        *,
        z1_pu: complex | None = None,
        z2_pu: complex | None = None,
        z0_pu: complex | None = None,
    ) -> None:
        """
        Set positive-, negative-, and zero-sequence impedances.
        """

        self.z1_pu = (
            self._validate_optional_impedance(
                z1_pu,
                "z1_pu",
            )
        )

        self.z2_pu = (
            self._validate_optional_impedance(
                z2_pu,
                "z2_pu",
            )
        )

        self.z0_pu = (
            self._validate_optional_impedance(
                z0_pu,
                "z0_pu",
            )
        )

    def set_neutral_grounding_impedance(self, impedance_ohm: complex | None) -> None:
        """Set the external-source neutral grounding impedance."""
        self.neutral_grounding_impedance_ohm = self._validate_optional_impedance(
            impedance_ohm,
            "neutral_grounding_impedance_ohm",
        )

    def has_sequence_impedance_data(self) -> bool:
        """
        Return whether positive-sequence impedance data exists.
        """

        return self.z1_pu is not None

    # ============================================================
    # VALIDATION
    # ============================================================

    def validate_parameters(self) -> bool:
        """
        Validate Grid-local engineering parameters.

        Network topology is deliberately not validated here.
        """

        self.nominal_voltage_kv = (
            self._validate_non_negative(
                self.nominal_voltage_kv,
                "nominal_voltage_kv",
            )
        )

        self.frequency_hz = (
            self._validate_positive(
                self.frequency_hz,
                "frequency_hz",
            )
        )

        self.voltage_pu = (
            self._validate_positive(
                self.voltage_pu,
                "voltage_pu",
            )
        )

        self.angle_deg = (
            self._validate_finite(
                self.angle_deg,
                "angle_deg",
            )
        )

        self.p_mw = (
            self._validate_finite(
                self.p_mw,
                "p_mw",
            )
        )

        self.q_mvar = (
            self._validate_finite(
                self.q_mvar,
                "q_mvar",
            )
        )

        self.short_circuit_mva = (
            self._validate_optional_positive(
                self.short_circuit_mva,
                "short_circuit_mva",
            )
        )

        self.x_over_r = (
            self._validate_optional_positive(
                self.x_over_r,
                "x_over_r",
            )
        )

        self.z1_pu = (
            self._validate_optional_impedance(
                self.z1_pu,
                "z1_pu",
            )
        )

        self.z2_pu = (
            self._validate_optional_impedance(
                self.z2_pu,
                "z2_pu",
            )
        )

        self.z0_pu = (
            self._validate_optional_impedance(
                self.z0_pu,
                "z0_pu",
            )
        )
        self.impedance_base_mva = self._validate_optional_positive(self.impedance_base_mva, "impedance_base_mva")
        self.impedance_base_voltage_kv = self._validate_optional_positive(self.impedance_base_voltage_kv, "impedance_base_voltage_kv")
        self.neutral_grounding_impedance_ohm = self._validate_optional_impedance(
            self.neutral_grounding_impedance_ohm,
            "neutral_grounding_impedance_ohm",
        )
        if (self.impedance_base_mva is None) != (self.impedance_base_voltage_kv is None):
            raise ValueError("Grid impedance base requires both MVA and voltage bases.")
        if any(value is not None for value in (self.z1_pu, self.z2_pu, self.z0_pu)) and (
            self.impedance_base_mva is None or self.impedance_base_voltage_kv is None
        ):
            raise ValueError("Grid per-unit sequence impedances require explicit impedance_base_mva and impedance_base_voltage_kv.")

        self.in_service = self._validate_bool(
            self.in_service,
            "in_service",
        )

        self.grounded = self._validate_bool(
            self.grounded,
            "grounded",
        )

        if self._terminal.owner is not self:
            raise ValueError(
                f"Grid '{self.id}' terminal ownership is invalid."
            )

        if self._terminal.role != "terminal":
            raise ValueError(
                "Grid terminal role must be 'terminal'."
            )

        self._terminal.validate()

        return True

    def validate(self) -> bool:
        """
        Validate the complete Grid model.

        ElectricalObject provides the common model-level
        validation contract.
        """

        self.validate_parameters()

        return super().validate()

    # ============================================================
    # EXTENSIONS
    # ============================================================

    def register_extension(
        self,
        extension_id: str,
        extension: Any,
    ) -> None:
        """
        Register an optional engineering extension.
        """

        if not isinstance(
            extension_id,
            str,
        ):
            raise TypeError(
                "extension_id must be a string."
            )

        extension_id = extension_id.strip()

        if not extension_id:
            raise ValueError(
                "extension_id cannot be empty."
            )

        if extension is None:
            raise ValueError(
                "extension cannot be None."
            )

        if extension_id in self._extensions:
            raise ValueError(
                f"Extension '{extension_id}' is already registered."
            )

        self._extensions[
            extension_id
        ] = extension

    def get_extension(
        self,
        extension_id: str,
    ) -> Any | None:
        """Return an extension, or None."""

        return self._extensions.get(
            extension_id
        )

    def remove_extension(
        self,
        extension_id: str,
    ) -> Any | None:
        """Remove and return an extension."""

        return self._extensions.pop(
            extension_id,
            None,
        )

    @property
    def extension_ids(self) -> tuple[str, ...]:
        """Return registered extension identifiers."""

        return tuple(
            self._extensions.keys()
        )

    # ============================================================
    # DIAGNOSTICS
    # ============================================================

    def summary(self) -> dict[str, Any]:
        """Return a structured Grid diagnostic summary."""

        endpoint = self._terminal.endpoint

        endpoint_id = None

        if endpoint is not None:
            endpoint_id = getattr(
                endpoint,
                "id",
                endpoint,
            )

        return {
            "id": self.id,
            "name": self.name,
            "type": self.TYPE,

            "terminal": self._terminal,
            "terminal_role": self._terminal.role,

            "endpoint": endpoint_id,
            "bus": endpoint_id,
            "is_connected":
                self._terminal.is_connected,

            "nominal_voltage_kv":
                self.nominal_voltage_kv,

            "frequency_hz":
                self.frequency_hz,

            "voltage_pu":
                self.voltage_pu,

            "angle_deg":
                self.angle_deg,

            "p_mw":
                self.p_mw,

            "q_mvar":
                self.q_mvar,

            "injection":
                self.get_power(),

            "short_circuit_mva":
                self.short_circuit_mva,

            "x_over_r":
                self.x_over_r,

            "z1_pu":
                self.z1_pu,

            "z2_pu":
                self.z2_pu,

            "z0_pu":
                self.z0_pu,

            "impedance_base_mva":
                self.impedance_base_mva,

            "impedance_base_voltage_kv":
                self.impedance_base_voltage_kv,

            "neutral_grounding_impedance_ohm":
                self.neutral_grounding_impedance_ohm,

            "in_service":
                self.in_service,

            "grounded":
                self.grounded,

            "extensions":
                self.extension_ids,
        }

    # ============================================================
    # REPRESENTATION
    # ============================================================

    def __repr__(self) -> str:
        """Return concise developer-facing representation."""

        endpoint = self._terminal.endpoint

        endpoint_id = None

        if endpoint is not None:
            endpoint_id = getattr(
                endpoint,
                "id",
                endpoint,
            )

        return (
            f"<Grid "
            f"id={self.id}, "
            f"endpoint={endpoint_id}, "
            f"P={self.p_mw:.6f} MW, "
            f"Q={self.q_mvar:.6f} MVAr, "
            f"V={self.voltage_pu:.6f} pu, "
            f"in_service={self.in_service}>"
        )

    # ============================================================
    # VALIDATION HELPERS
    # ============================================================

    @staticmethod
    def _validate_finite(
        value: float,
        name: str,
    ) -> float:
        """Convert to float and require a finite value."""

        try:
            numeric = float(value)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                f"{name} must be numeric."
            ) from exc

        if not math.isfinite(
            numeric
        ):
            raise ValueError(
                f"{name} must be finite."
            )

        return numeric

    @classmethod
    def _validate_positive(
        cls,
        value: float,
        name: str,
    ) -> float:
        """Convert to float and require value > 0."""

        numeric = cls._validate_finite(
            value,
            name,
        )

        if numeric <= 0.0:
            raise ValueError(
                f"{name} must be greater than zero."
            )

        return numeric

    @classmethod
    def _validate_non_negative(
        cls,
        value: float,
        name: str,
    ) -> float:
        """Convert to float and require value >= 0."""

        numeric = cls._validate_finite(
            value,
            name,
        )

        if numeric < 0.0:
            raise ValueError(
                f"{name} cannot be negative."
            )

        return numeric

    @classmethod
    def _validate_optional_positive(
        cls,
        value: float | None,
        name: str,
    ) -> float | None:
        """Validate an optional positive value."""

        if value is None:
            return None

        return cls._validate_positive(
            value,
            name,
        )

    @classmethod
    def _validate_optional_impedance(
        cls,
        value: complex | None,
        name: str,
    ) -> complex | None:
        """
        Validate an optional complex impedance.

        Both real and imaginary components must be finite.
        """

        if value is None:
            return None

        if not isinstance(
            value,
            complex,
        ):
            try:
                value = complex(
                    value
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ValueError(
                    f"{name} must be a complex impedance."
                ) from exc

        if not (
            math.isfinite(
                value.real
            )
            and math.isfinite(
                value.imag
            )
        ):
            raise ValueError(
                f"{name} must contain finite real "
                "and imaginary components."
            )

        return value

    @staticmethod
    def _validate_bool(
        value: bool,
        name: str,
    ) -> bool:
        """Validate a strict boolean."""

        if not isinstance(
            value,
            bool,
        ):
            raise TypeError(
                f"{name} must be boolean."
            )

        return value


__all__ = [
    "Grid",
]
