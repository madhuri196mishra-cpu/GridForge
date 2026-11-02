# ============================================================
# File: core/application/services/sld_service.py
# GridForge V2 — Application SLD presentation service
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any
import math

from ..command import Command
from ..errors import ValidationError
from ..results import ApplicationResult
from ..transaction import Transaction
from ui.sld.sld_model import SLDEndpoint


@dataclass(frozen=True, slots=True)
class SLDState:
    """Immutable snapshot of presentation-only SLD state."""
    nodes: tuple[dict[str, Any], ...] = ()
    connections: tuple[dict[str, Any], ...] = ()


class SLDService:
    """Application boundary for persistent SLD presentation state.

    The service performs presentation mutation only inside the Application
    transaction supplied by CommandManager. It never owns history or undo/redo.
    """

    INSERTION_POSITION_TOLERANCE = 5.0
    INSERTION_POSITION_TOLERANCE_SQ = INSERTION_POSITION_TOLERANCE ** 2

    COMMAND_TYPES = frozenset({
        "sld.set_node_position",
        "sld.set_node_presentation",
        "sld.add_node",
        "sld.remove_node",
        "sld.add_connection",
        "sld.remove_connection",
        "sld.set_connection_route",
        "sld.set_node_properties",
        "sld.set_annotation_blocks",
    })

    def __init__(
        self,
        document: Any,
        *,
        application: Any = None,
        symbol_presentation_factory: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._document = None
        self._application = application
        self._symbol_presentation_factory = symbol_presentation_factory
        if application is not None:
            self.attach_application(application)
        self.bind_document(document)

    @property
    def document(self) -> Any:
        if self._document is None:
            raise RuntimeError("SLD service has no active document.")
        return self._document

    @property
    def is_bound(self) -> bool:
        return self._document is not None

    def bind_document(self, document: Any) -> None:
        """Bind only the Application-authoritative active presentation document."""
        if document is None:
            raise TypeError("SLDService requires an SLD document.")
        if self._application is not None:
            presentation = getattr(self._application, "presentation", None)
            if presentation is not document:
                raise RuntimeError(
                    "SLDService cannot bind a document that is not the "
                    "Application-authoritative active presentation."
                )
        self._document = document

    @staticmethod
    def presentation_connection_id(core_connection_id: str) -> str:
        """Return the deterministic persistent SLD presentation identity for a Core connection."""
        if not isinstance(core_connection_id, str) or not core_connection_id:
            raise ValueError("core_connection_id must be a non-empty string")
        return f"sld-wire-{core_connection_id}"

    def audit_simple_wire_projection(self, network: Any) -> tuple[dict[str, Any], ...]:
        """Audit Core/SLD Simple-Wire consistency without mutating the document.

        This method is the activation/read-side counterpart to the mutating
        reconcile_simple_wire_projection method. It never creates, removes, or
        rewrites persistent SLD state and never marks the document modified.
        """
        if network is None or not hasattr(network, "connectivity"):
            raise TypeError("network must expose the authoritative connectivity aggregate")

        core_connections = tuple(network.connectivity.connections)
        core_by_id = {str(connection.connection_id): connection for connection in core_connections}
        diagnostics: list[dict[str, Any]] = []
        companions: dict[str, Any] = {}

        for connection in self.document.model.connections:
            properties = connection.properties
            kind = str(properties.get("connection_kind", "")).upper()
            lifecycle = str(properties.get("lifecycle_state", "BOUND")).upper()
            core_id = properties.get("core_connection_id")
            owner = str(properties.get("presentation_owner", "")).lower()

            if kind != "SIMPLE_WIRE" and not core_id:
                continue
            if kind != "SIMPLE_WIRE":
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "reason": "core_connection_mapping_on_non_simple_wire",
                })
                continue
            if lifecycle == "ORPHANED":
                continue
            if lifecycle != "BOUND":
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "reason": f"unsupported_lifecycle_state:{lifecycle}",
                })
                continue
            if not core_id:
                diagnostics.append({
                    "code": "SLD_WIRE_WITHOUT_CORE",
                    "connection_id": str(connection.connection_id),
                })
                continue

            core_id = str(core_id)
            expected_presentation_id = self.presentation_connection_id(core_id)
            if str(connection.connection_id) != expected_presentation_id:
                diagnostics.append({
                    "code": "PRESENTATION_ID_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "expected_connection_id": expected_presentation_id,
                    "core_connection_id": core_id,
                })
            if core_id in companions:
                diagnostics.append({
                    "code": "DUPLICATE_PRESENTATION_COMPANION",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                })
                continue

            companions[core_id] = connection
            if core_id not in core_by_id:
                diagnostics.append({
                    "code": "SLD_WIRE_WITHOUT_CORE",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                    "presentation_owner": owner,
                })
                continue

            core = core_by_id[core_id]
            expected_a = self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping())
            expected_b = self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping())
            actual_a = connection.source_endpoint.to_dict() if connection.source_endpoint is not None else None
            actual_b = connection.target_endpoint.to_dict() if connection.target_endpoint is not None else None
            if actual_a != expected_a or actual_b != expected_b:
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                    "presentation_owner": owner,
                })

        for core_id in core_by_id:
            if core_id not in companions:
                diagnostics.append({
                    "code": "CORE_WIRE_WITHOUT_SLD_COMPANION",
                    "connection_id": core_id,
                })
        return tuple(diagnostics)

    def reconcile_simple_wire_projection(self, network: Any) -> Callable[[], None]:
        """Reconcile persistent projection companions against authoritative Core Simple Wires.

        This activation-boundary operation returns an identity-aware inverse.
        Only connections created or fields changed by this reconciliation are
        reverted; untouched connection objects are never removed/recreated.
        """
        if network is None or not hasattr(network, "connectivity"):
            raise TypeError("network must expose the authoritative connectivity aggregate")

        original_connections = tuple(self.document.model.connections)
        previous = {
            str(connection.connection_id): connection.to_dict()
            for connection in original_connections
        }
        original_by_id = {
            str(connection.connection_id): connection
            for connection in original_connections
        }
        previous_modified = bool(self.document.modified)
        core_connections = tuple(network.connectivity.connections)
        core_by_id = {str(connection.connection_id): connection for connection in core_connections}
        created_connections = {}
        attempted_creations = {}
        changed = False

        def restore_original_fields(connection: Any, snapshot: Mapping[str, Any]) -> None:
            source = snapshot.get("source_endpoint")
            target = snapshot.get("target_endpoint")
            connection.source_endpoint = None if source is None else SLDEndpoint.from_dict(source)
            connection.target_endpoint = None if target is None else SLDEndpoint.from_dict(target)
            connection.properties.clear()
            connection.properties.update(deepcopy(dict(snapshot.get("properties", {}))))

        def rollback() -> None:
            collisions = []
            # Remove only exact objects created by this reconciliation. If
            # creation raised after partial insertion, use its recorded
            # signature and remove only a matching projection-owned object.
            for connection_id, created in tuple(created_connections.items()):
                current = self.document.model.get_connection_optional(connection_id)
                if current is None:
                    continue
                if current is not created:
                    collisions.append(connection_id)
                    continue
                self.document.model.remove_connection(connection_id)

            for connection_id, expected in tuple(attempted_creations.items()):
                if connection_id in created_connections:
                    continue
                current = self.document.model.get_connection_optional(connection_id)
                if current is None:
                    continue
                source = current.source_endpoint.to_dict() if current.source_endpoint is not None else None
                target = current.target_endpoint.to_dict() if current.target_endpoint is not None else None
                current_route = (
                    current.route.to_dict()
                    if getattr(current, "route", None) is not None
                    else None
                )
                if (
                    current.properties.get("projection_source") == "core_reconciliation"
                    and str(current.properties.get("core_connection_id")) == expected["core_connection_id"]
                    and current.source_node_id == expected["source_node_id"]
                    and current.target_node_id == expected["target_node_id"]
                    and source == expected["source_endpoint"]
                    and target == expected["target_endpoint"]
                    and current_route == expected["route"]
                    and dict(current.properties) == expected["properties"]
                ):
                    self.document.model.remove_connection(connection_id)
                else:
                    collisions.append(connection_id)

            # Restore only original identities whose serialized state actually
            # changed or disappeared. Untouched engineer-owned connections keep
            # both their object identity and all externally held references.
            for connection_id, snapshot in previous.items():
                original = original_by_id[connection_id]
                current = self.document.model.get_connection_optional(connection_id)
                if current is original:
                    if current.to_dict() != snapshot:
                        restore_original_fields(current, snapshot)
                elif current is None:
                    self._restore_connection_snapshot(snapshot)
                else:
                    collisions.append(connection_id)

            # Reconciliation is a projection repair. Its compensation must
            # restore the document's pre-activation dirty marker, not make an
            # otherwise clean document dirty merely because rollback ran.
            if previous_modified:
                self.document.mark_modified()
            else:
                self.document.mark_clean()
            if collisions:
                raise ValueError(
                    "SLD wire reconciliation rollback encountered conflicting identity owners: "
                    + ", ".join(sorted(set(collisions)))
                )

        companions = {}
        try:
            for connection in original_connections:
                kind = str(connection.properties.get("connection_kind", "")).upper()
                lifecycle = str(connection.properties.get("lifecycle_state", "BOUND")).upper()
                core_id = connection.properties.get("core_connection_id")

                if kind != "SIMPLE_WIRE" and not core_id:
                    continue
                if kind != "SIMPLE_WIRE":
                    raise ValueError(
                        f"SLD connection {connection.connection_id!r} has Core connection mapping but kind {kind!r}, expected SIMPLE_WIRE."
                    )

                # Engineer-owned orphaned artifacts are not active companions.
                if lifecycle == "ORPHANED":
                    if connection.properties.get("presentation_owner") != "engineer":
                        raise ValueError(
                            f"SLD Simple Wire {connection.connection_id!r} is ORPHANED "
                            "but is not engineer-owned."
                        )
                    if core_id is not None:
                        raise ValueError(
                            f"ORPHANED SLD Simple Wire {connection.connection_id!r} "
                            "must not retain an active Core connection binding."
                        )
                    continue

                if lifecycle != "BOUND":
                    raise ValueError(
                        f"SLD Simple Wire {connection.connection_id!r} has unsupported lifecycle state {lifecycle!r}."
                    )

                if not core_id and str(connection.connection_id).startswith("sld-wire-"):
                    core_id = str(connection.connection_id)[len("sld-wire-"):]
                    connection.properties["core_connection_id"] = core_id
                    changed = True
                if not core_id:
                    raise ValueError(
                        f"SLD Simple Wire {connection.connection_id!r} has no Core connection mapping."
                    )
                core_id = str(core_id)
                if core_id in companions:
                    raise ValueError(
                        f"Multiple SLD Simple Wire companions map to Core connection {core_id!r}."
                    )
                companions[core_id] = connection

            for core_id, core in core_by_id.items():
                if core_id in companions:
                    connection = companions[core_id]
                    expected_a = self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping())
                    expected_b = self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping())
                    actual_a = connection.source_endpoint.to_dict() if connection.source_endpoint is not None else None
                    actual_b = connection.target_endpoint.to_dict() if connection.target_endpoint is not None else None
                    if connection.properties.get("connection_kind") != "SIMPLE_WIRE":
                        raise ValueError(f"SLD connection {connection.connection_id!r} has an invalid connection kind.")
                    if actual_a != expected_a or actual_b != expected_b:
                        if connection.properties.get("presentation_owner") != "projection":
                            raise ValueError(
                                f"Engineer-owned SLD Simple Wire {connection.connection_id!r} conflicts with Core endpoints."
                            )
                        # Update the existing canonical connection in place.
                        # Replacing it would invalidate references held by
                        # projections and other activation participants.
                        connection.source_endpoint = SLDEndpoint.from_dict(expected_a)
                        connection.target_endpoint = SLDEndpoint.from_dict(expected_b)
                        connection.properties["core_connection_id"] = core_id
                        changed = True
                    continue

                presentation_id = self.presentation_connection_id(core_id)
                if self.document.model.get_connection_optional(presentation_id) is not None:
                    raise ValueError(
                        f"SLD connection identity collision for Core Simple Wire {core_id!r}: {presentation_id!r}."
                    )
                source = self._sld_node_for_endpoint_mapping(core.endpoint_a.to_mapping())
                target = self._sld_node_for_endpoint_mapping(core.endpoint_b.to_mapping())
                source_endpoint = self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping())
                target_endpoint = self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping())
                expected_properties = {
                    "connection_kind": "SIMPLE_WIRE",
                    "presentation_owner": "projection",
                    "projection_source": "core_reconciliation",
                    "core_connection_id": core_id,
                }
                attempted_creations[presentation_id] = {
                    "core_connection_id": core_id,
                    "source_node_id": source,
                    "target_node_id": target,
                    "source_endpoint": source_endpoint.to_dict(),
                    "target_endpoint": target_endpoint.to_dict(),
                    "route": {
                        "routing_mode": "orthogonal",
                        "ownership": "auto",
                        "points": [],
                    },
                    "properties": expected_properties,
                }
                self.document.model.create_connection(
                    connection_id=presentation_id,
                    source_node_id=source,
                    target_node_id=target,
                    source_endpoint=source_endpoint,
                    target_endpoint=target_endpoint,
                    properties=expected_properties,
                )
                created = self.document.model.get_connection(presentation_id)
                created_connections[presentation_id] = created
                companions[core_id] = created
                changed = True

            for core_id, connection in companions.items():
                if core_id not in core_by_id:
                    raise ValueError(
                        f"SLD Simple Wire {connection.connection_id!r} has no authoritative Core connection {core_id!r}."
                    )

            if changed:
                self.document.mark_modified()
        except Exception:
            rollback()
            raise

        return rollback

    def _sld_node_for_endpoint_mapping(self, mapping: Mapping[str, Any]) -> str:
        object_id = str(mapping.get("object_id") or "")
        if not object_id:
            raise ValueError("Simple Wire endpoint mapping requires object_id.")
        nodes = tuple(
            node for node in self.document.model.nodes
            if str(getattr(node, "equipment_id", "") or "") == object_id
        )
        if len(nodes) != 1:
            raise ValueError(
                f"Expected exactly one SLD node for Core endpoint object {object_id!r}; found {len(nodes)}."
            )
        return str(nodes[0].node_id)

    @staticmethod
    def _sld_endpoint_from_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "kind": "bus" if mapping.get("kind") == "bus" else "equipment",
            "node_id": str(mapping.get("object_id") or ""),
            "equipment_id": mapping.get("object_id") if mapping.get("kind") != "bus" else None,
            "terminal_role": mapping.get("terminal_role") if mapping.get("kind") != "bus" else None,
            "bus_id": mapping.get("object_id") if mapping.get("kind") == "bus" else None,
            "attachment_id": mapping.get("attachment_id") if mapping.get("kind") == "bus" else None,
        }

    def attach_application(self, application: Any) -> None:
        """Attach the Application whose presentation state is authoritative."""
        if application is None:
            raise TypeError("application must not be None")
        self._application = application
        if self._document is not None:
            presentation = getattr(application, "presentation", None)
            # Permit composition before the project-lifecycle presentation
            # contract is wired. Once an Application presentation exists, the
            # service remains bound to that exact authoritative document.
            if presentation is not None and presentation is not self._document:
                raise RuntimeError(
                    "Existing SLD document does not match the Application presentation."
                )

    @property
    def application(self) -> Any:
        return self._application

    def detach_document(self) -> Any:
        """Detach the active document so closed projects cannot be mutated."""
        document = self._document
        self._document = None
        return document

    def supports(self, command: Command) -> bool:
        return command.command_type in self.COMMAND_TYPES

    def execute(
        self,
        command: Command,
        transaction: Transaction,
        *,
        context: Any = None,
        authoritative_value: Any = None,
    ) -> ApplicationResult:
        """Apply one SLD command inside the canonical Application transaction.

        authoritative_value is the transaction-visible result of an
        originating Core mutation when SLD binding occurs before commit.
        """
        if not isinstance(command, Command):
            raise TypeError("command must be a Command")
        if not isinstance(transaction, Transaction):
            raise TypeError("transaction must be a Transaction")
        if not self.supports(command):
            raise ValueError(f"Unsupported SLD command: {command.command_type}")
        handler = {
            "sld.set_node_position": self._set_node_position,
            "sld.set_node_presentation": self._set_node_presentation,
            "sld.add_node": lambda cmd, tx: self._add_node(
                cmd, tx, authoritative_value=authoritative_value
            ),
            "sld.remove_node": self._remove_node,
            "sld.add_connection": self._add_connection,
            "sld.remove_connection": lambda cmd, tx: self._remove_connection(cmd, tx, context=context),
            "sld.set_connection_route": self._set_connection_route,
            "sld.set_node_properties": self._set_node_properties,
            "sld.set_annotation_blocks": self._set_annotation_blocks,
        }[command.command_type]
        document = self.document
        was_modified = bool(document.modified)

        def restore_dirty_state() -> None:
            if was_modified:
                document.mark_modified()
            else:
                document.mark_clean()

        # This finalizer belongs to immediate transaction rollback only. The
        # committed inverse journal still marks the document dirty on undo.
        transaction.record_rollback_finalizer(restore_dirty_state)
        return handler(command, transaction)

    def _set_node_position(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        original_node = node
        original_node_equipment_id = node.equipment_id
        original_node_identity = {
            key: deepcopy(node.properties.get(key))
            for key in (
                "element_type", "terminal_ids", "terminal_connectivity",
                "projection_source", "lifecycle_state",
            )
        }
        previous = tuple(node.position)
        previous_properties = deepcopy(node.properties)
        updated = (float(p["x"]), float(p["y"]))
        updated_properties = deepcopy(previous_properties)
        updated_properties["position_owner"] = "engineer"
        if tuple(float(value) for value in previous) == updated and updated_properties == previous_properties:
            return ApplicationResult.success_result(
                message="SLD node position is unchanged.",
                metadata={"presentation_operation": "set_node_position", "node_id": p["node_id"]},
            )
        def restore() -> None:
            target = self.document.model.get_node_optional(p["node_id"])
            if target is None:
                raise ValueError(
                    f"Cannot undo SLD node position edit; node is absent: {p['node_id']!r}."
                )
            target_identity = {
                key: deepcopy(target.properties.get(key))
                for key in original_node_identity
            }
            if (
                target.equipment_id != original_node_equipment_id
                or target_identity != original_node_identity
            ):
                raise ValueError(
                    "Cannot undo SLD node position edit; the live node's logical "
                    f"identity changed for {p['node_id']!r}."
                )
            self.document.set_node_position(p["node_id"], *previous)
            target.properties.clear()
            target.properties.update(deepcopy(previous_properties))
            self.document.mark_modified()

        transaction.record_undo(restore)
        self.document.set_node_position(p["node_id"], *updated)
        node.properties.clear()
        node.properties.update(updated_properties)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node position updated.",
            metadata={"presentation_operation": "set_node_position", "node_id": p["node_id"]},
        )

    def _set_node_presentation(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        original_node = node
        original_node_equipment_id = node.equipment_id
        original_node_identity = {
            key: deepcopy(node.properties.get(key))
            for key in (
                "element_type", "terminal_ids", "terminal_connectivity",
                "projection_source", "lifecycle_state",
            )
        }
        previous = None if node.presentation is None else node.presentation.to_dict()
        previous_properties = deepcopy(node.properties)
        def restore() -> None:
            target = self.document.model.get_node_optional(p["node_id"])
            if target is None:
                raise ValueError(
                    f"Cannot undo SLD node presentation edit; node is absent: {p['node_id']!r}."
                )
            target_identity = {
                key: deepcopy(target.properties.get(key))
                for key in original_node_identity
            }
            if (
                target.equipment_id != original_node_equipment_id
                or target_identity != original_node_identity
            ):
                raise ValueError(
                    "Cannot undo SLD node presentation edit; the live node's logical "
                    f"identity changed for {p['node_id']!r}."
                )
            if previous is None:
                target.clear_presentation()
            else:
                target.set_presentation(previous)
            target.properties.clear()
            target.properties.update(previous_properties)
            self.document.mark_modified()

        # Register before normalization/mutation so malformed presentation
        # payloads cannot leave a partially changed live node.
        transaction.record_undo(restore)
        node.set_presentation(p["presentation"])
        node.properties["symbol_owner"] = "engineer"
        node.properties.setdefault("presentation_owner", "projection" if node.properties.get("projection_source") else "engineer")
        updated = None if node.presentation is None else node.presentation.to_dict()
        if updated == previous and node.properties == previous_properties:
            # No state changed; the inverse is harmless and idempotent.
            return ApplicationResult.success_result(
                message="SLD node presentation is unchanged.",
                metadata={"presentation_operation": "set_node_presentation", "node_id": p["node_id"]},
            )
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node presentation updated.",
            metadata={"presentation_operation": "set_node_presentation", "node_id": p["node_id"]},
        )

    def _add_node(
        self,
        command: Command,
        transaction: Transaction,
        *,
        authoritative_value: Any = None,
    ) -> ApplicationResult:
        p = command.payload
        equipment_id = p.get("equipment_id")
        if equipment_id is not None:
            self._validate_equipment_reference(
                str(equipment_id),
                authoritative_value=authoritative_value,
            )
        presentation_owner = str(p.get("presentation_owner", "engineer"))
        projection_source = p.get("projection_source")
        if presentation_owner not in {"engineer", "projection"}:
            raise ValueError("presentation_owner must be 'engineer' or 'projection'.")
        if projection_source is not None and presentation_owner != "projection":
            raise ValueError("projection_source requires presentation_owner='projection'.")
        properties = {"presentation_owner": presentation_owner}
        element_type = p.get("element_type")
        if element_type is not None:
            properties["element_type"] = str(element_type)
        if projection_source is not None:
            properties["projection_source"] = str(projection_source)
        if equipment_id is not None:
            properties["lifecycle_state"] = "BOUND"
        presentation_properties = p.get("presentation_properties", {})
        if not isinstance(presentation_properties, Mapping):
            raise TypeError("presentation_properties must be a mapping")
        properties.update(dict(presentation_properties))

        presentation = p.get("presentation")
        presentation_rotation = p.get("presentation_rotation")
        if presentation_rotation is not None:
            if isinstance(presentation_rotation, bool) or not isinstance(presentation_rotation, (int, float)):
                raise TypeError("presentation_rotation must be numeric when provided.")
            if not math.isfinite(float(presentation_rotation)):
                raise ValueError("presentation_rotation must be finite.")
        if presentation is None and equipment_id is not None:
            if self._symbol_presentation_factory is None:
                raise ValueError(
                    f"SLD equipment node {p['node_id']!r} has no symbol presentation "
                    "and no symbol presentation factory is configured."
                )
            if not isinstance(element_type, str) or not element_type.strip():
                raise ValueError(
                    "SLD equipment node creation requires its canonical element_type "
                    "when a default symbol presentation factory is configured."
                )
            presentation = self._symbol_presentation_factory(element_type)
        if presentation is not None and not isinstance(presentation, Mapping):
            raise TypeError(
                f"SLD symbol presentation for node {p['node_id']!r} must be a mapping "
                "accepted by SymbolBase.from_dict()."
            )
        if presentation is not None:
            # Copy the complete renderer-neutral representation without
            # rewriting symbol identity, rotation, geometry, or properties.
            presentation = dict(presentation)
        if presentation_rotation is not None:
            if presentation is None:
                raise ValueError("presentation_rotation requires a canonical symbol presentation.")
            # Draft orientation is a separate authoring field, not a
            # SymbolBase mapping. Apply it only after the canonical symbol
            # identity has been resolved by the configured factory.
            presentation["rotation"] = float(presentation_rotation)

        node_id = p["node_id"]
        # Reject identity collisions before registering compensation. In
        # particular, never let a failed create roll back a pre-existing node.
        if self.document.model.get_node_optional(node_id) is not None:
            raise ValueError(f"SLD node identity collision: {node_id!r}.")

        x = float(p["x"])
        y = float(p["y"])
        created_node = None
        created_node_signature = None

        def node_creation_signature(node: Any) -> tuple[Any, ...]:
            """Snapshot the complete mutable state owned by this creation."""
            node_presentation = (
                None if node.presentation is None else node.presentation.to_dict()
            )
            return (
                node.equipment_id,
                float(node.x),
                float(node.y),
                deepcopy(dict(node.properties)),
                deepcopy(node_presentation),
            )

        # Capture the created object and its state after successful insertion.
        # During failed construction, compensate by ID only if the survivor
        # still matches the requested creation signature. During later undo,
        # require both the original object and its unchanged creation state.
        def undo_added_node() -> None:
            current = self.document.model.get_node_optional(node_id)
            if current is None:
                return
            if created_node is not None:
                if current is not created_node:
                    raise ValueError(
                        f"Cannot undo added SLD node; identity is now owned by another object: {node_id!r}."
                    )
                if node_creation_signature(current) != created_node_signature:
                    raise ValueError(
                        f"Cannot undo added SLD node; live state changed after creation: {node_id!r}."
                    )
            else:
                current_presentation = (
                    None if current.presentation is None else current.presentation.to_dict()
                )
                if presentation is None:
                    expected_presentation = None
                else:
                    symbol_id = presentation.get("symbol_id")
                    definition_id = presentation.get("definition_id")
                    if definition_id is None:
                        definition_id = symbol_id
                    expected_presentation = {
                        "symbol_id": symbol_id,
                        "definition_id": definition_id,
                        "representation_id": str(
                            presentation.get("representation_id", "symbol")
                        ).strip(),
                        "scale": float(presentation.get("scale", 1.0)),
                        "rotation": float(presentation.get("rotation", 0.0)),
                        "visible": bool(presentation.get("visible", True)),
                        "properties": dict(presentation.get("properties") or {}),
                    }
                if (
                    current.equipment_id != equipment_id
                    or float(current.x) != x
                    or float(current.y) != y
                    or dict(current.properties) != properties
                    or current_presentation != expected_presentation
                ):
                    raise ValueError(
                        f"Cannot compensate failed SLD node creation; identity collision: {node_id!r}."
                    )
            self.document.model.remove_node(node_id)
            self.document.mark_modified()

        transaction.record_undo(undo_added_node)
        try:
            self.document.model.create_node(
                node_id=node_id,
                equipment_id=equipment_id,
                x=x,
                y=y,
                presentation=presentation,
                properties=properties,
            )
            created_node = self.document.model.get_node_optional(node_id)
            if created_node is not None:
                created_node_signature = node_creation_signature(created_node)
        except Exception as exc:
            raise ValueError(
                f"Could not create SLD node {node_id!r} with its symbol presentation: {exc}"
            ) from exc
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node added.",
            metadata={"presentation_operation": "add_node", "node_id": p["node_id"]},
        )

    def _validate_equipment_reference(
        self,
        equipment_id: str,
        *,
        authoritative_value: Any = None,
    ) -> None:
        """Validate an SLD equipment association at the Application boundary.

        For an in-flight Core mutation, authoritative_value is the
        transaction-visible Core result. ReadModels remain post-commit
        projections and are not used for that binding.
        """
        if authoritative_value is not None:
            authoritative_id = getattr(authoritative_value, "id", None)
            if authoritative_id is None or str(authoritative_id) != equipment_id:
                raise ValueError(
                    f"SLD node equipment reference {equipment_id!r} does not "
                    "match the transaction-visible Core identity."
                )
            return

        if self._application is None:
            raise RuntimeError("SLDService requires an Application to validate equipment references.")
        read_model = self._application.read_network()
        if any(element.object_id == equipment_id for element in read_model.elements):
            return
        protection = self._application.read_protection()
        protection_elements = getattr(protection, "elements", None)
        if protection_elements is None:
            protection_elements = getattr(protection, "relays", ())
        if any(element.object_id == equipment_id for element in protection_elements):
            return
        raise ValueError(
            f"SLD node equipment reference {equipment_id!r} does not resolve to current Application read state."
        )

    @staticmethod
    def has_engineer_presentation_overrides(node: Any) -> bool:
        """Return whether a node contains authored presentation state."""
        properties = getattr(node, "properties", {}) or {}
        if properties.get("position_owner") == "engineer": return True
        if properties.get("symbol_owner") == "engineer": return True
        for key in ("labels_owner", "visual_properties_owner", "manual_geometry_owner", "presentation_fields_owner"):
            if properties.get(key) == "engineer": return True
        overrides = properties.get("engineer_presentation_overrides")
        if isinstance(overrides, Mapping) and bool(overrides): return True
        if properties.get("presentation_owner") == "engineer": return True
        return False

    @staticmethod
    def has_engineer_presentation_overrides_for_connection(connection: Any) -> bool:
        """Return whether a connection contains authored route/presentation state."""
        properties = getattr(connection, "properties", {}) or {}
        if properties.get("route_owner") == "engineer": return True
        route = getattr(connection, "route", None)
        if route is not None and getattr(route, "ownership", None) == "engineer": return True
        for key in ("labels_owner", "visual_properties_owner", "manual_geometry_owner", "presentation_fields_owner"):
            if properties.get(key) == "engineer": return True
        overrides = properties.get("engineer_presentation_overrides")
        if isinstance(overrides, Mapping) and bool(overrides): return True
        if properties.get("presentation_owner") == "engineer": return True
        return False

    @staticmethod
    def _set_node_lifecycle_state(node: Any, state: str) -> None:
        if state not in {"BOUND", "ORPHANED", "REMOVED"}: raise ValueError(f"Unsupported SLD node lifecycle state: {state!r}")
        node.properties["lifecycle_state"] = state

    @staticmethod
    def _set_connection_lifecycle_state(connection: Any, state: str) -> None:
        if state not in {"BOUND", "ORPHANED", "REMOVED"}: raise ValueError(f"Unsupported SLD connection lifecycle state: {state!r}")
        connection.properties["lifecycle_state"] = state
    def reconcile_element_update(
        self,
        *,
        equipment_id: str,
        element_type: str,
        core_object: Any,
        transaction: Transaction,
    ) -> None:
        """Reconcile presentation from the authoritative transaction-visible Core object.

        This is intentionally a service operation, not a second command/history
        mechanism. It is invoked by the Application pre-commit hook inside the
        transaction opened for the originating Core command. The Core object is
        the mutation result; ReadModels are never queried to establish the
        authoritative updated state.
        """
        if not isinstance(equipment_id, str) or not equipment_id:
            raise ValueError("equipment_id must be a non-empty string")
        node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        if node is None:
            return
        previous = node.to_dict()
        # Convert the already-mutated Core object into the existing
        # presentation projection shape without querying Application
        # ReadService. The Core object remains the authoritative source.
        from ..read_service import NetworkReadService
        projected = NetworkReadService._to_read_model(element_type, core_object)
        attributes = dict(projected.attributes)
        labels = dict(projected.labels)
        connectivity = tuple(projected.connectivity_refs)

        # Prepare the complete projected property state without mutating the
        # live node. Register the inverse while the canonical node is still
        # untouched so any subsequent presentation failure is compensatable.
        projected_properties = dict(node.properties)
        projected_properties.update({
            "element_type": str(element_type),
            "labels": labels,
            "attributes": attributes,
            "terminal_ids": connectivity,
            "terminal_connectivity": tuple(attributes.get("terminal_connectivity", ())),
            "lifecycle_state": "BOUND",
        })

        original_node = node
        expected_node_equipment_id = equipment_id
        expected_node_identity = {
            key: deepcopy(projected_properties.get(key))
            for key in (
                "element_type", "terminal_ids", "terminal_connectivity",
                "projection_source", "lifecycle_state",
            )
        }

        def restore_updated_node(snapshot=previous) -> None:
            # Undo must restore the node updated by this reconciliation, not
            # mutate an unrelated object that later reused the same node ID.
            target = self.document.model.get_node_optional(snapshot["node_id"])
            if target is None:
                raise ValueError(
                    "Cannot undo SLD element update; node is absent: "
                    f"{snapshot['node_id']!r}."
                )
            if target is not original_node:
                target_identity = {
                    key: target.properties.get(key)
                    for key in expected_node_identity
                }
                if (
                    target.equipment_id != expected_node_equipment_id
                    or target_identity != expected_node_identity
                ):
                    raise ValueError(
                        "Cannot undo SLD element update; a different logical "
                        f"node owns identity {snapshot['node_id']!r}."
                    )
            target.set_position(float(snapshot.get("x", 0.0)), float(snapshot.get("y", 0.0)))
            if snapshot.get("presentation") is None:
                target.clear_presentation()
            else:
                target.set_presentation(snapshot["presentation"])
            target.properties.clear()
            target.properties.update(deepcopy(dict(snapshot.get("properties", {}))))
            target.equipment_id = snapshot.get("equipment_id")
            self.document.mark_modified()

        transaction.record_undo(restore_updated_node)

        # All live-node mutations follow inverse registration. Keep the
        # canonical node object/identity and preserve fields not owned by this
        # projection update.
        node.properties.clear()
        node.properties.update(projected_properties)
        node.equipment_id = equipment_id
        self.document.mark_modified()

    @classmethod
    def _validate_insertion_geometry(
        cls,
        *,
        connection_id: str,
        old_connection: Any,
        source_node: Any,
        target_node: Any,
        insertion_position: tuple[float, float] | list[float],
        segment_index: int,
    ) -> tuple[float, float]:
        """Validate insertion geometry against exactly the selected SLD segment.

        The SLD document is authoritative for presentation route geometry.
        Core topology geometry is intentionally not consulted here.
        """
        if str(old_connection.properties.get("connection_kind", "")).upper() != "SIMPLE_WIRE":
            raise ValidationError(
                code="INVALID_INSERTION_CONNECTION",
                message="Electrical insertion requires a Simple Wire SLD connection.",
                details={"connection_id": str(connection_id), "reason": "not_simple_wire"},
            )

        if not isinstance(segment_index, int) or isinstance(segment_index, bool):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion segment index must be an integer.",
                details={"connection_id": str(connection_id), "segment_index": segment_index, "reason": "invalid_segment_index_type"},
            )

        if not isinstance(insertion_position, (tuple, list)) or len(insertion_position) != 2:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position must contain exactly two coordinates.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": insertion_position,
                    "reason": "invalid_coordinate_count",
                },
            )

        try:
            px, py = float(insertion_position[0]), float(insertion_position[1])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position coordinates must be numeric.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": tuple(insertion_position),
                    "reason": "non_numeric_coordinate",
                },
            ) from exc

        if not math.isfinite(px) or not math.isfinite(py):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position coordinates must be finite.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "non_finite_coordinate",
                },
            )

        try:
            source = (float(source_node.x), float(source_node.y))
            target = (float(target_node.x), float(target_node.y))
            route_points = tuple(
                (float(point[0]), float(point[1]))
                for point in old_connection.route.points
            )
        except (AttributeError, TypeError, ValueError, IndexError, OverflowError) as exc:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="The selected SLD connection has invalid route geometry.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "invalid_route_geometry",
                },
            ) from exc

        polyline = (source, *route_points, target)
        segment_count = len(polyline) - 1
        if segment_index < 0 or segment_index >= segment_count:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message=(
                    f"Insertion segment index {segment_index} is outside the "
                    f"route range for connection {connection_id}."
                ),
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "segment_count": segment_count,
                    "supplied_position": (px, py),
                    "reason": "segment_index_out_of_range",
                },
            )

        ax, ay = polyline[segment_index]
        bx, by = polyline[segment_index + 1]
        if not all(math.isfinite(value) for value in (ax, ay, bx, by)):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="The selected SLD route segment contains non-finite coordinates.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "non_finite_segment",
                },
            )

        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 0.0:
            closest_dx = px - ax
            closest_dy = py - ay
            distance_sq = closest_dx * closest_dx + closest_dy * closest_dy
        else:
            projection = ((px - ax) * dx + (py - ay) * dy) / length_sq
            projection = max(0.0, min(1.0, projection))
            closest_x = ax + projection * dx
            closest_y = ay + projection * dy
            closest_dx = px - closest_x
            closest_dy = py - closest_y
            distance_sq = closest_dx * closest_dx + closest_dy * closest_dy

        if not math.isfinite(distance_sq) or distance_sq > cls.INSERTION_POSITION_TOLERANCE_SQ:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message=(
                    f"Insertion position is not on selected SLD route segment "
                    f"{segment_index} within the allowed tolerance."
                ),
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "distance_squared": float(distance_sq),
                    "tolerance": float(cls.INSERTION_POSITION_TOLERANCE),
                    "tolerance_squared": float(cls.INSERTION_POSITION_TOLERANCE_SQ),
                    "reason": "position_outside_selected_segment",
                },
            )

        return (px, py)

    def reconcile_insertion(
        self,
        *,
        connection_id: str,
        equipment_id: str,
        equipment_type: str,
        insertion_position: tuple[float, float],
        orientation: float,
        segment_index: int,
        input_endpoint: Mapping[str, Any],
        output_endpoint: Mapping[str, Any],
        replacement_connection_ids: tuple[str, str],
        transaction: Transaction,
        terminal_anchors: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        """Atomically replace one persistent SLD wire with an inserted node and two wires.

        The operation mutates only the persistent SLD presentation companion
        inside the originating Application transaction. The old connection
        snapshot is retained exactly for undo.
        """
        old_id = self.presentation_connection_id(connection_id)
        old_connection = self.document.model.get_connection_optional(old_id)
        if old_connection is None:
            candidates = tuple(
                item
                for item in self.document.model.connections
                if str(item.properties.get("core_connection_id", "")) == str(connection_id)
            )
            if len(candidates) == 1:
                old_connection = candidates[0]
                old_id = old_connection.connection_id
            elif len(candidates) > 1:
                raise ValueError(
                    f"Multiple persistent SLD companions exist for Core connection {connection_id!r}."
                )
            else:
                raise ValidationError(
                    code="INVALID_INSERTION_CONNECTION",
                    message=(
                        f"No persistent SLD connection companion exists for "
                        f"Core connection {connection_id!r}."
                    ),
                    details={
                        "connection_id": str(connection_id),
                        "reason": "sld_connection_not_found",
                    },
                )

        first_core_id, second_core_id = replacement_connection_ids
        first_id = self.presentation_connection_id(first_core_id)
        second_id = self.presentation_connection_id(second_core_id)
        existing_node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        node_id = str(existing_node.node_id) if existing_node is not None else f"sld-core-{equipment_id}"
        if existing_node is not None:
            attached = tuple(
                item for item in self.document.model.connections
                if item.source_node_id == existing_node.node_id or item.target_node_id == existing_node.node_id
            )
            if attached:
                raise ValueError(
                    f"Existing equipment SLD node {node_id!r} already has persistent connection companions."
                )
        elif self.document.model.get_node_optional(node_id) is not None:
            raise ValueError(f"SLD node {node_id!r} already exists.")
        if self.document.model.get_connection_optional(first_id) is not None:
            raise ValueError(f"SLD connection {first_id!r} already exists.")
        if self.document.model.get_connection_optional(second_id) is not None:
            raise ValueError(f"SLD connection {second_id!r} already exists.")

        if str(old_connection.properties.get("connection_kind", "")).upper() != "SIMPLE_WIRE":
            raise ValueError("Electrical insertion requires a Simple Wire SLD companion.")
        snapshot = old_connection.to_dict()
        node_snapshot = existing_node.to_dict() if existing_node is not None else None
        previous_document_modified = bool(getattr(self.document, "modified", False))
        source_node = self.document.model.get_node(old_connection.source_node_id)
        target_node = self.document.model.get_node(old_connection.target_node_id)
        position = self._validate_insertion_geometry(
            connection_id=connection_id,
            old_connection=old_connection,
            source_node=source_node,
            target_node=target_node,
            insertion_position=insertion_position,
            segment_index=segment_index,
        )

        # Capture ownership/provenance before replacing the persistent
        # connection. These values belong to the authored SLD presentation and
        # must not be inferred from the insertion command itself.
        route = old_connection.route.to_dict()
        original_route_ownership = str(route.get("ownership", "auto")).lower()
        original_route_owner = old_connection.properties.get("route_owner")
        original_presentation_owner = old_connection.properties.get("presentation_owner")
        original_projection_source = old_connection.properties.get("projection_source")
        if original_presentation_owner not in (None, "engineer", "projection"):
            raise ValueError("SLD connection presentation ownership is unknown; insertion is rejected.")
        polyline = [
            (float(source_node.x), float(source_node.y)),
            *tuple(tuple(point) for point in old_connection.route.points),
            (float(target_node.x), float(target_node.y)),
        ]
        if segment_index < 0 or segment_index >= len(polyline) - 1:
            raise ValueError(
                f"Invalid insertion route segment index {segment_index} for connection {connection_id!r}."
            )
        anchors = dict(terminal_anchors or {})
        input_role = str(input_endpoint.get("terminal_role") or "")
        output_role = str(output_endpoint.get("terminal_role") or "")

        def rotated_anchor(role: str) -> tuple[float, float]:
            local = anchors.get(role)
            if local is None:
                return position
            lx, ly = float(local[0]), float(local[1])
            radians = math.radians(float(orientation))
            return (
                position[0] + lx * math.cos(radians) - ly * math.sin(radians),
                position[1] + lx * math.sin(radians) + ly * math.cos(radians),
            )

        input_anchor = rotated_anchor(input_role)
        output_anchor = rotated_anchor(output_role)
        first_polyline = [*polyline[: segment_index + 1], position, input_anchor]
        second_polyline = [output_anchor, position, *polyline[segment_index + 1 :]]

        def normalize_route(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
            normalized: list[tuple[float, float]] = []
            for point in points:
                value = (float(point[0]), float(point[1]))
                if normalized and value == normalized[-1]:
                    continue
                normalized.append(value)
            if str(route.get("ownership", "auto")).lower() != "engineer":
                compact: list[tuple[float, float]] = []
                for point in normalized:
                    if len(compact) >= 2:
                        ax, ay = compact[-2]
                        bx, by = compact[-1]
                        cx, cy = point
                        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) <= 1e-9:
                            compact[-1] = point
                            continue
                    compact.append(point)
                normalized = compact
            return normalized

        def route_for(points: list[tuple[float, float]]) -> dict[str, Any]:
            normalized = normalize_route(points)
            return {
                "routing_mode": route["routing_mode"],
                "ownership": route["ownership"],
                "points": [list(point) for point in normalized[1:-1]],
            }

        def presentation_endpoint(reference: Mapping[str, Any]) -> dict[str, str]:
            kind = str(reference.get("kind", "")).lower()
            if kind == "terminal":
                if str(reference.get("object_id")) != str(equipment_id):
                    raise ValueError("Insertion terminal endpoint belongs to a different equipment identity.")
                return {
                    "kind": "equipment",
                    "node_id": node_id,
                    "equipment_id": str(equipment_id),
                    "terminal_role": str(reference["terminal_role"]),
                }
            raise ValueError("Inserted equipment Simple Wire endpoints must be terminal references.")

        input_presentation_endpoint = presentation_endpoint(input_endpoint)
        output_presentation_endpoint = presentation_endpoint(output_endpoint)
        properties = dict(old_connection.properties)
        properties["connection_kind"] = "SIMPLE_WIRE"
        if original_presentation_owner is None:
            properties["presentation_owner"] = "projection"
        # Preserve the original presentation/provenance contract. In
        # particular, an engineer-owned route must never become projection
        # owned merely because insertion changed the Core connectivity.
        if original_projection_source is None:
            properties.pop("projection_source", None)
        else:
            properties["projection_source"] = original_projection_source
        if original_route_ownership == "engineer":
            # Engineer-authored routing explicitly carries engineer ownership
            # on both replacement companions, even if an older persisted
            # document omitted the redundant route_owner property.
            properties["route_owner"] = original_route_owner or "engineer"
        elif original_route_owner is None:
            properties.pop("route_owner", None)
        else:
            properties["route_owner"] = original_route_owner
        properties["lifecycle_state"] = "BOUND"
        properties["insertion_parent_connection_id"] = connection_id

        if existing_node is not None:
            # Start from the exact authored node metadata and change only the
            # fields explicitly owned by this insertion: current Core binding,
            # insertion provenance, position, and the explicitly supplied
            # rotation. Ownership fields such as symbol/labels/manual geometry
            # remain untouched.
            node_properties = dict(existing_node.properties)
            node_properties.update({
                "lifecycle_state": "BOUND",
                "element_type": equipment_type,
                "rotation": float(orientation),
                "insertion_parent_connection_id": connection_id,
            })
        else:
            node_properties = {
                "presentation_owner": "engineer",
                "lifecycle_state": "BOUND",
                "element_type": equipment_type,
                "position_owner": "engineer",
                "symbol_owner": "engineer",
                "rotation": float(orientation),
                "insertion_parent_connection_id": connection_id,
            }
        presentation = None
        if existing_node is None and self._symbol_presentation_factory is not None:
            presentation = self._symbol_presentation_factory(equipment_type)

        # Capture exact identities as each object is successfully inserted.
        # The inverse is registered before mutation, so signature checks are
        # limited to the narrow window where construction may have inserted an
        # object before returning it to this scope.
        created_node_ref: list[Any] = []
        created_connection_refs: dict[str, Any] = {}

        def remove_created_connection(connection_id_to_remove: str, core_id: str) -> None:
            current = self.document.model.get_connection_optional(connection_id_to_remove)
            if current is None:
                return
            expected_ref = created_connection_refs.get(connection_id_to_remove)
            if expected_ref is not None:
                if current is not expected_ref:
                    raise ValueError(
                        f"Cannot roll back SLD insertion; connection identity was reused: {connection_id_to_remove!r}."
                    )
            else:
                props = getattr(current, "properties", {})
                if (
                    str(props.get("core_connection_id", "")) != str(core_id)
                    or str(props.get("connection_kind", "")).upper() != "SIMPLE_WIRE"
                    or str(current.source_node_id) not in {str(old_connection.source_node_id), str(node_id)}
                    or str(current.target_node_id) not in {str(old_connection.target_node_id), str(node_id)}
                ):
                    raise ValueError(
                        f"Cannot safely identify partially created SLD insertion connection {connection_id_to_remove!r}."
                    )
            self.document.model.remove_connection(connection_id_to_remove)

        def restore() -> None:
            rollback_errors: list[str] = []
            # Continue restoring independent objects after a collision is
            # detected, but never remove an object whose identity is unknown.
            for replacement_id, core_id in ((first_id, first_core_id), (second_id, second_core_id)):
                try:
                    remove_created_connection(replacement_id, core_id)
                except Exception as exc:
                    rollback_errors.append(str(exc))

            try:
                current_node = self.document.model.get_node_optional(node_id)
                if existing_node is None:
                    if current_node is not None:
                        if created_node_ref:
                            if current_node is not created_node_ref[0]:
                                raise ValueError(
                                    f"Cannot roll back SLD insertion; node identity was reused: {node_id!r}."
                                )
                        else:
                            props = getattr(current_node, "properties", {})
                            if (
                                str(getattr(current_node, "equipment_id", "")) != str(equipment_id)
                                or str(props.get("insertion_parent_connection_id", "")) != str(connection_id)
                                or str(props.get("element_type", "")) != str(equipment_type)
                            ):
                                raise ValueError(
                                    f"Cannot safely identify partially created SLD insertion node {node_id!r}."
                                )
                        self.document.model.remove_node(node_id)
                else:
                    if current_node is not None and current_node is not existing_node:
                        raise ValueError(
                            f"Cannot restore SLD insertion node; identity already reused: {node_id!r}."
                        )
                    if current_node is None:
                        self._restore_node_snapshot(node_snapshot or {})
                    else:
                        current_node.set_position(float(node_snapshot["x"]), float(node_snapshot["y"]))
                        if node_snapshot.get("presentation") is None:
                            current_node.clear_presentation()
                        else:
                            current_node.set_presentation(node_snapshot["presentation"])
                        current_node.properties.clear()
                        current_node.properties.update(dict(node_snapshot.get("properties", {})))
                        current_node.equipment_id = node_snapshot.get("equipment_id")
            except Exception as exc:
                rollback_errors.append(str(exc))

            try:
                current_old = self.document.model.get_connection_optional(old_id)
                if current_old is not None:
                    if current_old is not old_connection:
                        raise ValueError(
                            f"Cannot restore original SLD connection; identity already reused: {old_id!r}."
                        )
                else:
                    self._restore_connection_snapshot(snapshot)
            except Exception as exc:
                rollback_errors.append(str(exc))

            # This inverse is also retained in committed undo history.
            # Restoring the pre-operation clean flag here would incorrectly
            # make an undone, persisted document appear clean. Immediate
            # transaction rollback restores the original dirty flag through
            # apply_command's rollback finalizer after all inverses succeed.
            self.document.mark_modified()
            if rollback_errors:
                raise ValueError(
                    "SLD insertion rollback encountered identity/restoration conflicts: "
                    + " | ".join(rollback_errors)
                )

        # Register the inverse before mutating the presentation so any failure
        # during the multi-object replacement is rollback-safe as well.
        transaction.record_undo(restore)

        self.document.model.remove_connection(old_id)
        if existing_node is None:
            self.document.model.create_node(
                node_id=node_id,
                equipment_id=equipment_id,
                x=position[0],
                y=position[1],
                presentation=presentation,
                properties=node_properties,
            )
            created_node_ref.append(self.document.model.get_node(node_id))
        else:
            existing_node.set_position(position[0], position[1])
            existing_node.properties.update(node_properties)
        self.document.model.create_connection(
            connection_id=first_id,
            source_node_id=old_connection.source_node_id,
            target_node_id=node_id,
            source_endpoint=old_connection.source_endpoint.to_dict() if old_connection.source_endpoint is not None else None,
            target_endpoint=input_presentation_endpoint,
            route=route_for(first_polyline),
            properties=dict(properties, core_connection_id=first_core_id),
        )
        created_connection_refs[first_id] = self.document.model.get_connection(first_id)
        self.document.model.create_connection(
            connection_id=second_id,
            source_node_id=node_id,
            target_node_id=old_connection.target_node_id,
            source_endpoint=output_presentation_endpoint,
            target_endpoint=old_connection.target_endpoint.to_dict() if old_connection.target_endpoint is not None else None,
            route=route_for(second_polyline),
            properties=dict(properties, core_connection_id=second_core_id),
        )
        created_connection_refs[second_id] = self.document.model.get_connection(second_id)
        self.document.mark_modified()

    def reconcile_element_delete(
        self,
        *,
        equipment_id: str,
        transaction: Transaction,
    ) -> str:
        """Remove or orphan a Core-bound SLD node using field-level ownership."""
        node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        if node is None:
            return "REMOVED"
        snapshot = node.to_dict()
        attached = tuple(
            connection for connection in self.document.model.connections
            if connection.source_node_id == node.node_id or connection.target_node_id == node.node_id
        )
        connection_snapshots = tuple(connection.to_dict() for connection in attached)
        original_connections = {connection.connection_id: connection for connection in attached}

        def restore_node_in_place(target: Any, node_snapshot: Mapping[str, Any]) -> None:
            target.set_position(
                float(node_snapshot.get("x", 0.0)),
                float(node_snapshot.get("y", 0.0)),
            )
            if node_snapshot.get("presentation") is None:
                target.clear_presentation()
            else:
                target.set_presentation(node_snapshot["presentation"])
            target.properties.clear()
            target.properties.update(deepcopy(dict(node_snapshot.get("properties", {}))))
            target.equipment_id = node_snapshot.get("equipment_id")

        def restore_connection_in_place(target: Any, connection_snapshot: Mapping[str, Any]) -> None:
            source = connection_snapshot.get("source_endpoint")
            target_endpoint = connection_snapshot.get("target_endpoint")
            target.source_endpoint = None if source is None else SLDEndpoint.from_dict(source)
            target.target_endpoint = None if target_endpoint is None else SLDEndpoint.from_dict(target_endpoint)
            target.properties.clear()
            target.properties.update(deepcopy(dict(connection_snapshot.get("properties", {}))))

        if not self.has_engineer_presentation_overrides(node):
            annotation_snapshot = [
                block.to_dict() for block in self.document.annotation_blocks
            ]
            remaining_annotations = [
                item for item in annotation_snapshot
                if item.get("node_id") != node.node_id
            ]

            def restore(
                node_snapshot=snapshot,
                original_node=node,
                connection_snapshots=connection_snapshots,
                original_connections=original_connections,
                annotation_snapshot=annotation_snapshot,
            ) -> None:
                current_node = self.document.model.get_node_optional(node_snapshot["node_id"])
                if current_node is None:
                    self.document.model.create_node(
                        node_id=node_snapshot["node_id"],
                        equipment_id=node_snapshot.get("equipment_id"),
                        x=node_snapshot.get("x", 0.0),
                        y=node_snapshot.get("y", 0.0),
                        presentation=node_snapshot.get("presentation"),
                        properties=node_snapshot.get("properties", {}),
                    )
                elif current_node is not original_node:
                    raise ValueError(
                        "Cannot restore deleted SLD node; a different object now owns "
                        f"identity {node_snapshot['node_id']!r}."
                    )
                else:
                    restore_node_in_place(current_node, node_snapshot)

                for item in connection_snapshots:
                    connection_id = item["connection_id"]
                    current = self.document.model.get_connection_optional(connection_id)
                    original = original_connections[connection_id]
                    if current is None:
                        self._restore_connection_snapshot(item)
                    elif current is not original:
                        raise ValueError(
                            "Cannot restore deleted SLD node; a different connection "
                            f"object now owns identity {connection_id!r}."
                        )
                    else:
                        restore_connection_in_place(current, item)
                self.document.replace_annotation_blocks(annotation_snapshot)
                self.document.mark_modified()

            # Record the complete inverse before mutation. Restoration accepts
            # the original live objects, recreates only absent objects, and
            # fails closed on conflicting identities rather than silently
            # skipping or deleting unrelated objects with matching IDs.
            transaction.record_undo(restore)
            self.document.model.remove_node(node.node_id)
            self.document.replace_annotation_blocks(remaining_annotations)
            self.document.mark_modified()
            return "REMOVED"

        def restore(
            node_snapshot=snapshot,
            original_node=node,
            connection_snapshots=connection_snapshots,
            original_connections=original_connections,
        ) -> None:
            current_node = self.document.model.get_node_optional(node_snapshot["node_id"])
            if current_node is None:
                self.document.model.create_node(
                    node_id=node_snapshot["node_id"],
                    equipment_id=node_snapshot.get("equipment_id"),
                    x=node_snapshot.get("x", 0.0),
                    y=node_snapshot.get("y", 0.0),
                    presentation=node_snapshot.get("presentation"),
                    properties=node_snapshot.get("properties", {}),
                )
            elif current_node is not original_node:
                raise ValueError(
                    "Cannot restore orphaned SLD node; a different object now owns "
                    f"identity {node_snapshot['node_id']!r}."
                )
            else:
                restore_node_in_place(current_node, node_snapshot)

            for item in connection_snapshots:
                connection_id = item["connection_id"]
                current = self.document.model.get_connection_optional(connection_id)
                original = original_connections[connection_id]
                if current is None:
                    self._restore_connection_snapshot(item)
                elif current is not original:
                    raise ValueError(
                        "Cannot restore orphaned SLD node; a different connection "
                        f"object now owns identity {connection_id!r}."
                    )
                else:
                    restore_connection_in_place(current, item)
            self.document.mark_modified()

        # Register the full inverse before touching the node or any connection.
        # The inverse restores retained objects in place, recreates absent
        # objects, and rejects identity collisions instead of replacing an
        # unrelated object that appeared during the transaction.
        transaction.record_undo(restore)

        node.equipment_id = None
        node.properties.pop("projection_source", None)
        node.properties["lifecycle_state"] = "ORPHANED"
        node.properties["orphaned_equipment_id"] = equipment_id

        for connection in attached:
            if self.has_engineer_presentation_overrides_for_connection(connection):
                if connection.source_node_id == node.node_id:
                    connection.source_endpoint = None
                if connection.target_node_id == node.node_id:
                    connection.target_endpoint = None
                connection.properties.pop("projection_source", None)
                self._set_connection_lifecycle_state(connection, "ORPHANED")
            else:
                self.document.model.remove_connection(connection.connection_id)

        self.document.mark_modified()
        return "ORPHANED"

    def rebind_orphaned_simple_wire(
        self,
        *,
        core_connection_id: str,
        source_endpoint: Mapping[str, Any],
        target_endpoint: Mapping[str, Any],
        transaction: Transaction,
    ) -> bool:
        """Rebind an authored orphan when a deleted Simple Wire is recreated.

        A delete may retain an authored SLD connection (including its route and
        presentation overrides) under the canonical presentation ID. Undo/redo
        recreates the same Core identity; reuse that exact object rather than
        colliding with it or discarding the engineer-authored presentation.
        """
        core_id = str(core_connection_id).strip()
        if not core_id:
            raise ValueError("core_connection_id must be a non-empty string.")
        presentation_id = self.presentation_connection_id(core_id)
        orphaned = tuple(
            item
            for item in self.document.model.connections
            if str(item.properties.get("lifecycle_state", "")).upper() == "ORPHANED"
            and str(item.properties.get("orphaned_from_core_connection_id", "")) == core_id
        )
        if len(orphaned) > 1:
            raise ValueError(
                f"Multiple orphaned SLD companions retain Core connection {core_id!r}."
            )
        if not orphaned:
            return False

        connection = orphaned[0]
        if str(connection.connection_id) != presentation_id:
            # Do not create a second canonical companion while a noncanonical
            # authored orphan still owns the prior relationship. Automatic ID
            # migration would mutate persistent presentation identity and needs
            # an explicit reconciliation/migration contract.
            raise ValueError(
                "Cannot rebind orphaned Simple Wire presentation: retained "
                f"identity {connection.connection_id!r} is not canonical "
                f"{presentation_id!r}."
            )
        properties = connection.properties

        source_node_id = str(source_endpoint.get("node_id", ""))
        target_node_id = str(target_endpoint.get("node_id", ""))
        if (
            not source_node_id
            or not target_node_id
            or str(connection.source_node_id) != source_node_id
            or str(connection.target_node_id) != target_node_id
        ):
            raise ValueError(
                "Cannot rebind orphaned Simple Wire presentation: endpoint node identity changed."
            )

        snapshot = connection.to_dict()
        original = connection

        def restore_orphaned_snapshot() -> None:
            current = self.document.model.get_connection_optional(presentation_id)
            if current is not original:
                raise ValueError(
                    "Cannot roll back Simple Wire rebind; canonical presentation identity changed."
                )
            source = snapshot.get("source_endpoint")
            target = snapshot.get("target_endpoint")
            current.source_endpoint = None if source is None else SLDEndpoint.from_dict(source)
            current.target_endpoint = None if target is None else SLDEndpoint.from_dict(target)
            current.properties.clear()
            current.properties.update(dict(snapshot.get("properties", {})))
            self.document.mark_modified()

        transaction.record_undo(restore_orphaned_snapshot)
        connection.source_endpoint = SLDEndpoint.from_dict(dict(source_endpoint))
        connection.target_endpoint = SLDEndpoint.from_dict(dict(target_endpoint))
        properties.pop("orphaned_from_core_connection_id", None)
        properties["core_connection_id"] = core_id
        properties["connection_kind"] = "SIMPLE_WIRE"
        properties["lifecycle_state"] = "BOUND"
        properties["projection_source"] = "core_transaction"
        self.document.mark_modified()
        return True

    def reconcile_connection_delete(
        self,
        *,
        connection_id: str,
        transaction: Transaction,
    ) -> str:
        """Remove or orphan a Core-bound SLD connection by route ownership."""
        # Resolve by authoritative Core mapping before trying a same-named
        # presentation ID. Core IDs and SLD IDs occupy separate identity
        # domains; a same-named engineer-authored or Line/Cable presentation
        # must never shadow the Simple Wire companion during deletion/undo.
        candidates = tuple(
            item
            for item in self.document.model.connections
            if str(item.properties.get("core_connection_id", "")) == str(connection_id)
            or (
                str(item.properties.get("lifecycle_state", "")).upper() == "ORPHANED"
                and str(item.properties.get("orphaned_from_core_connection_id", "")) == str(connection_id)
            )
        )
        if len(candidates) > 1:
            raise ValueError(
                f"Multiple SLD connection companions map to Core connection {connection_id!r}."
            )
        connection = candidates[0] if candidates else None
        if connection is None:
            # Line/Cable SLD companions may retain their Core object ID as
            # their presentation ID. Use this legacy/shared-ID path only when
            # no explicit Core-to-presentation mapping exists.
            connection = self.document.model.get_connection_optional(connection_id)
        if connection is None:
            return "REMOVED"
        presentation_id = connection.connection_id
        snapshot = connection.to_dict()
        if not self.has_engineer_presentation_overrides_for_connection(connection):
            # Register an identity-aware inverse before removing the connection.
            def restore_removed_connection(snapshot=snapshot, original=connection) -> None:
                current = self.document.model.get_connection_optional(presentation_id)
                if current is None:
                    self._restore_connection_snapshot(snapshot)
                elif current is not original:
                    raise ValueError(
                        "Cannot restore deleted SLD connection; a different object "
                        f"now owns identity {presentation_id!r}."
                    )
                # If the original object is still registered, removal did not
                # complete (or the same object was reinserted); no duplicate is needed.

            transaction.record_undo(restore_removed_connection)
            self.document.model.remove_connection(presentation_id)
            self.document.mark_modified()
            return "REMOVED"

        def restore_orphaned_connection(snapshot=snapshot, original=connection) -> None:
            current = self.document.model.get_connection_optional(snapshot["connection_id"])
            if current is None:
                self._restore_connection_snapshot(snapshot)
            elif current is not original:
                raise ValueError(
                    "Cannot restore orphaned SLD connection; a different object "
                    f"now owns identity {snapshot['connection_id']!r}."
                )
            else:
                source = snapshot.get("source_endpoint")
                target = snapshot.get("target_endpoint")
                current.source_endpoint = (
                    None if source is None else SLDEndpoint.from_dict(source)
                )
                current.target_endpoint = (
                    None if target is None else SLDEndpoint.from_dict(target)
                )
                current.properties.clear()
                current.properties.update(dict(snapshot.get("properties", {})))
                self.document.mark_modified()

        # The authored connection remains in the document, so undo must restore
        # it in place rather than attempting to create a duplicate identity.
        transaction.record_undo(restore_orphaned_connection)
        connection.source_endpoint = None
        connection.target_endpoint = None
        connection.properties.pop("projection_source", None)
        # Core deletion severs the binding. The authored SLD presentation
        # remains, but its old Core ID is no longer authoritative identity.
        connection.properties.pop("core_connection_id", None)
        connection.properties["orphaned_from_core_connection_id"] = connection_id
        connection.properties["lifecycle_state"] = "ORPHANED"
        self.document.mark_modified()
        return "ORPHANED"

    def _restore_node_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.document.model.create_node(
            node_id=snapshot["node_id"],
            equipment_id=snapshot.get("equipment_id"),
            x=snapshot.get("x", 0.0),
            y=snapshot.get("y", 0.0),
            presentation=snapshot.get("presentation"),
            properties=snapshot.get("properties", {}),
        )
        for item in snapshot.get("connections", ()):
            self._restore_connection_snapshot(item)
        self.document.mark_modified()

    def _restore_connection_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.document.model.create_connection(
            connection_id=snapshot["connection_id"],
            source_node_id=snapshot["source_node_id"],
            target_node_id=snapshot["target_node_id"],
            source_endpoint=snapshot.get("source_endpoint"),
            target_endpoint=snapshot.get("target_endpoint"),
            route=snapshot.get("route"),
            properties=snapshot.get("properties", {}),
        )
        self.document.mark_modified()

    def _remove_node(self, command: Command, transaction: Transaction) -> ApplicationResult:
        node_id = command.payload["node_id"]
        node = self.document.model.get_node(node_id)
        projection_source = command.payload.get("projection_source")
        if projection_source is None:
            self._require_engineer_owned_node(node)
        else:
            if node.properties.get("projection_source") != projection_source:
                raise ValueError("SLD node projection ownership does not match the removal command.")
        node_snapshot = node.to_dict()
        original_node = node
        original_connections = {
            connection.connection_id: connection
            for connection in self.document.model.connections
            if connection.source_node_id == node_id or connection.target_node_id == node_id
        }
        connection_snapshots = tuple(
            connection.to_dict() for connection in original_connections.values()
        )
        annotation_snapshot = [
            block.to_dict() for block in self.document.annotation_blocks
        ]
        remaining_annotations = [
            item for item in annotation_snapshot if item.get("node_id") != node_id
        ]
        def restore() -> None:
            rollback_errors: list[str] = []
            node_identity_safe = True

            # Preflight every still-present attached connection before restoring
            # the node. Otherwise a later connection collision can make undo
            # fail after the node has already been recreated, leaving a partial
            # graph restoration.
            identity_property_keys = (
                "core_connection_id",
                "projection_source",
                "connection_kind",
                "presentation_owner",
                "route_owner",
                "lifecycle_state",
            )
            for snapshot in connection_snapshots:
                connection_id = snapshot["connection_id"]
                current = self.document.model.get_connection_optional(connection_id)
                if current is None:
                    continue
                original = original_connections[connection_id]
                if current is not original:
                    node_identity_safe = False
                    rollback_errors.append(
                        "Cannot restore removed SLD node atomically; attached "
                        f"connection identity reused: {connection_id!r}."
                    )
                    continue
                if current.to_dict() != snapshot:
                    node_identity_safe = False
                    rollback_errors.append(
                        "Cannot restore removed SLD node atomically; attached "
                        f"connection state changed while removed: {connection_id!r}."
                    )
                    continue
                snapshot_properties = dict(snapshot.get("properties", {}))
                source = snapshot.get("source_endpoint")
                target_endpoint = snapshot.get("target_endpoint")
                current_source = (
                    None if current.source_endpoint is None
                    else current.source_endpoint.to_dict()
                )
                current_target = (
                    None if current.target_endpoint is None
                    else current.target_endpoint.to_dict()
                )
                expected_identity_properties = {
                    key: deepcopy(snapshot_properties.get(key))
                    for key in identity_property_keys
                }
                current_identity_properties = {
                    key: deepcopy(current.properties.get(key))
                    for key in identity_property_keys
                }
                if (
                    current.source_node_id != snapshot["source_node_id"]
                    or current.target_node_id != snapshot["target_node_id"]
                    or current_source != source
                    or current_target != target_endpoint
                    or current_identity_properties != expected_identity_properties
                ):
                    node_identity_safe = False
                    rollback_errors.append(
                        "Cannot restore removed SLD node atomically; attached "
                        f"connection identity or ownership changed: {connection_id!r}."
                    )

            current_node = self.document.model.get_node_optional(node_snapshot["node_id"])
            if not node_identity_safe:
                rollback_errors.append(
                    "SLD node was not restored because attached-connection "
                    "preflight found an identity conflict."
                )
            elif current_node is None:
                try:
                    self.document.model.create_node(
                        node_id=node_snapshot["node_id"],
                        equipment_id=node_snapshot.get("equipment_id"),
                        x=node_snapshot.get("x", 0.0),
                        y=node_snapshot.get("y", 0.0),
                        presentation=node_snapshot.get("presentation"),
                        properties=node_snapshot.get("properties", {}),
                    )
                except Exception as exc:
                    node_identity_safe = False
                    rollback_errors.append(f"Cannot restore SLD node {node_snapshot['node_id']!r}: {exc}")
            elif current_node is not original_node:
                node_identity_safe = False
                rollback_errors.append(
                    f"Cannot restore removed SLD node; identity reused: {node_snapshot['node_id']!r}."
                )
            else:
                snapshot_properties = dict(node_snapshot.get("properties", {}))
                identity_keys = (
                    "element_type",
                    "terminal_ids",
                    "terminal_connectivity",
                    "projection_source",
                    "lifecycle_state",
                )
                expected_identity = {
                    key: deepcopy(snapshot_properties.get(key))
                    for key in identity_keys
                }
                current_identity = {
                    key: deepcopy(current_node.properties.get(key))
                    for key in identity_keys
                }
                if (
                    current_node.equipment_id != node_snapshot.get("equipment_id")
                    or current_identity != expected_identity
                ):
                    node_identity_safe = False
                    rollback_errors.append(
                        "Cannot restore removed SLD node; the original object's "
                        f"logical identity changed: {node_snapshot['node_id']!r}."
                    )
                else:
                    current_node.set_position(
                        float(node_snapshot.get("x", 0.0)),
                        float(node_snapshot.get("y", 0.0)),
                    )
                    if node_snapshot.get("presentation") is None:
                        current_node.clear_presentation()
                    else:
                        current_node.set_presentation(node_snapshot["presentation"])
                    current_node.properties.clear()
                    current_node.properties.update(deepcopy(snapshot_properties))
                    current_node.equipment_id = node_snapshot.get("equipment_id")

            if not node_identity_safe:
                rollback_errors.append(
                    "Attached connections were not restored because the original node identity is unavailable."
                )
            for snapshot in (connection_snapshots if node_identity_safe else ()):
                connection_id = snapshot["connection_id"]
                current = self.document.model.get_connection_optional(connection_id)
                original = original_connections[connection_id]
                if current is None:
                    try:
                        self._restore_connection_snapshot(snapshot)
                    except Exception as exc:
                        rollback_errors.append(f"Cannot restore SLD connection {connection_id!r}: {exc}")
                elif current is not original:
                    rollback_errors.append(
                        f"Cannot restore removed SLD node; connection identity reused: {connection_id!r}."
                    )
                elif current.to_dict() != snapshot:
                    rollback_errors.append(
                        "Cannot restore removed SLD node; attached connection state "
                        f"changed while removed: {connection_id!r}."
                    )
                else:
                    snapshot_properties = dict(snapshot.get("properties", {}))
                    source = snapshot.get("source_endpoint")
                    target_endpoint = snapshot.get("target_endpoint")
                    current_source = (
                        None if current.source_endpoint is None
                        else current.source_endpoint.to_dict()
                    )
                    current_target = (
                        None if current.target_endpoint is None
                        else current.target_endpoint.to_dict()
                    )
                    identity_property_keys = (
                        "core_connection_id",
                        "projection_source",
                        "connection_kind",
                        "presentation_owner",
                        "route_owner",
                        "lifecycle_state",
                    )
                    expected_identity_properties = {
                        key: deepcopy(snapshot_properties.get(key))
                        for key in identity_property_keys
                    }
                    current_identity_properties = {
                        key: deepcopy(current.properties.get(key))
                        for key in identity_property_keys
                    }
                    if (
                        current.source_node_id != snapshot["source_node_id"]
                        or current.target_node_id != snapshot["target_node_id"]
                        or current_source != source
                        or current_target != target_endpoint
                        or current_identity_properties != expected_identity_properties
                    ):
                        rollback_errors.append(
                            "Cannot restore removed SLD node; attached connection "
                            f"identity or ownership changed: {connection_id!r}."
                        )
                        continue
                    current.source_node_id = snapshot["source_node_id"]
                    current.target_node_id = snapshot["target_node_id"]
                    current.source_endpoint = None if source is None else SLDEndpoint.from_dict(source)
                    current.target_endpoint = None if target_endpoint is None else SLDEndpoint.from_dict(target_endpoint)
                    route = snapshot.get("route")
                    if route is not None:
                        current.route = current.route.__class__.from_dict(route)
                    current.properties.clear()
                    current.properties.update(deepcopy(snapshot_properties))

            # Annotation blocks are a separate mutable presentation state.
            # Restore them only if the node/connections were restored safely
            # and the live list is still the exact post-removal state. Never
            # overwrite annotations edited after this removal command.
            current_annotations = [
                block.to_dict() for block in self.document.annotation_blocks
            ]
            if current_annotations == annotation_snapshot:
                pass
            elif not node_identity_safe:
                rollback_errors.append(
                    "Annotation blocks were not restored because the removed "
                    "node identity or attached connections could not be restored safely."
                )
            elif current_annotations != remaining_annotations:
                rollback_errors.append(
                    "Cannot restore removed SLD node annotations; live annotation "
                    "state changed after node removal."
                )
            else:
                self.document.replace_annotation_blocks(annotation_snapshot)
                self.document.mark_modified()

            if rollback_errors:
                raise ValueError("SLD node removal rollback conflicts: " + " | ".join(rollback_errors))

        # Record the complete inverse before removing the node, its attached
        # connections, or its annotation blocks.
        transaction.record_undo(restore)
        self.document.model.remove_node(node_id)
        self.document.replace_annotation_blocks(remaining_annotations)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node removed.",
            metadata={"presentation_operation": "remove_node", "node_id": node_id},
        )

    def _add_connection(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        source_endpoint = p.get("source_endpoint")
        target_endpoint = p.get("target_endpoint")
        route = p.get("route")
        presentation_owner = str(p.get("presentation_owner", "engineer"))
        projection_source = p.get("projection_source")
        if presentation_owner not in {"engineer", "projection"}:
            raise ValueError("presentation_owner must be 'engineer' or 'projection'.")
        if projection_source is not None and presentation_owner != "projection":
            raise ValueError("projection_source requires presentation_owner='projection'.")
        properties = {"presentation_owner": presentation_owner}
        connection_kind = p.get("connection_kind")
        if connection_kind is not None:
            properties["connection_kind"] = str(connection_kind)
        connection_id = p["connection_id"]
        if self.document.model.get_connection_optional(connection_id) is not None:
            raise ValueError(f"SLD connection identity collision: {connection_id!r}.")
        core_connection_id = p.get("core_connection_id")
        if core_connection_id is not None:
            core_connection_id = str(core_connection_id).strip()
            if not core_connection_id:
                raise ValueError("core_connection_id must be a non-empty string when provided.")
            properties["core_connection_id"] = core_connection_id
        if projection_source is not None:
            properties["projection_source"] = str(projection_source)
        if connection_kind is not None:
            properties["lifecycle_state"] = "BOUND"
        if isinstance(route, Mapping) and route.get("ownership") == "engineer":
            properties["route_owner"] = "engineer"
        created_connection = None

        def endpoint_signature(endpoint: Any) -> dict[str, Any] | None:
            if endpoint is None:
                return None
            if isinstance(endpoint, Mapping):
                return dict(endpoint)
            serializer = getattr(endpoint, "to_dict", None)
            if callable(serializer):
                return serializer()
            raise TypeError("SLD connection endpoint must be canonical endpoint data.")

        if route is None:
            expected_route = {
                "routing_mode": "orthogonal",
                "ownership": "auto",
                "points": [],
            }
        elif isinstance(route, Mapping):
            expected_route = {
                "routing_mode": route.get("routing_mode", "orthogonal"),
                "ownership": route.get("ownership", "auto"),
                "points": [
                    [float(point[0]), float(point[1])]
                    for point in route.get("points", ())
                ],
            }
        else:
            serializer = getattr(route, "to_dict", None)
            if not callable(serializer):
                raise TypeError("SLD connection route must be canonical route data.")
            expected_route = serializer()

        def undo_added_connection() -> None:
            current = self.document.model.get_connection_optional(connection_id)
            if current is None:
                return
            if created_connection is not None and current is not created_connection:
                raise ValueError(
                    "Cannot undo added SLD connection; identity is now owned "
                    f"by another object: {connection_id!r}."
                )
            # Object identity alone is insufficient: the same live object may
            # have been rebound or had its route/ownership changed after add.
            # Only remove it when its complete creation signature still matches.
            if (
                current.source_node_id != p["source_node_id"]
                or current.target_node_id != p["target_node_id"]
                or endpoint_signature(current.source_endpoint) != endpoint_signature(source_endpoint)
                or endpoint_signature(current.target_endpoint) != endpoint_signature(target_endpoint)
                or current.route.to_dict() != expected_route
                or dict(current.properties) != properties
            ):
                raise ValueError(
                    "Cannot undo added SLD connection; its structure or ownership "
                    f"changed after creation: {connection_id!r}."
                )
            self.document.model.remove_connection(connection_id)
            self.document.mark_modified()

        # Register compensation before the first structural mutation so a
        # failure during creation or document modification remains reversible.
        transaction.record_undo(undo_added_connection)
        self.document.model.create_connection(
            connection_id=connection_id,
            source_node_id=p["source_node_id"],
            target_node_id=p["target_node_id"],
            source_endpoint=source_endpoint,
            target_endpoint=target_endpoint,
            route=route,
            properties=properties,
        )
        created_connection = self.document.model.get_connection_optional(connection_id)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD connection added.",
            metadata={"presentation_operation": "add_connection", "connection_id": p["connection_id"]},
        )

    def _set_annotation_blocks(self, command: Command, transaction: Transaction) -> ApplicationResult:
        """Replace SLD annotation configuration atomically and retain undo history."""
        payload = command.payload.get("annotation_blocks")
        if not isinstance(payload, list) or any(not isinstance(item, Mapping) for item in payload):
            raise TypeError("annotation_blocks must be a list of mappings")
        previous = [block.to_dict() for block in self.document.annotation_blocks]
        updated = [deepcopy(dict(item)) for item in payload]
        if updated == previous:
            return ApplicationResult.success_result(
                message="SLD annotation blocks are unchanged.",
                metadata={"presentation_operation": "set_annotation_blocks"},
            )

        # Register the inverse before mutation so validation failures remain
        # transaction-safe. On rollback/undo, only restore over the exact state
        # this command installed; accept the original state as an idempotent
        # no-op when validation failed before mutation.
        installed_state = None

        def restore_annotation_blocks() -> None:
            current = [
                block.to_dict() for block in self.document.annotation_blocks
            ]
            if current == previous:
                return
            if installed_state is None or current != installed_state:
                raise ValueError(
                    "Cannot undo SLD annotation-block update; live annotation "
                    "state changed after this command."
                )
            self.document.replace_annotation_blocks(previous)
            self.document.mark_modified()

        transaction.record_undo(restore_annotation_blocks)
        self.document.replace_annotation_blocks(updated)
        installed_state = [
            block.to_dict() for block in self.document.annotation_blocks
        ]
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD annotation blocks updated.",
            metadata={"presentation_operation": "set_annotation_blocks"},
        )

    def _set_node_properties(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        original_node = node
        original_node_identity = {
            key: deepcopy(node.properties.get(key))
            for key in (
                "element_type", "terminal_ids", "terminal_connectivity",
                "projection_source", "lifecycle_state",
            )
        }
        original_equipment_id = node.equipment_id
        previous = deepcopy(node.properties)
        properties = p["properties"]
        if not isinstance(properties, Mapping):
            raise TypeError("properties must be a mapping")
        semantic_keys = {
            "equipment_id", "element_type", "terminal_ids", "terminal_connectivity",
            "attributes", "projection_source", "lifecycle_state",
        }
        if semantic_keys.intersection(properties):
            raise ValueError("SLD semantic binding fields are Application-owned and cannot be edited as presentation properties.")
        updated_properties = deepcopy(previous)
        updated_properties.update(deepcopy(dict(properties)))
        if "labels" in properties and "labels_owner" not in properties:
            updated_properties["labels_owner"] = "engineer"
        if "visual_properties" in properties and "visual_properties_owner" not in properties:
            updated_properties["visual_properties_owner"] = "engineer"
        if "manual_geometry" in properties and "manual_geometry_owner" not in properties:
            updated_properties["manual_geometry_owner"] = "engineer"
        if updated_properties == previous:
            return ApplicationResult.success_result(
                message="SLD node presentation properties are unchanged.",
                metadata={"presentation_operation": "set_node_properties", "node_id": p["node_id"]},
            )
        def restore_properties() -> None:
            # Resolve through the live document model at undo time. A later
            # remove/restore cycle may have replaced the original node object;
            # mutating the captured object would silently leave persisted state
            # unchanged on the currently registered node.
            target = self.document.model.get_node_optional(p["node_id"])
            if target is None:
                raise ValueError(
                    f"Cannot undo SLD node property edit; node is absent: {p['node_id']!r}."
                )
            target_identity = {
                key: deepcopy(target.properties.get(key))
                for key in original_node_identity
            }
            if (
                target.equipment_id != original_equipment_id
                or target_identity != original_node_identity
            ):
                raise ValueError(
                    "Cannot undo SLD node property edit; the live node's logical "
                    f"identity changed for {p['node_id']!r}."
                )
            target.properties.clear()
            target.properties.update(deepcopy(previous))
            self.document.mark_modified()

        transaction.record_undo(restore_properties)
        node.properties.clear()
        node.properties.update(updated_properties)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node presentation properties updated.",
            metadata={"presentation_operation": "set_node_properties", "node_id": p["node_id"]},
        )

    def _set_connection_route(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        connection = self.document.model.get_connection(p["connection_id"])
        owner = connection.properties.get("presentation_owner")
        if owner not in (None, "engineer", "projection"):
            raise ValueError("SLD connection ownership is unknown; route edit is rejected.")
        original_connection = connection
        original_connection_identity = {
            "source_node_id": connection.source_node_id,
            "target_node_id": connection.target_node_id,
            "source_endpoint": None if connection.source_endpoint is None else connection.source_endpoint.to_dict(),
            "target_endpoint": None if connection.target_endpoint is None else connection.target_endpoint.to_dict(),
            "core_connection_id": connection.properties.get("core_connection_id"),
            "projection_source": connection.properties.get("projection_source"),
            "connection_kind": connection.properties.get("connection_kind"),
        }
        previous = connection.route
        previous_properties = deepcopy(connection.properties)
        updated = self.document.model.get_connection(p["connection_id"]).route.__class__.from_dict(p["route"])
        if updated.ownership != "engineer":
            updated = updated.__class__(routing_mode=updated.routing_mode, ownership="engineer", points=updated.points)
        updated_properties = deepcopy(previous_properties)
        updated_properties["route_owner"] = "engineer"
        updated_properties.setdefault("presentation_owner", "projection" if updated_properties.get("projection_source") else "engineer")
        expected_route_edit_identity = {
            **original_connection_identity,
            "presentation_owner": updated_properties.get("presentation_owner"),
            "route_owner": updated_properties.get("route_owner"),
        }
        if updated == previous and updated_properties == previous_properties:
            return ApplicationResult.success_result(
                message="SLD connection route is unchanged.",
                metadata={"presentation_operation": "set_connection_route", "connection_id": p["connection_id"]},
            )
        def restore_route() -> None:
            # Undo may follow a remove/restore cycle that reconstructs the
            # connection object. Restore the live model object by canonical ID,
            # not the stale instance captured by the forward handler.
            target = self.document.model.get_connection_optional(p["connection_id"])
            if target is None:
                raise ValueError(
                    f"Cannot undo SLD connection route edit; connection is absent: {p['connection_id']!r}."
                )
            target_identity = {
                "source_node_id": target.source_node_id,
                "target_node_id": target.target_node_id,
                "source_endpoint": None if target.source_endpoint is None else target.source_endpoint.to_dict(),
                "target_endpoint": None if target.target_endpoint is None else target.target_endpoint.to_dict(),
                "core_connection_id": target.properties.get("core_connection_id"),
                "projection_source": target.properties.get("projection_source"),
                "connection_kind": target.properties.get("connection_kind"),
                "presentation_owner": target.properties.get("presentation_owner"),
                "route_owner": target.properties.get("route_owner"),
            }
            if target_identity != expected_route_edit_identity:
                raise ValueError(
                    "Cannot undo SLD connection route edit; the live connection "
                    f"identity or ownership changed for {p['connection_id']!r}."
                )
            target.route = previous
            target.properties.clear()
            target.properties.update(deepcopy(previous_properties))
            self.document.mark_modified()

        transaction.record_undo(restore_route)
        connection.route = updated
        connection.properties.clear()
        connection.properties.update(updated_properties)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD connection route updated.",
            metadata={"presentation_operation": "set_connection_route", "connection_id": p["connection_id"]},
        )

    @staticmethod
    def _require_engineer_owned_node(node: Any) -> None:
        source = node.properties.get("projection_source")
        owner = node.properties.get("presentation_owner")
        if source in {"application_read_model", "protection_read_model"}:
            raise ValueError(
                "Projection-owned SLD nodes cannot be removed through "
                "engineer-owned presentation commands."
            )
        if source is not None or owner not in (None, "engineer"):
            raise ValueError(
                "SLD node ownership is unknown or invalid; removal is rejected."
            )

    @staticmethod
    def _require_engineer_owned_connection(connection: Any) -> None:
        source = connection.properties.get("projection_source")
        owner = connection.properties.get("presentation_owner")
        if source in {"application_read_model", "protection_read_model"}:
            raise ValueError(
                "Projection-owned SLD connections cannot be removed through "
                "engineer-owned presentation commands."
            )
        if owner != "engineer":
            raise ValueError(
                "SLD connection ownership is unknown; removal is rejected."
            )

    def _remove_connection(self, command: Command, transaction: Transaction, *, context: Any = None) -> ApplicationResult:
        connection_id = command.payload["connection_id"]
        connection = self.document.model.get_connection(connection_id)

        # Projection-owned Simple Wire presentation is not an independent
        # electrical authority. Electrical deletion must originate from the
        # Core SimpleWire command; Application pre-commit then removes the
        # persistent SLD companion in the same transaction.
        if (
            connection.properties.get("projection_source") == "application_read_model"
            and connection.properties.get("connection_kind") == "SIMPLE_WIRE"
        ):
            raise ValueError(
                "Projection-owned Simple Wire SLD connections cannot be removed "
                "through an SLD-only command; use the Application Simple Wire "
                "deletion command."
            )

        self._require_engineer_owned_connection(connection)
        snapshot = connection.to_dict()
        original_connection = connection

        def restore() -> None:
            restored_id = snapshot["connection_id"]
            current = self.document.model.get_connection_optional(restored_id)
            if current is None:
                self.document.model.create_connection(
                    connection_id=restored_id,
                    source_node_id=snapshot["source_node_id"],
                    target_node_id=snapshot["target_node_id"],
                    source_endpoint=snapshot.get("source_endpoint"),
                    target_endpoint=snapshot.get("target_endpoint"),
                    route=snapshot.get("route"),
                    properties=deepcopy(snapshot.get("properties", {})),
                )
            elif current is not original_connection:
                raise ValueError(
                    f"Cannot restore removed SLD connection; identity reused: {restored_id!r}."
                )
            else:
                snapshot_properties = dict(snapshot.get("properties", {}))
                source = snapshot.get("source_endpoint")
                target = snapshot.get("target_endpoint")
                current_source = (
                    None if current.source_endpoint is None
                    else current.source_endpoint.to_dict()
                )
                current_target = (
                    None if current.target_endpoint is None
                    else current.target_endpoint.to_dict()
                )
                identity_property_keys = (
                    "core_connection_id",
                    "projection_source",
                    "connection_kind",
                    "presentation_owner",
                    "route_owner",
                    "lifecycle_state",
                )
                expected_identity_properties = {
                    key: deepcopy(snapshot_properties.get(key))
                    for key in identity_property_keys
                }
                current_identity_properties = {
                    key: deepcopy(current.properties.get(key))
                    for key in identity_property_keys
                }
                if (
                    current.source_node_id != snapshot["source_node_id"]
                    or current.target_node_id != snapshot["target_node_id"]
                    or current_source != source
                    or current_target != target
                    or current_identity_properties != expected_identity_properties
                ):
                    raise ValueError(
                        "Cannot restore removed SLD connection; the original "
                        f"connection identity or ownership changed: {restored_id!r}."
                    )
                current.source_node_id = snapshot["source_node_id"]
                current.target_node_id = snapshot["target_node_id"]
                current.source_endpoint = None if source is None else SLDEndpoint.from_dict(source)
                current.target_endpoint = None if target is None else SLDEndpoint.from_dict(target)
                route = snapshot.get("route")
                if route is not None:
                    current.route = current.route.__class__.from_dict(route)
                current.properties.clear()
                current.properties.update(deepcopy(snapshot_properties))
            self.document.mark_modified()

        # Register the inverse before removing the connection. If the removal
        # fails before changing state, rollback recognizes the original object.
        transaction.record_undo(restore)
        self.document.model.remove_connection(connection_id)
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD connection removed.",
            metadata={"presentation_operation": "remove_connection", "connection_id": connection_id},
        )


__all__ = ["SLDService", "SLDState"]
