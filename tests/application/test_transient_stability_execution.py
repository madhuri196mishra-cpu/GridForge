import json
from dataclasses import dataclass, replace
from enum import Enum
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from core.application.application import Application
from core.application.read_models import StudyResultReadModel
from core.application.read_service import StudyReadService
from core.application.revision import ProjectRevision
from ui.controllers.study_case_controller import StudyCaseController
from core.solver.dynamics.events import EventExecution, EventTransition
from core.solver.dynamics.transient_stability import TransientDiscontinuity, TransientStabilityResult

from core.analysis.dynamic_initial_state import DynamicMachineModelDefinition
from core.analysis.dynamic_model_association import DynamicMachineModelAssociation
from core.analysis.power_flow_preparation import PreparedBranch, PreparedPowerFlow
from core.analysis.transient_stability import TransientStabilityStudyConfiguration
from core.application.bootstrap import create_application
from core.application.study import StudyRequest
from core.network import Network
from core.model.bus import Bus
from core.model.synchronous_machine import SynchronousMachine
from core.numerical.ybus import YBus, YBusBuilder
from core.solver.dynamics.machine_models import ClassicalMachineParameters
from core.solver.power_flow.input import PowerFlowBusType, PowerFlowInput
from core.solver.power_flow.result import PowerFlowResult




def test_dynamic_machine_model_definition_rejects_unsupported_persisted_identity():
    with pytest.raises(ValueError, match="G-unsupported.*unsupported model identity.*classical"):
        DynamicMachineModelDefinition(
            machine_id="G-unsupported",
            bus_id="B1",
            parameters=ClassicalMachineParameters(H=3.0, Xd_prime=0.3, Efd=1.1),
            mechanical_power=0.0,
            model_type="classical",
        )

def test_application_executes_transient_stability_from_detached_pf_snapshot():
    network = Network()
    network.add_bus(Bus("B1", name="Bus 1", nominal_voltage_kv=11.0))
    network.add_bus(Bus("B2", name="Bus 2", nominal_voltage_kv=11.0))
    network.add_synchronous_machine(SynchronousMachine("G1", name="G1", endpoint=network.get_by_id("bus", "B1")))
    application = create_application(network)

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
        project_id=application.project_lifecycle.context.project_id,
        activation_generation=application.project_lifecycle.activation_generation,
        topology_revision=application.revision.topology_revision,
        source_revision=(
            application.revision.model_revision,
            application.revision.topology_revision,
            application.revision.presentation_revision,
            application.revision.persisted_revision,
        ),
    )
    snapshot = PreparedPowerFlow(
        input=snapshot.input,
        ybus=YBusBuilder().build(snapshot),
        base_mva=snapshot.base_mva,
        bus_voltage_bases=snapshot.bus_voltage_bases,
        branches=snapshot.branches,
        project_id=snapshot.project_id,
        activation_generation=snapshot.activation_generation,
        topology_revision=snapshot.topology_revision,
        source_revision=snapshot.source_revision,
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
        bus_ids=power_flow_input.bus_ids,
        snapshot_id=power_flow_input.snapshot_id,
    )

    application.dynamic_models.bind(DynamicMachineModelAssociation(
        machine_id="G1",
        bus_id="B1",
        model_type="classical_synchronous_machine",
        parameters=ClassicalMachineParameters(H=3.0, Xd_prime=0.3, Efd=1.1),
        mechanical_power=0.0,
        project_id=application.project_lifecycle.context.project_id,
        activation_generation=application.project_lifecycle.activation_generation,
    ))
    request = StudyRequest(
        project_id=application.project_lifecycle.context.project_id,
        activation_generation=application.project_lifecycle.activation_generation,
        source_revision=application.revision,
        study_type="transient_stability",
        configuration={
            "configuration": TransientStabilityStudyConfiguration(end_time=0.02, dt=0.01),
            "prepared_power_flow": snapshot,
            "power_flow_result": power_flow_result,
        },
    )

    result = application.execute_study(request)

    assert result.status == "completed"
    assert result.value.number_of_steps >= 1


