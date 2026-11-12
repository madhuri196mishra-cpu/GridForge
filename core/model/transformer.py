# ============================================================
# File: core/model/transformer.py
# GridForge V2 — Transformer Model
# Author: Subhendu Mishra
# ============================================================

"""Authoritative two-terminal transformer model.

Transformer ``r/x/b`` values require an explicit impedance basis.
For engineering values the reference voltage is explicit. For PU
values the original MVA base is explicit (or, for compatibility,
derived from the transformer rated MVA supplied as ``rate_mva``).
No unit or base inference is performed by numerical builders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.base.per_unit import PerUnitSystem

from .branch import Branch
from .transformer_zero_sequence_test_data import TransformerZeroSequenceTwoWindingTestData
from .transformer_zero_sequence_multiport_evidence import (
    TransformerZeroSequenceMultiportEvidence, TransformerDeltaLoopState,
    validate_transformer_zero_sequence_multiport_evidence,
)


class ImpedanceBasis(str, Enum):
    """Typed semantic basis for transformer r/x/b engineering values."""

    PU = "pu"
    ENGINEERING = "engineering"


class TransformerCoreConstruction(str, Enum):
    """Magnetic-core construction governing zero-sequence return paths."""
    THREE_LIMB = "THREE_LIMB"
    FIVE_LIMB = "FIVE_LIMB"
    SHELL_FORM = "SHELL_FORM"


class TransformerWindingConnection(str, Enum):
    """Physical connection of one transformer winding."""
    DELTA = "DELTA"
    WYE_GROUNDED = "WYE_GROUNDED"
    WYE_UNGROUNDED = "WYE_UNGROUNDED"


@dataclass(frozen=True, slots=True)
class TransformerWinding:
    """Canonical engineering identity and nameplate metadata for one winding.

    This record does not create an electrical terminal or network edge.
    """
    winding_id: str
    sequence: int
    role: str
    rated_voltage_kv: float | None = None
    rated_mva: float | None = None
    connection: TransformerWindingConnection | str | None = None
    phase_roles: tuple[str, ...] | None = None
    neutral_terminal_role: str | None = None
    terminal_role: str | None = None

    def __post_init__(self) -> None:
        for name in ("winding_id", "role"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a canonical non-empty string.")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("Winding sequence must be a positive integer.")
        for name in ("rated_voltage_kv", "rated_mva"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be finite and positive when declared.")
        connection = self.connection
        if connection is not None and not isinstance(connection, TransformerWindingConnection):
            try:
                connection = TransformerWindingConnection(str(connection).strip().upper())
            except ValueError as exc:
                raise ValueError("Winding connection is not supported.") from exc
            object.__setattr__(self, "connection", connection)
        if self.phase_roles is not None:
            if isinstance(self.phase_roles, (str, bytes)):
                raise TypeError("Winding phase_roles must be a tuple/list, not a string.")
            phases = tuple(str(item).strip().upper() for item in self.phase_roles)
            if not phases or len(phases) != len(set(phases)) or any(item not in {"A", "B", "C"} for item in phases):
                raise ValueError("Winding phase_roles must contain unique phase labels A, B, or C.")
            object.__setattr__(self, "phase_roles", phases)
        if self.neutral_terminal_role is not None and (not isinstance(self.neutral_terminal_role, str) or not self.neutral_terminal_role.strip()):
            raise ValueError("neutral_terminal_role must be a non-empty string when declared.")
        if self.neutral_terminal_role is not None and self.connection is not TransformerWindingConnection.WYE_GROUNDED:
            raise ValueError("neutral_terminal_role requires a grounded-wye winding connection.")
        if self.terminal_role is not None and self.terminal_role not in {"FROM", "TO"}:
            raise ValueError("terminal_role must be FROM or TO when declared.")


class Transformer(Branch):
    """Static two-terminal transformer with an explicit impedance basis.

    ``r``, ``x`` and ``b`` retain the established API. Their meaning is
    determined by ``impedance_basis``:

    * ``pu``: values are per-unit on ``impedance_base_mva`` and
      ``impedance_base_voltage_kv``.
    * ``engineering``: values are physical ohms/siemens referred to the
      declared ``impedance_base_voltage_kv`` side.

    ``rate_mva`` remains the existing transformer rating field and is
    exposed as ``rated_mva`` for engineering clarity.
    """

    TYPE = "TRANSFORMER"
    VALID_IMPEDANCE_BASES = frozenset(ImpedanceBasis)

    __slots__ = (
        "_tap",
        "_shift",
        "_impedance_basis",
        "_impedance_base_mva",
        "_impedance_base_voltage_kv",
        "_hv_winding_connection",
        "_lv_winding_connection",
        "_windings",
        "_hv_neutral_grounding_impedance_ohm",
        "_lv_neutral_grounding_impedance_ohm",
        "_hv_neutral_earth_return_impedance_ohm",
        "_lv_neutral_earth_return_impedance_ohm",
        "_hv_neutral_terminal_role",
        "_lv_neutral_terminal_role",
        "_hv_terminal_role", "_lv_terminal_role",
        "_hv_phase_roles", "_lv_phase_roles", "_impedance_reference_winding",
        "_zero_sequence_resistance_ohm", "_zero_sequence_reactance_ohm",
        "_core_construction", "_zero_sequence_test_basis",
        "_vector_group_configuration", "_zero_sequence_test_configuration",
        "_zero_sequence_two_winding_test_data",
        "_phase_domain_terminal_admittance_siemens",
        "_zero_sequence_multiport_evidence",
        "_phase_domain_delta_loop_states",
        "_phase_domain_multiport_tap_ratio", "_phase_domain_multiport_phase_shift_rad",
    )

    def __init__(
        self,
        id: str,
        endpoint_from: Any = None,
        endpoint_to: Any = None,
        *,
        r: float = 0.0,
        x: float = 0.0,
        b: float = 0.0,
        impedance_basis: ImpedanceBasis | str,
        impedance_base_mva: float | None = None,
        impedance_base_voltage_kv: float | None = None,
        hv_winding_connection: TransformerWindingConnection | str | None = None,
        lv_winding_connection: TransformerWindingConnection | str | None = None,
        windings: tuple[TransformerWinding, ...] = (),
        hv_neutral_grounding_impedance_ohm: complex | None = None,
        lv_neutral_grounding_impedance_ohm: complex | None = None,
        hv_neutral_earth_return_impedance_ohm: complex | None = None,
        lv_neutral_earth_return_impedance_ohm: complex | None = None,
        hv_neutral_terminal_role: str | None = None,
        lv_neutral_terminal_role: str | None = None,
        hv_phase_roles: tuple[str, ...] | None = None,
        lv_phase_roles: tuple[str, ...] | None = None,
        hv_terminal_role: str | None = None,
        lv_terminal_role: str | None = None,
        impedance_reference_winding: str | None = None,
        zero_sequence_resistance_ohm: float | None = None,
        zero_sequence_reactance_ohm: float | None = None,
        core_construction: TransformerCoreConstruction | str | None = None,
        zero_sequence_test_basis: str | None = None,
        vector_group_configuration: Any | None = None,
        zero_sequence_test_configuration: Any | None = None,
        zero_sequence_two_winding_test_data: TransformerZeroSequenceTwoWindingTestData | None = None,
        phase_domain_terminal_admittance_siemens: tuple[tuple[complex, ...], ...] | None = None,
        zero_sequence_multiport_evidence: TransformerZeroSequenceMultiportEvidence | None = None,
        phase_domain_delta_loop_states: tuple[TransformerDeltaLoopState, ...] = (),
        phase_domain_multiport_tap_ratio: float | None = None,
        phase_domain_multiport_phase_shift_rad: float | None = None,
        tap: float = 1.0,
        shift: float = 0.0,
        name: str = "",
        rate_mva: float | None = None,
        in_service: bool = True,
    ) -> None:
        if impedance_basis is None:
            raise ValueError(
                "Transformer impedance_basis is required; ambiguous r/x/b data is not accepted."
            )
        try:
            impedance_basis = (
                impedance_basis
                if isinstance(impedance_basis, ImpedanceBasis)
                else ImpedanceBasis(str(impedance_basis).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Transformer impedance_basis must be 'pu' or 'engineering'."
            ) from exc

        # The existing Branch ``rate_mva`` is the transformer nameplate
        # rating. It is a valid original MVA basis only when explicitly
        # supplied; no default base is invented.
        effective_base_mva = (
            impedance_base_mva if impedance_base_mva is not None else rate_mva
        )

        if effective_base_mva is None:
            raise ValueError(
                "Transformer impedance base MVA must be declared through "
                "impedance_base_mva or rate_mva."
            )

        effective_base_mva = self._validate_positive(
            effective_base_mva,
            "impedance_base_mva",
        )

        if impedance_base_voltage_kv is None:
            raise ValueError(
                "Transformer impedance_base_voltage_kv is required; "
                "the physical/reference voltage side must not be inferred."
            )
        impedance_base_voltage_kv = self._validate_positive(
            impedance_base_voltage_kv,
            "impedance_base_voltage_kv",
        )

        super().__init__(
            id=id,
            endpoint_from=endpoint_from,
            endpoint_to=endpoint_to,
            r=r,
            x=x,
            b=b,
            name=name,
            rate_mva=rate_mva,
            in_service=in_service,
        )

        self._impedance_basis = impedance_basis
        self._impedance_base_mva = effective_base_mva
        self._impedance_base_voltage_kv = impedance_base_voltage_kv
        self._tap = self._validate_positive(tap, "tap")
        self._shift = self._validate_finite(shift, "shift")
        self._hv_winding_connection = self._normalize_winding_connection(hv_winding_connection)
        self._lv_winding_connection = self._normalize_winding_connection(lv_winding_connection)
        self._windings = self._normalize_windings(windings)
        self._hv_neutral_grounding_impedance_ohm = self._validate_neutral_grounding(hv_neutral_grounding_impedance_ohm, self._hv_winding_connection, "HV")
        self._lv_neutral_grounding_impedance_ohm = self._validate_neutral_grounding(lv_neutral_grounding_impedance_ohm, self._lv_winding_connection, "LV")
        self._hv_neutral_earth_return_impedance_ohm = self._validate_neutral_earth_return(hv_neutral_earth_return_impedance_ohm, self._hv_winding_connection, "HV")
        self._lv_neutral_earth_return_impedance_ohm = self._validate_neutral_earth_return(lv_neutral_earth_return_impedance_ohm, self._lv_winding_connection, "LV")
        self._hv_neutral_terminal_role = self._validate_neutral_terminal_role(
            hv_neutral_terminal_role, self._hv_winding_connection, "HV"
        )
        self._lv_neutral_terminal_role = self._validate_neutral_terminal_role(
            lv_neutral_terminal_role, self._lv_winding_connection, "LV"
        )
        self._hv_phase_roles = self._validate_winding_phase_roles(hv_phase_roles, "HV")
        self._lv_phase_roles = self._validate_winding_phase_roles(lv_phase_roles, "LV")
        self._hv_terminal_role, self._lv_terminal_role = self._validate_terminal_mapping(hv_terminal_role, lv_terminal_role)
        self._validate_explicit_winding_compatibility()
        self._impedance_reference_winding = self._normalize_reference_winding(impedance_reference_winding)
        self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm = self._validate_zero_sequence_impedance(zero_sequence_resistance_ohm, zero_sequence_reactance_ohm)
        self._core_construction = self._normalize_core_construction(core_construction)
        self._zero_sequence_test_basis = self._normalize_zero_sequence_test_basis(zero_sequence_test_basis)
        self._vector_group_configuration = self._normalize_vector_group_configuration(vector_group_configuration)
        self._zero_sequence_test_configuration = self._normalize_zero_sequence_test_configuration(zero_sequence_test_configuration)
        self._zero_sequence_two_winding_test_data = self._normalize_zero_sequence_two_winding_test_data(zero_sequence_two_winding_test_data)
        (
            self._phase_domain_terminal_admittance_siemens,
            self._zero_sequence_multiport_evidence,
            self._phase_domain_delta_loop_states,
            self._phase_domain_multiport_tap_ratio,
            self._phase_domain_multiport_phase_shift_rad,
        ) = self._validate_phase_domain_multiport_configuration(
            matrix=phase_domain_terminal_admittance_siemens,
            evidence=zero_sequence_multiport_evidence,
            loop_states=phase_domain_delta_loop_states,
            tap_ratio=phase_domain_multiport_tap_ratio,
            phase_shift_rad=phase_domain_multiport_phase_shift_rad,
            tap=self._tap, shift=self._shift,
            hv_connection=self._hv_winding_connection, lv_connection=self._lv_winding_connection,
            hv_terminal_role=self._hv_terminal_role, lv_terminal_role=self._lv_terminal_role,
            hv_phase_roles=self._hv_phase_roles, lv_phase_roles=self._lv_phase_roles,
            hv_neutral_terminal_role=self._hv_neutral_terminal_role,
            lv_neutral_terminal_role=self._lv_neutral_terminal_role,
            core_construction=self._core_construction,
        )
        self._validate_zero_sequence_two_winding_test_data(self._zero_sequence_two_winding_test_data, self._hv_winding_connection, self._lv_winding_connection, self._vector_group_configuration)
        if self._zero_sequence_two_winding_test_data is not None and (self._hv_terminal_role is None or self._lv_terminal_role is None or self._impedance_reference_winding != "HV"):
            raise ValueError("Yg-Yg three-test data requires explicit distinct HV/LV terminal roles and HV impedance reference.")
        if self._zero_sequence_two_winding_test_data is not None and (self._core_construction is None or self._zero_sequence_test_basis is None):
            raise ValueError("Two-winding transformer Z0 test data requires explicit core_construction and zero_sequence_test_basis.")
        self._validate_zero_sequence_contracts(
            self._vector_group_configuration, self._zero_sequence_test_configuration,
            self._impedance_reference_winding, self._hv_winding_connection, self._lv_winding_connection,
            self._zero_sequence_resistance_ohm, self._zero_sequence_two_winding_test_data,
        )
        if self._zero_sequence_resistance_ohm is not None and (self._impedance_reference_winding is None or self._hv_terminal_role is None):
            raise ValueError("Transformer Z0 requires explicit impedance_reference_winding and HV/LV terminal mapping.")
        if self._zero_sequence_resistance_ohm is not None and (self._core_construction is None or self._zero_sequence_test_basis is None):
            raise ValueError("Transformer Z0 requires explicit core_construction and zero_sequence_test_basis.")

    @staticmethod
    def _normalize_windings(value: tuple[TransformerWinding, ...]) -> tuple[TransformerWinding, ...]:
        if isinstance(value, (str, bytes)):
            raise TypeError("windings must be an iterable of TransformerWinding records.")
        records = tuple(value)
        if any(not isinstance(item, TransformerWinding) for item in records):
            raise TypeError("windings must contain only TransformerWinding records.")
        if records:
            if len(records) < 2:
                raise ValueError("An explicit transformer winding collection requires at least HV and LV.")
            if len({item.winding_id for item in records}) != len(records):
                raise ValueError("Transformer winding IDs must be unique.")
            if len({item.sequence for item in records}) != len(records):
                raise ValueError("Transformer winding sequence numbers must be unique.")
            roles = {item.role.upper() for item in records}
            if not {"HV", "LV"}.issubset(roles):
                raise ValueError("Explicit transformer windings must declare HV and LV roles.")
            if len(roles) != len(records):
                raise ValueError("Transformer winding roles must be unique.")
            records = tuple(sorted(records, key=lambda item: item.sequence))
        return records

    def _validate_explicit_winding_compatibility(self) -> None:
        """Prevent explicit HV/LV records from contradicting legacy solver inputs."""
        records = getattr(self, "_windings", ())
        if not records:
            return
        by_role = {item.role.upper(): item for item in records}
        for role, connection, phases, neutral, terminal in (
            ("HV", self._hv_winding_connection, self._hv_phase_roles, self._hv_neutral_terminal_role, self._hv_terminal_role),
            ("LV", self._lv_winding_connection, self._lv_phase_roles, self._lv_neutral_terminal_role, self._lv_terminal_role),
        ):
            record = by_role[role]
            if (record.connection, record.phase_roles, record.neutral_terminal_role, record.terminal_role) != (connection, phases, neutral, terminal):
                raise ValueError(f"Explicit {role} winding record must exactly match the established Core {role} configuration.")

    @property
    def windings(self) -> tuple[TransformerWinding, ...]:
        """Return explicit windings, or a compatibility view of legacy HV/LV."""
        records = getattr(self, "_windings", ())
        if records:
            return records
        return (
            TransformerWinding("HV", 1, "HV", connection=self.hv_winding_connection, phase_roles=self.hv_phase_roles, neutral_terminal_role=self.hv_neutral_terminal_role, terminal_role=self.hv_terminal_role),
            TransformerWinding("LV", 2, "LV", connection=self.lv_winding_connection, phase_roles=self.lv_phase_roles, neutral_terminal_role=self.lv_neutral_terminal_role, terminal_role=self.lv_terminal_role),
        )

    @property
    def configured_windings(self) -> tuple[TransformerWinding, ...]:
        """Return only explicitly persisted winding records, without a legacy projection."""
        return self._windings

    def winding(self, winding_id: str) -> TransformerWinding:
        if not isinstance(winding_id, str) or not winding_id.strip():
            raise ValueError("winding_id must be a non-empty string.")
        for item in self.windings:
            if item.winding_id == winding_id:
                return item
        raise KeyError(f"Transformer winding not found: {winding_id!r}.")

    @staticmethod
    def _validate_terminal_mapping(hv_role: str | None, lv_role: str | None) -> tuple[str | None, str | None]:
        if hv_role is None and lv_role is None:
            return None, None
        hv = str(hv_role).strip().upper() if hv_role is not None else ""
        lv = str(lv_role).strip().upper() if lv_role is not None else ""
        if hv not in {"FROM", "TO"} or lv not in {"FROM", "TO"} or hv == lv:
            raise ValueError("HV/LV terminal mapping must explicitly assign distinct FROM and TO roles.")
        return hv, lv

    @staticmethod
    def _normalize_reference_winding(value: str | None) -> str | None:
        if value is None:
            return None
        result = str(value).strip().upper()
        if result not in {"HV", "LV"}:
            raise ValueError("impedance_reference_winding must be HV or LV.")
        return result

    @staticmethod
    def _validate_zero_sequence_impedance(resistance: float | None, reactance: float | None) -> tuple[float | None, float | None]:
        if (resistance is None) != (reactance is None):
            raise ValueError("Transformer zero-sequence resistance and reactance must be declared together.")
        if resistance is None:
            return None, None
        r = Transformer._validate_finite(resistance, "zero_sequence_resistance_ohm")
        x = Transformer._validate_finite(reactance, "zero_sequence_reactance_ohm")
        if r < 0.0 or (r == 0.0 and x == 0.0):
            raise ValueError("Transformer Z0 must be non-zero with non-negative resistance.")
        return r, x

    @property
    def hv_terminal_role(self) -> str | None:
        return self._hv_terminal_role

    @property
    def lv_terminal_role(self) -> str | None:
        return self._lv_terminal_role

    @property
    def impedance_reference_winding(self) -> str | None:
        return self._impedance_reference_winding

    @property
    def zero_sequence_resistance_ohm(self) -> float | None:
        return self._zero_sequence_resistance_ohm

    @property
    def zero_sequence_reactance_ohm(self) -> float | None:
        return self._zero_sequence_reactance_ohm

    @property
    def hv_winding_connection(self) -> TransformerWindingConnection | None:
        return self._hv_winding_connection

    @property
    def lv_winding_connection(self) -> TransformerWindingConnection | None:
        return self._lv_winding_connection

    @staticmethod
    def _validate_neutral_terminal_role(value: str | None, connection, side: str) -> str | None:
        if connection is TransformerWindingConnection.WYE_GROUNDED:
            if value is None:
                # Preserve legacy transformer construction. The physical
                # winding contract remains unavailable until the SLD/Core
                # terminal model exposes this neutral port explicitly.
                return None
            if not str(value).strip():
                raise ValueError(f"{side} neutral terminal role cannot be blank.")
            return str(value).strip()
        if value is not None:
            raise ValueError(f"{side} neutral terminal role is only valid for WYE_GROUNDED.")
        return None

    @property
    def hv_neutral_terminal_role(self) -> str | None:
        return self._hv_neutral_terminal_role

    @property
    def lv_neutral_terminal_role(self) -> str | None:
        return self._lv_neutral_terminal_role

    @staticmethod
    def _validate_winding_phase_roles(value: tuple[str, ...] | None, side: str) -> tuple[str, ...] | None:
        if value is None:
            return None
        if isinstance(value, (str, bytes)):
            raise TypeError(f"{side} phase roles must be an explicit tuple/list, not a string.")
        roles = tuple(str(role).strip().upper() for role in value)
        if roles != ("A", "B", "C"):
            raise ValueError(f"{side} winding phase roles must explicitly be ('A', 'B', 'C') in order.")
        return roles

    @classmethod
    def _validate_phase_domain_multiport_configuration(
        cls, *, matrix, evidence, loop_states, tap_ratio, phase_shift_rad,
        tap, shift, hv_connection, lv_connection, hv_terminal_role, lv_terminal_role,
        hv_phase_roles, lv_phase_roles, hv_neutral_terminal_role, lv_neutral_terminal_role,
        core_construction,
    ):
        matrix = cls._validate_phase_domain_terminal_admittance(matrix)
        loops = tuple(loop_states)
        if any(not isinstance(item, TransformerDeltaLoopState) for item in loops):
            raise TypeError("phase_domain_delta_loop_states must contain TransformerDeltaLoopState records.")
        if len({item.loop_id for item in loops}) != len(loops):
            raise ValueError("Transformer delta loop ids must be unique.")
        if matrix is None:
            if evidence is not None or loops or tap_ratio is not None or phase_shift_rad is not None:
                raise ValueError("Transformer multiport evidence, loops and snapshots require an explicit terminal admittance matrix.")
            return None, None, (), None, None
        if tap_ratio is None or phase_shift_rad is None:
            raise ValueError("Explicit transformer multiport matrix requires tap and phase-shift snapshots.")
        normalized_tap = cls._validate_positive(tap_ratio, "phase_domain_multiport_tap_ratio")
        normalized_shift = cls._validate_finite(phase_shift_rad, "phase_domain_multiport_phase_shift_rad")
        if not math.isclose(normalized_tap, tap, rel_tol=1e-10, abs_tol=1e-12) or not math.isclose(normalized_shift, shift, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("Transformer multiport matrix tap/shift snapshots must match the active transformer tap and phase shift.")
        if loops and len({item.loop_id for item in loops}) != len(loops):
            raise ValueError("Transformer delta loop ids must be unique.")
        if matrix is not None or evidence is not None or loops:
            if hv_connection is None or lv_connection is None or hv_terminal_role is None or lv_terminal_role is None or hv_phase_roles is None or lv_phase_roles is None:
                raise ValueError("Transformer multiport requires complete HV/LV winding, phase-role and terminal-role configuration.")
            labels = [f"HV:{role}" for role in hv_phase_roles]
            if hv_neutral_terminal_role is not None:
                labels.append("HV:N")
            labels.extend(f"LV:{role}" for role in lv_phase_roles)
            if lv_neutral_terminal_role is not None:
                labels.append("LV:N")
            if len(matrix) != len(labels):
                raise ValueError("Transformer terminal admittance matrix dimension must match the ordered HV/LV phase and neutral terminal inventory.")
            delta_windings = tuple(
                side for side, connection in (("HV", hv_connection), ("LV", lv_connection))
                if connection is TransformerWindingConnection.DELTA
            )
            if loops:
                loop_windings = {item.winding for item in loops}
                if loop_windings != set(delta_windings):
                    raise ValueError("Explicit delta-loop states must cover exactly the delta-connected Transformer windings.")
                if any(len(item.coupling_coefficients) != len(labels) for item in loops):
                    raise ValueError("Transformer delta-loop coupling vectors must match the ordered physical terminal inventory.")
            if evidence is not None or loops:
                if core_construction is None:
                    raise ValueError("Evidence-backed Transformer multiport requires explicit core construction.")
            if evidence is not None:
                validate_transformer_zero_sequence_multiport_evidence(
                    evidence, matrix=matrix, terminal_labels=labels, core_construction=core_construction,
                    delta_windings=delta_windings, delta_loop_states=loops,
                )
        return matrix, evidence, loops, normalized_tap, normalized_shift

    @staticmethod
    def _validate_phase_domain_terminal_admittance(value):
        if value is None:
            return None
        if isinstance(value, (str, bytes)):
            raise TypeError("phase_domain_terminal_admittance_siemens must be a square numeric matrix.")
        try:
            matrix = tuple(tuple(complex(item) for item in row) for row in value)
        except (TypeError, ValueError) as exc:
            raise ValueError("phase_domain_terminal_admittance_siemens must be a square numeric matrix.") from exc
        size = len(matrix)
        if size < 6 or any(len(row) != size for row in matrix):
            raise ValueError("Transformer terminal admittance matrix must be square and contain at least six terminals.")
        import numpy as np
        array = np.asarray(matrix, dtype=complex)
        if not np.all(np.isfinite(array.real)) or not np.all(np.isfinite(array.imag)):
            raise ValueError("Transformer terminal admittance matrix must contain finite values.")
        matrix_scale = max(float(np.max(np.abs(array))), float(np.finfo(float).tiny))
        if not np.allclose(array, array.T, rtol=1e-9, atol=1e-12 * matrix_scale):
            raise ValueError("Transformer terminal admittance matrix must be reciprocal in the declared terminal order.")
        eigenvalues = np.linalg.eigvalsh((array.real + array.real.T) * 0.5)
        conductance_scale = max(float(np.max(np.abs(eigenvalues))), float(np.finfo(float).tiny))
        if float(np.min(eigenvalues)) < -1e-10 * conductance_scale:
            raise ValueError("Transformer terminal conductance matrix must be positive semidefinite.")
        return matrix

    @property
    def phase_domain_multiport_tap_ratio(self) -> float | None:
        return self._phase_domain_multiport_tap_ratio

    @property
    def phase_domain_multiport_phase_shift_rad(self) -> float | None:
        return self._phase_domain_multiport_phase_shift_rad

    @property
    def phase_domain_delta_loop_states(self) -> tuple[TransformerDeltaLoopState, ...]:
        return self._phase_domain_delta_loop_states

    @property
    def zero_sequence_multiport_evidence(self) -> TransformerZeroSequenceMultiportEvidence | None:
        return self._zero_sequence_multiport_evidence

    @property
    def phase_domain_terminal_admittance_siemens(self):
        """Explicit complete terminal-domain primitive; None means unavailable."""
        return self._phase_domain_terminal_admittance_siemens

    @property
    def physical_conductor_construction(self):
        """Explicit HV/LV winding topology; never a galvanic HV-to-LV path."""
        from .transformer_physical_conductor_construction import (
            TransformerPhysicalConductorConstruction,
            TransformerWindingConductorConstruction,
        )
        if (
            self._hv_winding_connection is None
            or self._lv_winding_connection is None
            or self._hv_terminal_role is None
            or self._lv_terminal_role is None
            or self._hv_phase_roles is None
            or self._lv_phase_roles is None
            or (
                self._hv_winding_connection is TransformerWindingConnection.WYE_GROUNDED
                and self._hv_neutral_terminal_role is None
            )
            or (
                self._lv_winding_connection is TransformerWindingConnection.WYE_GROUNDED
                and self._lv_neutral_terminal_role is None
            )
        ):
            return None
        return TransformerPhysicalConductorConstruction(
            hv=TransformerWindingConductorConstruction(
                winding="HV",
                terminal_role=self._hv_terminal_role,
                connection=self._hv_winding_connection,
                phase_roles=self._hv_phase_roles,
                neutral_terminal_role=self._hv_neutral_terminal_role,
                neutral_grounding_impedance_ohm=self._hv_neutral_grounding_impedance_ohm,
                earth_return_impedance_ohm=self._hv_neutral_earth_return_impedance_ohm,
            ),
            lv=TransformerWindingConductorConstruction(
                winding="LV",
                terminal_role=self._lv_terminal_role,
                connection=self._lv_winding_connection,
                phase_roles=self._lv_phase_roles,
                neutral_terminal_role=self._lv_neutral_terminal_role,
                neutral_grounding_impedance_ohm=self._lv_neutral_grounding_impedance_ohm,
                earth_return_impedance_ohm=self._lv_neutral_earth_return_impedance_ohm,
            ),
            vector_group=self._vector_group_configuration,
            core_construction=self._core_construction,
        )

    @property
    def hv_neutral_earth_return_impedance_ohm(self) -> complex | None:
        return self._hv_neutral_earth_return_impedance_ohm

    @property
    def lv_neutral_earth_return_impedance_ohm(self) -> complex | None:
        return self._lv_neutral_earth_return_impedance_ohm

    @property
    def hv_phase_roles(self) -> tuple[str, ...] | None:
        return self._hv_phase_roles

    @property
    def lv_phase_roles(self) -> tuple[str, ...] | None:
        return self._lv_phase_roles

    @property
    def hv_neutral_grounding_impedance_ohm(self) -> complex | None:
        return self._hv_neutral_grounding_impedance_ohm

    @property
    def lv_neutral_grounding_impedance_ohm(self) -> complex | None:
        return self._lv_neutral_grounding_impedance_ohm

    @staticmethod
    def _normalize_winding_connection(value: TransformerWindingConnection | str | None) -> TransformerWindingConnection | None:
        if value is None:
            return None
        try:
            return value if isinstance(value, TransformerWindingConnection) else TransformerWindingConnection(str(value).strip().upper())
        except (TypeError, ValueError) as exc:
            raise ValueError("Winding connection must be DELTA, WYE_GROUNDED or WYE_UNGROUNDED.") from exc

    @staticmethod
    def _validate_neutral_grounding(value: complex | None, connection: TransformerWindingConnection | None, side: str) -> complex | None:
        if connection is not TransformerWindingConnection.WYE_GROUNDED:
            if value is not None:
                raise ValueError(f"{side} neutral grounding impedance is only valid for WYE_GROUNDED.")
            return None
        if value is None:
            raise ValueError(f"{side} grounded-wye winding requires explicit neutral grounding impedance; use 0j for solid grounding.")
        if isinstance(value, bool):
            raise TypeError(f"{side} neutral grounding impedance must be a complex value in ohms.")
        try:
            impedance = complex(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{side} neutral grounding impedance must be a complex value in ohms.") from exc
        if not math.isfinite(impedance.real) or not math.isfinite(impedance.imag) or impedance.real < 0.0:
            raise ValueError(f"{side} neutral grounding impedance must be finite with non-negative resistance.")
        return impedance
    @staticmethod
    def _validate_neutral_earth_return(value: complex | None, connection: TransformerWindingConnection | None, side: str) -> complex | None:
        if connection is not TransformerWindingConnection.WYE_GROUNDED:
            if value is not None:
                raise ValueError(f"{side} neutral earth-return impedance is only valid for WYE_GROUNDED.")
            return None
        if value is None:
            raise ValueError(f"{side} grounded-wye winding requires explicit earth-return impedance.")
        if isinstance(value, bool):
            raise TypeError(f"{side} earth-return impedance must be a complex value in ohms.")
        try:
            impedance = complex(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{side} earth-return impedance must be a complex value in ohms.") from exc
        if not math.isfinite(impedance.real) or not math.isfinite(impedance.imag) or impedance.real < 0.0 or impedance == 0j:
            raise ValueError(f"{side} earth-return impedance must be finite, passive and non-zero.")
        return impedance

    @staticmethod
    def _normalize_core_construction(value: TransformerCoreConstruction | str | None) -> TransformerCoreConstruction | None:
        if value is None:
            return None
        try:
            return value if isinstance(value, TransformerCoreConstruction) else TransformerCoreConstruction(str(value).strip().upper())
        except (TypeError, ValueError) as exc:
            raise ValueError("core_construction must be THREE_LIMB, FIVE_LIMB or SHELL_FORM.") from exc

    @staticmethod
    def _normalize_zero_sequence_test_basis(value: str | None) -> str | None:
        if value is None:
            return None
        result = str(value).strip().upper()
        if result not in {"MEASURED_ZERO_SEQUENCE_TEST", "MANUFACTURER_ZERO_SEQUENCE_DATA"}:
            raise ValueError("zero_sequence_test_basis must be MEASURED_ZERO_SEQUENCE_TEST or MANUFACTURER_ZERO_SEQUENCE_DATA.")
        return result

    @property
    def core_construction(self) -> TransformerCoreConstruction | None:
        return self._core_construction

    @property
    def zero_sequence_test_basis(self) -> str | None:
        return self._zero_sequence_test_basis

    @staticmethod
    def _normalize_vector_group_configuration(value: Any | None) -> Any | None:
        if value is None:
            return None
        from .transformer_zero_sequence_contracts import TransformerVectorGroupConfiguration
        if not isinstance(value, TransformerVectorGroupConfiguration):
            raise TypeError("vector_group_configuration must be TransformerVectorGroupConfiguration.")
        return value

    @staticmethod
    def _normalize_zero_sequence_test_configuration(value: Any | None) -> Any | None:
        if value is None:
            return None
        from .transformer_zero_sequence_contracts import TransformerZeroSequenceTestConfiguration
        if not isinstance(value, TransformerZeroSequenceTestConfiguration):
            raise TypeError("zero_sequence_test_configuration must be TransformerZeroSequenceTestConfiguration.")
        return value

    @staticmethod
    def _validate_zero_sequence_contracts(vector_group, test_configuration, reference, hv, lv, z0_resistance, two_winding_test_data=None) -> None:
        if z0_resistance is None:
            if two_winding_test_data is not None:
                if vector_group is None or test_configuration is None or reference != "HV":
                    raise ValueError("Three-test Yg-Yg data requires a Yy vector group, explicit HV test configuration, and HV reference winding.")
                if test_configuration.energized_winding.value != "HV" or test_configuration.other_winding_condition.value != "SHORT_CIRCUITED":
                    raise ValueError("Yg-Yg three-test configuration must identify the HV short-circuit test connection.")
                vector_group.validate_winding_connections(hv, lv)
                from .transformer_zero_sequence_contracts import validate_zero_sequence_test_applicability
                validate_zero_sequence_test_applicability(test=test_configuration, reference_winding=reference, hv_connection=hv, lv_connection=lv)
                return
            if vector_group is not None or test_configuration is not None:
                raise ValueError("Vector-group and Z0 test configuration require declared transformer Z0 data or explicit two-winding test data.")
            return
        if vector_group is None or test_configuration is None:
            raise ValueError("Transformer Z0 requires explicit vector-group and zero-sequence test configuration.")
        vector_group.validate_winding_connections(hv, lv)
        from .transformer_zero_sequence_contracts import validate_zero_sequence_test_applicability
        validate_zero_sequence_test_applicability(
            test=test_configuration, reference_winding=reference,
            hv_connection=hv, lv_connection=lv,
        )

    @property
    def vector_group_configuration(self):
        return getattr(self, "_vector_group_configuration", None)

    @property
    def zero_sequence_test_configuration(self):
        return getattr(self, "_zero_sequence_test_configuration", None)

    @property
    def zero_sequence_two_winding_test_data(self):
        return getattr(self, "_zero_sequence_two_winding_test_data", None)

    @staticmethod
    def _normalize_zero_sequence_two_winding_test_data(value):
        if value is None:
            return None
        if not isinstance(value, TransformerZeroSequenceTwoWindingTestData):
            raise TypeError("zero_sequence_two_winding_test_data must be TransformerZeroSequenceTwoWindingTestData.")
        return value

    @staticmethod
    def _validate_zero_sequence_two_winding_test_data(value, hv, lv, vector_group):
        if value is None:
            return
        if hv is not TransformerWindingConnection.WYE_GROUNDED or lv is not TransformerWindingConnection.WYE_GROUNDED:
            raise ValueError("Three-test two-winding Z0 data is valid only for grounded-wye/grounded-wye transformers.")
        if vector_group is None or vector_group.connection.value != "Yy":
            raise ValueError("Grounded-wye/grounded-wye three-test data requires an explicit Yy vector-group declaration.")

    @property
    def element_type(self) -> str:
        return self.TYPE

    @property
    def impedance_basis(self) -> ImpedanceBasis:
        """Return the declared basis of ``r/x/b``."""
        return self._impedance_basis

    @property
    def impedance_base_mva(self) -> float:
        """Return the declared original MVA basis of ``r/x/b``."""
        return self._impedance_base_mva

    @property
    def impedance_base_voltage_kv(self) -> float:
        """Return the declared voltage-side basis of ``r/x/b``."""
        return self._impedance_base_voltage_kv

    @property
    def rated_mva(self) -> float | None:
        """Return the transformer nameplate MVA rating."""
        return self.rate_mva

    @property
    def tap(self) -> float:
        return self._tap

    @tap.setter
    def tap(self, value: float) -> None:
        self._tap = self._validate_positive(value, "tap")

    @property
    def tap_ratio(self) -> float:
        return self._tap

    @tap_ratio.setter
    def tap_ratio(self, value: float) -> None:
        self._tap = self._validate_positive(value, "tap_ratio")

    @property
    def turns_ratio(self) -> float:
        return self._tap

    def set_tap(self, tap: float) -> None:
        self.tap = tap

    @property
    def shift(self) -> float:
        return self._shift

    @shift.setter
    def shift(self, value: float) -> None:
        self._shift = self._validate_finite(value, "shift")

    @property
    def phase_shift_rad(self) -> float:
        return self._shift

    @phase_shift_rad.setter
    def phase_shift_rad(self, value: float) -> None:
        self._shift = self._validate_finite(value, "phase_shift_rad")

    @property
    def phase_shift_deg(self) -> float:
        return math.degrees(self._shift)

    @phase_shift_deg.setter
    def phase_shift_deg(self, value: float) -> None:
        self._shift = math.radians(
            self._validate_finite(value, "phase_shift_deg")
        )

    def set_phase_shift(self, shift: float) -> None:
        self.shift = shift

    def set_phase_shift_degrees(self, degrees: float) -> None:
        self.phase_shift_deg = degrees

    def update_configuration(
        self,
        *,
        r: float | None = None,
        x: float | None = None,
        b: float | None = None,
        impedance_basis: ImpedanceBasis | str | None = None,
        impedance_base_mva: float | None = None,
        tap: float | None = None,
        shift: float | None = None,
        rate_mva: float | None = None,
        name: str | None = None,
        in_service: bool | None = None,
        hv_winding_connection: TransformerWindingConnection | str | None = None,
        lv_winding_connection: TransformerWindingConnection | str | None = None,
        hv_neutral_grounding_impedance_ohm: complex | None = None,
        lv_neutral_grounding_impedance_ohm: complex | None = None,
        hv_terminal_role: str | None = None,
        lv_terminal_role: str | None = None,
        impedance_reference_winding: str | None = None,
        zero_sequence_resistance_ohm: float | None = None,
        zero_sequence_reactance_ohm: float | None = None,
        core_construction: TransformerCoreConstruction | str | None = None,
        zero_sequence_test_basis: str | None = None,
        vector_group_configuration: Any | None = None,
        zero_sequence_test_configuration: Any | None = None,
        zero_sequence_two_winding_test_data: TransformerZeroSequenceTwoWindingTestData | None = None,
        hv_neutral_earth_return_impedance_ohm: complex | None = None,
        lv_neutral_earth_return_impedance_ohm: complex | None = None,
        hv_neutral_terminal_role: str | None = None,
        lv_neutral_terminal_role: str | None = None,
        hv_phase_roles: tuple[str, ...] | None = None,
        lv_phase_roles: tuple[str, ...] | None = None,
        _replace_physical_conductor_configuration: bool = False,
        phase_domain_terminal_admittance_siemens: tuple[tuple[complex, ...], ...] | None = None,
        zero_sequence_multiport_evidence: TransformerZeroSequenceMultiportEvidence | None = None,
        phase_domain_delta_loop_states: tuple[TransformerDeltaLoopState, ...] | None = None,
        phase_domain_multiport_tap_ratio: float | None = None,
        phase_domain_multiport_phase_shift_rad: float | None = None,
        _replace_phase_domain_multiport_configuration: bool = False,
        _replace_winding_configuration: bool = False,
        windings: tuple[TransformerWinding, ...] | None = None,
        _replace_windings: bool = False,
    ) -> None:
        """Atomically apply a validated Transformer engineering configuration.

        When the impedance basis or MVA base changes without explicit r/x/b
        replacements, the existing physical impedance/admittance is preserved
        through the canonical PerUnitSystem. Explicit r/x/b values are
        interpreted in the requested target representation.

        impedance_base_voltage_kv is intentionally absent: it is a reference
        construction value and is not an ordinary editable Transformer field.
        """
        target_basis = self._normalize_impedance_basis(
            self._impedance_basis if impedance_basis is None else impedance_basis
        )
        target_base_mva = self._impedance_base_mva if impedance_base_mva is None else self._validate_positive(
            impedance_base_mva, "impedance_base_mva"
        )

        current_basis = self._impedance_basis
        basis_changed = target_basis is not current_basis
        base_changed = not math.isclose(
            target_base_mva, self._impedance_base_mva, rel_tol=0.0, abs_tol=0.0
        )
        coupled_update = basis_changed or base_changed

        target_r = self._r
        target_x = self._x
        target_b = self._b
        if coupled_update:
            z = complex(self._r, self._x)
            y = complex(0.0, self._b)
            if current_basis is ImpedanceBasis.PU:
                source = PerUnitSystem(self._impedance_base_mva)
                physical_z = source.from_pu_impedance(z, self._impedance_base_voltage_kv)
                physical_y = source.from_pu_admittance(y, self._impedance_base_voltage_kv)
            else:
                physical_z = z
                physical_y = y

            if target_basis is ImpedanceBasis.PU:
                target = PerUnitSystem(target_base_mva)
                target_z = target.to_pu_impedance(physical_z, self._impedance_base_voltage_kv)
                target_y = target.to_pu_admittance(physical_y, self._impedance_base_voltage_kv)
            else:
                target_z = physical_z
                target_y = physical_y
            target_r, target_x, target_b = target_z.real, target_z.imag, target_y.imag

        if r is not None:
            target_r = self._validate_finite(r, "r")
        if x is not None:
            target_x = self._validate_finite(x, "x")
        if b is not None:
            target_b = self._validate_finite(b, "b")

        target_tap = self._tap if tap is None else self._validate_positive(tap, "tap")
        target_shift = self._shift if shift is None else self._validate_finite(shift, "shift")
        target_rate = self._rate_mva if rate_mva is None else self._validate_positive(rate_mva, "rate_mva")
        target_name = self.name if name is None else name
        if not isinstance(target_name, str):
            raise TypeError("name must be a string.")
        target_name = target_name.strip() or self.id
        target_in_service = self._in_service if in_service is None else bool(in_service)
        target_hv_connection = self._normalize_winding_connection(
            hv_winding_connection if _replace_winding_configuration
            else self._hv_winding_connection if hv_winding_connection is None else hv_winding_connection
        )
        target_lv_connection = self._normalize_winding_connection(
            lv_winding_connection if _replace_winding_configuration
            else self._lv_winding_connection if lv_winding_connection is None else lv_winding_connection
        )
        target_hv_grounding_value = (
            self._hv_neutral_grounding_impedance_ohm
            if hv_neutral_grounding_impedance_ohm is None and hv_winding_connection is None and not _replace_winding_configuration
            else hv_neutral_grounding_impedance_ohm
        )
        target_lv_grounding_value = (
            self._lv_neutral_grounding_impedance_ohm
            if lv_neutral_grounding_impedance_ohm is None and lv_winding_connection is None and not _replace_winding_configuration
            else lv_neutral_grounding_impedance_ohm
        )
        target_hv_grounding = self._validate_neutral_grounding(target_hv_grounding_value, target_hv_connection, "HV")
        target_lv_grounding = self._validate_neutral_grounding(target_lv_grounding_value, target_lv_connection, "LV")
        if _replace_physical_conductor_configuration:
            target_hv_earth_return = self._validate_neutral_earth_return(hv_neutral_earth_return_impedance_ohm, target_hv_connection, "HV")
            target_lv_earth_return = self._validate_neutral_earth_return(lv_neutral_earth_return_impedance_ohm, target_lv_connection, "LV")
            target_hv_phase_roles = self._validate_winding_phase_roles(hv_phase_roles, "HV")
            target_lv_phase_roles = self._validate_winding_phase_roles(lv_phase_roles, "LV")
            target_hv_neutral_role = self._validate_neutral_terminal_role(hv_neutral_terminal_role, target_hv_connection, "HV")
            target_lv_neutral_role = self._validate_neutral_terminal_role(lv_neutral_terminal_role, target_lv_connection, "LV")
        else:
            target_hv_earth_return = self._validate_neutral_earth_return(self._hv_neutral_earth_return_impedance_ohm if hv_winding_connection is None else hv_neutral_earth_return_impedance_ohm, target_hv_connection, "HV")
            target_lv_earth_return = self._validate_neutral_earth_return(self._lv_neutral_earth_return_impedance_ohm if lv_winding_connection is None else lv_neutral_earth_return_impedance_ohm, target_lv_connection, "LV")
            target_hv_phase_roles = self._validate_winding_phase_roles(self._hv_phase_roles if hv_phase_roles is None else hv_phase_roles, "HV")
            target_lv_phase_roles = self._validate_winding_phase_roles(self._lv_phase_roles if lv_phase_roles is None else lv_phase_roles, "LV")
            target_hv_neutral_role = self._validate_neutral_terminal_role(self._hv_neutral_terminal_role if hv_neutral_terminal_role is None else hv_neutral_terminal_role, target_hv_connection, "HV")
            target_lv_neutral_role = self._validate_neutral_terminal_role(self._lv_neutral_terminal_role if lv_neutral_terminal_role is None else lv_neutral_terminal_role, target_lv_connection, "LV")
        target_hv_role = self._hv_terminal_role if hv_terminal_role is None and not _replace_winding_configuration else hv_terminal_role
        target_lv_role = self._lv_terminal_role if lv_terminal_role is None and not _replace_winding_configuration else lv_terminal_role
        target_hv_role, target_lv_role = self._validate_terminal_mapping(target_hv_role, target_lv_role)
        if not isinstance(_replace_windings, bool):
            raise TypeError("_replace_windings must be a bool.")
        if _replace_windings:
            target_windings = self._normalize_windings(() if windings is None else windings)
        else:
            if windings is not None:
                raise ValueError("windings may only be supplied with _replace_windings=True.")
            target_windings = self._windings
        if target_windings:
            by_role = {item.role.upper(): item for item in target_windings}
            for role, connection, phases, neutral, terminal in (
                ("HV", target_hv_connection, target_hv_phase_roles, target_hv_neutral_role, target_hv_role),
                ("LV", target_lv_connection, target_lv_phase_roles, target_lv_neutral_role, target_lv_role),
            ):
                record = by_role[role]
                if (record.connection, record.phase_roles, record.neutral_terminal_role, record.terminal_role) != (connection, phases, neutral, terminal):
                    raise ValueError(f"Explicit {role} winding record must exactly match the established Core {role} configuration.")
        target_reference_winding = self._normalize_reference_winding(self._impedance_reference_winding if impedance_reference_winding is None and not _replace_winding_configuration else impedance_reference_winding)
        target_z0r = self._zero_sequence_resistance_ohm if zero_sequence_resistance_ohm is None and not _replace_winding_configuration else zero_sequence_resistance_ohm
        target_z0x = self._zero_sequence_reactance_ohm if zero_sequence_reactance_ohm is None and not _replace_winding_configuration else zero_sequence_reactance_ohm
        target_z0r, target_z0x = self._validate_zero_sequence_impedance(target_z0r, target_z0x)
        target_core = self._normalize_core_construction(self._core_construction if core_construction is None and not _replace_winding_configuration else core_construction)
        target_test_basis = self._normalize_zero_sequence_test_basis(self._zero_sequence_test_basis if zero_sequence_test_basis is None and not _replace_winding_configuration else zero_sequence_test_basis)
        if target_z0r is not None and (target_reference_winding is None or target_hv_role is None):
            raise ValueError("Transformer Z0 requires explicit impedance_reference_winding and HV/LV terminal mapping.")
        if target_z0r is not None and (target_core is None or target_test_basis is None):
            raise ValueError("Transformer Z0 requires explicit core_construction and zero_sequence_test_basis.")
        target_vector_group = self._normalize_vector_group_configuration(
            self._vector_group_configuration if vector_group_configuration is None and not _replace_winding_configuration else vector_group_configuration
        )
        target_test_configuration = self._normalize_zero_sequence_test_configuration(
            self._zero_sequence_test_configuration if zero_sequence_test_configuration is None and not _replace_winding_configuration else zero_sequence_test_configuration
        )
        target_two_winding_test_data = self._normalize_zero_sequence_two_winding_test_data(
            self._zero_sequence_two_winding_test_data if zero_sequence_two_winding_test_data is None and not _replace_winding_configuration else zero_sequence_two_winding_test_data
        )
        self._validate_zero_sequence_two_winding_test_data(target_two_winding_test_data, target_hv_connection, target_lv_connection, target_vector_group)
        if target_two_winding_test_data is not None and (target_hv_role is None or target_lv_role is None or target_reference_winding != "HV"):
            raise ValueError("Yg-Yg three-test data requires explicit distinct HV/LV terminal roles and HV impedance reference.")
        if target_two_winding_test_data is not None and (target_core is None or target_test_basis is None):
            raise ValueError("Two-winding transformer Z0 test data requires explicit core_construction and zero_sequence_test_basis.")
        self._validate_zero_sequence_contracts(
            target_vector_group, target_test_configuration, target_reference_winding,
            target_hv_connection, target_lv_connection, target_z0r, target_two_winding_test_data,
        )
        if _replace_phase_domain_multiport_configuration:
            multiport_values = (
                phase_domain_terminal_admittance_siemens, zero_sequence_multiport_evidence,
                () if phase_domain_delta_loop_states is None else phase_domain_delta_loop_states,
                phase_domain_multiport_tap_ratio, phase_domain_multiport_phase_shift_rad,
            )
        else:
            multiport_values = (
                self._phase_domain_terminal_admittance_siemens, self._zero_sequence_multiport_evidence,
                self._phase_domain_delta_loop_states, self._phase_domain_multiport_tap_ratio,
                self._phase_domain_multiport_phase_shift_rad,
            )
        (
            target_multiport_matrix, target_multiport_evidence, target_delta_loops,
            target_multiport_tap, target_multiport_shift,
        ) = self._validate_phase_domain_multiport_configuration(
            matrix=multiport_values[0], evidence=multiport_values[1], loop_states=multiport_values[2],
            tap_ratio=multiport_values[3], phase_shift_rad=multiport_values[4],
            tap=target_tap, shift=target_shift, hv_connection=target_hv_connection,
            lv_connection=target_lv_connection, hv_terminal_role=target_hv_role,
            lv_terminal_role=target_lv_role, hv_phase_roles=target_hv_phase_roles,
            lv_phase_roles=target_lv_phase_roles, hv_neutral_terminal_role=target_hv_neutral_role,
            lv_neutral_terminal_role=target_lv_neutral_role, core_construction=target_core,
        )

        # Validate the complete candidate before mutating any authoritative state.
        self._validate_finite(target_r, "r")
        self._validate_finite(target_x, "x")
        self._validate_finite(target_b, "b")
        self._validate_positive(target_base_mva, "impedance_base_mva")
        self._validate_positive(self._impedance_base_voltage_kv, "impedance_base_voltage_kv")

        old_state = (
            self._r, self._x, self._b, self._impedance_basis,
            self._impedance_base_mva, self._tap, self._shift,
            self._rate_mva, self.name, self._in_service,
            self._hv_winding_connection, self._lv_winding_connection,
            self._hv_neutral_grounding_impedance_ohm, self._lv_neutral_grounding_impedance_ohm,
            self._hv_neutral_earth_return_impedance_ohm, self._lv_neutral_earth_return_impedance_ohm,
            self._hv_phase_roles, self._lv_phase_roles,
            self._hv_neutral_terminal_role, self._lv_neutral_terminal_role,
            self._hv_terminal_role, self._lv_terminal_role, self._impedance_reference_winding,
            self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm,
            self._core_construction, self._zero_sequence_test_basis,
            self._vector_group_configuration, self._zero_sequence_test_configuration,
            self._zero_sequence_two_winding_test_data,
            self._phase_domain_terminal_admittance_siemens, self._zero_sequence_multiport_evidence,
            self._phase_domain_delta_loop_states, self._phase_domain_multiport_tap_ratio,
            self._phase_domain_multiport_phase_shift_rad,
            self._windings,
        )
        try:
            self._r = target_r
            self._x = target_x
            self._b = target_b
            self._impedance_basis = target_basis
            self._impedance_base_mva = target_base_mva
            self._tap = target_tap
            self._shift = target_shift
            self._rate_mva = target_rate
            self.name = target_name
            self._in_service = target_in_service
            self._hv_winding_connection = target_hv_connection
            self._lv_winding_connection = target_lv_connection
            self._hv_neutral_grounding_impedance_ohm = target_hv_grounding
            self._lv_neutral_grounding_impedance_ohm = target_lv_grounding
            self._hv_neutral_earth_return_impedance_ohm = target_hv_earth_return
            self._lv_neutral_earth_return_impedance_ohm = target_lv_earth_return
            self._hv_phase_roles, self._lv_phase_roles = target_hv_phase_roles, target_lv_phase_roles
            self._hv_neutral_terminal_role, self._lv_neutral_terminal_role = target_hv_neutral_role, target_lv_neutral_role
            self._hv_terminal_role, self._lv_terminal_role = target_hv_role, target_lv_role
            self._impedance_reference_winding = target_reference_winding
            self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm = target_z0r, target_z0x
            self._core_construction, self._zero_sequence_test_basis = target_core, target_test_basis
            self._vector_group_configuration = target_vector_group
            self._zero_sequence_test_configuration = target_test_configuration
            self._zero_sequence_two_winding_test_data = target_two_winding_test_data
            self._phase_domain_terminal_admittance_siemens = target_multiport_matrix
            self._zero_sequence_multiport_evidence = target_multiport_evidence
            self._phase_domain_delta_loop_states = target_delta_loops
            self._phase_domain_multiport_tap_ratio = target_multiport_tap
            self._phase_domain_multiport_phase_shift_rad = target_multiport_shift
            self._windings = target_windings
            self.validate()
        except Exception:
            (
                self._r, self._x, self._b, self._impedance_basis,
                self._impedance_base_mva, self._tap, self._shift,
                self._rate_mva, old_name, self._in_service,
                self._hv_winding_connection, self._lv_winding_connection,
                self._hv_neutral_grounding_impedance_ohm, self._lv_neutral_grounding_impedance_ohm,
                self._hv_neutral_earth_return_impedance_ohm, self._lv_neutral_earth_return_impedance_ohm,
                self._hv_phase_roles, self._lv_phase_roles,
                self._hv_neutral_terminal_role, self._lv_neutral_terminal_role,
                self._hv_terminal_role, self._lv_terminal_role, self._impedance_reference_winding,
                self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm,
                self._core_construction, self._zero_sequence_test_basis,
                self._vector_group_configuration, self._zero_sequence_test_configuration,
                self._zero_sequence_two_winding_test_data,
                self._phase_domain_terminal_admittance_siemens, self._zero_sequence_multiport_evidence,
                self._phase_domain_delta_loop_states, self._phase_domain_multiport_tap_ratio,
                self._phase_domain_multiport_phase_shift_rad,
                self._windings,
            ) = old_state
            self.name = old_name
            raise

    @staticmethod
    def _normalize_impedance_basis(value: ImpedanceBasis | str) -> ImpedanceBasis:
        if isinstance(value, ImpedanceBasis):
            return value
        try:
            return ImpedanceBasis(str(value).strip().lower())
        except (TypeError, ValueError) as exc:
            raise ValueError("Transformer impedance_basis must be 'pu' or 'engineering'.") from exc

    def validate_parameters(self) -> bool:
        Branch.validate_parameters(self)
        if self._impedance_basis not in self.VALID_IMPEDANCE_BASES:
            raise ValueError("Transformer impedance_basis is invalid.")
        self._impedance_base_mva = self._validate_positive(
            self._impedance_base_mva,
            "impedance_base_mva",
        )
        self._impedance_base_voltage_kv = self._validate_positive(
            self._impedance_base_voltage_kv,
            "impedance_base_voltage_kv",
        )
        self._tap = self._validate_positive(self._tap, "tap")
        self._shift = self._validate_finite(self._shift, "shift")
        self._hv_winding_connection = self._normalize_winding_connection(self._hv_winding_connection)
        self._lv_winding_connection = self._normalize_winding_connection(self._lv_winding_connection)
        self._windings = self._normalize_windings(getattr(self, "_windings", ()))
        self._hv_neutral_grounding_impedance_ohm = self._validate_neutral_grounding(
            self._hv_neutral_grounding_impedance_ohm, self._hv_winding_connection, "HV"
        )
        self._lv_neutral_grounding_impedance_ohm = self._validate_neutral_grounding(
            self._lv_neutral_grounding_impedance_ohm, self._lv_winding_connection, "LV"
        )
        self._hv_terminal_role, self._lv_terminal_role = self._validate_terminal_mapping(self._hv_terminal_role, self._lv_terminal_role)
        self._validate_explicit_winding_compatibility()
        self._impedance_reference_winding = self._normalize_reference_winding(self._impedance_reference_winding)
        self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm = self._validate_zero_sequence_impedance(self._zero_sequence_resistance_ohm, self._zero_sequence_reactance_ohm)
        self._core_construction = self._normalize_core_construction(self._core_construction)
        self._zero_sequence_test_basis = self._normalize_zero_sequence_test_basis(self._zero_sequence_test_basis)
        if self._zero_sequence_resistance_ohm is not None and (self._impedance_reference_winding is None or self._hv_terminal_role is None):
            raise ValueError("Transformer Z0 requires explicit impedance_reference_winding and HV/LV terminal mapping.")
        if self._zero_sequence_resistance_ohm is not None and (self._core_construction is None or self._zero_sequence_test_basis is None):
            raise ValueError("Transformer Z0 requires explicit core_construction and zero_sequence_test_basis.")
        self._zero_sequence_two_winding_test_data = self._normalize_zero_sequence_two_winding_test_data(getattr(self, "_zero_sequence_two_winding_test_data", None))
        self._validate_zero_sequence_two_winding_test_data(
            self._zero_sequence_two_winding_test_data, self._hv_winding_connection,
            self._lv_winding_connection, getattr(self, "_vector_group_configuration", None),
        )
        if self._zero_sequence_two_winding_test_data is not None and (self._hv_terminal_role is None or self._lv_terminal_role is None or self._impedance_reference_winding != "HV"):
            raise ValueError("Yg-Yg three-test data requires explicit distinct HV/LV terminal roles and HV impedance reference.")
        if self._zero_sequence_two_winding_test_data is not None and (self._core_construction is None or self._zero_sequence_test_basis is None):
            raise ValueError("Two-winding transformer Z0 test data requires explicit core_construction and zero_sequence_test_basis.")
        self._validate_zero_sequence_contracts(
            getattr(self, "_vector_group_configuration", None), getattr(self, "_zero_sequence_test_configuration", None),
            self._impedance_reference_winding, self._hv_winding_connection,
            self._lv_winding_connection, self._zero_sequence_resistance_ohm,
            self._zero_sequence_two_winding_test_data,
        )
        (
            self._phase_domain_terminal_admittance_siemens,
            self._zero_sequence_multiport_evidence,
            self._phase_domain_delta_loop_states,
            self._phase_domain_multiport_tap_ratio,
            self._phase_domain_multiport_phase_shift_rad,
        ) = self._validate_phase_domain_multiport_configuration(
            matrix=getattr(self, "_phase_domain_terminal_admittance_siemens", None),
            evidence=getattr(self, "_zero_sequence_multiport_evidence", None),
            loop_states=getattr(self, "_phase_domain_delta_loop_states", ()),
            tap_ratio=getattr(self, "_phase_domain_multiport_tap_ratio", None),
            phase_shift_rad=getattr(self, "_phase_domain_multiport_phase_shift_rad", None),
            tap=self._tap, shift=self._shift, hv_connection=self._hv_winding_connection,
            lv_connection=self._lv_winding_connection, hv_terminal_role=self._hv_terminal_role,
            lv_terminal_role=self._lv_terminal_role, hv_phase_roles=getattr(self, "_hv_phase_roles", None),
            lv_phase_roles=getattr(self, "_lv_phase_roles", None),
            hv_neutral_terminal_role=getattr(self, "_hv_neutral_terminal_role", None),
            lv_neutral_terminal_role=getattr(self, "_lv_neutral_terminal_role", None),
            core_construction=self._core_construction,
        )
        return True

    def summary(self) -> dict[str, Any]:
        summary = super().summary()
        summary.update(
            {
                "type": self.TYPE,
                "impedance_basis": self._impedance_basis,
                "impedance_base_mva": self._impedance_base_mva,
                "impedance_base_voltage_kv": self._impedance_base_voltage_kv,
                "rated_mva": self.rate_mva,
                "tap": self._tap,
                "tap_ratio": self._tap,
                "shift_rad": self._shift,
                "shift_deg": self.phase_shift_deg,
                "hv_winding_connection": self._hv_winding_connection,
                "lv_winding_connection": self._lv_winding_connection,
                "windings": self.windings,
                "hv_neutral_grounding_impedance_ohm": self._hv_neutral_grounding_impedance_ohm,
                "hv_neutral_earth_return_impedance_ohm": self._hv_neutral_earth_return_impedance_ohm,
                "lv_neutral_earth_return_impedance_ohm": self._lv_neutral_earth_return_impedance_ohm,
                "lv_neutral_grounding_impedance_ohm": self._lv_neutral_grounding_impedance_ohm,
                "hv_terminal_role": self._hv_terminal_role,
                "lv_terminal_role": self._lv_terminal_role,
                "impedance_reference_winding": self._impedance_reference_winding,
                "zero_sequence_resistance_ohm": self._zero_sequence_resistance_ohm,
                "zero_sequence_reactance_ohm": self._zero_sequence_reactance_ohm,
                "core_construction": self._core_construction,
                "zero_sequence_test_basis": self._zero_sequence_test_basis,
                "vector_group_configuration": getattr(self, "_vector_group_configuration", None),
                "zero_sequence_test_configuration": getattr(self, "_zero_sequence_test_configuration", None),
                "zero_sequence_two_winding_test_data": getattr(self, "_zero_sequence_two_winding_test_data", None),
                "phase_domain_terminal_admittance_siemens": self._phase_domain_terminal_admittance_siemens,
                "zero_sequence_multiport_evidence": self._zero_sequence_multiport_evidence,
                "phase_domain_delta_loop_states": self._phase_domain_delta_loop_states,
                "phase_domain_multiport_tap_ratio": self._phase_domain_multiport_tap_ratio,
                "phase_domain_multiport_phase_shift_rad": self._phase_domain_multiport_phase_shift_rad,
            }
        )
        return summary

    def __repr__(self) -> str:
        from_endpoint = self.from_endpoint
        to_endpoint = self.to_endpoint
        from_id = getattr(from_endpoint, "id", None) if from_endpoint is not None else None
        to_id = getattr(to_endpoint, "id", None) if to_endpoint is not None else None
        return (
            f"<Transformer id={self.id}, {from_id} -> {to_id}, "
            f"basis={self._impedance_basis}, "
            f"base={self._impedance_base_mva:.6f} MVA/{self._impedance_base_voltage_kv:.6f} kV, "
            f"tap={self._tap:.6f}, shift={self._shift:.6f} rad>"
        )

    @staticmethod
    def _validate_finite(value: float, name: str) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric.") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite.")
        return value

    @staticmethod
    def _validate_positive(value: float, name: str) -> float:
        value = Transformer._validate_finite(value, name)
        if value <= 0.0:
            raise ValueError(f"{name} must be greater than zero.")
        return value


__all__ = ["ImpedanceBasis", "TransformerCoreConstruction", "TransformerWindingConnection", "Transformer"]
