"""Project-scoped Application authority for BTD/OTD/WTD inventory."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from core.application.command import Command
from core.application.commands.temperature_instrument_commands import (
    SET_TEMPERATURE_INSTRUMENT_INVENTORY, SetTemperatureInstrumentInventoryCommand,
)
from core.application.results import ApplicationResult
from core.application.transaction import Transaction
from core.model.temperature_instrument import (
    TemperatureAlarmTripBinding, TemperatureInstrument, TemperatureInstrumentInventory,
    TemperatureInterfaceDevice,
)


@dataclass(frozen=True, slots=True)
class TemperatureInstrumentServiceState:
    project_id: str | None
    activation_generation: int
    network: Any | None
    inventory: TemperatureInstrumentInventory


class TemperatureInstrumentService:
    """Own temperature inventory activation, terminal resolution and mutation."""

    def __init__(self, instrumentation_wiring_service: Any | None = None) -> None:
        if instrumentation_wiring_service is not None and not hasattr(instrumentation_wiring_service, "inventory"):
            raise TypeError("instrumentation_wiring_service must expose the canonical wiring inventory.")
        self._instrumentation_wiring_service = instrumentation_wiring_service
        self._measurement_channels_provider: Any | None = None
        self._project_id: str | None = None
        self._activation_generation = 0
        self._network: Any | None = None
        self._inventory = TemperatureInstrumentInventory()

    def bind_measurement_channels_provider(self, provider: Any | None) -> None:
        if provider is not None and not callable(provider):
            raise TypeError("measurement channels provider must be callable or None.")
        self._measurement_channels_provider = provider

    def validate_measurement_channels(self, channels: Any | None = None) -> None:
        if channels is None:
            provider = self._measurement_channels_provider
            channels = provider() if provider is not None else {}
        if not isinstance(channels, dict):
            channels = dict(channels)
        interfaces = {item.id: item for item in self._inventory.interfaces}
        for instrument in self._inventory.instruments:
            if instrument.input_mode.value != "DIRECT_SENSOR" and instrument.sensor_input_mappings:
                interface = interfaces[instrument.interface_equipment_id]
                mapped_inputs = {item.interface_terminal.identity for item in instrument.sensor_input_mappings}
                declared_inputs = {item.identity for item in interface.input_terminals}
                mapped_sensors = {item.sensor_terminal.identity for item in instrument.sensor_input_mappings}
                declared_sensors = {item.identity for item in instrument.sensor_terminals}
                if mapped_inputs != declared_inputs or mapped_sensors != declared_sensors:
                    raise ValueError(
                        f"Temperature instrument '{instrument.id}' terminal map must cover every declared sensor and interface input pin exactly once."
                    )
                if any(item.interface_terminal not in interface.input_terminals for item in instrument.sensor_input_mappings):
                    raise ValueError(f"Temperature instrument '{instrument.id}' maps to an undeclared interface input pin.")
            channel_id = instrument.measurement_channel_id
            if channel_id is None:
                continue
            channel = channels.get(channel_id)
            if channel is None:
                raise ValueError(f"Temperature instrument '{instrument.id}' references missing MeasurementChannel '{channel_id}'.")
            if getattr(getattr(channel, "signal_type", None), "value", None) != "TEMPERATURE":
                raise ValueError(f"Temperature instrument '{instrument.id}' requires a TEMPERATURE MeasurementChannel.")
            if instrument.unit is None or channel.unit != instrument.unit:
                raise ValueError(f"Temperature instrument '{instrument.id}' requires an explicit unit matching its MeasurementChannel.")
            expected_source = instrument.sensor_equipment_id
            expected_terminals = instrument.sensor_terminals
            if instrument.input_mode.value != "DIRECT_SENSOR":
                interface = interfaces[instrument.interface_equipment_id]
                expected_source = interface.id
                expected_terminals = interface.output_terminals
                if interface.measurement_channel_id not in (None, channel_id):
                    raise ValueError(f"Temperature interface '{interface.id}' references a different MeasurementChannel.")
                if channel.unit != interface.output_unit:
                    raise ValueError(f"Temperature channel unit does not match interface '{interface.id}' output unit.")
            if getattr(channel, "source_id", None) != expected_source:
                raise ValueError(f"Temperature channel '{channel_id}' source does not match the declared sensor/interface.")
            source_terminal = getattr(channel, "source_terminal", None)
            if source_terminal is None or not any(
                item.equipment_id == expected_source and item.terminal_role == source_terminal.terminal_role
                for item in expected_terminals
            ):
                raise ValueError(f"Temperature channel '{channel_id}' source terminal is not one of the declared physical output terminals.")

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def activation_generation(self) -> int:
        return self._activation_generation

    @property
    def inventory(self) -> TemperatureInstrumentInventory:
        return self._inventory

    def snapshot_state(self) -> TemperatureInstrumentServiceState:
        return TemperatureInstrumentServiceState(
            self._project_id, self._activation_generation, self._network, self._inventory,
        )

    def restore_state(self, state: TemperatureInstrumentServiceState) -> None:
        if not isinstance(state, TemperatureInstrumentServiceState):
            raise TypeError("state must be a TemperatureInstrumentServiceState.")
        self._project_id, self._activation_generation = state.project_id, state.activation_generation
        self._network, self._inventory = state.network, state.inventory

    @staticmethod
    def _validate_terminal(network: Any, reference: Any) -> None:
        try:
            equipment = network.get_by_identity(reference.equipment_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Temperature terminal owner '{reference.equipment_id}' is not registered in the active Network."
            ) from exc
        if getattr(equipment, "id", None) != reference.equipment_id:
            raise ValueError("Temperature terminal resolved to a different Core equipment identity.")
        terminals = getattr(equipment, "terminals", None)
        if terminals is None:
            raise ValueError(f"Temperature terminal owner '{reference.equipment_id}' exposes no Core terminals.")
        matches = [t for t in terminals if t.role == reference.terminal_role and t.owner is equipment]
        if len(matches) != 1:
            raise ValueError(
                f"Temperature terminal {reference.equipment_id}:{reference.terminal_role} "
                f"resolved to {len(matches)} Core terminals; exactly one is required."
            )

    @classmethod
    def _validate_inventory(cls, network: Any, inventory: TemperatureInstrumentInventory) -> None:
        refs = []
        for instrument in inventory.instruments:
            refs.extend(instrument.sensor_terminals)
            refs.extend(instrument.receiver_terminals)
        for interface in inventory.interfaces:
            refs.extend(interface.input_terminals)
            refs.extend(interface.output_terminals)
        for receiver in inventory.receiver_inputs:
            refs.extend(receiver.terminals)
        seen = set()
        for reference in refs:
            if reference.identity in seen:
                continue
            seen.add(reference.identity)
            cls._validate_terminal(network, reference)

    @staticmethod
    def _has_physical_route(
        wiring_inventory: Any, starts: tuple[Any, ...], ends: tuple[Any, ...], *,
        thermocouple_type: str | None = None, polarity: str | None = None,
        thermocouple_cable_specs: tuple[Any, ...] = (),
    ) -> bool:
        """Return whether every declared terminal has a path to the opposite endpoint set.

        Edges come only from ordered terminal paths in canonical cable cores and
        panel wires. Signal bindings and MeasurementChannel IDs are not treated
        as evidence of physical continuity.
        """
        adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {}
        cable_by_id = {item.id: item for item in wiring_inventory.cables}
        for carrier in tuple(wiring_inventory.cores) + tuple(wiring_inventory.panel_wires):
            role = getattr(getattr(carrier, "role", None), "value", getattr(carrier, "role", None))
            if role not in {"SIGNAL", "RETURN", "COMMUNICATION"}:
                continue
            if thermocouple_type is not None:
                if (getattr(carrier, "thermocouple_type", None) != thermocouple_type
                        or getattr(carrier, "thermocouple_polarity", None) != polarity
                        or not getattr(carrier, "conductor_material", None)):
                    continue
                if hasattr(carrier, "cable_id"):
                    cable = cable_by_id.get(carrier.cable_id)
                    if cable is None or not cable.cable_type or not cable.conductor_material:
                        continue
                    designation = cable.cable_type.strip().upper()
                else:
                    designation = getattr(carrier, "thermocouple_cable_designation", None)
                    designation = designation.strip().upper() if designation else None
                spec = next((item for item in thermocouple_cable_specs
                             if item.thermocouple_type == thermocouple_type
                             and item.cable_designation == designation), None)
                if spec is None:
                    continue
                expected_material = (spec.positive_conductor_material if polarity == "POSITIVE"
                                     else spec.negative_conductor_material)
                if carrier.conductor_material.casefold() != expected_material.casefold():
                    continue
            path = tuple(carrier.terminal_path)
            for left, right in zip(path, path[1:]):
                a, b = left.identity, right.identity
                adjacency.setdefault(a, set()).add(b)
                adjacency.setdefault(b, set()).add(a)

        def reachable(start: tuple[str, str], targets: set[tuple[str, str]]) -> bool:
            pending = [start]
            visited = {start}
            while pending:
                current = pending.pop()
                if current in targets:
                    return True
                for neighbour in adjacency.get(current, ()):
                    if neighbour not in visited:
                        visited.add(neighbour)
                        pending.append(neighbour)
            return False

        start_ids = {item.identity for item in starts}
        end_ids = {item.identity for item in ends}
        if not start_ids or not end_ids:
            return False
        return (
            all(reachable(item, end_ids) for item in start_ids)
            and all(reachable(item, start_ids) for item in end_ids)
        )

    @classmethod
    def _validate_physical_continuity(
        cls, inventory: TemperatureInstrumentInventory, wiring_inventory: Any
    ) -> tuple[str, ...]:
        """Fail closed on declared but physically discontinuous temperature loops.

        Records with no terminal map remain explicitly unverified and are
        returned to the caller; this method never invents terminal assignments.
        """
        unverified: list[str] = []
        interfaces = {item.id: item for item in inventory.interfaces}
        for instrument in inventory.instruments:
            interface = interfaces.get(instrument.interface_equipment_id)
            if not instrument.sensor_terminals or not instrument.receiver_terminals:
                unverified.append(instrument.id)
                continue
            if instrument.input_mode.value == "DIRECT_SENSOR":
                if not cls._has_physical_route(
                    wiring_inventory, tuple(instrument.sensor_terminals), tuple(instrument.receiver_terminals)
                ):
                    raise ValueError(
                        f"Temperature instrument '{instrument.id}' has no continuous canonical cable-core/"
                        "panel-wire route for sensor-to-receiver."
                    )
                continue
            if interface is None:
                raise ValueError(
                    f"Temperature instrument '{instrument.id}' has no declared interface for its physical route."
                )
            if (instrument.sensor_technology.value == "THERMOCOUPLE"
                    and instrument.sensor_input_mappings):
                if not instrument.thermocouple_type:
                    raise ValueError(
                        f"Temperature instrument '{instrument.id}' has mapped thermocouple terminals "
                        "but no explicit thermocouple_type."
                    )
                for mapping in instrument.sensor_input_mappings:
                    if not cls._has_physical_route(
                        wiring_inventory, (mapping.sensor_terminal,), (mapping.interface_terminal,),
                        thermocouple_type=instrument.thermocouple_type, polarity=mapping.polarity,
                        thermocouple_cable_specs=inventory.thermocouple_cable_specs,
                    ):
                        raise ValueError(
                            f"Temperature instrument '{instrument.id}' thermocouple {mapping.polarity.lower()} "
                            "conductor path lacks matching type, polarity, conductor material, or declared cable construction."
                        )
            elif instrument.sensor_input_mappings:
                for mapping in instrument.sensor_input_mappings:
                    if not cls._has_physical_route(
                        wiring_inventory, (mapping.sensor_terminal,), (mapping.interface_terminal,)
                    ):
                        raise ValueError(
                            f"Temperature instrument '{instrument.id}' has no continuous canonical "
                            "per-lead sensor-to-interface terminal path."
                        )
            elif not cls._has_physical_route(
                wiring_inventory, tuple(instrument.sensor_terminals), tuple(interface.input_terminals)
            ):
                raise ValueError(
                    f"Temperature instrument '{instrument.id}' has no continuous canonical "
                    "sensor-to-interface-input terminal path."
                )
            if not cls._has_physical_route(
                wiring_inventory, tuple(interface.output_terminals), tuple(instrument.receiver_terminals)
            ):
                raise ValueError(
                    f"Temperature instrument '{instrument.id}' has no continuous canonical "
                    "interface-output-to-receiver terminal path."
                )
        return tuple(unverified)

    def validate_wiring_inventory(self, wiring_inventory: Any) -> tuple[str, ...]:
        """Protect active declared temperature loops from physical wiring edits."""
        if self._project_id is None:
            return ()
        return self._validate_physical_continuity(self._inventory, wiring_inventory)

    def create_replace_command(
        self, replacement: TemperatureInstrumentInventory
    ) -> SetTemperatureInstrumentInventoryCommand:
        if not isinstance(replacement, TemperatureInstrumentInventory):
            raise TypeError("replacement must be a TemperatureInstrumentInventory.")
        return SetTemperatureInstrumentInventoryCommand(
            expected_inventory=self._inventory, replacement_inventory=replacement,
        )

    def create_add_record_command(self, record: Any) -> SetTemperatureInstrumentInventoryCommand:
        if isinstance(record, TemperatureInstrument):
            field = "instruments"
        elif isinstance(record, TemperatureInterfaceDevice):
            field = "interfaces"
        elif isinstance(record, TemperatureAlarmTripBinding):
            field = "alarm_trip_bindings"
        else:
            raise TypeError(f"Unsupported temperature instrument record type: {type(record)!r}.")
        records = getattr(self._inventory, field)
        if any(item.id == record.id for item in records):
            raise ValueError(f"Temperature record ID {record.id!r} already exists.")
        return self.create_replace_command(replace(self._inventory, **{field: records + (record,)}))

    def create_update_record_command(self, record: Any) -> SetTemperatureInstrumentInventoryCommand:
        if isinstance(record, TemperatureInstrument):
            field = "instruments"
        elif isinstance(record, TemperatureInterfaceDevice):
            field = "interfaces"
        elif isinstance(record, TemperatureAlarmTripBinding):
            field = "alarm_trip_bindings"
        else:
            raise TypeError(f"Unsupported temperature instrument record type: {type(record)!r}.")
        records = list(getattr(self._inventory, field))
        matches = [i for i, item in enumerate(records) if item.id == record.id]
        if len(matches) != 1:
            raise KeyError(f"Temperature record {record.id!r} does not exist uniquely.")
        records[matches[0]] = record
        return self.create_replace_command(replace(self._inventory, **{field: tuple(records)}))

    def create_remove_record_command(self, record_type: type, record_id: str) -> SetTemperatureInstrumentInventoryCommand:
        field_by_type = {
            TemperatureInstrument: "instruments",
            TemperatureInterfaceDevice: "interfaces",
            TemperatureAlarmTripBinding: "alarm_trip_bindings",
        }
        try:
            field = field_by_type[record_type]
        except KeyError as exc:
            raise TypeError(f"Unsupported temperature record type: {record_type!r}.") from exc
        records = getattr(self._inventory, field)
        remaining = tuple(item for item in records if item.id != record_id)
        if len(remaining) == len(records):
            raise KeyError(f"Temperature record {record_id!r} does not exist.")
        return self.create_replace_command(replace(self._inventory, **{field: remaining}))

    def supports(self, command: Command) -> bool:
        return command.command_type == SET_TEMPERATURE_INSTRUMENT_INVENTORY

    def execute(self, command: Command, context: Any, transaction: Transaction) -> ApplicationResult:
        if not isinstance(command, SetTemperatureInstrumentInventoryCommand):
            raise TypeError("command must be SetTemperatureInstrumentInventoryCommand.")
        if not isinstance(transaction, Transaction) or not transaction.active:
            raise ValueError("Temperature instrument mutations require an active transaction.")
        network = getattr(context, "network", None)
        if self._project_id is None or network is not self._network:
            raise ValueError("Temperature command does not target the active project Network.")
        if command.payload["expected_inventory"] is not self._inventory:
            raise ValueError("Temperature inventory changed since command preparation.")
        replacement = command.payload["replacement_inventory"]
        self._validate_inventory(network, replacement)
        if self._instrumentation_wiring_service is not None:
            self._validate_physical_continuity(
                replacement, self._instrumentation_wiring_service.inventory
            )
        previous_inventory = self._inventory
        self._inventory = replacement
        try:
            self.validate_measurement_channels()
        except Exception:
            self._inventory = previous_inventory
            raise
        self._inventory = previous_inventory
        previous = self._inventory
        project_id, generation = self._project_id, self._activation_generation
        self._inventory = replacement

        def undo() -> None:
            if (self._project_id != project_id or self._activation_generation != generation
                    or self._network is not network or self._inventory is not replacement):
                raise RuntimeError("Stale temperature inventory undo rejected.")
            self._inventory = previous

        try:
            transaction.record_undo(undo)
        except Exception:
            self._inventory = previous
            raise
        return ApplicationResult.success_result(
            value=replacement, message="Temperature instrument inventory updated.",
            metadata={"project_id": project_id, "activation_generation": generation,
                      "instruments": len(replacement.instruments),
                      "interfaces": len(replacement.interfaces),
                      "alarm_trip_bindings": len(replacement.alarm_trip_bindings)},
        )

    def handlers(self) -> dict[str, Any]:
        return {SET_TEMPERATURE_INSTRUMENT_INVENTORY: self.execute}

    def activate(self, context: Any | None, inventory: TemperatureInstrumentInventory | None = None,
                 *, network: Any | None = None, generation: int = 0) -> TemperatureInstrumentServiceState:
        candidate = inventory if inventory is not None else TemperatureInstrumentInventory()
        if not isinstance(candidate, TemperatureInstrumentInventory):
            raise TypeError("inventory must be a TemperatureInstrumentInventory.")
        if context is None:
            if candidate != TemperatureInstrumentInventory():
                raise ValueError("A project-less activation cannot contain temperature instruments.")
            next_project, next_generation, next_network = None, 0, None
        else:
            project_id = getattr(context, "project_id", None)
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("Project context must expose a non-empty project_id.")
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
                raise ValueError("generation must be a positive integer.")
            if network is None or not callable(getattr(network, "get_by_identity", None)):
                raise ValueError("Active temperature instruments require the canonical Core Network.")
            self._validate_inventory(network, candidate)
            if self._instrumentation_wiring_service is not None:
                self._validate_physical_continuity(
                    candidate, self._instrumentation_wiring_service.inventory
                )
            next_project, next_generation, next_network = project_id.strip(), generation, network
        previous = self.snapshot_state()
        self._project_id, self._activation_generation = next_project, next_generation
        self._network, self._inventory = next_network, candidate
        return previous


class TemperatureInstrumentCommandHandlers:
    def __init__(self, service: TemperatureInstrumentService) -> None:
        if not isinstance(service, TemperatureInstrumentService):
            raise TypeError("service must be a TemperatureInstrumentService.")
        self._service = service

    def handlers(self) -> dict[str, Any]:
        return self._service.handlers()


__all__ = ["TemperatureInstrumentService", "TemperatureInstrumentServiceState", "TemperatureInstrumentCommandHandlers"]
