"""Atomic Application command for replacing the project wiring inventory."""

from __future__ import annotations

from uuid import UUID, uuid4

from core.model.instrument_wiring import InstrumentationWiringInventory

from ..command import Command
from ..reversible import ReversibleCommand

SET_INSTRUMENTATION_WIRING_INVENTORY = "instrumentation_wiring.set_inventory"


class SetInstrumentationWiringInventoryCommand(ReversibleCommand):
    """Install one complete validated inventory snapshot as one undoable change."""

    def __init__(
        self,
        *,
        expected_inventory: InstrumentationWiringInventory,
        replacement_inventory: InstrumentationWiringInventory,
        command_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> None:
        if not isinstance(expected_inventory, InstrumentationWiringInventory):
            raise TypeError("expected_inventory must be an InstrumentationWiringInventory.")
        if not isinstance(replacement_inventory, InstrumentationWiringInventory):
            raise TypeError("replacement_inventory must be an InstrumentationWiringInventory.")
        if expected_inventory == replacement_inventory:
            raise ValueError("Replacement inventory must differ from the expected inventory.")
        super().__init__(
            command_type=SET_INSTRUMENTATION_WIRING_INVENTORY,
            payload={
                "expected_inventory": expected_inventory,
                "replacement_inventory": replacement_inventory,
            },
            command_id=command_id or uuid4(),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def inverse(self) -> Command:
        return SetInstrumentationWiringInventoryCommand(
            expected_inventory=self.payload["replacement_inventory"],
            replacement_inventory=self.payload["expected_inventory"],
            correlation_id=self.correlation_id,
            causation_id=self.command_id,
        )


__all__ = [
    "SET_INSTRUMENTATION_WIRING_INVENTORY",
    "SetInstrumentationWiringInventoryCommand",
]
