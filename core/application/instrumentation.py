"""Application-owned immutable instrumentation collection for one project.

This value object is the project-scoped owner of physical source records and
their monitored-equipment installation associations. It is not a live sample
registry, electrical Network collection, or MeasurementChannel authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.model.transformer import Transformer
from core.measurement.measurement_source import (
    MeasurementSourceInstallation,
    PhysicalMeasurementSource,
)


INSTRUMENTATION_COLLECTION_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class ProjectInstrumentationCollection:
    """Canonical project-scoped collection of physical instruments."""

    project_id: str
    sources: tuple[PhysicalMeasurementSource, ...] = ()
    installations: tuple[MeasurementSourceInstallation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id or self.project_id != self.project_id.strip():
            raise ValueError("project_id must be a canonical non-empty string.")
        sources = tuple(self.sources)
        installations = tuple(self.installations)
        if any(not isinstance(item, PhysicalMeasurementSource) for item in sources):
            raise TypeError("sources must contain only PhysicalMeasurementSource values.")
        if any(not isinstance(item, MeasurementSourceInstallation) for item in installations):
            raise TypeError("installations must contain only MeasurementSourceInstallation values.")
        source_ids = [item.source_id for item in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Project instrumentation contains duplicate source identities.")
        source_by_id = {item.source_id: item for item in sources}
        installation_ids = [item.installation_id for item in installations]
        if len(installation_ids) != len(set(installation_ids)):
            raise ValueError("Project instrumentation contains duplicate installation identities.")
        associations: set[tuple[str, str, str, str]] = set()
        for installation in installations:
            source = source_by_id.get(installation.source_id)
            if source is None:
                raise ValueError(
                    f"Installation {installation.installation_id!r} references an unknown source."
                )
            source.interface(installation.interface_id)
            association = (
                installation.source_id, installation.interface_id,
                installation.monitored_equipment_id, installation.monitored_point_id,
            )
            if association in associations:
                raise ValueError("Duplicate physical source-to-monitoring-point association.")
            associations.add(association)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "installations", installations)

    def source(self, source_id: str) -> PhysicalMeasurementSource:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise KeyError(f"Project instrumentation source not found: {source_id!r}.")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": INSTRUMENTATION_COLLECTION_SCHEMA,
            "project_id": self.project_id,
            "sources": [item.to_mapping() for item in self.sources],
            "installations": [item.to_mapping() for item in self.installations],
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, project_id: str) -> "ProjectInstrumentationCollection":
        if not isinstance(data, Mapping):
            raise TypeError("Project instrumentation collection must be a mapping.")
        if set(data) != {"schema", "project_id", "sources", "installations"}:
            raise ValueError("Project instrumentation collection fields are not canonical.")
        if isinstance(data["schema"], bool) or not isinstance(data["schema"], int):
            raise TypeError("Instrumentation collection schema must be an integer.")
        if data["schema"] != INSTRUMENTATION_COLLECTION_SCHEMA:
            raise ValueError(f"Unsupported instrumentation collection schema: {data['schema']!r}.")
        if data["project_id"] != project_id:
            raise ValueError("Project instrumentation collection belongs to a different project.")
        sources, installations = data["sources"], data["installations"]
        if not isinstance(sources, (list, tuple)) or not isinstance(installations, (list, tuple)):
            raise TypeError("Instrumentation sources and installations must be arrays.")
        return cls(
            project_id=project_id,
            sources=tuple(PhysicalMeasurementSource.from_mapping(item) for item in sources),
            installations=tuple(MeasurementSourceInstallation.from_mapping(item) for item in installations),
        )




def validate_instrumentation_targets(collection: ProjectInstrumentationCollection, network: Any) -> None:
    """Validate project installations against canonical Network equipment.

    Transformer winding targets resolve through the Core Transformer's
    canonical winding identity collection, including explicitly declared
    tertiary and higher-order winding records. These associations remain
    domain monitoring points, not electrical terminals or topology edges.
    """
    if not isinstance(collection, ProjectInstrumentationCollection):
        raise TypeError("collection must be ProjectInstrumentationCollection.")
    if network is None or not callable(getattr(network, "get_by_identity", None)):
        raise TypeError("network must expose the canonical equipment identity resolver.")
    for installation in collection.installations:
        try:
            monitored = network.get_by_identity(installation.monitored_equipment_id)
        except KeyError as exc:
            raise ValueError(f"Instrumentation installation {installation.installation_id!r} references missing monitored equipment {installation.monitored_equipment_id!r}.") from exc
        expected_type = installation.monitored_equipment_type.replace("-", "_").upper()
        actual_type = str(getattr(monitored, "element_type", "")).upper()
        if expected_type != actual_type:
            raise ValueError(f"Instrumentation installation {installation.installation_id!r} expects monitored equipment type {expected_type!r}, found {actual_type!r}.")
        interface = collection.source(installation.source_id).interface(installation.interface_id)
        winding_id = interface.winding_id
        if winding_id is None:
            continue
        if not isinstance(monitored, Transformer):
            raise ValueError("Winding-bound measurement interfaces must monitor Core Transformer equipment.")
        if installation.monitored_equipment_type != "transformer":
            raise ValueError("Winding-bound transformer installation must declare monitored_equipment_type='transformer'.")
        if installation.monitored_point_id != winding_id:
            raise ValueError(f"Installation {installation.installation_id!r} monitoring point does not match its source interface winding_id.")
        try:
            winding = monitored.winding(winding_id)
        except KeyError as exc:
            raise ValueError(f"Transformer {monitored.id!r} does not declare canonical winding {winding_id!r}.") from exc
        if winding.connection is None:
            raise ValueError(f"Transformer {monitored.id!r} winding {winding_id!r} has no explicit connection; winding monitoring cannot be commissioned.")
        if interface.phase is not None and winding.phase_roles is not None and interface.phase.upper() not in winding.phase_roles:
            raise ValueError(f"Measurement interface phase {interface.phase!r} is not declared on transformer winding {winding_id!r}.")

__all__ = ["ProjectInstrumentationCollection", "INSTRUMENTATION_COLLECTION_SCHEMA", "validate_instrumentation_targets"]
