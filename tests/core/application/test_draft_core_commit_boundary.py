from __future__ import annotations

import pytest

from core.application.bootstrap import create_application
from core.application.commands.draft_commands import (
    AddDraftEquipmentCommand,
    CommitNetworkCommand,
)
from core.application.creation import CreationCommitIntent, CreationCommandPreparer
from core.application.creation_schema import creation_contracts_from_definitions

_CANONICAL_CREATION_CONTRACTS = creation_contracts_from_definitions()
from core.application.draft.network import (
    DraftConnection,
    DraftEndpointReference,
    DraftEquipment,
    DraftNetwork,
)
from core.network import Network
from core.application.project import ProjectContext
from core.persistence.project_persistence import ProjectPersistenceService
from core.application.services.sld_service import SLDService
from ui.sld.sld_document import SLDDocument


def _equipment(
    *,
    draft_id: str,
    equipment_type: str,
    command_type: str,
    id_field: str,
    terminal_contract: tuple[str, ...],
    values: dict,
    parameter_mapping: dict[str, str],
    endpoint_mapping: dict[str, str] | None = None,
    placement: tuple[float, float] = (10.0, 20.0),
    endpoints: dict[str, DraftEndpointReference] | None = None,
) -> DraftEquipment:
    contract = _CANONICAL_CREATION_CONTRACTS[equipment_type]
    if tuple(terminal_contract) != tuple(contract["terminal_contract"]):
        raise ValueError(f"{equipment_type}: test fixture terminal inventory is not canonical.")
    if command_type != contract["command_type"] or id_field != contract["id_field"]:
        raise ValueError(f"{equipment_type}: test fixture command identity is not canonical.")
    if dict(parameter_mapping) != dict(contract["parameter_mapping"]):
        raise ValueError(f"{equipment_type}: test fixture parameter mapping is not canonical.")
    if dict(endpoint_mapping or {}) != dict(contract["endpoint_mapping"]):
        raise ValueError(f"{equipment_type}: test fixture endpoint mapping is not canonical.")
    return DraftEquipment(
        draft_id=draft_id,
        equipment_type=equipment_type,
        display_name=equipment_type.title(),
        terminal_contract=tuple(contract["terminal_contract"]),
        engineering_data=values,
        endpoints=endpoints or {},
        placement=placement,
        command_type=command_type,
        id_field=id_field,
        parameter_mapping=parameter_mapping,
        endpoint_mapping=endpoint_mapping or {},
        required_connection_terminals=tuple(contract["required_connection_terminals"]),
        parameter_contract=tuple(contract["parameter_contract"]),
        conditional_requirements=tuple(contract["conditional_requirements"]),
    )


def _bus(draft_id: str) -> DraftEquipment:
    return _equipment(
        draft_id=draft_id,
        equipment_type="bus",
        command_type="model.create_bus",
        id_field="bus_id",
        terminal_contract=("bus",),
        values={
            "nominal_voltage_kv": 132.0,
            "frequency_hz": 50.0,
            "in_service": True,
        },
        parameter_mapping={
            "nominal_voltage_kv": "nominal_voltage_kv",
            "frequency_hz": "frequency_hz",
            "in_service": "in_service",
        },
    )


def _transformer(draft_id: str) -> DraftEquipment:
    return _equipment(
        draft_id=draft_id,
        equipment_type="transformer",
        command_type="model.create_transformer",
        id_field="transformer_id",
        terminal_contract=("FROM", "TO"),
        values={
            "r": 0.01,
            "x": 0.05,
            "b": 0.0,
            "impedance_basis": "engineering",
            "impedance_base_voltage_kv": 132.0,
            "rate_mva": 10.0,
            "tap": 1.0,
            "shift": 0.0,
            "name": "Transformer T1",
        },
        parameter_mapping={
            "r": "r",
            "x": "x",
            "b": "b",
            "impedance_basis": "impedance_basis",
            "impedance_base_voltage_kv": "impedance_base_voltage_kv",
            "impedance_base_mva": "impedance_base_mva",
            "rate_mva": "rate_mva",
            "tap": "tap",
            "shift": "shift",
            "name": "name",
        },
        endpoint_mapping={"FROM": "endpoint_from", "TO": "endpoint_to"},
        placement=(40.0, 50.0),
    )


