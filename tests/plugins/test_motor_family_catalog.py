from __future__ import annotations
import pytest
from plugins.motor_dynamics.catalog import MOTOR_FAMILY_PROFILES, MotorFamily, MotorNameplate, motor_family_profile
from plugins.motor_dynamics.instrumentation import motor_measurement_points


def test_every_motor_family_has_a_profile_and_measurement_template() -> None:
    assert set(MOTOR_FAMILY_PROFILES) == set(MotorFamily)
    for family in MotorFamily:
        points = motor_measurement_points(family)
        assert points
        ids = [point.point_id for point in points]
        assert len(ids) == len(set(ids))
        assert any(point.required for point in points)


@pytest.mark.parametrize("family", [
    MotorFamily.THREE_PHASE_SQUIRREL_CAGE_INDUCTION,
    MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS,
    MotorFamily.BRUSHLESS_DC,
    MotorFamily.AC_SERVO,
    MotorFamily.TRACTION_PERMANENT_MAGNET,
])
def test_ac_family_requires_frequency(family: MotorFamily) -> None:
    with pytest.raises(ValueError, match="frequency_hz is required"):
        MotorNameplate(family, 15.0, 400.0, 28.0, 1470.0)


def test_dc_nameplate_does_not_require_frequency() -> None:
    item = MotorNameplate(MotorFamily.DC_SHUNT, 5.5, 220.0, 28.0, 1450.0)
    assert item.frequency_hz is None


def test_dynamic_solver_availability_is_explicit_by_family() -> None:
    assert motor_family_profile(MotorFamily.THREE_PHASE_SQUIRREL_CAGE_INDUCTION).dynamic_model == "induction_dq"
    assert motor_family_profile(MotorFamily.PERMANENT_MAGNET_SYNCHRONOUS).dynamic_model == "pmsm_dq"
    assert motor_family_profile(MotorFamily.TRACTION_PERMANENT_MAGNET).dynamic_model == "pmsm_dq"
    assert motor_family_profile(MotorFamily.BRUSHLESS_DC).dynamic_model == "bldc_trapezoidal"
    assert motor_family_profile(MotorFamily.SYNCHRONOUS_RELUCTANCE).dynamic_model == "synrm_dq"
    assert motor_family_profile(MotorFamily.SWITCHED_RELUCTANCE).dynamic_model == "srm_phase_inductance"



@pytest.mark.parametrize("family", [
    MotorFamily.STEPPER_VARIABLE_RELUCTANCE,
    MotorFamily.STEPPER_PERMANENT_MAGNET,
    MotorFamily.STEPPER_HYBRID,
])
def test_stepper_instrumentation_uses_two_winding_phases(family: MotorFamily) -> None:
    point_ids = {point.point_id for point in motor_measurement_points(family)}
    assert "terminal.phase_a_current" in point_ids
    assert "terminal.phase_b_current" in point_ids
    assert "terminal.ic" not in point_ids
    assert "drive.dc_link_voltage" in point_ids



from plugins.motor_dynamics.transient_solver import (
    PMSMParameters,
    solve_pmsm_transient_cpu,
)


def _pmsm_parameters(**overrides):
    values = dict(
        stator_resistance_ohm=0.5,
        d_axis_inductance_h=0.004,
        q_axis_inductance_h=0.006,
        permanent_magnet_flux_wb=0.08,
        pole_pairs=4,
        inertia_kg_m2=0.02,
        thermal_capacity_j_k=5000.0,
        thermal_conductance_w_k=20.0,
    )
    values.update(overrides)
    return PMSMParameters(**values)


