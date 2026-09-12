import numpy as np

from core.solver.dynamics import DAESolver, Integrator, MultiMachineSystem, TransientStabilitySolver
from core.solver.dynamics.machine_models import ClassicalSynchronousMachine


def test_event_inside_step_is_applied_at_exact_boundary_before_recording_voltage():
    machine = ClassicalSynchronousMachine("G1", "B1", H=3.0, Xd_prime=0.3, Efd=1.1)
    system = MultiMachineSystem((machine,))
    applied = {"fault": False}
    observed_times = []

    def network_solver(state, time):
        del state
        observed_times.append(time)
        return {"B1": 0.5 + 0.0j if applied["fault"] else 1.0 + 0.0j}

    dae = DAESolver(
        system,
        network_solver,
        {"G1": 0.0},
        integrator=Integrator("RK4"),
    )
    solver = TransientStabilitySolver(dae, start_time=0.0, end_time=0.25, dt=0.1)
    solver.event_manager.add(
        0.15,
        lambda: applied.__setitem__("fault", True),
        event_id="fault-on",
        event_type="fault_apply",
    )
    solver.initialize(np.array([0.0, 0.0]), time=0.0)

    result = solver.run()

    assert np.any(np.isclose(result.time, 0.15))
    boundary_index = int(np.where(np.isclose(result.time, 0.15))[0][0])
    assert result.terminal_voltages[boundary_index]["B1"] == 0.5 + 0.0j
    assert any(np.isclose(time, 0.15) for time in observed_times)
    assert result.events[0].event_id == "fault-on"
