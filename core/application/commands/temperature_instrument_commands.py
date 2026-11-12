"""Atomic Application command for replacing temperature instrument inventory."""
from __future__ import annotations

from uuid import UUID, uuid4

from core.model.temperature_instrument import TemperatureInstrumentInventory
from ..command import Command
from ..reversible import ReversibleCommand

SET_TEMPERATURE_INSTRUMENT_INVENTORY = "temperature_instruments.set_inventory"


class SetTemperatureInstrumentInventoryCommand(ReversibleCommand):
    def __init__(self, *, expected_inventory: TemperatureInstrumentInventory,
                 replacement_inventory: TemperatureInstrumentInventory,
                 command_id: UUID | None = None, correlation_id: UUID | None = None,
                 causation_id: UUID | None = None) -> None:
        if not isinstance(expected_inventory, TemperatureInstrumentInventory):
            raise TypeError("expected_inventory must be a TemperatureInstrumentInventory.")
        if not isinstance(replacement_inventory, TemperatureInstrumentInventory):
            raise TypeError("replacement_inventory must be a TemperatureInstrumentInventory.")
        if expected_inventory == replacement_inventory:
            raise ValueError("Replacement temperature inventory must differ from expected inventory.")
        super().__init__(
            command_type=SET_TEMPERATURE_INSTRUMENT_INVENTORY,
            payload={"expected_inventory": expected_inventory, "replacement_inventory": replacement_inventory},
            command_id=command_id or uuid4(), correlation_id=correlation_id, causation_id=causation_id,
        )

    def inverse(self) -> Command:
        return SetTemperatureInstrumentInventoryCommand(
            expected_inventory=self.payload["replacement_inventory"],
            replacement_inventory=self.payload["expected_inventory"],
            correlation_id=self.correlation_id, causation_id=self.command_id,
        )


__all__ = ["SET_TEMPERATURE_INSTRUMENT_INVENTORY", "SetTemperatureInstrumentInventoryCommand"]
