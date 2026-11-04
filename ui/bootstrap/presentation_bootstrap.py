# ============================================================
# File: ui/bootstrap/presentation_bootstrap.py
# GridForge V2 — Presentation Bootstrap Boundary
# Author: Subhendu Mishra
# ============================================================
"""Compose shared presentation registries and read-only SLD boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ui.canvas.semantic_presentation_realization import SemanticPresentationRealization
from ui.canvas.sld_graphics_item_factory import SLDGraphicsItemFactory
from ui.equipment.equipment_registry import EquipmentRegistry
from ui.equipment.symbol.built_in_symbol_catalogue import register_builtin_symbols
from ui.equipment.symbol.symbol_factory import SymbolFactory
from ui.equipment.symbol.symbol_registry import SymbolRegistry
from ui.sld.sld_read_synchronizer import SLDReadSynchronizer
from ui.sld.sld_equipment_identity import equipment_type_for_semantic
from ui.workspace.workspace_manager import WorkspaceManager


@dataclass
class PresentationBootstrap:
    """Own one coherent presentation composition graph."""

    workspace_manager: WorkspaceManager
    application: Any = None
    sld_read_synchronizer: SLDReadSynchronizer | None = None
    shell: Any = None
    equipment_registry: EquipmentRegistry = field(default_factory=EquipmentRegistry.create_default)
    symbol_registry: SymbolRegistry = field(default_factory=SymbolRegistry)
    symbol_factory: SymbolFactory | None = None
    semantic_realization: SemanticPresentationRealization | None = None
    sld_graphics_item_factory: SLDGraphicsItemFactory | None = None

    def __post_init__(self) -> None:
        if not self.symbol_registry.symbol_ids():
            register_builtin_symbols(self.symbol_registry)
        if self.application is not None:
            self._validate_terminal_contract(self.application)
        self.symbol_factory = self.symbol_factory or SymbolFactory(self.symbol_registry)
        self.semantic_realization = self.semantic_realization or SemanticPresentationRealization(
            self.equipment_registry, self.symbol_registry
        )
        self.sld_graphics_item_factory = self.sld_graphics_item_factory or SLDGraphicsItemFactory(
            self.equipment_registry, self.symbol_registry
        )

    @classmethod
    def create(cls, workspace_manager: WorkspaceManager | None = None, application: Any = None,
               sld_read_synchronizer: SLDReadSynchronizer | None = None) -> "PresentationBootstrap":
        if application is None:
            raise TypeError("PresentationBootstrap.create() requires the Application facade for terminal-contract validation.")
        bootstrap = cls(workspace_manager=workspace_manager or WorkspaceManager(), application=application,
                        sld_read_synchronizer=sld_read_synchronizer)
        bootstrap._synchronize_application_boundary()
        return bootstrap

    def attach_application(self, application: Any) -> None:
        if application is None:
            raise TypeError("application must not be None")
        self._validate_terminal_contract(application)
        self.application = application
        self._synchronize_application_boundary()

    def detach_application(self) -> Any:
        application = self.application
        self.application = None
        self._synchronize_application_boundary()
        return application

    def attach_sld_read_synchronizer(self, synchronizer: SLDReadSynchronizer) -> None:
        if not isinstance(synchronizer, SLDReadSynchronizer):
            raise TypeError("synchronizer must be an SLDReadSynchronizer")
        self.sld_read_synchronizer = synchronizer
        self._synchronize_application_boundary()

    def detach_sld_read_synchronizer(self) -> SLDReadSynchronizer | None:
        synchronizer = self.sld_read_synchronizer
        if synchronizer is not None:
            synchronizer.detach_application()
        self.sld_read_synchronizer = None
        return synchronizer

    def require_application(self) -> Any:
        if self.application is None:
            raise RuntimeError("Presentation Application facade is not configured")
        return self.application

    def default_symbol_presentation(self, identifier: str) -> dict[str, Any]:
        """Resolve an equipment semantic or identity to the canonical default symbol state."""
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("symbol presentation identifier must be a non-empty string.")

        identifier = identifier.strip()
        equipment_type: str | None = None

        if self.equipment_registry.contains(identifier):
            equipment_type = identifier
        else:
            try:
                semantic_equipment_type = equipment_type_for_semantic(identifier)
            except (TypeError, ValueError):
                semantic_equipment_type = ""
            if semantic_equipment_type and self.equipment_registry.contains(semantic_equipment_type):
                equipment_type = semantic_equipment_type

        if equipment_type is None:
            application = self.application
            if application is None:
                raise KeyError(f"Unknown equipment presentation identity: {identifier!r}")
            read_network = application.read_network()
            matches = [
                element
                for element in read_network.elements
                if element.object_id == identifier
            ]
            if not matches:
                protection_reader = getattr(application, "read_protection", None)
                if callable(protection_reader):
                    protection = protection_reader()
                    matches = [
                        element
                        for element in protection.elements
                        if element.object_id == identifier
                    ]
            if not matches:
                raise KeyError(f"Unknown equipment presentation identity: {identifier!r}")
            equipment_type = equipment_type_for_semantic(matches[0].element_type)

        definition = self.equipment_registry.require(equipment_type)
        if self.symbol_factory is None:
            raise RuntimeError("SymbolFactory is not configured")
        return self.symbol_factory.create(
            definition.symbol_id,
            representation_id="symbol",
        ).to_dict()

    def attach_shell(self, shell: Any) -> None:
        self.shell = shell

    def detach_shell(self) -> Any:
        shell = self.shell
        self.shell = None
        return shell

    def _validate_terminal_contract(self, application: Any) -> None:
        """Validate presentation terminal semantics against the immutable Application contract."""
        if application is None:
            raise TypeError("application must not be None")
        roles = getattr(application, "terminal_roles_by_type", None)
        if roles is None:
            raise TypeError("Application must expose terminal_roles_by_type before presentation composition.")
        if not hasattr(roles, "items"):
            raise TypeError("Application terminal_roles_by_type must be a mapping.")
        self.equipment_registry.validate_terminal_contracts(
            self.symbol_registry,
            core_roles_by_type=roles,
        )
        creation_errors = self.equipment_registry.validate_creation_contracts()
        if creation_errors:
            raise ValueError("Creation contract mismatch: " + " ".join(creation_errors))

        # The validated catalogue is the single schema source. Pass a plain,
        # framework-neutral snapshot into Application; commit handlers resolve
        # from that trusted snapshot, never from mutable DraftEquipment metadata.
        contracts = {}
        for equipment in self.equipment_registry.catalogue():
            definition = equipment.creation_definition
            if definition is None:
                raise ValueError(
                    f"{equipment.equipment_type}: canonical CreationDefinition is missing."
                )
            contracts[definition.equipment_type] = {
                "equipment_type": definition.equipment_type,
                "terminal_contract": tuple(equipment.terminal_names),
                "terminal_requirements": tuple({
                    "terminal_name": item.terminal_name,
                    "required": item.required,
                    "cardinality": item.cardinality,
                    "allowed_connection_types": tuple(item.allowed_connection_types),
                    "acquisition_state": item.acquisition_state,
                    "initial_endpoint_required": item.initial_endpoint_required,
                } for item in definition.terminal_requirements),
                "command_type": definition.command_type,
                "id_field": definition.id_field,
                "parameter_mapping": dict(definition.parameter_mapping),
                "endpoint_mapping": dict(definition.endpoint_mapping),
                "required_connection_terminals": tuple(
                    requirement.name
                    for requirement in definition.topology_requirements
                    if requirement.required
                ),
                "parameter_contract": tuple({
                    "parameter_id": item.parameter_id,
                    "display_name": item.display_name,
                    "datatype": item.datatype,
                    "unit": item.unit,
                    "required_before_create": item.required_before_create,
                    "default_value": item.default_value,
                    "editable": item.editable,
                    "derived": item.derived,
                    "choices": tuple(item.choices),
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "validation": dict(item.validation),
                } for item in definition.parameter_definitions),
                "conditional_requirements": tuple({
                    "label": item.label,
                    "require_any": tuple(item.require_any),
                    "require_all": tuple(item.require_all),
                } for item in definition.conditional_requirements),
            }
        configure_creation_contracts = getattr(application, "configure_creation_contracts", None)
        if not callable(configure_creation_contracts):
            raise TypeError(
                "Application must expose configure_creation_contracts() before presentation composition."
            )
        configure_creation_contracts(contracts)

    def _synchronize_application_boundary(self) -> None:
        if self.sld_read_synchronizer is None:
            return
        if self.application is None:
            self.sld_read_synchronizer.detach_application()
        else:
            self.sld_read_synchronizer.attach_application(self.application)


__all__ = ["PresentationBootstrap"]
