"""Transformer family and construction capability profile."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class TransformerFamily(str, Enum):
    POWER="power"; DISTRIBUTION="distribution"; AUTOTRANSFORMER="autotransformer"
    INSTRUMENT_CURRENT="instrument_current"; INSTRUMENT_POTENTIAL="instrument_potential"
    ISOLATION="isolation"; GROUNDING="grounding"; ZIGZAG="zigzag"
    PHASE_SHIFTING="phase_shifting"; FURNACE="furnace"; RECTIFIER="rectifier"
    CONVERTER="converter"; TRACTION="traction"; HIGH_FREQUENCY="high_frequency"
    TEST="test"; SPECIAL_PURPOSE="special_purpose"

class InsulationSystem(str, Enum):
    MINERAL_OIL="mineral_oil"; NATURAL_ESTER="natural_ester"
    SYNTHETIC_ESTER="synthetic_ester"; SILICONE_FLUID="silicone_fluid"
    DRY_AIR="dry_air"; CAST_RESIN="cast_resin"; GAS_INSULATED="gas_insulated"; OTHER="other"

class CoolingMethod(str, Enum):
    AN="AN"; AF="AF"; ONAN="ONAN"; ONAF="ONAF"; OFAF="OFAF"; OFWF="OFWF"
    ODAF="ODAF"; ODWF="ODWF"; KNAN="KNAN"; KNAF="KNAF"
    NATURAL="natural"; FORCED_AIR="forced_air"; FORCED_LIQUID="forced_liquid"; OTHER="other"

@dataclass(frozen=True, slots=True)
class TransformerProfile:
    """Optional construction/capability data associated with one Core transformer."""
    transformer_id: str
    family: TransformerFamily
    winding_ids: tuple[str, ...]
    insulation: InsulationSystem
    cooling: CoolingMethod
    rated_frequency_hz: float
    minimum_frequency_hz: float | None = None
    maximum_frequency_hz: float | None = None
    has_on_load_tap_changer: bool = False
    has_off_circuit_tap_changer: bool = False
    has_phase_shifting_capability: bool = False
    has_conservator: bool | None = None
    has_gas_relay: bool | None = None
    has_online_dga: bool = False
    has_partial_discharge_monitoring: bool = False
    has_fiber_optic_hotspot_sensors: bool = False
    converter_interface: str | None = None

    def __post_init__(self) -> None:
        if not self.transformer_id.strip():
            raise ValueError("Transformer profile requires a Core transformer id.")
        windings = tuple(str(item).strip() for item in self.winding_ids)
        if not windings or any(not item for item in windings):
            raise ValueError("At least one non-empty winding id is required.")
        if len(set(windings)) != len(windings):
            raise ValueError("Transformer winding ids must be unique.")
        object.__setattr__(self, "winding_ids", windings)
        if self.rated_frequency_hz <= 0:
            raise ValueError("Rated frequency must be positive.")
        low, high = self.minimum_frequency_hz, self.maximum_frequency_hz
        if low is not None and low <= 0 or high is not None and high <= 0:
            raise ValueError("Operating frequency limits must be positive.")
        if low is not None and high is not None and low > high:
            raise ValueError("Minimum frequency cannot exceed maximum frequency.")
        if low is not None and self.rated_frequency_hz < low:
            raise ValueError("Rated frequency is below the operating range.")
        if high is not None and self.rated_frequency_hz > high:
            raise ValueError("Rated frequency is above the operating range.")
        if self.family is TransformerFamily.PHASE_SHIFTING and not self.has_phase_shifting_capability:
            raise ValueError("Phase-shifting family requires phase-shifting capability.")
        if self.family is TransformerFamily.CONVERTER and not self.converter_interface:
            raise ValueError("Converter family requires a converter interface.")

    @property
    def winding_count(self) -> int:
        return len(self.winding_ids)

    @property
    def is_multiwinding(self) -> bool:
        return self.winding_count > 2

    @property
    def is_liquid_immersed(self) -> bool:
        return self.insulation in {
            InsulationSystem.MINERAL_OIL, InsulationSystem.NATURAL_ESTER,
            InsulationSystem.SYNTHETIC_ESTER, InsulationSystem.SILICONE_FLUID,
        }
