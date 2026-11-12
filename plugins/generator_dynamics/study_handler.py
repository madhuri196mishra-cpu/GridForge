"""Canonical StudyService adapter for generator transient execution.

Application StudyService owns scheduling, cancellation, provenance and result
persistence. The host composition supplies either the legacy sampled GGOV1
solver or the opt-in augmented DAE solver; this adapter owns neither a scheduler
nor an integrator.
"""
from __future__ import annotations

from typing import Any

from core.application.study import (
    StudyCancellationToken,
    StudyExecutionContext,
    StudyRequest,
)

from .ggov1_execution import GGOV1SampledExecutionAdapter
from .integrator_adapter import GeneratorAugmentedDAEAdapter


class GeneratorTransientStudyHandler:
    """Dispatch an explicitly selected generator transient execution contract.

    The default execution_mode is sampled_ggov1 for compatibility.
    augmented_dae is opt-in and requires the host's dedicated solver and
    adapter factory. Both host solvers return detached result evidence to
    StudyService; neither plugin path persists independently.
    """

    def __init__(
        self,
        solver: Any,
        sampled_adapter_factory: Any,
        *,
        augmented_solver: Any = None,
        augmented_adapter_factory: Any = None,
    ) -> None:
        if solver is None or not callable(getattr(solver, "solve_generator_transient", None)):
            raise TypeError("generator transient solver must expose solve_generator_transient(...)")
        if not callable(sampled_adapter_factory):
            raise TypeError("sampled_adapter_factory must be callable")
        if augmented_solver is not None and not callable(
            getattr(augmented_solver, "solve_generator_augmented_transient", None)
        ):
            raise TypeError(
                "augmented solver must expose solve_generator_augmented_transient(...)"
            )
        if augmented_adapter_factory is not None and not callable(augmented_adapter_factory):
            raise TypeError("augmented_adapter_factory must be callable")
        if (augmented_solver is None) != (augmented_adapter_factory is None):
            raise TypeError(
                "augmented solver and augmented adapter factory must be supplied together"
            )
        self._solver = solver
        self._sampled_adapter_factory = sampled_adapter_factory
        self._augmented_solver = augmented_solver
        self._augmented_adapter_factory = augmented_adapter_factory

    def __call__(
        self,
        request: StudyRequest,
        execution_context: StudyExecutionContext,
        cancellation: StudyCancellationToken,
    ) -> Any:
        if not isinstance(request, StudyRequest):
            raise TypeError("request must be StudyRequest")
        if not isinstance(execution_context, StudyExecutionContext):
            raise TypeError("execution_context must be StudyExecutionContext")
        if not isinstance(cancellation, StudyCancellationToken):
            raise TypeError("cancellation must be StudyCancellationToken")
        if request.study_type != "generator_transient":
            raise ValueError("GeneratorTransientStudyHandler only accepts generator_transient")
        if (
            request.project_id != execution_context.project_id
            or request.activation_generation != execution_context.activation_generation
            or request.source_revision != execution_context.source_revision
        ):
            raise ValueError("generator transient request/context provenance mismatch")
        if cancellation.cancelled:
            return None

        execution_mode = request.configuration.get("execution_mode", "sampled_ggov1")
        if execution_mode == "sampled_ggov1":
            sampled_adapter = self._sampled_adapter_factory(
                request=request, execution_context=execution_context
            )
            if not isinstance(sampled_adapter, GGOV1SampledExecutionAdapter):
                raise TypeError(
                    "sampled adapter factory must return GGOV1SampledExecutionAdapter"
                )
            if cancellation.cancelled:
                return None
            return self._solver.solve_generator_transient(
                request=request,
                execution_context=execution_context,
                cancellation=cancellation,
                sampled_adapter=sampled_adapter,
            )

        if execution_mode == "augmented_dae":
            if self._augmented_solver is None or self._augmented_adapter_factory is None:
                raise RuntimeError(
                    "augmented_dae was requested but the Application composition "
                    "does not provide its augmented solver and adapter factory"
                )
            augmented_adapter = self._augmented_adapter_factory(
                request=request, execution_context=execution_context
            )
            if not isinstance(augmented_adapter, GeneratorAugmentedDAEAdapter):
                raise TypeError(
                    "augmented adapter factory must return GeneratorAugmentedDAEAdapter"
                )
            if cancellation.cancelled:
                return None
            return self._augmented_solver.solve_generator_augmented_transient(
                request=request,
                execution_context=execution_context,
                cancellation=cancellation,
                augmented_adapter=augmented_adapter,
            )

        raise ValueError(
            f"unsupported generator transient execution_mode: {execution_mode!r}"
        )


__all__ = ["GeneratorTransientStudyHandler"]
