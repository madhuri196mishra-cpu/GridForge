
"""Persistence-owned migration boundaries for historical GridForge data.

Migration is detached from Application, Core mutation, and Qt/UI state. It
transforms raw persisted mappings into the current canonical representation;
validation and deserialization happen only afterwards.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .project_package import PROJECT_SCHEMA_VERSION, PRESENTATION_SCHEMA_VERSION, SLD_SCHEMA_VERSION, CONNECTIVITY_SERIALIZATION_VERSION


class AmbiguousElectricalDataError(ValueError):
    """Raised when legacy electrical values cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Detached migration output containing only explicitly resolved values."""

    equipment_type: str
    values: Mapping[str, Any]
    migrated: bool


class LegacyElectricalDataMigration:
    """Validate and migrate legacy electrical payloads without guessing units."""

    def migrate(self, equipment_type: str, payload: Mapping[str, Any]) -> MigrationResult:
        if not isinstance(equipment_type, str) or not equipment_type.strip():
            raise ValueError("equipment_type must be a non-empty string.")
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping.")
        normalized_type = equipment_type.strip().lower()
        values = deepcopy(dict(payload))
        if normalized_type == "cable":
            self._validate_cable(values)
        elif normalized_type == "transformer":
            self._validate_transformer(values)
        return MigrationResult(normalized_type, values, True)

    @staticmethod
    def _validate_cable(values: Mapping[str, Any]) -> None:
        legacy = {key: values.get(key) for key in ("r", "x", "b") if key in values}
        if not legacy:
            return
        explicit_units = values.get("impedance_units")
        if explicit_units is None:
            raise AmbiguousElectricalDataError(
                "Legacy cable r/x/b values require explicit impedance_units; "
                "migration will not guess ohms versus per-unit."
            )
        units = str(explicit_units).strip().lower()
        if units not in {"ohm", "ohms", "pu", "per_unit"}:
            raise AmbiguousElectricalDataError(
                f"Unsupported legacy cable impedance_units={explicit_units!r}."
            )

    @staticmethod
    def _validate_transformer(values: Mapping[str, Any]) -> None:
        if "impedance_basis" not in values:
            raise AmbiguousElectricalDataError(
                "Legacy transformer impedance requires an explicit impedance_basis; "
                "migration will not infer the basis."
            )
        basis = str(values["impedance_basis"]).strip().lower()
        if basis not in {
            "ohm_hv", "ohm_lv", "percent_on_rated", "pu_on_rated",
            "pu_on_system", "pu", "engineering",
        }:
            raise AmbiguousElectricalDataError(
                f"Unsupported transformer impedance_basis={values['impedance_basis']!r}."
            )


class ProjectMigrationError(ValueError):
    """Raised when persisted project data cannot be migrated safely."""


class UnsupportedProjectSchemaError(ProjectMigrationError):
    """Raised for project schemas outside the supported migration range."""


class ProjectMigration(ABC):
    """One deterministic, side-effect-free project schema migration step."""

    source_schema: int
    target_schema: int

    @abstractmethod
    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return a detached representation in target_schema."""


class _ProjectSchema1To2(ProjectMigration):
    source_schema = 1
    target_schema = 2

    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema", 1) != self.source_schema:
            raise ProjectMigrationError("Project schema 1-to-2 received a non-schema-1 payload.")
        project = result.get("project")
        if not isinstance(project, Mapping):
            raise ProjectMigrationError("Project metadata is required before migrating schema 1.")
        project_id = project.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectMigrationError(
                "Project schema 1 migration requires a stable project_id for the legacy SLD."
            )

        presentations = result.get("presentations")
        legacy_sld = result.get("sld")
        if presentations is None:
            if legacy_sld is None:
                documents: list[dict[str, Any]] = []
                active_id = None
            elif isinstance(legacy_sld, Mapping):
                document = deepcopy(dict(legacy_sld))
                document_id = document.get("document_id")
                if not isinstance(document_id, str) or not document_id.strip():
                    document_id = f"{project_id}:sld"
                document["document_id"] = document_id
                document.setdefault("document_type", "sld")
                documents = [document]
                active_id = document_id
            else:
                raise ProjectMigrationError("Legacy project sld payload must be an object.")
            result["presentations"] = {
                "schema": PRESENTATION_SCHEMA_VERSION,
                "documents": documents,
                "active_document_id": active_id,
            }
        elif not isinstance(presentations, Mapping):
            raise ProjectMigrationError("Legacy presentations payload must be an object.")

        result.pop("sld", None)
        result["schema"] = self.target_schema
        return result


class _ProjectSchema2To3(ProjectMigration):
    source_schema = 2
    target_schema = 3

    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise ProjectMigrationError("Project schema 2-to-3 received a non-schema-2 payload.")
        result.setdefault("measurement", {"channels": []})
        result.setdefault("dynamic_models", [])
        result["schema"] = self.target_schema
        return result


class _ProjectSchema3To4(ProjectMigration):
    source_schema = 3
    target_schema = 4

    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise ProjectMigrationError("Project schema 3-to-4 received a non-schema-3 payload.")
        result.setdefault("study_cases", [])
        result["schema"] = self.target_schema
        return result


