# ============================================================
# File: core/application/read_service.py
# GridForge V2 — Application Read Services
# Author: Subhendu Mishra
# ============================================================
"""Read-only Application boundaries for authoritative Core state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
import math
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .revision import ProjectRevision

from core.solver.power_flow.result import PowerFlowResult
from core.solver.dynamics.events import EventExecution, EventTransition
from core.solver.dynamics.dae_solver import AlgebraicNetworkSolution
from core.solver.dynamics.transient_stability import TransientDiscontinuity, TransientStabilityResult
from core.solver.short_circuit.result import ShortCircuitConductorCurrent, ShortCircuitTransformerDeltaLoopCurrent

from core.network.network import Network
from core.protection.protection_system import ProtectionSystem
from core.protection.decision import ProtectionDecision
from core.model.ct import CTPolarity
from core.model.cvt import CVTPolarity

from .read_models import (
    ElementReadModel,
    MeasurementChannelReadModel,
    EngineeringParameterReadModel,
    NetworkReadModel,
    ProtectionReadModel,
    RelayInputBindingReadModel,
    RelayReadModel,
    SimpleWireReadModel,
    StudyResultReadModel,
    TransientEventReadModel,
    TransientAlgebraicQualityReadModel,
    TransientSignalReadModel,
    TransientResultPresentationReadModel,
    TransientDiscontinuityReadModel,
)

_ELEMENT_COLLECTIONS = (
    "buses", "grids", "generators", "synchronous_machines", "loads",
    "motors", "shunts", "capacitors", "reactors", "solar", "batteries",
    "current_transformers", "potential_transformers", "capacitive_voltage_transformers",
    "lines", "cables", "transformers", "breakers", "switches",
    "disconnectors", "fuses",
)

_ELEMENT_TYPE_ALIASES = {
    "bus": "buses", "buses": "buses", "grid": "grids", "grids": "grids",
    "generator": "generators", "generators": "generators",
    "synchronous_machine": "synchronous_machines", "synchronous_machines": "synchronous_machines",
    "load": "loads", "loads": "loads", "motor": "motors", "motors": "motors",
    "shunt": "shunts", "shunts": "shunts", "capacitor": "capacitors", "capacitors": "capacitors",
    "reactor": "reactors", "reactors": "reactors", "solar": "solar",
    "battery": "batteries", "batteries": "batteries",
    "ct": "current_transformers", "current_transformer": "current_transformers", "current_transformers": "current_transformers",
    "pt": "potential_transformers", "potential_transformer": "potential_transformers", "potential_transformers": "potential_transformers",
    "cvt": "capacitive_voltage_transformers", "capacitive_voltage_transformer": "capacitive_voltage_transformers", "capacitive_voltage_transformers": "capacitive_voltage_transformers",
"line": "lines", "lines": "lines", "cable": "cables", "cables": "cables",
    "transformer": "transformers", "transformers": "transformers", "breaker": "breakers", "breakers": "breakers",
    "switch": "switches", "switches": "switches", "disconnector": "disconnectors", "disconnectors": "disconnectors",
    "fuse": "fuses", "fuses": "fuses",
}

_CORE_ELEMENT_TYPES = {
    "buses": "bus", "grids": "grid", "generators": "generator",
    "synchronous_machines": "synchronous_machine", "loads": "load",
    "motors": "motor", "shunts": "shunt", "capacitors": "capacitor",
    "reactors": "reactor", "solar": "solar", "batteries": "battery",
    "current_transformers": "current_transformer",
    "potential_transformers": "potential_transformer",
    "capacitive_voltage_transformers": "capacitive_voltage_transformer",
    "lines": "line", "cables": "cable", "transformers": "transformer",
    "breakers": "breaker", "switches": "switch", "disconnectors": "disconnector",
    "fuses": "fuse",
}

_FIELD_CONTRACTS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "buses": (("nominal_voltage_kv", ("nominal_voltage_kv",)), ("voltage_pu", ("voltage_pu",)), ("angle_deg", ("angle_deg",)), ("frequency_hz", ("frequency_hz",)), ("in_service", ("in_service",))),
    "lines": (("resistance_ohm", ("resistance_ohm", "r")), ("reactance_ohm", ("reactance_ohm", "x")), ("shunt_susceptance_siemens", ("shunt_susceptance_siemens", "b")), ("zero_sequence_resistance_ohm", ("zero_sequence_resistance_ohm",)), ("zero_sequence_reactance_ohm", ("zero_sequence_reactance_ohm",)), ("rate_mva", ("rate_mva", "rated_power")), ("in_service", ("in_service",))),
    "cables": (("length_km", ("length_km",)), ("rated_voltage_kv", ("rated_voltage_kv",)), ("rated_current_a", ("rated_current_a",)), ("r1_ohm_per_km", ("r1_ohm_per_km", "r1")), ("x1_ohm_per_km", ("x1_ohm_per_km", "x1")), ("b1_us_per_km", ("b1_us_per_km", "b1")), ("r0_ohm_per_km", ("r0_ohm_per_km", "r0")), ("x0_ohm_per_km", ("x0_ohm_per_km", "x0")), ("b0_us_per_km", ("b0_us_per_km", "b0")), ("thermal_limit_mva", ("thermal_limit_mva",)), ("conductor_count", ("conductor_count",)), ("in_service", ("in_service",))),
    "transformers": (("r", ("r",)), ("x", ("x",)), ("b", ("b",)), ("impedance_basis", ("impedance_basis",)), ("impedance_base_mva", ("impedance_base_mva",)), ("impedance_base_voltage_kv", ("impedance_base_voltage_kv",)), ("rate_mva", ("rate_mva", "rated_power")), ("tap", ("tap",)), ("tap_ratio", ("tap_ratio",)), ("turns_ratio", ("turns_ratio",)), ("shift", ("shift",)), ("phase_shift_rad", ("phase_shift_rad",)), ("phase_shift_deg", ("phase_shift_deg",)), ("in_service", ("in_service",))),
    "switches": (("closed", ("closed",)), ("in_service", ("in_service",)), ("normally_closed", ("normally_closed",)), ("rated_voltage_kv", ("rated_voltage_kv",)), ("rated_current_a", ("rated_current_a",))),
    "breakers": (("closed", ("closed",)), ("failed", ("failed",)), ("in_service", ("in_service",)), ("voltage_kv", ("voltage_kv", "rated_voltage_kv")), ("current_a", ("current_a", "rated_current_a")), ("interrupting_ka", ("interrupting_ka",))),
    "disconnectors": (("closed", ("closed",)), ("in_service", ("in_service",)), ("normally_closed", ("normally_closed",)), ("voltage_kv", ("voltage_kv", "rated_voltage_kv")), ("rated_current_a", ("rated_current_a",)), ("operating_time", ("operating_time",))),
    "fuses": (("in_service", ("in_service",)), ("blown", ("blown",)), ("rated_current_a", ("rated_current_a",)), ("rated_voltage_v", ("rated_voltage_v",)), ("interrupting_rating_ka", ("interrupting_rating_ka",))),
    "loads": (("p", ("p",)), ("q", ("q",)), ("in_service", ("in_service",))),
    "generators": (("p", ("p", "active_power_injection_mw")), ("q", ("q", "reactive_power_injection_mvar")), ("V_setpoint", ("V_setpoint", "v_setpoint")), ("q_min", ("q_min",)), ("q_max", ("q_max",)), ("in_service", ("in_service",))),
    "synchronous_machines": (("active_power_injection_mw", ("active_power_injection_mw",)), ("reactive_power_injection_mvar", ("reactive_power_injection_mvar",)), ("rated_power_mva", ("rated_power_mva",)), ("rated_voltage_kv", ("rated_voltage_kv",)), ("frequency_hz", ("frequency_hz",)), ("in_service", ("in_service",))),
    "motors": (("rated_mva", ("rated_mva",)), ("rated_kv", ("rated_kv",)), ("power_factor", ("power_factor",)), ("p", ("p",)), ("q", ("q",)), ("efficiency", ("efficiency",)), ("slip", ("slip",)), ("starting_current_pu", ("starting_current_pu",)), ("running", ("running",)), ("in_service", ("in_service",))),
    "shunts": (("g_pu", ("g_pu",)), ("b_pu", ("b_pu",)), ("in_service", ("in_service",))),
    "capacitors": (("reactive_power_injection_mvar", ("reactive_power_injection_mvar", "q")), ("in_service", ("in_service",))),
    "reactors": (("reactive_power_injection_mvar", ("reactive_power_injection_mvar", "q")), ("in_service", ("in_service",))),
    "solar": (("p_mw", ("p_mw", "p")), ("q_mvar", ("q_mvar", "q")), ("p_min_mw", ("p_min_mw",)), ("p_max_mw", ("p_max_mw",)), ("q_min_mvar", ("q_min_mvar",)), ("q_max_mvar", ("q_max_mvar",)), ("in_service", ("in_service",))),
    "batteries": (("p_mw", ("p_mw", "p")), ("q_mvar", ("q_mvar", "q")), ("max_charge_mw", ("max_charge_mw",)), ("max_discharge_mw", ("max_discharge_mw",)), ("energy_capacity_mwh", ("energy_capacity_mwh",)), ("soc", ("soc",)), ("soc_min", ("soc_min",)), ("soc_max", ("soc_max",)), ("in_service", ("in_service",))),
    "grids": (("nominal_voltage_kv", ("nominal_voltage_kv",)), ("frequency_hz", ("frequency_hz",)), ("voltage_pu", ("voltage_pu",)), ("angle_deg", ("angle_deg",)), ("p_mw", ("p_mw", "p")), ("q_mvar", ("q_mvar", "q")), ("short_circuit_mva", ("short_circuit_mva",)), ("x_over_r", ("x_over_r",)), ("z1_pu", ("z1_pu",)), ("z2_pu", ("z2_pu",)), ("z0_pu", ("z0_pu",)), ("impedance_base_mva", ("impedance_base_mva",)), ("impedance_base_voltage_kv", ("impedance_base_voltage_kv",)), ("neutral_grounding_impedance_ohm", ("neutral_grounding_impedance_ohm",)), ("in_service", ("in_service",)), ("grounded", ("grounded",))),
    "current_transformers": (("primary_rated_current_a", ("primary_rated_current_a",)), ("secondary_rated_current_a", ("secondary_rated_current_a",)), ("ratio", ("ratio",)), ("burden_va", ("burden_va",)), ("accuracy_class", ("accuracy_class",)), ("frequency_hz", ("frequency_hz",)), ("polarity", ("polarity",)), ("in_service", ("in_service",))),
    "potential_transformers": (("primary_voltage_kv", ("primary_voltage_kv",)), ("secondary_voltage_v", ("secondary_voltage_v",)), ("voltage_ratio", ("voltage_ratio", "ratio")), ("accuracy_class", ("accuracy_class",)), ("burden_va", ("burden_va",)), ("phase_displacement_deg", ("phase_displacement_deg",)), ("in_service", ("in_service",))),
    "capacitive_voltage_transformers": (("rated_primary_voltage_kv", ("rated_primary_voltage_kv",)), ("rated_secondary_voltage_v", ("rated_secondary_voltage_v",)), ("voltage_ratio", ("voltage_ratio", "ratio")), ("accuracy_class", ("accuracy_class",)), ("rated_burden_va", ("rated_burden_va",)), ("polarity", ("polarity",)), ("frequency_hz", ("frequency_hz",)), ("in_service", ("in_service",))),
    "relays": (("relay_type", ("relay_type", "type")), ("function_type", ("function_type",)), ("plugin_id", ("plugin_id",)), ("in_service", ("in_service",)), ("enabled", ("enabled",)), ("blocked", ("blocked",)), ("picked_up", ("picked_up",)), ("tripped", ("tripped",))),
}

_REQUIRED_FIELDS = {element_type: frozenset(canonical for canonical, _ in fields) for element_type, fields in _FIELD_CONTRACTS.items()}
# Grid short-circuit strength and sequence impedances are optional Core
# engineering values. Keep them in the projection vocabulary when present,
# but do not reject a valid Grid when they have not yet been configured.
# Line zero-sequence values are an optional coupled pair in Core.
_REQUIRED_FIELDS["lines"] = frozenset({
    "resistance_ohm", "reactance_ohm", "shunt_susceptance_siemens",
    "rate_mva", "in_service",
})
_REQUIRED_FIELDS["grids"] = frozenset({
    "nominal_voltage_kv",
    "frequency_hz",
    "voltage_pu",
    "angle_deg",
    "p_mw",
    "q_mvar",
    "in_service",
    "grounded",
})

_PARAMETER_METADATA: dict[str, dict[str, Any]] = {
    "nominal_voltage_kv": {"unit": "kV", "datatype": "float"},
    "impedance_base_mva": {"unit": "MVA", "datatype": "float", "editable": True, "study_impact": True},
    "impedance_base_voltage_kv": {"unit": "kV", "datatype": "float", "editable": True, "study_impact": True},
    "neutral_grounding_impedance_ohm": {"unit": "ohm", "datatype": "complex", "editable": False, "study_impact": True},
    "voltage_pu": {"unit": "pu", "datatype": "float"},
    "angle_deg": {"unit": "deg", "datatype": "float"},

    "resistance_ohm": {"unit": "ohm", "datatype": "float", "editable": True, "study_impact": True},
    "reactance_ohm": {"unit": "ohm", "datatype": "float", "editable": True, "study_impact": True},
    "shunt_susceptance_siemens": {"unit": "S", "datatype": "float", "editable": True, "study_impact": True},
    "zero_sequence_resistance_ohm": {
        "unit": "ohm", "datatype": "float", "editable": True, "study_impact": True,
        "coupling_group": "line_zero_sequence",
        "validation": {"authoritative": "Core", "paired_with": "zero_sequence_reactance_ohm"},
    },
    "zero_sequence_reactance_ohm": {
        "unit": "ohm", "datatype": "float", "editable": True, "study_impact": True,
        "coupling_group": "line_zero_sequence",
        "validation": {"authoritative": "Core", "paired_with": "zero_sequence_resistance_ohm"},
    },
    "length_km": {"unit": "km", "datatype": "float", "editable": True, "study_impact": True},
    "thermal_limit_mva": {"unit": "MVA", "datatype": "float", "editable": True, "study_impact": True, "validation": {"authoritative": "Core", "non_negative": True}},
    "conductor_count": {"datatype": "int", "editable": True, "study_impact": True, "validation": {"authoritative": "Core", "minimum": 1}},
    "conductor_system_summary": {
        "datatype": "mapping", "editable": False, "derived": True, "study_impact": True,
        "validation": {"description": "Read-only inventory and impedance-matrix summary; edit through the typed Cable conductor configuration workflow."},
    },
    "transformer_configuration_summary": {
        "datatype": "mapping", "editable": False, "derived": True, "study_impact": True,
        "validation": {"description": "Read-only winding, neutral grounding and earth-return, neutral-terminal mapping, phase-role, vector-group, zero-sequence test and multiport evidence summary. Configure through the dedicated Transformer editor in the Properties panel; Application commands and Core validation remain authoritative."},
    },
    "rated_voltage_kv": {"unit": "kV", "datatype": "float", "editable": True, "study_impact": True},
    "rated_current_a": {"unit": "A", "datatype": "float", "editable": True, "study_impact": True},
    "r1_ohm_per_km": {"unit": "ohm/km", "datatype": "float", "editable": True, "study_impact": True},
    "x1_ohm_per_km": {"unit": "ohm/km", "datatype": "float", "editable": True, "study_impact": True},
    "b1_us_per_km": {"unit": "µS/km", "datatype": "float", "editable": True, "study_impact": True},
    "r0_ohm_per_km": {"unit": "ohm/km", "datatype": "float", "editable": True, "study_impact": True},
    "x0_ohm_per_km": {"unit": "ohm/km", "datatype": "float", "editable": True, "study_impact": True},
    "b0_us_per_km": {"unit": "µS/km", "datatype": "float", "editable": True, "study_impact": True},
    "rate_mva": {"unit": "MVA", "datatype": "float", "editable": True, "study_impact": True},
    "impedance_basis": {"datatype": "enum", "choices": ("pu", "engineering"), "editable": True, "study_impact": True},
    "impedance_base_mva": {"unit": "MVA", "datatype": "float", "editable": True, "study_impact": True},
    "impedance_base_voltage_kv": {"unit": "kV", "datatype": "float", "editable": False, "study_impact": True},
    "tap": {"datatype": "float", "study_impact": True},
    "tap_ratio": {"datatype": "float", "derived": True, "study_impact": True},
    "turns_ratio": {"datatype": "float", "derived": True, "study_impact": True},
    "shift": {"unit": "rad", "datatype": "float", "study_impact": True},
    "phase_shift_rad": {"unit": "rad", "datatype": "float", "derived": True, "study_impact": True},
    "phase_shift_deg": {"unit": "deg", "datatype": "float", "derived": True, "study_impact": True},
    "q_min": {
        "unit": "MVAr", "datatype": "float", "editable": True, "study_impact": True,
        "coupling_group": "generator_q_limits",
        "validation": {"authoritative": "Core", "paired_with": "q_max"},
    },
    "q_max": {
        "unit": "MVAr", "datatype": "float", "editable": True, "study_impact": True,
        "coupling_group": "generator_q_limits",
        "validation": {"authoritative": "Core", "paired_with": "q_min"},
    },
    "in_service": {"datatype": "bool", "topology_impact": True, "study_impact": True},
    # Instrument-transformer engineering contracts.  Core remains the
    # authoritative validator; these descriptors only describe presentation.
    "primary_rated_current_a": {
        "unit": "A", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "secondary_rated_current_a": {
        "unit": "A", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "ratio": {
        "datatype": "float", "editable": False, "derived": True, "study_impact": True,
        "coupling_group": "ct_ratio",
        "validation": {"depends_on": ("primary_rated_current_a", "secondary_rated_current_a")},
    },
    "burden_va": {
        "unit": "VA", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core"},
    },
    "accuracy_class": {
        "datatype": "str", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core"},
    },
    "polarity": {
        "datatype": "enum", "editable": True, "study_impact": True,
        "choices": tuple(value.value for value in CTPolarity),
        "validation": {"authoritative": "Core"},
    },
    "primary_voltage_kv": {
        "unit": "kV", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "secondary_voltage_v": {
        "unit": "V", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "voltage_ratio": {
        "datatype": "float", "editable": False, "derived": True, "study_impact": True,
        "coupling_group": "voltage_ratio",
        "validation": {"depends_on": ("primary_voltage_kv", "secondary_voltage_v")},
    },
    "phase_displacement_deg": {
        "unit": "deg", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core"},
    },
    "rated_primary_voltage_kv": {
        "unit": "kV", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "rated_secondary_voltage_v": {
        "unit": "V", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "rated_burden_va": {
        "unit": "VA", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core"},
    },
    "frequency_hz": {
        "unit": "Hz", "datatype": "float", "editable": True, "study_impact": True,
        "validation": {"authoritative": "Core", "positive": True},
    },
    "cvt_polarity": {
        "datatype": "enum", "editable": True, "study_impact": True,
        "choices": tuple(value.value for value in CVTPolarity),
        "validation": {"authoritative": "Core"},
    },
    "p": {"unit": "MW", "datatype": "float", "editable": True, "study_impact": True},
    "q": {"unit": "MVAr", "datatype": "float", "editable": True, "study_impact": True},
    "V_setpoint": {"unit": "pu", "datatype": "float", "editable": True, "study_impact": True, "validation": {"authoritative": "Core", "positive": True}},
}

# Application-owned read metadata.  These descriptors are intentionally
# generic and read-only: they describe the meaning exposed to projections
# without duplicating Core validation or providing a UI mutation authority.
class ReadService(ABC):
    """Framework-neutral contract for Application network read operations."""
    @abstractmethod
    def network(self) -> NetworkReadModel: raise NotImplementedError
    @abstractmethod
    def element(self, element_type: str, object_id: str) -> ElementReadModel: raise NotImplementedError

class NetworkReadService(ReadService):
    """Default read adapter over the authoritative Core Network aggregate."""
    def __init__(self, network: Network) -> None:
        if not isinstance(network, Network): raise TypeError("NetworkReadService requires a Network")
        self._network = network
    def network(self) -> NetworkReadModel:
        """Return only NETWORK-domain presentation elements.

        Relays remain authoritative Core Network objects and are exposed
        through ProtectionReadService; they are intentionally excluded from
        the network presentation read collection because Relay presentation
        identity is owned exclusively by the PROTECTION projection domain.
        """
        elements: list[ElementReadModel] = []
        for element_type in _ELEMENT_COLLECTIONS:
            for model in getattr(self._network, element_type): elements.append(self._to_read_model(element_type, model))
        simple_wires = tuple(
            SimpleWireReadModel(
                connection_id=connection.connection_id,
                endpoint_a=connection.endpoint_a.to_mapping(),
                endpoint_b=connection.endpoint_b.to_mapping(),
                kind=connection.kind,
            )
            for connection in self._network.connectivity.connections
        )
        return NetworkReadModel(elements=tuple(elements), simple_wires=simple_wires)
    def element(self, element_type: str, object_id: str) -> ElementReadModel:
        """Return one NETWORK-domain element read model.

        Relay is a Core Network object, but its presentation/read ownership is
        PROTECTION. Generic Network element reads therefore reject Relay
        explicitly; callers needing Relay presentation data must use the
        Application protection read boundary.
        """
        requested = element_type.strip().lower()
        if requested in {"relay", "relays"}:
            raise ValueError(
                "Relay is owned by the protection read boundary; "
                "use Application.read_relay() instead of read_element()."
            )
        read_model_type = _ELEMENT_TYPE_ALIASES.get(requested, requested)
        if read_model_type == "relays":
            raise ValueError(
                "Relay is owned by the protection read boundary; "
                "use Application.read_relay() instead of read_element()."
            )
        core_type = _CORE_ELEMENT_TYPES.get(read_model_type)
        if core_type is None:
            raise KeyError(f"Unknown Application network element type: {element_type}")
        model = self._network.get_by_id(core_type, object_id)
        return self._to_read_model(read_model_type, model)
    @staticmethod
    def _value(model: Any, names: tuple[str, ...]) -> Any:
        for name in names:
            value = getattr(model, name, None)
            if value is not None: return value.value if hasattr(value, "value") else value
        return None
    @classmethod
    def _project_attributes(cls, element_type: str, model: Any) -> dict[str, Any]:
        attributes: dict[str, Any] = {}; missing_required: list[str] = []
        for canonical, aliases in _FIELD_CONTRACTS.get(element_type, ()):
            value = cls._value(model, aliases)
            if value is None:
                if canonical in _REQUIRED_FIELDS.get(element_type, ()): missing_required.append(canonical)
                continue
            if isinstance(value, (str, int, float, bool, complex)): attributes[canonical] = value
            elif canonical in _REQUIRED_FIELDS.get(element_type, ()): missing_required.append(canonical)
        if missing_required: raise ValueError(f"Missing required Application field(s) for {element_type}: {', '.join(missing_required)}")
        if element_type == "cables":
            conductor_system = getattr(model, "conductor_system", None)
            attributes["conductor_system_summary"] = (
                conductor_system.summary() if conductor_system is not None else None
            )
        if element_type == "transformers":
            enum_value = lambda value: value.value if hasattr(value, "value") else value
            vector_group = getattr(model, "vector_group_configuration", None)
            test_configuration = getattr(model, "zero_sequence_test_configuration", None)
            multiport = getattr(model, "phase_domain_terminal_admittance_siemens", None)
            evidence = getattr(model, "zero_sequence_multiport_evidence", None)
            attributes["transformer_configuration_summary"] = {
                "hv_winding_connection": enum_value(getattr(model, "hv_winding_connection", None)),
                "lv_winding_connection": enum_value(getattr(model, "lv_winding_connection", None)),
                "hv_terminal_role": getattr(model, "hv_terminal_role", None),
                "lv_terminal_role": getattr(model, "lv_terminal_role", None),
                "hv_neutral_terminal_role": getattr(model, "hv_neutral_terminal_role", None),
                "lv_neutral_terminal_role": getattr(model, "lv_neutral_terminal_role", None),
                "hv_neutral_grounding_impedance_ohm": getattr(model, "hv_neutral_grounding_impedance_ohm", None),
                "hv_neutral_earth_return_impedance_ohm": getattr(model, "hv_neutral_earth_return_impedance_ohm", None),
                "lv_neutral_earth_return_impedance_ohm": getattr(model, "lv_neutral_earth_return_impedance_ohm", None),
                "hv_phase_roles": getattr(model, "hv_phase_roles", None),
                "lv_phase_roles": getattr(model, "lv_phase_roles", None),
                "lv_neutral_grounding_impedance_ohm": getattr(model, "lv_neutral_grounding_impedance_ohm", None),
                "impedance_reference_winding": getattr(model, "impedance_reference_winding", None),
                "zero_sequence_resistance_ohm": getattr(model, "zero_sequence_resistance_ohm", None),
                "zero_sequence_reactance_ohm": getattr(model, "zero_sequence_reactance_ohm", None),
                "core_construction": enum_value(getattr(model, "core_construction", None)),
                "zero_sequence_test_basis": getattr(model, "zero_sequence_test_basis", None),
                "vector_group": ({
                    "connection": enum_value(vector_group.connection),
                    "clock_number": vector_group.clock_number,
                    "hv_polarity": enum_value(vector_group.hv_polarity),
                    "lv_polarity": enum_value(vector_group.lv_polarity),
                    "phase_displacement_deg": vector_group.phase_displacement_deg,
                } if vector_group is not None else None),
                "zero_sequence_test": ({
                    "energized_winding": enum_value(test_configuration.energized_winding),
                    "other_winding_condition": enum_value(test_configuration.other_winding_condition),
                    "neutral_return_included": test_configuration.neutral_return_included,
                } if test_configuration is not None else None),
                "two_winding_test_data_available": getattr(model, "zero_sequence_two_winding_test_data", None) is not None,
                "zero_sequence_two_winding_test_data": getattr(model, "zero_sequence_two_winding_test_data", None),
                "physical_conductor_construction_available": getattr(model, "physical_conductor_construction", None) is not None,
                # Keep the complete typed source inputs on the immutable
                # Application read side. Counts/availability alone are not
                # sufficient for an engineer to inspect or review the exact
                # configured multiport and its provenance.
                "physical_conductor_construction": getattr(model, "physical_conductor_construction", None),
                "phase_domain_terminal_count": len(multiport) if multiport is not None else None,
                "phase_domain_matrix_available": multiport is not None,
                "phase_domain_terminal_admittance_siemens": multiport,
                "phase_domain_multiport_tap_ratio": getattr(model, "phase_domain_multiport_tap_ratio", None),
                "phase_domain_multiport_phase_shift_rad": getattr(model, "phase_domain_multiport_phase_shift_rad", None),
                "zero_sequence_multiport_evidence_available": evidence is not None,
                "zero_sequence_multiport_evidence_reference": (
                    getattr(evidence, "core_zero_sequence_test_reference", None) if evidence is not None else None
                ),
                "zero_sequence_multiport_evidence": evidence,
                "phase_domain_delta_loop_states": tuple(getattr(model, "phase_domain_delta_loop_states", ())),
                "delta_loop_state_count": len(getattr(model, "phase_domain_delta_loop_states", ())),
            }
        if element_type == "fuses" and "in_service" in attributes and "blown" in attributes: attributes["conducts"] = bool(attributes["in_service"] and not attributes["blown"])
        if element_type == "disconnectors" and "closed" in attributes: attributes["conducts"] = bool(attributes["closed"] and attributes.get("in_service", True))
        if element_type == "reactors" and "reactive_power_injection_mvar" in attributes: attributes["absorption_mvar"] = abs(float(attributes["reactive_power_injection_mvar"]))
        if element_type == "batteries":
            if "soc" in attributes: attributes["state_of_charge"] = attributes["soc"]; attributes["soc_percent"] = float(attributes["soc"]) * 100.0
            if "energy_capacity_mwh" in attributes and "soc" in attributes:
                stored = float(attributes["energy_capacity_mwh"]) * float(attributes["soc"]); attributes["stored_energy_mwh"] = stored; attributes["available_energy_mwh"] = max(0.0, float(attributes["energy_capacity_mwh"]) - stored)
        return attributes
    @staticmethod
    def _to_read_model(element_type: str, model: Any) -> ElementReadModel:
        object_id = str(getattr(model, "id")); name = getattr(model, "name", None); labels = {"name": str(name)} if name is not None else {}
        connectivity_refs, terminal_connectivity = NetworkReadService._connectivity(model); attributes = NetworkReadService._project_attributes(element_type, model)
        engineering_parameters = NetworkReadService._engineering_parameters(
            element_type,
            attributes,
        )
        endpoint_from_id, endpoint_to_id = NetworkReadService._branch_endpoint_ids(model)
        if endpoint_from_id is not None: attributes.update({"from_endpoint": endpoint_from_id, "endpoint_from_id": endpoint_from_id, "from_terminal": endpoint_from_id})
        if endpoint_to_id is not None: attributes.update({"to_endpoint": endpoint_to_id, "endpoint_to_id": endpoint_to_id, "to_terminal": endpoint_to_id})
        if terminal_connectivity: attributes["terminal_connectivity"] = terminal_connectivity
        return ElementReadModel(object_id=object_id, element_type=element_type, labels=labels, connectivity_refs=connectivity_refs, attributes=attributes, engineering_parameters=engineering_parameters)
    @staticmethod
    def _engineering_parameters(
        element_type: str,
        attributes: dict[str, Any],
    ) -> tuple[EngineeringParameterReadModel, ...]:
        from .read_models import EngineeringParameterReadModel, SimpleWireReadModel

        result: list[EngineeringParameterReadModel] = []
        if element_type == "grids":
            for optional_field in (
                "impedance_base_mva",
                "impedance_base_voltage_kv",
                "neutral_grounding_impedance_ohm",
            ):
                attributes.setdefault(optional_field, None)
        basis = str(attributes.get("impedance_basis", "")).lower()
        for parameter_id, value in attributes.items():
            if parameter_id in {"from_endpoint", "endpoint_from_id", "from_terminal", "to_endpoint", "endpoint_to_id", "to_terminal", "terminal_connectivity"}:
                continue
            metadata = dict(_PARAMETER_METADATA.get(parameter_id, {}))
            if element_type == "grids" and parameter_id in {
                "impedance_base_mva",
                "impedance_base_voltage_kv",
                "neutral_grounding_impedance_ohm",
            }:
                metadata["editable"] = True
                metadata["validation"] = {**dict(metadata.get("validation", {})), "nullable": True}
            if element_type in {"loads", "generators"} and parameter_id == "in_service":
                metadata["editable"] = True
            if element_type == "lines" and parameter_id == "in_service":
                metadata["editable"] = True
            if element_type == "cables" and parameter_id == "conductor_count" and attributes.get("conductor_system_summary") is not None:
                metadata["editable"] = False
                metadata["validation"] = {
                    **dict(metadata.get("validation", {})),
                    "reason": "Conductor count is owned by the explicit physical conductor inventory.",
                }
            if element_type == "capacitive_voltage_transformers" and parameter_id == "polarity":
                metadata["choices"] = tuple(value.value for value in CVTPolarity)
                metadata["datatype"] = "enum"
                metadata["editable"] = True
                metadata["derived"] = False
            if element_type in {
                "current_transformers",
                "potential_transformers",
                "capacitive_voltage_transformers",
            }:
                metadata["editable"] = parameter_id not in {"ratio", "voltage_ratio"}
                if parameter_id in {"ratio", "voltage_ratio"}:
                    metadata["derived"] = True
                    metadata["editable"] = False
            if element_type == "transformers" and parameter_id in {"r", "x", "b", "impedance_basis", "impedance_base_mva", "tap", "shift", "rate_mva", "in_service"}:
                metadata["editable"] = True
            if parameter_id in {"r", "x"}:
                metadata.setdefault("unit", "pu" if basis == "pu" else "ohm")
                metadata.setdefault("study_impact", True)
                metadata.setdefault("coupling_group", "transformer_impedance")
            elif parameter_id == "b":
                metadata.setdefault("unit", "pu" if basis == "pu" else "S")
                metadata.setdefault("study_impact", True)
                metadata.setdefault("coupling_group", "transformer_impedance")
            if parameter_id in {"r", "x", "b"} and basis:
                metadata["validation"] = {"depends_on": ("impedance_basis", "impedance_base_mva", "impedance_base_voltage_kv")}
            result.append(EngineeringParameterReadModel(
                parameter_id=parameter_id,
                value=value,
                unit=metadata.get("unit"),
                datatype=metadata.get("datatype", type(value).__name__),
                choices=tuple(metadata.get("choices", ())),
                editable=bool(metadata.get("editable", False)),
                derived=bool(metadata.get("derived", False)),
                validation=metadata.get("validation", {}),
                coupling_group=metadata.get("coupling_group"),
                topology_impact=bool(metadata.get("topology_impact", False)),
                study_impact=bool(metadata.get("study_impact", False)),
            ))
        return tuple(result)

    @staticmethod
    def _connectivity(model: Any) -> tuple[tuple[str, ...], tuple[tuple[str, str | None], ...]]:
        refs: list[str] = []; terminal_connectivity: list[tuple[str, str | None]] = []; terminals = getattr(model, "terminals", None)
        if terminals is not None:
            for terminal in terminals:
                role = getattr(terminal, "role", None); terminal_id = getattr(terminal, "id", None); endpoint = getattr(terminal, "endpoint", None); endpoint_id = getattr(endpoint, "id", None)
                if terminal_id is not None: refs.append(str(terminal_id))
                if role is not None: terminal_connectivity.append((str(role), None if endpoint_id is None else str(endpoint_id)))
            return tuple(dict.fromkeys(refs)), tuple(terminal_connectivity)
        for attribute in ("from_terminal", "to_terminal", "terminal"):
            value = getattr(model, attribute, None); value_id = getattr(value, "id", None) if value is not None else None
            if value_id is not None: refs.append(str(value_id))
        return tuple(dict.fromkeys(refs)), ()
    @staticmethod
    def _branch_endpoint_ids(model: Any) -> tuple[str | None, str | None]:
        def endpoint_id(terminal: Any) -> str | None:
            if terminal is None: return None
            endpoint = getattr(terminal, "endpoint", None); value = getattr(endpoint, "id", None)
            if value is not None: return str(value)
            terminal_value = getattr(terminal, "id", None); return None if terminal_value is None else str(terminal_value)
        return endpoint_id(getattr(model, "from_terminal", None)), endpoint_id(getattr(model, "to_terminal", None))

class ProtectionReadService:
    """Read adapter over authoritative physical Relays, optionally scoped by runtime configuration."""
    def __init__(self, source: Network | ProtectionSystem, decision_provider: Any | None = None) -> None:
        if not isinstance(source, (Network, ProtectionSystem)): raise TypeError("ProtectionReadService requires a Network or ProtectionSystem")
        if decision_provider is not None and not callable(decision_provider): raise TypeError("decision_provider must be callable.")
        self._source = source
        self._decision_provider = decision_provider
    def protection(self) -> ProtectionReadModel:
        return ProtectionReadModel(relays=tuple(self._to_read_model(relay, self._decision_provider) for relay in self._relays()))
    def relay(self, object_id: str) -> RelayReadModel:
        for relay in self._relays():
            if relay.id == object_id: return self._to_read_model(relay, self._decision_provider)
        raise KeyError(f"Relay '{object_id}' is not represented by the authoritative protection source")
    def _relays(self) -> tuple[Any, ...]:
        if isinstance(self._source, Network): return self._source.relays
        return self._source.relays()
    @staticmethod
    def _to_read_model(relay: Any, decision_provider: Any | None = None) -> RelayReadModel:
        bindings = tuple(
            RelayInputBindingReadModel(
                input_name=str(name),
                channel_id=None if getattr(channel, "id", None) is None else str(getattr(channel, "id")),
            )
            for name, channel in sorted(relay.input_channels.items())
        )
        provided = decision_provider(str(relay.id)) if decision_provider is not None else None
        if provided is None:
            element_decisions: tuple[ProtectionDecision, ...] = ()
        elif isinstance(provided, ProtectionDecision):
            element_decisions = (provided,)
        elif isinstance(provided, (tuple, list)):
            element_decisions = tuple(provided)
        else:
            raise TypeError(
                "Protection decision provider must return a ProtectionDecision, "
                "a sequence of decisions, or None."
            )
        # Preserve the legacy singular projection only when it is unambiguous.
        decision = element_decisions[0] if len(element_decisions) == 1 else None
        return RelayReadModel(
            object_id=str(relay.id),
            name=str(relay.name),
            relay_type=str(relay.type),
            function_type=str(relay.function_type),
            plugin_id=None if relay.plugin_id is None else str(relay.plugin_id),
            settings=relay.settings,
            in_service=bool(relay.in_service),
            enabled=bool(relay.enabled),
            blocked=bool(relay.blocked),
            picked_up=bool(relay.picked_up),
            tripped=bool(relay.tripped),
            input_channel_bindings=bindings,
            decision=decision,
            element_decisions=element_decisions,
        )

@dataclass(frozen=True, slots=True)
class ArchivedStudyResultReadModel:
    """Read-only archive row with stable execution identity and provenance."""

    record_id: UUID
    saved_at: str
    study_id: UUID
    project_id: str
    activation_generation: int
    source_revision: ProjectRevision
    study_type: str
    status: str
    message: str
    metadata: Any
    value: Any

    @property
    def transformer_delta_loop_currents(self) -> Any:
        """Find internal-loop diagnostics inside detached result snapshots."""
        def find(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return ()
            if isinstance(value, Mapping):
                direct = value.get("transformer_delta_loop_currents")
                if direct is not None:
                    return direct
                for key in ("fields", "values", "short_circuit", "result", "value"):
                    nested = value.get(key)
                    if nested is not None:
                        found = find(nested, depth + 1)
                        if found:
                            return found
                return ()
            if isinstance(value, (tuple, list)):
                for item in value:
                    found = find(item, depth + 1)
                    if found:
                        return found
            return ()
        return find(self.value)


@dataclass(frozen=True, slots=True)
class StudyResultNumericDeltaReadModel:
    """One numerical difference whose physical unit and bases are identical."""

    path: str
    left_value: complex | float
    right_value: complex | float
    delta: complex | float
    unit: str
    base: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class TransientDiscontinuitySignalDeltaReadModel:
    """Pre/post values and deltas for one typed signal at an event boundary."""

    signal_id: str
    unit: str
    left_pre: float
    right_pre: float
    pre_delta: float
    left_post: float
    right_post: float
    post_delta: float


@dataclass(frozen=True, slots=True)
class TransientDiscontinuityDeltaReadModel:
    """Matched event boundary with separate pre-event and post-event deltas."""

    event_semantic_keys: tuple[tuple[Any, ...], ...]
    time_s: float
    left_pre_topology_revision: int | None
    right_pre_topology_revision: int | None
    left_post_topology_revision: int | None
    right_post_topology_revision: int | None
    signals: tuple[TransientDiscontinuitySignalDeltaReadModel, ...]


@dataclass(frozen=True, slots=True)
class TransientSemanticEventMatchReadModel:
    """One event pair matched by engineering operation, not run-local event ID."""

    semantic_key: tuple[Any, ...]
    left_event_id: str
    right_event_id: str
    event_time_s: float
    left_dispatch_time_s: float | None
    right_dispatch_time_s: float | None
    dispatch_delta_s: float | None
    transition_type: str
    target_kind: str
    target_id: str
    previous_state: Any
    new_state: Any


@dataclass(frozen=True, slots=True)
class TransientSignalDeltaReadModel:
    """Exact-grid per-signal transient delta; no interpolation or index pairing."""

    signal_id: str
    unit: str
    left_values: tuple[float, ...]
    right_values: tuple[float, ...]
    deltas: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.signal_id or not self.unit:
            raise ValueError("Transient signal delta identity and unit must be non-empty.")
        left = tuple(float(value) for value in self.left_values)
        right = tuple(float(value) for value in self.right_values)
        delta = tuple(float(value) for value in self.deltas)
        if not (len(left) == len(right) == len(delta)):
            raise ValueError("Transient signal delta arrays must have identical lengths.")
        if any(not math.isfinite(value) for values in (left, right, delta) for value in values):
            raise ValueError("Transient signal comparison values must be finite.")
        object.__setattr__(self, "left_values", left)
        object.__setattr__(self, "right_values", right)
        object.__setattr__(self, "deltas", delta)


@dataclass(frozen=True, slots=True)
class StudyResultComparisonReadModel:
    """Two-execution comparison with only base-compatible numeric deltas."""

    left: StudyResultReadModel | ArchivedStudyResultReadModel
    right: StudyResultReadModel | ArchivedStudyResultReadModel
    compatible: bool
    compatibility_message: str
    same_source_revision: bool
    same_activation_generation: bool
    numeric_deltas: tuple[StudyResultNumericDeltaReadModel, ...] = ()
    numeric_comparison_status: str = "NO_BASE_COMPATIBLE_VALUES"
    transient_signal_deltas: tuple[TransientSignalDeltaReadModel, ...] = ()
    transient_compatibility_diagnostics: tuple[str, ...] = ()
    transient_event_matches: tuple[TransientSemanticEventMatchReadModel, ...] = ()
    transient_discontinuity_deltas: tuple[TransientDiscontinuityDeltaReadModel, ...] = ()


def _study_result_numeric_observations(value: Any) -> dict[str, tuple[complex | float, str, tuple[Any, ...]]]:
    """Extract only solver values whose unit and engineering bases are explicit."""
    observations: dict[str, tuple[complex | float, str, tuple[Any, ...]]] = {}
    ambiguous_observations: set[str] = set()
    visited = 0

    def add_observation(
        identity: str,
        observation: tuple[complex | float, str, tuple[Any, ...]],
    ) -> None:
        # Duplicate physical identities are ambiguous; never let traversal
        # order silently choose which value participates in a comparison.
        if identity in ambiguous_observations:
            return
        if identity in observations:
            observations.pop(identity, None)
            ambiguous_observations.add(identity)
            return
        observations[identity] = observation

    def visit(item: Any, path: str, depth: int) -> None:
        nonlocal visited
        if depth > 12 or visited >= 20000 or item is None:
            return
        visited += 1
        if isinstance(item, PowerFlowResult):
            # Both a stable solver snapshot and stable bus identities are
            # mandatory. Positional fallback would silently pair different
            # buses when the result's ordering changes.
            if item.snapshot_id is None or not item.bus_ids:
                return
            for bus_id, magnitude, angle in zip(item.bus_ids, item.voltage_magnitudes, item.voltage_angles):
                prefix = f"bus:{bus_id!r}"
                add_observation(
                    f"{prefix}.voltage_magnitude",
                    (magnitude, "p.u.", (item.snapshot_id,)),
                )
                add_observation(
                    f"{prefix}.voltage_angle",
                    (angle, "deg", (item.snapshot_id,)),
                )
            return
        if isinstance(item, ShortCircuitConductorCurrent):
            # Match by physical conductor/terminal identity, never by collection
            # position. Topology revision is part of the comparison base so a
            # conductor record from a different topology cannot be paired.
            identity = (
                item.equipment_id,
                item.terminal_role,
                item.conductor_id,
                item.bus_id,
                item.phase_or_sequence,
            )
            observation_path = f"conductor:{identity!r}"
            base = (
                item.base_mva,
                item.voltage_base_kv,
                item.topology_revision,
                item.phase_or_sequence,
                item.reference_direction,
                item.current_units,
            )
            add_observation(
                f"{observation_path}.current",
                (item.current, "p.u.", base),
            )
            return
        if isinstance(item, ShortCircuitTransformerDeltaLoopCurrent):
            # A transformer may contain multiple loops and winding records.
            # Include all physical identifiers in the key; direction and
            # current base are also required to agree before calculating delta.
            identity = (item.transformer_id, item.loop_id, item.winding)
            observation_path = f"transformer_delta_loop:{identity!r}"
            base = (
                item.base_mva,
                item.voltage_base_kv,
                item.current_base_a,
                item.positive_direction,
            )
            add_observation(
                f"{observation_path}.current_a",
                (item.current_a, "A", base),
            )
            add_observation(
                f"{observation_path}.current_pu",
                (item.current_pu, "p.u.", base),
            )
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                visit(nested, f"{path}.{key}", depth + 1)
            return
        if isinstance(item, (tuple, list)):
            for index, nested in enumerate(item):
                visit(nested, f"{path}[{index}]", depth + 1)
            return
        if is_dataclass(item) and not isinstance(item, type):
            for descriptor in fields(item):
                visit(getattr(item, descriptor.name), f"{path}.{descriptor.name}", depth + 1)

    visit(value, "result", 0)
    return observations


class StudyReadService:
    """Canonical Application read boundary for live and archived study results.

    The service adapts the existing StudyService and StudyResultArchive. It
    performs no execution, mutation, numerical reinterpretation or measurement
    generation. Archived values remain detached snapshots.
    """

    def __init__(self, studies: Any, archive: Any | None = None) -> None:
        if studies is None or not callable(getattr(studies, "get_result", None)):
            raise TypeError("studies must provide get_result().")
        if not callable(getattr(studies, "results", None)):
            raise TypeError("studies must provide results().")
        self._studies = studies
        self._archive = None
        self.configure_archive(archive)

    def configure_archive(self, archive: Any | None) -> None:
        if archive is not None and not callable(getattr(archive, "load_all", None)):
            raise TypeError("archive must provide load_all().")
        if archive is not None and not callable(getattr(archive, "load_latest", None)):
            raise TypeError("archive must provide load_latest().")
        self._archive = archive

    @staticmethod
    def _to_read_model(
        result: Any,
        *,
        current_project_id: str | None = None,
        current_activation_generation: int | None = None,
        current_revision: ProjectRevision | None = None,
    ) -> StudyResultReadModel | None:
        if result is None:
            return None
        source_revision = result.source_revision
        current = (
            current_project_id is not None
            and current_activation_generation is not None
            and current_revision is not None
            and result.project_id == current_project_id
            and result.activation_generation == current_activation_generation
            and source_revision.matches_study_state(current_revision)
        )
        return StudyResultReadModel(
            study_id=result.study_id,
            project_id=result.project_id,
            activation_generation=result.activation_generation,
            source_revision=source_revision,
            study_type=result.study_type,
            status=result.status,
            value=result.value,
            message=result.message,
            metadata=result.metadata,
            current=current,
        )

    @staticmethod
    def _archived_to_read_model(record: Any) -> ArchivedStudyResultReadModel:
        result = record.result
        return ArchivedStudyResultReadModel(
            record_id=record.record_id,
            saved_at=record.saved_at,
            study_id=result.study_id,
            project_id=result.project_id,
            activation_generation=result.activation_generation,
            source_revision=result.source_revision,
            study_type=result.study_type,
            status=result.status,
            message=result.message,
            metadata=result.metadata,
            value=result.value,
        )

    @staticmethod
    def _transient_presentation(
        row: Any,
        *,
        historical: bool,
        record_id: UUID | None = None,
        saved_at: str | None = None,
    ) -> TransientResultPresentationReadModel | None:
        value = getattr(row, "value", None)
        transient_fields: Mapping[str, Any] | None = None
        if isinstance(value, TransientStabilityResult):
            transient_fields = {
                "time": value.time,
                "states": value.states,
                "terminal_voltages": value.terminal_voltages,
                "electrical_powers": value.electrical_powers,
                "events": value.events,
                "algebraic_quality": value.algebraic_quality,
                "discontinuities": value.discontinuities,
                "provenance": value.provenance,
            }
        elif isinstance(value, Mapping) and value.get("$dataclass") == (
            f"{TransientStabilityResult.__module__}.{TransientStabilityResult.__qualname__}"
        ):
            # Schema-v1 archive records deliberately retain their historical
            # generic-dataclass mapping semantics. Adapt the known payload
            # shape here without mutating/retyping the archived source record.
            candidate = value.get("fields")
            if not isinstance(candidate, Mapping):
                raise ValueError("Legacy transient result mapping has no canonical fields object.")
            transient_fields = candidate
        else:
            return None

        time_s = tuple(float(item) for item in transient_fields.get("time", ()))
        raw_states = transient_fields.get("states", ())
        voltage_samples = tuple(dict(sample) for sample in transient_fields.get("terminal_voltages", ()))
        power_samples = tuple(dict(sample) for sample in transient_fields.get("electrical_powers", ()))
        sample_count = len(time_s)
        source_indices = tuple(range(sample_count))
        signals: list[TransientSignalReadModel] = []

        # State column labels are authored by the solver from its global
        # MultiMachineSystem state layout. Legacy results without descriptors
        # remain fully inspectable through explicit unlabeled State N signals.
        provenance_source = transient_fields.get("provenance", {})
        if not isinstance(provenance_source, Mapping):
            raise ValueError("Transient solver provenance must be a mapping.")
        state_descriptors = provenance_source.get("state_signals", ())
        if getattr(raw_states, "ndim", None) == 2:
            state_width = int(raw_states.shape[1])
        elif len(raw_states):
            state_width = len(raw_states[0])
        else:
            state_width = 0
        if len(raw_states) != sample_count:
            raise ValueError("Transient time and state sample counts must match exactly.")
        if any(len(sample) != state_width for sample in raw_states):
            raise ValueError("Transient state rows must retain a consistent column width.")
        if len(voltage_samples) != sample_count or len(power_samples) != sample_count:
            raise ValueError("Transient time, voltage and electrical-power sample counts must match exactly.")
        if state_descriptors:
            if not isinstance(state_descriptors, (tuple, list)):
                raise ValueError("Transient solver state_signals provenance must be an ordered sequence.")
            descriptors = []
            for descriptor in state_descriptors:
                if not isinstance(descriptor, Mapping):
                    raise ValueError("Transient state signal descriptor must be a mapping.")
                global_index = descriptor.get("global_index")
                if (
                    isinstance(global_index, bool)
                    or not isinstance(global_index, int)
                    or global_index < 0
                    or global_index >= state_width
                ):
                    raise ValueError("Transient state signal descriptor has an invalid global_index.")
                descriptors.append(descriptor)
            indices = tuple(int(item["global_index"]) for item in descriptors)
            if len(indices) != len(set(indices)) or set(indices) != set(range(state_width)):
                raise ValueError("Transient state signal descriptors must cover every state column exactly once.")
        else:
            descriptors = tuple(
                {
                    "signal_id": f"state:{index}",
                    "global_index": index,
                    "label": f"State {index} (unlabeled)",
                    "state_name": f"state_{index}",
                    "unit": "unknown",
                    "machine_id": None,
                }
                for index in range(state_width)
            )

        for descriptor in descriptors:
            index = int(descriptor["global_index"])
            signals.append(TransientSignalReadModel(
                signal_id=str(descriptor.get("signal_id") or f"state:{index}"),
                label=str(descriptor.get("label") or f"State {index}"),
                quantity=str(descriptor.get("state_name") or f"state_{index}"),
                unit=str(descriptor.get("unit") or "unknown"),
                values=tuple(float(sample[index]) for sample in raw_states),
                source_indices=source_indices,
                machine_id=(
                    None if descriptor.get("machine_id") is None
                    else str(descriptor.get("machine_id"))
                ),
            ))

        bus_ids: tuple[str, ...] = ()
        if voltage_samples:
            bus_ids = tuple(sorted(str(bus_id) for bus_id in voltage_samples[0]))
            expected = set(bus_ids)
            for sample_index, sample in enumerate(voltage_samples):
                if set(sample) != expected:
                    raise ValueError(
                        f"Transient terminal-voltage Bus identities changed at sample {sample_index}; "
                        "the Application read model will not reorder or silently fill missing samples."
                    )
        for bus_id in bus_ids:
            signals.extend((
                TransientSignalReadModel(
                    signal_id=f"voltage:{bus_id}:real",
                    label=f"{bus_id} — Re(V)",
                    quantity="terminal_voltage_real",
                    unit="p.u.",
                    values=tuple(float(sample[bus_id].real) for sample in voltage_samples),
                    source_indices=source_indices,
                    bus_id=bus_id,
                    component="real",
                ),
                TransientSignalReadModel(
                    signal_id=f"voltage:{bus_id}:imag",
                    label=f"{bus_id} — Im(V)",
                    quantity="terminal_voltage_imag",
                    unit="p.u.",
                    values=tuple(float(sample[bus_id].imag) for sample in voltage_samples),
                    source_indices=source_indices,
                    bus_id=bus_id,
                    component="imag",
                ),
            ))

        machine_ids: tuple[str, ...] = ()
        if power_samples:
            machine_ids = tuple(sorted(str(machine_id) for machine_id in power_samples[0]))
            expected = set(machine_ids)
            for sample_index, sample in enumerate(power_samples):
                if set(sample) != expected:
                    raise ValueError(
                        f"Transient electrical-power machine identities changed at sample {sample_index}; "
                        "the Application read model will not reorder or silently fill missing samples."
                    )
        for machine_id in machine_ids:
            for component_index, component, label in (
                (0, "active_power", "P"),
                (1, "reactive_power", "Q"),
            ):
                values = []
                for sample_index, sample in enumerate(power_samples):
                    pair = sample[machine_id]
                    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                        raise ValueError(
                            f"Transient electrical-power sample {sample_index} for machine '{machine_id}' "
                            "must contain exactly P and Q."
                        )
                    values.append(float(pair[component_index]))
                signals.append(TransientSignalReadModel(
                    signal_id=f"power:{machine_id}:{component}",
                    label=f"{machine_id} — {label}",
                    quantity=component,
                    unit="p.u.",
                    values=tuple(values),
                    source_indices=source_indices,
                    machine_id=machine_id,
                    component=component,
                ))

        event_records = []
        event_class_name = f"{EventExecution.__module__}.{EventExecution.__qualname__}"
        for event in transient_fields.get("events", ()):
            if isinstance(event, EventExecution):
                event_data = {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "scheduled_time": event.scheduled_time,
                    "dispatch_time": event.dispatch_time,
                    "priority": event.priority,
                    "description": event.description,
                    "status": event.status,
                    "metadata": event.metadata,
                    "result": event.result,
                    "failure_type": event.failure_type,
                    "failure_message": event.failure_message,
                    "pre_event_revision": event.pre_event_revision,
                    "post_event_revision": event.post_event_revision,
                    "post_event_re_evaluation_status": event.post_event_re_evaluation_status,
                    "post_event_re_evaluation_failure_type": event.post_event_re_evaluation_failure_type,
                    "post_event_re_evaluation_failure_message": event.post_event_re_evaluation_failure_message,
                    "transition": (
                        None if event.transition is None else {
                            "transition_type": event.transition.transition_type,
                            "target_kind": event.transition.target_kind,
                            "target_id": event.transition.target_id,
                            "previous_state": event.transition.previous_state,
                            "new_state": event.transition.new_state,
                            "source_topology_revision": event.transition.source_topology_revision,
                            "pre_event_revision": event.transition.pre_event_revision,
                            "post_event_revision": event.transition.post_event_revision,
                            "details": event.transition.details,
                        }
                    ),
                }
            elif isinstance(event, Mapping) and event.get("$dataclass") == event_class_name:
                legacy_event_fields = event.get("fields")
                if not isinstance(legacy_event_fields, Mapping):
                    raise ValueError("Legacy EventExecution mapping has no canonical fields object.")
                scheduled_time = float(legacy_event_fields["time"])
                legacy_transition = legacy_event_fields.get("transition")
                if isinstance(legacy_transition, Mapping) and isinstance(legacy_transition.get("fields"), Mapping):
                    legacy_transition = legacy_transition["fields"]
                if isinstance(legacy_transition, Mapping) and legacy_transition.get("$dataclass"):
                    legacy_transition = legacy_transition.get("fields")
                typed_transition = (
                    EventTransition.from_mapping(legacy_transition)
                    if isinstance(legacy_transition, Mapping) else None
                )
                event_data = {
                    "event_id": str(legacy_event_fields["event_id"]),
                    "event_type": str(legacy_event_fields["event_type"]),
                    "scheduled_time": scheduled_time,
                    "dispatch_time": None,
                    "priority": None,
                    "description": None,
                    "status": "succeeded",
                    "metadata": {},
                    "result": legacy_event_fields.get("result"),
                    "failure_type": None,
                    "failure_message": None,
                    "pre_event_revision": None,
                    "post_event_revision": None,
                    "post_event_re_evaluation_status": "unavailable",
                    "post_event_re_evaluation_failure_type": None,
                    "post_event_re_evaluation_failure_message": None,
                    "transition": (
                        None if typed_transition is None else {
                            "transition_type": typed_transition.transition_type,
                            "target_kind": typed_transition.target_kind,
                            "target_id": typed_transition.target_id,
                            "previous_state": typed_transition.previous_state,
                            "new_state": typed_transition.new_state,
                            "source_topology_revision": typed_transition.source_topology_revision,
                            "pre_event_revision": typed_transition.pre_event_revision,
                            "post_event_revision": typed_transition.post_event_revision,
                            "details": typed_transition.details,
                        }
                    ),
                }
            else:
                raise ValueError("Transient event history contains an unknown event record type.")
            event_records.append(TransientEventReadModel(**event_data))
        events = tuple(event_records)
        quality_source = transient_fields.get("algebraic_quality", ())
        if quality_source is None:
            quality_source = ()
        if not isinstance(quality_source, (tuple, list)):
            raise ValueError("Transient algebraic_quality must be an ordered sequence.")
        quality_records = []
        if "algebraic_quality" in transient_fields and len(quality_source) != sample_count:
            raise ValueError(
                "Transient algebraic-quality record count must match the recorded time sample count."
            )
        algebraic_solution_name = (
            f"{AlgebraicNetworkSolution.__module__}.{AlgebraicNetworkSolution.__qualname__}"
        )
        for sample_index, record in enumerate(quality_source):
            if isinstance(record, AlgebraicNetworkSolution):
                quality_fields = record
                def quality_value(name: str, default: Any = None) -> Any:
                    return getattr(quality_fields, name, default)
            elif isinstance(record, Mapping) and record.get("$dataclass") == algebraic_solution_name:
                legacy_fields = record.get("fields")
                if not isinstance(legacy_fields, Mapping):
                    raise ValueError("Legacy AlgebraicNetworkSolution mapping has no canonical fields object.")
                def quality_value(name: str, default: Any = None) -> Any:
                    return legacy_fields.get(name, default)
            else:
                raise ValueError("Transient algebraic quality contains an unknown record type.")
            solver_time = quality_value("simulation_time")
            if solver_time is not None and not math.isclose(
                float(solver_time), time_s[sample_index], rel_tol=0.0, abs_tol=1e-12
            ):
                raise ValueError(
                    f"Transient algebraic-quality sample {sample_index} time does not match the recorded time vector."
                )
            quality_records.append(TransientAlgebraicQualityReadModel(
                sample_index=sample_index,
                time_s=time_s[sample_index],
                status=quality_value("status", "UNAVAILABLE"),
                accepted=quality_value("accepted", False),
                quality_verified=quality_value("quality_verified", False),
                solver_name=quality_value("solver_name", "unavailable"),
                solver_time_s=None if solver_time is None else float(solver_time),
                bus_voltages=quality_value("bus_voltages", {}),
                absolute_residual_norm=quality_value("absolute_residual_norm"),
                normalized_residual=quality_value("normalized_residual"),
                residual_tolerance=quality_value("residual_tolerance"),
                condition_number=quality_value("condition_number"),
                condition_limit=quality_value("condition_limit"),
                bus_ids=tuple(quality_value("bus_ids", ())),
                topology_revision=quality_value("topology_revision"),
                source_ids=tuple(quality_value("source_ids", ())),
                active_fault_id=quality_value("active_fault_id"),
                active_fault_bus_id=quality_value("active_fault_bus_id"),
                active_fault_type=quality_value("active_fault_type"),
                study_provenance=quality_value("study_provenance", {}),
                diagnostics=tuple(quality_value("diagnostics", ())),
            ))
        algebraic_quality = tuple(quality_records)
        discontinuity_records = []
        discontinuity_class_name = f"{TransientDiscontinuity.__module__}.{TransientDiscontinuity.__qualname__}"
        for record in transient_fields.get("discontinuities", ()):
            if isinstance(record, TransientDiscontinuity):
                discontinuity = record
                discontinuity_records.append(TransientDiscontinuityReadModel(
                    time_s=discontinuity.time,
                    event_ids=discontinuity.event_ids,
                    post_status=discontinuity.post_status,
                    pre_state=tuple(float(value) for value in discontinuity.pre_state),
                    pre_terminal_voltages=discontinuity.pre_terminal_voltages,
                    pre_electrical_powers=discontinuity.pre_electrical_powers,
                    pre_topology_revision=discontinuity.pre_topology_revision,
                    post_state=None if discontinuity.post_state is None else tuple(float(value) for value in discontinuity.post_state),
                    post_terminal_voltages=discontinuity.post_terminal_voltages,
                    post_electrical_powers=discontinuity.post_electrical_powers,
                    post_topology_revision=discontinuity.post_topology_revision,
                ))
            elif isinstance(record, Mapping) and record.get("$dataclass") == discontinuity_class_name:
                raise ValueError("Legacy generic discontinuity records are not supported; use archive schema v4.")
            else:
                raise ValueError("Transient discontinuities contain an unknown record type.")
        discontinuities = tuple(discontinuity_records)
        provenance = dict(provenance_source)
        provenance["execution_identity"] = {
            "study_id": str(row.study_id),
            "project_id": row.project_id,
            "activation_generation": row.activation_generation,
            "source_revision": row.source_revision,
            "current": bool(getattr(row, "current", False)) if not historical else False,
            "historical": historical,
            "record_id": None if record_id is None else str(record_id),
            "saved_at": saved_at,
        }
        return TransientResultPresentationReadModel(
            study_id=row.study_id,
            project_id=row.project_id,
            activation_generation=row.activation_generation,
            source_revision=row.source_revision,
            study_type=row.study_type,
            status=row.status,
            message=str(getattr(row, "message", "") or ""),
            current=bool(getattr(row, "current", False)) if not historical else False,
            historical=historical,
            record_id=record_id,
            saved_at=saved_at,
            time_s=time_s,
            signals=tuple(signals),
            events=events,
            provenance=provenance,
            algebraic_quality=algebraic_quality,
            discontinuities=discontinuities,
        )

    def transient_result(
        self,
        study_id: UUID,
        *,
        project_id: str,
        activation_generation: int,
        current_revision: ProjectRevision | None = None,
    ) -> TransientResultPresentationReadModel | None:
        row = self.result(
            study_id,
            project_id=project_id,
            activation_generation=activation_generation,
            current_revision=current_revision,
        )
        if row is None:
            return None
        return self._transient_presentation(row, historical=False)

    def archived_transient_result(
        self,
        record_id: UUID,
        *,
        project_id: str,
        activation_generation: int | None = None,
    ) -> TransientResultPresentationReadModel | None:
        row = self.archived_result(
            record_id,
            project_id=project_id,
            activation_generation=activation_generation,
        )
        return self._transient_presentation(
            row,
            historical=True,
            record_id=row.record_id,
            saved_at=row.saved_at,
        )

    def result(
        self,
        study_id: UUID,
        *,
        project_id: str,
        activation_generation: int,
        current_revision: ProjectRevision | None = None,
    ) -> StudyResultReadModel | None:
        result = self._studies.get_result(
            study_id,
            project_id=project_id,
            activation_generation=activation_generation,
        )
        return self._to_read_model(
            result,
            current_project_id=project_id,
            current_activation_generation=activation_generation,
            current_revision=current_revision,
        )

    def results(
        self,
        *,
        project_id: str,
        activation_generation: int,
        current_revision: ProjectRevision | None = None,
    ) -> tuple[StudyResultReadModel, ...]:
        return tuple(
            self._to_read_model(
                result,
                current_project_id=project_id,
                current_activation_generation=activation_generation,
                current_revision=current_revision,
            )
            for result in self._studies.results(
                project_id=project_id,
                activation_generation=activation_generation,
            )
        )

    def archived_results(
        self,
        *,
        project_id: str,
        activation_generation: int | None = None,
        study_id: UUID | None = None,
        study_type: str | None = None,
        status: str | None = None,
        saved_after: str | None = None,
        saved_before: str | None = None,
        search: str | None = None,
    ) -> tuple[ArchivedStudyResultReadModel, ...]:
        if self._archive is None:
            return ()
        if status is not None and status not in {"completed", "failed", "cancelled"}:
            raise ValueError("status must be completed, failed, cancelled, or None.")
        if study_type is not None and (not isinstance(study_type, str) or not study_type.strip()):
            raise ValueError("study_type must be a non-empty string or None.")
        def parse_timestamp(value: str | None, label: str) -> datetime | None:
            if value is None:
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"{label} must be an ISO-8601 timestamp.") from exc
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

        after = parse_timestamp(saved_after, "saved_after")
        before = parse_timestamp(saved_before, "saved_before")
        if after is not None and before is not None and after > before:
            raise ValueError("saved_after must not be later than saved_before.")
        records = self._archive.load_all(
            project_id,
            activation_generation=activation_generation,
            study_id=study_id,
        )
        needle = search.strip().casefold() if isinstance(search, str) else ""
        rows = []
        for record in records:
            row = self._archived_to_read_model(record)
            if study_type is not None and row.study_type != study_type.strip():
                continue
            if status is not None and row.status != status:
                continue
            saved_at = parse_timestamp(row.saved_at, "archived saved_at")
            if after is not None and (saved_at is None or saved_at < after):
                continue
            if before is not None and (saved_at is None or saved_at > before):
                continue
            if needle:
                haystack = " ".join((
                    str(row.study_id), row.study_type, row.status, row.message,
                    str(row.metadata), str(row.record_id),
                )).casefold()
                if needle not in haystack:
                    continue
            rows.append(row)
        return tuple(sorted(rows, key=lambda row: (row.saved_at, str(row.record_id)), reverse=True))

    def archived_result(
        self, record_id: UUID, *, project_id: str,
        activation_generation: int | None = None,
    ) -> ArchivedStudyResultReadModel:
        if not isinstance(record_id, UUID):
            raise TypeError("record_id must be a UUID.")
        for row in self.archived_results(
            project_id=project_id, activation_generation=activation_generation
        ):
            if row.record_id == record_id:
                return row
        raise KeyError(f"Archived Study Result record '{record_id}' was not found in this project scope.")

    def compare_study_results(
        self, left_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel,
        right_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel, *,
        project_id: str, activation_generation: int | None = None,
        current_revision: ProjectRevision | None = None,
    ) -> StudyResultComparisonReadModel:
        """Compare two project-scoped live and/or archived result snapshots."""
        def resolve(reference: Any) -> StudyResultReadModel | ArchivedStudyResultReadModel:
            if isinstance(reference, ArchivedStudyResultReadModel):
                if reference.project_id != project_id:
                    raise KeyError("Archived Study Result belongs to another project.")
                return reference
            if isinstance(reference, StudyResultReadModel):
                if reference.project_id != project_id:
                    raise KeyError("Live Study Result belongs to another project.")
                if activation_generation is not None and reference.activation_generation != activation_generation:
                    raise KeyError("Live Study Result belongs to another activation generation.")
                return reference
            return self.archived_result(reference, project_id=project_id)

        left = resolve(left_record_id)
        right = resolve(right_record_id)
        left_is_historical = isinstance(left, ArchivedStudyResultReadModel)
        right_is_historical = isinstance(right, ArchivedStudyResultReadModel)
        left_transient = self._transient_presentation(
            left, historical=left_is_historical,
            record_id=getattr(left, "record_id", None), saved_at=getattr(left, "saved_at", None),
        )
        right_transient = self._transient_presentation(
            right, historical=right_is_historical,
            record_id=getattr(right, "record_id", None), saved_at=getattr(right, "saved_at", None),
        )
        if left_transient is not None or right_transient is not None:
            diagnostics: list[str] = []
            if left_transient is None or right_transient is None:
                diagnostics.append("TRANSIENT_RESULT_TYPE_MISMATCH")
            if left.project_id != right.project_id:
                diagnostics.append("PROJECT_ID_MISMATCH")
            if left.study_type != right.study_type:
                diagnostics.append("STUDY_TYPE_MISMATCH")
            if left.status != "completed" or right.status != "completed":
                diagnostics.append("INCOMPLETE_OR_NON_SUCCESSFUL_EXECUTION")
            if isinstance(left, StudyResultReadModel) and (
                not left.current
                or (current_revision is not None and not left.source_revision.matches_study_state(current_revision))
            ):
                diagnostics.append("LEFT_LIVE_RESULT_NOT_CURRENT")
            if isinstance(right, StudyResultReadModel) and (
                not right.current
                or (current_revision is not None and not right.source_revision.matches_study_state(current_revision))
            ):
                diagnostics.append("RIGHT_LIVE_RESULT_NOT_CURRENT")
            if left_transient is not None and right_transient is not None:
                if left_transient.time_s != right_transient.time_s:
                    diagnostics.append("TIME_GRID_MISMATCH")
                left_signals = {item.signal_id: item for item in left_transient.signals}
                right_signals = {item.signal_id: item for item in right_transient.signals}
                if set(left_signals) != set(right_signals):
                    diagnostics.append("SIGNAL_IDENTITY_SET_MISMATCH")
                # Compare provenance only within the same named semantic dimension.
                # The canonical Core topology fingerprint and detached prepared-network
                # fingerprint have different authorities/input domains; neither is
                # compared to, nor substituted for, the other.
                required_provenance = (
                    "nominal_frequency_hz",
                    "power_base_mva",
                    "bus_voltage_bases_kv",
                    "canonical_topology_fingerprint",
                    "prepared_network_topology_fingerprint",
                    "machine_model_manifest",
                )
                for key in required_provenance:
                    left_value = left_transient.provenance.get(key)
                    right_value = right_transient.provenance.get(key)
                    if left_value is None or right_value is None:
                        diagnostics.append(f"PROVENANCE_UNAVAILABLE:{key}")
                    elif left_value != right_value:
                        diagnostics.append(f"PROVENANCE_MISMATCH:{key}")
                for signal_id in sorted(set(left_signals).intersection(right_signals)):
                    a, b = left_signals[signal_id], right_signals[signal_id]
                    identity_a = (a.quantity, a.unit, a.machine_id, a.bus_id, a.component)
                    identity_b = (b.quantity, b.unit, b.machine_id, b.bus_id, b.component)
                    if identity_a != identity_b:
                        diagnostics.append(f"SIGNAL_SEMANTICS_MISMATCH:{signal_id}")
                def semantic_value(value: Any) -> Any:
                    if isinstance(value, Mapping):
                        return tuple(
                            (str(key), semantic_value(item))
                            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                        )
                    if isinstance(value, (tuple, list)):
                        return tuple(semantic_value(item) for item in value)
                    if isinstance(value, (str, int, float, bool, type(None))):
                        return value
                    return (type(value).__module__, type(value).__qualname__, str(value))

                def event_key(event: TransientEventReadModel) -> tuple[Any, ...] | None:
                    transition = event.transition
                    if transition is None:
                        return None
                    transition_type = transition.get("transition_type")
                    target_kind = transition.get("target_kind")
                    target_id = transition.get("target_id")
                    if not all(isinstance(value, str) and value.strip() for value in (transition_type, target_kind, target_id)):
                        return None
                    return (
                        event.event_type,
                        event.scheduled_time,
                        transition_type,
                        target_kind,
                        target_id,
                        semantic_value(transition.get("previous_state")),
                        semantic_value(transition.get("new_state")),
                    )
                left_event_keys = [event_key(event) for event in left_transient.events]
                right_event_keys = [event_key(event) for event in right_transient.events]
                if any(key is None for key in left_event_keys + right_event_keys):
                    diagnostics.append("EVENT_SEMANTIC_IDENTITY_UNAVAILABLE")
                else:
                    if len(set(left_event_keys)) != len(left_event_keys) or len(set(right_event_keys)) != len(right_event_keys):
                        diagnostics.append("EVENT_SEMANTIC_IDENTITY_AMBIGUOUS")
                    elif set(left_event_keys) != set(right_event_keys):
                        diagnostics.append("EVENT_SEMANTIC_SET_MISMATCH")
                    elif any(event.status != "succeeded" for event in left_transient.events + right_transient.events):
                        diagnostics.append("EVENT_DISPATCH_NOT_SUCCESSFUL")
                    else:
                        right_by_key = {
                            event_key(event): event for event in right_transient.events
                        }
                        for event, key in zip(left_transient.events, left_event_keys):
                            peer = right_by_key[key]
                            if (
                                event.post_event_re_evaluation_status != "succeeded"
                                or peer.post_event_re_evaluation_status != "succeeded"
                            ):
                                diagnostics.append(f"EVENT_POST_EVALUATION_NOT_SUCCESSFUL:{event.event_id}")
            deltas: list[TransientSignalDeltaReadModel] = []
            event_matches: list[TransientSemanticEventMatchReadModel] = []
            discontinuity_deltas: list[TransientDiscontinuityDeltaReadModel] = []
            if not diagnostics and left_transient is not None and right_transient is not None:
                right_signals = {item.signal_id: item for item in right_transient.signals}
                def boundary_key(
                    boundary: TransientDiscontinuityReadModel,
                    events: tuple[TransientEventReadModel, ...],
                ) -> tuple[Any, ...] | None:
                    if boundary.post_status != "succeeded":
                        return None
                    keys = []
                    for event_id in boundary.event_ids:
                        event = next((item for item in events if item.event_id == event_id), None)
                        if event is None:
                            return None
                        key = event_key(event)
                        if key is None:
                            return None
                        keys.append(key)
                    return (boundary.time_s, tuple(sorted(keys, key=repr)))
                left_boundaries = {
                    boundary_key(item, left_transient.events): item
                    for item in left_transient.discontinuities
                }
                right_boundaries = {
                    boundary_key(item, right_transient.events): item
                    for item in right_transient.discontinuities
                }
                if None in left_boundaries or None in right_boundaries:
                    diagnostics.append("DISCONTINUITY_BOUNDARY_EVIDENCE_UNAVAILABLE")
                elif set(left_boundaries) != set(right_boundaries):
                    diagnostics.append("DISCONTINUITY_BOUNDARY_SET_MISMATCH")
                elif len(left_boundaries) != len(left_transient.discontinuities) or len(right_boundaries) != len(right_transient.discontinuities):
                    diagnostics.append("DISCONTINUITY_BOUNDARY_IDENTITY_AMBIGUOUS")
                if not diagnostics:
                    state_descriptors = left_transient.provenance.get("state_signals")
                    right_state_descriptors = right_transient.provenance.get("state_signals")
                    if not isinstance(state_descriptors, (tuple, list)) or state_descriptors != right_state_descriptors:
                        diagnostics.append("DISCONTINUITY_STATE_DESCRIPTOR_MISMATCH")
                    else:
                        for boundary_key_value in sorted(left_boundaries, key=repr):
                            left_boundary = left_boundaries[boundary_key_value]
                            right_boundary = right_boundaries[boundary_key_value]
                            if (
                                left_boundary.post_state is None or right_boundary.post_state is None
                                or left_boundary.post_terminal_voltages is None or right_boundary.post_terminal_voltages is None
                                or left_boundary.post_electrical_powers is None or right_boundary.post_electrical_powers is None
                            ):
                                diagnostics.append("DISCONTINUITY_POST_SIDE_UNAVAILABLE")
                                break
                            left_values: dict[str, tuple[str, float, float]] = {}
                            right_values: dict[str, tuple[str, float, float]] = {}
                            for descriptor in state_descriptors:
                                index = descriptor.get("global_index")
                                signal_id = descriptor.get("signal_id")
                                unit = descriptor.get("unit")
                                if not isinstance(index, int) or not isinstance(signal_id, str) or not isinstance(unit, str):
                                    diagnostics.append("DISCONTINUITY_STATE_DESCRIPTOR_INVALID")
                                    break
                                if index >= len(left_boundary.pre_state) or index >= len(right_boundary.pre_state) or index >= len(left_boundary.post_state) or index >= len(right_boundary.post_state):
                                    diagnostics.append("DISCONTINUITY_STATE_WIDTH_MISMATCH")
                                    break
                                left_values[signal_id] = (unit, left_boundary.pre_state[index], left_boundary.post_state[index])
                                right_values[signal_id] = (unit, right_boundary.pre_state[index], right_boundary.post_state[index])
                            if diagnostics:
                                break
                            for side_name, left_map, right_map in (
                                ("voltage", left_boundary.pre_terminal_voltages, right_boundary.pre_terminal_voltages),
                            ):
                                if set(left_map) != set(right_map):
                                    diagnostics.append("DISCONTINUITY_PRE_VOLTAGE_IDENTITY_MISMATCH")
                                    break
                            if diagnostics:
                                break
                            if set(left_boundary.pre_electrical_powers) != set(right_boundary.pre_electrical_powers):
                                diagnostics.append("DISCONTINUITY_PRE_POWER_IDENTITY_MISMATCH")
                                break
                            if set(left_boundary.post_terminal_voltages) != set(right_boundary.post_terminal_voltages):
                                diagnostics.append("DISCONTINUITY_POST_VOLTAGE_IDENTITY_MISMATCH")
                                break
                            if set(left_boundary.post_electrical_powers) != set(right_boundary.post_electrical_powers):
                                diagnostics.append("DISCONTINUITY_POST_POWER_IDENTITY_MISMATCH")
                                break
                            for bus_id in sorted(left_boundary.pre_terminal_voltages):
                                for component, unit in (("real", "p.u."), ("imag", "p.u.")):
                                    signal_id = f"voltage:{bus_id}:{component}"
                                    lv = getattr(left_boundary.pre_terminal_voltages[bus_id], component)
                                    rv = getattr(right_boundary.pre_terminal_voltages[bus_id], component)
                                    lp = getattr(left_boundary.post_terminal_voltages[bus_id], component)
                                    rp = getattr(right_boundary.post_terminal_voltages[bus_id], component)
                                    left_values[signal_id] = (unit, lv, lp)
                                    right_values[signal_id] = (unit, rv, rp)
                            for machine_id in sorted(left_boundary.pre_electrical_powers):
                                for index, component in ((0, "active_power"), (1, "reactive_power")):
                                    signal_id = f"power:{machine_id}:{component}"
                                    left_values[signal_id] = ("p.u.", left_boundary.pre_electrical_powers[machine_id][index], left_boundary.post_electrical_powers[machine_id][index])
                                    right_values[signal_id] = ("p.u.", right_boundary.pre_electrical_powers[machine_id][index], right_boundary.post_electrical_powers[machine_id][index])
                            if set(left_values) != set(right_values):
                                diagnostics.append("DISCONTINUITY_SIGNAL_IDENTITY_MISMATCH")
                                break
                            boundary_signals = tuple(
                                TransientDiscontinuitySignalDeltaReadModel(
                                    signal_id=signal_id,
                                    unit=left_values[signal_id][0],
                                    left_pre=left_values[signal_id][1],
                                    right_pre=right_values[signal_id][1],
                                    pre_delta=right_values[signal_id][1] - left_values[signal_id][1],
                                    left_post=left_values[signal_id][2],
                                    right_post=right_values[signal_id][2],
                                    post_delta=right_values[signal_id][2] - left_values[signal_id][2],
                                )
                                for signal_id in sorted(left_values)
                                if left_values[signal_id][0] == right_values[signal_id][0]
                            )
                            if len(boundary_signals) != len(left_values):
                                diagnostics.append("DISCONTINUITY_SIGNAL_UNIT_MISMATCH")
                                break
                            discontinuity_deltas.append(TransientDiscontinuityDeltaReadModel(
                                event_semantic_keys=boundary_key_value[1],
                                time_s=boundary_key_value[0],
                                left_pre_topology_revision=left_boundary.pre_topology_revision,
                                right_pre_topology_revision=right_boundary.pre_topology_revision,
                                left_post_topology_revision=left_boundary.post_topology_revision,
                                right_post_topology_revision=right_boundary.post_topology_revision,
                                signals=boundary_signals,
                            ))
                if not diagnostics:
                    for signal in sorted(left_transient.signals, key=lambda item: item.signal_id):
                        other = right_signals[signal.signal_id]
                        deltas.append(TransientSignalDeltaReadModel(
                            signal_id=signal.signal_id,
                            unit=signal.unit,
                            left_values=signal.values,
                            right_values=other.values,
                            deltas=tuple(b - a for a, b in zip(signal.values, other.values)),
                        ))
                    right_by_key = {event_key(event): event for event in right_transient.events}
                    for event in left_transient.events:
                        key = event_key(event)
                        transition = event.transition
                        peer = right_by_key[key]
                        event_matches.append(TransientSemanticEventMatchReadModel(
                            semantic_key=key,
                            left_event_id=event.event_id,
                            right_event_id=peer.event_id,
                            event_time_s=event.scheduled_time,
                            left_dispatch_time_s=event.dispatch_time,
                            right_dispatch_time_s=peer.dispatch_time,
                            dispatch_delta_s=(
                                None if event.dispatch_time is None or peer.dispatch_time is None
                                else peer.dispatch_time - event.dispatch_time
                            ),
                            transition_type=str(transition["transition_type"]),
                            target_kind=str(transition["target_kind"]),
                            target_id=str(transition["target_id"]),
                            previous_state=transition.get("previous_state"),
                            new_state=transition.get("new_state"),
                        ))
            compatible_transient = not diagnostics
            return StudyResultComparisonReadModel(
                left=left,
                right=right,
                compatible=compatible_transient,
                compatibility_message=(
                    "Transient trajectories are compatible on the exact recorded time grid."
                    if compatible_transient else
                    "Transient trajectory comparison rejected: " + "; ".join(diagnostics)
                ),
                same_source_revision=left.source_revision == right.source_revision,
                same_activation_generation=left.activation_generation == right.activation_generation,
                numeric_deltas=(),
                numeric_comparison_status="TRANSIENT_COMPARABLE" if compatible_transient else "TRANSIENT_INCOMPATIBLE",
                transient_signal_deltas=tuple(deltas),
                transient_compatibility_diagnostics=tuple(diagnostics),
                transient_event_matches=tuple(event_matches),
                transient_discontinuity_deltas=tuple(discontinuity_deltas),
            )
        compatible = left.project_id == right.project_id and left.study_type == right.study_type
        if left.project_id != right.project_id:
            message = "Comparison rejected: records belong to different projects."
            compatible = False
        elif left.study_type != right.study_type:
            message = "Comparison rejected: study types differ."
            compatible = False
        else:
            message = "Same project and study type; compare values with source-revision and activation provenance shown."
        numeric_deltas: list[StudyResultNumericDeltaReadModel] = []
        if compatible:
            left_values = _study_result_numeric_observations(left.value)
            right_values = _study_result_numeric_observations(right.value)
            for path in sorted(set(left_values).intersection(right_values)):
                left_value, left_unit, left_base = left_values[path]
                right_value, right_unit, right_base = right_values[path]
                if left_unit != right_unit or left_base != right_base:
                    continue
                finite = all(
                    math.isfinite(part)
                    for value in (left_value, right_value)
                    for part in ((value.real, value.imag) if isinstance(value, complex) else (float(value),))
                )
                if not finite:
                    continue
                numeric_deltas.append(StudyResultNumericDeltaReadModel(
                    path=path, left_value=left_value, right_value=right_value,
                    delta=right_value - left_value, unit=left_unit, base=left_base,
                ))
        return StudyResultComparisonReadModel(
            left=left,
            right=right,
            compatible=compatible,
            compatibility_message=message,
            same_source_revision=left.source_revision == right.source_revision,
            same_activation_generation=left.activation_generation == right.activation_generation,
            numeric_deltas=tuple(numeric_deltas),
            numeric_comparison_status="COMPARABLE" if numeric_deltas else "NO_BASE_COMPATIBLE_VALUES",
        )

    def compare_archived_results(
        self, left_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel,
        right_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel, *,
        project_id: str, activation_generation: int | None = None,
        current_revision: ProjectRevision | None = None,
    ) -> StudyResultComparisonReadModel:
        """Compatibility alias for the unified live/archive comparison contract."""
        return self.compare_study_results(
            left_record_id, right_record_id, project_id=project_id,
            activation_generation=activation_generation, current_revision=current_revision,
        )

    def export_study_comparison(
        self, left_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel,
        right_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel, *,
        project_id: str, activation_generation: int | None = None,
        current_revision: ProjectRevision | None = None,
    ) -> str:
        """Return a versioned lossless export for two live and/or archived transient runs."""
        comparison = self.compare_study_results(
            left_record_id, right_record_id, project_id=project_id,
            activation_generation=activation_generation, current_revision=current_revision,
        )
        left, right = comparison.left, comparison.right
        transient_name = (
            f"{TransientStabilityResult.__module__}."
            f"{TransientStabilityResult.__qualname__}"
        )

        def is_transient(value: Any) -> bool:
            return (
                isinstance(value, TransientStabilityResult)
                or (isinstance(value, Mapping) and value.get("$dataclass") == transient_name)
                or (
                    isinstance(value, Mapping)
                    and value.get("$type") == "solver_result"
                    and value.get("class") == transient_name
                )
            )

        if not (is_transient(left.value) or is_transient(right.value)):
            raise ValueError("Comparison does not contain a transient-stability result.")

        def encode(value: Any, _active: set[int] | None = None) -> Any:
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if _active is None:
                _active = set()

            def encode_compound(builder: Any) -> Any:
                identity = id(value)
                if identity in _active:
                    raise ValueError("Transient comparison export cannot encode cyclic values.")
                _active.add(identity)
                try:
                    return builder()
                finally:
                    _active.remove(identity)

            if isinstance(value, float):
                if math.isfinite(value):
                    return value
                label = "nan" if math.isnan(value) else (
                    "positive_infinity" if value > 0 else "negative_infinity"
                )
                return {"$type": "float", "value": label}
            if isinstance(value, complex):
                if not math.isfinite(value.real) or not math.isfinite(value.imag):
                    raise ValueError("Transient comparison export contains a non-finite complex value.")
                return {"$type": "complex", "real": value.real, "imag": value.imag}
            if isinstance(value, UUID):
                return {"$type": "uuid", "value": str(value)}
            if isinstance(value, Enum):
                if type(value) not in (CTPolarity, CVTPolarity):
                    raise ValueError(
                        "Transient comparison export cannot encode unregistered enum "
                        f"{type(value).__module__}.{type(value).__qualname__}."
                    )
                return {
                    "$type": "enum",
                    "class": f"{type(value).__module__}.{type(value).__qualname__}",
                    "value": encode(value.value, _active),
                }
            if isinstance(value, Mapping):
                if any(not isinstance(key, str) for key in value):
                    raise ValueError("Transient comparison export requires string mapping keys.")
                return encode_compound(
                    lambda: {key: encode(item, _active) for key, item in value.items()}
                )
            if isinstance(value, (tuple, list)):
                return encode_compound(lambda: [encode(item, _active) for item in value])
            if isinstance(value, (set, frozenset)):
                def encode_set() -> list[Any]:
                    encoded = [encode(item, _active) for item in value]
                    return sorted(encoded, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
                return encode_compound(encode_set)
            if hasattr(value, "dtype") and hasattr(value, "shape") and callable(getattr(value, "tolist", None)):
                dtype = value.dtype
                if getattr(dtype, "kind", None) not in "biufc" or getattr(dtype, "hasobject", True):
                    raise ValueError("Transient comparison export contains an unsupported array dtype.")
                return encode_compound(lambda: {
                    "$type": "ndarray",
                    "dtype": dtype.str,
                    "shape": list(value.shape),
                    "data": encode(value.tolist(), _active),
                })
            if hasattr(value, "item") and callable(value.item) and type(value).__module__.startswith("numpy"):
                return encode(value.item(), _active)
            if is_dataclass(value) and not isinstance(value, type):
                known_tags = {
                    TransientStabilityResult: "solver_result",
                    EventExecution: "event_execution",
                    EventTransition: "event_transition",
                    TransientDiscontinuity: "transient_discontinuity",
                    AlgebraicNetworkSolution: "algebraic_network_solution",
                    ProjectRevision: "project_revision",
                    StudyResultNumericDeltaReadModel: "numeric_delta",
                    TransientDiscontinuitySignalDeltaReadModel: "discontinuity_signal_delta",
                    TransientDiscontinuityDeltaReadModel: "discontinuity_delta",
                    TransientSemanticEventMatchReadModel: "semantic_event_match",
                    TransientSignalDeltaReadModel: "signal_delta",
                }
                value_type = type(value)
                tag = known_tags.get(value_type)
                if tag is None:
                    raise ValueError(
                        "Transient comparison export cannot encode unregistered dataclass "
                        f"{value_type.__module__}.{value_type.__qualname__}."
                    )
                qualified = f"{value_type.__module__}.{value_type.__qualname__}"
                return encode_compound(lambda: {
                    "$type": tag,
                    "class": qualified,
                    "fields": {item.name: encode(getattr(value, item.name), _active) for item in fields(value)},
                })
            raise ValueError(
                f"Transient comparison export cannot encode {type(value).__module__}.{type(value).__qualname__}."
            )

        def record_payload(record: Any) -> dict[str, Any]:
            return {
                "source_kind": "archive" if isinstance(record, ArchivedStudyResultReadModel) else "live",
                "record_id": None if getattr(record, "record_id", None) is None else str(record.record_id),
                "saved_at": getattr(record, "saved_at", None),
                "study_id": str(record.study_id),
                "project_id": record.project_id,
                "activation_generation": record.activation_generation,
                "source_revision": encode(record.source_revision),
                "study_type": record.study_type,
                "status": record.status,
                "message": record.message,
                "metadata": encode(record.metadata),
                "value": encode(record.value),
            }

        payload = {
            "format": "GridForgeTransientComparison",
            "schema_version": 1,
            "left": record_payload(left),
            "right": record_payload(right),
            "comparison": {
                "compatible": comparison.compatible,
                "compatibility_message": comparison.compatibility_message,
                "same_source_revision": comparison.same_source_revision,
                "same_activation_generation": comparison.same_activation_generation,
                "numeric_comparison_status": comparison.numeric_comparison_status,
                "transient_compatibility_diagnostics": encode(comparison.transient_compatibility_diagnostics),
                "transient_signal_deltas": encode(comparison.transient_signal_deltas),
                "transient_event_matches": encode(comparison.transient_event_matches),
                "transient_discontinuity_deltas": encode(comparison.transient_discontinuity_deltas),
                "numeric_deltas": encode(comparison.numeric_deltas),
            },
        }
        try:
            return json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Transient comparison export is not canonical JSON: {exc}") from exc

    def export_archived_comparison(
        self, left_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel,
        right_record_id: UUID | StudyResultReadModel | ArchivedStudyResultReadModel, *,
        project_id: str, activation_generation: int | None = None,
        current_revision: ProjectRevision | None = None,
    ) -> str:
        """Compatibility alias for the unified live/archive comparison export."""
        return self.export_study_comparison(
            left_record_id, right_record_id, project_id=project_id,
            activation_generation=activation_generation, current_revision=current_revision,
        )

    def verify_archived_results(self, *, project_id: str, activation_generation: int | None = None) -> Any:
        if self._archive is None or not callable(getattr(self._archive, "verify", None)):
            raise RuntimeError("Study Result archive verification is unavailable.")
        return self._archive.verify(project_id, activation_generation=activation_generation)

    def snapshot(self, *, project_id: str, activation_generation: int, current_revision: ProjectRevision | None = None) -> tuple[StudyResultReadModel, ...]:
        return self.results(
            project_id=project_id,
            activation_generation=activation_generation,
            current_revision=current_revision,
        )




class MeasurementChannelReadService:
    """Detached read boundary over the Application-owned channel lifecycle service."""

    def __init__(self, measurement_service: Any) -> None:
        if measurement_service is None or not callable(getattr(measurement_service, "get", None)):
            raise TypeError("measurement_service must provide get(channel_id).")
        self._measurement_service = measurement_service

    def channel(self, channel_id: str) -> MeasurementChannelReadModel:
        normalized = str(channel_id).strip()
        if not normalized:
            raise ValueError("channel_id must be non-empty.")
        channel = self._measurement_service.get(normalized)
        if channel is None:
            raise KeyError(f"MeasurementChannel '{normalized}' is not available.")
        validity = channel.validity()
        return MeasurementChannelReadModel(
            channel_id=str(channel.id),
            value=channel.engineering_value,
            unit=str(channel.unit),
            signal_type=channel.signal_type.value,
            phase=channel.phase.value,
            quality=channel.quality.value,
            available=bool(channel.available),
            timestamp=channel.timestamp,
            timestamp_domain=channel.timestamp_domain,
            sample_sequence=channel.sample_sequence,
            stale_after=channel.stale_after,
            producer_id=getattr(channel, "producer_id", None),
            validity=validity.value,
        )

    def channels(self) -> tuple[MeasurementChannelReadModel, ...]:
        registry = getattr(self._measurement_service, "channels", {})
        return tuple(self.channel(channel_id) for channel_id in sorted(registry))

__all__ = [
    "ArchivedStudyResultReadModel",
    "StudyReadService",
    "StudyResultComparisonReadModel",
    "StudyResultNumericDeltaReadModel",
    "NetworkReadService",
    "ProtectionReadService",
    "MeasurementChannelReadService",
    "ReadService",
]
