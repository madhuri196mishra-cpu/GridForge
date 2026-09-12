import numpy as np

from core.analysis.power_flow_preparation import PreparedBranch, PreparedShunt
from core.analysis.transient_network import DetachedTransientNetworkState, TransientNetworkSolver
from core.solver.dynamics.machine_models import ClassicalSynchronousMachine
from core.solver.dynamics.multimachine import MultiMachineSystem


def test_machine_algebraic_coupling_uses_detached_passive_network():
    state = DetachedTransientNetworkState(
        bus_ids=("B1", "B2"),
        branches=(PreparedBranch("L1", "B1", "B2", 0.0, 0.2, 0.0),),
        transformers=(),
        shunts=(PreparedShunt("S1", "B2", 0.0, 0.0),),
        breaker_states={},
        equipment_states={"L1": True},
        topology_revision=7,
    )
    machine = ClassicalSynchronousMachine(
        "G1", "B1", H=3.0, Xd_prime=0.3, Efd=1.1
    )
    system = MultiMachineSystem((machine,))
    solver = TransientNetworkSolver(state, system)

    voltages = solver.solve(np.array([0.0, 0.0]), 0.0)

    assert set(voltages) == {"B1", "B2"}
    assert np.isfinite(voltages["B1"].real)
    assert np.isfinite(voltages["B2"].real)
    assert state.bus_ids == ("B1", "B2")
