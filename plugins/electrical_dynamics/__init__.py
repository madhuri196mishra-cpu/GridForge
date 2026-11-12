"""Shared interoperability contracts for GridForge electrical dynamics plugins."""
from .composition import ElectricalDynamicsComposition, require_electrical_dynamics_composition
from core.solver.dynamics.component_contract import (
    DynamicComponentInputs, DynamicComponentEvaluation, DynamicStateComponent,
    validate_dynamic_state_component,
)
from .contracts import (
    DynamicsEquipmentBinding,
    DynamicsEquipmentKind,
    DynamicsModelCapability,
    DynamicsParticipant,
    DynamicsModelReadiness,
    MeasurementPointContract,
    equipment_binding_from_endpoint,
    normalize_measurement_points,
    participant_from_endpoint,
    validate_unique_equipment_participants,
)

__all__ = [
    "ElectricalDynamicsComposition", "require_electrical_dynamics_composition",
    "DynamicComponentInputs", "DynamicComponentEvaluation", "DynamicStateComponent",
    "validate_dynamic_state_component",
    "DynamicsEquipmentBinding", "DynamicsEquipmentKind",
    "DynamicsModelCapability", "DynamicsModelReadiness", "DynamicsParticipant",
    "MeasurementPointContract", "equipment_binding_from_endpoint",
    "normalize_measurement_points", "participant_from_endpoint",
    "validate_unique_equipment_participants",
]