def test_pmsm_solver_returns_finite_immutable_channels_and_positive_torque() -> None:
    result = solve_pmsm_transient_cpu(
        "pmsm-01",
        _pmsm_parameters(),
        0.05,
        0.0001,
        stator_voltage_d_v=0.0,
        stator_voltage_q_v=12.0,
        load_torque_nm=0.1,
    )
    channels = result.channels(_pmsm_parameters())
    assert result.backend == "cpu-rk4"
    assert result.sample_count == 501
    assert all(__import__("numpy").isfinite(values).all() for values in channels.values())
    assert channels["stator_current_q_a"][-1] > 0.0
    assert channels["electromagnetic_torque_nm"][-1] > 0.0
    assert channels["rotor_speed_rad_s"][-1] > 0.0
    assert not result.state_data.flags.writeable
    assert not channels["electromagnetic_torque_nm"].flags.writeable


def test_pmsm_zero_voltage_zero_initial_state_remains_at_rest() -> None:
    result = solve_pmsm_transient_cpu(
        "pmsm-rest",
        _pmsm_parameters(),
        0.02,
        0.001,
        stator_voltage_d_v=0.0,
        stator_voltage_q_v=0.0,
    )
    assert result.state("rotor_speed_rad_s")[-1] == pytest.approx(0.0)
    assert result.state("stator_current_d_a")[-1] == pytest.approx(0.0)
    assert result.state("stator_current_q_a")[-1] == pytest.approx(0.0)


def test_pmsm_supports_sample_aligned_voltage_and_load_disturbances() -> None:
    import numpy as np
    count = 101
    voltage_q = np.full(count, 8.0)
    load = np.zeros(count)
    load[50:] = 0.2
    result = solve_pmsm_transient_cpu(
        "pmsm-disturbance",
        _pmsm_parameters(),
        0.01,
        0.0001,
        stator_voltage_d_v=np.zeros(count),
        stator_voltage_q_v=voltage_q,
        load_torque_nm=load,
    )
    assert result.load_torque_nm[49] == 0.0
    assert result.load_torque_nm[50] == 0.2
    assert result.diagnostics["load_torque_profile"] == "zero_order_hold"


