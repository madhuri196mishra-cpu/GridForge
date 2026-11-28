"""Persistence and migration boundaries for GridForge electrical data.

Persistence owns representation/migration concerns only. It does not own
engineering interpretation, topology mutation, study preparation, or solving.
"""

from .migration import (
    AmbiguousElectricalDataError,
    LegacyElectricalDataMigration,
    MigrationResult,
    ProjectMigration,
    ProjectMigrationError,
    ProjectPersistenceMigration,
    PresentationCollectionMigrationError,
    SLDMigration,
    SLDMigrationError,
    UnsupportedProjectSchemaError,
)
from .project_package import (
    MANIFEST_NAME,
    PACKAGE_SUFFIX,
    PACKAGE_VERSION,
    PROJECT_SCHEMA_VERSION,
    PRESENTATION_SCHEMA_VERSION,
    SLD_SCHEMA_VERSION,
    CORE_SERIALIZATION_VERSION,
    PROJECT_NAME,
    normalize_package_path,
)
from .project_persistence import ProjectPersistenceError, ProjectPersistenceService
from .instrument_wiring_serializer import (
    WIRING_SCHEMA_VERSION,
    InstrumentationWiringSerializationError,
    serialize_instrumentation_wiring,
    deserialize_instrumentation_wiring,
)
from .study_result_archive import (
    ARCHIVE_FORMAT,
    ARCHIVE_SCHEMA_VERSION,
    ArchivedStudyResult,
    StudyResultArchive,
    StudyResultArchiveError,
    StudyResultArchiveCommitUncertain,
    StudyResultArchiveVerification,
)
from .type_registry import ModelTypeRegistry, UnknownModelTypeError

__all__ = [
    "AmbiguousElectricalDataError",
    "LegacyElectricalDataMigration",
    "MigrationResult",
    "ProjectMigration",
    "ProjectMigrationError",
    "ProjectPersistenceMigration",
    "PresentationCollectionMigrationError",
    "SLDMigration",
    "SLDMigrationError",
    "UnsupportedProjectSchemaError",
    "MANIFEST_NAME",
    "PACKAGE_SUFFIX",
    "PACKAGE_VERSION",
    "PROJECT_SCHEMA_VERSION",
    "PRESENTATION_SCHEMA_VERSION",
    "SLD_SCHEMA_VERSION",
    "CORE_SERIALIZATION_VERSION",
    "PROJECT_NAME",
    "normalize_package_path",
    "ProjectPersistenceError",
    "ProjectPersistenceService",
    "WIRING_SCHEMA_VERSION",
    "InstrumentationWiringSerializationError",
    "serialize_instrumentation_wiring",
    "deserialize_instrumentation_wiring",
    "ARCHIVE_FORMAT",
    "ARCHIVE_SCHEMA_VERSION",
    "ArchivedStudyResult",
    "StudyResultArchive",
    "StudyResultArchiveError",
    "StudyResultArchiveCommitUncertain",
    "StudyResultArchiveVerification",
    "ModelTypeRegistry",
    "UnknownModelTypeError",
]
