"""Canonical StudyService adapter for sampled generator transient execution.

The Application StudyService owns scheduling, cancellation, provenance and
result persistence. The host composition supplies the numerical transient
solver; this plugin adapter never creates a second scheduler or integrator.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.application.study import (
    StudyCancellationToken,
    StudyExecutionContext,
    StudyRequest,
)

from .ggov1_execution import GGOV1SampledExecutionAdapter


class GeneratorTransientStudyHandler:
    """Delegate a provenance-bound generator study to the host solver.

    The injected solver must expose solve_generator_transient(request=...,
    execution_context=..., cancellation=..., sampled_adapter=...). Its
    implementation owns integration, accepted/rejected-step policy, rollback
    and partial-result evidence. This adapter only connects it to StudyService.
    """

    def __init__(self, solver: Any, sampled_adapter_factory: Any) -> None:
        if solver is None or not callable(getattr(solver, "solve_generator_transient", None)):
            raise TypeError("generator transient solver must expose solve_generator_transient(...)")
        if not callable(sampled_adapter_factory):
            raise TypeError("sampled_adapter_factory must be callable")
        self._solver = solver
        self._sampled_adapter_factory = sampled_adapter_factory

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
        sampled_adapter = self._sampled_adapter_factory(
            request=request, execution_context=execution_context
        )
        if not isinstance(sampled_adapter, GGOV1SampledExecutionAdapter):
            raise TypeError("sampled adapter factory must return GGOV1SampledExecutionAdapter")
        # Return the solver-owned detached result unchanged. StudyService is
        # responsible for terminal status, durable persistence and events.
        return self._solver.solve_generator_transient(
            request=request,
            execution_context=execution_context,
            cancellation=cancellation,
            sampled_adapter=sampled_adapter,
        )


class GeneratorAugmentedTransientStudyHandler:
    """Explicit StudyService bridge for hosts implementing augmented DAE runs.

    This handler is opt-in and deliberately separate from the sampled GGOV1
    handler. The injected host runner owns accepted/rejected-step policy and
    must inspect the canonical cancellation token between accepted steps.
    """

    def __init__(self, solver: Any, augmented_adapter_factory: Any) -> None:
        if solver is None or not callable(
            getattr(solver, "solve_generator_augmented_transient", None)
        ):
            raise TypeError(
                "augmented generator solver must expose "
                "solve_generator_augmented_transient(...)"
            )
        if not callable(augmented_adapter_factory):
            raise TypeError("augmented_adapter_factory must be callable")
        self._solver = solver
        self._augmented_adapter_factory = augmented_adapter_factory

    def __call__(
        self,
        request: StudyRequest,
        execution_context: StudyExecutionContext,
        cancellation: StudyCancellationToken,
    ) -> Any:
        from .integrator_adapter import GeneratorAugmentedDAEAdapter
        from .study_evidence import (
            GeneratorAugmentedStudyEvidence,
            GeneratorStudyCheckpointIdentity,
        )

        if not isinstance(request, StudyRequest):
            raise TypeError("request must be StudyRequest")
        if not isinstance(execution_context, StudyExecutionContext):
            raise TypeError("execution_context must be StudyExecutionContext")
        if not isinstance(cancellation, StudyCancellationToken):
            raise TypeError("cancellation must be StudyCancellationToken")
        if request.study_type != "generator_transient":
            raise ValueError(
                "GeneratorAugmentedTransientStudyHandler only accepts generator_transient"
            )
        if (
            request.project_id != execution_context.project_id
            or request.activation_generation != execution_context.activation_generation
            or request.source_revision != execution_context.source_revision
        ):
            raise ValueError("generator transient request/context provenance mismatch")
        if cancellation.cancelled:
            return None

        adapter = self._augmented_adapter_factory(
            request=request, execution_context=execution_context
        )
        if not isinstance(adapter, GeneratorAugmentedDAEAdapter):
            raise TypeError(
                "augmented adapter factory must return GeneratorAugmentedDAEAdapter"
            )
        try:
            evidence = self._solver.solve_generator_augmented_transient(
                request=request,
                execution_context=execution_context,
                cancellation=cancellation,
                augmented_adapter=adapter,
            )
        except Exception as solver_error:
            # A checkpoint observer can fail after the integrator has already
            # accepted a step. Promote that attached accepted state into the
            # canonical partial-result contract before StudyService handles
            # the exception; never reinterpret an ordinary rejected step.
            if getattr(solver_error, "partial_result", None) is None and getattr(
                solver_error, "accepted_step_result", None
            ) is not None:
                prior_steps = getattr(solver_error, "accepted_steps", ())
                termination = getattr(solver_error, "termination", None)
                partial = GeneratorAugmentedStudyEvidence.from_checkpoint_publication_failure(
                    prior_steps,
                    solver_error,
                    termination=termination if isinstance(termination, Mapping) else None,
                    checkpoint_identity=(
                        getattr(solver_error, "checkpoint_identity", None)
                        if isinstance(
                            getattr(solver_error, "checkpoint_identity", None),
                            GeneratorStudyCheckpointIdentity,
                        )
                        else None
                    ),
                )
                solver_error.partial_result = partial
                solver_error.failing_time = partial.time[-1]
                solver_error.termination_reason = "checkpoint_publication_failed"
                solver_error.cause_type = str(
                    getattr(solver_error, "cause_type", type(solver_error).__name__)
                )
                solver_error.diagnostic_message = str(solver_error)
            raise
        if not isinstance(evidence, GeneratorAugmentedStudyEvidence):
            raise TypeError(
                "augmented transient solver must return GeneratorAugmentedStudyEvidence"
            )
        execution_status = evidence.provenance.get("execution_status")
        if execution_status not in {"completed", "cancelled", "failed"}:
            raise ValueError("augmented study evidence has invalid execution status")
        if execution_status == "failed":
            failure = RuntimeError("augmented generator transient execution failed")
            failure.partial_result = evidence
            termination = evidence.provenance.get("termination", {})
            if isinstance(termination, Mapping):
                failure.diagnostic_message = str(
                    termination.get("diagnostic_message", str(failure))
                )
                failure.failing_time = termination.get("failing_time")
                failure.termination_reason = str(
                    termination.get("reason", "augmented_solver_failure")
                )
            raise failure
        if cancellation.cancelled and execution_status != "cancelled":
            failure = RuntimeError(
                "augmented solver returned without recording the requested cancellation"
            )
            failure.partial_result = evidence
            failure.cause_type = "AugmentedCancellationEvidenceMismatch"
            failure.termination_reason = "cancellation_evidence_missing"
            failure.diagnostic_message = str(failure)
            raise failure
        if execution_status == "cancelled" and not cancellation.cancelled:
            failure = RuntimeError(
                "augmented solver reported cancellation without a StudyService cancellation request"
            )
            failure.partial_result = evidence
            failure.cause_type = "UnexpectedAugmentedCancellation"
            failure.termination_reason = "unsolicited_solver_cancellation"
            failure.diagnostic_message = str(failure)
            raise failure
        return evidence




__all__ = [
    "GeneratorTransientStudyHandler",
    "GeneratorAugmentedTransientStudyHandler",
]
