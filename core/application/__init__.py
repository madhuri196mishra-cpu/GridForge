# ============================================================
# File: core/application/__init__.py
# GridForge V2 — Headless Application Layer
# Author: Subhendu Mishra
# ============================================================

"""Headless Application boundary between consumers and GridForge Core."""

from __future__ import annotations

from .application import Application
from .sld_export_snapshot import (
    SLDAnnotationExportSnapshot, SLDConnectionExportSnapshot, SLDExportSnapshot,
    SLDNodeExportSnapshot, SLDSymbolPlacementSnapshot,
)
from .bootstrap import create_application
from .control_cycle import ControlCycleResult, ControlCycleService, ControlDiagnostic
from .control_dispatch import (
    ControlCommandDispatcher,
    ControlCommandTranslator,
    InvalidApplicationResultError,
)
from .control_execution import ControlExecutionResult, ControlExecutionService
from .control_signal_mapping import (
    ControlSignalBinding,
    ControlSignalDestination,
    ControlSignalMapping,
    ControlSignalQuality,
    ControlSignalResolution,
    ControlSignalResolutionError,
    ControlSignalSource,
)
from .protection_execution import (
    PostconditionStatus,
    ProtectionActionOutcome,
    ProtectionExecutionResult,
    ProtectionExecutionService,
    ProtectionPostconditionVerification,
    ProtectionTargetUnavailableError,
    StaleProtectionExecutionError,
    StaleProtectionDecisionError,
)
from .protection_dependency import (
    ProtectionDependency,
    ProtectionElementDependencyDescriptor,
    ProtectionDependencyRegistry,
    build_protection_dependency_registry,
)
from .project_transition import ProjectTransitionDecision, ProjectTransitionRequired
from .project_presentation import ProjectPresentationCollection
from .read_models import (
    ElementReadModel,
    NetworkReadModel,
    ProtectionReadModel,
    RelayInputBindingReadModel,
    RelayReadModel,
    SimpleWireReadModel,
    StudyResultReadModel,
    TransientSignalReadModel,
    TransientAlgebraicQualityReadModel,
    TransientEventReadModel,
    TransientResultPresentationReadModel,
)
from .read_service import (
    ArchivedStudyResultReadModel,
    NetworkReadService,
    ProtectionReadService,
    ReadService,
    StudyReadService,
    StudyResultComparisonReadModel,
    StudyResultNumericDeltaReadModel,
)
from .revision import ProjectRevision
from .study import StudyCaseDefinition, StudyRequest, StudyResult
from .protection_study import (
    ProtectionFaultScenario,
    ProtectionCoordinationTimingEnvelopeConfiguration,
    ProtectionCoordinationPairConfiguration,
    ProtectionTransientCurrentTraceConfiguration,
    ProtectionFaultScenarioResult,
    ProtectionStudyConfiguration,
    ProtectionStudyResult,
)
from .revision_service import RevisionService
from .validation import ValidationIssue, ValidationResult, ValidationSeverity, ValidationSummary
from .services.validation_service import ValidationService

__all__ = [
    "Application",
    "SLDAnnotationExportSnapshot",
    "SLDConnectionExportSnapshot",
    "SLDExportSnapshot",
    "SLDNodeExportSnapshot",
    "SLDSymbolPlacementSnapshot",
    "create_application",
    "ControlDiagnostic",
    "ControlCycleResult",
    "ControlCycleService",
    "ControlExecutionResult",
    "ControlExecutionService",
    "ControlCommandDispatcher",
    "ControlCommandTranslator",
    "InvalidApplicationResultError",
    "ControlSignalSource",
    "ControlSignalDestination",
    "ControlSignalBinding",
    "ControlSignalMapping",
    "ControlSignalQuality",
    "ControlSignalResolution",
    "ControlSignalResolutionError",
    "StaleProtectionExecutionError",
    "StaleProtectionDecisionError",
    "ProtectionTargetUnavailableError",
    "PostconditionStatus",
    "ProtectionPostconditionVerification",
    "ProtectionActionOutcome",
    "ProtectionExecutionResult",
    "ProtectionExecutionService",
    "ProtectionDependency",
    "ProtectionElementDependencyDescriptor",
    "ProtectionDependencyRegistry",
    "build_protection_dependency_registry",
    "ProjectTransitionDecision",
    "ProjectTransitionRequired",
    "ProjectPresentationCollection",
    "ElementReadModel",
    "NetworkReadModel",
    "ProtectionReadModel",
    "RelayInputBindingReadModel",
    "RelayReadModel",
    "SimpleWireReadModel",
    "StudyResultReadModel",
    "TransientSignalReadModel",
    "TransientAlgebraicQualityReadModel",
    "TransientEventReadModel",
    "TransientResultPresentationReadModel",
    "ArchivedStudyResultReadModel",
    "StudyResultComparisonReadModel",
    "StudyResultNumericDeltaReadModel",
    "StudyReadService",
    "StudyCaseDefinition",
    "StudyRequest",
    "StudyResult",
    "ProtectionFaultScenario",
    "ProtectionCoordinationTimingEnvelopeConfiguration",
    "ProtectionCoordinationPairConfiguration",
    "ProtectionTransientCurrentTraceConfiguration",
    "ProtectionFaultScenarioResult",
    "ProtectionStudyConfiguration",
    "ProtectionStudyResult",
    "NetworkReadService",
    "ProtectionReadService",
    "ReadService",
    "ProjectRevision",
    "RevisionService",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
    "ValidationSummary",
    "ValidationService",
]
