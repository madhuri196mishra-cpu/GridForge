"""Public API for the authoritative GridForge V2 Model Layer."""

from __future__ import annotations

from .base import ElectricalObject
from .terminal import Terminal
from .injection import Injection
from .bus import Bus
from .branch import Branch
from .conductor import ConductorRole, ConductorMaterial, PhysicalConductor, BranchConductorSystem
from .physical_conductor_construction import PhysicalConductorPath, PhysicalConductorConstruction
from .transformer_physical_conductor_construction import TransformerWindingConductorConstruction, TransformerPhysicalConductorConstruction
from .line import Line
from .cable import Cable
from .transformer import ImpedanceBasis, TransformerCoreConstruction, TransformerWindingConnection, TransformerWinding, Transformer
from .transformer_zero_sequence_test_data import TransformerZeroSequenceTwoWindingTestData
from .transformer_zero_sequence_multiport_evidence import TransformerZeroSequenceMultiportEvidence, TransformerDeltaLoopState, transformer_multiport_fingerprint, transformer_delta_loop_fingerprint
from .transformer_zero_sequence_contracts import (
    TransformerVectorGroupConnection,
    TransformerWindingPolarity,
    ZeroSequenceTestEnergizedWinding,
    ZeroSequenceTestOtherWindingCondition,
    TransformerVectorGroupConfiguration,
    TransformerZeroSequenceTestConfiguration,
    validate_zero_sequence_test_applicability,
)
from .switch import Switch
from .breaker import Breaker
from .disconnector import Disconnector
from .fuse import Fuse
from .load import Load
from .generator import Generator
from .synchronous_machine import SynchronousMachine, SyncMachine
from .motor import Motor
from .shunt import Shunt
from .capacitor import Capacitor
from .reactor import Reactor
from .solar import Solar
from .battery import Battery
from .grid import Grid
from .ct import CTPolarity, CurrentTransformer
from .pt import PT
PotentialTransformer = PT
from .cvt import CVT
from .relay import Relay

# Relay is a physical Core model and participates in the canonical identity
# contract without introducing a second identity implementation. The mature
# Relay constructor is preserved, while its public identity is made immutable
# and its canonical model TYPE is explicit.
Relay.TYPE = "RELAY"

def _relay_id_get(self):
    return self._id

def _relay_id_set(self, value):
    if hasattr(self, "_id"):
        raise AttributeError("Relay.id is immutable after construction.")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Relay.id must be a non-empty string.")
    self._id = value.strip()

Relay.id = property(_relay_id_get, _relay_id_set)
Relay.element_type = property(lambda self: "RELAY")
ElectricalObject.register(Relay)
from .endpoint_reference import EndpointReference, EndpointReferenceKind, EquipmentType

__all__ = (
    "ElectricalObject", "Terminal", "Injection", "PhysicalConductorPath", "PhysicalConductorConstruction", "TransformerWindingConductorConstruction", "TransformerPhysicalConductorConstruction", "Bus", "Branch", "ConductorRole", "ConductorMaterial", "PhysicalConductor", "BranchConductorSystem", "Line", "Cable", "ImpedanceBasis", "TransformerCoreConstruction", "TransformerWindingConnection", "TransformerWinding", "TransformerVectorGroupConnection", "TransformerWindingPolarity", "ZeroSequenceTestEnergizedWinding", "ZeroSequenceTestOtherWindingCondition", "TransformerVectorGroupConfiguration", "TransformerZeroSequenceTestConfiguration", "TransformerZeroSequenceTwoWindingTestData", "TransformerZeroSequenceMultiportEvidence", "TransformerDeltaLoopState", "transformer_multiport_fingerprint", "transformer_delta_loop_fingerprint", "validate_zero_sequence_test_applicability", "Transformer", "Switch",
    "Breaker", "Disconnector", "Fuse", "Load", "Generator", "SynchronousMachine", "SyncMachine", "Motor",
    "Shunt", "Capacitor", "Reactor", "Solar", "Battery", "Grid", "CTPolarity", "CurrentTransformer", "PT",
    "PotentialTransformer", "CVT", "Relay", "EndpointReference", "EndpointReferenceKind", "EquipmentType",
)

from .instrument_wiring import (
    WiringTerminalReference, WiringConductorRole, SignalKind, CarrierKind,
    CableAssembly, CableCore, PanelWire, SignalDefinition,
    SignalCarrierBinding, PhysicalConnectionHop, WiringTagSeries,
    WiringTagPolicy, InstrumentationWiringInventory,
)
from .temperature_instrument import (
    TemperatureService, TemperatureSensorTechnology, TemperatureInputMode,
    TemperatureConsumerKind, TemperatureThermocoupleCableClass,
    TemperatureThermocoupleCableSpec, TemperatureReceiverInputKind,
    TemperatureReceiverInput, TemperatureTerminalMapping,
    TemperatureInstrument, TemperatureInterfaceDevice,
    TemperatureAlarmTripBinding, TemperatureInstrumentInventory,
)
__all__ += [
    "WiringTerminalReference", "WiringConductorRole", "SignalKind", "CarrierKind",
    "CableAssembly", "CableCore", "PanelWire", "SignalDefinition",
    "SignalCarrierBinding", "PhysicalConnectionHop", "WiringTagSeries",
    "WiringTagPolicy", "InstrumentationWiringInventory", "TemperatureService",
    "TemperatureSensorTechnology", "TemperatureInputMode", "TemperatureConsumerKind",
    "TemperatureThermocoupleCableClass", "TemperatureThermocoupleCableSpec",
    "TemperatureReceiverInputKind", "TemperatureReceiverInput",
    "TemperatureTerminalMapping", "TemperatureInstrument", "TemperatureInterfaceDevice",
    "TemperatureAlarmTripBinding", "TemperatureInstrumentInventory",
]