class _ProjectSchema5To6(ProjectMigration):
    source_schema = 5
    target_schema = 6

    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise ProjectMigrationError("Project schema 5-to-6 received a non-schema-5 payload.")
        result.setdefault("instrumentation", None)
        result["schema"] = self.target_schema
        return result

class _ProjectSchema4To5(ProjectMigration):
    source_schema = 4
    target_schema = 5

    def migrate(self, data: Mapping[str, Any]) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise ProjectMigrationError("Project schema 4-to-5 received a non-schema-4 payload.")

        protection = result.get("protection")
        if protection is not None:
            if not isinstance(protection, Mapping):
                raise ProjectMigrationError("Protection configuration must be an object during schema 4-to-5 migration.")
            protection_copy = deepcopy(dict(protection))
            raw_bindings = protection_copy.get("measurement_bindings", [])
            raw_schemes = protection_copy.get("differential_schemes", [])
            if not isinstance(raw_bindings, list):
                raise ProjectMigrationError("Protection measurement_bindings must be an array during schema 4-to-5 migration.")
            if not isinstance(raw_schemes, list):
                raise ProjectMigrationError("Protection differential_schemes must be an array during schema 4-to-5 migration.")

            bindings_by_id: dict[str, Mapping[str, Any]] = {}
            for index, binding in enumerate(raw_bindings):
                if not isinstance(binding, Mapping):
                    raise ProjectMigrationError(
                        f"Protection measurement binding at index {index} is not an object; differential ownership cannot be resolved."
                    )
                binding_id = binding.get("binding_id")
                if not isinstance(binding_id, str) or not binding_id.strip():
                    raise ProjectMigrationError(
                        f"Protection measurement binding at index {index} has no stable binding_id."
                    )
                if binding_id in bindings_by_id:
                    raise ProjectMigrationError(
                        f"Duplicate Protection measurement binding ID {binding_id!r}; differential ownership is ambiguous."
                    )
                bindings_by_id[binding_id] = binding

            migrated_schemes: list[dict[str, Any]] = []
            for index, raw_scheme in enumerate(raw_schemes):
                if not isinstance(raw_scheme, Mapping):
                    raise ProjectMigrationError(
                        f"Differential scheme at index {index} is not an object; ownership cannot be migrated."
                    )
                scheme = deepcopy(dict(raw_scheme))
                scheme_id = scheme.get("scheme_id", f"<index:{index}>")
                member_ids = scheme.get("measurement_binding_ids")
                if not isinstance(member_ids, list) or len(member_ids) < 2:
                    raise ProjectMigrationError(
                        f"Differential scheme {scheme_id!r} must contain at least two explicit measurement_binding_ids."
                    )

                member_owners: set[str] = set()
                for binding_id in member_ids:
                    if not isinstance(binding_id, str) or not binding_id.strip():
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} contains an invalid measurement binding ID."
                        )
                    binding = bindings_by_id.get(binding_id)
                    if binding is None:
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} references missing measurement binding {binding_id!r}."
                        )
                    measurement_type = binding.get("measurement_type")
                    if not isinstance(measurement_type, str) or measurement_type.strip().lower() != "current":
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} member {binding_id!r} is not a current measurement binding."
                        )
                    owner = binding.get("protection_element_id")
                    if not isinstance(owner, str) or not owner.strip():
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} member {binding_id!r} has no Protection element owner."
                        )
                    member_owners.add(owner.strip())

                if len(member_owners) != 1:
                    raise ProjectMigrationError(
                        f"Differential scheme {scheme_id!r} spans multiple Protection element owners; migration will not guess an owner."
                    )
                resolved_owner = next(iter(member_owners))
                persisted_owner = scheme.get("protection_element_id")
                if persisted_owner is not None:
                    if not isinstance(persisted_owner, str) or not persisted_owner.strip():
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} has an invalid persisted protection_element_id."
                        )
                    if persisted_owner.strip() != resolved_owner:
                        raise ProjectMigrationError(
                            f"Differential scheme {scheme_id!r} persisted owner {persisted_owner!r} conflicts with member owner {resolved_owner!r}."
                        )
                scheme["protection_element_id"] = resolved_owner
                migrated_schemes.append(scheme)

            protection_copy["differential_schemes"] = migrated_schemes
            result["protection"] = protection_copy

        result["schema"] = self.target_schema
        return result


class SLDMigrationError(ProjectMigrationError):
    """Raised when an historical SLD representation cannot be migrated."""


class SLDMigration(ABC):
    """One deterministic, side-effect-free SLD schema migration step."""

    source_schema: int
    target_schema: int

    @abstractmethod
    def migrate(
        self,
        data: Mapping[str, Any],
        *,
        project_id: str,
        document_index: int,
        existing_document_ids: set[str],
    ) -> Mapping[str, Any]:
        """Return a detached SLD representation in target_schema."""


