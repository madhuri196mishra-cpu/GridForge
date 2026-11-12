# ============================================================
# File: core/persistence/project_persistence.py
# GridForge V2 — Project Persistence Service
# Author: Subhendu Mishra
# ============================================================

"""Canonical ``.gridforge`` project loader/saver."""

from __future__ import annotations
# Author: Subhendu Mishra

import json
import os
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.analysis.dynamic_model_association import DynamicMachineModelAssociation
from core.application.project import ProjectContext
from core.application.instrumentation import ProjectInstrumentationCollection, validate_instrumentation_targets
from core.application.revision import ProjectRevision
from core.application.study import StudyCaseDefinition
from core.analysis.power_flow_configuration import PowerFlowStudyConfiguration
from core.analysis.short_circuit_configuration import ShortCircuitStudyConfiguration
from core.analysis.transient_stability import TransientStabilityStudyConfiguration
from core.solver.short_circuit.fault_types import FaultType
from core.application.control_signal_mapping import ControlSignalMapping
from core.application.draft import DraftNetwork, DraftNetworkCollection
from core.control.configuration import ControlConfiguration
from core.network import Network
from core.protection.project_configuration import ProtectionProjectConfiguration, ProtectionProjectConfigurationSnapshot
from core.application.services.validation_service import ValidationService, ValidationSeverity

from .network_serializer import deserialize_network, serialize_network
from .migration import ProjectPersistenceMigration, ProjectMigrationError, SLDMigrationError, validate_canonical_sld
from .project_package import (
    MANIFEST_NAME,
    PACKAGE_VERSION,
    PROJECT_SCHEMA_VERSION,
    PRESENTATION_SCHEMA_VERSION,
    SLD_SCHEMA_VERSION,
    CORE_SERIALIZATION_VERSION,
    CONNECTIVITY_SERIALIZATION_VERSION,
    manifest_path,
    normalize_package_path,
    project_path,
)


@dataclass(frozen=True)
class LoadedProject:
    """Explicit in-memory representation of a loaded project package."""
    context: ProjectContext
    network: Network
    presentation: Mapping[str, Any] | None = None
    presentation_collection: Mapping[str, Any] | None = None
    dynamic_models: tuple[DynamicMachineModelAssociation, ...] = ()
    protection_configuration: ProtectionProjectConfiguration | None = None
    measurement_definitions: tuple[Mapping[str, Any], ...] = ()
    control_configuration: ControlConfiguration | None = None
    draft_network: DraftNetwork | None = None
    draft_network_collection: DraftNetworkCollection | None = None
    protection_presentation: Mapping[str, Any] | None = None
    control_signal_mapping: ControlSignalMapping | None = None
    study_cases: tuple[StudyCaseDefinition, ...] = ()
    instrumentation: ProjectInstrumentationCollection | None = None


class ProjectPersistenceError(RuntimeError):
    """Raised when a project package is invalid or cannot be persisted."""


