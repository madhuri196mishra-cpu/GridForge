"""Application service boundary for Transformer mutations.

Author: Subhendu Mishra

Transformer impedance reference voltage is explicit engineering input.
It is never derived from endpoint connectivity and is persisted on the
Transformer as ``impedance_base_voltage_kv`` for deterministic study
preparation.
"""

from __future__ import annotations

from typing import Any

from core.application.results import ApplicationResult
from core.application.services._model_service_support import ModelServiceSupport
from core.application.transaction import Transaction
from core.model.bus import Bus
from core.model.terminal import Terminal
from core.model.transformer import Transformer, TransformerWinding, TransformerWindingConnection
from core.model.transformer_zero_sequence_contracts import TransformerVectorGroupConfiguration, TransformerZeroSequenceTestConfiguration
from core.model.transformer_zero_sequence_test_data import TransformerZeroSequenceTwoWindingTestData
from core.model.transformer_zero_sequence_multiport_evidence import TransformerZeroSequenceMultiportEvidence, TransformerDeltaLoopState
from core.network.network import Network


class TransformerModelService(ModelServiceSupport):
    """Owns Application-layer mutation use cases for Transformer."""

    def __init__(self, network: Network) -> None:
        if not isinstance(network, Network):
            raise TypeError("network must be a Network.")
        self._network = network

    @property
    def network(self) -> Network:
        return self._network

    def create_transformer(
        self,
        *,
        transformer_id: str,
        endpoint_from: Bus | Terminal | None = None,
        endpoint_to: Bus | Terminal | None = None,
        r: float = 0.0,
        x: float = 0.0,
        b: float = 0.0,
        impedance_basis: str,
        tap: float = 1.0,
        shift: float = 0.0,
        name: str | None = None,
        rate_mva: float | None = None,
        impedance_base_mva: float | None = None,
        impedance_base_voltage_kv: float | None = None,
        hv_winding_connection: TransformerWindingConnection | str | None = None,
        lv_winding_connection: TransformerWindingConnection | str | None = None,
        windings: tuple[Any, ...] = (),
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
        core_construction: Any | None = None,
        zero_sequence_test_basis: str | None = None,
        vector_group_configuration: TransformerVectorGroupConfiguration | None = None,
        zero_sequence_test_configuration: TransformerZeroSequenceTestConfiguration | None = None,
        zero_sequence_two_winding_test_data: TransformerZeroSequenceTwoWindingTestData | None = None,
        phase_domain_terminal_admittance_siemens: tuple[tuple[complex, ...], ...] | None = None,
        zero_sequence_multiport_evidence: TransformerZeroSequenceMultiportEvidence | None = None,
        phase_domain_delta_loop_states: tuple[TransformerDeltaLoopState, ...] = (),
        phase_domain_multiport_tap_ratio: float | None = None,
        phase_domain_multiport_phase_shift_rad: float | None = None,
        transaction: Transaction,
    ) -> ApplicationResult[Transformer]:
        self._require_transaction(transaction)
        self._require_id(transformer_id, "transformer_id")
        if endpoint_from is not None:
            self._validate_endpoint(endpoint_from, "endpoint_from")
        if endpoint_to is not None:
            self._validate_endpoint(endpoint_to, "endpoint_to")
        if endpoint_from is not None and endpoint_to is not None:
            self._require_distinct_endpoints(endpoint_from, endpoint_to, "INVALID_TRANSFORMER_ENDPOINTS", "Transformer", transformer_id)
        self._ensure_not_exists("transformer", transformer_id, "Transformer")

        # Transformer impedance reference voltage is engineering input, never
        # inferred from an absent endpoint. A connected transformer may still
        # be created with explicit endpoint references, but placement does not
        # require them.
        if impedance_base_voltage_kv is None:
            raise ValueError(
                "Transformer impedance_base_voltage_kv must be explicitly declared; "
                "it cannot be inferred from endpoint connectivity."
            )
        impedance_base_voltage_kv = float(impedance_base_voltage_kv)
        if impedance_base_voltage_kv <= 0.0:
            raise ValueError("Transformer impedance_base_voltage_kv must be greater than zero.")

        transformer = Transformer(
            id=transformer_id,
            endpoint_from=endpoint_from,
            endpoint_to=endpoint_to,
            r=r,
            x=x,
            b=b,
            impedance_basis=impedance_basis,
            tap=tap,
            shift=shift,
            name="" if name is None else name,
            rate_mva=rate_mva,
            impedance_base_mva=impedance_base_mva,
            impedance_base_voltage_kv=impedance_base_voltage_kv,
            hv_winding_connection=hv_winding_connection,
            lv_winding_connection=lv_winding_connection,
            windings=windings,
            hv_neutral_grounding_impedance_ohm=hv_neutral_grounding_impedance_ohm,
            lv_neutral_grounding_impedance_ohm=lv_neutral_grounding_impedance_ohm,
            hv_neutral_earth_return_impedance_ohm=hv_neutral_earth_return_impedance_ohm,
            lv_neutral_earth_return_impedance_ohm=lv_neutral_earth_return_impedance_ohm,
            hv_neutral_terminal_role=hv_neutral_terminal_role,
            lv_neutral_terminal_role=lv_neutral_terminal_role,
            hv_phase_roles=hv_phase_roles,
            lv_phase_roles=lv_phase_roles,
            hv_terminal_role=hv_terminal_role,
            lv_terminal_role=lv_terminal_role,
            impedance_reference_winding=impedance_reference_winding,
            zero_sequence_resistance_ohm=zero_sequence_resistance_ohm,
            zero_sequence_reactance_ohm=zero_sequence_reactance_ohm,
            core_construction=core_construction,
            zero_sequence_test_basis=zero_sequence_test_basis,
            vector_group_configuration=vector_group_configuration,
            zero_sequence_test_configuration=zero_sequence_test_configuration,
            zero_sequence_two_winding_test_data=zero_sequence_two_winding_test_data,
            phase_domain_terminal_admittance_siemens=phase_domain_terminal_admittance_siemens,
            zero_sequence_multiport_evidence=zero_sequence_multiport_evidence,
            phase_domain_delta_loop_states=phase_domain_delta_loop_states,
            phase_domain_multiport_tap_ratio=phase_domain_multiport_tap_ratio,
            phase_domain_multiport_phase_shift_rad=phase_domain_multiport_phase_shift_rad,
        )
        self._network.add_transformer(transformer)
        transaction.record_undo(lambda transformer=transformer: self._network.remove_transformer(transformer))
        return self._success(transformer, "transformer", transformer_id, f"Transformer created: {transformer_id}")

    def update_transformer(
        self,
        *,
        transformer_id: str,
        name: str | None = None,
        r: float | None = None,
        x: float | None = None,
        b: float | None = None,
        impedance_basis: Any | None = None,
        impedance_base_mva: float | None = None,
        tap: float | None = None,
        shift: float | None = None,
        rate_mva: float | None = None,
        in_service: bool | None = None,
        hv_winding_connection: TransformerWindingConnection | str | None = None,
        lv_winding_connection: TransformerWindingConnection | str | None = None,
        windings: tuple[TransformerWinding, ...] | object = ...,
        hv_neutral_grounding_impedance_ohm: complex | None = None,
        lv_neutral_grounding_impedance_ohm: complex | None = None,
        hv_neutral_earth_return_impedance_ohm: complex | None | object = ...,
        lv_neutral_earth_return_impedance_ohm: complex | None | object = ...,
        hv_neutral_terminal_role: str | None | object = ...,
        lv_neutral_terminal_role: str | None | object = ...,
        hv_phase_roles: tuple[str, ...] | None | object = ...,
        lv_phase_roles: tuple[str, ...] | None | object = ...,
        replace_physical_conductor_configuration: bool = False,
        hv_terminal_role: str | None = None,
        lv_terminal_role: str | None = None,
        impedance_reference_winding: str | None = None,
        zero_sequence_resistance_ohm: float | None = None,
        zero_sequence_reactance_ohm: float | None = None,
        core_construction: Any | None = None,
        zero_sequence_test_basis: str | None = None,
        vector_group_configuration: TransformerVectorGroupConfiguration | None = None,
        zero_sequence_test_configuration: TransformerZeroSequenceTestConfiguration | None = None,
        zero_sequence_two_winding_test_data: TransformerZeroSequenceTwoWindingTestData | None = None,
        phase_domain_terminal_admittance_siemens: tuple[tuple[complex, ...], ...] | None | object = ...,
        zero_sequence_multiport_evidence: TransformerZeroSequenceMultiportEvidence | None | object = ...,
        phase_domain_delta_loop_states: tuple[TransformerDeltaLoopState, ...] | object = ...,
        phase_domain_multiport_tap_ratio: float | None | object = ...,
        phase_domain_multiport_phase_shift_rad: float | None | object = ...,
        replace_phase_domain_multiport_configuration: bool = False,
        transaction: Transaction,
    ) -> ApplicationResult[Transformer]:
        self._require_transaction(transaction)
        self._require_id(transformer_id, "transformer_id")
        transformer = self._get_required("transformer", transformer_id, "Transformer")
        self._require_type(transformer, Transformer, transformer_id, "Transformer")

        if not isinstance(replace_physical_conductor_configuration, bool):
            raise TypeError("replace_physical_conductor_configuration must be a bool.")
        physical_fields = (
            hv_neutral_earth_return_impedance_ohm, lv_neutral_earth_return_impedance_ohm,
            hv_neutral_terminal_role, lv_neutral_terminal_role, hv_phase_roles, lv_phase_roles,
        )
        if replace_physical_conductor_configuration and any(value is ... for value in physical_fields):
            raise ValueError("Physical conductor replacement requires all six fields, including explicit None values.")
        if not replace_physical_conductor_configuration and any(value is ... for value in physical_fields):
            (
                hv_neutral_earth_return_impedance_ohm, lv_neutral_earth_return_impedance_ohm,
                hv_neutral_terminal_role, lv_neutral_terminal_role, hv_phase_roles, lv_phase_roles,
            ) = (None if value is ... else value for value in physical_fields)
        values = (
            name, r, x, b, impedance_basis, impedance_base_mva,
            tap, shift, rate_mva, in_service,
            hv_winding_connection, lv_winding_connection,
            hv_neutral_grounding_impedance_ohm, lv_neutral_grounding_impedance_ohm,
            hv_neutral_earth_return_impedance_ohm, lv_neutral_earth_return_impedance_ohm,
            hv_neutral_terminal_role, lv_neutral_terminal_role, hv_phase_roles, lv_phase_roles,
            hv_terminal_role, lv_terminal_role, impedance_reference_winding,
            zero_sequence_resistance_ohm, zero_sequence_reactance_ohm,
            core_construction, zero_sequence_test_basis,
            vector_group_configuration, zero_sequence_test_configuration,
            zero_sequence_two_winding_test_data, None if windings is ... else windings,
            phase_domain_terminal_admittance_siemens, zero_sequence_multiport_evidence,
            phase_domain_delta_loop_states, phase_domain_multiport_tap_ratio, phase_domain_multiport_phase_shift_rad,
        )
        if all(value is None for value in values) and not replace_physical_conductor_configuration and not replace_phase_domain_multiport_configuration:
            raise ValueError("At least one Transformer configuration field must be specified.")
        if not isinstance(replace_phase_domain_multiport_configuration, bool):
            raise TypeError("replace_phase_domain_multiport_configuration must be a bool.")
        multiport_fields = (phase_domain_terminal_admittance_siemens, zero_sequence_multiport_evidence, phase_domain_delta_loop_states, phase_domain_multiport_tap_ratio, phase_domain_multiport_phase_shift_rad)
        if replace_phase_domain_multiport_configuration and any(value is ... for value in multiport_fields):
            raise ValueError("Multiport replacement requires all five fields, including explicit None or empty values.")
        if not replace_phase_domain_multiport_configuration and any(value is not ... for value in multiport_fields):
            raise ValueError(
                "Transformer multiport fields must be updated as one complete group with "
                "replace_phase_domain_multiport_configuration=True."
            )
        if not replace_phase_domain_multiport_configuration and any(value is ... for value in multiport_fields):
            phase_domain_terminal_admittance_siemens, zero_sequence_multiport_evidence, phase_domain_delta_loop_states, phase_domain_multiport_tap_ratio, phase_domain_multiport_phase_shift_rad = (
                None if value is ... else value for value in multiport_fields
            )
        if in_service is not None and not isinstance(in_service, bool):
            raise TypeError("in_service must be a bool.")

        old_state = {
            "name": transformer.name,
            "r": transformer.r,
            "x": transformer.x,
            "b": transformer.b,
            "impedance_basis": transformer.impedance_basis,
            "impedance_base_mva": transformer.impedance_base_mva,
            "tap": transformer.tap,
            "shift": transformer.shift,
            "rate_mva": transformer.rate_mva,
            "in_service": transformer.in_service,
            "hv_winding_connection": transformer.hv_winding_connection,
            "lv_winding_connection": transformer.lv_winding_connection,
            "windings": transformer.configured_windings,
            "hv_neutral_grounding_impedance_ohm": transformer.hv_neutral_grounding_impedance_ohm,
            "lv_neutral_grounding_impedance_ohm": transformer.lv_neutral_grounding_impedance_ohm,
            "hv_neutral_earth_return_impedance_ohm": transformer.hv_neutral_earth_return_impedance_ohm,
            "lv_neutral_earth_return_impedance_ohm": transformer.lv_neutral_earth_return_impedance_ohm,
            "hv_neutral_terminal_role": transformer.hv_neutral_terminal_role,
            "lv_neutral_terminal_role": transformer.lv_neutral_terminal_role,
            "hv_phase_roles": transformer.hv_phase_roles,
            "lv_phase_roles": transformer.lv_phase_roles,
            "hv_terminal_role": transformer.hv_terminal_role,
            "lv_terminal_role": transformer.lv_terminal_role,
            "impedance_reference_winding": transformer.impedance_reference_winding,
            "zero_sequence_resistance_ohm": transformer.zero_sequence_resistance_ohm,
            "zero_sequence_reactance_ohm": transformer.zero_sequence_reactance_ohm,
            "core_construction": transformer.core_construction,
            "zero_sequence_test_basis": transformer.zero_sequence_test_basis,
            "vector_group_configuration": transformer.vector_group_configuration,
            "zero_sequence_test_configuration": transformer.zero_sequence_test_configuration,
            "zero_sequence_two_winding_test_data": transformer.zero_sequence_two_winding_test_data,
            "phase_domain_terminal_admittance_siemens": transformer.phase_domain_terminal_admittance_siemens,
            "zero_sequence_multiport_evidence": transformer.zero_sequence_multiport_evidence,
            "phase_domain_delta_loop_states": transformer.phase_domain_delta_loop_states,
            "phase_domain_multiport_tap_ratio": transformer.phase_domain_multiport_tap_ratio,
            "phase_domain_multiport_phase_shift_rad": transformer.phase_domain_multiport_phase_shift_rad,
        }
        service_state_changed = (
            in_service is not None and in_service != old_state["in_service"]
        )
        physical_topology_changed = any(
            value is not None and value != old_state[key]
            for key, value in (
                ("hv_winding_connection", hv_winding_connection),
                ("lv_winding_connection", lv_winding_connection),
                ("hv_neutral_earth_return_impedance_ohm", hv_neutral_earth_return_impedance_ohm),
                ("lv_neutral_earth_return_impedance_ohm", lv_neutral_earth_return_impedance_ohm),
                ("hv_neutral_terminal_role", hv_neutral_terminal_role),
                ("lv_neutral_terminal_role", lv_neutral_terminal_role),
                ("hv_phase_roles", hv_phase_roles),
                ("lv_phase_roles", lv_phase_roles),
            )
        ) or (
            replace_physical_conductor_configuration
            and any(
                old_state[key] != value
                for key, value in (
                    ("hv_neutral_earth_return_impedance_ohm", hv_neutral_earth_return_impedance_ohm),
                    ("lv_neutral_earth_return_impedance_ohm", lv_neutral_earth_return_impedance_ohm),
                    ("hv_neutral_terminal_role", hv_neutral_terminal_role),
                    ("lv_neutral_terminal_role", lv_neutral_terminal_role),
                    ("hv_phase_roles", hv_phase_roles),
                    ("lv_phase_roles", lv_phase_roles),
                )
            )
        )
        winding_configuration_changed = windings is not ... and tuple(windings) != old_state["windings"]
        multiport_configuration_changed = replace_phase_domain_multiport_configuration and any(
            old_state[key] != value
            for key, value in (
                ("phase_domain_terminal_admittance_siemens", phase_domain_terminal_admittance_siemens),
                ("zero_sequence_multiport_evidence", zero_sequence_multiport_evidence),
                ("phase_domain_delta_loop_states", phase_domain_delta_loop_states),
                ("phase_domain_multiport_tap_ratio", phase_domain_multiport_tap_ratio),
                ("phase_domain_multiport_phase_shift_rad", phase_domain_multiport_phase_shift_rad),
            )
        )

        # Transformer.update_configuration validates the complete candidate
        # and restores its prior state if the candidate is invalid.
        transformer.update_configuration(
            name=name,
            r=r,
            x=x,
            b=b,
            impedance_basis=impedance_basis,
            impedance_base_mva=impedance_base_mva,
            tap=tap,
            shift=shift,
            rate_mva=rate_mva,
            in_service=in_service,
            hv_winding_connection=hv_winding_connection,
            lv_winding_connection=lv_winding_connection,
            windings=None if windings is ... else windings,
            _replace_windings=windings is not ...,
            hv_neutral_grounding_impedance_ohm=hv_neutral_grounding_impedance_ohm,
            lv_neutral_grounding_impedance_ohm=lv_neutral_grounding_impedance_ohm,
            hv_neutral_earth_return_impedance_ohm=hv_neutral_earth_return_impedance_ohm,
            lv_neutral_earth_return_impedance_ohm=lv_neutral_earth_return_impedance_ohm,
            hv_neutral_terminal_role=hv_neutral_terminal_role,
            lv_neutral_terminal_role=lv_neutral_terminal_role,
            hv_phase_roles=hv_phase_roles,
            lv_phase_roles=lv_phase_roles,
            _replace_physical_conductor_configuration=replace_physical_conductor_configuration,
            hv_terminal_role=hv_terminal_role,
            lv_terminal_role=lv_terminal_role,
            impedance_reference_winding=impedance_reference_winding,
            zero_sequence_resistance_ohm=zero_sequence_resistance_ohm,
            zero_sequence_reactance_ohm=zero_sequence_reactance_ohm,
            core_construction=core_construction,
            zero_sequence_test_basis=zero_sequence_test_basis,
            vector_group_configuration=vector_group_configuration,
            zero_sequence_test_configuration=zero_sequence_test_configuration,
            zero_sequence_two_winding_test_data=zero_sequence_two_winding_test_data,
            phase_domain_terminal_admittance_siemens=phase_domain_terminal_admittance_siemens,
            zero_sequence_multiport_evidence=zero_sequence_multiport_evidence,
            phase_domain_delta_loop_states=phase_domain_delta_loop_states,
            phase_domain_multiport_tap_ratio=phase_domain_multiport_tap_ratio,
            phase_domain_multiport_phase_shift_rad=phase_domain_multiport_phase_shift_rad,
            _replace_phase_domain_multiport_configuration=replace_phase_domain_multiport_configuration,
        )

        if service_state_changed or physical_topology_changed or winding_configuration_changed or multiport_configuration_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            transformer.update_configuration(
                **old_state,
                _replace_physical_conductor_configuration=True,
                _replace_phase_domain_multiport_configuration=True,
                _replace_winding_configuration=True,
                windings=old_state["windings"],
                _replace_windings=True,
            )
            if service_state_changed or physical_topology_changed or winding_configuration_changed or multiport_configuration_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(
            transformer,
            "transformer",
            transformer_id,
            f"Transformer updated: {transformer_id}",
        )

    def delete_transformer(self, *, transformer_id: str, transaction: Transaction) -> ApplicationResult[Transformer]:
        self._require_transaction(transaction)
        self._require_id(transformer_id, "transformer_id")
        transformer = self._get_required("transformer", transformer_id, "Transformer")
        self._require_type(transformer, Transformer, transformer_id, "Transformer")
        self._network.remove_transformer(transformer)
        transaction.record_undo(lambda transformer=transformer: self._network.add_transformer(transformer))
        return self._success(transformer, "transformer", transformer_id, f"Transformer deleted: {transformer_id}")


__all__ = ["TransformerModelService"]