def _breaker(draft_id: str) -> DraftEquipment:
    return _equipment(
        draft_id=draft_id,
        equipment_type="breaker",
        command_type="model.create_breaker",
        id_field="breaker_id",
        terminal_contract=("from", "to"),
        values={
            "voltage_kv": 132.0,
            "current_a": 1000.0,
            "interrupting_ka": 25.0,
            "closed": True,
            "in_service": True,
        },
        parameter_mapping={
            "voltage_kv": "voltage_kv",
            "current_a": "current_a",
            "interrupting_ka": "interrupting_ka",
            "closed": "closed",
            "in_service": "in_service",
        },
        endpoint_mapping={"from": "endpoint_from", "to": "endpoint_to"},
    )


def _attach_sld_document(app) -> SLDDocument:
    project_id = app.project_lifecycle.context.project_id
    document = SLDDocument(f"{project_id}:sld", project_id=project_id)
    app.project_lifecycle.activate_presentation(document)
    app.attach_sld_service(SLDService(
        document,
        symbol_presentation_factory=lambda element_type: {
            "symbol_id": f"test-symbol-{element_type}",
            "definition_id": f"test-definition-{element_type}",
        },
    ))
    return document


def _commit(app, draft: DraftNetwork):
    document = _attach_sld_document(app)
    app.set_draft_network(draft)
    return app.execute(
        CommitNetworkCommand(
            project_id=draft.project_id,
            activation_generation=draft.activation_generation,
            draft_network=app.draft_network.to_dict(),
            document_id=document.document_id,
        )
    )




def _connect_terminal(
    draft: DraftNetwork,
    *,
    connection_id: str,
    bus_id: str,
    attachment_id: str,
    equipment_id: str,
    equipment_type: str,
    terminal_role: str,
    route: dict | None = None,
) -> None:
    """Connect one required equipment terminal to a canonical Bus attachment."""
    connection_fields = {
        "connection_id": connection_id,
        "source": DraftEndpointReference.bus(bus_id=bus_id, attachment_id=attachment_id),
        "target": DraftEndpointReference.terminal(
            draft_id=equipment_id,
            equipment_type=equipment_type,
            terminal_role=terminal_role,
        ),
    }
    if route is not None:
        connection_fields["route"] = route
    draft.add_connection(DraftConnection(**connection_fields))


def test_creation_intent_matches_canonical_preparer_for_bus_transformer_breaker():
    preparer = CreationCommandPreparer()
    canonical_contracts = creation_contracts_from_definitions()

    intents = (
        CreationCommitIntent(
            command_type="model.create_bus",
            id_field="bus_id",
            object_id="bus-b1",
            parameter_mapping=_bus("b1").parameter_mapping,
            endpoint_mapping={},
            values=dict(_bus("b1").engineering_data),
            endpoints={},
            position=(1.0, 2.0),
            parameter_contract=tuple(canonical_contracts["bus"]["parameter_contract"]),
            conditional_requirements=tuple(canonical_contracts["bus"]["conditional_requirements"]),
            equipment_type="bus",
        ),
        CreationCommitIntent(
            command_type="model.create_transformer",
            id_field="transformer_id",
            object_id="transformer-t1",
            parameter_mapping=_transformer("t1").parameter_mapping,
            endpoint_mapping=dict(_transformer("t1").endpoint_mapping),
            values=dict(_transformer("t1").engineering_data),
            endpoints={},
            position=(3.0, 4.0),
            parameter_contract=tuple(canonical_contracts["transformer"]["parameter_contract"]),
            conditional_requirements=tuple(canonical_contracts["transformer"]["conditional_requirements"]),
            equipment_type="transformer",
        ),
        CreationCommitIntent(
            command_type="model.create_breaker",
            id_field="breaker_id",
            object_id="breaker-k1",
            parameter_mapping=_breaker("k1").parameter_mapping,
            endpoint_mapping=dict(_breaker("k1").endpoint_mapping),
            values=dict(_breaker("k1").engineering_data),
            endpoints={},
            position=(5.0, 6.0),
            parameter_contract=tuple(canonical_contracts["breaker"]["parameter_contract"]),
            conditional_requirements=tuple(canonical_contracts["breaker"]["conditional_requirements"]),
            equipment_type="breaker",
        ),
    )

    commands = tuple(
        preparer.prepare(intent, canonical_contract=canonical_contracts[intent.equipment_type])
        for intent in intents
    )

    assert [command.command_type for command in commands] == [
        "model.create_bus",
        "model.create_transformer",
        "model.create_breaker",
    ]
    assert commands[0].payload["bus_id"] == "bus-b1"
    assert commands[1].payload["transformer_id"] == "transformer-t1"
    assert commands[2].payload["breaker_id"] == "breaker-k1"