class ProjectPersistenceService:
    """Load and save the canonical GridForge engineering project package.

    The temporary-directory replacement strategy writes a complete candidate
    package before installation. An interrupted directory replacement leaves
    the previous package in a recoverable sibling backup; load-time recovery
    restores that checkpoint before parsing. File contents are fsync'd before
    replacement. Directory-entry durability remains platform-specific.
    """

    def __init__(self, migration: ProjectPersistenceMigration | None = None) -> None:
        self._migration = migration or ProjectPersistenceMigration()

    def load(self, path: str | Path) -> LoadedProject:
        package = normalize_package_path(path)
        self._recover_interrupted_save(package)
        if not package.is_dir():
            raise ProjectPersistenceError(f"Project package does not exist: {package}")
        manifest = self._read_json(manifest_path(package))
        package_version = manifest.get("package_version")
        if package_version != PACKAGE_VERSION:
            raise ProjectPersistenceError(
                f"Unsupported GridForge package version: {package_version!r}; "
                f"supported package version is {PACKAGE_VERSION}."
            )
        if manifest.get("format") != "GridForgeProject":
            raise ProjectPersistenceError("Invalid GridForge project manifest.")

        raw_project = self._read_json(project_path(package))
        try:
            project = self._migration.migrate_project(raw_project)
            self._validate_canonical_project_data(project)
        except (ProjectMigrationError, SLDMigrationError, TypeError, ValueError) as exc:
            raise ProjectPersistenceError(f"Project migration/validation failed: {exc}") from exc

        context_data = project["project"]
        project_id, name = context_data["project_id"], context_data["name"]
        try:
            network = deserialize_network(project["network"])
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid persisted Network: {exc}") from exc

        presentation_collection = self._normalize_presentation_collection(
            project["presentations"], project_id
        )
        active_id = presentation_collection["active_document_id"]
        documents = presentation_collection["documents"]
        presentation = next(
            (item for item in documents if item["document_id"] == active_id),
            documents[0] if documents else None,
        )
        try:
            dynamic_models = tuple(
                DynamicMachineModelAssociation.from_dict(item, project_id=project_id)
                for item in project["dynamic_models"]
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid dynamic machine model association: {exc}") from exc

        measurement_definitions = project["measurement"]["channels"]
        instrumentation_data = project.get("instrumentation")
        try:
            instrumentation = (
                ProjectInstrumentationCollection.from_mapping(instrumentation_data, project_id=project_id)
                if instrumentation_data is not None
                else ProjectInstrumentationCollection(project_id=project_id)
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid project instrumentation collection: {exc}") from exc
        document_ids = tuple(
            str(item.get("document_id", "")).strip()
            for item in presentation_collection.get("documents", ())
            if isinstance(item, Mapping) and item.get("document_id")
        )
        default_document_id = f"{project_id}:sld"
        if not document_ids:
            document_ids = (default_document_id,)
        active_document_id = presentation_collection.get("active_document_id") or document_ids[0]
        draft_collection_data = project.get("draft_networks")
        legacy_draft_data = project.get("draft_network")
        try:
            if draft_collection_data is not None and legacy_draft_data is not None:
                raise ValueError("Project cannot contain both draft_networks and legacy draft_network.")
            draft_network_collection = DraftNetworkCollection.from_dict(
                draft_collection_data if draft_collection_data is not None else legacy_draft_data,
                project_id=project_id,
                activation_generation=1,
                document_ids=document_ids,
                legacy_document_id=active_document_id,
            )
            active_draft = draft_network_collection.get(active_document_id)
            draft_network = active_draft or DraftNetwork.empty(project_id, 1)
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid persisted DraftNetworkCollection: {exc}") from exc

        control_data = project.get("control")
        try:
            control_configuration = (
                ControlConfiguration.from_dict(control_data)
                if control_data is not None else ControlConfiguration.empty(project_id)
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid Control configuration: {exc}") from exc
        if control_configuration.project_id != project_id:
            raise ProjectPersistenceError("Control configuration project_id does not match project metadata.")
        control_configuration.validate()

        mapping_data = project.get("control_signal_mapping")
        try:
            control_signal_mapping = (
                ControlSignalMapping.from_dict(mapping_data)
                if mapping_data is not None else None
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProjectPersistenceError(f"Invalid Control signal mapping: {exc}") from exc

        protection_data = project.get("protection")
        protection_presentation = project.get("protection_presentation")
        protection_configuration = None
        if protection_data is not None:
            try:
                protection_configuration = ProtectionProjectConfiguration.from_dict(protection_data)
            except (TypeError, ValueError, KeyError) as exc:
                raise ProjectPersistenceError(f"Invalid protection configuration: {exc}") from exc

        try:
            study_cases = tuple(
                self._deserialize_study_case(item, project_id=project_id, network=network)
                for item in project.get("study_cases", [])
            )
        except (TypeError, ValueError, KeyError, ProjectPersistenceError) as exc:
            raise ProjectPersistenceError(f"Invalid persisted Study Case definition: {exc}") from exc
        if len({case.study_id for case in study_cases}) != len(study_cases):
            raise ProjectPersistenceError("Persisted Study Cases contain duplicate study identities.")

        context = ProjectContext(project_id=project_id, name=name, path=package)
        self._validate_project_state(
            context, network, presentation, presentation_collection,
            dynamic_models, protection_configuration, control_configuration,
            measurement_definitions, instrumentation,
        )
        return LoadedProject(
            context=context,
            network=network,
            presentation=presentation,
            presentation_collection=presentation_collection,
            dynamic_models=dynamic_models,
            protection_configuration=protection_configuration,
            measurement_definitions=tuple(dict(item) for item in measurement_definitions),
            control_configuration=control_configuration,
            control_signal_mapping=control_signal_mapping,
            draft_network=draft_network,
            draft_network_collection=draft_network_collection,
            protection_presentation=protection_presentation,
            study_cases=study_cases,
            instrumentation=instrumentation,
        )

    def save(self, context: ProjectContext, network: Network,
             presentation: Mapping[str, Any] | str | Path | None = None,
             path: str | Path | None = None,
             *, dynamic_models: Sequence[DynamicMachineModelAssociation] = (),
             protection_configuration: ProtectionProjectConfigurationSnapshot | None = None,
             measurement_definitions: Sequence[Mapping[str, Any]] = (),
             control_configuration: ControlConfiguration | None = None,
             control_signal_mapping: ControlSignalMapping | None = None,
             draft_network: DraftNetwork | None = None,
             draft_network_collection: DraftNetworkCollection | None = None,
             protection_presentation: Mapping[str, Any] | None = None,
             presentation_collection: Mapping[str, Any] | None = None,
             study_cases: Sequence[StudyCaseDefinition] = (),
             instrumentation: ProjectInstrumentationCollection | None = None) -> None:
        if path is None:
            path = presentation
            presentation = None
        if path is None:
            raise TypeError("path is required.")
        if not isinstance(context, ProjectContext):
            raise TypeError("context must be a ProjectContext.")
        if not isinstance(network, Network):
            raise TypeError("network must be a Network.")
        if draft_network is not None and draft_network_collection is not None:
            raise TypeError("Pass draft_network_collection or legacy draft_network, not both.")
        if draft_network is not None:
            if not isinstance(draft_network, DraftNetwork):
                raise TypeError("draft_network must be DraftNetwork or None.")
            if draft_network.project_id != context.project_id:
                raise ProjectPersistenceError("DraftNetwork project_id does not match the project.")
        if draft_network_collection is not None:
            if not isinstance(draft_network_collection, DraftNetworkCollection):
                raise TypeError("draft_network_collection must be DraftNetworkCollection or None.")
            if draft_network_collection.project_id != context.project_id:
                raise ProjectPersistenceError("DraftNetworkCollection project_id does not match the project.")
        if presentation is not None and not isinstance(presentation, Mapping):
            raise TypeError("presentation must be a mapping or None.")
        if presentation_collection is not None and not isinstance(presentation_collection, Mapping):
            raise TypeError("presentation_collection must be a mapping or None.")
        if protection_presentation is not None and not isinstance(protection_presentation, Mapping):
            raise TypeError("protection_presentation must be a mapping or None.")
        if not isinstance(dynamic_models, Sequence):
            raise TypeError("dynamic_models must be a sequence.")
        if any(not isinstance(item, DynamicMachineModelAssociation) for item in dynamic_models):
            raise TypeError("dynamic_models contains an invalid association.")
        if any(item.project_id != context.project_id for item in dynamic_models):
            raise ProjectPersistenceError("dynamic_models contains an association for a different project.")
        if len({(item.project_id, item.activation_generation) for item in dynamic_models}) > 1:
            raise ProjectPersistenceError("dynamic_models contains mixed project-generation provenance.")
        protection_snapshot = protection_configuration
        if protection_snapshot is not None:
            if not isinstance(protection_snapshot, ProtectionProjectConfigurationSnapshot):
                raise TypeError("protection_configuration must be an immutable ProtectionProjectConfigurationSnapshot or None.")
            if protection_snapshot.project_id != context.project_id:
                raise ProjectPersistenceError("Protection snapshot project_id does not match the project.")
            # Rehydrate only the detached snapshot payload for cross-domain
            # validation. Never serialize or inspect the live aggregate here.
            try:
                protection_configuration = ProtectionProjectConfiguration.from_dict(protection_snapshot.to_dict())
            except (TypeError, ValueError, KeyError) as exc:
                raise ProjectPersistenceError(f"Invalid Protection snapshot payload: {exc}") from exc
        if not isinstance(measurement_definitions, Sequence) or any(
            not isinstance(item, Mapping) for item in measurement_definitions
        ):
            raise TypeError("measurement_definitions must be a sequence of mappings.")
        if control_configuration is not None:
            if not isinstance(control_configuration, ControlConfiguration):
                raise TypeError("control_configuration must be ControlConfiguration or None.")
            if control_configuration.project_id != context.project_id:
                raise ProjectPersistenceError("Control configuration project_id does not match the project.")
            control_configuration.validate()
        if control_signal_mapping is not None and not isinstance(control_signal_mapping, ControlSignalMapping):
            raise TypeError("control_signal_mapping must be ControlSignalMapping or None.")
        if instrumentation is None:
            instrumentation = ProjectInstrumentationCollection(project_id=context.project_id)
        if not isinstance(instrumentation, ProjectInstrumentationCollection):
            raise TypeError("instrumentation must be ProjectInstrumentationCollection or None.")
        if instrumentation.project_id != context.project_id:
            raise ProjectPersistenceError("Instrumentation collection project_id does not match the project.")
        if not isinstance(study_cases, Sequence) or any(
            not isinstance(item, StudyCaseDefinition) for item in study_cases
        ):
            raise TypeError("study_cases must be a sequence of StudyCaseDefinition values.")
        if any(item.project_id != context.project_id for item in study_cases):
            raise ProjectPersistenceError("study_cases contains a definition for a different project.")
        if len({item.study_id for item in study_cases}) != len(study_cases):
            raise ProjectPersistenceError("study_cases contains duplicate study identities.")

        target = normalize_package_path(path)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            if network.topology_dirty:
                network.rebuild_topology()
            network.validate()
        except Exception as exc:
            raise ProjectPersistenceError(
                f"Project Network failed the persistence validation gate: {exc}"
            ) from exc

        if presentation_collection is not None:
            canonical_collection = self._normalize_presentation_collection(
                presentation_collection, context.project_id
            )
        elif presentation is not None:
            canonical_collection = self._normalize_presentation_collection(
                {
                    "schema": PRESENTATION_SCHEMA_VERSION,
                    "documents": [dict(presentation)],
                    "active_document_id": presentation.get("document_id"),
                },
                context.project_id,
            )
        else:
            canonical_collection = {
                "schema": PRESENTATION_SCHEMA_VERSION,
                "documents": [],
                "active_document_id": None,
            }

        active_id = canonical_collection["active_document_id"]
        documents = canonical_collection["documents"]
        active_presentation = next(
            (item for item in documents if item["document_id"] == active_id),
            documents[0] if documents else None,
        )
        self._validate_project_state(
            context, network, active_presentation, canonical_collection,
            tuple(dynamic_models), protection_configuration, control_configuration,
            measurement_definitions,
            instrumentation,
        )
        document_ids = tuple(item["document_id"] for item in documents)
        if not document_ids:
            document_ids = (f"{context.project_id}:sld",)
        active_draft_document_id = active_id or document_ids[0]
        if draft_network_collection is not None:
            canonical_draft_collection = DraftNetworkCollection.from_dict(
                draft_network_collection.to_dict(),
                project_id=context.project_id,
                activation_generation=draft_network_collection.activation_generation,
                document_ids=document_ids,
            )
        else:
            legacy_draft_data = draft_network.to_dict() if draft_network is not None else None
            canonical_draft_collection = DraftNetworkCollection.from_dict(
                legacy_draft_data,
                project_id=context.project_id,
                activation_generation=(
                    draft_network.activation_generation if draft_network is not None else 1
                ),
                document_ids=document_ids,
                legacy_document_id=active_draft_document_id,
            )

        project: dict[str, Any] = {
            "schema": PROJECT_SCHEMA_VERSION,
            "project": {"project_id": context.project_id, "name": context.name},
            "network": serialize_network(network),
            "presentations": canonical_collection,
            "dynamic_models": [item.to_dict() for item in dynamic_models],
            "measurement": {"channels": [dict(item) for item in measurement_definitions]},
            "study_cases": [self._serialize_study_case(item) for item in study_cases],
            "instrumentation": instrumentation.to_mapping(),
        }
        if protection_snapshot is not None:
            project["protection"] = protection_snapshot.to_dict()
        if protection_presentation is not None:
            project["protection_presentation"] = dict(protection_presentation)
        if control_configuration is not None:
            project["control"] = control_configuration.to_dict()
        if control_signal_mapping is not None:
            project["control_signal_mapping"] = control_signal_mapping.to_dict()
        project["draft_networks"] = canonical_draft_collection.to_dict()

        manifest = {"format": "GridForgeProject", "package_version": PACKAGE_VERSION}
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
        backup_dir: Path | None = None
        replacement_installed = False
        try:
            self._write_json(temp_dir / MANIFEST_NAME, manifest)
            self._write_json(temp_dir / "project.json", project)
            if target.exists():
                backup_dir = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup.", dir=parent))
                backup_dir.rmdir()
                os.replace(target, backup_dir)
            os.replace(temp_dir, target)
            replacement_installed = True
            temp_dir = Path()
            if backup_dir is not None:
                shutil.rmtree(backup_dir)
                backup_dir = None
        except Exception as exc:
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if backup_dir is not None and backup_dir.exists():
                if replacement_installed and target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if not target.exists():
                    os.replace(backup_dir, target)
            raise ProjectPersistenceError(
                f"Unable to save GridForge project to {target}: {exc}"
            ) from exc

    @staticmethod
    def _normalize_presentation_collection(data: Mapping[str, Any], project_id: str) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise ProjectPersistenceError("Persistent presentation collection must be a mapping.")
        if data.get("schema") != PRESENTATION_SCHEMA_VERSION:
            raise ProjectPersistenceError(
                f"Non-canonical presentation collection schema: {data.get('schema')!r}; "
                f"expected {PRESENTATION_SCHEMA_VERSION}."
            )
        documents = data.get("documents")
        if not isinstance(documents, list):
            raise ProjectPersistenceError("Presentation collection documents must be an array.")

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in documents:
            if not isinstance(item, Mapping):
                raise ProjectPersistenceError("Every persisted presentation document must be an object.")
            document_id = item.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip():
                raise ProjectPersistenceError("Persisted presentation document_id must be a non-empty string.")
            if item.get("document_type") != "sld":
                raise ProjectPersistenceError(
                    f"Unsupported persisted presentation document_type: {item.get('document_type')!r}"
                )
            if item.get("schema") != SLD_SCHEMA_VERSION:
                raise ProjectPersistenceError(
                    f"Non-canonical SLD schema in document {document_id!r}: "
                    f"{item.get('schema')!r}; expected {SLD_SCHEMA_VERSION}."
                )
            item_project_id = item.get("project_id")
            if item_project_id != project_id:
                raise ProjectPersistenceError(
                    f"Presentation document {document_id!r} belongs to another project or has no canonical project_id."
                )
            try:
                validate_canonical_sld(item, project_id=project_id)
            except (SLDMigrationError, TypeError, ValueError) as exc:
                raise ProjectPersistenceError(
                    f"Malformed SLD document {document_id!r}: {exc}"
                ) from exc
            if document_id in seen:
                raise ProjectPersistenceError(f"Duplicate persisted document_id: {document_id}")
            seen.add(document_id)
            normalized.append(dict(item))

        active_id = data.get("active_document_id")
        if active_id is not None and not isinstance(active_id, str):
            raise ProjectPersistenceError("active_document_id must be a string or null.")
        if active_id is not None and active_id not in seen:
            raise ProjectPersistenceError(
                f"active_document_id {active_id!r} does not reference a persisted SLD document."
            )
        return {
            "schema": PRESENTATION_SCHEMA_VERSION,
            "documents": normalized,
            "active_document_id": active_id,
        }

    @staticmethod
    def _json_safe_study_value(value: Any) -> Any:
        """Convert immutable JSON-compatible Study Case values to canonical data."""
        from enum import Enum
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ProjectPersistenceError("Study Case floating-point values must be finite.")
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, complex):
            return {"__complex__": [value.real, value.imag]}
        if isinstance(value, Mapping):
            return {str(key): ProjectPersistenceService._json_safe_study_value(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [ProjectPersistenceService._json_safe_study_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted((ProjectPersistenceService._json_safe_study_value(item) for item in value), key=repr)
        raise ProjectPersistenceError(
            f"Study Case contains a non-persistable runtime value of type {type(value).__name__}."
        )

    @staticmethod
    def _restore_study_value(value: Any) -> Any:
        """Restore explicitly tagged complex values in persisted Study Case options."""
        if isinstance(value, Mapping):
            if set(value) == {"__complex__"}:
                parts = value["__complex__"]
                if not isinstance(parts, (list, tuple)) or len(parts) != 2:
                    raise ProjectPersistenceError("Malformed persisted complex Study Case value.")
                return complex(float(parts[0]), float(parts[1]))
            return {
                key: ProjectPersistenceService._restore_study_value(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [ProjectPersistenceService._restore_study_value(item) for item in value]
        return value

    @classmethod
    def _serialize_power_flow_configuration(
        cls, configuration: PowerFlowStudyConfiguration
    ) -> dict[str, Any]:
        return {
            "bus_types": {key: value.value for key, value in configuration.bus_types.items()},
            "base_mva": configuration.base_mva,
            "tolerance": configuration.tolerance,
            "max_iterations": configuration.max_iterations,
            "voltage_bases_kv": dict(configuration.voltage_bases_kv),
            "numerical_options": cls._json_safe_study_value(configuration.numerical_options),
        }

    @staticmethod
    def _deserialize_power_flow_configuration(
        payload: Mapping[str, Any]
    ) -> PowerFlowStudyConfiguration:
        return PowerFlowStudyConfiguration.from_mapping(
            payload["bus_types"],
            base_mva=payload["base_mva"],
            tolerance=payload.get("tolerance", 1e-8),
            max_iterations=payload.get("max_iterations", 50),
            voltage_bases_kv=payload.get("voltage_bases_kv", {}),
            numerical_options=payload.get("numerical_options", {}),
        )

    @classmethod
    def _serialize_study_case(cls, case: StudyCaseDefinition) -> dict[str, Any]:
        configuration = case.configuration.get("configuration")
        if isinstance(configuration, PowerFlowStudyConfiguration):
            typed = {
                "kind": "power_flow",
                **cls._serialize_power_flow_configuration(configuration),
            }
        elif isinstance(configuration, ShortCircuitStudyConfiguration):
            typed = {
                "kind": "short_circuit",
                "fault_type": configuration.fault_type.value,
                "fault_bus_id": configuration.fault_bus_id,
                "fault_impedance": cls._json_safe_study_value(configuration.fault_impedance),
                "element_ids": list(configuration.element_ids),
                "metadata": cls._json_safe_study_value(configuration.metadata),
                "base_mva": configuration.base_mva,
            }
        elif isinstance(configuration, TransientStabilityStudyConfiguration):
            typed = {
                "kind": "transient_stability",
                "start_time": configuration.start_time,
                "end_time": configuration.end_time,
                "dt": configuration.dt,
                "metadata": cls._json_safe_study_value(configuration.metadata),
                "power_flow_configuration": (
                    cls._serialize_power_flow_configuration(configuration.power_flow_configuration)
                    if configuration.power_flow_configuration is not None else None
                ),
            }
        else:
            raise ProjectPersistenceError(
                f"Unsupported typed Study Case configuration: {type(configuration).__name__}."
            )
        runtime_only_keys = {"prepared_power_flow", "power_flow_result"}
        if (
            isinstance(configuration, TransientStabilityStudyConfiguration)
            and configuration.power_flow_configuration is None
        ):
            raise ProjectPersistenceError(
                "Transient Stability Study Case must define power_flow_configuration "
                "to remain runnable after project reload."
            )
        extras = {
            key: cls._json_safe_study_value(value)
            for key, value in case.configuration.items()
            if key != "configuration" and key not in runtime_only_keys
        }
        revision = case.source_revision
        return {
            "schema": 1,
            "study_id": str(case.study_id),
            "study_type": case.study_type,
            "display_name": case.display_name,
            "source_revision": {
                "model_revision": revision.model_revision,
                "topology_revision": revision.topology_revision,
                "presentation_revision": revision.presentation_revision,
                "persisted_revision": revision.persisted_revision,
            },
            "configuration": typed,
            "options": extras,
        }

    @classmethod
    def _deserialize_study_case(
        cls, data: Mapping[str, Any], *, project_id: str, network: Network
    ) -> StudyCaseDefinition:
        if data.get("schema") != 1:
            raise ProjectPersistenceError(f"Unsupported Study Case schema {data.get('schema')!r}.")
        try:
            from uuid import UUID
            study_id = UUID(str(data["study_id"]))
            study_type = str(data["study_type"])
            payload = data["configuration"]
            if not isinstance(payload, Mapping):
                raise TypeError("Study Case configuration must be an object.")
            kind = payload.get("kind")
            if kind == "power_flow":
                typed = cls._deserialize_power_flow_configuration(payload)
            elif kind == "short_circuit":
                impedance = payload.get("fault_impedance", {"__complex__": [0.0, 0.0]})
                if isinstance(impedance, Mapping) and "__complex__" in impedance:
                    impedance = complex(*impedance["__complex__"])
                typed = ShortCircuitStudyConfiguration(
                    fault_type=FaultType.from_value(payload["fault_type"]),
                    fault_bus_id=str(payload["fault_bus_id"]),
                    fault_impedance=complex(impedance),
                    element_ids=tuple(payload.get("element_ids", ())),
                    metadata=payload.get("metadata", {}),
                    base_mva=payload.get("base_mva"),
                )
            elif kind == "transient_stability":
                initial_power_flow = payload.get("power_flow_configuration")
                if initial_power_flow is not None and not isinstance(initial_power_flow, Mapping):
                    raise TypeError("Transient Stability Power Flow initialization must be an object or null.")
                typed = TransientStabilityStudyConfiguration(
                    start_time=payload.get("start_time", 0.0),
                    end_time=payload.get("end_time", 10.0),
                    dt=payload.get("dt", 0.01),
                    metadata=payload.get("metadata", {}),
                    power_flow_configuration=(
                        cls._deserialize_power_flow_configuration(initial_power_flow)
                        if initial_power_flow is not None else None
                    ),
                )
            else:
                raise ProjectPersistenceError(f"Unsupported persisted Study Case configuration kind: {kind!r}.")
            revision_data = data.get("source_revision", {})
            revision = ProjectRevision(
                model_revision=revision_data.get("model_revision", 0),
                topology_revision=revision_data.get("topology_revision", 0),
                presentation_revision=revision_data.get("presentation_revision", 0),
                persisted_revision=revision_data.get("persisted_revision", 0),
            )
            options = data.get("options", {})
            if not isinstance(options, Mapping):
                raise TypeError("Study Case options must be an object.")
            config = {"configuration": typed, **cls._restore_study_value(dict(options))}
            case = StudyCaseDefinition(
                study_id=study_id,
                project_id=project_id,
                activation_generation=1,
                source_revision=revision,
                study_type=study_type,
                configuration=config,
                display_name=str(data.get("display_name", "")),
            )
        except ProjectPersistenceError:
            raise
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            raise ProjectPersistenceError(f"Malformed Study Case payload: {exc}") from exc

        bus_ids = {
            str(getattr(bus, "id", getattr(bus, "object_id", "")))
            for bus in getattr(network, "buses", ())
            if str(getattr(bus, "id", getattr(bus, "object_id", ""))).strip()
        }
        typed = case.configuration.get("configuration")
        referenced = set()
        power_flow_configuration = None
        if isinstance(typed, PowerFlowStudyConfiguration):
            power_flow_configuration = typed
        elif isinstance(typed, TransientStabilityStudyConfiguration):
            # Transient Stability can carry a persisted Power Flow initialization
            # contract. Validate its bus references at load time as well; otherwise
            # a malformed Study Case can survive project activation and fail much
            # later during numerical preparation.
            power_flow_configuration = typed.power_flow_configuration
        if power_flow_configuration is not None:
            referenced.update(power_flow_configuration.bus_types)
            referenced.update(power_flow_configuration.voltage_bases_kv)
        if isinstance(typed, ShortCircuitStudyConfiguration):
            referenced.add(typed.fault_bus_id)
        unknown = sorted(item for item in referenced if item not in bus_ids)
        if unknown:
            raise ProjectPersistenceError(
                "Study Case references Bus identities absent from the persisted Network: "
                + ", ".join(unknown)
            )
        return case

    @staticmethod
    def _deserialize_control_configuration(data: Mapping[str, Any], project_id: str) -> ControlConfiguration:
        configuration = ControlConfiguration.from_dict(data)
        if configuration.project_id != project_id:
            raise ValueError("Control configuration project_id does not match the project.")
        configuration.validate()
        return configuration

    @staticmethod
    def _validate_canonical_project_data(project: Mapping[str, Any]) -> None:
        """Validate the current persistence representation before deserialization."""
        if not isinstance(project, Mapping):
            raise ProjectPersistenceError("Canonical project representation must be an object.")
        allowed = {
            "schema", "project", "network", "presentations", "dynamic_models",
            "measurement", "control", "control_signal_mapping", "protection", "protection_presentation",
            "draft_network", "draft_networks", "study_cases", "instrumentation",
        }
        unknown = sorted(set(project) - allowed)
        if unknown:
            raise ProjectPersistenceError(
                "Unknown canonical project fields are rejected to prevent silent data loss: "
                + ", ".join(str(item) for item in unknown)
            )
        if project.get("schema") != PROJECT_SCHEMA_VERSION:
            raise ProjectPersistenceError(
                f"Canonical project schema must be {PROJECT_SCHEMA_VERSION}, got {project.get('schema')!r}."
            )
        metadata = project.get("project")
        if not isinstance(metadata, Mapping):
            raise ProjectPersistenceError("Canonical project metadata must be an object.")
        project_id, name = metadata.get("project_id"), metadata.get("name")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectPersistenceError("project_id must be a non-empty string.")
        if not isinstance(name, str) or not name.strip():
            raise ProjectPersistenceError("project name must be a non-empty string.")

        network = project.get("network")
        if not isinstance(network, Mapping) or network.get("schema") != CORE_SERIALIZATION_VERSION:
            raise ProjectPersistenceError(
                f"Canonical Core serialization schema must be {CORE_SERIALIZATION_VERSION}."
            )
        if not isinstance(network.get("elements"), list):
            raise ProjectPersistenceError("Canonical network elements must be an array.")
        connectivity = network.get("connectivity")
        if not isinstance(connectivity, Mapping) or connectivity.get("schema") != CONNECTIVITY_SERIALIZATION_VERSION:
            raise ProjectPersistenceError(
                f"Canonical connectivity serialization schema must be {CONNECTIVITY_SERIALIZATION_VERSION}."
            )
        if not isinstance(connectivity.get("simple_wires"), list):
            raise ProjectPersistenceError("Canonical simple_wires must be an array.")
        if not isinstance(connectivity.get("phase_domain_nodes"), list) or not isinstance(connectivity.get("phase_domain_connections"), list):
            raise ProjectPersistenceError("Canonical phase-domain conductor graph collections must be arrays.")

        collection = project.get("presentations")
        if not isinstance(collection, Mapping):
            raise ProjectPersistenceError("Canonical presentations collection is required.")
        ProjectPersistenceService._normalize_presentation_collection(collection, project_id)

        dynamic_models = project.get("dynamic_models")
        if not isinstance(dynamic_models, list):
            raise ProjectPersistenceError("Canonical dynamic_models must be an array.")
        measurement = project.get("measurement")
        if not isinstance(measurement, Mapping):
            raise ProjectPersistenceError("Canonical measurement must be an object.")
        channels = measurement.get("channels")
        if not isinstance(channels, list) or any(not isinstance(item, Mapping) for item in channels):
            raise ProjectPersistenceError("Canonical measurement.channels must be an array of objects.")
        for key in ("control", "protection", "protection_presentation", "draft_network", "draft_networks"):
            if key in project and project[key] is not None and not isinstance(project[key], Mapping):
                raise ProjectPersistenceError(f"Canonical {key} payload must be an object.")
        if "draft_network" in project and "draft_networks" in project:
            raise ProjectPersistenceError("Canonical project cannot contain both draft_network and draft_networks.")
        study_cases = project.get("study_cases", [])
        if not isinstance(study_cases, list) or any(not isinstance(item, Mapping) for item in study_cases):
            raise ProjectPersistenceError("Canonical study_cases must be an array of objects.")

    @staticmethod
    def _validate_project_state(context: ProjectContext, network: Network, presentation: Mapping[str, Any] | None, presentation_collection: Mapping[str, Any] | None, dynamic_models: Sequence[DynamicMachineModelAssociation], protection_configuration: ProtectionProjectConfiguration | None, control_configuration: ControlConfiguration | None = None, measurement_definitions: Sequence[Mapping[str, Any]] = (), instrumentation: ProjectInstrumentationCollection | None = None) -> None:
        """Validate cross-domain project invariants at load/save boundaries."""
        channel_ids: set[str] = set()
        for definition in measurement_definitions:
            channel_id = definition.get("channel_id", definition.get("id"))
            if not isinstance(channel_id, str) or not channel_id.strip():
                raise ProjectPersistenceError("Persisted measurement channel ID must be a non-empty string.")
            channel_id = channel_id.strip()
            if channel_id in channel_ids:
                raise ProjectPersistenceError(f"Duplicate persisted measurement channel ID: {channel_id}")
            channel_ids.add(channel_id)
        if protection_configuration is not None:
            for configuration in protection_configuration.elements:
                for input_name, channel_id in configuration.input_channel_ids.items():
                    if not isinstance(input_name, str) or not input_name.strip():
                        raise ProjectPersistenceError(
                            f"Protection configuration '{configuration.element_id}' has an invalid input name."
                        )
                    if (
                        not isinstance(channel_id, str)
                        or not channel_id.strip()
                        or channel_id != channel_id.strip()
                    ):
                        raise ProjectPersistenceError(
                            f"Protection configuration '{configuration.element_id}' input '{input_name}' "
                            "must reference a canonical, non-empty measurement channel ID."
                        )
                    if channel_id not in channel_ids:
                        raise ProjectPersistenceError(
                            f"Protection configuration '{configuration.element_id}' input '{input_name}' "
                            f"references missing measurement channel '{channel_id}'."
                        )
        if not isinstance(context, ProjectContext) or not isinstance(network, Network):
            raise ProjectPersistenceError("Project context or Network is invalid.")
        if instrumentation is not None:
            if instrumentation.project_id != context.project_id:
                raise ProjectPersistenceError("Instrumentation collection project_id does not match the active project.")
            try:
                validate_instrumentation_targets(instrumentation, network)
            except (TypeError, ValueError) as exc:
                raise ProjectPersistenceError(f"Invalid project instrumentation target: {exc}") from exc
        if presentation_collection is not None:
            normalized_collection = ProjectPersistenceService._normalize_presentation_collection(
                presentation_collection,
                context.project_id,
            )
            for document in normalized_collection["documents"]:
                document_schema = document.get("schema")
                if document_schema != SLD_SCHEMA_VERSION:
                    raise ProjectPersistenceError(
                        f"Non-canonical SLD representation schema: {document_schema!r}; expected {SLD_SCHEMA_VERSION}"
                    )
                document_validation = ValidationService.validate_sld_associations(
                    context,
                    network,
                    document,
                )
                for issue in document_validation.issues:
                    if issue.severity == ValidationSeverity.ERROR:
                        raise ProjectPersistenceError(issue.message)
        if presentation is not None:
            if not isinstance(presentation, Mapping):
                raise ProjectPersistenceError("Persistent SLD representation must be a mapping.")
            sld_schema = presentation.get("schema")
            if sld_schema != SLD_SCHEMA_VERSION:
                raise ProjectPersistenceError(
                    f"Non-canonical SLD representation schema: {sld_schema!r}; expected {SLD_SCHEMA_VERSION}"
                )
            sld_validation = ValidationService.validate_sld_associations(
                context,
                network,
                presentation,
            )
            for issue in sld_validation.issues:
                # Authored ORPHANED presentation objects remain warnings and
                # are retained; malformed or stale BOUND associations block save.
                if issue.severity == ValidationSeverity.ERROR:
                    raise ProjectPersistenceError(issue.message)
        for association in dynamic_models:
            if association.project_id != context.project_id:
                raise ProjectPersistenceError(f"Dynamic model association '{association.machine_id}' belongs to another project.")
            try:
                machine = network.get_by_identity(association.machine_id)
                bus = network.get_by_identity(association.bus_id)
            except KeyError as exc:
                raise ProjectPersistenceError(f"Dynamic model association references a missing Core object: {exc}") from exc
            if machine.element_type != "SYNCHRONOUS_MACHINE":
                raise ProjectPersistenceError(f"Dynamic model association '{association.machine_id}' does not reference a SynchronousMachine.")
            if bus.element_type != "BUS":
                raise ProjectPersistenceError(f"Dynamic model association '{association.bus_id}' does not reference a Bus.")
        if control_configuration is not None:
            if control_configuration.project_id != context.project_id: raise ProjectPersistenceError("Control configuration project_id does not match active project.")
            try: control_configuration.validate()
            except (TypeError,ValueError) as exc: raise ProjectPersistenceError(f"Invalid Control configuration: {exc}") from exc
        if protection_configuration is not None:
            if protection_configuration.project_id != context.project_id:
                raise ProjectPersistenceError("Protection configuration project_id does not match the active project.")
            for item in protection_configuration.elements:
                try:
                    protected_element = network.get_by_identity(item.element_id)
                    relay = network.get_by_identity(item.relay_id)
                except KeyError as exc:
                    raise ProjectPersistenceError(
                        f"Protection configuration '{item.element_id}' references a missing Core object: {exc}"
                    ) from exc
                if relay.element_type != "RELAY":
                    raise ProjectPersistenceError(
                        f"Protection configuration '{item.element_id}' relay_id does not reference a Relay."
                    )
                if not getattr(protected_element, "id", None):
                    raise ProjectPersistenceError(
                        f"Protection configuration '{item.element_id}' references an invalid protected object."
                    )

    @staticmethod
    def _recover_interrupted_save(package: Path) -> None:
        """Recover a previous valid package after an interrupted replacement."""
        parent = package.parent
        backup_candidates = sorted(
            parent.glob(f".{package.name}.backup.*"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        if package.is_dir():
            # A completed replacement may leave its old backup behind if the
            # process stopped before cleanup. The installed package remains
            # authoritative; stale backups are safe to discard.
            for backup in backup_candidates:
                shutil.rmtree(backup, ignore_errors=True)
            return
        if not backup_candidates:
            return

        backup = backup_candidates[0]
        try:
            os.replace(backup, package)
        except OSError as exc:
            raise ProjectPersistenceError(
                f"Unable to recover the previous persisted project package: {package}"
            ) from exc
        for stale in backup_candidates[1:]:
            shutil.rmtree(stale, ignore_errors=True)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file(): raise ProjectPersistenceError(f"Required project file is missing: {path.name}")
        try:
            with path.open("r", encoding="utf-8") as handle: value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc: raise ProjectPersistenceError(f"Unable to read {path.name}: {exc}") from exc
        if not isinstance(value, dict): raise ProjectPersistenceError(f"{path.name} must contain a JSON object.")
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        try:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as exc: raise ProjectPersistenceError(f"Unable to write {path.name}: {exc}") from exc


__all__ = ["LoadedProject", "ProjectPersistenceError", "ProjectPersistenceService"]
