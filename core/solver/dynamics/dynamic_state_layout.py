"""Composable global state layout for classical machines and dynamic extensions.

This value contract preserves the existing MultiMachineSystem vector as the
leading, unchanged machine segment. Extension-component states are appended in
explicit registration order. It does not register components with a solver or
change DAESolver's machine-only state contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from .component_contract import validate_dynamic_state_component


class DynamicSystemLayoutError(ValueError):
    """Raised when a composed dynamic-system state layout is inconsistent."""


@dataclass(frozen=True, slots=True)
class DynamicStateBlockSlice:
    """One named owner block in the composed global state vector."""

    owner_kind: str
    owner_id: str
    start: int
    stop: int

    def __post_init__(self) -> None:
        if self.owner_kind not in {"machine", "component"}:
            raise DynamicSystemLayoutError("owner_kind must be 'machine' or 'component'")
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise DynamicSystemLayoutError("owner_id must be a non-empty stable identity")
        if type(self.start) is not int or type(self.stop) is not int:
            raise DynamicSystemLayoutError("state slice boundaries must be integers")
        if self.start < 0 or self.stop <= self.start:
            raise DynamicSystemLayoutError("state slices must be non-empty and non-negative")

    @property
    def size(self) -> int:
        return self.stop - self.start


@dataclass(frozen=True, slots=True)
class DynamicSystemStateLayout:
    """Frozen machine-first layout with extension states appended contiguously.

    Machine slices retain their existing MultiMachineSystem offsets. Component
    slices follow them in the supplied component order. Packing and derivative
    assembly are pure vector transformations; no component is evaluated here.
    """

    blocks: tuple[DynamicStateBlockSlice, ...]
    machine_state_size: int
    state_size: int

    def __post_init__(self) -> None:
        blocks = tuple(self.blocks)
        if type(self.machine_state_size) is not int or self.machine_state_size < 0:
            raise DynamicSystemLayoutError("machine_state_size must be a non-negative integer")
        if type(self.state_size) is not int or self.state_size <= 0:
            raise DynamicSystemLayoutError("state_size must be a positive integer")
        cursor = 0
        seen: set[tuple[str, str]] = set()
        saw_component = False
        for block in blocks:
            if not isinstance(block, DynamicStateBlockSlice):
                raise DynamicSystemLayoutError("blocks must contain DynamicStateBlockSlice values")
            identity = (block.owner_kind, block.owner_id)
            if identity in seen:
                raise DynamicSystemLayoutError(f"duplicate state owner {identity!r}")
            seen.add(identity)
            if block.start != cursor:
                raise DynamicSystemLayoutError("global state blocks must be contiguous and ordered")
            if block.owner_kind == "component":
                saw_component = True
            elif saw_component:
                raise DynamicSystemLayoutError("machine state blocks must precede extension components")
            cursor = block.stop
        if cursor != self.state_size:
            raise DynamicSystemLayoutError("state_size must equal the final block boundary")
        if any(block.stop > self.machine_state_size for block in blocks if block.owner_kind == "machine"):
            raise DynamicSystemLayoutError("machine slices cannot extend beyond machine_state_size")
        if any(block.start < self.machine_state_size for block in blocks if block.owner_kind == "component"):
            raise DynamicSystemLayoutError("component slices must begin after the machine state segment")
        machine_end = max(
            (block.stop for block in blocks if block.owner_kind == "machine"),
            default=0,
        )
        if machine_end != self.machine_state_size:
            raise DynamicSystemLayoutError("machine blocks must cover the complete machine state segment")
        object.__setattr__(self, "blocks", blocks)

    @classmethod
    def compose(
        cls,
        machine_slices: Sequence[object],
        components: Sequence[object],
    ) -> "DynamicSystemStateLayout":
        """Compose existing machine offsets with validated extension dimensions."""
        blocks: list[DynamicStateBlockSlice] = []
        cursor = 0
        machine_ids: set[str] = set()
        for item in machine_slices:
            machine_id = getattr(item, "machine_id", None)
            start = getattr(item, "start", None)
            stop = getattr(item, "stop", None)
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise DynamicSystemLayoutError("machine slices require stable machine_id values")
            if machine_id in machine_ids:
                raise DynamicSystemLayoutError(f"duplicate machine state owner {machine_id!r}")
            if type(start) is not int or type(stop) is not int or start != cursor or stop <= start:
                raise DynamicSystemLayoutError("machine slices must be positive, contiguous and in registration order")
            machine_ids.add(machine_id)
            blocks.append(DynamicStateBlockSlice("machine", machine_id, start, stop))
            cursor = stop
        machine_state_size = cursor

        component_ids: set[str] = set()
        for component in components:
            try:
                component_id, state_size = validate_dynamic_state_component(component)
            except (TypeError, ValueError) as exc:
                raise DynamicSystemLayoutError(f"invalid dynamic component: {exc}") from exc
            if component_id in component_ids:
                raise DynamicSystemLayoutError(f"duplicate dynamic component id {component_id!r}")
            component_ids.add(component_id)
            blocks.append(DynamicStateBlockSlice("component", component_id, cursor, cursor + state_size))
            cursor += state_size
        if cursor <= 0:
            raise DynamicSystemLayoutError("a composed layout must contain at least one state")
        return cls(tuple(blocks), machine_state_size, cursor)

    @property
    def machine_blocks(self) -> tuple[DynamicStateBlockSlice, ...]:
        return tuple(block for block in self.blocks if block.owner_kind == "machine")

    @property
    def component_blocks(self) -> tuple[DynamicStateBlockSlice, ...]:
        return tuple(block for block in self.blocks if block.owner_kind == "component")

    def pack(
        self,
        machine_state: Sequence[float] | np.ndarray,
        component_states: Mapping[str, Sequence[float] | np.ndarray],
    ) -> np.ndarray:
        """Pack the unchanged machine vector followed by every component state."""
        machine = self._vector(machine_state, self.machine_state_size, "machine_state")
        expected = {block.owner_id for block in self.component_blocks}
        if set(component_states) != expected:
            raise DynamicSystemLayoutError("component state identities must match the composed layout exactly")
        result = np.empty(self.state_size, dtype=float)
        result[: self.machine_state_size] = machine
        for block in self.component_blocks:
            local = self._vector(component_states[block.owner_id], block.size, block.owner_id)
            result[block.start:block.stop] = local
        return result

    def unpack(
        self,
        state: Sequence[float] | np.ndarray,
    ) -> tuple[np.ndarray, Mapping[str, np.ndarray]]:
        """Split a composed vector into machine and detached component snapshots."""
        vector = self._vector(state, self.state_size, "global_state")
        machine = vector[: self.machine_state_size].copy()
        machine.setflags(write=False)
        components: dict[str, np.ndarray] = {}
        for block in self.component_blocks:
            local = vector[block.start:block.stop].copy()
            local.setflags(write=False)
            components[block.owner_id] = local
        return machine, MappingProxyType(components)

    def assemble_derivatives(
        self,
        machine_derivatives: Sequence[float] | np.ndarray,
        component_derivatives: Mapping[str, Sequence[float] | np.ndarray],
    ) -> np.ndarray:
        """Assemble derivative blocks using the exact same frozen offsets."""
        return self.pack(machine_derivatives, component_derivatives)

    @staticmethod
    def _vector(values: Sequence[float] | np.ndarray, size: int, name: str) -> np.ndarray:
        try:
            vector = np.asarray(values, dtype=float)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DynamicSystemLayoutError(f"{name} must be a finite numeric vector") from exc
        if vector.ndim != 1 or vector.size != size or not np.all(np.isfinite(vector)):
            raise DynamicSystemLayoutError(f"{name} must be a finite vector of length {size}")
        return vector


__all__ = [
    "DynamicSystemLayoutError",
    "DynamicStateBlockSlice",
    "DynamicSystemStateLayout",
]
