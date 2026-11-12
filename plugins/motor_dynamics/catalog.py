"""Validated electric-motor family catalogue.

A catalogue entry is not a claim that a family has a validated transient
solver. Numerical model availability is explicit in each family profile.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Mapping


class MotorFamily(str, Enum):
    THREE_PHASE_SQUIRREL_CAGE_INDUCTION = "three_phase_squirrel_cage_induction"
    THREE_PHASE_WOUND_ROTOR_INDUCTION = "three_phase_wound_rotor_induction"
    SINGLE_PHASE_SPLIT_PHASE = "single_phase_split_phase"
    SINGLE_PHASE_CAPACITOR_START = "single_phase_capacitor_start"
    SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR = "single_phase_permanent_split_capacitor"
    SINGLE_PHASE_CAPACITOR_START_RUN = "single_phase_capacitor_start_run"
    SINGLE_PHASE_SHADED_POLE = "single_phase_shaded_pole"
    WOUND_FIELD_SYNCHRONOUS = "wound_field_synchronous"
    PERMANENT_MAGNET_SYNCHRONOUS = "permanent_magnet_synchronous"
    BRUSHLESS_DC = "brushless_dc"
    SYNCHRONOUS_RELUCTANCE = "synchronous_reluctance"
    SWITCHED_RELUCTANCE = "switched_reluctance"
    DC_SHUNT = "dc_shunt"
    DC_SERIES = "dc_series"
    DC_COMPOUND = "dc_compound"
    DC_SEPARATELY_EXCITED = "dc_separately_excited"
    DC_PERMANENT_MAGNET = "dc_permanent_magnet"
    UNIVERSAL = "universal"
    STEPPER_VARIABLE_RELUCTANCE = "stepper_variable_reluctance"
    STEPPER_PERMANENT_MAGNET = "stepper_permanent_magnet"
    STEPPER_HYBRID = "stepper_hybrid"
    AC_SERVO = "ac_servo"
    DC_SERVO = "dc_servo"
    LINEAR_INDUCTION = "linear_induction"
    LINEAR_SYNCHRONOUS = "linear_synchronous"
    VOICE_COIL = "voice_coil"
    TRACTION_INDUCTION = "traction_induction"
    TRACTION_PERMANENT_MAGNET = "traction_permanent_magnet"
    HYSTERESIS = "hysteresis"
    TORQUE = "torque"


class MotorOperatingMode(str, Enum):
    CONSTANT_SPEED = "constant_speed"
    VARIABLE_SPEED = "variable_speed"
    CONSTANT_TORQUE = "constant_torque"
    CONSTANT_POWER = "constant_power"
    POSITION_CONTROL = "position_control"
    REGENERATIVE = "regenerative"
    PLUGGING = "plugging"
    DYNAMIC_BRAKING = "dynamic_braking"
    LINEAR_THRUST = "linear_thrust"


@dataclass(frozen=True, slots=True)
class MotorFamilyProfile:
    family: MotorFamily
    supply: str
    phase_count: int
    principle: str
    typical_drive: str
    dynamic_model: str | None
    model_status: str
    regenerative_capable: bool
    notes: str = ""


def _profile(family: MotorFamily, supply: str, phases: int, principle: str,
             drive: str, *, dynamic_model: str | None = None,
             regenerative: bool = False, notes: str = "") -> MotorFamilyProfile:
    status = {"induction_dq": "experimental_induction_dq", "pmsm_dq": "experimental_pmsm_dq", "bldc_trapezoidal": "experimental_bldc_trapezoidal", "synrm_dq": "experimental_synrm_dq", "srm_phase_inductance": "experimental_srm_phase_inductance", "dc_excitation": "experimental_dc_excitation", "single_phase_rms_envelope": "experimental_single_phase_rms_envelope", "pmsm_servo_cascade": "experimental_pmsm_servo_cascade", "induction_servo_foc": "experimental_induction_servo_foc", "dc_servo_cascade": "experimental_dc_servo_cascade", "stepper_two_phase_current_control": "experimental_stepper_two_phase_current_control", "linear_induction_dq": "experimental_linear_induction_dq", "linear_synchronous_dq": "experimental_linear_synchronous_dq", "voice_coil_actuator": "experimental_voice_coil_actuator"}.get(dynamic_model, "catalogued_model_required")
    return MotorFamilyProfile(family, supply, phases, principle, drive, dynamic_model, status, regenerative, notes)


MOTOR_FAMILY_PROFILES: Mapping[MotorFamily, MotorFamilyProfile] = MappingProxyType({
    MotorFamily.THREE_PHASE_SQUIRREL_CAGE_INDUCTION: _profile(MotorFamily.THREE_PHASE_SQUIRREL_CAGE_INDUCTION, "AC", 3, "asynchronous squirrel-cage induction", "DOL / soft starter / VFD", dynamic_model="induction_dq", regenerative=True, notes="Existing dq solver is experimental; saturation and unbalance are not covered."),
    MotorFamily.THREE_PHASE_WOUND_ROTOR_INDUCTION: _profile(MotorFamily.THREE_PHASE_WOUND_ROTOR_INDUCTION, "AC", 3, "asynchronous wound-rotor induction", "rotor-resistance starter / VFD", dynamic_model="induction_dq", regenerative=True, notes="Experimental CPU dq model supports a fixed external rotor resistance; slip-ring contact and separate resistor thermal dynamics remain unmodeled."),
    MotorFamily.SINGLE_PHASE_SPLIT_PHASE: _profile(MotorFamily.SINGLE_PHASE_SPLIT_PHASE, "AC", 1, "single-phase split-phase induction", "line / starter", dynamic_model="single_phase_rms_envelope"),
    MotorFamily.SINGLE_PHASE_CAPACITOR_START: _profile(MotorFamily.SINGLE_PHASE_CAPACITOR_START, "AC", 1, "capacitor-start induction", "line / starter", dynamic_model="single_phase_rms_envelope"),
    MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR: _profile(MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR, "AC", 1, "permanent-split-capacitor induction", "line / VFD", dynamic_model="single_phase_rms_envelope"),
    MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN: _profile(MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN, "AC", 1, "capacitor-start/capacitor-run induction", "line / starter", dynamic_model="single_phase_rms_envelope"),
    MotorFamily.SINGLE_PHASE_SHADED_POLE: _profile(MotorFamily.SINGLE_PHASE_SHADED_POLE, "AC", 1, "shaded-pole induction", "line", dynamic_model="single_phase_rms_envelope"),
    MotorFamily.WOUND_FIELD_SYNCHRONOUS: _profile(MotorFamily.WOUND_FIELD_SYNCHRONOUS, "AC", 3, "DC-excited synchronous", "VFD / damper-start / exciter", dynamic_model="wound_field_synchronous_damper", regenerative=True, notes="Experimental CPU rotor-dq model with separately supplied field voltage and simplified damper-current envelopes; damper induced EMF and complete flux coupling are not resolved."),
    MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS: _profile(MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS, "AC", 3, "surface/interior PM synchronous", "FOC / inverter", dynamic_model="pmsm_dq", regenerative=True, notes="Experimental CPU dq transient model; linear magnetic parameters and no PWM switching."),
    MotorFamily.BRUSHLESS_DC: _profile(MotorFamily.BRUSHLESS_DC, "DC link", 3, "electronically commutated PM", "six-step / FOC", dynamic_model="bldc_trapezoidal", regenerative=True, notes="Experimental CPU phase-domain model with trapezoidal back-EMF and averaged six-step inverter commands; PWM switching and closed-loop commutation are not modeled."),
    MotorFamily.SYNCHRONOUS_RELUCTANCE: _profile(MotorFamily.SYNCHRONOUS_RELUCTANCE, "AC", 3, "synchronous reluctance", "VFD / vector control", dynamic_model="synrm_dq", regenerative=True, notes="Experimental CPU rotor-dq model with saliency torque; linear inductances and no saturation or iron loss."),
    MotorFamily.SWITCHED_RELUCTANCE: _profile(MotorFamily.SWITCHED_RELUCTANCE, "DC link", 3, "switched reluctance", "asymmetric bridge converter", dynamic_model="srm_phase_inductance", regenerative=True, notes="Experimental CPU three-phase position-dependent inductance model with approximate current saturation; converter switching and current chopping are not modeled."),
    MotorFamily.DC_SHUNT: _profile(MotorFamily.DC_SHUNT, "DC", 2, "shunt-wound brushed DC", "DC drive", dynamic_model="dc_excitation"),
    MotorFamily.DC_SERIES: _profile(MotorFamily.DC_SERIES, "DC", 2, "series-wound brushed DC", "DC drive", dynamic_model="dc_excitation"),
    MotorFamily.DC_COMPOUND: _profile(MotorFamily.DC_COMPOUND, "DC", 2, "compound-wound brushed DC", "DC drive", dynamic_model="dc_excitation"),
    MotorFamily.DC_SEPARATELY_EXCITED: _profile(MotorFamily.DC_SEPARATELY_EXCITED, "DC", 2, "separately excited brushed DC", "four-quadrant DC drive", dynamic_model="dc_excitation", regenerative=True),
    MotorFamily.DC_PERMANENT_MAGNET: _profile(MotorFamily.DC_PERMANENT_MAGNET, "DC", 2, "permanent-magnet brushed DC", "PWM DC drive", dynamic_model="dc_excitation", regenerative=True),
    MotorFamily.UNIVERSAL: _profile(MotorFamily.UNIVERSAL, "AC/DC", 1, "series-wound commutator", "line / phase-angle / DC drive", dynamic_model="universal_series_commutator", notes="Experimental CPU averaged AC/DC series-field envelope; commutation ripple, brush contact and cycle-resolved AC are omitted."),
    MotorFamily.STEPPER_VARIABLE_RELUCTANCE: _profile(MotorFamily.STEPPER_VARIABLE_RELUCTANCE, "DC link", 2, "variable-reluctance stepper", "stepper current driver", dynamic_model="stepper_two_phase_current_control", notes="Experimental CPU averaged two-phase current/motion model; switched driver, nonlinear flux maps and certified missed-step prediction are not modelled."),
    MotorFamily.STEPPER_PERMANENT_MAGNET: _profile(MotorFamily.STEPPER_PERMANENT_MAGNET, "DC link", 2, "permanent-magnet stepper", "stepper current driver", dynamic_model="stepper_two_phase_current_control", notes="Experimental CPU averaged two-phase current/motion model; switched driver, nonlinear flux maps and certified missed-step prediction are not modelled."),
    MotorFamily.STEPPER_HYBRID: _profile(MotorFamily.STEPPER_HYBRID, "DC link", 2, "hybrid stepper", "microstepping current driver", dynamic_model="stepper_two_phase_current_control", notes="Experimental CPU averaged two-phase current/motion model; switched driver, nonlinear flux maps and certified missed-step prediction are not modelled."),
    MotorFamily.AC_SERVO: _profile(MotorFamily.AC_SERVO, "AC / DC link", 3, "synchronous PM or induction servo", "servo inverter / FOC with encoder/resolver feedback", dynamic_model=None, regenerative=True, notes="Technology-specific servo solver selection is explicit: experimental PMSM cascade or separate experimental induction rotor-flux FOC CPU model; do not infer one technology from the family label."),
    MotorFamily.DC_SERVO: _profile(MotorFamily.DC_SERVO, "DC", 2, "brushed DC servo", "four-quadrant servo drive", dynamic_model="dc_servo_cascade", regenerative=True, notes="Experimental CPU position/speed/current cascade for explicitly configured brushed-DC excitation machines; averaged drive and commutation approximation."),
    MotorFamily.LINEAR_INDUCTION: _profile(MotorFamily.LINEAR_INDUCTION, "AC", 3, "linear induction travelling field", "linear VFD", dynamic_model="linear_induction_dq", regenerative=True, notes="Experimental travelling-field dq model; end effects and nonlinear secondary flux are not resolved."),
    MotorFamily.LINEAR_SYNCHRONOUS: _profile(MotorFamily.LINEAR_SYNCHRONOUS, "AC / DC link", 3, "linear permanent-magnet synchronous", "linear servo inverter", dynamic_model="linear_synchronous_dq", regenerative=True, notes="Experimental PM d/q thrust model; segmented magnet and end-effect fields are not resolved."),
    MotorFamily.VOICE_COIL: _profile(MotorFamily.VOICE_COIL, "DC / current drive", 1, "moving-coil Lorentz-force linear actuator", "current-regulated linear amplifier", dynamic_model="voice_coil_actuator", regenerative=True, notes="Experimental lumped R-L/back-EMF actuator model with suspension, stroke and thermal dynamics."),
    MotorFamily.TRACTION_INDUCTION: _profile(MotorFamily.TRACTION_INDUCTION, "AC / DC link", 3, "traction induction", "traction inverter / FOC", regenerative=True),
    MotorFamily.TRACTION_PERMANENT_MAGNET: _profile(MotorFamily.TRACTION_PERMANENT_MAGNET, "AC / DC link", 3, "traction PMSM / IPM", "traction inverter / FOC", dynamic_model="pmsm_dq", regenerative=True, notes="Uses the experimental sinusoidal PMSM dq model; traction thermal, field-weakening and inverter limits remain unmodeled."),
    MotorFamily.HYSTERESIS: _profile(MotorFamily.HYSTERESIS, "AC", 1, "hysteresis synchronous", "AC supply / inverter", dynamic_model="hysteresis_torque_angle", regenerative=True, notes="Experimental CPU averaged torque-angle model; not a material hysteresis, asynchronous pull-in or validated synchronisation model."),
    MotorFamily.TORQUE: _profile(MotorFamily.TORQUE, "AC / DC link", 3, "direct-drive torque motor", "servo inverter / FOC", regenerative=True),
})


def motor_family_profile(family: MotorFamily) -> MotorFamilyProfile:
    if not isinstance(family, MotorFamily):
        raise TypeError("family must be a MotorFamily")
    return MOTOR_FAMILY_PROFILES[family]


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return result


@dataclass(frozen=True, slots=True)
class MotorNameplate:
    """Common nameplate contract with explicit engineering units."""
    family: MotorFamily
    rated_power_kw: float
    rated_voltage_v: float
    rated_current_a: float
    rated_speed_rpm: float
    frequency_hz: float | None = None
    poles: int | None = None
    rated_torque_nm: float | None = None
    power_factor: float | None = None
    efficiency_percent: float | None = None
    duty_type: str = "S1"
    insulation_class: str = ""
    enclosure: str = ""
    cooling_method: str = ""
    manufacturer: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, MotorFamily):
            raise TypeError("family must be a MotorFamily")
        for name in ("rated_power_kw", "rated_voltage_v", "rated_current_a", "rated_speed_rpm"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        for name in ("frequency_hz", "rated_torque_nm"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name, positive=True))
        if motor_family_profile(self.family).supply != "DC" and self.frequency_hz is None:
            raise ValueError("frequency_hz is required for AC and AC/DC motor families")
        if self.poles is not None and (isinstance(self.poles, bool) or not isinstance(self.poles, int) or self.poles < 1 or self.poles % 2):
            raise ValueError("poles must be a positive even integer or None")
        if self.power_factor is not None:
            pf = _finite(self.power_factor, "power_factor", positive=True)
            if pf > 1.0:
                raise ValueError("power_factor must not exceed 1.0")
            object.__setattr__(self, "power_factor", pf)
        if self.efficiency_percent is not None:
            efficiency = _finite(self.efficiency_percent, "efficiency_percent", positive=True)
            if efficiency > 100.0:
                raise ValueError("efficiency_percent must not exceed 100")
            object.__setattr__(self, "efficiency_percent", efficiency)
        if not isinstance(self.duty_type, str) or not self.duty_type.strip():
            raise ValueError("duty_type must be a non-empty IEC duty designation")


__all__ = ["MotorFamily", "MotorOperatingMode", "MotorFamilyProfile", "MotorNameplate",
           "MOTOR_FAMILY_PROFILES", "motor_family_profile"]