def test_study_comparison_read_contract_accepts_live_and_archived_results():
    project_id = "project-live-archive"
    revision = ProjectRevision(model_revision=3, topology_revision=2)
    live_study_id = uuid4()
    archived_record_id = uuid4()
    provenance = {
        "nominal_frequency_hz": 50.0,
        "power_base_mva": 100.0,
        "bus_voltage_bases_kv": {"B1": 11.0},
        "canonical_topology_fingerprint": "topology-sha256",
        "prepared_network_topology_fingerprint": "prepared-sha256",
        "machine_model_manifest": (("G1", "classical_synchronous_machine"),),
        "state_signals": ({
            "signal_id": "machine:G1:rotor_angle",
            "global_index": 0,
            "label": "G1 rotor angle",
            "state_name": "rotor_angle",
            "unit": "rad",
            "machine_id": "G1",
        },),
    }
    def transient_result(
        offset, *, event_id=None, boundary_status="succeeded",
        target_id="SW1", time_samples=(0.0, 0.01), provenance_override=None,
        event_status="succeeded", reevaluation_status="succeeded",
        include_transition=True, boundary_event_ids=None,
    ):
        events = ()
        discontinuities = ()
        if event_id is not None:
            event = EventExecution(
                event_id=event_id,
                event_type="switch_operation",
                time=0.01,
                scheduled_time=0.01,
                dispatch_time=0.0101 + offset,
                status=event_status,
                failure_type="dispatch_error" if event_status == "failed" else None,
                failure_message="dispatch rejected" if event_status == "failed" else None,
                post_event_re_evaluation_status=reevaluation_status,
                post_event_re_evaluation_failure_type=(
                    "evaluation_error" if reevaluation_status == "failed" else None
                ),
                post_event_re_evaluation_failure_message=(
                    "post-event evaluation failed" if reevaluation_status == "failed" else None
                ),
                transition=EventTransition(
                    transition_type="switch_state_change",
                    target_kind="switch",
                    target_id=target_id,
                    previous_state={"closed": True},
                    new_state={"closed": False},
                ) if include_transition else None,
            )
            events = (event,)
            successful_boundary = boundary_status == "succeeded"
            discontinuities = (TransientDiscontinuity(
                time=0.01,
                event_ids=(event_id,) if boundary_event_ids is None else boundary_event_ids,
                event_executions=(event,),
                pre_state=np.asarray([0.2 + offset]),
                pre_terminal_voltages={"B1": 0.99 + 0.01j},
                pre_electrical_powers={"G1": (0.79, 0.11)},
                pre_algebraic_quality=None,
                pre_topology_revision=2,
                post_state=np.asarray([0.25 + offset]) if successful_boundary else None,
                post_terminal_voltages={"B1": 0.98 + 0.02j} if successful_boundary else None,
                post_electrical_powers={"G1": (0.78, 0.12)} if successful_boundary else None,
                post_algebraic_quality=None,
                post_topology_revision=3 if successful_boundary else None,
                post_status=boundary_status,
            ),)
        return TransientStabilityResult(
            time=np.asarray(time_samples),
            states=np.asarray([[0.1 + offset], [0.2 + offset]]),
            terminal_voltages=({"B1": 1.0 + 0.0j}, {"B1": 0.99 + 0.01j}),
            electrical_powers=({"G1": (0.8, 0.1)}, {"G1": (0.79, 0.11)}),
            events=events,
            discontinuities=discontinuities,
            provenance=provenance if provenance_override is None else provenance_override,
        )
    archived_result = SimpleNamespace(
        study_id=uuid4(),
        project_id=project_id,
        activation_generation=3,
        source_revision=revision,
        study_type="transient_stability",
        status="completed",
        value=transient_result(0.0),
        message="archived run",
        metadata={},
    )
    live_result = StudyResultReadModel(
        study_id=live_study_id,
        project_id=project_id,
        activation_generation=4,
        source_revision=revision,
        study_type="transient_stability",
        status="completed",
        value=transient_result(0.05),
        current=True,
    )
    archive_record = SimpleNamespace(
        record_id=archived_record_id,
        saved_at="2026-11-05T10:00:00+00:00",
        result=archived_result,
    )
    studies = SimpleNamespace(
        get_result=lambda *args, **kwargs: None,
        results=lambda **kwargs: (),
    )
    archive = SimpleNamespace(
        load_all=lambda *args, **kwargs: (archive_record,),
        load_latest=lambda *args, **kwargs: None,
    )
    read_service = StudyReadService(studies, archive)

    comparison = read_service.compare_study_results(
        live_result,
        archived_record_id,
        project_id=project_id,
        activation_generation=4,
        current_revision=revision,
    )

    assert comparison.left is live_result
    assert comparison.right.record_id == archived_record_id
    assert comparison.left.study_id == live_study_id
    assert comparison.compatible
    assert comparison.numeric_comparison_status == "TRANSIENT_COMPARABLE"
    assert comparison.transient_signal_deltas
    assert comparison.transient_signal_deltas[0].deltas == (0.05, 0.05)
    payload = json.loads(read_service.export_study_comparison(
        live_result,
        archived_record_id,
        project_id=project_id,
        activation_generation=4,
        current_revision=revision,
    ))
    assert payload["format"] == "GridForgeTransientComparison"
    assert payload["left"]["source_kind"] == "live"
    assert payload["left"]["record_id"] is None
    assert payload["right"]["source_kind"] == "archive"
    assert payload["right"]["record_id"] == str(archived_record_id)
    stale_live = StudyResultReadModel(
        study_id=live_study_id,
        project_id=project_id,
        activation_generation=4,
        source_revision=revision,
        study_type="transient_stability",
        status="completed",
        value=transient_result(0.05),
        current=False,
    )
    stale_comparison = read_service.compare_study_results(
        stale_live,
        archived_record_id,
        project_id=project_id,
        activation_generation=4,
        current_revision=revision,
    )
    assert not stale_comparison.compatible
    assert "LEFT_LIVE_RESULT_NOT_CURRENT" in stale_comparison.transient_compatibility_diagnostics
    revision_drift_comparison = read_service.compare_study_results(
        live_result,
        archived_record_id,
        project_id=project_id,
        activation_generation=4,
        current_revision=ProjectRevision(model_revision=4, topology_revision=2),
    )
    assert not revision_drift_comparison.compatible
    assert "LEFT_LIVE_RESULT_NOT_CURRENT" in revision_drift_comparison.transient_compatibility_diagnostics
    left_event_run = StudyResultReadModel(
        study_id=uuid4(), project_id=project_id, activation_generation=4,
        source_revision=revision, study_type="transient_stability", status="completed",
        value=transient_result(0.0, event_id="left-event"), current=True,
    )
    right_event_run = SimpleNamespace(
        study_id=uuid4(), project_id=project_id, activation_generation=3,
        source_revision=revision, study_type="transient_stability", status="completed",
        value=transient_result(0.1, event_id="right-event"), message="", metadata={},
    )
    event_record_id = uuid4()
    event_archive_record = SimpleNamespace(
        record_id=event_record_id, saved_at="2026-11-05T10:00:00+00:00",
        result=right_event_run,
    )
    event_archive_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (event_archive_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    event_comparison = event_archive_service.compare_study_results(
        left_event_run, event_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert event_comparison.compatible
    assert len(event_comparison.transient_event_matches) == 1
    assert event_comparison.transient_event_matches[0].left_event_id == "left-event"
    assert event_comparison.transient_event_matches[0].right_event_id == "right-event"
    event_match = event_comparison.transient_event_matches[0]
    assert event_match.event_time_s == 0.01
    assert event_match.left_dispatch_time_s == pytest.approx(0.0101)
    assert event_match.right_dispatch_time_s == pytest.approx(0.1101)
    assert event_match.dispatch_delta_s == pytest.approx(0.1)
    assert len(event_comparison.transient_discontinuity_deltas) == 1
    boundary = event_comparison.transient_discontinuity_deltas[0]
    assert boundary.signals[0].pre_delta == 0.1
    assert boundary.signals[0].post_delta == 0.1

    semantic_mismatch_record_id = uuid4()
    semantic_mismatch_record = SimpleNamespace(
        record_id=semantic_mismatch_record_id, saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=transient_result(0.1, event_id="other-event", target_id="SW2"),
            message="", metadata={},
        ),
    )
    semantic_mismatch_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (semantic_mismatch_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    semantic_mismatch = semantic_mismatch_service.compare_study_results(
        left_event_run, semantic_mismatch_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert not semantic_mismatch.compatible
    assert "EVENT_SEMANTIC_SET_MISMATCH" in semantic_mismatch.transient_compatibility_diagnostics

    grid_mismatch_record_id = uuid4()
    grid_mismatch_record = SimpleNamespace(
        record_id=grid_mismatch_record_id, saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=transient_result(0.1, time_samples=(0.0, 0.02)),
            message="", metadata={},
        ),
    )
    grid_mismatch_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (grid_mismatch_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    grid_mismatch = grid_mismatch_service.compare_study_results(
        live_result, grid_mismatch_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert not grid_mismatch.compatible
    assert "TIME_GRID_MISMATCH" in grid_mismatch.transient_compatibility_diagnostics

    mismatched_provenance = dict(provenance)
    mismatched_provenance["nominal_frequency_hz"] = 60.0
    provenance_record_id = uuid4()
    provenance_record = SimpleNamespace(
        record_id=provenance_record_id, saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=transient_result(0.1, provenance_override=mismatched_provenance),
            message="", metadata={},
        ),
    )
    provenance_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (provenance_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    provenance_comparison = provenance_service.compare_study_results(
        live_result, provenance_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert not provenance_comparison.compatible
    assert "PROVENANCE_MISMATCH:nominal_frequency_hz" in provenance_comparison.transient_compatibility_diagnostics

    failed_boundary_result = SimpleNamespace(
        study_id=uuid4(), project_id=project_id, activation_generation=3,
        source_revision=revision, study_type="transient_stability", status="completed",
        value=transient_result(0.1, event_id="failed-event", boundary_status="dispatch_failed"),
        message="", metadata={},
    )
    failed_boundary_record_id = uuid4()
    failed_boundary_record = SimpleNamespace(
        record_id=failed_boundary_record_id, saved_at="2026-11-05T10:00:00+00:00",
        result=failed_boundary_result,
    )
    failed_boundary_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (failed_boundary_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    failed_boundary_comparison = failed_boundary_service.compare_study_results(
        left_event_run, failed_boundary_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert not failed_boundary_comparison.compatible
    assert "DISCONTINUITY_BOUNDARY_EVIDENCE_UNAVAILABLE" in failed_boundary_comparison.transient_compatibility_diagnostics
    assert failed_boundary_comparison.transient_signal_deltas == ()
    assert failed_boundary_comparison.transient_event_matches == ()
    assert failed_boundary_comparison.transient_discontinuity_deltas == ()

    def compare_event_variant(*, event_id, **event_options):
        record_id = uuid4()
        record = SimpleNamespace(
            record_id=record_id,
            saved_at="2026-11-05T10:00:00+00:00",
            result=SimpleNamespace(
                study_id=uuid4(), project_id=project_id, activation_generation=3,
                source_revision=revision, study_type="transient_stability", status="completed",
                value=transient_result(0.1, event_id=event_id, **event_options),
                message="", metadata={},
            ),
        )
        service = StudyReadService(studies, SimpleNamespace(
            load_all=lambda *args, **kwargs: (record,),
            load_latest=lambda *args, **kwargs: None,
        ))
        return service.compare_study_results(
            left_event_run, record_id, project_id=project_id,
            activation_generation=4, current_revision=revision,
        )

    dispatch_failure = compare_event_variant(
        event_id="dispatch-failed", event_status="failed",
    )
    assert not dispatch_failure.compatible
    assert "EVENT_DISPATCH_NOT_SUCCESSFUL" in dispatch_failure.transient_compatibility_diagnostics

    reevaluation_failure = compare_event_variant(
        event_id="reevaluation-failed", reevaluation_status="failed",
    )
    assert not reevaluation_failure.compatible
    assert any(
        item.startswith("EVENT_POST_EVALUATION_NOT_SUCCESSFUL:")
        for item in reevaluation_failure.transient_compatibility_diagnostics
    )

    missing_transition = compare_event_variant(
        event_id="transition-missing", include_transition=False,
    )
    assert not missing_transition.compatible
    assert "EVENT_SEMANTIC_IDENTITY_UNAVAILABLE" in missing_transition.transient_compatibility_diagnostics

    missing_boundary_event = compare_event_variant(
        event_id="boundary-event", boundary_event_ids=("unknown-run-local-event",),
    )
    assert not missing_boundary_event.compatible
    assert "DISCONTINUITY_BOUNDARY_EVIDENCE_UNAVAILABLE" in missing_boundary_event.transient_compatibility_diagnostics

    duplicate_event_result = transient_result(0.1, event_id="duplicate-event")
    duplicate_event_result = replace(
        duplicate_event_result,
        events=duplicate_event_result.events + duplicate_event_result.events,
    )
    duplicate_event_record_id = uuid4()
    duplicate_event_record = SimpleNamespace(
        record_id=duplicate_event_record_id,
        saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=duplicate_event_result, message="", metadata={},
        ),
    )
    duplicate_event_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (duplicate_event_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    duplicate_event_comparison = duplicate_event_service.compare_study_results(
        left_event_run, duplicate_event_record_id, project_id=project_id,
        activation_generation=4, current_revision=revision,
    )
    assert not duplicate_event_comparison.compatible
    assert "EVENT_SEMANTIC_IDENTITY_AMBIGUOUS" in duplicate_event_comparison.transient_compatibility_diagnostics

    unsupported_export_value = object()
    unsupported_provenance = dict(provenance)
    unsupported_provenance["unsupported_payload"] = unsupported_export_value
    unsupported_record_id = uuid4()
    unsupported_record = SimpleNamespace(
        record_id=unsupported_record_id,
        saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=transient_result(0.1, provenance_override=unsupported_provenance),
            message="", metadata={},
        ),
    )
    unsupported_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (unsupported_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    with pytest.raises(ValueError, match="cannot encode"):
        unsupported_service.export_study_comparison(
            live_result, unsupported_record_id, project_id=project_id,
            activation_generation=4, current_revision=revision,
        )

    @dataclass(frozen=True)
    class UnregisteredExportDataclass:
        value: str

    class UnregisteredExportEnum(Enum):
        VALUE = "value"

    for unsupported_value in (
        UnregisteredExportDataclass("must not be serialized by fallback"),
        UnregisteredExportEnum.VALUE,
    ):
        rejected_provenance = dict(provenance)
        rejected_provenance["unregistered_value"] = unsupported_value
        rejected_record_id = uuid4()
        rejected_record = SimpleNamespace(
            record_id=rejected_record_id,
            saved_at="2026-11-05T10:00:00+00:00",
            result=SimpleNamespace(
                study_id=uuid4(), project_id=project_id, activation_generation=3,
                source_revision=revision, study_type="transient_stability", status="completed",
                value=transient_result(0.1, provenance_override=rejected_provenance),
                message="", metadata={},
            ),
        )
        rejected_service = StudyReadService(studies, SimpleNamespace(
            load_all=lambda *args, **kwargs: (rejected_record,),
            load_latest=lambda *args, **kwargs: None,
        ))
        with pytest.raises(ValueError, match="unregistered"):
            rejected_service.export_study_comparison(
                live_result, rejected_record_id, project_id=project_id,
                activation_generation=4, current_revision=revision,
            )

    cyclic_metadata = {}
    cyclic_metadata["self"] = cyclic_metadata
    cyclic_record_id = uuid4()
    cyclic_record = SimpleNamespace(
        record_id=cyclic_record_id,
        saved_at="2026-11-05T10:00:00+00:00",
        result=SimpleNamespace(
            study_id=uuid4(), project_id=project_id, activation_generation=3,
            source_revision=revision, study_type="transient_stability", status="completed",
            value=transient_result(0.1), message="", metadata=cyclic_metadata,
        ),
    )
    cyclic_service = StudyReadService(studies, SimpleNamespace(
        load_all=lambda *args, **kwargs: (cyclic_record,),
        load_latest=lambda *args, **kwargs: None,
    ))
    with pytest.raises(ValueError, match="cyclic values"):
        cyclic_service.export_study_comparison(
            live_result, cyclic_record_id, project_id=project_id,
            activation_generation=4, current_revision=revision,
        )

    assert read_service.compare_archived_results(
        live_result,
        archived_record_id,
        project_id=project_id,
        activation_generation=4,
        current_revision=revision,
    ) == comparison



def test_study_case_controller_rejects_non_string_comparison_source():
    errors = []
    calls = []
    application = SimpleNamespace(
        study_case=lambda study_id: calls.append(("study_case", study_id)),
        execute_study_case=lambda study_id: calls.append(("execute", study_id)),
    )
    controller = StudyCaseController(
        application=application,
        error_handler=errors.append,
    )

    result = controller.export_study_comparison(
        [], str(uuid4()), "live", str(uuid4()),
    )

    assert result is None
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "source is invalid" in str(errors[0])
    assert calls == []



def test_application_rejects_non_string_study_comparison_source():
    application = Application.__new__(Application)
    application.project_lifecycle = SimpleNamespace(
        context=SimpleNamespace(project_id="project"),
        has_project=True,
        state="ACTIVE",
        activation_generation=1,
    )

    with pytest.raises(ValueError, match="source must be"):
        application.export_study_comparison(
            [], uuid4(), "live", uuid4(),
        )
