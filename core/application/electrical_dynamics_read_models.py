"""Application-owned, thread-safe motor simulation projections.

This module is deliberately independent of plugin lifecycle and UI code.
Plugins publish detached study results into this Application-owned store; plugin
shutdown must never clear it.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

_MAX_STUDY_HISTORY = 4096
import numpy as np

def _freeze_diagnostic(value: Any) -> Any:
    """Detach and recursively freeze diagnostic metadata for read consumers."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_diagnostic(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_diagnostic(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_diagnostic(item) for item in value)
    return value


@dataclass(frozen=True)
class SimulationSummary:
    motor_id: str
    status: str
    sample_count: int
    sample_period_s: float
    channels: tuple[str, ...]
    fault: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))


class MotorSimulationReadModelStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._summaries: dict[str, SimulationSummary] = {}
        self._channels: dict[tuple[str, str], np.ndarray] = {}
        self._study_motor_ids: dict[str, str] = {}
        self._study_orders_by_motor: dict[str, int] = {}
    def publish(self, motor_id: str, channels: Mapping[str, Any], sample_period_s: float, status: str = "completed", fault: str | None = None, *, study_id: str | None = None, study_order: int | None = None, diagnostics: Mapping[str, Any] | None = None) -> bool:
        if not isinstance(motor_id, str) or not motor_id.strip():
            raise ValueError("motor_id must be a non-empty string")
        motor_id = motor_id.strip()
        if isinstance(sample_period_s, bool) or not isinstance(sample_period_s, (int, float)):
            raise TypeError("sample_period_s must be numeric")
        if not np.isfinite(float(sample_period_s)) or float(sample_period_s) <= 0:
            raise ValueError("sample_period_s must be finite and positive")
        if not isinstance(channels, Mapping):
            raise TypeError("channels must be a mapping")
        if diagnostics is not None and not isinstance(diagnostics, Mapping):
            raise TypeError("diagnostics must be a mapping or None")
        if diagnostics is not None and any(
            not isinstance(key, str) or not key.strip() for key in diagnostics
        ):
            raise ValueError("diagnostic keys must be non-empty strings")
        frozen_diagnostics = _freeze_diagnostic(diagnostics or {})
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be a non-empty string")
        if fault is not None and not isinstance(fault, str):
            raise TypeError("fault must be a string or None")
        if study_id is not None and (not isinstance(study_id, str) or not study_id.strip()):
            raise ValueError("study_id must be a non-empty string when provided")
        if study_id is not None:
            study_id = study_id.strip()
        if study_order is not None and (
            isinstance(study_order, bool) or not isinstance(study_order, int) or study_order < 1
        ):
            raise ValueError("study_order must be a positive integer when provided")

        frozen: dict[tuple[str, str], np.ndarray] = {}
        for name, values in channels.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("channel names must be non-empty strings")
            raw = np.asarray(values)
            if not np.issubdtype(raw.dtype, np.number) or not np.isrealobj(raw):
                raise TypeError(f"channel {name!r} must contain real numeric values")
            arr = np.asarray(raw, dtype=np.float64).reshape(-1).copy()
            if not np.isfinite(arr).all():
                raise ValueError(f"channel {name!r} contains non-finite values")
            arr.setflags(write=False)
            frozen[(motor_id, name)] = arr

        summary = SimulationSummary(
            motor_id=motor_id,
            status=status.strip(),
            sample_count=max((arr.size for arr in frozen.values()), default=0),
            sample_period_s=float(sample_period_s),
            channels=tuple(sorted(name for _, name in frozen)),
            fault=fault,
            diagnostics=frozen_diagnostics,
        )
        with self._lock:
            # Study-start order, not completion order, prevents an older long
            # running study from overwriting a newer study that finished first.
            previous_order = self._study_orders_by_motor.get(motor_id)
            if study_order is not None and previous_order is not None and study_order <= previous_order:
                return False
            # Replace channels and summary atomically so diagnostics can never
            # describe a different trajectory than the visible channel vectors.
            stale_keys = [key for key in self._channels if key[0] == motor_id]
            for key in stale_keys:
                del self._channels[key]
            self._channels.update(frozen)
            self._summaries[motor_id] = summary
            if study_order is not None:
                self._study_orders_by_motor[motor_id] = study_order
            if study_id is not None:
                self._study_motor_ids[study_id] = motor_id
                # Keep study-to-motor lookup useful without unbounded growth
                # during long-running sessions.
                while len(self._study_motor_ids) > _MAX_STUDY_HISTORY:
                    oldest_study_id = next(iter(self._study_motor_ids))
                    del self._study_motor_ids[oldest_study_id]
        return True

    def clear(self) -> None:
        """Explicitly clear projections only during Application-owned disposal."""
        with self._lock:
            self._channels.clear()
            self._summaries.clear()
            self._study_motor_ids.clear()
            self._study_orders_by_motor.clear()

    def get_motor_id_for_study(self, study_id: str) -> str | None:
        """Resolve a successful StudyCompleted event's ID to its motor result."""
        if not isinstance(study_id, str) or not study_id.strip():
            return None
        with self._lock:
            return self._study_motor_ids.get(study_id.strip())
    def get_simulation_summary(self, motor_id: str) -> SimulationSummary | None:
        with self._lock: return self._summaries.get(motor_id)
    def get_raw_channel_vector(self, motor_id: str, channel: str) -> np.ndarray | None:
        with self._lock:
            arr = self._channels.get((motor_id, channel))
            if arr is None: return None
            # Read-only backing array; callers cannot mutate cached simulation results.
            view = arr.view(); view.setflags(write=False); return view
    def snapshot(self) -> tuple[SimulationSummary, ...]:
        with self._lock: return tuple(self._summaries.values())


__all__ = ["SimulationSummary", "MotorSimulationReadModelStore"]
