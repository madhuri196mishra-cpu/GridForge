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
from .profile import TransformerProfile, TransformerFamily, InsulationSystem, CoolingMethod
from .measurement_binding import (
    TransformerMeasurementBinding,
    resolve_transformer_measurement_bindings,
)
from .telemetry import (
    ChannelDefinition, MeasurementQuality, SignalKind, TelemetrySample,
    TelemetrySnapshot, TransformerQuantity, build_transformer_channel_catalogue,
)

__all__ = [
    "TransformerPlugin", "Winding", "VectorGroup", "Grounding", "TapChanger",
    "TransformerEquivalent", "TransformerProfile", "TransformerFamily",
    "InsulationSystem", "CoolingMethod", "ChannelDefinition",
    "MeasurementQuality", "SignalKind", "TelemetrySample", "TelemetrySnapshot",
    "TransformerQuantity", "build_transformer_channel_catalogue",
    "TransformerMeasurementBinding", "resolve_transformer_measurement_bindings",
]
__version__ = "0.2.0"
