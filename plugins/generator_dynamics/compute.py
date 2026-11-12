"""Capability-gated compute policy for generator-dynamics workloads.

No hardware probing or accelerator import occurs here. Application/runtime
adapters supply capability facts to GridForge's shared numerical policy.
NPU is deliberately limited to inference workloads; protection decisions and
authoritative physical simulation are never delegated to AI inference.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.numerics.backend import BackendCapability, BackendSelection, select_backend

GENERATOR_WORKLOADS = frozenset({
    "generator_transient_simulation",
    "generator_batch_simulation",
    "generator_parameter_estimation",
    "generator_waveform_analysis",
    "generator_condition_inference",
    "generator_anomaly_inference",
})

NPU_INFERENCE_WORKLOADS = frozenset({
    "generator_condition_inference",
    "generator_anomaly_inference",
})


def select_generator_backend(
    capabilities: Iterable[BackendCapability],
    *,
    workload: str,
    workload_size: int = 1,
    precision: str = "float64",
    requested: str = "auto",
) -> BackendSelection:
    """Select only a backend compatible with the declared generator workload."""
    if workload not in GENERATOR_WORKLOADS:
        raise ValueError(f"unsupported generator workload: {workload!r}")
    items = tuple(capabilities)
    if any(not isinstance(item, BackendCapability) for item in items):
        raise TypeError("capabilities must contain BackendCapability values")
    # An NPU may not advertise itself as a physical transient solver.
    # GPU numerical workloads remain subject to explicit runtime capabilities.
    filtered = tuple(
        item for item in items
        if item.kind != "npu" or workload in NPU_INFERENCE_WORKLOADS
    )
    if not filtered:
        raise ValueError("no eligible compute backend was supplied for this workload")
    return select_backend(
        filtered,
        workload=workload,
        workload_size=workload_size,
        precision=precision,
        requested=requested,
    )


__all__ = ["GENERATOR_WORKLOADS", "NPU_INFERENCE_WORKLOADS", "select_generator_backend"]
