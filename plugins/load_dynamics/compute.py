"""Capability-gated execution policy for load engineering workloads."""
from __future__ import annotations

from collections.abc import Iterable

from core.numerics.backend import BackendCapability, BackendSelection, select_backend

LOAD_NUMERICAL_WORKLOADS = frozenset({
    "load_transient_simulation",
    "motor_starting_simulation",
    "load_profile_batch",
    "harmonic_batch_analysis",
    "load_parameter_estimation",
    "load_forecast_inference",
    "load_condition_inference",
})

NPU_INFERENCE_WORKLOADS = frozenset({
    "load_forecast_inference",
    "load_condition_inference",
})


def select_load_backend(
    capabilities: Iterable[BackendCapability],
    *,
    workload: str,
    workload_size: int = 1,
    precision: str = "float64",
    requested: str = "auto",
) -> BackendSelection:
    """Select only an implemented workload/backend pairing; NPU is inference-only."""
    if workload not in LOAD_NUMERICAL_WORKLOADS:
        raise ValueError(f"unsupported load workload: {workload!r}")
    items = tuple(capabilities)
    if any(not isinstance(item, BackendCapability) for item in items):
        raise TypeError("capabilities must contain BackendCapability values")
    eligible = tuple(
        item for item in items
        if item.kind != "npu" or workload in NPU_INFERENCE_WORKLOADS
    )
    if not eligible:
        raise ValueError("no eligible compute backend supplied for load workload")
    return select_backend(
        eligible,
        workload=workload,
        workload_size=workload_size,
        precision=precision,
        requested=requested,
    )


__all__ = ["LOAD_NUMERICAL_WORKLOADS", "NPU_INFERENCE_WORKLOADS", "select_load_backend"]