class _SLDSchema1To2(SLDMigration):
    source_schema = 1
    target_schema = 2

    def migrate(
        self,
        data: Mapping[str, Any],
        *,
        project_id: str,
        document_index: int,
        existing_document_ids: set[str],
    ) -> Mapping[str, Any]:
        result = deepcopy(dict(data))
        if result.get("schema", 1) != self.source_schema:
            raise SLDMigrationError("SLD schema 1-to-2 received a non-schema-1 payload.")

        document_id = result.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            base = f"{project_id}:sld"
            document_id = base if base not in existing_document_ids else f"{base}:{document_index + 1}"
            while document_id in existing_document_ids:
                document_id = f"{base}:{len(existing_document_ids) + 1}"
        result["document_id"] = document_id
        result.setdefault("document_type", "sld")
        result.setdefault("project_id", project_id)
        result.setdefault("name", "Untitled SLD")
        result.setdefault("metadata", {})

        model = result.get("model", {})
        if not isinstance(model, Mapping):
            raise SLDMigrationError("SLD model must be an object.")
        model = deepcopy(dict(model))
        nodes = model.get("nodes", [])
        connections = model.get("connections", [])
        if not isinstance(nodes, list) or not isinstance(connections, list):
            raise SLDMigrationError("SLD nodes and connections must be arrays.")

        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                raise SLDMigrationError("Every historical SLD node must be an object.")
            item = deepcopy(dict(node))
            item.setdefault("presentation", None)
            item.setdefault("properties", {})
            normalized_nodes.append(item)

        normalized_connections: list[dict[str, Any]] = []
        for connection in connections:
            if not isinstance(connection, Mapping):
                raise SLDMigrationError("Every historical SLD connection must be an object.")
            item = deepcopy(dict(connection))
            item.setdefault("source_endpoint", None)
            item.setdefault("target_endpoint", None)
            item.setdefault("route", {
                "routing_mode": "orthogonal",
                "ownership": "auto",
                "points": [],
            })
            item.setdefault("properties", {})
            normalized_connections.append(item)

        model["nodes"] = normalized_nodes
        model["connections"] = normalized_connections
        result["model"] = model
        result["schema"] = self.target_schema
        return result


class _SLDSchema2To3(SLDMigration):
    source_schema = 2
    target_schema = 3

    def migrate(
        self,
        data: Mapping[str, Any],
        *,
        project_id: str,
        document_index: int,
        existing_document_ids: set[str],
    ) -> Mapping[str, Any]:
        del project_id, document_index, existing_document_ids
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise SLDMigrationError("SLD schema 2-to-3 received a non-schema-2 payload.")
        result.setdefault("annotation_blocks", [])
        result["schema"] = self.target_schema
        return result


class _SLDSchema3To4(SLDMigration):
    source_schema = 3
    target_schema = 4

    def migrate(self, data: Mapping[str, Any], *, project_id: str, document_index: int, existing_document_ids: set[str]) -> Mapping[str, Any]:
        del project_id, document_index, existing_document_ids
        result = deepcopy(dict(data))
        if result.get("schema") != self.source_schema:
            raise SLDMigrationError("SLD schema 3-to-4 received a non-schema-3 payload.")
        if "layers" in result:
            raise SLDMigrationError("SLD schema 3 payload cannot contain a layers registry.")
        result["layers"] = [{"layer_id": "default", "name": "Default", "visible": True, "locked": False, "opacity": 1.0, "order": 0}]
        model = result.get("model")
        if not isinstance(model, Mapping):
            raise SLDMigrationError("SLD model must be an object during schema 3-to-4 migration.")
        model_copy = deepcopy(dict(model))
        for collection_name in ("nodes", "connections"):
            collection = model_copy.get(collection_name)
            if not isinstance(collection, list):
                raise SLDMigrationError("SLD model " + collection_name + " must be an array during schema 3-to-4 migration.")
            for index, item in enumerate(collection):
                if not isinstance(item, Mapping):
                    raise SLDMigrationError("SLD " + collection_name + "[" + str(index) + "] must be an object during schema 3-to-4 migration.")
                if "layer_id" in item:
                    raise SLDMigrationError("SLD schema 3 " + collection_name + " cannot contain layer_id.")
                item_copy = deepcopy(dict(item))
                item_copy["layer_id"] = "default"
                collection[index] = item_copy
        result["model"] = model_copy
        annotations = result.get("annotation_blocks", [])
        if not isinstance(annotations, list):
            raise SLDMigrationError("SLD annotation_blocks must be an array during schema 3-to-4 migration.")
        migrated_annotations = []
        for index, block in enumerate(annotations):
            if not isinstance(block, Mapping):
                raise SLDMigrationError("SLD annotation_blocks[" + str(index) + "] must be an object during schema 3-to-4 migration.")
            if "layer_id" in block:
                raise SLDMigrationError("SLD schema 3 annotation blocks cannot contain layer_id.")
            block_copy = deepcopy(dict(block))
            block_copy["layer_id"] = "default"
            migrated_annotations.append(block_copy)
        result["annotation_blocks"] = migrated_annotations
        result["schema"] = self.target_schema
        return result


