"""Family-aware motor measurement templates using canonical Core channels."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from core.model import EndpointReference
from core.measurement.measurement_channel import MeasurementChannel, MeasurementPhase, MeasurementSignalType
from core.measurement.measurement_provisioning import MeasurementProvisioning
from .catalog import MotorFamily, motor_family_profile


@dataclass(frozen=True, slots=True)
class MotorMeasurementPoint:
    point_id: str
    name: str
    signal_type: MeasurementSignalType
    phase: MeasurementPhase
    unit: str
    consumers: frozenset[str]
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        for field in ("point_id", "name", "unit"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.signal_type, MeasurementSignalType):
            raise TypeError("signal_type must be a MeasurementSignalType")
        if not isinstance(self.phase, MeasurementPhase):
            raise TypeError("phase must be a MeasurementPhase")
        if not isinstance(self.consumers, frozenset) or not self.consumers:
            raise TypeError("consumers must be a non-empty frozenset")
        if not isinstance(self.required, bool):
            raise TypeError("required must be bool")


def _p(pid: str, name: str, kind: MeasurementSignalType, phase: MeasurementPhase,
       unit: str, consumers: tuple[str, ...], required: bool = False) -> MotorMeasurementPoint:
    return MotorMeasurementPoint(pid, name, kind, phase, unit, frozenset(consumers), required)


def motor_measurement_points(family: MotorFamily) -> tuple[MotorMeasurementPoint, ...]:
    """Return required electrical points plus optional mechanical, thermal and drive points."""
    profile = motor_family_profile(family)
    C, V, P, F, D, X = (MeasurementSignalType.CURRENT, MeasurementSignalType.VOLTAGE,
                        MeasurementSignalType.POWER, MeasurementSignalType.FREQUENCY,
                        MeasurementSignalType.DIGITAL, MeasurementSignalType.CUSTOM)
    N, A, B, CPH, AB, BC, CA, T = (MeasurementPhase.NONE, MeasurementPhase.A,
        MeasurementPhase.B, MeasurementPhase.C, MeasurementPhase.AB,
        MeasurementPhase.BC, MeasurementPhase.CA, MeasurementPhase.THREE_PHASE)
    pts: list[MotorMeasurementPoint] = []
    if profile.supply == "DC":
        pts += [_p("terminal.armature_voltage", "Armature terminal voltage", V, N, "V", ("metering","control","protection"), True),
                _p("terminal.armature_current", "Armature current", C, N, "A", ("metering","control","protection"), True),
                _p("electrical.input_power", "DC input power", P, N, "kW", ("metering","scada"), True)]
    elif profile.phase_count == 1:
        pts += [_p("terminal.voltage", "Single-phase terminal voltage", V, N, "V", ("metering","protection","control"), True),
                _p("terminal.current", "Single-phase line current", C, N, "A", ("metering","protection","control"), True),
                _p("electrical.input_power", "Single-phase input power", P, N, "kW", ("metering","scada"), True),
                _p("electrical.frequency", "Supply frequency", F, N, "Hz", ("control","protection","scada"))]
    elif profile.phase_count == 2:
        # Two-phase winding motors (stepper families) are measured at the
        # winding phases, not misrepresented as three-phase line equipment.
        pts += [_p("terminal.phase_a_voltage", "Winding phase-A voltage", V, A, "V", ("control","monitoring"), True),
                _p("terminal.phase_a_current", "Winding phase-A current", C, A, "A", ("control","protection","monitoring"), True),
                _p("terminal.phase_b_voltage", "Winding phase-B voltage", V, B, "V", ("control","monitoring"), True),
                _p("terminal.phase_b_current", "Winding phase-B current", C, B, "A", ("control","protection","monitoring"), True),
                _p("electrical.input_power", "Stepper drive input power", P, N, "kW", ("metering","analysis"), True)]
    else:
        for phase, suffix in ((A,"a"), (B,"b"), (CPH,"c")):
            pts += [_p(f"terminal.i{suffix}", f"Phase-{suffix.upper()} current", C, phase, "A", ("metering","protection","scada"), True),
                    _p(f"terminal.v{suffix}", f"Phase-{suffix.upper()} voltage", V, phase, "V", ("metering","protection","control"), True)]
        pts += [_p("terminal.vab", "Line voltage AB", V, AB, "V", ("metering","protection")),
                _p("terminal.vbc", "Line voltage BC", V, BC, "V", ("metering","protection")),
                _p("terminal.vca", "Line voltage CA", V, CA, "V", ("metering","protection")),
                _p("terminal.in", "Neutral current", C, N, "A", ("protection","monitoring")),
                _p("electrical.active_power", "Three-phase active power", P, T, "kW", ("metering","scada","control"), True),
                _p("electrical.reactive_power", "Three-phase reactive power", P, T, "kvar", ("metering","scada")),
                _p("electrical.apparent_power", "Three-phase apparent power", P, T, "kVA", ("metering","scada")),
                _p("electrical.power_factor", "Power factor", P, T, "pu", ("metering","scada")),
                _p("electrical.frequency", "Supply frequency", F, N, "Hz", ("control","protection","scada"), True),
                _p("electrical.phase_sequence", "Phase sequence", D, N, "state", ("protection","commissioning"))]

    if family == MotorFamily.WOUND_FIELD_SYNCHRONOUS:
        pts += [_p("machine.stator_d_current", "Stator d-axis current", C, N, "A", ("metering","control","protection"), True),
                _p("machine.stator_q_current", "Stator q-axis current", C, N, "A", ("metering","control","protection"), True),
                _p("machine.field_current", "Rotor field current", X, N, "A", ("metering","control","protection"), True),
                _p("machine.field_voltage", "Rotor field voltage", X, N, "V", ("monitoring","control"), True),
                _p("machine.damper_d_current", "D-axis damper current", X, N, "A", ("monitoring","analysis")),
                _p("machine.damper_q_current", "Q-axis damper current", X, N, "A", ("monitoring","analysis")),
                _p("machine.synchronous_speed_error", "Synchronous speed error", X, N, "rad/s", ("control","protection","alarm")),
                _p("machine.rotor_angle", "Rotor electrical angle", X, N, "rad", ("control","analysis")),
                _p("machine.field_winding_temperature", "Field winding temperature", X, N, "degC", ("monitoring","alarm","maintenance"))]
    if family == MotorFamily.HYSTERESIS:
        pts += [_p("machine.rotor_speed", "Rotor speed", X, N, "rad/s", ("metering","control","protection"), True),
                _p("machine.load_angle", "Rotor load angle", X, N, "rad", ("control","analysis","protection"), True),
                _p("machine.pullout_torque_margin", "Pull-out torque margin", X, N, "N.m", ("control","protection","alarm"), True),
                _p("machine.rotor_temperature", "Rotor temperature", X, N, "degC", ("monitoring","alarm","maintenance")),
                _p("machine.rotor_loss", "Rotor loss estimate", X, N, "W", ("monitoring","analysis"))]
    if family == MotorFamily.UNIVERSAL:
        pts += [_p("machine.armature_current", "Armature current", C, N, "A", ("metering","protection","control"), True),
                _p("machine.series_field_current", "Series field current", C, N, "A", ("monitoring","protection")),
                _p("machine.field_flux", "Series field flux", X, N, "Wb", ("monitoring","analysis")),
                _p("machine.armature_copper_loss", "Armature copper loss", P, N, "W", ("monitoring","analysis")),
                _p("machine.field_copper_loss", "Series field copper loss", P, N, "W", ("monitoring","analysis")),
                _p("machine.commutator_temperature", "Commutator / winding temperature", X, N, "degC", ("monitoring","alarm","maintenance"))]
    if family in {MotorFamily.SINGLE_PHASE_SPLIT_PHASE, MotorFamily.SINGLE_PHASE_CAPACITOR_START, MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR, MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN, MotorFamily.SINGLE_PHASE_SHADED_POLE}:
        pts += [_p("machine.main_winding_temperature", "Main winding temperature", X, N, "degC", ("monitoring","alarm","protection")),
                _p("machine.auxiliary_winding_current", "Auxiliary winding current", C, N, "A", ("monitoring","protection")),
                _p("machine.main_winding_copper_loss", "Main winding copper loss", P, N, "W", ("monitoring","analysis")),
                _p("machine.auxiliary_winding_copper_loss", "Auxiliary winding copper loss", P, N, "W", ("monitoring","analysis")),
                _p("machine.auxiliary_phase_displacement", "Auxiliary winding phase displacement", X, N, "rad", ("commissioning","analysis"))]
        if family in {MotorFamily.SINGLE_PHASE_SPLIT_PHASE, MotorFamily.SINGLE_PHASE_CAPACITOR_START}:
            pts.append(_p("drive.auxiliary_cutout", "Start winding cutout state", D, N, "state", ("control","monitoring")))
        if family in {MotorFamily.SINGLE_PHASE_CAPACITOR_START, MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR, MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN}:
            pts.append(_p("machine.auxiliary_capacitor_voltage", "Auxiliary capacitor voltage", V, N, "V", ("monitoring","maintenance")))
    if family == MotorFamily.THREE_PHASE_WOUND_ROTOR_INDUCTION:
        pts += [_p("rotor.external_resistance", "External rotor resistance", X, N, "ohm", ("control","commissioning","analysis")),
                _p("rotor.rotor_current", "Rotor current magnitude", C, N, "A", ("monitoring","protection")),
                _p("rotor.rotor_copper_loss", "Rotor circuit copper loss", P, N, "W", ("monitoring","analysis"))]

    pts += [
        _p("machine.speed", "Rotor / translator speed", X, N, "rpm_or_m_s", ("control","protection","monitoring"), True),
        _p("machine.shaft_torque", "Shaft torque", X, N, "N.m", ("monitoring","analysis")),
        _p("machine.output_power", "Mechanical output power", P, N, "kW", ("metering","analysis")),
        _p("machine.position", "Rotor / linear position", X, N, "deg_or_mm", ("control","monitoring")),
        _p("machine.acceleration", "Shaft / translator acceleration", X, N, "m/s2", ("monitoring","analysis")),
        _p("machine.vibration_x", "Vibration X", X, N, "mm/s_RMS", ("condition_monitoring","alarm")),
        _p("machine.vibration_y", "Vibration Y", X, N, "mm/s_RMS", ("condition_monitoring","alarm")),
        _p("machine.vibration_z", "Vibration Z", X, N, "mm/s_RMS", ("condition_monitoring","alarm")),
        _p("machine.bearing_de", "Drive-end bearing temperature", X, N, "degC", ("monitoring","alarm","protection")),
        _p("machine.bearing_nde", "Non-drive-end bearing temperature", X, N, "degC", ("monitoring","alarm","protection")),
        _p("stator.winding_u", "Stator winding U temperature", X, N, "degC", ("monitoring","alarm","protection")),
        _p("stator.winding_v", "Stator winding V temperature", X, N, "degC", ("monitoring","alarm","protection")),
        _p("stator.winding_w", "Stator winding W temperature", X, N, "degC", ("monitoring","alarm","protection")),
        _p("machine.coolant_inlet_temperature", "Cooling medium inlet temperature", X, N, "degC", ("monitoring","alarm")),
        _p("machine.coolant_outlet_temperature", "Cooling medium outlet temperature", X, N, "degC", ("monitoring","alarm")),
        _p("insulation.insulation_resistance", "Insulation resistance to earth", X, N, "Mohm", ("maintenance","commissioning")),
        _p("insulation.leakage_current", "Insulation leakage current", C, N, "mA", ("protection","monitoring")),
        _p("shaft.shaft_voltage", "Shaft voltage", V, N, "V", ("condition_monitoring","maintenance")),
        _p("shaft.bearing_current", "Bearing current", C, N, "mA", ("condition_monitoring","maintenance")),
        _p("environment.ambient_temperature", "Ambient temperature", X, N, "degC", ("monitoring","analysis")),
        _p("protection.trip_status", "Motor protection trip status", D, N, "state", ("protection","scada")),
        _p("protection.thermal_model_state", "Thermal overload model state", D, N, "state", ("protection","monitoring")),
    ]
    drive_families = {MotorFamily.BRUSHLESS_DC, MotorFamily.SWITCHED_RELUCTANCE,
        MotorFamily.STEPPER_VARIABLE_RELUCTANCE, MotorFamily.STEPPER_PERMANENT_MAGNET,
        MotorFamily.STEPPER_HYBRID, MotorFamily.AC_SERVO, MotorFamily.LINEAR_SYNCHRONOUS,
        MotorFamily.TRACTION_INDUCTION, MotorFamily.TRACTION_PERMANENT_MAGNET,
        MotorFamily.TORQUE, MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS}
    if profile.supply in {"DC link", "AC / DC link"} or family in drive_families:
        stepper_drive = family in {MotorFamily.STEPPER_VARIABLE_RELUCTANCE, MotorFamily.STEPPER_PERMANENT_MAGNET, MotorFamily.STEPPER_HYBRID}
        pts += [_p("drive.dc_link_voltage", "Drive DC-link voltage", V, N, "V", ("control","protection","monitoring"), stepper_drive),
                _p("drive.dc_link_current", "Drive DC-link current", C, N, "A", ("control","protection","monitoring"), stepper_drive),
                _p("drive.inverter_temperature", "Inverter heat-sink temperature", X, N, "degC", ("monitoring","alarm")),
                _p("drive.switching_frequency", "Inverter switching frequency", F, N, "Hz", ("control","diagnostics")),
                _p("drive.active_current_reference", "Active-axis current reference", C, N, "A", ("control","diagnostics")),
                _p("drive.torque_current_reference", "Torque-axis current reference", C, N, "A", ("control","diagnostics")),
                _p("drive.modulation_index", "PWM modulation index", X, N, "pu", ("control","diagnostics")),
                _p("drive.regeneration_status", "Regenerative operating status", D, N, "state", ("control","protection","scada"))]
    if family in {MotorFamily.WOUND_FIELD_SYNCHRONOUS, MotorFamily.DC_SHUNT, MotorFamily.DC_SERIES,
                  MotorFamily.DC_COMPOUND, MotorFamily.DC_SEPARATELY_EXCITED}:
        pts += [_p("excitation.field_voltage", "Field winding voltage", V, N, "V", ("control","monitoring")),
                _p("excitation.field_current", "Field winding current", C, N, "A", ("control","monitoring","protection")),
                _p("excitation.field_temperature", "Field winding temperature", X, N, "degC", ("monitoring","alarm"))]
    if family in {MotorFamily.DC_SHUNT, MotorFamily.DC_SERIES, MotorFamily.DC_COMPOUND, MotorFamily.DC_SEPARATELY_EXCITED, MotorFamily.DC_PERMANENT_MAGNET}:
        pts += [_p("machine.armature_temperature", "Armature winding temperature", X, N, "degC", ("monitoring","alarm","protection")),
                _p("machine.air_gap_flux", "Main air-gap flux", X, N, "Wb", ("monitoring","analysis")),
                _p("machine.back_emf", "Armature back EMF", V, N, "V", ("monitoring","control")),
                _p("machine.armature_copper_loss", "Armature copper loss", P, N, "W", ("monitoring","analysis")),
                _p("machine.field_copper_loss", "Field copper loss", P, N, "W", ("monitoring","analysis"))]
        if family in {MotorFamily.DC_SERIES, MotorFamily.DC_COMPOUND}:
            pts.append(_p("excitation.series_field_current", "Series-field current", C, N, "A", ("monitoring","protection")))
        if family == MotorFamily.DC_COMPOUND:
            pts.append(_p("excitation.compound_polarity", "Compound field polarity", D, N, "state", ("commissioning","control")))
    if family == MotorFamily.THREE_PHASE_WOUND_ROTOR_INDUCTION:
        pts += [_p("rotor.external_resistance", "External rotor resistance", X, N, "ohm", ("control","monitoring")),
                _p("rotor.slip_ring_temperature", "Slip-ring temperature", X, N, "degC", ("monitoring","alarm")),
                _p("rotor.brush_wear", "Rotor brush wear", X, N, "%", ("maintenance","alarm"))]
    if family in {MotorFamily.SINGLE_PHASE_CAPACITOR_START, MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR,
                  MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN}:
        pts += [_p("starting.capacitor_voltage", "Start/run capacitor voltage", V, N, "V", ("monitoring","maintenance")),
                _p("starting.capacitor_current", "Start/run capacitor current", C, N, "A", ("monitoring","maintenance")),
                _p("starting.switch_status", "Centrifugal switch / start relay status", D, N, "state", ("control","maintenance"))]
    if family in {MotorFamily.DC_SHUNT, MotorFamily.DC_SERIES, MotorFamily.DC_COMPOUND,
                  MotorFamily.DC_SEPARATELY_EXCITED, MotorFamily.DC_PERMANENT_MAGNET,
                  MotorFamily.UNIVERSAL, MotorFamily.DC_SERVO}:
        pts += [_p("commutator.temperature", "Commutator temperature", X, N, "degC", ("monitoring","alarm")),
                _p("brush.current", "Brush current", C, N, "A", ("monitoring","maintenance")),
                _p("brush.wear", "Brush wear", X, N, "%", ("maintenance","alarm"))]
    if family in {MotorFamily.AC_SERVO, MotorFamily.DC_SERVO, MotorFamily.STEPPER_VARIABLE_RELUCTANCE,
                  MotorFamily.STEPPER_PERMANENT_MAGNET, MotorFamily.STEPPER_HYBRID,
                  MotorFamily.BRUSHLESS_DC, MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS}:
        pts += [_p("feedback.encoder_position", "Encoder position feedback", X, N, "deg", ("control","commissioning")),
                _p("feedback.encoder_speed", "Encoder speed feedback", X, N, "rpm", ("control","commissioning")),
                _p("feedback.resolver_position", "Resolver position feedback", X, N, "deg", ("control","commissioning")),
                _p("feedback.hall_state", "Hall sensor commutation state", D, N, "state", ("control","diagnostics"))]
    if family == MotorFamily.DC_SERVO:
        pts += [_p("drive.armature_current_reference", "DC servo armature current reference", C, N, "A", ("control","diagnostics")),
                _p("drive.armature_voltage_command", "DC servo armature voltage command", V, N, "V", ("control","diagnostics")),
                _p("feedback.position_command", "DC servo position command", X, N, "rad", ("control","commissioning")),
                _p("feedback.position_error", "DC servo position following error", X, N, "rad", ("control","alarm")),
                _p("feedback.speed_reference", "DC servo speed reference", X, N, "rad/s", ("control","diagnostics"))]
    if family in {MotorFamily.STEPPER_VARIABLE_RELUCTANCE, MotorFamily.STEPPER_PERMANENT_MAGNET, MotorFamily.STEPPER_HYBRID}:
        pts += [_p("drive.phase_a_current_reference", "Stepper phase-A current reference", C, A, "A", ("control","diagnostics")),
                _p("drive.phase_b_current_reference", "Stepper phase-B current reference", C, B, "A", ("control","diagnostics")),
                _p("drive.phase_a_voltage_command", "Stepper phase-A voltage command", V, A, "V", ("control","diagnostics")),
                _p("drive.phase_b_voltage_command", "Stepper phase-B voltage command", V, B, "V", ("control","diagnostics")),
                _p("feedback.position_command", "Stepper commanded position", X, N, "rad", ("control","motion_control"), True),
                _p("feedback.position_error", "Stepper position following error", X, N, "rad", ("control","alarm","motion_control")),
                _p("machine.electromagnetic_torque", "Stepper electromagnetic torque", X, N, "N.m", ("control","analysis")),
                _p("machine.winding_temperature", "Stepper winding temperature", X, N, "degC", ("monitoring","alarm","maintenance"))]
    if family == MotorFamily.SYNCHRONOUS_RELUCTANCE:
        pts += [
            _p("drive.d_axis_current_reference", "d-axis current reference", C, N, "A", ("control","diagnostics")),
            _p("drive.q_axis_current_reference", "q-axis current reference", C, N, "A", ("control","diagnostics")),
            _p("machine.d_axis_inductance", "d-axis inductance", X, N, "H", ("analysis","commissioning")),
            _p("machine.q_axis_inductance", "q-axis inductance", X, N, "H", ("analysis","commissioning")),
            _p("machine.reluctance_torque", "Reluctance torque component", X, N, "N.m", ("analysis","monitoring")),
        ]
    if family == MotorFamily.SWITCHED_RELUCTANCE:
        pts += [
            _p("drive.phase_a_voltage_command", "SRM phase-A voltage command", V, A, "V", ("control","diagnostics")),
            _p("drive.phase_b_voltage_command", "SRM phase-B voltage command", V, B, "V", ("control","diagnostics")),
            _p("drive.phase_c_voltage_command", "SRM phase-C voltage command", V, CPH, "V", ("control","diagnostics")),
            _p("drive.phase_a_current_reference", "SRM phase-A current reference", C, A, "A", ("control","diagnostics")),
            _p("drive.phase_b_current_reference", "SRM phase-B current reference", C, B, "A", ("control","diagnostics")),
            _p("drive.phase_c_current_reference", "SRM phase-C current reference", C, CPH, "A", ("control","diagnostics")),
            _p("machine.phase_a_inductance", "SRM phase-A inductance", X, A, "H", ("analysis","commissioning")),
            _p("machine.phase_b_inductance", "SRM phase-B inductance", X, B, "H", ("analysis","commissioning")),
            _p("machine.phase_c_inductance", "SRM phase-C inductance", X, CPH, "H", ("analysis","commissioning")),
            _p("drive.rotor_alignment_position", "Rotor alignment position", X, N, "deg", ("control","diagnostics")),
        ]
    if family == MotorFamily.BRUSHLESS_DC:
        # Drive telemetry is distinct from motor-terminal CT/VT measurements.
        # Optional points are provisioned only when the inverter exposes them.
        pts += [
            _p("drive.commutation_sector", "Electronic commutation sector", D, N, "sector", ("control","diagnostics")),
            _p("drive.phase_a_duty", "Inverter phase-A signed duty", X, A, "pu", ("control","diagnostics")),
            _p("drive.phase_b_duty", "Inverter phase-B signed duty", X, B, "pu", ("control","diagnostics")),
            _p("drive.phase_c_duty", "Inverter phase-C signed duty", X, CPH, "pu", ("control","diagnostics")),
            _p("machine.phase_a_back_emf", "Phase-A back-EMF", V, A, "V", ("analysis","diagnostics")),
            _p("machine.phase_b_back_emf", "Phase-B back-EMF", V, B, "V", ("analysis","diagnostics")),
            _p("machine.phase_c_back_emf", "Phase-C back-EMF", V, CPH, "V", ("analysis","diagnostics")),
        ]
    if family == MotorFamily.AC_SERVO:
        # Drive feedback is kept distinct from motor terminal metering. The
        # host provisions only signals actually exposed by its servo drive.
        pts += [
            _p("drive.dc_link_voltage", "Servo DC-link voltage", V, N, "V", ("control","diagnostics")),
            _p("machine.rotor_flux_magnitude", "Induction servo rotor flux magnitude", X, N, "Wb", ("control","diagnostics")),
            _p("drive.rotor_flux_reference", "Induction servo rotor flux reference", X, N, "Wb", ("control","diagnostics")),
            _p("drive.voltage_saturation", "Servo voltage vector saturation", D, N, "state", ("control","protection")),
            _p("drive.current_saturation", "Servo current limit active", D, N, "state", ("control","protection")),
            _p("drive.dc_link_current", "Servo DC-link current", C, N, "A", ("control","diagnostics")),
            _p("drive.d_axis_current_reference", "Servo d-axis current reference", C, N, "A", ("control","diagnostics")),
            _p("drive.q_axis_current_reference", "Servo q-axis current reference", C, N, "A", ("control","diagnostics")),
            _p("drive.d_axis_current_feedback", "Servo d-axis current feedback", C, N, "A", ("control","diagnostics")),
            _p("drive.q_axis_current_feedback", "Servo q-axis current feedback", C, N, "A", ("control","diagnostics")),
            _p("feedback.encoder_position", "Servo encoder position", X, N, "rad", ("control","monitoring")),
            _p("feedback.encoder_speed", "Servo encoder speed", X, N, "rad/s", ("control","monitoring")),
            _p("feedback.position_command", "Servo position command", X, N, "rad", ("control","diagnostics")),
            _p("feedback.position_error", "Servo position following error", X, N, "rad", ("control","protection")),
            _p("feedback.speed_command", "Servo speed command", X, N, "rad/s", ("control","diagnostics")),
            _p("feedback.torque_command", "Servo torque command", X, N, "N·m", ("control","diagnostics")),
            _p("drive.inverter_temperature", "Servo inverter temperature", X, N, "°C", ("monitoring","protection")),
            _p("drive.brake_state", "Servo holding-brake state", D, N, "state", ("control","protection")),
            _p("drive.ready_state", "Servo drive ready state", D, N, "state", ("control","protection")),
            _p("drive.fault_state", "Servo drive fault state", D, N, "state", ("control","protection")),
        ]
    if family in {MotorFamily.LINEAR_INDUCTION, MotorFamily.LINEAR_SYNCHRONOUS}:
        pts += [_p("linear.thrust_force", "Linear motor thrust force", X, N, "N", ("control","analysis")),
                _p("linear.air_gap", "Primary-secondary air gap", X, N, "mm", ("monitoring","maintenance")),
                _p("linear.position", "Translator position", X, N, "mm", ("control","monitoring")),
                _p("linear.translator_velocity", "Translator linear velocity", X, N, "m/s", ("control","protection","monitoring"), True),
                _p("linear.translator_acceleration", "Translator linear acceleration", X, N, "m/s2", ("monitoring","analysis")),
                _p("linear.load_force", "External load force", X, N, "N", ("control","analysis")),
                _p("linear.primary_winding_temperature", "Primary winding temperature", X, N, "degC", ("monitoring","alarm","maintenance")),
                _p("linear.secondary_temperature", "Secondary reaction-plate temperature", X, N, "degC", ("monitoring","alarm","maintenance")),
                _p("linear.drive_electrical_frequency", "Travelling-field electrical frequency", F, N, "Hz", ("control","diagnostics")),
                _p("linear.end_effect_compensation_state", "End-effect compensation state", D, N, "state", ("control","diagnostics"))]

    if family == MotorFamily.VOICE_COIL:
        pts += [_p("voice_coil.current", "Voice-coil winding current", C, N, "A", ("control","protection","monitoring"), True),
                _p("voice_coil.drive_voltage", "Voice-coil drive voltage", V, N, "V", ("control","diagnostics")),
                _p("voice_coil.position", "Actuator position", X, N, "m", ("control","monitoring"), True),
                _p("voice_coil.velocity", "Moving-coil velocity", X, N, "m/s", ("control","monitoring")),
                _p("voice_coil.force", "Electromagnetic actuator force", X, N, "N", ("control","analysis")),
                _p("voice_coil.external_load", "External load force", X, N, "N", ("control","analysis")),
                _p("voice_coil.stroke_utilization", "Stroke utilization", X, N, "pu", ("control","alarm","monitoring")),
                _p("voice_coil.winding_temperature", "Coil winding temperature", X, N, "degC", ("monitoring","alarm","maintenance")),
                _p("voice_coil.end_stop_force", "End-stop reaction force", X, N, "N", ("monitoring","protection","diagnostics"))]

    ids = [point.point_id for point in pts]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"duplicate motor measurement point IDs for {family.value}")
    return tuple(pts)


def provision_motor_measurement_channels(*, motor_id: str, family: MotorFamily,
        sources: Mapping[str, tuple[Any, Any, EndpointReference]]) -> tuple[MeasurementChannel, ...]:
    """Provision configured sources only; required points must be wired explicitly.

    Each source tuple is (source equipment, authoritative Core terminal,
    EndpointReference). Optional points may be omitted. No instrument,
    terminal, topology or measurement source is fabricated.
    """
    if not isinstance(motor_id, str) or not motor_id.strip():
        raise ValueError("motor_id must be a non-empty string")
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping")
    points = motor_measurement_points(family)
    by_id = {point.point_id: point for point in points}
    supplied = set(sources)
    unknown = supplied - set(by_id)
    missing = {p.point_id for p in points if p.required} - supplied
    if unknown or missing:
        raise ValueError(f"instrument source mismatch; missing_required={sorted(missing)}, unknown={sorted(unknown)}")
    channels = []
    for point in points:
        if point.point_id not in sources:
            continue
        source, terminal, reference = sources[point.point_id]
        channels.append(MeasurementProvisioning.provision_channel(
            source=source, source_terminal=terminal, source_terminal_reference=reference,
            channel_id=f"{motor_id}.{point.point_id}", signal_type=point.signal_type,
            name=point.name, phase=point.phase, unit=point.unit))
    return tuple(channels)


__all__ = ["MotorMeasurementPoint", "motor_measurement_points",
           "provision_motor_measurement_channels"]