def test_commit_prepares_multiple_equipment_deterministically_and_clears_draft():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    draft.add_equipment(_transformer("t2"))
    draft.add_equipment(_bus("b1"))
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(_bus("b3"))
    draft.add_equipment(_breaker("k1"))
    _connect_terminal(draft, connection_id="wire-t2-from", bus_id="b1", attachment_id="attachment-0", equipment_id="t2", equipment_type="transformer", terminal_role="FROM")
    _connect_terminal(draft, connection_id="wire-t2-to", bus_id="b2", attachment_id="attachment-0", equipment_id="t2", equipment_type="transformer", terminal_role="TO")
    _connect_terminal(draft, connection_id="wire-k1-from", bus_id="b2", attachment_id="attachment-1", equipment_id="k1", equipment_type="breaker", terminal_role="from")
    _connect_terminal(draft, connection_id="wire-k1-to", bus_id="b3", attachment_id="attachment-0", equipment_id="k1", equipment_type="breaker", terminal_role="to")

    result = _commit(app, draft)

    assert result.success
    assert not app.draft_network.equipment
    ids = {element.object_id for element in app.read_network().elements}
    assert "bus-b1" in ids
    assert "breaker-k1" in ids
    assert "transformer-t2" in ids


def test_commit_rejects_missing_required_engineering_data_before_core_mutation():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    invalid = _transformer("t-invalid")
    invalid = DraftEquipment(
        **{
            **{name: getattr(invalid, name) for name in (
                "draft_id", "equipment_type", "display_name", "terminal_contract",
                "endpoints", "placement", "presentation", "validation_state",
                "command_type", "id_field", "parameter_mapping", "endpoint_mapping",
            )},
            "engineering_data": {"r": 0.01, "x": 0.05},
        }
    )
    draft.add_equipment(invalid)

    with pytest.raises(Exception):
        _commit(app, draft)

    assert app.read_network().elements == ()
    assert len(app.draft_network.equipment) == 1


def test_commit_rejects_core_identity_collision_before_mutation():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    draft.add_equipment(_bus("b1"))

    # Establish the exact deterministic Core identity expected from the
    # Application commit mapping without using a UI-created Core object.
    app.execute(
        __import__("core.application.commands.model_commands", fromlist=["CreateBusCommand"]).CreateBusCommand(
            bus_id="bus-b1",
            nominal_voltage_kv=132.0,
            frequency_hz=50.0,
        )
    )

    with pytest.raises(Exception):
        _commit(app, draft)

    assert len(app.draft_network.equipment) == 1
    assert len(app.read_network().elements) == 1


def test_draft_endpoint_translation_maps_draft_bus_and_terminal_to_core_identity():
    resolver = __import__(
        "core.application.draft.handlers",
        fromlist=["DraftConnectivityResolver"],
    ).DraftConnectivityResolver()

    bus_endpoint = DraftEndpointReference.bus(
        bus_id="b1",
        attachment_id="attachment-0",
    )
    terminal_endpoint = DraftEndpointReference.terminal(
        draft_id="t1",
        equipment_type="transformer",
        terminal_role="FROM",
    )

    bus_ref = resolver._to_core_endpoint(bus_endpoint, {"b1": "bus-b1", "t1": "transformer-t1"})
    terminal_ref = resolver._to_core_endpoint(
        terminal_endpoint,
        {"b1": "bus-b1", "t1": "transformer-t1"},
    )

    assert bus_ref.to_mapping()["object_id"] == "bus-b1"
    assert terminal_ref.to_mapping()["object_id"] == "transformer-t1"
    assert terminal_ref.to_mapping()["terminal_role"] == "FROM"


def test_commit_undo_restores_draft_and_redo_recommits():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    draft.add_equipment(_bus("b1"))

    assert _commit(app, draft).success
    assert not app.draft_network.equipment
    assert any(element.object_id == "bus-b1" for element in app.read_network().elements)

    assert app.undo().success
    assert len(app.draft_network.equipment) == 1
    assert not app.read_network().elements

    assert app.redo().success
    assert not app.draft_network.equipment
    assert any(element.object_id == "bus-b1" for element in app.read_network().elements)


def _route() -> dict:
    return {
        "routing_mode": "orthogonal",
        "ownership": "engineer",
        "points": [
            [100.0, 200.0],
            [150.0, 200.0],
            [150.0, 300.0],
        ],
    }


