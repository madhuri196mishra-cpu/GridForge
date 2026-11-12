"""Headless induction-motor electromechanical and lumped-thermal transient solver.

CPU/NumPy is the numerical reference. Optional Numba-CUDA batch execution maps
independent motor trajectories to GPU threads; it never imports Qt or mutates
GridForge Core/Application state. NPU execution is not selected for this
state-recurrent integration kernel: no compatible general-purpose NPU runtime
contract is currently defined for this numerical workload.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from core.numerics.backend import BackendCapability, select_backend
from core.numerics.ode import NumericalProblem, solve_numerical_problem, uniform_time_grid

_STATE_COUNT = 6
_PSI_DS, _PSI_QS, _PSI_DR, _PSI_QR, _OMEGA_M, _TEMP_C = range(_STATE_COUNT)
_STATE_NAMES = (
    "psi_stator_d_wb",
    "psi_stator_q_wb",
    "psi_rotor_d_wb",
    "psi_rotor_q_wb",
    "rotor_speed_rad_s",
    "winding_temperature_c",
)


@dataclass(frozen=True)
class MotorParameters:
    """SI machine parameters for a balanced, squirrel-cage induction motor.

    Inductances are per-phase equivalent values. The model uses a synchronous
    dq reference frame, constant load torque, linear magnetic parameters, and
    one lumped winding thermal state. Saturation, skin effect, unbalance,
    converter switching, and separate stator/rotor thermal nodes are outside
    this model's present fidelity boundary.
    """

    stator_resistance_ohm: float
    rotor_resistance_ohm: float
    stator_leakage_inductance_h: float
    rotor_leakage_inductance_h: float
    magnetizing_inductance_h: float
    pole_pairs: int
    inertia_kg_m2: float
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 35.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_temperature_c: float = 25.0
    external_rotor_resistance_ohm: float = 0.0

    def __post_init__(self) -> None:
        positive = (
            "stator_resistance_ohm", "rotor_resistance_ohm",
            "stator_leakage_inductance_h", "rotor_leakage_inductance_h",
            "magnetizing_inductance_h", "inertia_kg_m2",
            "thermal_capacity_j_k",
        )
        for name in positive:
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if not _is_numeric_scalar(self.external_rotor_resistance_ohm) or not isfinite(float(self.external_rotor_resistance_ohm)) or self.external_rotor_resistance_ohm < 0.0:
            raise ValueError("external_rotor_resistance_ohm must be finite and non-negative")
        for name in (
            "friction_n_m_s", "thermal_conductance_w_k",
            "resistance_temp_coefficient_per_k", "ambient_temperature_c",
            "initial_speed_rad_s", "initial_temperature_c",
        ):
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)) or (name in ("friction_n_m_s", "thermal_conductance_w_k") and value < 0):
                raise ValueError(f"{name} must be finite and non-negative" if name in ("friction_n_m_s", "thermal_conductance_w_k") else f"{name} must be finite")
        if isinstance(self.pole_pairs, (bool, np.bool_)) or not isinstance(self.pole_pairs, (int, np.integer)) or self.pole_pairs < 1:
            raise ValueError("pole_pairs must be a positive integer")
        determinant = (
            (self.stator_leakage_inductance_h + self.magnetizing_inductance_h)
            * (self.rotor_leakage_inductance_h + self.magnetizing_inductance_h)
            - self.magnetizing_inductance_h ** 2
        )
        if determinant <= 0.0 or not isfinite(determinant):
            raise ValueError("machine inductance matrix must be positive and nonsingular")


@dataclass(frozen=True)
class MotorSimulationResult:
    """Immutable result metadata around a flat, contiguous state buffer."""

    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.diagnostics, Mapping):
            raise TypeError("diagnostics must be a mapping")
        # Frozen dataclasses do not freeze nested mappings; copy and expose a
        # read-only outer mapping so consumers cannot rewrite result metadata.
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self) -> int:
        return int(self.time_s.size)

    @property
    def state_names(self) -> tuple[str, ...]:
        return _STATE_NAMES

    def state(self, name: str) -> np.ndarray:
        """Return a read-only state channel view."""
        try:
            index = _STATE_NAMES.index(name)
        except ValueError as exc:
            raise KeyError(name) from exc
        matrix = self.state_data.reshape(self.sample_count, _STATE_COUNT)
        view = matrix[:, index].view()
        view.setflags(write=False)
        return view

    def publish_to(
        self,
        read_model_store: Any,
        parameters: MotorParameters,
        electrical_frequency_hz: float,
        load_torque_nm: float,
        *,
        study_id: str | None = None,
        study_order: int | None = None,
    ) -> bool:
        """Publish a computed snapshot; caller owns StudyCompleted event timing.

        This method never dispatches UI events or mutates Core. The caller
        should emit the canonical StudyCompleted event only after its study
        orchestration commits successfully.
        """
        publish = getattr(read_model_store, "publish", None)
        if not callable(publish):
            raise TypeError("read_model_store must expose publish(...)")
        if (
            not _is_numeric_scalar(electrical_frequency_hz)
            or not isfinite(float(electrical_frequency_hz))
            or electrical_frequency_hz < 0
        ):
            raise ValueError("electrical_frequency_hz must be finite and non-negative")
        if not _is_numeric_scalar(load_torque_nm) or not isfinite(float(load_torque_nm)):
            raise ValueError("load_torque_nm must be finite and numeric")
        return publish(
            self.motor_id,
            self.channels(
                parameters,
                2.0 * np.pi * float(electrical_frequency_hz),
                float(load_torque_nm),
            ),
            self.sample_period_s,
            status="completed",
            study_id=study_id,
            study_order=study_order,
            diagnostics=self.diagnostics,
        )

    def channels(self, parameters: MotorParameters, electrical_rad_s: float,
                 load_torque_nm: float) -> dict[str, np.ndarray]:
        """Derive read-model channels from the integrated state trajectory."""
        matrix = self.state_data.reshape(self.sample_count, _STATE_COUNT)
        ps, qs, pr, qr, speed, temp = (matrix[:, i] for i in range(_STATE_COUNT))
        lm = parameters.magnetizing_inductance_h
        ls = parameters.stator_leakage_inductance_h + lm
        lr = parameters.rotor_leakage_inductance_h + lm
        det = ls * lr - lm * lm
        ids = (lr * ps - lm * pr) / det
        iqs = (lr * qs - lm * qr) / det
        idr = (ls * pr - lm * ps) / det
        iqr = (ls * qr - lm * qs) / det
        torque = 1.5 * parameters.pole_pairs * (ps * iqs - qs * ids)
        sync_mech = electrical_rad_s / parameters.pole_pairs
        slip_hz = (electrical_rad_s - parameters.pole_pairs * speed) / (2.0 * np.pi)
        values = {
            "stator_current_a": np.hypot(ids, iqs),
            "stator_current_d_a": ids,
            "stator_current_q_a": iqs,
            "rotor_current_d_a": idr,
            "rotor_current_q_a": iqr,
            "rotor_current_rms_a": np.hypot(idr, iqr),
            "external_rotor_resistance_ohm": np.full(self.sample_count, parameters.external_rotor_resistance_ohm),
            "rotor_copper_loss_w": 1.5 * (parameters.rotor_resistance_ohm + parameters.external_rotor_resistance_ohm) * (idr * idr + iqr * iqr),
            "electromagnetic_torque_nm": torque,
            "rotor_speed_rad_s": speed,
            "rotor_speed_rpm": speed * 60.0 / (2.0 * np.pi),
            "slip_hz": slip_hz,
            "winding_temperature_c": temp,
            "load_torque_nm": np.full(self.sample_count, float(load_torque_nm)),
            "synchronous_speed_rad_s": np.full(self.sample_count, sync_mech),
        }
        for value in values.values():
            value.setflags(write=False)
        return values


def _is_numeric_scalar(value: Any) -> bool:
    """Accept Python/NumPy real scalars while rejecting booleans and arrays."""
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
        value, (bool, np.bool_)
    )


def _validate_time(duration_s: float, step_s: float) -> tuple[int, np.ndarray]:
    """Use the shared numerical time-grid contract for all motor backends."""
    times, _effective_step = uniform_time_grid(duration_s, step_s)
    return int(times.size), times

def _voltage_profile(value: float | Sequence[float] | np.ndarray, count: int, name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float64)
    if arr.ndim == 0:
        arr = np.full(count, float(arr), dtype=np.float64)
    else:
        arr = arr.reshape(-1)
        if arr.size != count:
            raise ValueError(f"{name} profile must contain exactly {count} samples")
        arr = arr.copy()
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} profile must be finite")
    return arr


def _derivative(state: np.ndarray, p: MotorParameters, vds: float, vqs: float,
                omega_e: float, load_torque: float) -> np.ndarray:
    ps, qs, pr, qr, omega_m, temp = state
    lm = p.magnetizing_inductance_h
    ls = p.stator_leakage_inductance_h + lm
    lr = p.rotor_leakage_inductance_h + lm
    det = ls * lr - lm * lm
    ids, iqs = (lr * ps - lm * pr) / det, (lr * qs - lm * qr) / det
    idr, iqr = (ls * pr - lm * ps) / det, (ls * qr - lm * qs) / det
    temp_delta = temp - p.ambient_temperature_c
    rs = p.stator_resistance_ohm * max(0.05, 1.0 + p.resistance_temp_coefficient_per_k * temp_delta)
    rr = (p.rotor_resistance_ohm + p.external_rotor_resistance_ohm) * max(0.05, 1.0 + p.resistance_temp_coefficient_per_k * temp_delta)
    omega_slip = omega_e - p.pole_pairs * omega_m
    torque = 1.5 * p.pole_pairs * (ps * iqs - qs * ids)
    copper_loss = 1.5 * (rs * (ids * ids + iqs * iqs) + rr * (idr * idr + iqr * iqr))
    out = np.empty(_STATE_COUNT, dtype=np.float64)
    out[_PSI_DS] = vds - rs * ids + omega_e * qs
    out[_PSI_QS] = vqs - rs * iqs - omega_e * ps
    out[_PSI_DR] = -rr * idr + omega_slip * qr
    out[_PSI_QR] = -rr * iqr - omega_slip * pr
    out[_OMEGA_M] = (torque - load_torque - p.friction_n_m_s * omega_m) / p.inertia_kg_m2
    out[_TEMP_C] = (copper_loss - p.thermal_conductance_w_k * temp_delta) / p.thermal_capacity_j_k
    return out


def solve_transient_cpu(
    motor_id: str,
    parameters: MotorParameters,
    duration_s: float,
    step_s: float,
    *,
    stator_voltage_d_v: float | Sequence[float] | np.ndarray,
    stator_voltage_q_v: float | Sequence[float] | np.ndarray = 0.0,
    electrical_frequency_hz: float = 50.0,
    load_torque_nm: float = 0.0,
) -> MotorSimulationResult:
    """Integrate motor physics through GridForge's shared CPU RK4 engine.

    Voltage profiles are dq components in the synchronous reference frame.
    The shared integrator owns time stepping and RK4 stage mechanics; this
    plugin owns the induction-motor derivative and electrical inputs.
    """
    if not isinstance(motor_id, str) or not motor_id.strip():
        raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters, MotorParameters):
        raise TypeError("parameters must be MotorParameters")
    for name, value in (("electrical_frequency_hz", electrical_frequency_hz), ("load_torque_nm", load_torque_nm)):
        if not _is_numeric_scalar(value) or not isfinite(float(value)):
            raise ValueError(f"{name} must be finite and numeric")
    if electrical_frequency_hz < 0:
        raise ValueError("electrical_frequency_hz must be non-negative")
    count, times = _validate_time(duration_s, step_s)
    vd = _voltage_profile(stator_voltage_d_v, count, "stator_voltage_d_v")
    vq = _voltage_profile(stator_voltage_q_v, count, "stator_voltage_q_v")
    omega_e = 2.0 * np.pi * float(electrical_frequency_hz)
    initial = np.zeros(_STATE_COUNT, dtype=np.float64)
    initial[_OMEGA_M] = parameters.initial_speed_rad_s
    initial[_TEMP_C] = parameters.initial_temperature_c

    problem = NumericalProblem(
        problem_id=motor_id.strip(),
        workload="motor_transient_single",
        derivative=lambda state, voltage: _derivative(
            state, parameters, voltage[0], voltage[1], omega_e, float(load_torque_nm)
        ),
        initial_state=initial,
        duration_s=duration_s,
        maximum_step_s=step_s,
        input_at=lambda index, _time: (float(vd[index]), float(vq[index])),
        precision="float64",
    )
    solution = solve_numerical_problem(problem)
    flat = solution.state_data
    if solution.sample_count != count:
        raise RuntimeError("shared ODE engine returned an unexpected sample count")
    return MotorSimulationResult(
        motor_id=motor_id.strip(), time_s=solution.time_s, state_data=flat,
        sample_period_s=solution.sample_period_s, backend="cpu-rk4",
        diagnostics={
            "integrator": solution.integrator,
            "precision": "float64",
            "workload": "motor_transient_single",
            "state_layout": "flat_motor_major_step_major_state_minor",
            "state_count": _STATE_COUNT,
            "model": "induction_motor_dq_electromechanical_lumped_thermal",
            "rotor_circuit": "wound_rotor_with_external_resistance" if parameters.external_rotor_resistance_ohm > 0 else "squirrel_cage_equivalent",
            "external_rotor_resistance_ohm": parameters.external_rotor_resistance_ohm,
            "electrical_frequency_hz": float(electrical_frequency_hz),
            "load_torque_nm": float(load_torque_nm),
            "voltage_profile": "zero_order_hold",
            "requested_max_step_s": float(step_s),
            "effective_step_s": solution.sample_period_s,
            "integration_engine": "core.numerics.ode.solve_rk4",
        },
    )


def cuda_available() -> bool:
    """Return whether Numba can initialize a CUDA device; never require it."""
    try:
        from numba import cuda
        return bool(cuda.is_available())
    except Exception:
        return False



class _MotorCudaBatchAdapter:
    """Motor-specific adapter for the compatible constant-dq CUDA workload."""

    @property
    def capability(self) -> BackendCapability:
        return BackendCapability(
            name="numba-cuda",
            kind="gpu",
            available=cuda_available(),
            supported_workloads=frozenset({"motor_transient_batch"}),
            minimum_workload_size=8,
            precision_modes=frozenset({"float64"}),
        )

    def solve_batch(
        self,
        motor_ids: Sequence[str],
        parameter_sets: Sequence[MotorParameters],
        duration_s: float,
        step_s: float,
        voltage_d: float,
        voltage_q: float,
        electrical_frequency_hz: float,
        load_torques: Sequence[float],
    ) -> tuple[MotorSimulationResult, ...]:
        """Execute only the workload declared by this adapter's capability."""
        return _solve_batch_cuda(
            motor_ids, parameter_sets, duration_s, step_s, voltage_d, voltage_q,
            electrical_frequency_hz, load_torques,
        )


