"""Project-scoped Application authority for physical instrumentation wiring."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from typing import Any

from core.application.command import Command
from core.application.commands.instrumentation_wiring_commands import (
    SET_INSTRUMENTATION_WIRING_INVENTORY,
    SetInstrumentationWiringInventoryCommand,
)
from core.application.results import ApplicationResult
from core.application.read_models import InstrumentationWiringReadModel
from core.application.transaction import Transaction
from core.model.instrument_wiring import (
    CableAssembly, CableCore, InstrumentationWiringInventory, PanelWire,
    SignalCarrierBinding, SignalDefinition, PhysicalConnectionHop,
)


@dataclass(frozen=True, slots=True)
class InstrumentationWiringServiceState:
    project_id: str | None
    activation_generation: int
    network: Any | None
    inventory: InstrumentationWiringInventory


class InstrumentationWiringService:
    """Project lifecycle, canonical terminal validation and controlled mutation."""

    def __init__(self) -> None:
        self._inventory_validator: Any | None = None
        self._receiver_validator: Any | None = None
        self._project_id: str | None = None
        self._activation_generation = 0
        self._network: Any | None = None
        self._inventory = InstrumentationWiringInventory()

    def bind_inventory_validator(self, validator: Any | None) -> None:
        """Bind cross-domain validation before replacing physical wiring."""
        if validator is not None and not callable(validator):
            raise TypeError("inventory validator must be callable or None.")
        self._inventory_validator = validator

    def bind_receiver_validator(self, validator: Any | None) -> None:
        """Bind Control/Protection reconciliation against active project registries."""
        if validator is not None and not callable(validator):
            raise TypeError("receiver validator must be callable or None.")
        self._receiver_validator = validator

    def validate_receiver_bindings(
        self, inventory: InstrumentationWiringInventory | None = None
    ) -> None:
        candidate = self._inventory if inventory is None else inventory
        if not isinstance(candidate, InstrumentationWiringInventory):
            raise TypeError("inventory must be an InstrumentationWiringInventory.")
        if self._receiver_validator is not None:
            self._receiver_validator(candidate)

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def activation_generation(self) -> int:
        return self._activation_generation

    @property
    def inventory(self) -> InstrumentationWiringInventory:
        return self._inventory

    def snapshot_state(self) -> InstrumentationWiringServiceState:
        return InstrumentationWiringServiceState(
            self._project_id, self._activation_generation,
            self._network, self._inventory,
        )

    def restore_state(self, state: InstrumentationWiringServiceState) -> None:
        if not isinstance(state, InstrumentationWiringServiceState):
            raise TypeError("state must be an InstrumentationWiringServiceState.")
        self._project_id = state.project_id
        self._activation_generation = state.activation_generation
        self._network = state.network
        self._inventory = state.inventory

    @staticmethod
    def _validate_terminal_reference(network: Any, reference: Any) -> None:
        """Resolve the existing Core identity: equipment ID plus exact role."""
        try:
            equipment = network.get_by_identity(reference.equipment_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Wiring terminal owner '{reference.equipment_id}' is not registered in the active Network."
            ) from exc
        if getattr(equipment, "id", None) != reference.equipment_id:
            raise ValueError(
                f"Wiring terminal owner '{reference.equipment_id}' resolved to a different Core identity."
            )
        terminals = getattr(equipment, "terminals", None)
        if terminals is None:
            raise ValueError(
                f"Wiring terminal owner '{reference.equipment_id}' does not expose Core terminals."
            )
        matches = [
            terminal for terminal in terminals
            if terminal.role == reference.terminal_role and terminal.owner is equipment
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Wiring terminal reference {reference.equipment_id}:{reference.terminal_role} "
                f"resolved to {len(matches)} Core terminals; exactly one is required."
            )

    @classmethod
    def _validate_inventory_terminals(cls, network: Any, inventory: InstrumentationWiringInventory) -> None:
        references = []
        for core in inventory.cores:
            references.extend(core.terminal_path)
        for wire in inventory.panel_wires:
            references.extend(wire.terminal_path)
        seen: set[tuple[str, str]] = set()
        for reference in references:
            if reference.identity in seen:
                continue
            seen.add(reference.identity)
            cls._validate_terminal_reference(network, reference)

    @staticmethod
    def _read_value(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value) and not isinstance(value, type):
            return {
                item.name: InstrumentationWiringService._read_value(getattr(value, item.name))
                for item in fields(value)
            }
        if isinstance(value, tuple):
            return tuple(InstrumentationWiringService._read_value(item) for item in value)
        if isinstance(value, list):
            return tuple(InstrumentationWiringService._read_value(item) for item in value)
        if isinstance(value, dict):
            return {
                key: InstrumentationWiringService._read_value(item)
                for key, item in value.items()
            }
        return value

    def read_model(self) -> InstrumentationWiringReadModel:
        """Return a detached, immutable read model for the active project only."""
        if self._project_id is None or self._activation_generation < 1:
            raise RuntimeError("No active project instrumentation wiring inventory.")
        inventory = self._inventory
        return InstrumentationWiringReadModel(
            project_id=self._project_id,
            activation_generation=self._activation_generation,
            cables=tuple(self._read_value(item) for item in inventory.cables),
            cores=tuple(self._read_value(item) for item in inventory.cores),
            panel_wires=tuple(self._read_value(item) for item in inventory.panel_wires),
            signals=tuple(self._read_value(item) for item in inventory.signals),
            signal_bindings=tuple(self._read_value(item) for item in inventory.signal_bindings),
            tag_policy=tuple(self._read_value(item) for item in inventory.tag_policy.series),
            connection_hops=tuple(self._read_value(item) for item in inventory.connection_hops),
        )

    def allocate_tag(
        self, record_kind: str
    ) -> tuple[str, InstrumentationWiringInventory]:
        """Prepare one project-policy tag and the inventory carrying its advanced counter.

        The returned inventory is not installed until the caller submits it
        through create_replace_command() and the normal Application command path.
        """
        kind = str(record_kind).strip().upper()
        used_by_kind = {
            "CABLE": tuple(item.tag for item in self._inventory.cables),
            "CORE": tuple(item.core_tag for item in self._inventory.cores if item.core_tag is not None),
            "PANEL_WIRE": tuple(item.tag for item in self._inventory.panel_wires),
            "SIGNAL": tuple(item.tag for item in self._inventory.signals),
        }
        if kind not in used_by_kind:
            raise ValueError(f"Unsupported instrumentation wiring tag category: {kind!r}.")
        all_used_tags = tuple(tag for values in used_by_kind.values() for tag in values)
        tag, updated_policy = self._inventory.tag_policy.allocate(
            kind, used_tags=all_used_tags
        )
        return tag, replace(self._inventory, tag_policy=updated_policy)

    @staticmethod
    def _record_collection(record_or_type: Any) -> str:
        record_type = record_or_type if isinstance(record_or_type, type) else type(record_or_type)
        collections = {
            CableAssembly: "cables",
            CableCore: "cores",
            PanelWire: "panel_wires",
            SignalDefinition: "signals",
            SignalCarrierBinding: "signal_bindings",
            PhysicalConnectionHop: "connection_hops",
        }
        try:
            return collections[record_type]
        except KeyError as exc:
            raise TypeError(f"Unsupported instrumentation wiring record type: {record_type!r}.") from exc

    def _tag_policy_candidate(self, candidate: InstrumentationWiringInventory | None) -> InstrumentationWiringInventory:
        if candidate is None:
            return self._inventory
        if not isinstance(candidate, InstrumentationWiringInventory):
            raise TypeError("tag_policy_inventory must be an InstrumentationWiringInventory.")
        for field in ("cables", "cores", "panel_wires", "signals", "signal_bindings", "connection_hops"):
            if getattr(candidate, field) != getattr(self._inventory, field):
                raise ValueError("Tag allocation candidate cannot contain record mutations.")
        return candidate

    def create_add_record_command(self, record: Any, *, tag_policy_inventory: InstrumentationWiringInventory | None = None) -> SetInstrumentationWiringInventoryCommand:
        """Prepare a typed record addition; aggregate checks reject dangling dependencies."""
        field = self._record_collection(record)
        base = self._tag_policy_candidate(tag_policy_inventory)
        records = getattr(base, field)
        if any(item.id == record.id for item in records):
            raise ValueError(f"Instrumentation wiring record ID {record.id!r} already exists.")
        return self.create_replace_command(replace(base, **{field: records + (record,)}))

    def create_update_record_command(self, record: Any) -> SetInstrumentationWiringInventoryCommand:
        """Prepare a record update without permitting stable identity replacement."""
        field = self._record_collection(record)
        records = list(getattr(self._inventory, field))
        matches = [index for index, item in enumerate(records) if item.id == record.id]
        if len(matches) != 1:
            raise KeyError(f"Instrumentation wiring record {record.id!r} does not exist uniquely.")
        records[matches[0]] = record
        return self.create_replace_command(replace(self._inventory, **{field: tuple(records)}))

    def create_remove_record_command(self, record_type: type, record_id: str) -> SetInstrumentationWiringInventoryCommand:
        """Prepare a removal; dependent records must be removed before their owner."""
        field = self._record_collection(record_type)
        if not isinstance(record_id, str) or not record_id.strip() or record_id != record_id.strip():
            raise ValueError("record_id must be a canonical non-empty string.")
        records = getattr(self._inventory, field)
        remaining = tuple(item for item in records if item.id != record_id)
        if len(remaining) == len(records):
            raise KeyError(f"Instrumentation wiring record {record_id!r} does not exist.")
        return self.create_replace_command(replace(self._inventory, **{field: remaining}))
    def create_update_carrier_route_command(
        self,
        carrier_kind: Any,
        carrier_id: str,
        terminal_path: tuple[Any, ...] | list[Any],
        *,
        new_hop_ids: tuple[str, ...] | list[str] = (),
    ) -> SetInstrumentationWiringInventoryCommand:
        """Prepare a route revision while retaining IDs for unchanged segments.

        new_hop_ids supplies explicit IDs only for newly introduced adjacent
        segments, in route order. Removed active segments remain as RETIRED
        history records; no ID is generated or inferred.
        """
        from core.model.instrument_wiring import CarrierKind, WiringTerminalReference

        if not isinstance(carrier_kind, CarrierKind):
            raise TypeError("carrier_kind must be a CarrierKind.")
        if carrier_kind not in {CarrierKind.CABLE_CORE, CarrierKind.PANEL_WIRE}:
            raise ValueError("Only cable-core and panel-wire routes can be revised.")
        if not isinstance(carrier_id, str) or not carrier_id.strip():
            raise ValueError("carrier_id must be a non-empty string.")
        path = tuple(terminal_path)
        if len(path) < 2 or any(not isinstance(item, WiringTerminalReference) for item in path):
            raise ValueError("terminal_path must contain at least two terminal references.")
        if len({item.identity for item in path}) != len(path):
            raise ValueError("terminal_path must not repeat a terminal.")
        supplied_ids = tuple(new_hop_ids)
        if any(not isinstance(item, str) or not item.strip() for item in supplied_ids):
            raise ValueError("new_hop_ids must contain non-empty explicit IDs.")

        field = "cores" if carrier_kind is CarrierKind.CABLE_CORE else "panel_wires"
        records = list(getattr(self._inventory, field))
        matches = [index for index, item in enumerate(records) if item.id == carrier_id]
        if len(matches) != 1:
            raise KeyError(f"Instrumentation wiring carrier {carrier_id!r} does not exist uniquely.")
        carrier_index = matches[0]
        carrier = records[carrier_index]
        new_pairs = [
            frozenset((path[index].identity, path[index + 1].identity))
            for index in range(len(path) - 1)
        ]
        if len(new_pairs) != len(set(new_pairs)):
            raise ValueError("A route cannot contain a repeated physical segment.")

        existing = [
            hop for hop in self._inventory.connection_hops
            if hop.carrier_kind is carrier_kind and hop.carrier_id == carrier_id
        ]
        active_by_pair = {
            frozenset((hop.from_terminal.identity, hop.to_terminal.identity)): hop
            for hop in existing if hop.lifecycle_status in {"PLANNED", "INSTALLED"}
        }
        preserved_pairs = set(active_by_pair).intersection(new_pairs)
        added_pairs = [pair for pair in new_pairs if pair not in preserved_pairs]
        if len(supplied_ids) != len(added_pairs):
            raise ValueError(
                f"Route revision requires exactly {len(added_pairs)} new_hop_ids; "
                f"received {len(supplied_ids)}."
            )
        existing_ids = {item.id for item in self._inventory.connection_hops}
        if len(supplied_ids) != len(set(supplied_ids)) or existing_ids.intersection(supplied_ids):
            raise ValueError("New hop IDs must be unique and must not reuse existing hop IDs.")

        new_hops = []
        id_iter = iter(supplied_ids)
        for index in range(len(path) - 1):
            left, right = path[index], path[index + 1]
            pair = frozenset((left.identity, right.identity))
            if pair in preserved_pairs:
                continue
            new_hops.append(PhysicalConnectionHop(
                id=next(id_iter),
                from_terminal=left,
                to_terminal=right,
                carrier_kind=carrier_kind,
                carrier_id=carrier_id,
                revision=1,
                lifecycle_status="PLANNED",
            ))

        revised_hops = []
        for hop in self._inventory.connection_hops:
            if hop.carrier_kind is not carrier_kind or hop.carrier_id != carrier_id:
                revised_hops.append(hop)
                continue
            pair = frozenset((hop.from_terminal.identity, hop.to_terminal.identity))
            if hop.lifecycle_status in {"PLANNED", "INSTALLED"} and pair not in new_pairs:
                revised_hops.append(replace(
                    hop, revision=hop.revision + 1, lifecycle_status="RETIRED",
                ))
            else:
                revised_hops.append(hop)
        revised_hops.extend(new_hops)

        records[carrier_index] = replace(
            carrier, terminal_path=path, revision=carrier.revision + 1,
        )
        replacement = replace(
            self._inventory,
            **{field: tuple(records), "connection_hops": tuple(revised_hops)},
        )
        return self.create_replace_command(replacement)

    def create_replace_command(
        self, replacement: InstrumentationWiringInventory
    ) -> SetInstrumentationWiringInventoryCommand:
        """Capture the current snapshot as a stale-state precondition."""
        return SetInstrumentationWiringInventoryCommand(
            expected_inventory=self._inventory,
            replacement_inventory=replacement,
        )

    def supports(self, command: Command) -> bool:
        return command.command_type == SET_INSTRUMENTATION_WIRING_INVENTORY

    def execute(
        self, command: Command, context: Any, transaction: Transaction
    ) -> ApplicationResult:
        """Apply one atomic inventory replacement with a stale-safe undo journal."""
        if not isinstance(command, SetInstrumentationWiringInventoryCommand):
            raise TypeError("command must be a SetInstrumentationWiringInventoryCommand.")
        if not isinstance(transaction, Transaction) or not transaction.active:
            raise ValueError("Instrumentation wiring mutations require an active transaction.")
        network = getattr(context, "network", None)
        if self._project_id is None or self._network is None or network is not self._network:
            raise ValueError("Instrumentation wiring command does not target the active project Network.")
        expected = command.payload["expected_inventory"]
        replacement = command.payload["replacement_inventory"]
        if self._inventory is not expected:
            raise ValueError("Instrumentation wiring inventory changed since the command was prepared.")
        self._validate_inventory_terminals(network, replacement)
        if self._inventory_validator is not None:
            self._inventory_validator(replacement)
        self.validate_receiver_bindings(replacement)
        previous = self._inventory
        project_id = self._project_id
        generation = self._activation_generation
        self._inventory = replacement

        def undo() -> None:
            if (
                self._project_id != project_id
                or self._activation_generation != generation
                or self._network is not network
                or self._inventory is not replacement
            ):
                raise RuntimeError(
                    "Stale instrumentation wiring undo rejected; active project or inventory changed."
                )
            self._inventory = previous

        try:
            transaction.record_undo(undo)
        except Exception:
            self._inventory = previous
            raise
        return ApplicationResult.success_result(
            value=replacement,
            message="Instrumentation wiring inventory updated.",
            metadata={
                "project_id": project_id,
                "activation_generation": generation,
                "cables": len(replacement.cables),
                "cores": len(replacement.cores),
                "panel_wires": len(replacement.panel_wires),
                "signals": len(replacement.signals),
            },
        )

    def handlers(self) -> dict[str, Any]:
        return {SET_INSTRUMENTATION_WIRING_INVENTORY: self.execute}

    def activate(
        self,
        context: Any | None,
        inventory: InstrumentationWiringInventory | None = None,
        *,
        network: Any | None = None,
        generation: int = 0,
    ) -> InstrumentationWiringServiceState:
        """Install a candidate project inventory after validating terminal owners."""
        candidate = inventory if inventory is not None else InstrumentationWiringInventory()
        if not isinstance(candidate, InstrumentationWiringInventory):
            raise TypeError("inventory must be an InstrumentationWiringInventory.")
        if context is None:
            if candidate != InstrumentationWiringInventory():
                raise ValueError("A project-less activation cannot contain instrumentation wiring.")
            next_project_id = None
            next_generation = 0
            next_network = None
        else:
            project_id = getattr(context, "project_id", None)
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("Project context must expose a non-empty project_id.")
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
                raise ValueError("generation must be a positive integer for an active project.")
            if network is None or not callable(getattr(network, "get_by_identity", None)):
                raise ValueError("An active project wiring inventory requires the canonical Core Network.")
            self._validate_inventory_terminals(network, candidate)
            next_project_id = project_id.strip()
            next_generation = generation
            next_network = network
        previous = self.snapshot_state()
        self._project_id = next_project_id
        self._activation_generation = next_generation
        self._network = next_network
        self._inventory = candidate
        return previous


class InstrumentationWiringCommandHandlers:
    """Bind wiring inventory commands to the project-scoped Application service."""

    def __init__(self, service: InstrumentationWiringService) -> None:
        if not isinstance(service, InstrumentationWiringService):
            raise TypeError("service must be an InstrumentationWiringService.")
        self._service = service

    def handlers(self) -> dict[str, Any]:
        return self._service.handlers()


__all__ = [
    "InstrumentationWiringService",
    "InstrumentationWiringServiceState",
    "InstrumentationWiringCommandHandlers",
]
