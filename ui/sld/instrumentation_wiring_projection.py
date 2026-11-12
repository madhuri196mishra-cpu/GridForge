"""Presentation projection of physical instrumentation wiring into an SLD document.

This adapter is deliberately read-only: Core/Application own cable, core,
wire and signal identity. SLDDocument owns only node references and route
presentation. No electrical connectivity is created by this projection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.application.read_models import InstrumentationWiringReadModel
from ui.sld.sld_model import SLDEndpoint, SLDEndpointKind, SLDModel, SLDRoute


@dataclass(frozen=True, slots=True)
class SLDPhysicalWiringSegment:
    """One drawable presentation segment for a physical conductor path."""

    projection_id: str
    carrier_kind: str
    carrier_id: str
    carrier_tag: str
    conductor_role: str
    segment_index: int
    source: SLDEndpoint
    target: SLDEndpoint
    cable_id: str | None = None
    cable_tag: str | None = None
    core_id: str | None = None
    core_number: int | None = None
    signal_tags: tuple[str, ...] = ()
    route: SLDRoute = SLDRoute()


@dataclass(frozen=True, slots=True)
class SLDPhysicalWiringRecord:
    """A complete carrier projection or an explicit unresolved diagnostic."""

    carrier_kind: str
    carrier_id: str
    carrier_tag: str
    segments: tuple[SLDPhysicalWiringSegment, ...]
    complete: bool
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SLDInstrumentationWiringProjection:
    """Detached physical wiring projection for one SLD document."""

    project_id: str
    activation_generation: int
    records: tuple[SLDPhysicalWiringRecord, ...]

    @property
    def segments(self) -> tuple[SLDPhysicalWiringSegment, ...]:
        return tuple(segment for record in self.records for segment in record.segments)


class SLDInstrumentationWiringAdapter:
    """Map Application wiring read models to document-node endpoint references."""

    def project(
        self,
        read_model: InstrumentationWiringReadModel,
        model: SLDModel,
        route_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> SLDInstrumentationWiringProjection:
        if not isinstance(read_model, InstrumentationWiringReadModel):
            raise TypeError("read_model must be an InstrumentationWiringReadModel")
        if not isinstance(model, SLDModel):
            raise TypeError("model must be an SLDModel")
        if route_overrides is None:
            route_overrides = {}
        if not isinstance(route_overrides, Mapping):
            raise TypeError("route_overrides must be a mapping or None")

        nodes_by_equipment: dict[str, list[Any]] = {}
        for node in model.nodes:
            if node.equipment_id is not None:
                nodes_by_equipment.setdefault(node.equipment_id, []).append(node)

        cables = {str(item["id"]): item for item in read_model.cables}
        signal_tags: dict[tuple[str, str], set[str]] = {}
        for binding in read_model.signal_bindings:
            signal = next(
                (item for item in read_model.signals if item["id"] == binding["signal_id"]),
                None,
            )
            if signal is None:
                continue
            key = (str(binding["carrier_kind"]), str(binding["carrier_id"]))
            signal_tags.setdefault(key, set()).add(str(signal["tag"]))

        records: list[SLDPhysicalWiringRecord] = []
        for core in read_model.cores:
            cable = cables.get(str(core["cable_id"]))
            if cable is None:
                records.append(SLDPhysicalWiringRecord(
                    "CABLE_CORE", str(core["id"]), str(core.get("core_tag") or core["core_number"]),
                    (), False, ("Owning cable assembly is absent from the Application inventory.",),
                ))
                continue
            tag = str(core.get("core_tag") or f'{cable["tag"]}/C{core["core_number"]}')
            records.append(self._carrier_record(
                carrier_kind="CABLE_CORE",
                carrier_id=str(core["id"]),
                carrier_tag=tag,
                conductor_role=str(core["role"]),
                terminal_path=core["terminal_path"],
                nodes_by_equipment=nodes_by_equipment,
                signal_tags=tuple(sorted(signal_tags.get(("CABLE_CORE", str(core["id"])), ()))),
                cable_id=str(cable["id"]),
                cable_tag=str(cable["tag"]),
                core_id=str(core["id"]),
                core_number=int(core["core_number"]),
                route_overrides=route_overrides,
            ))

        for wire in read_model.panel_wires:
            records.append(self._carrier_record(
                carrier_kind="PANEL_WIRE",
                carrier_id=str(wire["id"]),
                carrier_tag=str(wire["tag"]),
                conductor_role=str(wire["role"]),
                terminal_path=wire["terminal_path"],
                nodes_by_equipment=nodes_by_equipment,
                signal_tags=tuple(sorted(signal_tags.get(("PANEL_WIRE", str(wire["id"])), ()))),
                route_overrides=route_overrides,
            ))

        return SLDInstrumentationWiringProjection(
            project_id=read_model.project_id,
            activation_generation=read_model.activation_generation,
            records=tuple(records),
        )

    @staticmethod
    def _carrier_record(
        *,
        carrier_kind: str,
        carrier_id: str,
        carrier_tag: str,
        conductor_role: str,
        terminal_path: tuple[Mapping[str, Any], ...],
        nodes_by_equipment: Mapping[str, list[Any]],
        signal_tags: tuple[str, ...],
        cable_id: str | None = None,
        cable_tag: str | None = None,
        core_id: str | None = None,
        core_number: int | None = None,
        route_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> SLDPhysicalWiringRecord:
        if not terminal_path:
            return SLDPhysicalWiringRecord(
                carrier_kind, carrier_id, carrier_tag, (), False,
                ("No physical terminal path has been assigned; this carrier is not drawable yet.",),
            )

        endpoints: list[SLDEndpoint] = []
        diagnostics: list[str] = []
        for index, terminal in enumerate(terminal_path):
            equipment_id = str(terminal["equipment_id"])
            role = str(terminal["terminal_role"])
            matches = nodes_by_equipment.get(equipment_id, ())
            if len(matches) != 1:
                reason = "is absent from" if not matches else "is ambiguous in"
                diagnostics.append(
                    f"Terminal {equipment_id}/{role} {reason} the active SLD document."
                )
                continue
            node = matches[0]
            endpoints.append(SLDEndpoint(
                kind=SLDEndpointKind.EQUIPMENT,
                node_id=node.node_id,
                equipment_id=equipment_id,
                terminal_role=role,
            ))

        if diagnostics:
            return SLDPhysicalWiringRecord(
                carrier_kind, carrier_id, carrier_tag, (), False, tuple(diagnostics),
            )
        if len(endpoints) != len(terminal_path):
            return SLDPhysicalWiringRecord(
                carrier_kind, carrier_id, carrier_tag, (), False,
                ("One or more physical terminal references could not be resolved.",),
            )

        route_overrides = route_overrides or {}
        segments = tuple(
            SLDPhysicalWiringSegment(
                projection_id=f"__gf_projection__:instrumentation_wiring:{carrier_kind}:{carrier_id}:{index + 1}",
                carrier_kind=carrier_kind,
                carrier_id=carrier_id,
                carrier_tag=carrier_tag,
                conductor_role=conductor_role,
                segment_index=index + 1,
                source=endpoints[index],
                target=endpoints[index + 1],
                cable_id=cable_id,
                cable_tag=cable_tag,
                core_id=core_id,
                core_number=core_number,
                signal_tags=signal_tags,
                route=SLDRoute.from_dict(route_overrides[f"{carrier_kind}:{carrier_id}:{index + 1}"]) if f"{carrier_kind}:{carrier_id}:{index + 1}" in route_overrides else SLDRoute(),
            )
            for index in range(len(endpoints) - 1)
        )
        return SLDPhysicalWiringRecord(
            carrier_kind, carrier_id, carrier_tag, segments, True, (),
        )


__all__ = [
    "SLDPhysicalWiringSegment",
    "SLDPhysicalWiringRecord",
    "SLDInstrumentationWiringProjection",
    "SLDInstrumentationWiringAdapter",
]