SLD_DOCUMENT_FIELDS = frozenset({"schema", "document_id", "project_id", "document_type", "name", "metadata", "model", "annotation_blocks", "layers"})
SLD_MODEL_FIELDS = frozenset({"nodes", "connections"})
SLD_LAYER_FIELDS = frozenset({"layer_id", "name", "visible", "locked", "opacity", "order"})
SLD_NODE_FIELDS = frozenset({"node_id", "equipment_id", "x", "y", "presentation", "properties", "layer_id"})
SLD_CONNECTION_FIELDS = frozenset({"connection_id", "source_node_id", "target_node_id", "source_endpoint", "target_endpoint", "route", "properties", "layer_id"})
SLD_ENDPOINT_FIELDS = frozenset({"kind", "node_id", "equipment_id", "terminal_role", "bus_id", "attachment_id"})
SLD_ROUTE_FIELDS = frozenset({"routing_mode", "ownership", "points"})
SLD_SYMBOL_FIELDS = frozenset({"symbol_id", "definition_id", "symbol_family_id", "standard_id", "variant_id", "standard_reference_id", "representation_id", "scale", "rotation", "visible", "properties"})
SLD_ENDPOINT_KINDS = frozenset({"equipment", "bus"})
SLD_ROUTING_MODES = frozenset({"direct", "orthogonal", "manual"})
SLD_ROUTE_OWNERSHIP = frozenset({"auto", "engineer"})
SLD_ANNOTATION_BLOCK_FIELDS = frozenset({"block_id", "node_id", "visible", "fields", "offset_x", "offset_y", "quadrant", "layer_id"})


