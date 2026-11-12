"""Generator adapter to the shared electrical-dynamics contracts."""
from __future__ import annotations

from core.model import EquipmentType

from plugins.electrical_dynamics.contracts import (
    DynamicsEquipmentKind, DynamicsModelCapability, DynamicsModelReadiness,
    MeasurementPointContract, normalize_measurement_points,
)
from .catalog import GeneratorFamily
from .instrumentation import generator_measurement_points

PLUGIN_ID = "gridforge.plugin.generator_dynamics"


def generator_measurement_contracts(family: GeneratorFamily) -> tuple[MeasurementPointContract, ...]:
    if not isinstance(family, GeneratorFamily):
        raise TypeError("family must be GeneratorFamily")
    return normalize_measurement_points(
        generator_measurement_points(family),
        equipment_kind=DynamicsEquipmentKind.GENERATOR,
        family_id=family.value,
    )


def generator_model_capability(family: GeneratorFamily) -> DynamicsModelCapability:
    if not isinstance(family, GeneratorFamily):
        raise TypeError("family must be GeneratorFamily")
    points = generator_measurement_contracts(family)
    return DynamicsModelCapability(
        plugin_id=PLUGIN_ID,
        equipment_kind=DynamicsEquipmentKind.GENERATOR,
        family_id=family.value,
        model_id=f"generator.{family.value}",
        readiness=DynamicsModelReadiness.HOST_SOLVER_REQUIRED,
        study_types=frozenset({"generator_transient"}),
        numerical_workloads=frozenset({"generator_transient_simulation"}),
        measurement_point_ids=frozenset(point.point_id for point in points),
    )


def generator_equipment_type():
    """Return the canonical Core equipment discriminator."""
    return EquipmentType.GENERATOR


__all__ = [
    "generator_measurement_contracts", "generator_model_capability",
    "generator_equipment_type",
]
