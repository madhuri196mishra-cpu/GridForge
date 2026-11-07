# ============================================================
# File: core/application/commands/draft_commands.py
# GridForge V2 — Draft/Commit Application Commands
# Author: Subhendu Mishra
# ============================================================
from __future__ import annotations
from typing import Any, Mapping
from uuid import UUID, uuid4
from ..command import Command

ADD_DRAFT_EQUIPMENT = "draft.add_equipment"
UPDATE_DRAFT_EQUIPMENT = "draft.update_equipment"
REMOVE_DRAFT_EQUIPMENT = "draft.remove_equipment"
ADD_DRAFT_CONNECTION = "draft.add_connection"
CREATE_DRAFT_CONNECTION = "draft.create_connection"
REMOVE_DRAFT_CONNECTION = "draft.remove_connection"
COMMIT_NETWORK = "network.commit_draft"
PASTE_DRAFT_SELECTION = "draft.paste_selection"

class _DraftCommand(Command):
    def __init__(self, command_type: str, payload: Mapping[str, Any], *, command_id: UUID|None=None, correlation_id: UUID|None=None, causation_id: UUID|None=None):
        super().__init__(command_type=command_type,payload=dict(payload),command_id=command_id or uuid4(),correlation_id=correlation_id,causation_id=causation_id)

class AddDraftEquipmentCommand(_DraftCommand):
    def __init__(self, *, equipment: Mapping[str,Any], command_id=None, correlation_id=None, causation_id=None):
        super().__init__(ADD_DRAFT_EQUIPMENT,{"equipment":dict(equipment)},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
class UpdateDraftEquipmentCommand(_DraftCommand):
    def __init__(self, *, draft_id: str, changes: Mapping[str,Any], command_id=None, correlation_id=None, causation_id=None):
        super().__init__(UPDATE_DRAFT_EQUIPMENT,{"draft_id":draft_id,"changes":dict(changes)},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
class RemoveDraftEquipmentCommand(_DraftCommand):
    def __init__(self, *, draft_id: str, command_id=None, correlation_id=None, causation_id=None):
        super().__init__(REMOVE_DRAFT_EQUIPMENT,{"draft_id":draft_id},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
class CreateDraftConnectionCommand(_DraftCommand):
    """Immutable authoring command for one canonical DraftConnection."""
    def __init__(self, *, connection: Mapping[str,Any], command_id=None, correlation_id=None, causation_id=None):
        super().__init__(CREATE_DRAFT_CONNECTION, {"connection":dict(connection)}, command_id=command_id, correlation_id=correlation_id, causation_id=causation_id)


class AddDraftConnectionCommand(_DraftCommand):
    def __init__(self, *, connection: Mapping[str,Any], command_id=None, correlation_id=None, causation_id=None):
        super().__init__(ADD_DRAFT_CONNECTION,{"connection":dict(connection)},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
class RemoveDraftConnectionCommand(_DraftCommand):
    def __init__(self, *, connection_id: str, command_id=None, correlation_id=None, causation_id=None):
        super().__init__(REMOVE_DRAFT_CONNECTION,{"connection_id":connection_id},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
class PasteDraftSelectionCommand(_DraftCommand):
    """Atomically add a semantic clipboard selection to the Application DraftNetwork."""
    def __init__(self, *, equipment: tuple[Mapping[str, Any], ...], connections: tuple[Mapping[str, Any], ...], command_id=None, correlation_id=None, causation_id=None):
        if not equipment:
            raise ValueError("A draft clipboard paste requires at least one equipment item.")
        super().__init__(PASTE_DRAFT_SELECTION, {"equipment": tuple(dict(item) for item in equipment), "connections": tuple(dict(item) for item in connections)}, command_id=command_id, correlation_id=correlation_id, causation_id=causation_id)

class CommitNetworkCommand(_DraftCommand):
    """One immutable boundary crossing from DraftNetwork to Core Network."""
    def __init__(self, *, project_id: str, activation_generation: int, draft_network: Mapping[str,Any], command_id=None, correlation_id=None, causation_id=None):
        super().__init__(COMMIT_NETWORK,{"project_id":project_id,"activation_generation":int(activation_generation),"draft_network":dict(draft_network)},command_id=command_id,correlation_id=correlation_id,causation_id=causation_id)
__all__=["ADD_DRAFT_EQUIPMENT","UPDATE_DRAFT_EQUIPMENT","REMOVE_DRAFT_EQUIPMENT","ADD_DRAFT_CONNECTION","CREATE_DRAFT_CONNECTION","REMOVE_DRAFT_CONNECTION","COMMIT_NETWORK","PASTE_DRAFT_SELECTION","PasteDraftSelectionCommand","AddDraftEquipmentCommand","UpdateDraftEquipmentCommand","RemoveDraftEquipmentCommand","CreateDraftConnectionCommand","AddDraftConnectionCommand","RemoveDraftConnectionCommand","CommitNetworkCommand"]
