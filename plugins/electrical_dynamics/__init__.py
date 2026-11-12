"""Shared interoperability contracts for GridForge electrical dynamics plugins."""
from .composition import ElectricalDynamicsComposition, require_electrical_dynamics_composition
from core.solver.dynamics.dynamic_state_layout import (
    DynamicSystemLayoutError, DynamicStateBlockSlice, DynamicSystemStateLayout,
)
from core.solver.dynamics.component_assembly import (
    DynamicComponentAssemblyError, DynamicComponentBinding,
    DynamicComponentBatchEvaluation, evaluate_dynamic_components,
)
from core.solver.dynamics.derivative_assembly import (
    DynamicDerivativeAssemblyError, DynamicSystemDerivativeAssembly,
    MachineDerivativeEvaluator, evaluate_composed_dynamic_derivatives,
)
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
    "DynamicDerivativeAssemblyError", "DynamicSystemDerivativeAssembly",
    "MachineDerivativeEvaluator", "evaluate_composed_dynamic_derivatives",
    "DynamicComponentAssemblyError", "DynamicComponentBinding",
    "DynamicComponentBatchEvaluation", "evaluate_dynamic_components",
    "DynamicSystemLayoutError", "DynamicStateBlockSlice", "DynamicSystemStateLayout",
    "DynamicsEquipmentBinding", "DynamicsEquipmentKind",
    "DynamicsModelCapability", "DynamicsModelReadiness", "DynamicsParticipant",
    "MeasurementPointContract", "equipment_binding_from_endpoint",
    "normalize_measurement_points", "participant_from_endpoint",
    "validate_unique_equipment_participants",
]
