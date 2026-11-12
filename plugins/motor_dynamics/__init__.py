"""GridForge V2 motor dynamics extension; optional dependencies are loaded lazily."""
from .plugin import MotorDynamicsPlugin
from .interoperability import motor_measurement_contracts, motor_model_capability
from .catalog import MOTOR_FAMILY_PROFILES, MotorFamily, MotorFamilyProfile, MotorNameplate, MotorOperatingMode, motor_family_profile
from .instrumentation import MotorMeasurementPoint, motor_measurement_points, provision_motor_measurement_channels
from .transient_solver import (
    MotorParameters, MotorSimulationResult, PMSMParameters, PMSMSimulationResult,
    cuda_available, solve_transient_batch, solve_transient_cpu, solve_pmsm_transient_cpu,
    BLDCParameters, BLDCSimulationResult, solve_bldc_transient_cpu,
    SynRMParameters, SynRMSimulationResult, solve_synrm_transient_cpu,
    SRMParameters, SRMSimulationResult, solve_srm_transient_cpu,
    DCMotorParameters, DCMotorSimulationResult, solve_dc_motor_transient_cpu,
    SinglePhaseMotorParameters, SinglePhaseMotorSimulationResult, solve_single_phase_motor_transient_cpu,
    UniversalMotorParameters, UniversalMotorSimulationResult, solve_universal_motor_transient_cpu,
    WoundFieldSynchronousParameters, WoundFieldSynchronousSimulationResult, solve_wound_field_synchronous_transient_cpu,
    HysteresisMotorParameters, HysteresisMotorSimulationResult, solve_hysteresis_motor_transient_cpu,
    PMSMServoParameters, PMSMServoSimulationResult, solve_pmsm_servo_transient_cpu,
    InductionServoParameters, InductionServoSimulationResult, solve_induction_servo_transient_cpu,
    DCServoParameters, DCServoSimulationResult, solve_dc_servo_transient_cpu,
)
__all__ = [
    "MotorDynamicsPlugin", "motor_measurement_contracts", "motor_model_capability", "MotorFamily", "MotorFamilyProfile", "MotorNameplate", "MotorOperatingMode",
    "MOTOR_FAMILY_PROFILES", "motor_family_profile", "MotorMeasurementPoint", "motor_measurement_points",
    "provision_motor_measurement_channels", "MotorParameters", "MotorSimulationResult", "PMSMParameters",
    "PMSMSimulationResult", "solve_pmsm_transient_cpu", "BLDCParameters", "BLDCSimulationResult",
    "solve_bldc_transient_cpu", "SynRMParameters", "SynRMSimulationResult", "solve_synrm_transient_cpu",
    "SRMParameters", "SRMSimulationResult", "solve_srm_transient_cpu", "DCMotorParameters",
    "DCMotorSimulationResult", "solve_dc_motor_transient_cpu", "SinglePhaseMotorParameters",
    "SinglePhaseMotorSimulationResult", "solve_single_phase_motor_transient_cpu", "UniversalMotorParameters",
    "UniversalMotorSimulationResult", "solve_universal_motor_transient_cpu", "WoundFieldSynchronousParameters",
    "WoundFieldSynchronousSimulationResult", "solve_wound_field_synchronous_transient_cpu",
    "HysteresisMotorParameters", "HysteresisMotorSimulationResult", "solve_hysteresis_motor_transient_cpu",
    "PMSMServoParameters", "PMSMServoSimulationResult", "solve_pmsm_servo_transient_cpu",
    "InductionServoParameters", "InductionServoSimulationResult", "solve_induction_servo_transient_cpu",
    "DCServoParameters", "DCServoSimulationResult", "solve_dc_servo_transient_cpu",
    "solve_transient_cpu", "solve_transient_batch", "cuda_available",
]
