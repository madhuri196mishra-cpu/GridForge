"""Detached transient-study fault state."""

from __future__ import annotations

from dataclasses import dataclass

from core.solver.short_circuit.fault_types import FaultType


@dataclass(frozen=True, slots=True)
class TransientFault:
    """Immutable fault intent carried by detached transient runtime state."""

    fault_type: FaultType
    bus_id: str
    impedance: complex = 0.0j

    def __post_init__(self) -> None:
        object.__setattr__(self, "fault_type", FaultType.from_value(self.fault_type))
        bus_id = str(self.bus_id).strip()
        if not bus_id:
            raise ValueError("Transient fault bus_id must be non-empty.")
        object.__setattr__(self, "bus_id", bus_id)
        object.__setattr__(self, "impedance", complex(self.impedance))
        if self.fault_type is not FaultType.THREE_PHASE:
            raise ValueError(
                "Transient algebraic fault coupling currently supports only THREE_PHASE faults."
            )


__all__ = ["TransientFault"]