@pytest.mark.parametrize("kwargs, message", [
    ({"stator_resistance_ohm": 0.0}, "stator_resistance_ohm must be finite and positive"),
    ({"d_axis_inductance_h": -0.1}, "d_axis_inductance_h must be finite and positive"),
    ({"permanent_magnet_flux_wb": float("nan")}, "permanent_magnet_flux_wb must be finite and positive"),
    ({"pole_pairs": 0}, "pole_pairs must be a positive integer"),
])
def test_pmsm_parameter_validation(kwargs, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _pmsm_parameters(**kwargs)


def test_pmsm_rejects_misaligned_input_profiles() -> None:
    with pytest.raises(ValueError, match="exactly 11 samples"):
        solve_pmsm_transient_cpu(
            "pmsm-bad-profile",
            _pmsm_parameters(),
            0.01,
            0.001,
            stator_voltage_d_v=[0.0] * 10,
            stator_voltage_q_v=8.0,
        )



from plugins.motor_dynamics.transient_solver import (
    BLDCParameters,
    solve_bldc_transient_cpu,
)


def _bldc_parameters(**overrides):
    values = dict(
        phase_resistance_ohm=0.35,
        phase_inductance_h=0.0012,
        back_emf_constant_v_s_per_rad=0.045,
        torque_constant_nm_per_a=0.045,
        pole_pairs=4,
        inertia_kg_m2=0.008,
        thermal_capacity_j_k=3000.0,
        thermal_conductance_w_k=18.0,
    )
    values.update(overrides)
    return BLDCParameters(**values)


def test_bldc_family_advertises_its_own_phase_domain_solver() -> None:
    profile = motor_family_profile(MotorFamily.BRUSHLESS_DC)
    assert profile.dynamic_model == "bldc_trapezoidal"
    assert profile.model_status == "experimental_bldc_trapezoidal"


def test_bldc_solver_preserves_floating_neutral_and_returns_channels() -> None:
    import numpy as np
    parameters = _bldc_parameters()
    result = solve_bldc_transient_cpu(
        "bldc-01", parameters, 0.02, 0.00002,
        dc_bus_voltage_v=48.0, duty_a=0.8, duty_b=-0.4, duty_c=-0.4,
        load_torque_nm=0.01,
    )
    channels = result.channels(parameters)
    assert result.backend == "cpu-rk4"
    assert result.sample_count == 1001
    assert np.allclose(
        channels["phase_a_current_a"] + channels["phase_b_current_a"] + channels["phase_c_current_a"],
        0.0, atol=1e-12,
    )
    assert all(np.isfinite(values).all() for values in channels.values())
    assert set(np.unique(channels["hall_sector"])).issubset({1.0, 2.0, 3.0, 4.0, 5.0, 6.0})
    assert not result.state_data.flags.writeable
    assert not channels["phase_c_current_a"].flags.writeable


def test_bldc_zero_voltage_and_zero_speed_remains_at_rest() -> None:
    result = solve_bldc_transient_cpu(
        "bldc-rest", _bldc_parameters(), 0.01, 0.001,
        dc_bus_voltage_v=48.0, duty_a=0.0, duty_b=0.0, duty_c=0.0,
    )
    assert result.state("rotor_speed_rad_s")[-1] == pytest.approx(0.0)
    assert result.state("phase_a_current_a")[-1] == pytest.approx(0.0)
    assert result.state("phase_b_current_a")[-1] == pytest.approx(0.0)


def test_bldc_accepts_sample_aligned_commutation_duty_and_load_profiles() -> None:
    import numpy as np
    count = 101
    duty_a = np.zeros(count); duty_b = np.zeros(count); duty_c = np.zeros(count)
    duty_a[:34] = 0.8; duty_b[34:68] = 0.8; duty_c[68:] = 0.8
    load = np.zeros(count); load[50:] = 0.05
    result = solve_bldc_transient_cpu(
        "bldc-profile", _bldc_parameters(), 0.01, 0.0001,
        dc_bus_voltage_v=np.full(count, 48.0),
        duty_a=duty_a, duty_b=duty_b, duty_c=duty_c, load_torque_nm=load,
    )
    assert result.duty_a[0] == 0.8
    assert result.duty_b[50] == 0.8
    assert result.diagnostics["commutation"] == "six_step_120_degree_trapezoidal"


@pytest.mark.parametrize("kwargs, message", [
    ({"phase_resistance_ohm": 0.0}, "phase_resistance_ohm must be finite and positive"),
    ({"phase_inductance_h": -0.1}, "phase_inductance_h must be finite and positive"),
    ({"pole_pairs": 0}, "pole_pairs must be a positive integer"),
])
def test_bldc_parameter_validation(kwargs, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _bldc_parameters(**kwargs)


def test_bldc_rejects_duty_outside_normalized_inverter_range() -> None:
    with pytest.raises(ValueError, match="duty_a values must be within"):
        solve_bldc_transient_cpu(
            "bldc-invalid-duty", _bldc_parameters(), 0.01, 0.001,
            dc_bus_voltage_v=48.0, duty_a=1.1, duty_b=-0.5, duty_c=-0.5,
        )



def test_bldc_instrumentation_exposes_commutation_and_phase_back_emf_points() -> None:
    points = {point.point_id: point for point in motor_measurement_points(MotorFamily.BRUSHLESS_DC)}
    assert {
        "drive.dc_link_voltage",
        "drive.dc_link_current",
        "drive.commutation_sector",
        "drive.phase_a_duty",
        "drive.phase_b_duty",
        "drive.phase_c_duty",
        "feedback.hall_state",
        "machine.phase_a_back_emf",
        "machine.phase_b_back_emf",
        "machine.phase_c_back_emf",
    }.issubset(points)
    assert points["drive.commutation_sector"].unit == "sector"
    assert points["machine.phase_a_back_emf"].unit == "V"



from plugins.motor_dynamics.transient_solver import SynRMParameters, solve_synrm_transient_cpu, SRMParameters, solve_srm_transient_cpu


def _synrm_parameters(**kw):
    values=dict(stator_resistance_ohm=0.42,d_axis_inductance_h=0.018,q_axis_inductance_h=0.006,pole_pairs=2,inertia_kg_m2=0.012,thermal_capacity_j_k=3500.,thermal_conductance_w_k=20.)
    values.update(kw); return SynRMParameters(**values)


def test_synrm_reluctance_torque_and_shared_solver_channels():
    import numpy as np
    p=_synrm_parameters()
    result=solve_synrm_transient_cpu("synrm-01",p,0.02,0.0001,stator_voltage_d_v=12.,stator_voltage_q_v=18.,load_torque_nm=0.02)
    channels=result.channels(p)
    assert result.backend=="cpu-rk4" and result.sample_count==201
    assert np.isfinite(result.state_data).all() and channels["electromagnetic_torque_nm"][-1]>0
    assert not result.state_data.flags.writeable


def test_synrm_zero_voltage_rest_and_profile_validation():
    result=solve_synrm_transient_cpu("synrm-rest",_synrm_parameters(),0.01,0.001,stator_voltage_d_v=0.,stator_voltage_q_v=0.)
    assert result.state("rotor_speed_rad_s")[-1]==pytest.approx(0.)
    with pytest.raises(ValueError,match="stator_voltage_d_v profile must contain exactly"):
        solve_synrm_transient_cpu("synrm-bad",_synrm_parameters(),0.01,0.001,stator_voltage_d_v=[0.]*5,stator_voltage_q_v=0.)


def test_synrm_requires_positive_saliency():
    with pytest.raises(ValueError,match="d-axis inductance must exceed"):
        _synrm_parameters(d_axis_inductance_h=0.005,q_axis_inductance_h=0.006)


def _srm_parameters(**kw):
    values=dict(phase_resistance_ohm=0.28,unaligned_inductance_h=0.0015,aligned_inductance_h=0.012,rotor_poles=6,inertia_kg_m2=0.009,saturation_current_a=80.,thermal_capacity_j_k=3200.,thermal_conductance_w_k=19.)
    values.update(kw); return SRMParameters(**values)


def test_srm_position_dependent_inductance_and_torque_channels():
    import numpy as np
    p=_srm_parameters()
    result=solve_srm_transient_cpu("srm-01",p,0.02,0.0001,phase_a_voltage_v=24.,phase_b_voltage_v=0.,phase_c_voltage_v=0.,load_torque_nm=0.01)
    ch=result.channels(p)
    assert result.backend=="cpu-rk4" and result.sample_count==201
    assert np.isfinite(result.state_data).all()
    assert np.all(ch["phase_a_inductance_h"]>=p.unaligned_inductance_h)
    assert np.all(ch["phase_a_inductance_h"]<=p.aligned_inductance_h)
    assert not ch["electromagnetic_torque_nm"].flags.writeable


def test_srm_zero_voltage_rest_and_parameter_validation():
    result=solve_srm_transient_cpu("srm-rest",_srm_parameters(),0.01,0.001,phase_a_voltage_v=0.,phase_b_voltage_v=0.,phase_c_voltage_v=0.)
    assert result.state("rotor_speed_rad_s")[-1]==pytest.approx(0.)
    assert result.state("phase_a_current_a")[-1]==pytest.approx(0.)
    with pytest.raises(ValueError,match="aligned_inductance_h must exceed"):
        _srm_parameters(aligned_inductance_h=0.001)
    with pytest.raises(ValueError,match="rotor_poles must be"):
        _srm_parameters(rotor_poles=1)


def test_reluctance_motor_instrumentation_is_family_specific():
    synrm={p.point_id for p in motor_measurement_points(MotorFamily.SYNCHRONOUS_RELUCTANCE)}
    srm={p.point_id for p in motor_measurement_points(MotorFamily.SWITCHED_RELUCTANCE)}
    assert {"drive.d_axis_current_reference","drive.q_axis_current_reference","machine.d_axis_inductance","machine.q_axis_inductance","machine.reluctance_torque"}.issubset(synrm)
    assert {"drive.phase_a_voltage_command","drive.phase_b_voltage_command","drive.phase_c_voltage_command","machine.phase_a_inductance","machine.phase_b_inductance","machine.phase_c_inductance","drive.rotor_alignment_position"}.issubset(srm)


from plugins.motor_dynamics.transient_solver import DCMotorParameters, solve_dc_motor_transient_cpu


def _dc_parameters(excitation="separately_excited", **kw):
    values=dict(armature_resistance_ohm=.4,armature_inductance_h=.008,inertia_kg_m2=.02,excitation=excitation,
        field_resistance_ohm=110.,series_field_resistance_ohm=.08,field_inductance_h=2.,shunt_flux_wb_per_a=.08,series_flux_wb_per_a=.012,
        saturation_flux_wb=.9,permanent_magnet_flux_wb=.06)
    values.update(kw); return DCMotorParameters(**values)


@pytest.mark.parametrize("family,excitation",[
    (MotorFamily.DC_SEPARATELY_EXCITED,"separately_excited"),(MotorFamily.DC_SHUNT,"shunt"),
    (MotorFamily.DC_SERIES,"series"),(MotorFamily.DC_COMPOUND,"compound"),
    (MotorFamily.DC_PERMANENT_MAGNET,"permanent_magnet")])
def test_dc_family_profiles_and_excitation_solver(family,excitation):
    import numpy as np
    assert motor_family_profile(family).dynamic_model=="dc_excitation"
    p=_dc_parameters(excitation)
    result=solve_dc_motor_transient_cpu("dc-family",p,.02,.0001,armature_voltage_v=24.,
        field_voltage_v=12. if excitation in {"separately_excited","shunt","compound"} else 0.,load_torque_nm=.01)
    ch=result.channels(p)
    assert result.backend=="cpu-rk4" and np.isfinite(result.state_data).all()
    assert ch["armature_current_a"][-1]>0 and ch["electromagnetic_torque_nm"][-1]>0
    assert not ch["air_gap_flux_wb"].flags.writeable


def test_dc_zero_input_rest_and_series_field_tracks_armature():
    p=_dc_parameters("series")
    r=solve_dc_motor_transient_cpu("dc-rest",p,.01,.001,armature_voltage_v=0.)
    assert r.state("rotor_speed_rad_s")[-1]==pytest.approx(0.)
    assert r.channels(p)["series_field_current_a"][-1]==pytest.approx(0.)


def test_dc_compound_cumulative_and_differential_polarity():
    import numpy as np
    p1=_dc_parameters("compound",compound_connection="cumulative")
    p2=_dc_parameters("compound",compound_connection="differential")
    a=solve_dc_motor_transient_cpu("dc-cum",p1,.01,.0001,armature_voltage_v=18.,field_voltage_v=10.).channels(p1)
    b=solve_dc_motor_transient_cpu("dc-diff",p2,.01,.0001,armature_voltage_v=18.,field_voltage_v=10.).channels(p2)
    assert np.all(a["air_gap_flux_wb"]>=b["air_gap_flux_wb"]-1e-12)


def test_dc_rejects_field_voltage_for_series_and_pm():
    with pytest.raises(ValueError,match="field_voltage_v must be zero"):
        solve_dc_motor_transient_cpu("dc-invalid",_dc_parameters("series"),.01,.001,armature_voltage_v=12.,field_voltage_v=2.)


def test_dc_instrumentation_exposes_field_and_thermal_points():
    points={p.point_id for p in motor_measurement_points(MotorFamily.DC_COMPOUND)}
    assert {"terminal.armature_voltage","terminal.armature_current","excitation.field_voltage","excitation.field_current",
        "excitation.series_field_current","machine.air_gap_flux","machine.back_emf","machine.armature_temperature",
        "machine.field_copper_loss"}.issubset(points)


def test_wound_rotor_family_uses_induction_dq_with_external_resistance():
    import numpy as np
    from plugins.motor_dynamics.transient_solver import MotorParameters, solve_transient_cpu
    p = MotorParameters(stator_resistance_ohm=.45, rotor_resistance_ohm=.3,
        external_rotor_resistance_ohm=.8, stator_leakage_inductance_h=.012,
        rotor_leakage_inductance_h=.012, magnetizing_inductance_h=.18,
        pole_pairs=2, inertia_kg_m2=.02)
    assert motor_family_profile(MotorFamily.THREE_PHASE_WOUND_ROTOR_INDUCTION).dynamic_model == "induction_dq"
    r = solve_transient_cpu("wound-rotor", p, .01, .0001,
        stator_voltage_d_v=120., stator_voltage_q_v=0., electrical_frequency_hz=50., load_torque_nm=.01)
    ch = r.channels(p, 2 * np.pi * 50., .01)
    assert r.diagnostics["rotor_circuit"] == "wound_rotor_with_external_resistance"
    assert ch["external_rotor_resistance_ohm"][0] == pytest.approx(.8)
    assert ch["rotor_copper_loss_w"][-1] >= 0.
    with pytest.raises(ValueError, match="external_rotor_resistance_ohm"):
        MotorParameters(stator_resistance_ohm=.45, rotor_resistance_ohm=.3,
            external_rotor_resistance_ohm=-.1, stator_leakage_inductance_h=.012,
            rotor_leakage_inductance_h=.012, magnetizing_inductance_h=.18,
            pole_pairs=2, inertia_kg_m2=.02)


from plugins.motor_dynamics.transient_solver import SinglePhaseMotorParameters, solve_single_phase_motor_transient_cpu


@pytest.mark.parametrize("family,kind",[
    (MotorFamily.SINGLE_PHASE_SPLIT_PHASE,"split_phase"),
    (MotorFamily.SINGLE_PHASE_CAPACITOR_START,"capacitor_start"),
    (MotorFamily.SINGLE_PHASE_PERMANENT_SPLIT_CAPACITOR,"permanent_split_capacitor"),
    (MotorFamily.SINGLE_PHASE_CAPACITOR_START_RUN,"capacitor_start_run"),
    (MotorFamily.SINGLE_PHASE_SHADED_POLE,"shaded_pole"),
])
def test_single_phase_family_profiles_and_solver(family,kind):
    import numpy as np
    assert motor_family_profile(family).dynamic_model=="single_phase_rms_envelope"
    p=SinglePhaseMotorParameters(family=kind,main_resistance_ohm=8.,main_inductance_h=.04,
        auxiliary_resistance_ohm=12.,auxiliary_inductance_h=.025,inertia_kg_m2=.02,
        torque_constant_nm_per_a2=.5,auxiliary_capacitance_f=.0002 if "capacitor" in kind else 0.)
    r=solve_single_phase_motor_transient_cpu("single-phase",p,.02,.0001,supply_voltage_rms_v=230.,load_torque_nm=.02)
    ch=r.channels(p)
    assert r.backend=="cpu-rk4" and np.isfinite(r.state_data).all()
    assert ch["line_current_rms_a"][-1]>=0 and np.isfinite(ch["electromagnetic_torque_nm"]).all()


def test_single_phase_capacitor_families_require_capacitance():
    with pytest.raises(ValueError,match="require auxiliary_capacitance_f"):
        SinglePhaseMotorParameters(family="capacitor_start",main_resistance_ohm=8.,main_inductance_h=.04,
            auxiliary_resistance_ohm=12.,auxiliary_inductance_h=.025,inertia_kg_m2=.02,torque_constant_nm_per_a2=.5)


def test_universal_motor_ac_and_dc_series_field_response():
    import numpy as np
    from plugins.motor_dynamics.transient_solver import UniversalMotorParameters, solve_universal_motor_transient_cpu
    for mode in ("ac", "dc"):
        parameters = UniversalMotorParameters(
            armature_resistance_ohm=1.2, field_resistance_ohm=0.8,
            armature_inductance_h=0.015, field_inductance_h=0.025,
            inertia_kg_m2=0.03, torque_constant_nm_per_a_wb=12.0,
            back_emf_constant_v_s_per_rad_wb=12.0,
            field_flux_per_ampere_wb_a=0.04, saturation_current_a=10.0,
            supply_mode=mode,
        )
        result = solve_universal_motor_transient_cpu(
            "universal", parameters, 0.02, 0.0001,
            supply_voltage_v=120.0, load_torque_nm=0.1,
        )
        channels = result.channels(parameters)
        assert result.backend == "cpu-rk4"
        assert np.isfinite(result.state_data).all()
        assert np.allclose(channels["armature_current_a"], channels["series_field_current_a"])
        assert np.isfinite(channels["electromagnetic_torque_nm"]).all()


def test_universal_motor_rejects_invalid_supply_mode():
    import pytest
    from plugins.motor_dynamics.transient_solver import UniversalMotorParameters
    with pytest.raises(ValueError, match="supply_mode"):
        UniversalMotorParameters(
            armature_resistance_ohm=1.0, field_resistance_ohm=1.0,
            armature_inductance_h=0.01, field_inductance_h=0.01,
            inertia_kg_m2=0.02, torque_constant_nm_per_a_wb=1.0,
            back_emf_constant_v_s_per_rad_wb=1.0,
            field_flux_per_ampere_wb_a=0.1, supply_mode="three_phase",
        )


def test_wound_field_synchronous_solver_tracks_field_and_damper_states():
    import numpy as np
    from plugins.motor_dynamics.transient_solver import WoundFieldSynchronousParameters, solve_wound_field_synchronous_transient_cpu
    p=WoundFieldSynchronousParameters(
        stator_resistance_ohm=.4,stator_d_inductance_h=.08,stator_q_inductance_h=.09,
        field_resistance_ohm=1.2,field_inductance_h=.3,damper_d_resistance_ohm=.5,
        damper_q_resistance_ohm=.6,damper_d_inductance_h=.04,damper_q_inductance_h=.05,
        mutual_field_inductance_h=.02,mutual_damper_d_inductance_h=.01,
        mutual_damper_q_inductance_h=.01,pole_pairs=2,inertia_kg_m2=.08)
    r=solve_wound_field_synchronous_transient_cpu("sync",p,.02,.0001,
        stator_voltage_d_v=20.,stator_voltage_q_v=10.,field_voltage_v=12.,load_torque_nm=.2)
    c=r.channels(p)
    assert r.backend=="cpu-rk4" and np.isfinite(r.state_data).all()
    assert c["field_current_a"][-1]>0
    assert np.isfinite(c["electromagnetic_torque_nm"]).all()
    assert "damper_d_current_a" in c and "synchronous_speed_error_rad_s" in c


def test_wound_field_synchronous_rejects_nonpositive_mutual_energy():
    import pytest
    from plugins.motor_dynamics.transient_solver import WoundFieldSynchronousParameters
    with pytest.raises(ValueError,match="positive-energy"):
        WoundFieldSynchronousParameters(
            stator_resistance_ohm=.4,stator_d_inductance_h=.08,stator_q_inductance_h=.09,
            field_resistance_ohm=1.2,field_inductance_h=.3,damper_d_resistance_ohm=.5,
            damper_q_resistance_ohm=.6,damper_d_inductance_h=.04,damper_q_inductance_h=.05,
            mutual_field_inductance_h=.2,mutual_damper_d_inductance_h=.01,
            mutual_damper_q_inductance_h=.01,pole_pairs=2,inertia_kg_m2=.08)
