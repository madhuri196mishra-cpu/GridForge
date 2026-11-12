"""Adapter from load-dynamics requests to the Application-owned StudyService."""
from __future__ import annotations

from typing import Any

from core.application.study import StudyCancellationToken, StudyExecutionContext, StudyRequest


class LoadTransientStudyHandler:
    """Delegate integration and state ownership to the host's canonical solver.

    The injected solver must implement solve_load_transient(...). A fresh
    sampled adapter is created for each request so per-load state is never
    shared between concurrent studies.
    """

    def __init__(self, solver: Any, sampled_adapter_factory: Any) -> None:
        if not callable(getattr(solver, "solve_load_transient", None)):
            raise TypeError("load transient solver must expose solve_load_transient(...)")
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
        if request.study_type != "load_transient":
            raise ValueError("LoadTransientStudyHandler only accepts load_transient")
        if (
            request.project_id != execution_context.project_id
            or request.activation_generation != execution_context.activation_generation
            or request.source_revision != execution_context.source_revision
        ):
            raise ValueError("load transient request/context provenance mismatch")
        if cancellation.cancelled:
            return None
        adapter = self._sampled_adapter_factory(
            request=request, execution_context=execution_context
        )
        if adapter is None or not callable(getattr(adapter, "evaluate_sample", None)):
            raise TypeError("sampled adapter factory must return an evaluate_sample-capable adapter")
        return self._solver.solve_load_transient(
            request=request,
            execution_context=execution_context,
            cancellation=cancellation,
            sampled_adapter=adapter,
        )


__all__ = ["LoadTransientStudyHandler"]
