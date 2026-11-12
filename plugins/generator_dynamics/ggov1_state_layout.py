"""Solver-owned contiguous state layout for one GGOV1 controller extension.

The layout is a pure adapter between the plugin block state records and a
host-owned global DAE vector. It owns no state, integrator, solver or clock.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np

from .ggov1_execution import GGOV1SampleDerivatives, GGOV1SampleStates


@dataclass(frozen=True, slots=True)
class GGOV1StateSlice:
    """Named half-open range in the host global dynamic-state vector."""

    name: str
    start: int
    stop: int

    def __post_init__(self) -> None:
        if not self.name or self.start < 0 or self.stop <= self.start:
            raise ValueError("GGOV1 state slice requires a name and non-empty positive range")

    @property
    def size(self) -> int:
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class GGOV1DynamicStateLayout:
    """Immutable ordered layout for the six continuous GGOV1 state groups."""

    slices: tuple[GGOV1StateSlice, ...]
    state_size: int

    _NAMES = ("pid", "acceleration", "temperature", "load_limiter", "actuator", "turbine")

    def __post_init__(self) -> None:
        slices = tuple(self.slices)
        if tuple(item.name for item in slices) != self._NAMES:
            raise ValueError("GGOV1 state slices must follow the canonical six-block order")
        cursor = 0
        for item in slices:
            if not isinstance(item, GGOV1StateSlice) or item.start != cursor:
                raise ValueError("GGOV1 state slices must be contiguous and ordered")
            cursor = item.stop
        if isinstance(self.state_size, bool) or self.state_size != cursor or cursor <= 0:
            raise ValueError("state_size must equal the final contiguous slice boundary")
        object.__setattr__(self, "slices", slices)

    @classmethod
    def from_initial_states(cls, states: GGOV1SampleStates) -> "GGOV1DynamicStateLayout":
        if not isinstance(states, GGOV1SampleStates):
            raise TypeError("states must be GGOV1SampleStates")
        cursor = 0
        slices = []
        for name in cls._NAMES:
            values = getattr(states, name)
            if not isinstance(values, tuple) or not values:
                raise ValueError(f"{name} initial state must be a non-empty tuple")
            if any(not math.isfinite(float(value)) for value in values):
                raise ValueError(f"{name} initial state contains non-finite values")
            stop = cursor + len(values)
            slices.append(GGOV1StateSlice(name, cursor, stop))
            cursor = stop
        return cls(tuple(slices), cursor)

    def pack(self, states: GGOV1SampleStates) -> np.ndarray:
        if not isinstance(states, GGOV1SampleStates):
            raise TypeError("states must be GGOV1SampleStates")
        values = []
        for item in self.slices:
            block = tuple(getattr(states, item.name))
            if len(block) != item.size:
                raise ValueError(f"{item.name} state size does not match the frozen layout")
            values.extend(float(value) for value in block)
        vector = np.asarray(values, dtype=float)
        if vector.shape != (self.state_size,) or not np.all(np.isfinite(vector)):
            raise ValueError("packed GGOV1 state vector is invalid")
        return vector

    def unpack(self, vector: np.ndarray) -> GGOV1SampleStates:
        values = np.asarray(vector, dtype=float)
        if values.ndim != 1 or values.size != self.state_size or not np.all(np.isfinite(values)):
            raise ValueError("global state vector does not match the GGOV1 layout")
        return GGOV1SampleStates(**{
            item.name: tuple(float(value) for value in values[item.start:item.stop])
            for item in self.slices
        })

    def pack_derivatives(self, derivatives: GGOV1SampleDerivatives) -> np.ndarray:
        if not isinstance(derivatives, GGOV1SampleDerivatives):
            raise TypeError("derivatives must be GGOV1SampleDerivatives")
        values = []
        for item in self.slices:
            block = tuple(getattr(derivatives, item.name))
            if len(block) != item.size:
                raise ValueError(f"{item.name} derivative size does not match the frozen layout")
            values.extend(float(value) for value in block)
        vector = np.asarray(values, dtype=float)
        if vector.shape != (self.state_size,) or not np.all(np.isfinite(vector)):
            raise ValueError("packed GGOV1 derivative vector is invalid")
        return vector


__all__ = ["GGOV1StateSlice", "GGOV1DynamicStateLayout"]
