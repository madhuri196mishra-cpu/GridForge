"""Canonical physical cable installation routing contracts.

These records describe installation pathways (tray, ladder, duct, trench,
conduit, riser and pull/junction points). They are deliberately independent
of electrical Network topology and of SLD drawing coordinates.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Optional


class InstallationSystemKind(str, Enum):
    TRAY = "TRAY"
    LADDER = "LADDER"
    TRENCH = "TRENCH"
    DUCT = "DUCT"
    CONDUIT = "CONDUIT"
    UNDERGROUND = "UNDERGROUND"
    RISER = "RISER"
    JUNCTION_BOX = "JUNCTION_BOX"
    PULL_BOX = "PULL_BOX"
    OTHER = "OTHER"


class CableServiceClass(str, Enum):
    POWER = "POWER"
    CONTROL = "CONTROL"
    INSTRUMENTATION_ANALOG = "INSTRUMENTATION_ANALOG"
    INSTRUMENTATION_DIGITAL = "INSTRUMENTATION_DIGITAL"
    COMMUNICATION = "COMMUNICATION"
    PROTECTION_TRIP = "PROTECTION_TRIP"
    CT_SECONDARY = "CT_SECONDARY"
    PT_SECONDARY = "PT_SECONDARY"
    UNKNOWN = "UNKNOWN"


class LengthSource(str, Enum):
    FIELD_MEASURED = "FIELD_MEASURED"
    APPROVED_ROUTE_GEOMETRY = "APPROVED_ROUTE_GEOMETRY"
    MANUAL_ENGINEERING = "MANUAL_ENGINEERING"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class RoutingStatus(str, Enum):
    DRAFT = "DRAFT"
    INCOMPLETE = "INCOMPLETE"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    INSTALLED = "INSTALLED"
    RETIRED = "RETIRED"


class ValidationSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INCOMPLETE = "INCOMPLETE"


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a canonical non-empty string.")
    return value


def _optional_positive(value: Optional[float], name: str) -> Optional[float]:
    if value is None:
        return None
    numeric = float(value)
    if not isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be finite and positive when supplied.")
    return numeric


def _optional_finite(value: Optional[float], name: str) -> Optional[float]:
    if value is None:
        return None
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{name} must be finite when supplied.")
    return numeric


@dataclass(frozen=True, slots=True)
class InstallationPoint:
    id: str
    tag: str
    point_kind: InstallationSystemKind
    location_reference: Optional[str] = None
    elevation_m: Optional[float] = None
    installation_level: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("id", "tag"):
            _required(getattr(self, name), name)
        if not isinstance(self.point_kind, InstallationSystemKind):
            raise TypeError("point_kind must be InstallationSystemKind.")
        if self.location_reference is not None:
            _required(self.location_reference, "location_reference")
        _optional_finite(self.elevation_m, "elevation_m")
        if self.installation_level is not None:
            _required(self.installation_level, "installation_level")


@dataclass(frozen=True, slots=True)
class InstallationSystem:
    id: str
    tag: str
    system_kind: InstallationSystemKind
    usable_width_mm: Optional[float] = None
    usable_area_mm2: Optional[float] = None
    fill_limit_ratio: Optional[float] = None
    segregation_classes: tuple[CableServiceClass, ...] = ()
    fire_classification: Optional[str] = None
    environmental_classification: Optional[str] = None
    minimum_bend_radius_mm: Optional[float] = None
    design_criteria_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("id", "tag"):
            _required(getattr(self, name), name)
        if not isinstance(self.system_kind, InstallationSystemKind):
            raise TypeError("system_kind must be InstallationSystemKind.")
        _optional_positive(self.usable_width_mm, "usable_width_mm")
        _optional_positive(self.usable_area_mm2, "usable_area_mm2")
        if self.fill_limit_ratio is not None:
            ratio = float(self.fill_limit_ratio)
            if not isfinite(ratio) or not 0 < ratio <= 1:
                raise ValueError("fill_limit_ratio must be greater than 0 and at most 1.")
        classes = tuple(self.segregation_classes)
        if any(not isinstance(item, CableServiceClass) for item in classes):
            raise TypeError("segregation_classes must contain CableServiceClass values.")
        object.__setattr__(self, "segregation_classes", classes)
        for name in ("fire_classification", "environmental_classification", "design_criteria_id"):
            value = getattr(self, name)
            if value is not None:
                _required(value, name)
        _optional_positive(self.minimum_bend_radius_mm, "minimum_bend_radius_mm")


@dataclass(frozen=True, slots=True)
class InstallationRouteSegment:
    id: str
    sequence: int
    from_point_id: str
    to_point_id: str
    installation_system_id: str
    length_m: Optional[float] = None
    length_source: LengthSource = LengthSource.UNKNOWN
    elevation_change_m: Optional[float] = None
    bend_count: Optional[int] = None
    minimum_bend_radius_mm: Optional[float] = None
    installation_notes: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("id", "from_point_id", "to_point_id", "installation_system_id"):
            _required(getattr(self, name), name)
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("sequence must be a positive integer.")
        if self.from_point_id == self.to_point_id:
            raise ValueError("A route segment must connect distinct installation points.")
        _optional_nonnegative(self.length_m, "length_m")
        if not isinstance(self.length_source, LengthSource):
            raise TypeError("length_source must be LengthSource.")
        if self.length_m is None and self.length_source is not LengthSource.UNKNOWN:
            raise ValueError("A length source cannot be claimed when length_m is absent.")
        if self.length_m is not None and self.length_source is LengthSource.UNKNOWN:
            raise ValueError("A supplied length requires explicit provenance.")
        if self.elevation_change_m is not None and not isfinite(float(self.elevation_change_m)):
            raise ValueError("elevation_change_m must be finite.")
        if self.bend_count is not None and (isinstance(self.bend_count, bool) or not isinstance(self.bend_count, int) or self.bend_count < 0):
            raise ValueError("bend_count must be a non-negative integer.")
        _optional_positive(self.minimum_bend_radius_mm, "minimum_bend_radius_mm")


@dataclass(frozen=True, slots=True)
class PhysicalCableRoute:
    id: str
    tag: str
    start_point_id: str
    end_point_id: str
    segments: tuple[InstallationRouteSegment, ...] = ()
    status: RoutingStatus = RoutingStatus.DRAFT
    route_type: Optional[str] = None
    design_criteria_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("id", "tag", "start_point_id", "end_point_id"):
            _required(getattr(self, name), name)
        if self.start_point_id == self.end_point_id:
            raise ValueError("Route start and end points must differ.")
        if not isinstance(self.status, RoutingStatus):
            raise TypeError("status must be RoutingStatus.")
        segments = tuple(sorted(tuple(self.segments), key=lambda item: item.sequence))
        if any(not isinstance(item, InstallationRouteSegment) for item in segments):
            raise TypeError("segments must contain InstallationRouteSegment records.")
        if [item.sequence for item in segments] != list(range(1, len(segments) + 1)):
            raise ValueError("Route segment sequence must be contiguous and start at 1.")
        if segments:
            if segments[0].from_point_id != self.start_point_id or segments[-1].to_point_id != self.end_point_id:
                raise ValueError("Ordered segments do not match the route start/end points.")
            for left, right in zip(segments, segments[1:]):
                if left.to_point_id != right.from_point_id:
                    raise ValueError("Route segments contain a disconnected transition.")
        object.__setattr__(self, "segments", segments)
        for name in ("route_type", "design_criteria_id"):
            value = getattr(self, name)
            if value is not None:
                _required(value, name)

    @property
    def route_length_m(self) -> Optional[float]:
        if not self.segments or any(
            item.length_m is None or item.length_source is not LengthSource.APPROVED_ROUTE_GEOMETRY
            for item in self.segments
        ):
            return None
        return sum(item.length_m or 0.0 for item in self.segments)


@dataclass(frozen=True, slots=True)
class CableRouteAssignment:
    id: str
    cable_id: str
    route_id: str
    service_class: CableServiceClass
    manual_cable_length_m: Optional[float] = None
    manual_length_source: LengthSource = LengthSource.UNKNOWN
    slack_allowance_m: Optional[float] = None
    termination_allowance_m: Optional[float] = None
    procurement_rule_id: Optional[str] = None
    approved_procurement_length_m: Optional[float] = None
    cable_outer_diameter_mm: Optional[float] = None
    installation_allowance_factor: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("id", "cable_id", "route_id"):
            _required(getattr(self, name), name)
        if not isinstance(self.service_class, CableServiceClass):
            raise TypeError("service_class must be CableServiceClass.")
        _optional_nonnegative(self.manual_cable_length_m, "manual_cable_length_m")
        if not isinstance(self.manual_length_source, LengthSource):
            raise TypeError("manual_length_source must be LengthSource.")
        if self.manual_cable_length_m is None and self.manual_length_source is not LengthSource.UNKNOWN:
            raise ValueError("Manual length provenance requires a supplied manual length.")
        if self.manual_cable_length_m is not None and self.manual_length_source is LengthSource.UNKNOWN:
            raise ValueError("Manual cable length requires explicit provenance.")
        _optional_nonnegative(self.slack_allowance_m, "slack_allowance_m")
        _optional_nonnegative(self.termination_allowance_m, "termination_allowance_m")
        _optional_nonnegative(self.approved_procurement_length_m, "approved_procurement_length_m")
        _optional_positive(self.cable_outer_diameter_mm, "cable_outer_diameter_mm")
        if self.installation_allowance_factor is not None:
            factor = float(self.installation_allowance_factor)
            if not isfinite(factor) or factor < 1.0:
                raise ValueError("installation_allowance_factor must be finite and at least 1.0.")
        if self.procurement_rule_id is not None:
            _required(self.procurement_rule_id, "procurement_rule_id")
        if self.approved_procurement_length_m is not None and self.procurement_rule_id is None:
            raise ValueError("Approved procurement length requires a procurement_rule_id.")

    def installed_length_m(self, route: PhysicalCableRoute) -> Optional[float]:
        if route.id != self.route_id:
            raise ValueError("Route does not match this cable assignment.")
        if self.manual_cable_length_m is not None:
            return self.manual_cable_length_m
        return route.route_length_m

    def required_length_before_procurement_rule_m(self, route: PhysicalCableRoute) -> Optional[float]:
        installed = self.installed_length_m(route)
        if installed is None:
            return None
        return installed + (self.slack_allowance_m or 0.0) + (self.termination_allowance_m or 0.0)


@dataclass(frozen=True, slots=True)
class CableRoutingFinding:
    code: str
    severity: ValidationSeverity
    message: str
    record_id: Optional[str] = None

    def __post_init__(self) -> None:
        _required(self.code, "code")
        _required(self.message, "message")
        if not isinstance(self.severity, ValidationSeverity):
            raise TypeError("severity must be ValidationSeverity.")


@dataclass(frozen=True, slots=True)
class PhysicalCableRoutingInventory:
    points: tuple[InstallationPoint, ...] = ()
    systems: tuple[InstallationSystem, ...] = ()
    routes: tuple[PhysicalCableRoute, ...] = ()
    assignments: tuple[CableRouteAssignment, ...] = ()

    def __post_init__(self) -> None:
        for name, values, kind in (
            ("points", self.points, InstallationPoint),
            ("systems", self.systems, InstallationSystem),
            ("routes", self.routes, PhysicalCableRoute),
            ("assignments", self.assignments, CableRouteAssignment),
        ):
            values = tuple(values)
            if any(not isinstance(item, kind) for item in values):
                raise TypeError(f"{name} must contain {kind.__name__} records.")
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} contains duplicate IDs.")
            object.__setattr__(self, name, values)
        all_ids = [item.id for group in (self.points, self.systems, self.routes, self.assignments) for item in group]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Physical cable routing IDs must be globally unique.")
        tags = [item.tag.casefold() for item in (*self.points, *self.systems, *self.routes)]
        if len(tags) != len(set(tags)):
            raise ValueError("Duplicate installation point/system/route tag.")
        point_ids = {item.id for item in self.points}
        system_ids = {item.id for item in self.systems}
        route_by_id = {item.id: item for item in self.routes}
        for route in self.routes:
            if route.start_point_id not in point_ids or route.end_point_id not in point_ids:
                raise ValueError(f"Route {route.id!r} references a missing endpoint.")
            for segment in route.segments:
                if segment.from_point_id not in point_ids or segment.to_point_id not in point_ids:
                    raise ValueError(f"Route {route.id!r} references a missing installation point.")
                if segment.installation_system_id not in system_ids:
                    raise ValueError(f"Route segment {segment.id!r} references a missing installation system.")
        cable_ids = [item.cable_id for item in self.assignments]
        if len(cable_ids) != len(set(cable_ids)):
            raise ValueError("A cable may have only one active physical route assignment.")
        for assignment in self.assignments:
            if assignment.route_id not in route_by_id:
                raise ValueError(f"Cable assignment {assignment.id!r} references a missing route.")

    def validate_route(self, route_id: str) -> tuple[CableRoutingFinding, ...]:
        route = next((item for item in self.routes if item.id == route_id), None)
        if route is None:
            raise KeyError(route_id)
        findings: list[CableRoutingFinding] = []
        route_by_id = {item.id: item for item in self.routes}
        if not route.segments:
            findings.append(CableRoutingFinding("ROUTE_NO_SEGMENTS", ValidationSeverity.INCOMPLETE, "Route has no installation segments.", route.id))
        for segment in route.segments:
            if segment.length_m is None:
                findings.append(CableRoutingFinding("SEGMENT_LENGTH_UNKNOWN", ValidationSeverity.INCOMPLETE, f"Length is not defined for segment {segment.id}.", segment.id))
            system = next(item for item in self.systems if item.id == segment.installation_system_id)
            # Capacity is shared by every cable assigned to any route that
            # traverses this installation system, not just this route.
            system_cables: dict[str, CableRouteAssignment] = {}
            for assignment in self.assignments:
                assigned_route = route_by_id[assignment.route_id]
                if any(item.installation_system_id == system.id for item in assigned_route.segments):
                    system_cables[assignment.cable_id] = assignment
            for assignment in system_cables.values():
                if system.segregation_classes and assignment.service_class not in system.segregation_classes:
                    findings.append(CableRoutingFinding("SEGREGATION_NOT_PERMITTED", ValidationSeverity.ERROR, f"Installation system {system.tag} does not permit {assignment.service_class.value}.", segment.id))
            if system.usable_area_mm2 is None or system.fill_limit_ratio is None:
                findings.append(CableRoutingFinding("CAPACITY_DATA_INCOMPLETE", ValidationSeverity.INCOMPLETE, f"Capacity cannot be evaluated for {system.tag}; usable area and fill limit are required.", system.id))
            elif any(item.cable_outer_diameter_mm is None for item in system_cables.values()):
                findings.append(CableRoutingFinding("CABLE_DIAMETER_INCOMPLETE", ValidationSeverity.INCOMPLETE, f"Capacity cannot be evaluated for {system.tag}; one or more assigned cables lack an outer diameter.", system.id))
            else:
                from math import pi
                occupied = sum(
                    pi * (item.cable_outer_diameter_mm / 2.0) ** 2 * (item.installation_allowance_factor or 1.0)
                    for item in system_cables.values()
                )
                if occupied > system.usable_area_mm2 * system.fill_limit_ratio:
                    findings.append(CableRoutingFinding("TRAY_CAPACITY_EXCEEDED", ValidationSeverity.ERROR, f"Assigned cable occupancy exceeds the configured usable fill area for {system.tag}.", system.id))
        if route.route_length_m is None:
            findings.append(CableRoutingFinding("ROUTE_LENGTH_NOT_AUTHORITATIVE", ValidationSeverity.INCOMPLETE, "A route-derived length is unavailable because one or more segments lack approved geometric lengths.", route.id))
        return tuple(findings)


__all__ = [
    "InstallationSystemKind", "CableServiceClass", "LengthSource", "RoutingStatus",
    "ValidationSeverity", "InstallationPoint", "InstallationSystem",
    "InstallationRouteSegment", "PhysicalCableRoute", "CableRouteAssignment",
    "CableRoutingFinding", "PhysicalCableRoutingInventory",
]
