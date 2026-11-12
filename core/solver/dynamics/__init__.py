"""
GridForge Dynamic Solver
========================

Public package exports for the current dynamic solver implementation.

Author: Subhendu Mishra
"""

from .integrator import Integrator, RK4Integrator, TrapezoidalIntegrator
from .swing_equation import SwingEquation
from .machine_models import (
    ClassicalMachineParameters,
    ClassicalSynchronousMachine,
    MachineElectricalOutput,
)
from .multimachine import MultiMachineSystem
from .derivative_assembly import (
    DynamicDerivativeAssemblyError,
    DynamicSystemDerivativeAssembly,
    MachineDerivativeEvaluator,
    evaluate_composed_dynamic_derivatives,
)
from .component_assembly import (
    DynamicComponentAssemblyError,
    DynamicComponentBinding,
    DynamicComponentBatchEvaluation,
    evaluate_dynamic_components,
    aggregate_dynamic_terminal_injections,
)
from .dynamic_state_layout import (
    DynamicSystemLayoutError,
    DynamicStateBlockSlice,
    DynamicSystemStateLayout,
)
from .component_contract import (
    DynamicComponentContractError,
    DynamicComponentInputs,
    DynamicComponentEvaluation,
    DynamicTerminalInjection,
    DynamicStateComponent,
    validate_dynamic_state_component,
)
from .network_coupling import (
    DynamicNetworkCouplingError,
    DynamicNetworkSolveRequest,
    DynamicNetworkSolver,
    validate_dynamic_network_solution,
    DynamicNetworkStageResult,
    TerminalInjectionEvaluator,
    solve_dynamic_network_stage,
)
from .events import EventBatchConflictError, EventExecution, EventManager, EventOperation, EventTransition, SimulationEvent
from .dae_solver import (
    AlgebraicNetworkSolution,
    NetworkSolver,
    MechanicalPowerMap,
    DAESolverError,
    DAEConfigurationError,
    DAEAlgebraicError,
    DAENumericalError,
    DAESolution,
    DAESolver,
)
from .transient_stability import (
    TransientStabilityError,
    TransientStabilityResult,
    TransientStabilitySolver,
)

__all__ = [
    "Integrator",
    "RK4Integrator",
    "TrapezoidalIntegrator",
    "SwingEquation",
    "ClassicalMachineParameters",
    "ClassicalSynchronousMachine",
    "MachineElectricalOutput",
    "MultiMachineSystem",
    "DynamicComponentContractError",
    "DynamicComponentInputs",
    "DynamicComponentEvaluation",
    "DynamicTerminalInjection",
    "DynamicStateComponent",
    "validate_dynamic_state_component",
    "DynamicSystemLayoutError",
    "DynamicStateBlockSlice",
    "DynamicSystemStateLayout",
    "DynamicComponentAssemblyError",
    "DynamicComponentBinding",
    "DynamicComponentBatchEvaluation",
    "evaluate_dynamic_components",
    "aggregate_dynamic_terminal_injections",
    "DynamicDerivativeAssemblyError",
    "DynamicSystemDerivativeAssembly",
    "MachineDerivativeEvaluator",
    "evaluate_composed_dynamic_derivatives",
    "SimulationEvent",
    "EventOperation",
    "EventBatchConflictError",
    "EventExecution",
    "EventTransition",
    "EventManager",
    "DynamicNetworkCouplingError",
    "DynamicNetworkSolveRequest",
    "DynamicNetworkSolver",
    "validate_dynamic_network_solution",
    "DynamicNetworkStageResult",
    "TerminalInjectionEvaluator",
    "solve_dynamic_network_stage",
    "AlgebraicNetworkSolution",
    "NetworkSolver",
    "MechanicalPowerMap",
    "DAESolverError",
    "DAEConfigurationError",
    "DAEAlgebraicError",
    "DAENumericalError",
    "DAESolution",
    "DAESolver",
    "TransientStabilityError",
    "TransientStabilityResult",
    "TransientDiscontinuity",
    "TransientStabilitySolver",
]