def _commit_with_sld(draft: DraftNetwork):
    app = create_application(Network())
    project_id = app.project_lifecycle.context.project_id
    draft_data = draft.to_dict()
    draft_data["project_id"] = project_id
    draft_data["activation_generation"] = app.project_lifecycle.activation_generation
    draft = DraftNetwork.from_dict(
        draft_data,
        project_id=project_id,
        activation_generation=app.project_lifecycle.activation_generation,
    )
    document = SLDDocument("route:sld", project_id=project_id)
    app.project_lifecycle.activate_presentation(document)
    app.attach_sld_service(SLDService(
        document,
        symbol_presentation_factory=lambda element_type: {
            "symbol_id": f"test-symbol-{element_type}",
            "definition_id": f"test-definition-{element_type}",
        },
    ))
    app.set_draft_network(draft)
    result = app.execute(CommitNetworkCommand(
        project_id=draft.project_id,
        activation_generation=draft.activation_generation,
        draft_network=app.draft_network.to_dict(),
        document_id=document.document_id,
    ))
    return app, result, document

def test_valid_draft_connection_commits_as_core_simple_wire():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    bus = _bus("b1")
    transformer = _transformer("t1")
    draft.add_equipment(bus)
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(transformer)
    _connect_terminal(draft, connection_id="wire-1", bus_id="b1", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="FROM")
    _connect_terminal(draft, connection_id="wire-2", bus_id="b2", attachment_id="attachment-1", equipment_id="t1", equipment_type="transformer", terminal_role="TO")

    result = _commit(app, draft)

    assert result.success
    network = app.read_network()
    assert any(
        str(connection.connection_id) == "wire-1"
        for connection in network.simple_wires
    )
    assert not app.draft_network.connections


def test_commit_persists_engineer_authored_draft_route_structurally():
    draft = DraftNetwork("project-route", 1)
    draft.add_equipment(_bus("b1"))
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(_transformer("t1"))
    authored_route = _route()
    _connect_terminal(draft, connection_id="wire-route", bus_id="b1", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="FROM", route=authored_route)
    _connect_terminal(draft, connection_id="wire-route-to", bus_id="b2", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="TO")

    app, result, document = _commit_with_sld(draft)

    assert result.success
    connection = document.model.get_connection("sld-wire-wire-route")
    assert connection.route.to_dict() == authored_route
    assert connection.properties["route_owner"] == "engineer"


def test_commit_route_is_not_replaced_by_generated_routing():
    draft = DraftNetwork("project-route-generated", 1)
    draft.add_equipment(_bus("b1"))
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(_transformer("t1"))
    authored_route = {"routing_mode": "orthogonal", "ownership": "engineer", "points": [[901.0, 902.0], [903.0, 904.0], [905.0, 906.0]]}
    _connect_terminal(draft, connection_id="wire-distinctive", bus_id="b1", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="FROM", route=authored_route)
    _connect_terminal(draft, connection_id="wire-distinctive-to", bus_id="b2", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="TO")

    app, result, document = _commit_with_sld(draft)

    assert result.success
    assert document.model.get_connection("sld-wire-wire-distinctive").route.to_dict() == authored_route


def test_commit_route_undo_redo_restores_the_same_persistent_sld_route():
    draft = DraftNetwork("project-route-history", 1)
    draft.add_equipment(_bus("b1"))
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(_transformer("t1"))
    authored_route = _route()
    _connect_terminal(draft, connection_id="wire-history", bus_id="b1", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="FROM", route=authored_route)
    _connect_terminal(draft, connection_id="wire-history-to", bus_id="b2", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="TO")

    app, result, document = _commit_with_sld(draft)
    assert result.success
    assert document.model.get_connection("sld-wire-wire-history").route.to_dict() == authored_route

    assert app.undo().success
    assert document.model.get_connection_optional("sld-wire-wire-history") is None

    assert app.redo().success
    assert document.model.get_connection("sld-wire-wire-history").route.to_dict() == authored_route


def test_commit_route_save_reload_preserves_persistent_sld_route(tmp_path):
    draft = DraftNetwork("project-route-persistence", 1)
    draft.add_equipment(_bus("b1"))
    draft.add_equipment(_bus("b2"))
    draft.add_equipment(_transformer("t1"))
    authored_route = _route()
    _connect_terminal(draft, connection_id="wire-persist", bus_id="b1", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="FROM", route=authored_route)
    _connect_terminal(draft, connection_id="wire-persist-to", bus_id="b2", attachment_id="attachment-0", equipment_id="t1", equipment_type="transformer", terminal_role="TO")

    app, result, document = _commit_with_sld(draft)
    assert result.success

    path = tmp_path / "route-roundtrip.gridforge"
    context = ProjectContext(app.project_lifecycle.context.project_id, "Route Persistence", None)
    ProjectPersistenceService().save(context, app._command_manager.context.network, document.to_dict(), path)
    loaded = ProjectPersistenceService().load(path)
    restored = SLDDocument.from_dict(loaded.presentation)

    assert restored.model.get_connection("sld-wire-wire-persist").route.to_dict() == authored_route


