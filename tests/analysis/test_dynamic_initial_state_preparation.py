import math

import pytest

from core.analysis.dynamic_initial_state import DynamicInitialStatePreparation, DynamicMachineModelDefinition
from core.solver.dynamics.machine_models import ClassicalMachineParameters
from core.solver.power_flow.input import PowerFlowBusType, PowerFlowInput
from core.solver.power_flow.result import PowerFlowResult


def test_power_flow_result_prepares_dynamic_initial_state():
    result = PowerFlowResult(
        success=True,
        iterations=3,
        error=1e-10,
        pv_to_pq=(),
        history=(1.0, 1e-4, 1e-10),
        message="converged",
        voltage_magnitudes=(1.0, 0.98),
        voltage_angles=(0.1, -0.02),
    )
    power_flow_input = PowerFlowInput(
        bus_ids=("B1", "B2"),
        bus_types=(PowerFlowBusType.SLACK, PowerFlowBusType.PQ),
        p_spec=(0.8, -0.3),
        q_spec=(0.1, -0.1),
        q_min=(None, None),
        q_max=(None, None),
        initial_vm=(1.0, 1.0),
        initial_va=(0.0, 0.0),
    )
    machine = DynamicMachineModelDefinition(
        machine_id="G1",
        bus_id="B1",
        parameters=ClassicalMachineParameters(H=3.0, Xd_prime=0.3, Efd=1.1),
        mechanical_power=0.8,
    )

    prepared = DynamicInitialStatePreparation.prepare(result, power_flow_input, machine)

    expected_voltage = complex(math.cos(0.1), math.sin(0.1))
    assert prepared.terminal_voltage == pytest.approx(expected_voltage)
    assert prepared.state_vector[1] == 0.0
    assert prepared.machine_id == "G1"
