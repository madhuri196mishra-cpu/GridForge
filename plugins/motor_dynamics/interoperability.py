"""Motor adapter to the shared electrical-dynamics contracts."""
from __future__ import annotations

from core.model import EquipmentType

from plugins.electrical_dynamics.contracts import (
    DynamicsEquipmentKind, DynamicsModelCapability, DynamicsModelReadiness,
    MeasurementPointContract, normalize_measurement_points,
)
from .catalog import MotorFamily, motor_family_profile
from .instrumentation import motor_measurement_points

PLUGIN_ID = "gridforge.plugin.motor_dynamics"


def motor_measurement_contracts(family: MotorFamily) -> tuple[MeasurementPointContract, ...]:
    if not isinstance(family, MotorFamily):
        raise TypeError("family must be MotorFamily")
    return normalize_measurement_points(
        motor_measurement_points(family),
        equipment_kind=DynamicsEquipmentKind.MOTOR,
        family_id=family.value,
    )


def motor_model_capability(family: MotorFamily) -> DynamicsModelCapability:
    if not isinstance(family, MotorFamily):
        raise TypeError("family must be MotorFamily")
    profile = motor_family_profile(family)
    points = motor_measurement_contracts(family)
    experimental = profile.dynamic_model is not None
    return DynamicsModelCapability(
        plugin_id=PLUGIN_ID,
        equipment_kind=DynamicsEquipmentKind.MOTOR,
        family_id=family.value,
        model_id=f"motor.{profile.dynamic_model or 'catalogue_only'}.{family.value}",
        readiness=(
            DynamicsModelReadiness.EXPERIMENTAL
            if experimental else DynamicsModelReadiness.CATALOGUE_ONLY
        ),
        study_types=frozenset({"motor_transient"}) if experimental else frozenset(),
        numerical_workloads=frozenset({"motor_transient_simulation"}) if experimental else frozenset(),
        measurement_point_ids=frozenset(point.point_id for point in points),
    )


def motor_equipment_type():
    """Return the canonical Core equipment discriminator."""
    return EquipmentType.MOTOR


__all__ = ["motor_measurement_contracts", "motor_model_capability", "motor_equipment_type"]