def _sld_unknown(value: Mapping[str, Any], allowed: frozenset[str], context: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise SLDMigrationError(
            "Unknown canonical SLD fields in " + context + ": " + ", ".join(unknown)
        )


def _sld_string(value: Any, field: str, context: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise SLDMigrationError("Invalid SLD " + field + " in " + context + ": expected non-empty string.")


def _sld_number(value: Any, field: str, context: str) -> None:
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SLDMigrationError("Invalid SLD " + field + " in " + context + ": expected finite number.")


def validate_canonical_sld(data: Mapping[str, Any], *, project_id: str | None = None) -> None:
    """Validate canonical schema-4 SLD structure without constructing runtime state."""
    if not isinstance(data, Mapping):
        raise SLDMigrationError("SLD document must be an object.")
    _sld_unknown(data, SLD_DOCUMENT_FIELDS, "document")
    if data.get("schema") != SLD_SCHEMA_VERSION:
        raise SLDMigrationError("SLD document must use schema " + str(SLD_SCHEMA_VERSION) + ".")
    _sld_string(data.get("document_id"), "document_id", "document")
    _sld_string(data.get("project_id"), "project_id", "document")
    if project_id is not None and data.get("project_id") != project_id:
        raise SLDMigrationError("SLD document project_id does not match project metadata.")
    if data.get("document_type") != "sld":
        raise SLDMigrationError("SLD document_type must be 'sld'.")
    _sld_string(data.get("name"), "name", "document")
    if not isinstance(data.get("metadata"), Mapping):
        raise SLDMigrationError("SLD document metadata must be an object.")
    layers = data.get("layers")
    if not isinstance(layers, list) or not layers:
        raise SLDMigrationError("SLD layers must be a non-empty array.")
    layer_ids: set[str] = set()
    layer_orders: set[int] = set()
    for index, layer in enumerate(layers):
        context = "layer[" + str(index) + "]"
        if not isinstance(layer, Mapping):
            raise SLDMigrationError("SLD " + context + " must be an object.")
        _sld_unknown(layer, SLD_LAYER_FIELDS, context)
        for field in ("layer_id", "name", "visible", "locked", "opacity", "order"):
            if field not in layer:
                raise SLDMigrationError("Missing SLD layer field " + field + " in " + context + ".")
        _sld_string(layer["layer_id"], "layer_id", context)
        _sld_string(layer["name"], "name", context)
        if layer["layer_id"] in layer_ids:
            raise SLDMigrationError("Duplicate SLD layer_id " + repr(layer["layer_id"]) + ".")
        layer_ids.add(layer["layer_id"])
        if not isinstance(layer["visible"], bool) or not isinstance(layer["locked"], bool):
            raise SLDMigrationError("SLD layer visible and locked fields must be boolean in " + context + ".")
        _sld_number(layer["opacity"], "opacity", context)
        if not 0.0 <= float(layer["opacity"]) <= 1.0:
            raise SLDMigrationError("SLD layer opacity must be between 0 and 1 in " + context + ".")
        order = layer["order"]
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise SLDMigrationError("SLD layer order must be a non-negative integer in " + context + ".")
        if order in layer_orders:
            raise SLDMigrationError("Duplicate SLD layer order " + str(order) + ".")
        layer_orders.add(order)
    if "default" not in layer_ids:
        raise SLDMigrationError("SLD document must contain the default layer.")
    if layer_orders != set(range(len(layers))):
        raise SLDMigrationError("SLD layer order values must be contiguous from zero.")

    model = data.get("model")
    if not isinstance(model, Mapping):
        raise SLDMigrationError("SLD model must be an object.")
    _sld_unknown(model, SLD_MODEL_FIELDS, "model")
    nodes, connections = model.get("nodes"), model.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, list):
        raise SLDMigrationError("SLD model nodes and connections must be arrays.")
    node_ids = set()
    for index, node in enumerate(nodes):
        context = "node[" + str(index) + "]"
        if not isinstance(node, Mapping):
            raise SLDMigrationError("SLD " + context + " must be an object.")
        _sld_unknown(node, SLD_NODE_FIELDS, context)
        for field in ("node_id", "x", "y", "presentation", "properties", "equipment_id", "layer_id"):
            if field not in node:
                raise SLDMigrationError("Missing canonical SLD node field " + field + " in " + context + ".")
        _sld_string(node["node_id"], "node_id", context)
        _sld_string(node["layer_id"], "layer_id", context)
        if node["layer_id"] not in layer_ids:
            raise SLDMigrationError("SLD " + context + " references an unknown presentation layer.")
        if node["node_id"] in node_ids:
            raise SLDMigrationError("Duplicate SLD node_id " + repr(node["node_id"]) + ".")
        node_ids.add(node["node_id"])
        _sld_string(node["equipment_id"], "equipment_id", context, nullable=True)
        _sld_number(node["x"], "x", context); _sld_number(node["y"], "y", context)
        if node["presentation"] is not None:
            presentation = node["presentation"]
            if not isinstance(presentation, Mapping):
                raise SLDMigrationError("SLD " + context + " presentation must be an object or null.")
            _sld_unknown(presentation, SLD_SYMBOL_FIELDS, "symbol presentation " + context)
            for field in ("symbol_id", "definition_id", "representation_id", "scale", "rotation", "visible", "properties"):
                if field not in presentation:
                    raise SLDMigrationError("Missing symbol presentation field " + field + " in " + context + ".")
            _sld_string(presentation["symbol_id"], "symbol_id", context)
            _sld_string(presentation["definition_id"], "definition_id", context)
            # Variant identity fields are additive and optional for legacy
            # schema-3 payloads. SymbolBase supplies stable legacy defaults.
            for field in ("symbol_family_id", "standard_id", "variant_id", "standard_reference_id"):
                if field in presentation:
                    _sld_string(presentation[field], field, context)
            _sld_string(presentation["representation_id"], "representation_id", context)
            _sld_number(presentation["scale"], "scale", context)
            if float(presentation["scale"]) <= 0:
                raise SLDMigrationError("Symbol scale must be greater than zero in " + context + ".")
            _sld_number(presentation["rotation"], "rotation", context)
            if not isinstance(presentation["visible"], bool):
                raise SLDMigrationError("Symbol visible must be boolean in " + context + ".")
            if not isinstance(presentation["properties"], Mapping):
                raise SLDMigrationError("Symbol properties must be an object in " + context + ".")
        if not isinstance(node["properties"], Mapping):
            raise SLDMigrationError("SLD node properties must be an object in " + context + ".")
    annotation_blocks = data.get("annotation_blocks")
    if not isinstance(annotation_blocks, list):
        raise SLDMigrationError("SLD annotation_blocks must be an array.")
    block_ids = set()
    for index, block in enumerate(annotation_blocks):
        context = "annotation_block[" + str(index) + "]"
        if not isinstance(block, Mapping):
            raise SLDMigrationError("SLD " + context + " must be an object.")
        _sld_unknown(block, SLD_ANNOTATION_BLOCK_FIELDS, context)
        for field in ("block_id", "node_id", "visible", "fields", "offset_x", "offset_y", "layer_id"):
            if field not in block:
                raise SLDMigrationError("Missing annotation block field " + field + " in " + context + ".")
        _sld_string(block["block_id"], "block_id", context)
        if block["block_id"] in block_ids:
            raise SLDMigrationError("Duplicate SLD annotation block ID " + repr(block["block_id"]) + ".")
        block_ids.add(block["block_id"])
        _sld_string(block["node_id"], "node_id", context)
        _sld_string(block["layer_id"], "layer_id", context)
        if block["layer_id"] not in layer_ids:
            raise SLDMigrationError("SLD " + context + " references an unknown presentation layer.")
        if block["node_id"] not in node_ids:
            raise SLDMigrationError("SLD " + context + " references a missing node.")
        if not isinstance(block["visible"], bool):
            raise SLDMigrationError("SLD " + context + " visible must be boolean.")
        fields = block["fields"]
        if not isinstance(fields, list) or not fields:
            raise SLDMigrationError("SLD " + context + " fields must be a non-empty array.")
        seen_fields = set()
        for field in fields:
            _sld_string(field, "field", context)
            if field not in {"identity", "type"} and not (
                field.startswith("label:") and field[6:].strip()
                or field.startswith("attribute:") and field[10:].strip()
                or field.startswith("parameter:") and field[10:].strip()
            ):
                raise SLDMigrationError("Unsupported annotation field " + repr(field) + " in " + context + ".")
            if field in seen_fields:
                raise SLDMigrationError("Duplicate annotation field " + repr(field) + " in " + context + ".")
            seen_fields.add(field)
        _sld_number(block["offset_x"], "offset_x", context)
        _sld_number(block["offset_y"], "offset_y", context)
        if "quadrant" in block:
            _sld_string(block["quadrant"], "quadrant", context)
            if block["quadrant"] not in {"top_right", "top_left", "bottom_right", "bottom_left", "custom"}:
                raise SLDMigrationError("Unsupported annotation quadrant " + repr(block["quadrant"]) + " in " + context + ".")
    connection_ids = set()
    for index, connection in enumerate(connections):
        context = "connection[" + str(index) + "]"
        if not isinstance(connection, Mapping):
            raise SLDMigrationError("SLD " + context + " must be an object.")
        _sld_unknown(connection, SLD_CONNECTION_FIELDS, context)
        for field in ("connection_id", "source_node_id", "target_node_id", "source_endpoint", "target_endpoint", "route", "properties", "layer_id"):
            if field not in connection:
                raise SLDMigrationError("Missing canonical SLD connection field " + field + " in " + context + ".")
        _sld_string(connection["connection_id"], "connection_id", context)
        _sld_string(connection["layer_id"], "layer_id", context)
        if connection["layer_id"] not in layer_ids:
            raise SLDMigrationError("SLD " + context + " references an unknown presentation layer.")
        if connection["connection_id"] in connection_ids:
            raise SLDMigrationError("Duplicate SLD connection_id " + repr(connection["connection_id"]) + ".")
        connection_ids.add(connection["connection_id"])
        source, target = connection["source_node_id"], connection["target_node_id"]
        _sld_string(source, "source_node_id", context); _sld_string(target, "target_node_id", context)
        if not isinstance(connection["properties"], Mapping):
            raise SLDMigrationError("SLD connection properties must be an object in " + context + ".")
        orphaned_detached = (
            str(connection["properties"].get("lifecycle_state", "BOUND")).strip().upper() == "ORPHANED"
            and connection["source_endpoint"] is None
            and connection["target_endpoint"] is None
        )
        if (source not in node_ids or target not in node_ids) and not orphaned_detached:
            raise SLDMigrationError("SLD " + context + " references a missing node.")
        for side, expected_node in (("source_endpoint", source), ("target_endpoint", target)):
            endpoint = connection[side]
            if endpoint is None:
                continue
            if not isinstance(endpoint, Mapping):
                raise SLDMigrationError("SLD " + context + " " + side + " must be an object or null.")
            _sld_unknown(endpoint, SLD_ENDPOINT_FIELDS, context + " " + side)
            for field in ("kind", "node_id", "equipment_id", "terminal_role", "bus_id", "attachment_id"):
                if field not in endpoint:
                    raise SLDMigrationError("Missing endpoint field " + field + " in " + context + ".")
            if endpoint["kind"] not in SLD_ENDPOINT_KINDS:
                raise SLDMigrationError("Invalid endpoint kind in " + context + ".")
            _sld_string(endpoint["node_id"], "endpoint.node_id", context)
            if endpoint["node_id"] != expected_node:
                raise SLDMigrationError("Endpoint node_id does not match connection node in " + context + ".")
            if endpoint["kind"] == "equipment":
                _sld_string(endpoint["equipment_id"], "endpoint.equipment_id", context)
                _sld_string(endpoint["terminal_role"], "endpoint.terminal_role", context)
                if endpoint["bus_id"] is not None or endpoint["attachment_id"] is not None:
                    raise SLDMigrationError("Equipment endpoint contains bus identity in " + context + ".")
            else:
                _sld_string(endpoint["bus_id"], "endpoint.bus_id", context)
                _sld_string(endpoint["attachment_id"], "endpoint.attachment_id", context)
                if endpoint["equipment_id"] is not None or endpoint["terminal_role"] is not None:
                    raise SLDMigrationError("Bus endpoint contains equipment identity in " + context + ".")
        route = connection["route"]
        if not isinstance(route, Mapping):
            raise SLDMigrationError("SLD route must be an object in " + context + ".")
        _sld_unknown(route, SLD_ROUTE_FIELDS, context + " route")
        if route.get("routing_mode") not in SLD_ROUTING_MODES:
            raise SLDMigrationError("Invalid routing_mode in " + context + ".")
        if route.get("ownership") not in SLD_ROUTE_OWNERSHIP:
            raise SLDMigrationError("Invalid route ownership in " + context + ".")
        points = route.get("points")
        if not isinstance(points, list):
            raise SLDMigrationError("SLD route points must be an array in " + context + ".")
        for point_index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise SLDMigrationError("Malformed route point " + str(point_index) + " in " + context + ".")
            _sld_number(point[0], "route point x", context)
            _sld_number(point[1], "route point y", context)


class PresentationCollectionMigrationError(ProjectMigrationError):
    """Raised when the persisted presentation collection cannot be migrated."""


class ProjectPersistenceMigration:
    """Canonical migration pipeline for project/package persistence.

    Ordering is fixed: project schema, presentation collection, individual SLD.
    """

    _project_steps: dict[int, ProjectMigration] = {
        1: _ProjectSchema1To2(),
        2: _ProjectSchema2To3(),
        3: _ProjectSchema3To4(),
        4: _ProjectSchema4To5(),
        5: _ProjectSchema5To6(),
    }
    _sld_steps: dict[int, SLDMigration] = {1: _SLDSchema1To2(), 2: _SLDSchema2To3(), 3: _SLDSchema3To4()}

    def migrate_project(self, data: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise TypeError("Raw project payload must be a mapping.")

        current = deepcopy(dict(data))
        schema = current.get("schema", 1)
        if isinstance(schema, bool) or not isinstance(schema, int):
            raise UnsupportedProjectSchemaError(f"Project schema must be an integer, got {schema!r}.")
        if schema > PROJECT_SCHEMA_VERSION:
            raise UnsupportedProjectSchemaError(
                f"Unsupported future project schema {schema}; supported schema is {PROJECT_SCHEMA_VERSION}."
            )
        if schema < 1:
            raise UnsupportedProjectSchemaError(f"Unsupported project schema {schema}.")

        while schema < PROJECT_SCHEMA_VERSION:
            migration = self._project_steps.get(schema)
            if migration is None or migration.target_schema != schema + 1:
                raise UnsupportedProjectSchemaError(
                    f"No migration path from project schema {schema} to {PROJECT_SCHEMA_VERSION}."
                )
            current = deepcopy(dict(migration.migrate(current)))
            schema = migration.target_schema

        # Normalize required canonical containers even when an older file
        # already advertises the current project schema.
        current.setdefault("dynamic_models", [])
        current.setdefault("measurement", {"channels": []})
        current.setdefault("study_cases", [])
        current.setdefault("instrumentation", None)
        current = self._migrate_presentation_collection(current)
        current = self._migrate_network_connectivity(current)
        self._reject_legacy_project_aliases(current)
        return current

    @staticmethod
    def _migrate_network_connectivity(project: dict[str, Any]) -> dict[str, Any]:
        """Normalize legacy Core connectivity schemas to the current canonical schema."""
        network = project.get("network")
        if not isinstance(network, Mapping):
            raise ProjectMigrationError("Project network payload must be an object.")
        connectivity = network.get("connectivity")
        if connectivity is None:
            connectivity = {"schema": 1, "simple_wires": []}
        if not isinstance(connectivity, Mapping):
            raise ProjectMigrationError("Project connectivity payload must be an object.")
        schema = connectivity.get("schema", 1)
        if isinstance(schema, bool) or not isinstance(schema, int):
            raise ProjectMigrationError(f"Connectivity schema must be an integer, got {schema!r}.")
        if schema > CONNECTIVITY_SERIALIZATION_VERSION:
            raise ProjectMigrationError(
                f"Unsupported future connectivity schema {schema}; supported schema is {CONNECTIVITY_SERIALIZATION_VERSION}."
            )
        if schema < 1:
            raise ProjectMigrationError(f"Unsupported connectivity schema {schema}.")
        if schema == 1:
            if "junctions" in connectivity:
                raise ProjectMigrationError("Connectivity schema 1 cannot contain junctions.")
            wires = connectivity.get("simple_wires", [])
            if not isinstance(wires, list):
                raise ProjectMigrationError("Legacy connectivity.simple_wires must be an array.")
            migrated = deepcopy(dict(connectivity))
            migrated["schema"] = CONNECTIVITY_SERIALIZATION_VERSION
            migrated["junctions"] = []
            migrated["phase_domain_nodes"] = []
            migrated["phase_domain_connections"] = []
            network_copy = deepcopy(dict(network))
            network_copy["connectivity"] = migrated
            project["network"] = network_copy
            return project
        if schema == 2:
            migrated = deepcopy(dict(connectivity))
            migrated["schema"] = CONNECTIVITY_SERIALIZATION_VERSION
            migrated["phase_domain_nodes"] = []
            migrated["phase_domain_connections"] = []
            network_copy = deepcopy(dict(network))
            network_copy["connectivity"] = migrated
            project["network"] = network_copy
            return project
        if schema == 3:
            migrated = deepcopy(dict(connectivity))
            if not isinstance(migrated.get("junctions", []), list):
                raise ProjectMigrationError("Connectivity schema 3 junctions must be an array.")
            if not isinstance(migrated.get("simple_wires", []), list):
                raise ProjectMigrationError("Connectivity schema 3 simple_wires must be an array.")
            if not isinstance(migrated.get("phase_domain_nodes"), list) or not isinstance(migrated.get("phase_domain_connections"), list):
                raise ProjectMigrationError("Connectivity schema 3 phase-domain graph collections must be arrays.")
            migrated["schema"] = CONNECTIVITY_SERIALIZATION_VERSION
            network_copy = deepcopy(dict(network))
            network_copy["connectivity"] = migrated
            project["network"] = network_copy
            return project
        if schema == CONNECTIVITY_SERIALIZATION_VERSION:
            if not isinstance(connectivity.get("junctions", []), list):
                raise ProjectMigrationError("Canonical connectivity.junctions must be an array.")
            if not isinstance(connectivity.get("simple_wires", []), list):
                raise ProjectMigrationError("Canonical connectivity.simple_wires must be an array.")
            if not isinstance(connectivity.get("phase_domain_nodes"), list) or not isinstance(connectivity.get("phase_domain_connections"), list):
                raise ProjectMigrationError("Canonical phase-domain conductor graph collections must be arrays.")
            return project
        raise ProjectMigrationError(f"Unsupported connectivity schema {schema!r}.")

    def _migrate_presentation_collection(self, project: dict[str, Any]) -> dict[str, Any]:
        project_data = project.get("project")
        if not isinstance(project_data, Mapping):
            raise ProjectMigrationError("Project metadata must be an object.")
        project_id = project_data.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectMigrationError("Project migration requires a stable project_id.")

        raw = project.get("presentations")
        legacy_sld = project.get("sld")
        legacy_presentation = project.get("presentation")
        if legacy_sld is not None or legacy_presentation is not None:
            if legacy_sld is not None and legacy_presentation is not None:
                raise PresentationCollectionMigrationError(
                    "Legacy project contains both sld and presentation aliases; migration is ambiguous."
                )
            if raw is not None:
                raise PresentationCollectionMigrationError(
                    "Project contains both canonical presentations and a legacy single-SLD alias."
                )
            raw_legacy = legacy_sld if legacy_sld is not None else legacy_presentation
            if not isinstance(raw_legacy, Mapping):
                raise PresentationCollectionMigrationError("Legacy single-SLD payload must be an object.")
            raw = {
                "schema": PRESENTATION_SCHEMA_VERSION,
                "documents": [deepcopy(dict(raw_legacy))],
                "active_document_id": raw_legacy.get("document_id"),
            }
            project.pop("sld", None)
            project.pop("presentation", None)

        if raw is None:
            project["presentations"] = {
                "schema": PRESENTATION_SCHEMA_VERSION,
                "documents": [],
                "active_document_id": None,
            }
            return project
        if not isinstance(raw, Mapping):
            raise PresentationCollectionMigrationError("Presentations payload must be an object.")

        collection_schema = raw.get("schema")
        if collection_schema != PRESENTATION_SCHEMA_VERSION:
            raise PresentationCollectionMigrationError(
                f"Unsupported presentation collection schema {collection_schema!r}; "
                f"supported schema is {PRESENTATION_SCHEMA_VERSION}."
            )
        raw_documents = raw.get("documents", [])
        if not isinstance(raw_documents, list):
            raise PresentationCollectionMigrationError("Presentation documents must be an array.")

        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw_document in enumerate(raw_documents):
            if not isinstance(raw_document, Mapping):
                raise PresentationCollectionMigrationError("Every presentation document must be an object.")
            migrated = self._migrate_sld(
                raw_document,
                project_id=project_id,
                document_index=index,
                existing_document_ids=seen,
            )
            document_id = migrated.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip():
                raise PresentationCollectionMigrationError("Migrated SLD document has no stable document_id.")
            if document_id in seen:
                raise PresentationCollectionMigrationError(f"Duplicate persisted document_id: {document_id}.")
            seen.add(document_id)
            documents.append(migrated)

        active_id = raw.get("active_document_id")
        if active_id is not None and not isinstance(active_id, str):
            raise PresentationCollectionMigrationError("active_document_id must be a string or null.")
        if active_id is not None and active_id not in seen:
            raise PresentationCollectionMigrationError(
                f"active_document_id {active_id!r} does not reference a persisted SLD document."
            )

        project["presentations"] = {
            "schema": PRESENTATION_SCHEMA_VERSION,
            "documents": documents,
            "active_document_id": active_id,
        }
        return project

    def _migrate_sld(
        self,
        data: Mapping[str, Any],
        *,
        project_id: str,
        document_index: int,
        existing_document_ids: set[str],
    ) -> dict[str, Any]:
        current = deepcopy(dict(data))
        schema = current.get("schema", 1)
        if isinstance(schema, bool) or not isinstance(schema, int):
            raise SLDMigrationError(f"SLD schema must be an integer, got {schema!r}.")
        if schema > SLD_SCHEMA_VERSION:
            raise SLDMigrationError(
                f"Unsupported future SLD schema {schema}; supported schema is {SLD_SCHEMA_VERSION}."
            )
        if schema < 1:
            raise SLDMigrationError(f"Unsupported SLD schema {schema}.")

        while schema < SLD_SCHEMA_VERSION:
            migration = self._sld_steps.get(schema)
            if migration is None or migration.target_schema != schema + 1:
                raise SLDMigrationError(
                    f"No SLD migration path from schema {schema} to {SLD_SCHEMA_VERSION}."
                )
            current = deepcopy(dict(migration.migrate(
                current,
                project_id=project_id,
                document_index=document_index,
                existing_document_ids=existing_document_ids,
            )))
            schema = migration.target_schema
        validate_canonical_sld(current, project_id=project_id)
        return current

    @staticmethod
    def _reject_legacy_project_aliases(project: Mapping[str, Any]) -> None:
        if "sld" in project:
            raise ProjectMigrationError("Legacy sld field survived project migration.")
        if "presentation" in project:
            raise ProjectMigrationError("Legacy presentation field survived project migration.")


__all__ = [
    "AmbiguousElectricalDataError",
    "LegacyElectricalDataMigration",
    "MigrationResult",
    "ProjectMigration",
    "ProjectMigrationError",
    "ProjectPersistenceMigration",
    "PresentationCollectionMigrationError",
    "SLDMigration",
    "SLDMigrationError",
    "validate_canonical_sld",
    "UnsupportedProjectSchemaError",
]
