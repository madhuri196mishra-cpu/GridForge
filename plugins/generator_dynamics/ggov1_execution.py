"""Stateless sampled GGOV1 block composition for the host solver.

The adapter evaluates one host-requested sample from caller-owned state
snapshots. It owns no clock, integration, persistent state, transport-delay
history, DAE vector, Application service, or lifecycle. The caller commits
returned derivatives to its solver and supplies the next sampled FSR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .controllers import GGOV1Parameters, _number
from .ggov1_acceleration import GGOV1AccelerationLimiter
from .ggov1_actuator import GGOV1ValveActuator
from .ggov1_load_limiter import GGOV1LoadLimiter
from .ggov1_pid import GGOV1PIDGovernor
from .ggov1_selector import GGOV1ControlCoordinator, GGOV1ControlDecision
from .ggov1_temperature import GGOV1TemperatureMeasurement
from .ggov1_turbine import GGOV1TurbineFuelPath


@dataclass(frozen=True, slots=True)
class GGOV1SampleStates:
    """One immutable snapshot of solver-owned local state vectors."""

    pid: tuple[float, ...]
    acceleration: tuple[float, ...]
    temperature: tuple[float, ...]
    load_limiter: tuple[float, ...]
    actuator: tuple[float, ...]
    turbine: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class GGOV1SampleInputs:
    """External signals and previous-sample feedback for one GGOV1 evaluation."""

    electrical_power_pu: float
    speed_deviation_pu: float
    power_reference_pu: float
    supervisory_power_setpoint_pu: float
    sample_interval_s: float
    previous_selected_fuel_command_pu: float
    governor_output_feedback_pu: float
    delayed_fuel_flow_pu: float | None = None


@dataclass(frozen=True, slots=True)
class GGOV1SampleDerivatives:
    """Local derivative vectors returned to the host integrator."""

    pid: tuple[float, ...]
    acceleration: tuple[float, ...]
    temperature: tuple[float, ...]
    load_limiter: tuple[float, ...]
    actuator: tuple[float, ...]
    turbine: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class GGOV1SampleEvaluation:
    """Immutable algebraic outputs, command decision and local derivatives."""

    decision: GGOV1ControlDecision
    derivatives: GGOV1SampleDerivatives
    governor_request_pu: float
    acceleration_request_pu: float
    load_temperature_request_pu: float
    actual_valve_position_pu: float
    temperature_proxy_input_pu: float
    exhaust_temperature_proxy_pu: float
    mechanical_power_pu: float


class GGOV1SampledExecutionAdapter:
    """Compose the existing GGOV1 blocks for one explicit host sample.

    The sample order is: read actual valve position; calculate the un-delayed
    temperature proxy and conditioned TEXM; evaluate governor and load
    requests; calculate FSRA from the *previous* selected FSR and host DELT;
    select and apply common FSR bounds; then evaluate all continuous-time
    derivatives using the resulting common command for PID/load tracking and
    actuator input. The returned actuator position derivative is not applied
    until the host solver advances its state.

    Rselect=-2 feedback remains an explicit previous/algebraic host input.
    Positive Teng requires a delayed fuel-flow signal from the host solver's
    delay facility. This adapter does not resolve either dependency itself.
    """

    def __init__(
        self,
        parameters: GGOV1Parameters,
        *,
        pid: GGOV1PIDGovernor,
        acceleration: GGOV1AccelerationLimiter,
        temperature: GGOV1TemperatureMeasurement,
        load_limiter: GGOV1LoadLimiter,
        actuator: GGOV1ValveActuator,
        turbine: GGOV1TurbineFuelPath,
        pid_tracking_time_constant_s: float,
        load_tracking_time_constant_s: float,
    ) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        blocks = (pid, acceleration, temperature, load_limiter, actuator, turbine)
        expected = (
            GGOV1PIDGovernor, GGOV1AccelerationLimiter,
            GGOV1TemperatureMeasurement, GGOV1LoadLimiter,
            GGOV1ValveActuator, GGOV1TurbineFuelPath,
        )
        for block, block_type in zip(blocks, expected):
            if not isinstance(block, block_type):
                raise TypeError(f"{block_type.__name__} instance is required")
            if block.parameters != parameters:
                raise ValueError("all GGOV1 blocks must use the same parameter contract")
        self.parameters = parameters
        self.coordinator = GGOV1ControlCoordinator(parameters)
        self.pid = pid
        self.acceleration = acceleration
        self.temperature = temperature
        self.load_limiter = load_limiter
        self.actuator = actuator
        self.turbine = turbine
        self.pid_tracking_time_constant_s = _number(
            pid_tracking_time_constant_s, "pid_tracking_time_constant_s"
        )
        self.load_tracking_time_constant_s = _number(
            load_tracking_time_constant_s, "load_tracking_time_constant_s"
        )
        if self.pid_tracking_time_constant_s <= 0.0 or self.load_tracking_time_constant_s <= 0.0:
            raise ValueError("controller tracking time constants must be greater than zero")

    def evaluate_sample(
        self,
        states: GGOV1SampleStates,
        inputs: GGOV1SampleInputs,
    ) -> GGOV1SampleEvaluation:
        """Evaluate one sample without mutating states or advancing time."""
        if not isinstance(states, GGOV1SampleStates):
            raise TypeError("states must be GGOV1SampleStates")
        if not isinstance(inputs, GGOV1SampleInputs):
            raise TypeError("inputs must be GGOV1SampleInputs")

        pelec = _number(inputs.electrical_power_pu, "electrical_power_pu")
        speed = _number(inputs.speed_deviation_pu, "speed_deviation_pu")
        shaft_speed = 1.0 + speed
        if shaft_speed <= 0.0:
            raise ValueError("absolute shaft speed must be greater than zero")
        power_reference = _number(inputs.power_reference_pu, "power_reference_pu")
        supervisory_setpoint = _number(
            inputs.supervisory_power_setpoint_pu, "supervisory_power_setpoint_pu"
        )
        sample_interval = _number(inputs.sample_interval_s, "sample_interval_s")
        if sample_interval <= 0.0:
            raise ValueError("sample_interval_s must be greater than zero")
        previous_fsr = _number(
            inputs.previous_selected_fuel_command_pu,
            "previous_selected_fuel_command_pu",
        )
        if not self.parameters.valve_min_pu <= previous_fsr <= self.parameters.valve_max_pu:
            raise ValueError("previous selected FSR feedback is outside common valve limits")
        governor_feedback = _number(
            inputs.governor_output_feedback_pu, "governor_output_feedback_pu"
        )

        # Actual valve position is read from the solver-owned actuator state.
        valve_position = self.actuator.output(states.actuator)
        temperature_input = self.turbine.temperature_proxy_input(
            valve_stroke_pu=valve_position,
            speed_deviation_pu=speed,
        )
        texm = self.temperature.output(
            states.temperature,
            temperature_proxy_input_pu=temperature_input,
        )

        governor_request = self.pid.output(
            states.pid,
            speed_deviation_pu=speed,
            power_reference_pu=power_reference,
            valve_stroke_pu=valve_position,
            governor_output_pu=governor_feedback,
            electrical_power_pu=pelec,
        )
        load_request = self.load_limiter.output(
            states.load_limiter,
            exhaust_temperature_pu=texm,
        )
        acceleration_request = self.acceleration.output(
            states.acceleration,
            speed_deviation_pu=speed,
            selected_fuel_command_pu=previous_fsr,
            sample_interval_s=sample_interval,
        )
        decision = self.coordinator.coordinate({
            "governor": governor_request,
            "acceleration": acceleration_request,
            "load_temperature": load_request,
        })
        command = decision.actuator_command_pu

        derivatives = GGOV1SampleDerivatives(
            pid=self.pid.derivatives(
                states.pid,
                electrical_power_pu=pelec,
                speed_deviation_pu=speed,
                power_reference_pu=power_reference,
                supervisory_power_setpoint_pu=supervisory_setpoint,
                valve_stroke_pu=valve_position,
                governor_output_pu=governor_feedback,
                selected_fuel_command_pu=command,
                tracking_time_constant_s=self.pid_tracking_time_constant_s,
            ),
            acceleration=self.acceleration.derivatives(
                states.acceleration, speed_deviation_pu=speed
            ),
            temperature=self.temperature.derivatives(
                states.temperature, temperature_proxy_input_pu=temperature_input
            ),
            load_limiter=self.load_limiter.derivatives(
                states.load_limiter,
                exhaust_temperature_pu=texm,
                selected_fuel_command_pu=command,
                tracking_time_constant_s=self.load_tracking_time_constant_s,
            ),
            actuator=self.actuator.derivatives(
                states.actuator, governor_fuel_command_pu=command
            ),
            turbine=self.turbine.derivatives(
                states.turbine,
                valve_stroke_pu=valve_position,
                shaft_speed_pu=shaft_speed,
                delayed_fuel_flow_pu=inputs.delayed_fuel_flow_pu,
            ),
        )
        mechanical_power = self.turbine.output(
            states.turbine,
            valve_stroke_pu=valve_position,
            shaft_speed_pu=shaft_speed,
            speed_deviation_pu=speed,
            delayed_fuel_flow_pu=inputs.delayed_fuel_flow_pu,
        )
        return GGOV1SampleEvaluation(
            decision=decision,
            derivatives=derivatives,
            governor_request_pu=governor_request,
            acceleration_request_pu=acceleration_request,
            load_temperature_request_pu=load_request,
            actual_valve_position_pu=valve_position,
            temperature_proxy_input_pu=temperature_input,
            exhaust_temperature_proxy_pu=texm,
            mechanical_power_pu=mechanical_power,
        )


__all__ = [
    "GGOV1SampleStates",
    "GGOV1SampleInputs",
    "GGOV1SampleDerivatives",
    "GGOV1SampleEvaluation",
    "GGOV1SampledExecutionAdapter",
]
