import numpy as np
from scipy.sparse import csr_matrix

from core.analysis.dynamic_model_association import DynamicMachineModelAssociation
from core.analysis.power_flow_preparation import PreparedBranch, PreparedPowerFlow
from core.analysis.transient_stability import TransientStabilityStudyConfiguration
from core.application.bootstrap import create_application
from core.application.study import StudyRequest
from core.network import Network
from core.numerical.ybus import YBus, YBusBuilder
from core.solver.dynamics.machine_models import ClassicalMachineParameters
from core.solver.power_flow.input import PowerFlowBusType, PowerFlowInput
from core.solver.power_flow.result import PowerFlowResult


def test_application_executes_transient_stability_from_detached_pf_snapshot():
    bus_ids = ("B1", "B2")
    power_flow_input = PowerFlowInput(
        bus_ids=bus_ids,
        bus_types=(PowerFlowBusType.SLACK, PowerFlowBusType.PQ),
        p_spec=(0.8, -0.3),
        q_spec=(0.1, -0.1),
        q_min=(None, None),
        q_max=(None, None),
        initial_vm=(1.0, 1.0),
        initial_va=(0.0, 0.0),
    )
    branch = PreparedBranch("L1", "B1", "B2", 0.0, 0.2, 0.0)
    empty_ybus = YBus(csr_matrix((2, 2), dtype=np.complex128), bus_ids)
    snapshot = PreparedPowerFlow(
        input=power_flow_input,
        ybus=empty_ybus,
        base_mva=100.0,
        bus_voltage_bases={"B1": 11.0, "B2": 11.0},
        branches=(branch,),
    )
    snapshot = PreparedPowerFlow(
        input=snapshot.input,
        ybus=YBusBuilder().build(snapshot),
        base_mva=snapshot.base_mva,
        bus_voltage_bases=snapshot.bus_voltage_bases,
        branches=snapshot.branches,
    )
    power_flow_result = PowerFlowResult(
        success=True,
        iterations=2,
        error=1e-10,
        pv_to_pq=(),
        history=(1.0, 1e-10),
        message="converged",
        voltage_magnitudes=(1.0, 0.98),
        voltage_angles=(0.1, -0.02),
    )

    application = create_application(Network())
    application.dynamic_models.bind(DynamicMachineModelAssociation(
        machine_id="G1",
        bus_id="B1",
        model_type="classical",
        parameters=ClassicalMachineParameters(H=3.0, Xd_prime=0.3, Efd=1.1),
        mechanical_power=0.0,
    ))
    request = StudyRequest(
        study_type="transient_stability",
        configuration={
            "configuration": TransientStabilityStudyConfiguration(end_time=0.02, dt=0.01),
            "prepared_power_flow": snapshot,
            "power_flow_result": power_flow_result,
        },
    )

    result = application.execute_study(request)

    assert result.status == "completed"
    assert result.value.result.number_of_steps >= 1