def solve_transient_batch(
    motor_ids: Sequence[str],
    parameter_sets: Sequence[MotorParameters],
    duration_s: float,
    step_s: float,
    *,
    stator_voltage_d_v: Sequence[float],
    stator_voltage_q_v: Sequence[float],
    electrical_frequency_hz: float,
    load_torque_nm: Sequence[float],
    backend: str = "auto",
) -> tuple[MotorSimulationResult, ...]:
    """Solve independent motors through the shared backend selection policy.

    Runtime availability is supplied by the CPU/CUDA adapters. GPU capability
    is limited to constant-dq, float64 batch integration with at least eight
    independent motors. The CPU implementation remains the reference/fallback.
    """
    n = len(motor_ids)
    if n == 0 or len(parameter_sets) != n or len(load_torque_nm) != n:
        raise ValueError("motor_ids, parameter_sets and load_torque_nm must have the same non-zero length")
    # Validate the common integration grid before backend capability discovery
    # or accelerator execution. Invalid time inputs are caller errors, not GPU
    # failures that should be hidden behind CPU fallback.
    _validate_time(duration_s, step_s)
    if any(not isinstance(mid, str) or not mid.strip() for mid in motor_ids):
        raise ValueError("every motor_id must be a non-empty string")
    canonical_ids = tuple(mid.strip() for mid in motor_ids)
    if len(set(canonical_ids)) != len(canonical_ids):
        raise ValueError("motor_ids must be unique within a transient batch")
    if any(not isinstance(p, MotorParameters) for p in parameter_sets):
        raise TypeError("every parameter_sets entry must be MotorParameters")
    if backend not in ("auto", "cpu", "cuda"):
        raise ValueError("backend must be 'auto', 'cpu', or 'cuda'")
    if backend == "cuda" and n < 8:
        raise ValueError(
            "backend='cuda' requires at least 8 independent motors; "
            "use backend='auto' or 'cpu' for smaller batches."
        )
    vd = np.asarray(stator_voltage_d_v, dtype=np.float64)
    vq = np.asarray(stator_voltage_q_v, dtype=np.float64)
    count, _times = _validate_time(duration_s, step_s)
    for name, profile in (("stator_voltage_d_v", vd), ("stator_voltage_q_v", vq)):
        if profile.ndim not in (0, 1):
            raise ValueError(f"{name} must be a scalar or one-dimensional time profile")
        if profile.ndim == 1 and profile.size != count:
            raise ValueError(f"{name} profile must contain exactly {count} samples")
        if not np.isfinite(profile).all():
            raise ValueError(f"{name} must contain only finite values")
    constant_dq_voltage = vd.ndim == 0 and vq.ndim == 0
    if backend == "cuda" and not constant_dq_voltage:
        raise ValueError(
            "backend='cuda' requires constant scalar dq voltages; "
            "use backend='auto' or 'cpu' for time-varying voltage profiles"
        )
    voltage_d = float(vd) if vd.ndim == 0 else vd
    voltage_q = float(vq) if vq.ndim == 0 else vq
    if (
        not _is_numeric_scalar(electrical_frequency_hz)
        or not isfinite(float(electrical_frequency_hz))
        or electrical_frequency_hz < 0
    ):
        raise ValueError("electrical_frequency_hz must be finite, numeric and non-negative")
    for index, torque in enumerate(load_torque_nm):
        if not _is_numeric_scalar(torque) or not isfinite(float(torque)):
            raise ValueError(f"load_torque_nm[{index}] must be finite and numeric")

    # Capability facts come from the runtime adapters; the policy itself never
    # imports/probes accelerator libraries.
    cuda_adapter = _MotorCudaBatchAdapter()
    cpu_capability = BackendCapability(
        name="cpu-reference",
        kind="cpu",
        available=True,
        supported_workloads=frozenset({"motor_transient_batch"}),
        minimum_workload_size=1,
        precision_modes=frozenset({"float64"}),
    )
    # The CUDA kernel accepts constant dq inputs only. A varying voltage
    # profile is still a valid CPU batch workload; auto must select CPU rather
    # than rejecting the request or attempting an incompatible accelerator.
    capabilities = (
        (cpu_capability, cuda_adapter.capability)
        if constant_dq_voltage
        else (cpu_capability,)
    )
    requested = "gpu" if backend == "cuda" else backend
    selection = select_backend(
        capabilities,
        workload="motor_transient_batch",
        workload_size=n,
        precision="float64",
        requested=requested,
        preference=("gpu", "cpu", "npu"),
    )
    selection_diagnostics = {
        "requested_backend": backend,
        "selected_backend_kind": selection.selected.kind,
        "selected_backend_name": selection.selected.name,
        "backend_selection_reason": selection.reason,
        "backend_rejections": tuple(
            {"backend": name, "reason": reason}
            for name, reason in selection.rejected
        ),
    }

    if selection.selected.kind == "gpu":
        try:
            results = cuda_adapter.solve_batch(
                motor_ids, parameter_sets, duration_s, step_s, float(vd), float(vq),
                electrical_frequency_hz, load_torque_nm,
            )
            return tuple(_with_dispatch_diagnostics(r, selection_diagnostics) for r in results)
        except Exception as exc:
            # Accelerator execution failures are visible and fall back to CPU.
            failure = {"type": type(exc).__name__, "message": str(exc)[:512]}
            results = tuple(
                solve_transient_cpu(
                    mid, p, duration_s, step_s, stator_voltage_d_v=voltage_d,
                    stator_voltage_q_v=voltage_q, electrical_frequency_hz=electrical_frequency_hz,
                    load_torque_nm=float(torque),
                ) for mid, p, torque in zip(motor_ids, parameter_sets, load_torque_nm)
            )
            return tuple(
                _with_dispatch_diagnostics(
                    r, {
                        **selection_diagnostics,
                        "selected_backend_kind": "cpu",
                        "selected_backend_name": "cpu-reference",
                        "backend_selection_reason": "gpu_execution_failed_cpu_fallback",
                    },
                    failure=failure,
                )
                for r in results
            )

    results = tuple(
        solve_transient_cpu(
            mid, p, duration_s, step_s, stator_voltage_d_v=voltage_d,
            stator_voltage_q_v=voltage_q, electrical_frequency_hz=electrical_frequency_hz,
            load_torque_nm=float(torque),
        ) for mid, p, torque in zip(motor_ids, parameter_sets, load_torque_nm)
    )
    return tuple(_with_dispatch_diagnostics(r, selection_diagnostics) for r in results)


def _with_dispatch_diagnostics(
    result: MotorSimulationResult,
    selection: Mapping[str, Any],
    *,
    failure: Mapping[str, str] | None = None,
) -> MotorSimulationResult:
    """Return a result carrying requested/selected/executed backend details."""
    diagnostics = dict(result.diagnostics)
    diagnostics.update(selection)
    diagnostics["executed_backend"] = result.backend
    if failure is not None:
        diagnostics["accelerator_failure"] = dict(failure)
    return MotorSimulationResult(
        result.motor_id, result.time_s, result.state_data, result.sample_period_s,
        result.backend, diagnostics,
    )


def _solve_batch_cuda(
    motor_ids: Sequence[str], parameter_sets: Sequence[MotorParameters],
    duration_s: float, step_s: float, vds: float, vqs: float,
    electrical_frequency_hz: float, load_torques: Sequence[float],
) -> tuple[MotorSimulationResult, ...]:
    """Optional Numba CUDA implementation; one CUDA thread integrates one motor."""
    from numba import cuda

    count, times = _validate_time(duration_s, step_s)
    if not _is_numeric_scalar(electrical_frequency_hz) or not isfinite(float(electrical_frequency_hz)) or electrical_frequency_hz < 0:
        raise ValueError("electrical_frequency_hz must be finite, numeric and non-negative")
    omega_e = 2.0 * np.pi * float(electrical_frequency_hz)
    n = len(motor_ids)
    # Packed numeric contract: Rs,Rr,Lls,Llr,Lm,p,J,B,Cth,Gth,Tamb,alpha,
    # initial speed, initial temperature, load torque.
    packed = np.asarray([[
        p.stator_resistance_ohm, p.rotor_resistance_ohm,
        p.stator_leakage_inductance_h, p.rotor_leakage_inductance_h,
        p.magnetizing_inductance_h, p.pole_pairs, p.inertia_kg_m2,
        p.friction_n_m_s, p.thermal_capacity_j_k, p.thermal_conductance_w_k,
        p.ambient_temperature_c, p.resistance_temp_coefficient_per_k,
        p.initial_speed_rad_s, p.initial_temperature_c, float(t),
    ] for p, t in zip(parameter_sets, load_torques)], dtype=np.float64)
    output = np.zeros((n * count * _STATE_COUNT,), dtype=np.float64)
    dt = float(times[1] - times[0])

    @cuda.jit(device=True)
    def deriv(y, p, vd0, vq0, we, out):
        ps, qs, pr, qr, wm, temp = y[0], y[1], y[2], y[3], y[4], y[5]
        lm = p[4]
        ls, lr = p[2] + lm, p[3] + lm
        det = ls * lr - lm * lm
        ids, iqs = (lr * ps - lm * pr) / det, (lr * qs - lm * qr) / det
        idr, iqr = (ls * pr - lm * ps) / det, (ls * qr - lm * qs) / det
        td = temp - p[10]
        rs = p[0] * max(0.05, 1.0 + p[11] * td)
        rr = p[1] * max(0.05, 1.0 + p[11] * td)
        slip = we - p[5] * wm
        torque = 1.5 * p[5] * (ps * iqs - qs * ids)
        loss = 1.5 * (rs * (ids * ids + iqs * iqs) + rr * (idr * idr + iqr * iqr))
        out[0] = vd0 - rs * ids + we * qs
        out[1] = vq0 - rs * iqs - we * ps
        out[2] = -rr * idr + slip * qr
        out[3] = -rr * iqr - slip * pr
        out[4] = (torque - p[14] - p[7] * wm) / p[6]
        out[5] = (loss - p[9] * td) / p[8]

    @cuda.jit(device=True)
    def finite_state(values):
        for index in range(_STATE_COUNT):
            value = values[index]
            if not (value == value and abs(value) < 1.7976931348623157e308):
                return False
        return True

    @cuda.jit
    def integrate(params, result, status, motor_count, steps, h, vd0, vq0, we):
        m = cuda.grid(1)
        if m >= motor_count:
            return
        p = params[m]
        y = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        a = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        b = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        c = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        d = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        tmp = cuda.local.array(_STATE_COUNT, dtype=np.float64)
        for j in range(_STATE_COUNT):
            y[j] = 0.0
        y[4], y[5] = p[12], p[13]
        base = m * steps * _STATE_COUNT
        for j in range(_STATE_COUNT):
            result[base + j] = y[j]
        for step in range(steps - 1):
            deriv(y, p, vd0, vq0, we, a)
            if not finite_state(a):
                status[m] = 1
                result[base] = float('nan')
                return
            for j in range(_STATE_COUNT): tmp[j] = y[j] + 0.5 * h * a[j]
            if not finite_state(tmp):
                status[m] = 2
                result[base] = float('nan')
                return
            deriv(tmp, p, vd0, vq0, we, b)
            if not finite_state(b):
                status[m] = 3
                result[base] = float('nan')
                return
            for j in range(_STATE_COUNT): tmp[j] = y[j] + 0.5 * h * b[j]
            if not finite_state(tmp):
                status[m] = 4
                result[base] = float('nan')
                return
            deriv(tmp, p, vd0, vq0, we, c)
            if not finite_state(c):
                status[m] = 5
                result[base] = float('nan')
                return
            for j in range(_STATE_COUNT): tmp[j] = y[j] + h * c[j]
            if not finite_state(tmp):
                status[m] = 6
                result[base] = float('nan')
                return
            deriv(tmp, p, vd0, vq0, we, d)
            if not finite_state(d):
                status[m] = 7
                result[base] = float('nan')
                return
            for j in range(_STATE_COUNT):
                y[j] = y[j] + (h / 6.0) * (a[j] + 2.0*b[j] + 2.0*c[j] + d[j])
                result[base + (step + 1) * _STATE_COUNT + j] = y[j]
            if not finite_state(y):
                status[m] = 8
                result[base] = float('nan')
                return

    d_params = cuda.to_device(packed)
    d_output = cuda.to_device(output)
    d_status = cuda.to_device(np.zeros(n, dtype=np.int32))
    integrate[(n + 31) // 32, 32](
        d_params, d_output, d_status, n, count, dt, float(vds), float(vqs), omega_e
    )
    cuda.synchronize()
    status = d_status.copy_to_host()
    failed = np.flatnonzero(status)
    if failed.size:
        index = int(failed[0])
        raise FloatingPointError(
            f"CUDA motor transient failed for batch index {index} "
            f"(stage code {int(status[index])}); batch discarded before result creation"
        )
    states = d_output.copy_to_host().reshape(n, count, _STATE_COUNT)
    if not np.isfinite(states).all():
        raise FloatingPointError("CUDA motor transient produced non-finite states; batch discarded before result creation")
    results = []
    for i, (mid, p, torque) in enumerate(zip(motor_ids, parameter_sets, load_torques)):
        flat = np.ascontiguousarray(states[i].reshape(-1))
        flat.setflags(write=False)
        results.append(MotorSimulationResult(
            str(mid).strip(), times, flat, dt, "cuda-numba-rk4",
            {"integrator": "classical_rk4", "workload": "motor_transient_batch", "state_layout": "flat_motor_major_step_major_state_minor",
             "state_count": _STATE_COUNT, "model": "induction_motor_dq_electromechanical_lumped_thermal",
             "electrical_frequency_hz": float(electrical_frequency_hz), "load_torque_nm": float(torque),
             "voltage_profile": "constant_dq", "precision": "float64",
             "requested_max_step_s": float(step_s), "effective_step_s": dt},
        ))
    return tuple(results)


