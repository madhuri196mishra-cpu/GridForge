"""Load adapter to the shared electrical-dynamics contracts."""
from __future__ import annotations

from core.model import EquipmentType

from plugins.electrical_dynamics.contracts import (
    DynamicsEquipmentKind, DynamicsModelCapability, DynamicsModelReadiness,
    MeasurementPointContract, normalize_measurement_points,
)
from .catalog import LoadFamily
from .instrumentation import load_measurement_points

PLUGIN_ID = "gridforge.plugin.load_dynamics"


def load_measurement_contracts(family: LoadFamily) -> tuple[MeasurementPointContract, ...]:
    if not isinstance(family, LoadFamily):
        raise TypeError("family must be LoadFamily")
    return normalize_measurement_points(
        load_measurement_points(family),
        equipment_kind=DynamicsEquipmentKind.LOAD,
        family_id=family.value,
    )


def load_model_capability(family: LoadFamily) -> DynamicsModelCapability:
    if not isinstance(family, LoadFamily):
        raise TypeError("family must be LoadFamily")
    points = load_measurement_contracts(family)
    # The initial executable shared electrical representation is ZIP demand.
    # Family-specific transient solvers are not advertised by this catalogue.
    return DynamicsModelCapability(
        plugin_id=PLUGIN_ID,
        equipment_kind=DynamicsEquipmentKind.LOAD,
        family_id=family.value,
        model_id=f"load.zip.{family.value}",
        readiness=DynamicsModelReadiness.EXPERIMENTAL,
        study_types=frozenset(),
        numerical_workloads=frozenset(),
        measurement_point_ids=frozenset(point.point_id for point in points),
    )


def load_equipment_type():
    """Return the canonical Core equipment discriminator."""
    return EquipmentType.LOAD


__all__ = ["load_measurement_contracts", "load_model_capability", "load_equipment_type"]
