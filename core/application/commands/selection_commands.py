"""Atomic Application command for deleting a semantic SLD selection."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from ..command import Command


DELETE_SELECTION_BATCH = "sld.delete_selection_batch"


class DeleteSelectionBatchCommand(Command):
    """Compose supported delete commands under one Application transaction."""

    def __init__(
        self,
        *,
        commands: Sequence[Command],
        document_id: str,
        command_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> None:
        children = tuple(commands)
        if not children:
            raise ValueError("A selection batch requires at least one delete command.")
        if any(not isinstance(child, Command) for child in children):
            raise TypeError("Selection batch children must be immutable Application commands.")
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Selection batch requires the active SLD document identity.")
        super().__init__(
            command_type=DELETE_SELECTION_BATCH,
            payload={"commands": children, "document_id": document_id.strip()},
            command_id=command_id or uuid4(),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )



ENGINEERING_UPDATE_BATCH = "model.update_batch"


class EngineeringUpdateBatchCommand(Command):
    """Apply canonical per-equipment update commands as one atomic operation."""

    def __init__(
        self,
        *,
        commands: Sequence[Command],
        command_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> None:
        children = tuple(commands)
        if len(children) < 2:
            raise ValueError("An engineering update batch requires at least two equipment updates.")
        if any(not isinstance(child, Command) for child in children):
            raise TypeError("Engineering update batch children must be immutable Application commands.")
        super().__init__(
            command_type=ENGINEERING_UPDATE_BATCH,
            payload={"commands": children},
            command_id=command_id or uuid4(),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


__all__ = ["DELETE_SELECTION_BATCH", "DeleteSelectionBatchCommand", "ENGINEERING_UPDATE_BATCH", "EngineeringUpdateBatchCommand"]