__all__ = [
    "MotorParameters", "MotorSimulationResult", "solve_transient_cpu",
    "solve_transient_batch", "cuda_available",
    "PMSMParameters", "PMSMSimulationResult", "solve_pmsm_transient_cpu",
    "BLDCParameters", "BLDCSimulationResult", "solve_bldc_transient_cpu",
    "SynRMParameters", "SynRMSimulationResult", "solve_synrm_transient_cpu",
    "SRMParameters", "SRMSimulationResult", "solve_srm_transient_cpu",
]


# ---------------------------------------------------------------------------
# Permanent-magnet synchronous motor (PMSM) reference transient model
# ---------------------------------------------------------------------------

_PMSM_STATE_COUNT = 5
_PMSM_ID, _PMSM_IQ, _PMSM_OMEGA_M, _PMSM_THETA_E, _PMSM_TEMP_C = range(_PMSM_STATE_COUNT)
_PMSM_STATE_NAMES = (
    "stator_current_d_a",
    "stator_current_q_a",
    "rotor_speed_rad_s",
    "electrical_position_rad",
    "winding_temperature_c",
)


@dataclass(frozen=True, slots=True)
class PMSMParameters:
    """SI parameters for a sinusoidally modelled surface/interior PMSM.

    Voltages and currents are rotor-flux-aligned dq quantities. The model
    includes d/q inductance saliency, permanent-magnet flux, copper loss,
    temperature-dependent stator resistance, mechanical inertia/friction,
    and one lumped winding thermal node. It does not model PWM switching,
    magnetic saturation, iron/magnet losses, demagnetisation, or inverter
    current-loop dynamics.
    """

    stator_resistance_ohm: float
    d_axis_inductance_h: float
    q_axis_inductance_h: float
    permanent_magnet_flux_wb: float
    pole_pairs: int
    inertia_kg_m2: float
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 35.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_electrical_position_rad: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self) -> None:
        positive = (
            "stator_resistance_ohm", "d_axis_inductance_h",
            "q_axis_inductance_h", "permanent_magnet_flux_wb",
            "inertia_kg_m2", "thermal_capacity_j_k",
        )
        for name in positive:
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "friction_n_m_s", "thermal_conductance_w_k",
            "resistance_temp_coefficient_per_k", "ambient_temperature_c",
            "initial_speed_rad_s", "initial_electrical_position_rad",
            "initial_temperature_c",
        ):
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            if name in ("friction_n_m_s", "thermal_conductance_w_k") and value < 0.0:
                raise ValueError(f"{name} must be non-negative")
        if (
            isinstance(self.pole_pairs, (bool, np.bool_))
            or not isinstance(self.pole_pairs, (int, np.integer))
            or self.pole_pairs < 1
        ):
            raise ValueError("pole_pairs must be a positive integer")


@dataclass(frozen=True)
class PMSMSimulationResult:
    """Immutable PMSM trajectory and its aligned dq input/load histories."""

    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    voltage_d_v: np.ndarray
    voltage_q_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.diagnostics, Mapping):
            raise TypeError("diagnostics must be a mapping")
        if not isinstance(self.motor_id, str) or not self.motor_id.strip():
            raise ValueError("motor_id must be a non-empty string")
        count = int(self.time_s.size)
        if self.state_data.size != count * _PMSM_STATE_COUNT:
            raise ValueError("PMSM state buffer does not match the time grid")
        for name in ("time_s", "state_data", "voltage_d_v", "voltage_q_v", "load_torque_nm"):
            value = np.asarray(getattr(self, name), dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(value).all():
                raise ValueError(f"{name} must contain only finite values")
            value.setflags(write=False)
            object.__setattr__(self, name, value)
        if any(getattr(self, name).size != count for name in ("voltage_d_v", "voltage_q_v", "load_torque_nm")):
            raise ValueError("PMSM voltage and load profiles must align with the time grid")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self) -> int:
        return int(self.time_s.size)

    @property
    def state_names(self) -> tuple[str, ...]:
        return _PMSM_STATE_NAMES

    def state(self, name: str) -> np.ndarray:
        try:
            index = _PMSM_STATE_NAMES.index(name)
        except ValueError as exc:
            raise KeyError(name) from exc
        view = self.state_data.reshape(self.sample_count, _PMSM_STATE_COUNT)[:, index].view()
        view.setflags(write=False)
        return view

    def channels(self, parameters: PMSMParameters) -> dict[str, np.ndarray]:
        """Derive electrical, mechanical, thermal and drive channels."""
        if not isinstance(parameters, PMSMParameters):
            raise TypeError("parameters must be PMSMParameters")
        matrix = self.state_data.reshape(self.sample_count, _PMSM_STATE_COUNT)
        ids, iqs, speed, theta, temp = (matrix[:, index] for index in range(_PMSM_STATE_COUNT))
        resistance_factor = np.maximum(
            0.05,
            1.0 + parameters.resistance_temp_coefficient_per_k
            * (temp - parameters.ambient_temperature_c),
        )
        resistance = parameters.stator_resistance_ohm * resistance_factor
        torque = 1.5 * parameters.pole_pairs * (
            parameters.permanent_magnet_flux_wb * iqs
            + (parameters.d_axis_inductance_h - parameters.q_axis_inductance_h) * ids * iqs
        )
        values = {
            "stator_current_a": np.hypot(ids, iqs),
            "stator_current_d_a": ids,
            "stator_current_q_a": iqs,
            "electromagnetic_torque_nm": torque,
            "rotor_speed_rad_s": speed,
            "rotor_speed_rpm": speed * 60.0 / (2.0 * np.pi),
            "electrical_position_rad": theta,
            "electrical_speed_rad_s": parameters.pole_pairs * speed,
            "electrical_frequency_hz": parameters.pole_pairs * speed / (2.0 * np.pi),
            "winding_temperature_c": temp,
            "load_torque_nm": self.load_torque_nm,
            "electrical_input_power_w": 1.5 * (
                self.voltage_d_v * ids + self.voltage_q_v * iqs
            ),
            "stator_copper_loss_w": 1.5 * resistance * (ids * ids + iqs * iqs),
            "mechanical_output_power_w": torque * speed,
        }
        for value in values.values():
            value.setflags(write=False)
        return values

    def publish_to(
        self,
        read_model_store: Any,
        parameters: PMSMParameters,
        *,
        study_id: str | None = None,
        study_order: int | None = None,
    ) -> bool:
        """Publish a completed trajectory snapshot; caller owns StudyCompleted."""
        publish = getattr(read_model_store, "publish", None)
        if not callable(publish):
            raise TypeError("read_model_store must expose publish(...)")
        return publish(
            self.motor_id,
            self.channels(parameters),
            self.sample_period_s,
            status="completed",
            study_id=study_id,
            study_order=study_order,
            diagnostics=self.diagnostics,
        )


def _pmsm_derivative(
    state: np.ndarray,
    parameters: PMSMParameters,
    voltage_d_v: float,
    voltage_q_v: float,
    load_torque_nm: float,
) -> np.ndarray:
    ids, iqs, omega_m, _theta_e, temperature = state
    p = parameters
    resistance = p.stator_resistance_ohm * max(
        0.05,
        1.0 + p.resistance_temp_coefficient_per_k
        * (temperature - p.ambient_temperature_c),
    )
    omega_e = p.pole_pairs * omega_m
    # Rotor dq convention:
    # vd = R*id + Ld*did/dt - omega_e*Lq*iq
    # vq = R*iq + Lq*diq/dt + omega_e*(Ld*id + psi_pm)
    did = (voltage_d_v - resistance * ids + omega_e * p.q_axis_inductance_h * iqs) / p.d_axis_inductance_h
    diq = (
        voltage_q_v - resistance * iqs
        - omega_e * (p.d_axis_inductance_h * ids + p.permanent_magnet_flux_wb)
    ) / p.q_axis_inductance_h
    torque = 1.5 * p.pole_pairs * (
        p.permanent_magnet_flux_wb * iqs
        + (p.d_axis_inductance_h - p.q_axis_inductance_h) * ids * iqs
    )
    copper_loss = 1.5 * resistance * (ids * ids + iqs * iqs)
    return np.asarray(
        (
            did,
            diq,
            (torque - load_torque_nm - p.friction_n_m_s * omega_m) / p.inertia_kg_m2,
            omega_e,
            (copper_loss - p.thermal_conductance_w_k * (temperature - p.ambient_temperature_c))
            / p.thermal_capacity_j_k,
        ),
        dtype=np.float64,
    )


def solve_pmsm_transient_cpu(
    motor_id: str,
    parameters: PMSMParameters,
    duration_s: float,
    step_s: float,
    *,
    stator_voltage_d_v: float | Sequence[float] | np.ndarray,
    stator_voltage_q_v: float | Sequence[float] | np.ndarray,
    load_torque_nm: float | Sequence[float] | np.ndarray = 0.0,
) -> PMSMSimulationResult:
    """Integrate a sinusoidal PMSM in rotor dq coordinates using shared RK4.

    dq voltage and load profiles use zero-order hold and must be scalar or
    contain exactly one value per output sample. The CPU implementation is
    the reference; the induction CUDA kernel is intentionally not reused.
    """
    if not isinstance(motor_id, str) or not motor_id.strip():
        raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters, PMSMParameters):
        raise TypeError("parameters must be PMSMParameters")
    count, times = _validate_time(duration_s, step_s)
    vd = _voltage_profile(stator_voltage_d_v, count, "stator_voltage_d_v")
    vq = _voltage_profile(stator_voltage_q_v, count, "stator_voltage_q_v")
    load = _voltage_profile(load_torque_nm, count, "load_torque_nm")
    initial = np.asarray(
        (
            0.0,
            0.0,
            parameters.initial_speed_rad_s,
            parameters.initial_electrical_position_rad,
            parameters.initial_temperature_c,
        ),
        dtype=np.float64,
    )
    problem = NumericalProblem(
        problem_id=motor_id.strip(),
        workload="pmsm_transient_single",
        derivative=lambda state, inputs: _pmsm_derivative(
            state, parameters, inputs[0], inputs[1], inputs[2]
        ),
        initial_state=initial,
        duration_s=duration_s,
        maximum_step_s=step_s,
        input_at=lambda index, _time: (
            float(vd[index]), float(vq[index]), float(load[index])
        ),
        precision="float64",
    )
    solution = solve_numerical_problem(problem)
    if solution.sample_count != count:
        raise RuntimeError("shared ODE engine returned an unexpected PMSM sample count")
    return PMSMSimulationResult(
        motor_id=motor_id.strip(),
        time_s=solution.time_s,
        state_data=solution.state_data,
        voltage_d_v=vd,
        voltage_q_v=vq,
        load_torque_nm=load,
        sample_period_s=solution.sample_period_s,
        backend="cpu-rk4",
        diagnostics={
            "integrator": solution.integrator,
            "precision": "float64",
            "workload": "pmsm_transient_single",
            "state_layout": "step_major_state_minor",
            "state_count": _PMSM_STATE_COUNT,
            "model": "pmsm_rotor_dq_electromechanical_lumped_thermal",
            "model_fidelity": "linear_magnetic_parameters_no_pwm_or_iron_loss",
            "voltage_profile": "zero_order_hold_rotor_dq",
            "load_torque_profile": "zero_order_hold",
            "requested_max_step_s": float(step_s),
            "effective_step_s": solution.sample_period_s,
            "integration_engine": "core.numerics.ode.solve_rk4",
        },
    )


# ---------------------------------------------------------------------------
# Brushless DC motor (BLDC) three-phase trapezoidal phase-domain model
# ---------------------------------------------------------------------------

_BLDC_STATE_COUNT = 5
_BLDC_IA, _BLDC_IB, _BLDC_OMEGA_M, _BLDC_THETA_M, _BLDC_TEMP_C = range(_BLDC_STATE_COUNT)
_BLDC_STATE_NAMES = (
    "phase_a_current_a", "phase_b_current_a", "rotor_speed_rad_s",
    "mechanical_position_rad", "winding_temperature_c",
)


@dataclass(frozen=True, slots=True)
class BLDCParameters:
    """Balanced three-phase BLDC parameters in SI units.

    The phase back-EMF is a normalized 120-degree-flat trapezoid with
    60-degree transitions. Torque and back-EMF constants are phase/peak
    quantities and must use a consistent identification convention.
    """

    phase_resistance_ohm: float
    phase_inductance_h: float
    back_emf_constant_v_s_per_rad: float
    torque_constant_nm_per_a: float
    pole_pairs: int
    inertia_kg_m2: float
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 35.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_mechanical_position_rad: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self) -> None:
        positive = (
            "phase_resistance_ohm", "phase_inductance_h",
            "back_emf_constant_v_s_per_rad", "torque_constant_nm_per_a",
            "inertia_kg_m2", "thermal_capacity_j_k",
        )
        for name in positive:
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        for name in (
            "friction_n_m_s", "thermal_conductance_w_k",
            "resistance_temp_coefficient_per_k", "ambient_temperature_c",
            "initial_speed_rad_s", "initial_mechanical_position_rad",
            "initial_temperature_c",
        ):
            value = getattr(self, name)
            if not _is_numeric_scalar(value):
                raise TypeError(f"{name} must be numeric")
            if not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
            if name in ("friction_n_m_s", "thermal_conductance_w_k") and value < 0.0:
                raise ValueError(f"{name} must be non-negative")
        if (
            isinstance(self.pole_pairs, (bool, np.bool_))
            or not isinstance(self.pole_pairs, (int, np.integer))
            or self.pole_pairs < 1
        ):
            raise ValueError("pole_pairs must be a positive integer")


