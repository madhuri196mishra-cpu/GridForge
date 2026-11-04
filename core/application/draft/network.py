# ============================================================
# File: core/application/draft/network.py
# GridForge V2
# Author: Subhendu Mishra
# ============================================================
"""Application-owned SLD authoring state; never authoritative Core state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
import re
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DraftEndpointReference:
    """Stable authoring identity for a draft terminal or a Bus attachment."""
    kind: str
    object_id: str
    terminal_role: str | None = None
    attachment_id: str | None = None
    equipment_type: str | None = None
    scope: str = "draft"

    def __post_init__(self) -> None:
        if self.kind not in {"terminal", "bus"}:
            raise ValueError("Draft endpoint kind must be 'terminal' or 'bus'.")
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise ValueError("Draft endpoint object_id must be non-empty.")
        object.__setattr__(self, "object_id", self.object_id.strip())
        if self.scope not in {"draft", "core"}:
            raise ValueError("Draft endpoint scope must be draft or core.")
        if self.kind == "terminal":
            if not isinstance(self.terminal_role, str) or not self.terminal_role.strip():
                raise ValueError("Draft terminal endpoint requires terminal_role.")
            if not isinstance(self.equipment_type, str) or not self.equipment_type.strip():
                raise ValueError("Draft terminal endpoint requires equipment_type.")
            if self.attachment_id is not None:
                raise ValueError("Draft terminal endpoint cannot carry attachment_id.")
            object.__setattr__(self, "terminal_role", self.terminal_role.strip())
            object.__setattr__(self, "equipment_type", self.equipment_type.strip().lower())
        else:
            if not isinstance(self.attachment_id, str) or not self.attachment_id.strip():
                raise ValueError("Draft Bus endpoint requires attachment_id.")
            if self.terminal_role is not None or self.equipment_type is not None:
                raise ValueError("Draft Bus endpoint cannot carry terminal identity.")
            attachment_id = self.attachment_id.strip()
            if not re.fullmatch(r"attachment-(?:[0-8])", attachment_id):
                raise ValueError(
                    "Bus attachment_id must use canonical 'attachment-N' form "
                    "with an index in the supported range 0..8."
                )
            object.__setattr__(self, "attachment_id", attachment_id)

    @classmethod
    def terminal(cls, *, draft_id: str, equipment_type: str, terminal_role: str, scope: str = "draft") -> "DraftEndpointReference":
        return cls("terminal", draft_id, terminal_role=terminal_role, equipment_type=equipment_type, scope=scope)

    @classmethod
    def bus(
        cls,
        *,
        bus_id: str,
        attachment_id: str,
        scope: str = "draft",
    ) -> "DraftEndpointReference":
        """Create a canonical draft or committed Bus attachment boundary."""
        return cls(
            "bus",
            bus_id,
            attachment_id=attachment_id,
            scope=scope,
        )

    @property
    def is_terminal(self) -> bool: return self.kind == "terminal"
    @property
    def is_bus(self) -> bool: return self.kind == "bus"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "object_id": self.object_id, "terminal_role": self.terminal_role,
                "attachment_id": self.attachment_id, "equipment_type": self.equipment_type, "scope": self.scope}

    def to_core_reference(self):
        """Translate this canonical authoring identity without substituting endpoints."""
        from core.model import EndpointReference, EquipmentType
        if self.is_bus:
            return EndpointReference.bus(self.object_id, self.attachment_id or "")
        return EndpointReference.terminal(
            equipment_type=EquipmentType(str(self.equipment_type).strip().lower()),
            equipment_id=self.object_id,
            terminal_role=self.terminal_role or "",
        )

    @classmethod
    def from_value(cls, value: Any) -> "DraftEndpointReference":
        if isinstance(value, cls): return value
        if not isinstance(value, Mapping): raise TypeError("Draft endpoint must be a mapping or DraftEndpointReference.")
        return cls(str(value["kind"]), str(value["object_id"]), value.get("terminal_role"), value.get("attachment_id"), value.get("equipment_type"), str(value.get("scope", "draft")))




@dataclass(frozen=True, slots=True)
class DraftEquipment:
    draft_id: str
    equipment_type: str
    display_name: str
    terminal_contract: tuple[str, ...]
    engineering_data: Mapping[str, Any] = field(default_factory=dict)
    endpoints: Mapping[str, DraftEndpointReference] = field(default_factory=dict)
    placement: tuple[float, float] | None = None
    presentation: Mapping[str, Any] = field(default_factory=dict)
    validation_state: Mapping[str, Any] = field(default_factory=dict)
    command_type: str = ""
    id_field: str = ""
    parameter_mapping: Mapping[str, str] = field(default_factory=dict)
    endpoint_mapping: Mapping[str, str] = field(default_factory=dict)
    required_connection_terminals: tuple[str, ...] = ()
    parameter_contract: tuple[Mapping[str, Any], ...] = ()
    conditional_requirements: tuple[Mapping[str, Any], ...] = ()
    command_field_dispositions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.draft_id.strip() or not self.equipment_type.strip(): raise ValueError("Draft equipment identity is required.")
        if not self.command_type.strip() or not self.id_field.strip(): raise ValueError("DraftEquipment requires its canonical command contract.")
        object.__setattr__(self, "terminal_contract", tuple(str(x) for x in self.terminal_contract))
        if self.placement is not None:
            if (
                not isinstance(self.placement, (tuple, list))
                or len(self.placement) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in self.placement
                )
            ):
                raise ValueError("Draft equipment placement must be a finite numeric (x, y) pair or None.")
            placement = (float(self.placement[0]), float(self.placement[1]))
            if not all(isfinite(value) for value in placement):
                raise ValueError("Draft equipment placement coordinates must be finite.")
            object.__setattr__(self, "placement", placement)
        object.__setattr__(self, "engineering_data", MappingProxyType(dict(self.engineering_data)))
        object.__setattr__(self, "endpoints", MappingProxyType({str(k): DraftEndpointReference.from_value(v) for k, v in self.endpoints.items()}))
        object.__setattr__(self, "presentation", MappingProxyType(dict(self.presentation)))
        object.__setattr__(self, "validation_state", MappingProxyType(dict(self.validation_state)))
        object.__setattr__(self, "parameter_mapping", MappingProxyType(dict(self.parameter_mapping)))
        object.__setattr__(self, "endpoint_mapping", MappingProxyType(dict(self.endpoint_mapping)))
        required_terminals = tuple(str(role).strip() for role in self.required_connection_terminals)
        if any(not role for role in required_terminals):
            raise ValueError("Required connection terminal names must be non-empty.")
        if len(set(required_terminals)) != len(required_terminals):
            raise ValueError("Required connection terminal names must be unique.")
        if not set(required_terminals).issubset(set(self.terminal_contract)):
            raise ValueError("Required connection terminals must belong to the canonical terminal contract.")
        object.__setattr__(self, "required_connection_terminals", required_terminals)
        object.__setattr__(self, "parameter_contract", tuple(MappingProxyType(dict(x)) for x in self.parameter_contract))
        object.__setattr__(self, "conditional_requirements", tuple(MappingProxyType(dict(x)) for x in self.conditional_requirements))
        dispositions = {}
        for field_name, disposition in self.command_field_dispositions.items():
            if not isinstance(field_name, str) or not field_name.strip() or not isinstance(disposition, Mapping):
                raise TypeError("Draft command-field dispositions require non-empty field names and mapping values.")
            kind = disposition.get("kind")
            reason = disposition.get("reason")
            allowed_kinds = {
                "metadata", "operating_point", "later_configuration",
                "lifecycle_state", "operational_state", "measurement_association",
                "provisional_default", "not_applicable",
            }
            if not isinstance(kind, str) or kind not in allowed_kinds:
                raise ValueError(f"Draft command-field disposition {field_name!r} has an unsupported kind.")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Draft command-field disposition {field_name!r} requires a reason.")
            workflow = disposition.get("workflow")
            if kind in {
                "operating_point", "later_configuration", "lifecycle_state",
                "operational_state", "measurement_association",
            } and (not isinstance(workflow, str) or not workflow.strip()):
                raise ValueError(f"Draft command-field disposition {field_name!r} requires its canonical workflow.")
            if workflow is not None and (not isinstance(workflow, str) or not workflow.strip()):
                raise ValueError(f"Draft command-field disposition {field_name!r} has an invalid workflow.")
            dispositions[field_name.strip()] = MappingProxyType({
                "kind": kind.strip(), "reason": reason.strip(),
                **({"workflow": workflow.strip()} if isinstance(workflow, str) else {}),
            })
        object.__setattr__(self, "command_field_dispositions", MappingProxyType(dispositions))

    def to_dict(self) -> dict[str, Any]:
        return {"draft_id": self.draft_id, "equipment_type": self.equipment_type, "display_name": self.display_name,
                "terminal_contract": list(self.terminal_contract), "engineering_data": deepcopy(dict(self.engineering_data)),
                "endpoints": {k: v.to_dict() for k, v in self.endpoints.items()},
                "placement": None if self.placement is None else list(self.placement),
                "presentation": deepcopy(dict(self.presentation)), "validation_state": deepcopy(dict(self.validation_state)),
                "command_type": self.command_type, "id_field": self.id_field,
                "parameter_mapping": dict(self.parameter_mapping), "endpoint_mapping": dict(self.endpoint_mapping),
                "required_connection_terminals": list(self.required_connection_terminals),
                "parameter_contract": [dict(x) for x in self.parameter_contract],
                "conditional_requirements": [dict(x) for x in self.conditional_requirements],
                "command_field_dispositions": {k: dict(v) for k, v in self.command_field_dispositions.items()}}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DraftEquipment":
        placement = data.get("placement")
        return cls(str(data["draft_id"]), str(data["equipment_type"]), str(data.get("display_name", data["equipment_type"])),
                    tuple(str(x) for x in data.get("terminal_contract", ())), dict(data.get("engineering_data", {})),
                    {k: DraftEndpointReference.from_value(v) for k, v in dict(data.get("endpoints", {})).items()},
                    None if placement is None else (float(placement[0]), float(placement[1])), dict(data.get("presentation", {})),
                    dict(data.get("validation_state", {})), str(data["command_type"]), str(data["id_field"]),
                    dict(data.get("parameter_mapping", {})), dict(data.get("endpoint_mapping", {})),
                    tuple(str(x) for x in data.get("required_connection_terminals", ())),
                    tuple(dict(x) for x in data.get("parameter_contract", ())),
                    tuple(dict(x) for x in data.get("conditional_requirements", ())),
                    {str(k): dict(v) for k, v in dict(data.get("command_field_dispositions", {})).items()})


@dataclass(frozen=True, slots=True)
class DraftConnection:
    """One immutable connection whose endpoints are the canonical draft identities."""
    connection_id: str
    source: DraftEndpointReference
    target: DraftEndpointReference
    connection_kind: str = "simple_wire"
    route: Mapping[str, Any] = field(default_factory=dict)
    validation_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.connection_id, str) or not self.connection_id.strip(): raise ValueError("Draft connection_id is required.")
        object.__setattr__(self, "source", DraftEndpointReference.from_value(self.source))
        object.__setattr__(self, "target", DraftEndpointReference.from_value(self.target))
        if self.source == self.target: raise ValueError("Draft connection endpoints must be distinct.")
        object.__setattr__(self, "connection_id", self.connection_id.strip())
        object.__setattr__(self, "connection_kind", str(self.connection_kind).strip().lower())

        # Route coordinates are presentation metadata, never electrical
        # endpoints. Canonicalize consecutive duplicate bends at the draft
        # boundary and reject malformed/non-finite coordinates before they
        # reach projection or commit. Keep non-consecutive revisits intact:
        # they may represent an intentional authored route.
        route = dict(self.route)
        if "points" in route:
            raw_points = route["points"]
            if not isinstance(raw_points, (tuple, list)):
                raise ValueError("Draft connection route points must be a sequence.")
            normalized_points = []
            for index, raw_point in enumerate(raw_points):
                if not isinstance(raw_point, (tuple, list)) or len(raw_point) != 2:
                    raise ValueError(
                        f"Draft connection route point {index} must contain exactly two coordinates."
                    )
                point = []
                for coordinate in raw_point:
                    if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                        raise ValueError("Draft connection route coordinates must be numeric and finite.")
                    value = float(coordinate)
                    if not isfinite(value):
                        raise ValueError("Draft connection route coordinates must be numeric and finite.")
                    point.append(value)
                normalized = (point[0], point[1])
                if not normalized_points or normalized != normalized_points[-1]:
                    normalized_points.append(normalized)
            if raw_points and len(normalized_points) < 2:
                raise ValueError("Draft connection route must contain at least two distinct consecutive points.")
            route["points"] = tuple(normalized_points)
        object.__setattr__(self, "route", MappingProxyType(route))
        object.__setattr__(self, "validation_state", MappingProxyType(dict(self.validation_state)))

    @property
    def source_draft_id(self) -> str: return self.source.object_id
    @property
    def target_draft_id(self) -> str: return self.target.object_id
    @property
    def source_terminal(self) -> str | None: return self.source.terminal_role
    @property
    def target_terminal(self) -> str | None: return self.target.terminal_role

    def to_dict(self) -> dict[str, Any]:
        def canonical_value(value: Any) -> Any:
            # Command payloads thaw immutable tuples into lists. Keep nested
            # route metadata canonical across that round-trip.
            if isinstance(value, Mapping):
                return {str(key): canonical_value(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [canonical_value(item) for item in value]
            return deepcopy(value)

        return {
            "connection_id": self.connection_id,
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "connection_kind": self.connection_kind,
            "route": canonical_value(self.route),
            "validation_state": canonical_value(self.validation_state),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DraftConnection":
        # Reject the former dual-source representation instead of silently
        # maintaining two independent endpoint identities.
        if "source" not in data or "target" not in data:
            raise ValueError("DraftConnection requires canonical source and target DraftEndpointReference values.")
        return cls(str(data["connection_id"]), DraftEndpointReference.from_value(data["source"]), DraftEndpointReference.from_value(data["target"]),
                    str(data.get("connection_kind", "simple_wire")), dict(data.get("route", {})), dict(data.get("validation_state", {})))


class DraftNetwork:
    SCHEMA = 2
    def __init__(self, project_id: str, activation_generation: int):
        if not str(project_id).strip() or int(activation_generation) < 1: raise ValueError("DraftNetwork requires valid project scope.")
        self.project_id = str(project_id); self.activation_generation = int(activation_generation)
        self._equipment: dict[str, DraftEquipment] = {}; self._connections: dict[str, DraftConnection] = {}; self.validation_state: dict[str, Any] = {}

    @property
    def equipment(self) -> tuple[DraftEquipment, ...]: return tuple(self._equipment.values())
    @property
    def connections(self) -> tuple[DraftConnection, ...]: return tuple(self._connections.values())
    def require_equipment(self, draft_id: str) -> DraftEquipment: return self._equipment[draft_id]

    def add_equipment(self, equipment: DraftEquipment) -> None:
        if equipment.draft_id in self._equipment: raise ValueError(f"Draft equipment already exists: {equipment.draft_id}")
        self._equipment[equipment.draft_id] = equipment; self.validation_state = {}

    def update_equipment(self, draft_id: str, **changes: Any) -> DraftEquipment:
        current = self.require_equipment(draft_id); allowed = {"display_name","engineering_data","endpoints","placement","presentation","validation_state"}
        unknown = set(changes) - allowed
        if unknown: raise ValueError(f"Unsupported DraftEquipment fields: {sorted(unknown)!r}")
        values = {name: getattr(current, name) for name in ("draft_id","equipment_type","display_name","terminal_contract","engineering_data","endpoints","placement","presentation","validation_state","command_type","id_field","parameter_mapping","endpoint_mapping","required_connection_terminals","parameter_contract","conditional_requirements","command_field_dispositions")}
        values.update(changes); updated = DraftEquipment(**values); self._equipment[draft_id] = updated; self.validation_state = {}; return updated

    def remove_equipment(self, draft_id: str) -> DraftEquipment:
        equipment = self._equipment.pop(draft_id)

        # Endpoint identity includes scope. A committed Core endpoint may
        # legitimately share the same object_id string as a transient draft
        # item; removing that draft item must not erase the independent Core
        # connection.
        def belongs_to_removed_draft(endpoint: DraftEndpointReference) -> bool:
            return endpoint.scope == "draft" and endpoint.object_id == draft_id

        for connection_id, connection in tuple(self._connections.items()):
            if (
                belongs_to_removed_draft(connection.source)
                or belongs_to_removed_draft(connection.target)
            ):
                self._connections.pop(connection_id)
        self.validation_state = {}
        return equipment

    def _validate_endpoint(self, endpoint: DraftEndpointReference, *, connection_id: str | None = None, network: Any | None = None) -> None:
        prefix = f"{connection_id}: " if connection_id else ""
        if endpoint.is_bus:
            if endpoint.terminal_role is not None or endpoint.equipment_type is not None:
                raise ValueError(f"{prefix}Draft Bus endpoint cannot carry terminal identity.")
            if not endpoint.attachment_id:
                raise ValueError(f"{prefix}Bus endpoint attachment is missing.")
            if endpoint.scope == "draft":
                equipment = self._equipment.get(endpoint.object_id)
                if equipment is None:
                    raise ValueError(
                        f"{prefix}unknown draft Bus {endpoint.object_id!r}."
                    )
                if str(equipment.equipment_type).strip().lower() != "bus":
                    raise ValueError(
                        f"{prefix}draft Bus endpoint {endpoint.object_id!r} "
                        "does not identify DraftEquipment(type='bus')."
                    )
                # DraftEndpointReference validates the full canonical
                # identifier and the established 0..8 index range.
                return
            if endpoint.scope == "core":
                if network is None:
                    return
                from core.network.electrical_boundary import EndpointCompatibility, EndpointCompatibilityError
                try:
                    EndpointCompatibility.validate_reference(endpoint.to_core_reference(), network)
                except EndpointCompatibilityError as exc:
                    raise ValueError(f"{prefix}{exc}") from exc
                return
            raise ValueError(f"{prefix}unsupported Bus endpoint scope {endpoint.scope!r}.")
        if endpoint.scope == "core":
            if network is None: return
            from core.network.electrical_boundary import EndpointCompatibility, EndpointCompatibilityError
            try: EndpointCompatibility.validate_reference(endpoint.to_core_reference(), network)
            except EndpointCompatibilityError as exc: raise ValueError(f"{prefix}{exc}") from exc
            return
        equipment = self._equipment.get(endpoint.object_id)
        if equipment is None: raise ValueError(f"{prefix}unknown draft equipment {endpoint.object_id!r}.")
        if endpoint.terminal_role not in equipment.terminal_contract: raise ValueError(f"{prefix}unknown terminal role {endpoint.terminal_role!r} for {endpoint.object_id!r}.")
        # DraftEquipment.endpoints stores the declared *opposite* endpoint
        # for this terminal role, so it must not be compared with this terminal
        # reference. DraftConnection is the authoring topology authority;
        # CommitNetworkHandler reconciles any declaration with the graph's
        # opposite endpoint before preparing Core commands.

    @staticmethod
    def _endpoint_pair_key(
        source: DraftEndpointReference,
        target: DraftEndpointReference,
    ) -> tuple[DraftEndpointReference, DraftEndpointReference]:
        """Return an orientation-independent key for one semantic wire pair."""
        return tuple(sorted((source, target), key=lambda endpoint: repr(endpoint.to_dict())))

    def validate_connection_candidate(self, connection: DraftConnection) -> None:
        """Validate a candidate without changing draft state or transaction history."""
        if connection.connection_id in self._connections:
            raise ValueError(f"Draft connection already exists: {connection.connection_id}")
        if connection.connection_kind != "simple_wire":
            raise ValueError("Only simple_wire DraftConnection is supported by the canonical authoring contract.")
        self._validate_endpoint(connection.source, connection_id=connection.connection_id)
        self._validate_endpoint(connection.target, connection_id=connection.connection_id)
        if connection.source == connection.target:
            raise ValueError(f"{connection.connection_id}: self-connection is invalid.")

        # Reuse the canonical Core electrical-domain policy during draft
        # preflight; never wait until commit to reject measurement-secondary
        # terminals or other incompatible Simple Wire endpoint pairs.
        from core.network.electrical_boundary import (
            EndpointCompatibility,
            EndpointCompatibilityError,
        )
        try:
            EndpointCompatibility.validate_pair(
                connection.source.to_core_reference(),
                connection.target.to_core_reference(),
            )
        except EndpointCompatibilityError as exc:
            raise ValueError(f"{connection.connection_id}: {exc}") from exc

        candidate_pair = self._endpoint_pair_key(connection.source, connection.target)
        for existing in self._connections.values():
            if self._endpoint_pair_key(existing.source, existing.target) == candidate_pair:
                raise ValueError(
                    f"{connection.connection_id}: duplicate endpoint pair; "
                    f"already used by {existing.connection_id}."
                )

    def add_connection(self, connection: DraftConnection) -> None:
        self.validate_connection_candidate(connection)
        # Mutate only after every preflight check succeeds, so a rejected wire
        # cannot leave partially updated draft state.
        self._connections[connection.connection_id] = connection
        self.validation_state = {}

    def remove_connection(self, connection_id: str) -> DraftConnection:
        connection = self._connections.pop(connection_id)
        self.validation_state = {}
        return connection

    def validate(self, network: Any | None = None) -> tuple[str, ...]:
        errors: list[str] = []
        seen_pairs: set[tuple[DraftEndpointReference, DraftEndpointReference]] = set()
        for item in self._equipment.values():
            if item.placement is None:
                errors.append(f"{item.draft_id}: placement is required.")
            if len(set(item.terminal_contract)) != len(item.terminal_contract):
                errors.append(f"{item.draft_id}: duplicate terminal role in contract.")
            # DraftEquipment.endpoints is legacy acquisition metadata, not
            # topology. Only DraftConnection endpoints are validated as wiring.
        for item in self._connections.values():
            try:
                self._validate_endpoint(
                    item.source, connection_id=item.connection_id, network=network
                )
                self._validate_endpoint(
                    item.target, connection_id=item.connection_id, network=network
                )
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))
                continue
            pair = self._endpoint_pair_key(item.source, item.target)
            if pair in seen_pairs:
                errors.append(f"{item.connection_id}: duplicate endpoint pair.")
            seen_pairs.add(pair)
            if item.source == item.target:
                errors.append(f"{item.connection_id}: self-connection is invalid.")
        self.validation_state = {"errors": tuple(errors), "valid": not errors}
        return tuple(errors)

    def validate_required_connections(self) -> tuple[str, ...]:
        """Validate explicitly required terminal connectivity at commit time."""
        errors: list[str] = []
        for item in self._equipment.values():
            for role in item.required_connection_terminals:
                matches = 0
                for connection in self._connections.values():
                    for endpoint in (connection.source, connection.target):
                        if (
                            endpoint.is_terminal
                            and endpoint.scope == "draft"
                            and endpoint.object_id == item.draft_id
                            and endpoint.equipment_type == item.equipment_type.strip().lower()
                            and endpoint.terminal_role == role
                        ):
                            matches += 1
                if matches == 0:
                    errors.append(
                        f"{item.draft_id}: required terminal {role!r} is not connected."
                    )
                elif matches > 1:
                    errors.append(
                        f"{item.draft_id}: required terminal {role!r} has "
                        f"{matches} connections; exactly one is allowed."
                    )
        return tuple(errors)

    def clear_after_commit(self) -> None: self._equipment.clear(); self._connections.clear(); self.validation_state = {}
    def restore_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        restored = DraftNetwork.from_dict(snapshot, project_id=self.project_id, activation_generation=self.activation_generation)
        self._equipment = restored._equipment; self._connections = restored._connections; self.validation_state = restored.validation_state
    def to_dict(self) -> dict[str, Any]: return {"schema": self.SCHEMA, "project_id": self.project_id, "activation_generation": self.activation_generation, "equipment": [x.to_dict() for x in self.equipment], "connections": [x.to_dict() for x in self.connections], "validation_state": deepcopy(self.validation_state)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, project_id: str, activation_generation: int) -> "DraftNetwork":
        if data.get("project_id", project_id) != project_id: raise ValueError("DraftNetwork project_id does not match project metadata.")
        if data.get("activation_generation") is not None and int(data["activation_generation"]) > activation_generation: raise ValueError("DraftNetwork generation is newer than active project generation.")
        draft = cls(project_id, activation_generation)
        for item in data.get("equipment", ()): draft.add_equipment(DraftEquipment.from_dict(item))
        for item in data.get("connections", ()): draft.add_connection(DraftConnection.from_dict(item))
        draft.validation_state = dict(data.get("validation_state", {})); return draft

    @classmethod
    def empty(cls, project_id: str, activation_generation: int) -> "DraftNetwork": return cls(project_id, activation_generation)


__all__ = ["DraftEndpointReference", "DraftEquipment", "DraftConnection", "DraftNetwork"]
