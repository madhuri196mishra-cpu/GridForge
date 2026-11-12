"""Transformer engineering and dynamics plugin capabilities.

The Core Transformer remains authoritative for equipment identity and
connectivity. This package contributes optional capability profiles,
instrumentation metadata and telemetry contracts.
"""
from .base import TransformerPlugin
from .winding import Winding
from .vector_group import VectorGroup
from .grounding import Grounding
from .tap_changer import TapChanger
from .equivalent import TransformerEquivalent
from .profile import TransformerProfile, TransformerFamily, InsulationSystem, CoolingMethod, validate_transformer_profile_windings
from .measurement_binding import (
    TransformerMeasurementBinding,
    resolve_transformer_measurement_bindings,
)
from .rtd import (\n    RTDConfiguration, RTDCommissioningEvidence, RTDObservation,\n    resistance_to_iec60751_temperature, adapt_rtd_observation,\n)\nfrom .telemetry import (
    ChannelDefinition, MeasurementQuality, SignalKind, TelemetrySample,
    TelemetrySnapshot, TransformerQuantity, build_transformer_channel_catalogue,
    build_transformer_channel_catalogue_for_core,
    build_transformer_measurement_source_interfaces, validate_transformer_snapshot,
    to_core_measurement_quality, measurement_availability_for_quality,
    publish_transformer_sensor_snapshot, publish_active_transformer_sensor_snapshot,
)

__all__ = [
    "TransformerPlugin", "Winding", "VectorGroup", "Grounding", "TapChanger",
    "TransformerEquivalent", "TransformerProfile", "TransformerFamily",
    "InsulationSystem", "CoolingMethod", "validate_transformer_profile_windings", "ChannelDefinition",
    "MeasurementQuality", "SignalKind", "TelemetrySample", "TelemetrySnapshot",
    "TransformerQuantity", "build_transformer_channel_catalogue", "build_transformer_channel_catalogue_for_core", "build_transformer_measurement_source_interfaces",
    "validate_transformer_snapshot", "to_core_measurement_quality",
    "measurement_availability_for_quality", "publish_transformer_sensor_snapshot", "publish_active_transformer_sensor_snapshot",
    "TransformerMeasurementBinding", "resolve_transformer_measurement_bindings",
]
__version__ = "0.2.0"