@dataclass(frozen=True, slots=True)
class BLDCSimulationResult:
    """Immutable BLDC trajectory and aligned averaged-inverter inputs."""

    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    duty_a: np.ndarray
    duty_b: np.ndarray
    duty_c: np.ndarray
    dc_bus_voltage_v: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.motor_id, str) or not self.motor_id.strip():
            raise ValueError("motor_id must be a non-empty string")
        if not isinstance(self.diagnostics, Mapping):
            raise TypeError("diagnostics must be a mapping")
        for name in ("time_s", "state_data", "duty_a", "duty_b", "duty_c", "dc_bus_voltage_v"):
            value = np.asarray(getattr(self, name), dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(value).all():
                raise ValueError(f"{name} must contain only finite values")
            value.setflags(write=False)
            object.__setattr__(self, name, value)
        count = self.time_s.size
        if self.state_data.size != count * _BLDC_STATE_COUNT:
            raise ValueError("BLDC state buffer does not match the time grid")
        if any(getattr(self, name).size != count for name in ("duty_a", "duty_b", "duty_c", "dc_bus_voltage_v")):
            raise ValueError("BLDC inverter profiles must align with the time grid")
        if any(np.any(np.abs(getattr(self, name)) > 1.0) for name in ("duty_a", "duty_b", "duty_c")):
            raise ValueError("BLDC phase duties must be within [-1, 1]")
        if np.any(self.dc_bus_voltage_v <= 0.0):
            raise ValueError("dc_bus_voltage_v must be positive")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self) -> int:
        return int(self.time_s.size)

    @property
    def state_names(self) -> tuple[str, ...]:
        return _BLDC_STATE_NAMES

    def state(self, name: str) -> np.ndarray:
        try:
            index = _BLDC_STATE_NAMES.index(name)
        except ValueError as exc:
            raise KeyError(name) from exc
        view = self.state_data.reshape(self.sample_count, _BLDC_STATE_COUNT)[:, index].view()
        view.setflags(write=False)
        return view

    def channels(self, parameters: BLDCParameters) -> dict[str, np.ndarray]:
        if not isinstance(parameters, BLDCParameters):
            raise TypeError("parameters must be BLDCParameters")
        matrix = self.state_data.reshape(self.sample_count, _BLDC_STATE_COUNT)
        ia, ib, speed, theta_m, temp = (matrix[:, i] for i in range(_BLDC_STATE_COUNT))
        ic = -ia - ib
        theta_e = np.mod(parameters.pole_pairs * theta_m, 2.0 * np.pi)
        fa = _bldc_trapezoid(theta_e)
        fb = _bldc_trapezoid(theta_e - 2.0 * np.pi / 3.0)
        fc = _bldc_trapezoid(theta_e + 2.0 * np.pi / 3.0)
        torque = parameters.torque_constant_nm_per_a * (fa * ia + fb * ib + fc * ic)
        resistance = parameters.phase_resistance_ohm * np.maximum(
            0.05, 1.0 + parameters.resistance_temp_coefficient_per_k
            * (temp - parameters.ambient_temperature_c)
        )
        ea = parameters.back_emf_constant_v_s_per_rad * speed * fa
        eb = parameters.back_emf_constant_v_s_per_rad * speed * fb
        ec = parameters.back_emf_constant_v_s_per_rad * speed * fc
        values = {
            "phase_a_current_a": ia, "phase_b_current_a": ib, "phase_c_current_a": ic,
            "phase_current_rms_proxy_a": np.sqrt((ia * ia + ib * ib + ic * ic) / 3.0),
            "phase_a_back_emf_v": ea, "phase_b_back_emf_v": eb, "phase_c_back_emf_v": ec,
            "electromagnetic_torque_nm": torque,
            "rotor_speed_rad_s": speed, "rotor_speed_rpm": speed * 60.0 / (2.0 * np.pi),
            "mechanical_position_rad": theta_m,
            "electrical_position_rad": parameters.pole_pairs * theta_m,
            "electrical_frequency_hz": parameters.pole_pairs * speed / (2.0 * np.pi),
            "hall_sector": np.floor(theta_e / (np.pi / 3.0)) + 1.0,
            "winding_temperature_c": temp, "dc_bus_voltage_v": self.dc_bus_voltage_v,
            "dc_bus_current_a": 0.5 * (self.duty_a * ia + self.duty_b * ib + self.duty_c * ic),
            "stator_copper_loss_w": resistance * (ia * ia + ib * ib + ic * ic),
            "mechanical_output_power_w": torque * speed,
        }
        for value in values.values():
            value.setflags(write=False)
        return values

    def publish_to(
        self, read_model_store: Any, parameters: BLDCParameters, *,
        study_id: str | None = None, study_order: int | None = None,
    ) -> bool:
        publish = getattr(read_model_store, "publish", None)
        if not callable(publish):
            raise TypeError("read_model_store must expose publish(...)")
        return publish(
            self.motor_id, self.channels(parameters), self.sample_period_s,
            status="completed", study_id=study_id, study_order=study_order,
            diagnostics=self.diagnostics,
        )


def _bldc_trapezoid(angle_rad: float | np.ndarray) -> float | np.ndarray:
    """Normalized trapezoidal BLDC phase back-EMF in [-1, 1]."""
    angle = np.mod(np.asarray(angle_rad, dtype=np.float64), 2.0 * np.pi)
    degrees = angle * (180.0 / np.pi)
    result = np.empty_like(degrees)
    rising = degrees < 60.0
    positive = (degrees >= 60.0) & (degrees < 180.0)
    falling = (degrees >= 180.0) & (degrees < 240.0)
    negative = degrees >= 240.0
    result[rising] = -1.0 + 2.0 * degrees[rising] / 60.0
    result[positive] = 1.0
    result[falling] = 1.0 - 2.0 * (degrees[falling] - 180.0) / 60.0
    result[negative] = -1.0
    if np.ndim(angle_rad) == 0:
        return float(result)
    return result


def _bldc_profile(value: float | Sequence[float] | np.ndarray, count: int, name: str) -> np.ndarray:
    return _voltage_profile(value, count, name)


def _bldc_derivative(
    state: np.ndarray, parameters: BLDCParameters,
    duties: tuple[float, float, float], bus_v: float, load_torque_nm: float,
) -> np.ndarray:
    ia, ib, omega_m, theta_m, temperature = state
    ic = -ia - ib
    theta_e = parameters.pole_pairs * theta_m
    fa = _bldc_trapezoid(theta_e)
    fb = _bldc_trapezoid(theta_e - 2.0 * np.pi / 3.0)
    fc = _bldc_trapezoid(theta_e + 2.0 * np.pi / 3.0)
    ea = parameters.back_emf_constant_v_s_per_rad * omega_m * fa
    eb = parameters.back_emf_constant_v_s_per_rad * omega_m * fb
    ec = parameters.back_emf_constant_v_s_per_rad * omega_m * fc
    resistance = parameters.phase_resistance_ohm * max(
        0.05, 1.0 + parameters.resistance_temp_coefficient_per_k
        * (temperature - parameters.ambient_temperature_c)
    )
    va, vb, vc = (duty * bus_v * 0.5 for duty in duties)
    neutral = (
        va - ea - resistance * ia
        + vb - eb - resistance * ib
        + vc - ec - resistance * ic
    ) / 3.0
    dia = (va - ea - resistance * ia - neutral) / parameters.phase_inductance_h
    dib = (vb - eb - resistance * ib - neutral) / parameters.phase_inductance_h
    torque = parameters.torque_constant_nm_per_a * (fa * ia + fb * ib + fc * ic)
    copper_loss = resistance * (ia * ia + ib * ib + ic * ic)
    return np.asarray((
        dia, dib,
        (torque - load_torque_nm - parameters.friction_n_m_s * omega_m) / parameters.inertia_kg_m2,
        omega_m,
        (copper_loss - parameters.thermal_conductance_w_k
         * (temperature - parameters.ambient_temperature_c)) / parameters.thermal_capacity_j_k,
    ), dtype=np.float64)


def solve_bldc_transient_cpu(
    motor_id: str, parameters: BLDCParameters, duration_s: float, step_s: float, *,
    dc_bus_voltage_v: float | Sequence[float] | np.ndarray,
    duty_a: float | Sequence[float] | np.ndarray,
    duty_b: float | Sequence[float] | np.ndarray,
    duty_c: float | Sequence[float] | np.ndarray,
    load_torque_nm: float | Sequence[float] | np.ndarray = 0.0,
) -> BLDCSimulationResult:
    """Integrate BLDC trapezoidal phase dynamics using averaged six-step PWM duties.

    Duty inputs are signed normalized phase pole-voltage commands in [-1, 1].
    Scalar or sample-aligned zero-order-held profiles are accepted. PWM carrier
    switching, dead time, device drops and closed-loop current control are not
    modeled; caller supplies the commutation duty sequence.
    """
    if not isinstance(motor_id, str) or not motor_id.strip():
        raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters, BLDCParameters):
        raise TypeError("parameters must be BLDCParameters")
    count, _times = _validate_time(duration_s, step_s)
    bus = _bldc_profile(dc_bus_voltage_v, count, "dc_bus_voltage_v")
    if np.any(bus <= 0.0):
        raise ValueError("dc_bus_voltage_v must be positive")
    duties = []
    for name, value in (("duty_a", duty_a), ("duty_b", duty_b), ("duty_c", duty_c)):
        profile = _bldc_profile(value, count, name)
        if np.any(profile < -1.0) or np.any(profile > 1.0):
            raise ValueError(f"{name} values must be within [-1, 1]")
        duties.append(profile)
    load = _bldc_profile(load_torque_nm, count, "load_torque_nm")
    initial = np.asarray((
        0.0, 0.0, parameters.initial_speed_rad_s,
        parameters.initial_mechanical_position_rad, parameters.initial_temperature_c,
    ), dtype=np.float64)
    problem = NumericalProblem(
        problem_id=motor_id.strip(),
        workload="bldc_trapezoidal_transient_single",
        derivative=lambda state, inputs: _bldc_derivative(
            state, parameters, (inputs[0], inputs[1], inputs[2]), inputs[3], inputs[4]
        ),
        initial_state=initial, duration_s=duration_s, maximum_step_s=step_s,
        input_at=lambda index, _time: (
            float(duties[0][index]), float(duties[1][index]),
            float(duties[2][index]), float(bus[index]), float(load[index]),
        ),
        precision="float64",
    )
    solution = solve_numerical_problem(problem)
    if solution.sample_count != count:
        raise RuntimeError("shared ODE engine returned an unexpected BLDC sample count")
    return BLDCSimulationResult(
        motor_id=motor_id.strip(), time_s=solution.time_s, state_data=solution.state_data,
        duty_a=duties[0], duty_b=duties[1], duty_c=duties[2],
        dc_bus_voltage_v=bus, sample_period_s=solution.sample_period_s,
        backend="cpu-rk4",
        diagnostics={
            "integrator": solution.integrator, "precision": "float64",
            "workload": "bldc_trapezoidal_transient_single",
            "state_layout": "step_major_state_minor", "state_count": _BLDC_STATE_COUNT,
            "model": "bldc_three_phase_trapezoidal_back_emf_floating_neutral",
            "commutation": "six_step_120_degree_trapezoidal",
            "inverter": "averaged_signed_pole_voltage_duty",
            "phase_current_constraint": "ia_plus_ib_plus_ic_equals_zero",
            "input_profile": "zero_order_hold",
            "model_fidelity": "linear_phase_rl_no_pwm_switching_or_saturation",
            "requested_max_step_s": float(step_s),
            "effective_step_s": solution.sample_period_s,
            "integration_engine": "core.numerics.ode.solve_rk4",
        },
    )


_SYNRM_STATE_COUNT = 5
_SYNRM_STATE_NAMES = ("stator_current_d_a","stator_current_q_a","rotor_speed_rad_s","electrical_position_rad","winding_temperature_c")


@dataclass(frozen=True, slots=True)
class SynRMParameters:
    stator_resistance_ohm: float
    d_axis_inductance_h: float
    q_axis_inductance_h: float
    pole_pairs: int
    inertia_kg_m2: float
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 35.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_electrical_position_rad: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self):
        for name in ("stator_resistance_ohm","d_axis_inductance_h","q_axis_inductance_h","inertia_kg_m2","thermal_capacity_j_k"):
            value=getattr(self,name)
            if not _is_numeric_scalar(value) or not isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("friction_n_m_s","thermal_conductance_w_k","resistance_temp_coefficient_per_k","ambient_temperature_c","initial_speed_rad_s","initial_electrical_position_rad","initial_temperature_c"):
            value=getattr(self,name)
            if not _is_numeric_scalar(value) or not isfinite(float(value)):
                raise ValueError(f"{name} must be finite and numeric")
            if name in ("friction_n_m_s","thermal_conductance_w_k") and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.d_axis_inductance_h <= self.q_axis_inductance_h:
            raise ValueError("SynRM d-axis inductance must exceed q-axis inductance")
        if isinstance(self.pole_pairs,(bool,np.bool_)) or not isinstance(self.pole_pairs,(int,np.integer)) or self.pole_pairs < 1:
            raise ValueError("pole_pairs must be a positive integer")