def test_commit_rejects_stale_snapshot_without_discarding_newer_draft_changes():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    document = _attach_sld_document(app)
    draft.add_equipment(_bus("b1"))
    app.set_draft_network(draft)
    stale_payload = app.draft_network.to_dict()

    # Model a later, valid Application-owned authoring command after the
    # original commit snapshot was captured.
    app.execute(AddDraftEquipmentCommand(
        equipment=_bus("b2").to_dict(),
        document_id=document.document_id,
    ))

    with pytest.raises(ValueError, match="current active DraftNetwork snapshot"):
        app.execute(
            CommitNetworkCommand(
                project_id=draft.project_id,
                activation_generation=draft.activation_generation,
                draft_network=stale_payload,
                document_id=document.document_id,
            )
        )

    assert app.read_network().elements == ()
    assert {item.draft_id for item in app.draft_network.equipment} == {"b1", "b2"}


def test_application_draft_network_exposes_only_detached_snapshots():
    app = create_application(Network())
    document = _attach_sld_document(app)
    supplied = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)
    supplied.add_equipment(_bus("b1"))
    app.set_draft_network(supplied)

    # Mutating the caller-owned object after installation must not mutate the
    # Application-owned authoring state.
    supplied.add_equipment(_bus("b2"))

    # The compatibility property is a detached value snapshot, not a live
    # mutation handle into the Application.
    exposed = app.draft_network
    exposed.add_equipment(_bus("b3"))
    assert {item.draft_id for item in app.draft_network.equipment} == {"b1"}

    # Active authoring changes remain available through Application commands.
    app.execute(AddDraftEquipmentCommand(equipment=_bus("b2").to_dict(), document_id=document.document_id))
    assert {item.draft_id for item in app.draft_network.equipment} == {"b1", "b2"}


def test_empty_draft_commit_is_rejected_and_retained():
    app = create_application(Network())
    draft = DraftNetwork(app.project_lifecycle.context.project_id, app.project_lifecycle.activation_generation)

    with pytest.raises(Exception):
        _commit(app, draft)

    assert not app.read_network().elements
    assert not draft.equipment
    assert not draft.connections


def test_draft_equipment_persists_presentation_layer_without_changing_core_contract():
    equipment = _bus("layered-bus")
    layered = DraftEquipment.from_dict({**equipment.to_dict(), "layer_id": "protection"})
    restored = DraftEquipment.from_dict(layered.to_dict())
    assert restored.layer_id == "protection"

    draft = DraftNetwork("project-layered", 1)
    draft.add_equipment(restored)
    updated = draft.update_equipment("layered-bus", layer_id="control")
    assert updated.layer_id == "control"
    assert DraftNetwork.from_dict(
        draft.to_dict(),
        project_id="project-layered",
        activation_generation=1,
    ).require_equipment("layered-bus").layer_id == "control"


def test_draft_connection_presentation_layer_round_trips_and_survives_route_update():
    source = DraftEndpointReference.terminal(
        draft_id="layered-source",
        equipment_type="breaker",
        terminal_role="from",
    )
    target = DraftEndpointReference.terminal(
        draft_id="layered-target",
        equipment_type="breaker",
        terminal_role="to",
    )
    connection = DraftConnection(
        connection_id="layered-wire",
        source=source,
        target=target,
        route={"points": [(10.0, 20.0)]},
        layer_id="protection",
    )

    legacy_payload = connection.to_dict()
    restored = DraftConnection.from_dict(legacy_payload)
    assert restored.layer_id == "protection"
    assert DraftConnection.from_dict({
        key: value for key, value in legacy_payload.items() if key != "layer_id"
    }).layer_id == "default"

    draft = DraftNetwork("project-layer-wire", 1)
    draft.add_equipment(_breaker("layered-source"))
    draft.add_equipment(_breaker("layered-target"))
    draft.add_connection(restored)
    updated = draft.update_connection_route(
        "layered-wire",
        {"points": [(30.0, 40.0)]},
    )
    assert updated.layer_id == "protection"
    assert DraftNetwork.from_dict(
        draft.to_dict(),
        project_id="project-layer-wire",
        activation_generation=1,
    ).connections[0].layer_id == "protection"