@dataclass(frozen=True, slots=True)
class SynRMSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    voltage_d_v: np.ndarray
    voltage_q_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip():
            raise ValueError("motor_id must be a non-empty string")
        for name in ("time_s","state_data","voltage_d_v","voltage_q_v","load_torque_nm"):
            value=np.asarray(getattr(self,name),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(value).all(): raise ValueError(f"{name} must contain only finite values")
            value.setflags(write=False); object.__setattr__(self,name,value)
        n=self.time_s.size
        if self.state_data.size != n*_SYNRM_STATE_COUNT: raise ValueError("SynRM state buffer does not match the time grid")
        if any(getattr(self,nm).size != n for nm in ("voltage_d_v","voltage_q_v","load_torque_nm")): raise ValueError("SynRM input profiles must align with the time grid")
        if not isinstance(self.diagnostics,Mapping): raise TypeError("diagnostics must be a mapping")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _SYNRM_STATE_NAMES
    def state(self,name):
        try: i=_SYNRM_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        out=self.state_data.reshape(self.sample_count,_SYNRM_STATE_COUNT)[:,i].view(); out.setflags(write=False); return out

    def channels(self,p):
        if not isinstance(p,SynRMParameters): raise TypeError("parameters must be SynRMParameters")
        m=self.state_data.reshape(self.sample_count,_SYNRM_STATE_COUNT)
        id_,iq,w,theta,temp=(m[:,i] for i in range(_SYNRM_STATE_COUNT))
        torque=1.5*p.pole_pairs*(p.d_axis_inductance_h-p.q_axis_inductance_h)*id_*iq
        r=p.stator_resistance_ohm*np.maximum(0.05,1+p.resistance_temp_coefficient_per_k*(temp-p.ambient_temperature_c))
        values={
            "stator_current_d_a":id_,"stator_current_q_a":iq,
            "stator_current_rms_a":np.sqrt(0.5*(id_**2+iq**2)),
            "electromagnetic_torque_nm":torque,"rotor_speed_rad_s":w,"rotor_speed_rpm":w*60/(2*np.pi),
            "electrical_position_rad":theta,"electrical_frequency_hz":np.gradient(theta,self.time_s)/(2*np.pi),
            "winding_temperature_c":temp,"stator_copper_loss_w":1.5*r*(id_**2+iq**2),
            "electrical_input_power_w":1.5*(self.voltage_d_v*id_+self.voltage_q_v*iq),
            "mechanical_output_power_w":torque*w,
        }
        for v in values.values(): v.setflags(write=False)
        return values

    def publish_to(self,store,p,*,study_id=None,study_order=None):
        publish=getattr(store,"publish",None)
        if not callable(publish): raise TypeError("read_model_store must expose publish(...)")
        return publish(self.motor_id,self.channels(p),self.sample_period_s,status="completed",study_id=study_id,study_order=study_order,diagnostics=self.diagnostics)


def _synrm_derivative(x,p,vd,vq,load):
    id_,iq,w,_theta,temp=x; we=p.pole_pairs*w
    r=p.stator_resistance_ohm*max(0.05,1+p.resistance_temp_coefficient_per_k*(temp-p.ambient_temperature_c))
    did=(vd-r*id_+we*p.q_axis_inductance_h*iq)/p.d_axis_inductance_h
    diq=(vq-r*iq-we*p.d_axis_inductance_h*id_)/p.q_axis_inductance_h
    torque=1.5*p.pole_pairs*(p.d_axis_inductance_h-p.q_axis_inductance_h)*id_*iq
    copper=1.5*r*(id_**2+iq**2)
    return np.asarray((did,diq,(torque-load-p.friction_n_m_s*w)/p.inertia_kg_m2,we,(copper-p.thermal_conductance_w_k*(temp-p.ambient_temperature_c))/p.thermal_capacity_j_k),dtype=np.float64)


def solve_synrm_transient_cpu(motor_id,parameters,duration_s,step_s,*,stator_voltage_d_v,stator_voltage_q_v,load_torque_nm=0.0):
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters,SynRMParameters): raise TypeError("parameters must be SynRMParameters")
    count,_=_validate_time(duration_s,step_s)
    vd=_voltage_profile(stator_voltage_d_v,count,"stator_voltage_d_v"); vq=_voltage_profile(stator_voltage_q_v,count,"stator_voltage_q_v"); load=_voltage_profile(load_torque_nm,count,"load_torque_nm")
    initial=np.asarray((0.,0.,parameters.initial_speed_rad_s,parameters.initial_electrical_position_rad,parameters.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="synrm_dq_transient_single",derivative=lambda x,u:_synrm_derivative(x,parameters,*u),initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,input_at=lambda i,_t:(float(vd[i]),float(vq[i]),float(load[i])),precision="float64")
    sol=solve_numerical_problem(problem)
    if sol.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected SynRM sample count")
    return SynRMSimulationResult(motor_id.strip(),sol.time_s,sol.state_data,vd,vq,load,sol.sample_period_s,"cpu-rk4",{"integrator":sol.integrator,"precision":"float64","workload":"synrm_dq_transient_single","model":"linear_rotor_dq_synchronous_reluctance","torque_model":"1.5*pole_pairs*(Ld-Lq)*id*iq","model_fidelity":"linear_inductance_no_saturation_or_iron_loss","integration_engine":"core.numerics.ode.solve_rk4"})


_SRM_STATE_COUNT=6
_SRM_STATE_NAMES=("phase_a_current_a","phase_b_current_a","phase_c_current_a","rotor_speed_rad_s","mechanical_position_rad","winding_temperature_c")


@dataclass(frozen=True, slots=True)
class SRMParameters:
    phase_resistance_ohm: float
    unaligned_inductance_h: float
    aligned_inductance_h: float
    rotor_poles: int
    inertia_kg_m2: float
    saturation_current_a: float = 100.0
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 35.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_mechanical_position_rad: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self):
        for name in ("phase_resistance_ohm","unaligned_inductance_h","aligned_inductance_h","inertia_kg_m2","saturation_current_a","thermal_capacity_j_k"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{name} must be finite and positive")
        if self.aligned_inductance_h<=self.unaligned_inductance_h: raise ValueError("aligned_inductance_h must exceed unaligned_inductance_h")
        if isinstance(self.rotor_poles,(bool,np.bool_)) or not isinstance(self.rotor_poles,(int,np.integer)) or self.rotor_poles<2: raise ValueError("rotor_poles must be an integer of at least two")
        for name in ("friction_n_m_s","thermal_conductance_w_k","resistance_temp_coefficient_per_k","ambient_temperature_c","initial_speed_rad_s","initial_mechanical_position_rad","initial_temperature_c"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{name} must be finite and numeric")
            if name in ("friction_n_m_s","thermal_conductance_w_k") and v<0: raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class SRMSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    phase_a_voltage_v: np.ndarray
    phase_b_voltage_v: np.ndarray
    phase_c_voltage_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be a non-empty string")
        for name in ("time_s","state_data","phase_a_voltage_v","phase_b_voltage_v","phase_c_voltage_v","load_torque_nm"):
            v=np.asarray(getattr(self,name),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{name} must contain only finite values")
            v.setflags(write=False); object.__setattr__(self,name,v)
        n=self.time_s.size
        if self.state_data.size!=n*_SRM_STATE_COUNT: raise ValueError("SRM state buffer does not match the time grid")
        if any(getattr(self,k).size!=n for k in ("phase_a_voltage_v","phase_b_voltage_v","phase_c_voltage_v","load_torque_nm")): raise ValueError("SRM profiles must align with the time grid")
        if not isinstance(self.diagnostics,Mapping): raise TypeError("diagnostics must be a mapping")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _SRM_STATE_NAMES
    def state(self,name):
        try: i=_SRM_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        out=self.state_data.reshape(self.sample_count,_SRM_STATE_COUNT)[:,i].view(); out.setflags(write=False); return out

    def channels(self,p):
        if not isinstance(p,SRMParameters): raise TypeError("parameters must be SRMParameters")
        m=self.state_data.reshape(self.sample_count,_SRM_STATE_COUNT)
        currents=[m[:,i] for i in range(3)]; speed=m[:,3]; theta=m[:,4]; temp=m[:,5]
        offsets=(0.,2*np.pi/3,4*np.pi/3); inductances=[]; torque=np.zeros(self.sample_count)
        base=0.5*(p.aligned_inductance_h+p.unaligned_inductance_h); delta=0.5*(p.aligned_inductance_h-p.unaligned_inductance_h)
        for current,offset in zip(currents,offsets):
            angle=p.rotor_poles*theta-offset
            ratio=current/p.saturation_current_a
            sat=1+ratio**2
            inductances.append(base+delta*np.cos(angle)/sat)
            # Co-energy integral for lambda(i,theta)=i*(Lbase+delta*cos(angle)/(1+(i/Isat)^2)).
            torque += -0.5*delta*p.rotor_poles*np.sin(angle)*p.saturation_current_a**2*np.log1p(ratio**2)
        resistance=p.phase_resistance_ohm*np.maximum(0.05,1+p.resistance_temp_coefficient_per_k*(temp-p.ambient_temperature_c))
        power=sum(v*i for v,i in zip((self.phase_a_voltage_v,self.phase_b_voltage_v,self.phase_c_voltage_v),currents))
        values={"phase_a_current_a":currents[0],"phase_b_current_a":currents[1],"phase_c_current_a":currents[2],
            "phase_a_voltage_v":self.phase_a_voltage_v,"phase_b_voltage_v":self.phase_b_voltage_v,"phase_c_voltage_v":self.phase_c_voltage_v,
            "phase_a_inductance_h":inductances[0],"phase_b_inductance_h":inductances[1],"phase_c_inductance_h":inductances[2],
            "electromagnetic_torque_nm":torque,"rotor_speed_rad_s":speed,"rotor_speed_rpm":speed*60/(2*np.pi),
            "mechanical_position_rad":theta,"rotor_position_electrical_rad":np.mod(p.rotor_poles*theta,2*np.pi),
            "winding_temperature_c":temp,"stator_copper_loss_w":resistance*sum(i**2 for i in currents),
            "phase_input_power_w":power,"mechanical_output_power_w":torque*speed}
        for v in values.values(): v.setflags(write=False)
        return values

    def publish_to(self,store,p,*,study_id=None,study_order=None):
        publish=getattr(store,"publish",None)
        if not callable(publish): raise TypeError("read_model_store must expose publish(...)")
        return publish(self.motor_id,self.channels(p),self.sample_period_s,status="completed",study_id=study_id,study_order=study_order,diagnostics=self.diagnostics)


def _srm_phase_inductance(theta,current,p,offset):
    base=0.5*(p.aligned_inductance_h+p.unaligned_inductance_h); delta=0.5*(p.aligned_inductance_h-p.unaligned_inductance_h)
    angle=p.rotor_poles*theta-offset; sat=1+(current/p.saturation_current_a)**2
    return base+delta*np.cos(angle)/sat, -delta*p.rotor_poles*np.sin(angle)/sat


def _srm_derivative(x,p,voltages,load):
    currents=x[:3]; omega,theta,temp=x[3],x[4],x[5]
    r=p.phase_resistance_ohm*max(0.05,1+p.resistance_temp_coefficient_per_k*(temp-p.ambient_temperature_c))
    did=[]; torque=0.; copper=0.
    for current,voltage,offset in zip(currents,voltages,(0.,2*np.pi/3,4*np.pi/3)):
        inductance,dldtheta=_srm_phase_inductance(theta,float(current),p,offset)
        ratio=current/p.saturation_current_a
        angle=p.rotor_poles*theta-offset
        delta=0.5*(p.aligned_inductance_h-p.unaligned_inductance_h)
        differential=0.5*(p.aligned_inductance_h+p.unaligned_inductance_h) + delta*np.cos(angle)*(1-ratio**2)/(1+ratio**2)**2
        flux_angle=-current*delta*p.rotor_poles*np.sin(angle)/(1+ratio**2)
        did.append((voltage-r*current-flux_angle*omega)/max(differential,1e-12))
        torque += -0.5*delta*p.rotor_poles*np.sin(angle)*p.saturation_current_a**2*np.log1p(ratio**2)
        copper += r*current**2
    return np.asarray((*did,(torque-load-p.friction_n_m_s*omega)/p.inertia_kg_m2,omega,(copper-p.thermal_conductance_w_k*(temp-p.ambient_temperature_c))/p.thermal_capacity_j_k),dtype=np.float64)


def solve_srm_transient_cpu(motor_id,parameters,duration_s,step_s,*,phase_a_voltage_v,phase_b_voltage_v,phase_c_voltage_v,load_torque_nm=0.0):
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters,SRMParameters): raise TypeError("parameters must be SRMParameters")
    count,_=_validate_time(duration_s,step_s)
    va=_voltage_profile(phase_a_voltage_v,count,"phase_a_voltage_v"); vb=_voltage_profile(phase_b_voltage_v,count,"phase_b_voltage_v"); vc=_voltage_profile(phase_c_voltage_v,count,"phase_c_voltage_v"); load=_voltage_profile(load_torque_nm,count,"load_torque_nm")
    initial=np.asarray((0.,0.,0.,parameters.initial_speed_rad_s,parameters.initial_mechanical_position_rad,parameters.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="srm_phase_inductance_transient_single",derivative=lambda x,u:_srm_derivative(x,parameters,(u[0],u[1],u[2]),u[3]),initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,input_at=lambda i,_t:(float(va[i]),float(vb[i]),float(vc[i]),float(load[i])),precision="float64")
    sol=solve_numerical_problem(problem)
    if sol.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected SRM sample count")
    return SRMSimulationResult(motor_id.strip(),sol.time_s,sol.state_data,va,vb,vc,load,sol.sample_period_s,"cpu-rk4",{"integrator":sol.integrator,"precision":"float64","workload":"srm_phase_inductance_transient_single","model":"three_phase_position_dependent_inductance_srm","torque_model":"sum(0.5*i_phase^2*dL_phase/dtheta)","inductance_model":"cosine_alignment_with_current_saturation_approximation","converter":"averaged_phase_voltage_command","model_fidelity":"no_switching_bridge_current_chopping_or_flux_hysteresis","integration_engine":"core.numerics.ode.solve_rk4"})


_DC_STATE_COUNT = 6
_DC_STATE_NAMES = ("armature_current_a", "shunt_field_current_a", "rotor_speed_rad_s", "mechanical_position_rad", "armature_temperature_c", "field_temperature_c")


@dataclass(frozen=True, slots=True)
class DCMotorParameters:
    armature_resistance_ohm: float
    armature_inductance_h: float
    inertia_kg_m2: float
    excitation: str
    pole_pairs: int = 1
    field_resistance_ohm: float = 1.0
    field_inductance_h: float = 0.1
    shunt_flux_wb_per_a: float = 0.05
    series_flux_wb_per_a: float = 0.01
    saturation_flux_wb: float = 0.8
    permanent_magnet_flux_wb: float = 0.05
    compound_connection: str = "cumulative"
    friction_n_m_s: float = 0.0
    armature_thermal_capacity_j_k: float = 5000.0
    field_thermal_capacity_j_k: float = 3000.0
    thermal_conductance_w_k: float = 20.0
    ambient_temperature_c: float = 25.0
    resistance_temp_coefficient_per_k: float = 0.00393
    initial_speed_rad_s: float = 0.0
    initial_armature_temperature_c: float = 25.0
    initial_field_temperature_c: float = 25.0

    def __post_init__(self):
        for name in ("armature_resistance_ohm","armature_inductance_h","inertia_kg_m2","field_resistance_ohm","series_field_resistance_ohm","field_inductance_h","saturation_flux_wb","armature_thermal_capacity_j_k","field_thermal_capacity_j_k"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{name} must be finite and positive")
        for name in ("shunt_flux_wb_per_a","series_flux_wb_per_a","permanent_magnet_flux_wb","friction_n_m_s","thermal_conductance_w_k","resistance_temp_coefficient_per_k","ambient_temperature_c","initial_speed_rad_s","initial_armature_temperature_c","initial_field_temperature_c"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{name} must be finite and numeric")
            if name in ("shunt_flux_wb_per_a","series_flux_wb_per_a","permanent_magnet_flux_wb","friction_n_m_s","thermal_conductance_w_k") and v<0: raise ValueError(f"{name} must be non-negative")
        if self.excitation not in {"separately_excited","shunt","series","compound","permanent_magnet"}: raise ValueError("unsupported DC excitation")
        if self.compound_connection not in {"cumulative","differential"}: raise ValueError("compound_connection must be cumulative or differential")
        if isinstance(self.pole_pairs,(bool,np.bool_)) or not isinstance(self.pole_pairs,(int,np.integer)) or self.pole_pairs<1: raise ValueError("pole_pairs must be a positive integer")
        if self.excitation in {"separately_excited","shunt","compound"} and self.shunt_flux_wb_per_a<=0: raise ValueError("shunt_flux_wb_per_a must be positive")
        if self.excitation in {"series","compound"} and self.series_flux_wb_per_a<=0: raise ValueError("series_flux_wb_per_a must be positive")
        if self.excitation=="permanent_magnet" and self.permanent_magnet_flux_wb<=0: raise ValueError("permanent_magnet_flux_wb must be positive")


@dataclass(frozen=True, slots=True)
class DCMotorSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    armature_voltage_v: np.ndarray
    field_voltage_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be a non-empty string")
        for name in ("time_s","state_data","armature_voltage_v","field_voltage_v","load_torque_nm"):
            v=np.asarray(getattr(self,name),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{name} must contain only finite values")
            v.setflags(write=False); object.__setattr__(self,name,v)
        n=self.time_s.size
        if self.state_data.size!=n*_DC_STATE_COUNT: raise ValueError("DC motor state buffer does not match time grid")
        if any(getattr(self,k).size!=n for k in ("armature_voltage_v","field_voltage_v","load_torque_nm")): raise ValueError("DC motor profiles must align with time grid")
        if not isinstance(self.diagnostics,Mapping): raise TypeError("diagnostics must be a mapping")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _DC_STATE_NAMES
    def state(self,name):
        try: i=_DC_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        v=self.state_data.reshape(self.sample_count,_DC_STATE_COUNT)[:,i].view(); v.setflags(write=False); return v

    def channels(self,p):
        if not isinstance(p,DCMotorParameters): raise TypeError("parameters must be DCMotorParameters")
        m=self.state_data.reshape(self.sample_count,_DC_STATE_COUNT)
        ia,field_i,speed,pos,ta,tf=(m[:,i] for i in range(_DC_STATE_COUNT))
        if p.excitation=="permanent_magnet": flux=np.full(self.sample_count,p.permanent_magnet_flux_wb); fi=np.zeros(self.sample_count)
        elif p.excitation=="series": fi=ia; flux=p.saturation_flux_wb*np.tanh(p.series_flux_wb_per_a*ia/p.saturation_flux_wb)
        elif p.excitation=="compound":
            sign=-1.0 if p.compound_connection=="differential" else 1.0
            fi=field_i; flux=p.saturation_flux_wb*np.tanh((p.shunt_flux_wb_per_a*field_i+sign*p.series_flux_wb_per_a*ia)/p.saturation_flux_wb)
        else: fi=field_i; flux=p.saturation_flux_wb*np.tanh(p.shunt_flux_wb_per_a*field_i/p.saturation_flux_wb)
        ke=p.pole_pairs*flux; torque=ke*ia
        ra=p.armature_resistance_ohm*np.maximum(.05,1+p.resistance_temp_coefficient_per_k*(ta-p.ambient_temperature_c))
        rf=p.field_resistance_ohm*np.maximum(.05,1+p.resistance_temp_coefficient_per_k*(tf-p.ambient_temperature_c))
        fv=np.zeros(self.sample_count) if p.excitation in {"series","permanent_magnet"} else self.field_voltage_v
        values={"armature_current_a":ia,"armature_voltage_v":self.armature_voltage_v,"field_current_a":fi,"field_voltage_v":fv,
            "series_field_current_a":ia if p.excitation in {"series","compound"} else np.zeros(self.sample_count),
            "air_gap_flux_wb":flux,"back_emf_v":ke*speed,"electromagnetic_torque_nm":torque,
            "rotor_speed_rad_s":speed,"rotor_speed_rpm":speed*60/(2*np.pi),"mechanical_position_rad":pos,
            "armature_temperature_c":ta,"field_temperature_c":tf,"winding_temperature_c":ta,
            "armature_copper_loss_w":ra*ia**2,"field_copper_loss_w":(rs*fi**2 if p.excitation in {"series","compound"} else rf*fi**2),
            "electrical_input_power_w":self.armature_voltage_v*ia+fv*fi,"mechanical_output_power_w":torque*speed,
            "load_torque_nm":self.load_torque_nm}
        for v in values.values(): v.setflags(write=False)
        return values

    def publish_to(self,store,p,*,study_id=None,study_order=None):
        publish=getattr(store,"publish",None)
        if not callable(publish): raise TypeError("read_model_store must expose publish(...)")
        return publish(self.motor_id,self.channels(p),self.sample_period_s,status="completed",study_id=study_id,study_order=study_order,diagnostics=self.diagnostics)


def _dc_flux(p,ia,field_i):
    if p.excitation=="permanent_magnet": return p.permanent_magnet_flux_wb
    if p.excitation=="series": raw=p.series_flux_wb_per_a*ia
    elif p.excitation=="compound": raw=p.shunt_flux_wb_per_a*field_i+(-1 if p.compound_connection=="differential" else 1)*p.series_flux_wb_per_a*ia
    else: raw=p.shunt_flux_wb_per_a*field_i
    return p.saturation_flux_wb*np.tanh(raw/p.saturation_flux_wb)


def _dc_derivative(x,p,va,vf,load):
    ia,ifield,w,_pos,ta,tf=x
    ra=p.armature_resistance_ohm*max(.05,1+p.resistance_temp_coefficient_per_k*(ta-p.ambient_temperature_c))
    rf=p.field_resistance_ohm*max(.05,1+p.resistance_temp_coefficient_per_k*(tf-p.ambient_temperature_c))
    flux=_dc_flux(p,ia,ifield); ke=p.pole_pairs*flux
    dia=(va-(ra+(rs if p.excitation in {"series","compound"} else 0.0))*ia-ke*w)/p.armature_inductance_h
    dif=0.0 if p.excitation in {"series","permanent_magnet"} else (vf-rf*ifield)/p.field_inductance_h
    torque=ke*ia; arm_loss=ra*ia**2
    field_loss=(rs*ia**2 if p.excitation in {"series","compound"} else rf*ifield**2 if p.excitation not in {"permanent_magnet"} else 0.0)
    return np.asarray((dia,dif,(torque-load-p.friction_n_m_s*w)/p.inertia_kg_m2,w,
        (arm_loss-p.thermal_conductance_w_k*(ta-p.ambient_temperature_c))/p.armature_thermal_capacity_j_k,
        (field_loss-p.thermal_conductance_w_k*(tf-p.ambient_temperature_c))/p.field_thermal_capacity_j_k),dtype=np.float64)


def solve_dc_motor_transient_cpu(motor_id,parameters,duration_s,step_s,*,armature_voltage_v,field_voltage_v=0.0,load_torque_nm=0.0):
    """Experimental averaged DC motor model with explicit excitation and compound polarity."""
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be a non-empty string")
    if not isinstance(parameters,DCMotorParameters): raise TypeError("parameters must be DCMotorParameters")
    count,_=_validate_time(duration_s,step_s)
    va=_voltage_profile(armature_voltage_v,count,"armature_voltage_v")
    vf=_voltage_profile(field_voltage_v,count,"field_voltage_v")
    if parameters.excitation in {"shunt","compound"}:
        vf=va.copy()  # shunt field is connected across the armature supply
    load=_voltage_profile(load_torque_nm,count,"load_torque_nm")
    if parameters.excitation in {"series","permanent_magnet"} and np.any(vf!=0): raise ValueError("field_voltage_v must be zero for series and permanent_magnet excitation")
    initial=np.asarray((0.,0.,parameters.initial_speed_rad_s,0.,parameters.initial_armature_temperature_c,parameters.initial_field_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="dc_motor_excitation_transient_single",
        derivative=lambda x,u:_dc_derivative(x,parameters,*u),initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,
        input_at=lambda i,_t:(float(va[i]),float(vf[i]),float(load[i])),precision="float64")
    sol=solve_numerical_problem(problem)
    if sol.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected DC motor sample count")
    return DCMotorSimulationResult(motor_id.strip(),sol.time_s,sol.state_data,va,vf,load,sol.sample_period_s,"cpu-rk4",
        {"integrator":sol.integrator,"precision":"float64","workload":"dc_motor_excitation_transient_single",
         "model":"brushed_dc_electromechanical_two_node_thermal","excitation":parameters.excitation,
         "compound_connection":parameters.compound_connection if parameters.excitation=="compound" else None,
         "flux_model":"saturating_tanh","input_profile":"zero_order_hold",
         "model_fidelity":"averaged_armature_field_circuits_no_commutation_or_brush_arc",
         "integration_engine":"core.numerics.ode.solve_rk4"})


_SINGLE_PHASE_STATE_NAMES = ("main_current_a", "auxiliary_current_a", "rotor_speed_rad_s", "rotor_position_rad", "winding_temperature_c")


@dataclass(frozen=True, slots=True)
class SinglePhaseMotorParameters:
    """Reduced-order RMS-envelope parameters for single-phase induction families."""
    family: str
    main_resistance_ohm: float
    main_inductance_h: float
    auxiliary_resistance_ohm: float
    auxiliary_inductance_h: float
    inertia_kg_m2: float
    torque_constant_nm_per_a2: float
    frequency_hz: float = 50.0
    auxiliary_capacitance_f: float = 0.0
    auxiliary_cutout_speed_pu: float = 0.75
    shaded_pole_torque_factor: float = 0.12
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 5000.0
    thermal_conductance_w_k: float = 20.0
    ambient_temperature_c: float = 25.0
    initial_speed_rad_s: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self):
        allowed={"split_phase","capacitor_start","permanent_split_capacitor","capacitor_start_run","shaded_pole"}
        if self.family not in allowed: raise ValueError("unsupported single-phase motor family")
        for name in ("main_resistance_ohm","main_inductance_h","auxiliary_resistance_ohm","auxiliary_inductance_h","inertia_kg_m2","torque_constant_nm_per_a2","thermal_capacity_j_k"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{name} must be finite and positive")
        for name in ("frequency_hz","auxiliary_capacitance_f","auxiliary_cutout_speed_pu","shaded_pole_torque_factor","friction_n_m_s","thermal_conductance_w_k","ambient_temperature_c","initial_speed_rad_s","initial_temperature_c"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{name} must be finite and numeric")
        if self.frequency_hz<=0 or self.auxiliary_capacitance_f<0 or self.friction_n_m_s<0 or self.thermal_conductance_w_k<0: raise ValueError("frequency must be positive and passive parameters non-negative")
        if not 0<self.auxiliary_cutout_speed_pu<=1: raise ValueError("auxiliary_cutout_speed_pu must be in (0, 1]")
        if not 0<=self.shaded_pole_torque_factor<=1: raise ValueError("shaded_pole_torque_factor must be in [0, 1]")
        if self.family in {"capacitor_start","permanent_split_capacitor","capacitor_start_run"} and self.auxiliary_capacitance_f<=0: raise ValueError("capacitor motor families require auxiliary_capacitance_f > 0")


@dataclass(frozen=True, slots=True)
class SinglePhaseMotorSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    supply_voltage_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be non-empty")
        for name in ("time_s","state_data","supply_voltage_v","load_torque_nm"):
            v=np.asarray(getattr(self,name),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{name} must be finite")
            v.setflags(write=False); object.__setattr__(self,name,v)
        if self.state_data.size!=self.time_s.size*len(_SINGLE_PHASE_STATE_NAMES): raise ValueError("single-phase state buffer size mismatch")
        if self.supply_voltage_v.size!=self.time_s.size or self.load_torque_nm.size!=self.time_s.size: raise ValueError("single-phase profiles must align to time grid")
        if not isinstance(self.diagnostics,Mapping): raise TypeError("diagnostics must be a mapping")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _SINGLE_PHASE_STATE_NAMES
    def state(self,name):
        try: i=_SINGLE_PHASE_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        v=self.state_data.reshape(self.sample_count,len(_SINGLE_PHASE_STATE_NAMES))[:,i].view(); v.setflags(write=False); return v

    def channels(self,p):
        if not isinstance(p,SinglePhaseMotorParameters): raise TypeError("parameters must be SinglePhaseMotorParameters")
        m=self.state_data.reshape(self.sample_count,len(_SINGLE_PHASE_STATE_NAMES))
        im,ia,w,pos,temp=(m[:,i] for i in range(len(_SINGLE_PHASE_STATE_NAMES)))
        omega=2*np.pi*p.frequency_hz
        xa=omega*p.auxiliary_inductance_h-(1/(omega*p.auxiliary_capacitance_f) if p.auxiliary_capacitance_f>0 else 0.0)
        phase=np.arctan2(omega*p.main_inductance_h,p.main_resistance_ohm)-np.arctan2(xa,p.auxiliary_resistance_ohm)
        torque=p.torque_constant_nm_per_a2*im*ia*np.sin(phase)
        if p.family=="shaded_pole": torque*=p.shaded_pole_torque_factor
        active=np.ones(self.sample_count)
        if p.family in {"split_phase","capacitor_start"}: active=(w<p.auxiliary_cutout_speed_pu*omega).astype(float)
        vals={"line_voltage_rms_v":self.supply_voltage_v,"line_current_rms_a":np.hypot(im,ia*active),
              "main_winding_current_rms_a":im,"auxiliary_winding_current_rms_a":ia*active,
              "main_winding_copper_loss_w":p.main_resistance_ohm*im**2,
              "auxiliary_winding_copper_loss_w":p.auxiliary_resistance_ohm*ia**2*active,
              "auxiliary_phase_displacement_rad":np.full(self.sample_count,phase),
              "electromagnetic_torque_nm":torque,"rotor_speed_rad_s":w,"rotor_speed_rpm":w*60/(2*np.pi),
              "rotor_position_rad":pos,"winding_temperature_c":temp,
              "apparent_input_power_va":self.supply_voltage_v*np.hypot(im,ia*active),
              "mechanical_output_power_w":torque*w,"load_torque_nm":self.load_torque_nm}
        for v in vals.values(): v.setflags(write=False)
        return vals


def _single_phase_targets(p, voltage):
    omega=2*np.pi*p.frequency_hz
    zm=np.hypot(p.main_resistance_ohm,omega*p.main_inductance_h)
    xa=omega*p.auxiliary_inductance_h-(1/(omega*p.auxiliary_capacitance_f) if p.auxiliary_capacitance_f>0 else 0.0)
    za=np.hypot(p.auxiliary_resistance_ohm,xa)
    phase=np.arctan2(omega*p.main_inductance_h,p.main_resistance_ohm)-np.arctan2(xa,p.auxiliary_resistance_ohm)
    return abs(voltage)/zm,abs(voltage)/za,phase


def solve_single_phase_motor_transient_cpu(motor_id,parameters,duration_s,step_s,*,supply_voltage_rms_v,load_torque_nm=0.0):
    """Integrate a reduced-order single-phase motor RMS-envelope response."""
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be non-empty")
    if not isinstance(parameters,SinglePhaseMotorParameters): raise TypeError("parameters must be SinglePhaseMotorParameters")
    if not _is_numeric_scalar(supply_voltage_rms_v) or not isfinite(float(supply_voltage_rms_v)) or supply_voltage_rms_v<0: raise ValueError("supply_voltage_rms_v must be finite and non-negative")
    if not _is_numeric_scalar(load_torque_nm) or not isfinite(float(load_torque_nm)): raise ValueError("load_torque_nm must be finite and numeric")
    count,_=_validate_time(duration_s,step_s)
    voltage=np.full(count,float(supply_voltage_rms_v)); load=np.full(count,float(load_torque_nm))
    im_target,ia_target,phase=_single_phase_targets(parameters,float(supply_voltage_rms_v))
    tau_m=max(parameters.main_inductance_h/parameters.main_resistance_ohm,step_s)
    tau_a=max(parameters.auxiliary_inductance_h/parameters.auxiliary_resistance_ohm,step_s)
    omega=2*np.pi*parameters.frequency_hz
    def derivative(x,u):
        im,ia,w,_pos,temp=x
        active=not (parameters.family in {"split_phase","capacitor_start"} and w>=parameters.auxiliary_cutout_speed_pu*omega)
        dia=(ia_target-ia)/tau_a if active else -ia/tau_a
        torque=parameters.torque_constant_nm_per_a2*im*ia*np.sin(phase)
        if parameters.family=="shaded_pole": torque*=parameters.shaded_pole_torque_factor
        loss=parameters.main_resistance_ohm*im**2+(parameters.auxiliary_resistance_ohm*ia**2 if active else 0.)
        return np.asarray(((im_target-im)/tau_m,dia,(torque-u[0]-parameters.friction_n_m_s*w)/parameters.inertia_kg_m2,w,
            (loss-parameters.thermal_conductance_w_k*(temp-parameters.ambient_temperature_c))/parameters.thermal_capacity_j_k),dtype=np.float64)
    initial=np.asarray((0.,0.,parameters.initial_speed_rad_s,0.,parameters.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="single_phase_motor_rms_envelope_single",
        derivative=derivative,initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,
        input_at=lambda i,_t:(float(load[i]),),precision="float64")
    result=solve_numerical_problem(problem)
    if result.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected single-phase sample count")
    return SinglePhaseMotorSimulationResult(motor_id.strip(),result.time_s,result.state_data,voltage,load,result.sample_period_s,"cpu-rk4",
        {"integrator":result.integrator,"precision":"float64","workload":"single_phase_motor_rms_envelope_single",
         "model":"single_phase_asymmetric_winding_rms_envelope","family":parameters.family,
         "model_fidelity":"reduced_order_rms_envelope_not_cycle_resolved","integration_engine":"core.numerics.ode.solve_rk4"})


_UNIVERSAL_STATE_NAMES = ("current_a", "speed_rad_s", "position_rad", "temperature_c")


@dataclass(frozen=True)
class UniversalMotorParameters:
    armature_resistance_ohm: float
    field_resistance_ohm: float
    armature_inductance_h: float
    field_inductance_h: float
    inertia_kg_m2: float
    torque_constant_nm_per_a_wb: float
    back_emf_constant_v_s_per_rad_wb: float
    field_flux_per_ampere_wb_a: float
    saturation_current_a: float = 8.0
    frequency_hz: float = 50.0
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 6000.0
    thermal_conductance_w_k: float = 25.0
    ambient_temperature_c: float = 25.0
    initial_speed_rad_s: float = 0.0
    initial_temperature_c: float = 25.0
    supply_mode: str = "ac"

    def __post_init__(self):
        for name in ("armature_resistance_ohm","field_resistance_ohm","armature_inductance_h","field_inductance_h","inertia_kg_m2","torque_constant_nm_per_a_wb","back_emf_constant_v_s_per_rad_wb","field_flux_per_ampere_wb_a","saturation_current_a","thermal_capacity_j_k"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{name} must be finite and positive")
        for name in ("frequency_hz","friction_n_m_s","thermal_conductance_w_k","ambient_temperature_c","initial_speed_rad_s","initial_temperature_c"):
            v=getattr(self,name)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{name} must be finite and numeric")
        if self.frequency_hz<=0 or self.friction_n_m_s<0 or self.thermal_conductance_w_k<0: raise ValueError("frequency must be positive and passive parameters non-negative")
        if self.supply_mode not in {"ac","dc"}: raise ValueError("supply_mode must be 'ac' or 'dc'")


@dataclass(frozen=True)
class UniversalMotorSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    supply_voltage_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be non-empty")
        for name in ("time_s","state_data","supply_voltage_v","load_torque_nm"):
            v=np.asarray(getattr(self,name),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{name} must be finite")
            v.setflags(write=False); object.__setattr__(self,name,v)
        if self.state_data.size!=self.time_s.size*4 or self.supply_voltage_v.size!=self.time_s.size or self.load_torque_nm.size!=self.time_s.size: raise ValueError("universal motor result arrays must align")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))

    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _UNIVERSAL_STATE_NAMES
    def state(self,name):
        try: i=_UNIVERSAL_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        v=self.state_data.reshape(self.sample_count,4)[:,i].view(); v.setflags(write=False); return v
    def channels(self,p):
        m=self.state_data.reshape(self.sample_count,4); current,speed,position,temp=(m[:,i] for i in range(4))
        flux=p.field_flux_per_ampere_wb_a*p.saturation_current_a*np.tanh(current/p.saturation_current_a)
        torque=p.torque_constant_nm_per_a_wb*flux*current
        values={"terminal_voltage_v":self.supply_voltage_v,"line_current_a":current,"armature_current_a":current,
            "series_field_current_a":current,"field_flux_wb":flux,"electromagnetic_torque_nm":torque,
            "rotor_speed_rad_s":speed,"rotor_speed_rpm":speed*60/(2*np.pi),"rotor_position_rad":position,
            "winding_temperature_c":temp,"armature_copper_loss_w":p.armature_resistance_ohm*current**2,
            "field_copper_loss_w":p.field_resistance_ohm*current**2,"total_copper_loss_w":(p.armature_resistance_ohm+p.field_resistance_ohm)*current**2,
            "mechanical_output_power_w":torque*speed,"load_torque_nm":self.load_torque_nm}
        for v in values.values(): v.setflags(write=False)
        return values


def solve_universal_motor_transient_cpu(motor_id,parameters,duration_s,step_s,*,supply_voltage_v,load_torque_nm=0.0):
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be non-empty")
    if not isinstance(parameters,UniversalMotorParameters): raise TypeError("parameters must be UniversalMotorParameters")
    for name,value in (("supply_voltage_v",supply_voltage_v),("load_torque_nm",load_torque_nm)):
        if not _is_numeric_scalar(value) or not isfinite(float(value)): raise ValueError(f"{name} must be finite and numeric")
    if supply_voltage_v<0: raise ValueError("supply_voltage_v must be non-negative")
    count,_=_validate_time(duration_s,step_s)
    voltage=np.full(count,float(supply_voltage_v)); load=np.full(count,float(load_torque_nm))
    resistance=parameters.armature_resistance_ohm+parameters.field_resistance_ohm
    inductance=parameters.armature_inductance_h+parameters.field_inductance_h
    tau=max(inductance/resistance,step_s)
    drive=float(supply_voltage_v) if parameters.supply_mode=="dc" else float(supply_voltage_v)*2*np.sqrt(2)/np.pi
    def derivative(x,u):
        current,speed,_position,temp=x
        flux=parameters.field_flux_per_ampere_wb_a*parameters.saturation_current_a*np.tanh(current/parameters.saturation_current_a)
        torque=parameters.torque_constant_nm_per_a_wb*flux*current
        emf=parameters.back_emf_constant_v_s_per_rad_wb*flux*speed
        di=(drive-emf-resistance*current)/inductance
        copper=resistance*current**2
        return np.asarray((di,(torque-u[0]-parameters.friction_n_m_s*speed)/parameters.inertia_kg_m2,speed,
            (copper-parameters.thermal_conductance_w_k*(temp-parameters.ambient_temperature_c))/parameters.thermal_capacity_j_k),dtype=np.float64)
    initial=np.asarray((0.,parameters.initial_speed_rad_s,0.,parameters.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="universal_motor_series_field_single",derivative=derivative,
        initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,input_at=lambda i,_t:(float(load[i]),),precision="float64")
    result=solve_numerical_problem(problem)
    if result.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected universal motor sample count")
    return UniversalMotorSimulationResult(motor_id.strip(),result.time_s,result.state_data,voltage,load,result.sample_period_s,"cpu-rk4",
        {"integrator":result.integrator,"precision":"float64","workload":"universal_motor_series_field_single",
         "model":"series_commutator_motor_averaged_envelope","supply_mode":parameters.supply_mode,
         "model_fidelity":"averaged_rectified_envelope_not_cycle_resolved","integration_engine":"core.numerics.ode.solve_rk4"})


_WOUND_SYNC_STATE_NAMES = ("stator_d_current_a","stator_q_current_a","field_current_a","damper_d_current_a","damper_q_current_a","rotor_angle_rad","rotor_speed_rad_s","temperature_c")


@dataclass(frozen=True)
class WoundFieldSynchronousParameters:
    stator_resistance_ohm: float
    stator_d_inductance_h: float
    stator_q_inductance_h: float
    field_resistance_ohm: float
    field_inductance_h: float
    damper_d_resistance_ohm: float
    damper_q_resistance_ohm: float
    damper_d_inductance_h: float
    damper_q_inductance_h: float
    mutual_field_inductance_h: float
    mutual_damper_d_inductance_h: float
    mutual_damper_q_inductance_h: float
    pole_pairs: int
    inertia_kg_m2: float
    friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 12000.0
    thermal_conductance_w_k: float = 40.0
    ambient_temperature_c: float = 25.0
    initial_speed_rad_s: float = 0.0
    initial_temperature_c: float = 25.0
    frequency_hz: float = 50.0

    def __post_init__(self):
        for n in ("stator_resistance_ohm","stator_d_inductance_h","stator_q_inductance_h","field_resistance_ohm","field_inductance_h","damper_d_resistance_ohm","damper_q_resistance_ohm","damper_d_inductance_h","damper_q_inductance_h","inertia_kg_m2","thermal_capacity_j_k","frequency_hz"):
            v=getattr(self,n)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{n} must be finite and positive")
        for n in ("mutual_field_inductance_h","mutual_damper_d_inductance_h","mutual_damper_q_inductance_h"):
            v=getattr(self,n)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<0: raise ValueError(f"{n} must be finite and non-negative")
        for n in ("friction_n_m_s","thermal_conductance_w_k","ambient_temperature_c","initial_speed_rad_s","initial_temperature_c"):
            v=getattr(self,n)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{n} must be finite and numeric")
        if self.friction_n_m_s<0 or self.thermal_conductance_w_k<0: raise ValueError("passive parameters must be non-negative")
        if isinstance(self.pole_pairs,bool) or not isinstance(self.pole_pairs,int) or self.pole_pairs<1: raise ValueError("pole_pairs must be a positive integer")
        if self.mutual_field_inductance_h**2>=self.stator_d_inductance_h*self.field_inductance_h: raise ValueError("field mutual inductance violates positive-energy bound")
        if self.mutual_damper_d_inductance_h**2>=self.stator_d_inductance_h*self.damper_d_inductance_h: raise ValueError("d-axis damper mutual inductance violates positive-energy bound")
        if self.mutual_damper_q_inductance_h**2>=self.stator_q_inductance_h*self.damper_q_inductance_h: raise ValueError("q-axis damper mutual inductance violates positive-energy bound")


@dataclass(frozen=True)
class WoundFieldSynchronousSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    stator_voltage_d_v: np.ndarray
    stator_voltage_q_v: np.ndarray
    field_voltage_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be non-empty")
        for n in ("time_s","state_data","stator_voltage_d_v","stator_voltage_q_v","field_voltage_v","load_torque_nm"):
            v=np.asarray(getattr(self,n),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{n} must be finite")
            v.setflags(write=False); object.__setattr__(self,n,v)
        n=self.time_s.size
        if self.state_data.size!=n*8 or any(getattr(self,k).size!=n for k in ("stator_voltage_d_v","stator_voltage_q_v","field_voltage_v","load_torque_nm")): raise ValueError("synchronous result arrays must align")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))
    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return _WOUND_SYNC_STATE_NAMES
    def state(self,name):
        try: i=_WOUND_SYNC_STATE_NAMES.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        v=self.state_data.reshape(self.sample_count,8)[:,i].view(); v.setflags(write=False); return v
    def channels(self,p):
        x=self.state_data.reshape(self.sample_count,8); id_,iq,iff,ikd,ikq,angle,speed,temp=x.T
        torque=1.5*p.pole_pairs*((p.stator_d_inductance_h-p.stator_q_inductance_h)*id_*iq+p.mutual_field_inductance_h*iff*iq)
        vals={"stator_d_current_a":id_,"stator_q_current_a":iq,"field_current_a":iff,"damper_d_current_a":ikd,"damper_q_current_a":ikq,
          "rotor_angle_rad":angle,"rotor_speed_rad_s":speed,"rotor_speed_rpm":speed*60/(2*np.pi),
          "synchronous_speed_error_rad_s":speed-2*np.pi*p.frequency_hz/p.pole_pairs,"electromagnetic_torque_nm":torque,
          "winding_temperature_c":temp,"stator_copper_loss_w":1.5*p.stator_resistance_ohm*(id_**2+iq**2),
          "field_copper_loss_w":p.field_resistance_ohm*iff**2,"damper_copper_loss_w":p.damper_d_resistance_ohm*ikd**2+p.damper_q_resistance_ohm*ikq**2,
          "mechanical_output_power_w":torque*speed,"load_torque_nm":self.load_torque_nm,"field_voltage_v":self.field_voltage_v,
          "stator_voltage_d_v":self.stator_voltage_d_v,"stator_voltage_q_v":self.stator_voltage_q_v}
        for v in vals.values(): v.setflags(write=False)
        return vals


def solve_wound_field_synchronous_transient_cpu(motor_id,parameters,duration_s,step_s,*,stator_voltage_d_v,stator_voltage_q_v,field_voltage_v,load_torque_nm=0.0):
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be non-empty")
    if not isinstance(parameters,WoundFieldSynchronousParameters): raise TypeError("parameters must be WoundFieldSynchronousParameters")
    count,_=_validate_time(duration_s,step_s); p=parameters
    profiles={}
    for n,v in (("stator_voltage_d_v",stator_voltage_d_v),("stator_voltage_q_v",stator_voltage_q_v),("field_voltage_v",field_voltage_v),("load_torque_nm",load_torque_nm)):
        if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{n} must be finite and numeric")
        profiles[n]=np.full(count,float(v))
    def deriv(x,u):
        id_,iq,iff,ikd,ikq,angle,speed,temp=x
        slip=speed-2*np.pi*p.frequency_hz/p.pole_pairs
        psid=p.stator_d_inductance_h*id_+p.mutual_field_inductance_h*iff+p.mutual_damper_d_inductance_h*ikd
        psiq=p.stator_q_inductance_h*iq+p.mutual_damper_q_inductance_h*ikq
        did=(u[0]-p.stator_resistance_ohm*id_+slip*psiq)/p.stator_d_inductance_h
        diq=(u[1]-p.stator_resistance_ohm*iq-slip*psid)/p.stator_q_inductance_h
        diff=(u[2]-p.field_resistance_ohm*iff)/p.field_inductance_h
        dikd=-p.damper_d_resistance_ohm*ikd/p.damper_d_inductance_h
        dikq=-p.damper_q_resistance_ohm*ikq/p.damper_q_inductance_h
        torque=1.5*p.pole_pairs*((p.stator_d_inductance_h-p.stator_q_inductance_h)*id_*iq+p.mutual_field_inductance_h*iff*iq)
        loss=1.5*p.stator_resistance_ohm*(id_**2+iq**2)+p.field_resistance_ohm*iff**2+p.damper_d_resistance_ohm*ikd**2+p.damper_q_resistance_ohm*ikq**2
        return np.asarray((did,diq,diff,dikd,dikq,speed,(torque-u[3]-p.friction_n_m_s*speed)/p.inertia_kg_m2,(loss-p.thermal_conductance_w_k*(temp-p.ambient_temperature_c))/p.thermal_capacity_j_k),dtype=np.float64)
    initial=np.asarray((0.,0.,0.,0.,0.,0.,p.initial_speed_rad_s,p.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="wound_field_synchronous_damper_single",derivative=deriv,initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,
      input_at=lambda i,_t:(profiles["stator_voltage_d_v"][i],profiles["stator_voltage_q_v"][i],profiles["field_voltage_v"][i],profiles["load_torque_nm"][i]),precision="float64")
    r=solve_numerical_problem(problem)
    if r.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected synchronous motor sample count")
    return WoundFieldSynchronousSimulationResult(motor_id.strip(),r.time_s,r.state_data,profiles["stator_voltage_d_v"],profiles["stator_voltage_q_v"],profiles["field_voltage_v"],profiles["load_torque_nm"],r.sample_period_s,"cpu-rk4",
      {"integrator":r.integrator,"precision":"float64","workload":"wound_field_synchronous_damper_single","model":"experimental_rotor_dq_wound_field_damper_envelope","model_fidelity":"damper_induced_emf_and_full_flux_matrix_not_resolved","integration_engine":"core.numerics.ode.solve_rk4"})


@dataclass(frozen=True)
class HysteresisMotorParameters:
    """Averaged synchronous torque-angle approximation for a hysteresis motor."""
    pole_pairs: int
    rated_voltage_rms_v: float
    frequency_hz: float
    pullout_torque_nm: float
    inertia_kg_m2: float
    viscous_friction_n_m_s: float = 0.0
    thermal_capacity_j_k: float = 8000.0
    thermal_conductance_w_k: float = 25.0
    rotor_loss_fraction: float = 0.08
    ambient_temperature_c: float = 25.0
    initial_speed_rad_s: float = 0.0
    initial_load_angle_rad: float = 0.0
    initial_temperature_c: float = 25.0

    def __post_init__(self):
        if isinstance(self.pole_pairs,bool) or not isinstance(self.pole_pairs,int) or self.pole_pairs<1: raise ValueError("pole_pairs must be a positive integer")
        for n in ("rated_voltage_rms_v","frequency_hz","pullout_torque_nm","inertia_kg_m2","thermal_capacity_j_k"):
            v=getattr(self,n)
            if not _is_numeric_scalar(v) or not isfinite(float(v)) or v<=0: raise ValueError(f"{n} must be finite and positive")
        for n in ("viscous_friction_n_m_s","thermal_conductance_w_k","rotor_loss_fraction","ambient_temperature_c","initial_speed_rad_s","initial_load_angle_rad","initial_temperature_c"):
            v=getattr(self,n)
            if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{n} must be finite and numeric")
        if self.viscous_friction_n_m_s<0 or self.thermal_conductance_w_k<0 or not 0<=self.rotor_loss_fraction<=1: raise ValueError("friction/conductance must be non-negative and rotor_loss_fraction must be in [0,1]")


@dataclass(frozen=True)
class HysteresisMotorSimulationResult:
    motor_id: str
    time_s: np.ndarray
    state_data: np.ndarray
    supply_voltage_rms_v: np.ndarray
    load_torque_nm: np.ndarray
    sample_period_s: float
    backend: str
    diagnostics: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.motor_id,str) or not self.motor_id.strip(): raise ValueError("motor_id must be non-empty")
        for n in ("time_s","state_data","supply_voltage_rms_v","load_torque_nm"):
            v=np.asarray(getattr(self,n),dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(v).all(): raise ValueError(f"{n} must be finite")
            v.setflags(write=False); object.__setattr__(self,n,v)
        n=self.time_s.size
        if self.state_data.size!=n*4 or self.supply_voltage_rms_v.size!=n or self.load_torque_nm.size!=n: raise ValueError("hysteresis result arrays must align")
        object.__setattr__(self,"diagnostics",MappingProxyType(dict(self.diagnostics)))
    @property
    def sample_count(self): return int(self.time_s.size)
    @property
    def state_names(self): return ("rotor_speed_rad_s","rotor_electrical_angle_rad","load_angle_rad","rotor_temperature_c")
    def state(self,name):
        try: i=self.state_names.index(name)
        except ValueError as exc: raise KeyError(name) from exc
        v=self.state_data.reshape(self.sample_count,4)[:,i].view(); v.setflags(write=False); return v
    def channels(self,p):
        speed,rotor_angle,delta,temp=self.state_data.reshape(self.sample_count,4).T
        voltage_ratio=self.supply_voltage_rms_v/p.rated_voltage_rms_v
        torque=p.pullout_torque_nm*np.minimum(voltage_ratio**2,1.5)*np.sin(delta)
        sync_speed=2*np.pi*p.frequency_hz/p.pole_pairs
        margin=p.pullout_torque_nm*np.minimum(voltage_ratio**2,1.5)-np.abs(self.load_torque_nm)
        vals={"rotor_speed_rad_s":speed,"rotor_speed_rpm":speed*60/(2*np.pi),"rotor_electrical_angle_rad":rotor_angle,
          "load_angle_rad":delta,"electromagnetic_torque_nm":torque,"synchronous_speed_error_rad_s":speed-sync_speed,
          "pullout_torque_margin_nm":margin,"supply_voltage_rms_v":self.supply_voltage_rms_v,
          "rotor_loss_w":p.rotor_loss_fraction*np.abs(torque*speed),"rotor_temperature_c":temp,
          "load_torque_nm":self.load_torque_nm,"mechanical_output_power_w":torque*speed}
        for v in vals.values(): v.setflags(write=False)
        return vals


def solve_hysteresis_motor_transient_cpu(motor_id,parameters,duration_s,step_s,*,supply_voltage_rms_v,load_torque_nm=0.0):
    if not isinstance(motor_id,str) or not motor_id.strip(): raise ValueError("motor_id must be non-empty")
    if not isinstance(parameters,HysteresisMotorParameters): raise TypeError("parameters must be HysteresisMotorParameters")
    count,_=_validate_time(duration_s,step_s); p=parameters
    profiles={}
    for n,v in (("supply_voltage_rms_v",supply_voltage_rms_v),("load_torque_nm",load_torque_nm)):
        if not _is_numeric_scalar(v) or not isfinite(float(v)): raise ValueError(f"{n} must be finite and numeric")
        if n=="supply_voltage_rms_v" and v<0: raise ValueError("supply_voltage_rms_v must be non-negative")
        profiles[n]=np.full(count,float(v))
    sync=2*np.pi*p.frequency_hz/p.pole_pairs
    def deriv(x,u):
        speed,rotor_angle,delta,temp=x
        ratio=min(u[0]/p.rated_voltage_rms_v,np.sqrt(1.5))
        torque=p.pullout_torque_nm*ratio**2*np.sin(delta)
        loss=p.rotor_loss_fraction*abs(torque*speed)
        return np.asarray(((torque-u[1]-p.viscous_friction_n_m_s*speed)/p.inertia_kg_m2,
          p.pole_pairs*speed,sync-p.pole_pairs*speed,
          (loss-p.thermal_conductance_w_k*(temp-p.ambient_temperature_c))/p.thermal_capacity_j_k),dtype=np.float64)
    initial=np.asarray((p.initial_speed_rad_s,p.pole_pairs*p.initial_speed_rad_s*0.0,p.initial_load_angle_rad,p.initial_temperature_c))
    problem=NumericalProblem(problem_id=motor_id.strip(),workload="hysteresis_motor_torque_angle_single",derivative=deriv,
      initial_state=initial,duration_s=duration_s,maximum_step_s=step_s,
      input_at=lambda i,_t:(profiles["supply_voltage_rms_v"][i],profiles["load_torque_nm"][i]),precision="float64")
    r=solve_numerical_problem(problem)
    if r.sample_count!=count: raise RuntimeError("shared ODE engine returned an unexpected hysteresis motor sample count")
    return HysteresisMotorSimulationResult(motor_id.strip(),r.time_s,r.state_data,profiles["supply_voltage_rms_v"],profiles["load_torque_nm"],r.sample_period_s,"cpu-rk4",
      {"integrator":r.integrator,"precision":"float64","workload":"hysteresis_motor_torque_angle_single","model":"averaged_synchronous_torque_angle","model_fidelity":"not_a_material_hysteresis_or_asynchronous_pullin_model","synchronous_speed_rad_s":sync,"integration_engine":"core.numerics.ode.solve_rk4"})
