# ============================================================
# File: core/persistence/project_package.py
# GridForge V2 — Canonical Project Package
# Author: Subhendu Mishra
# ============================================================

"""Canonical on-disk ``.gridforge`` project package contract."""

from __future__ import annotations

from pathlib import Path


PACKAGE_SUFFIX = ".gridforge"
MANIFEST_NAME = "manifest.json"
PROJECT_NAME = "project.json"
PACKAGE_VERSION = 1

# Version layers are intentionally distinct ownership contracts.
PROJECT_SCHEMA_VERSION = 5
PRESENTATION_SCHEMA_VERSION = 1
SLD_SCHEMA_VERSION = 3
CORE_SERIALIZATION_VERSION = 1
TRANSFORMER_CONFIGURATION_SCHEMA_VERSION = 1
CONNECTIVITY_SERIALIZATION_VERSION = 4


def normalize_package_path(path: str | Path) -> Path:
    """Normalize a project package path and require the canonical suffix."""
    if not isinstance(path, (str, Path)):
        raise TypeError("Project path must be a string or Path.")
    target = Path(path)
    if not str(target):
        raise ValueError("Project path must not be empty.")
    if target.suffix.lower() != PACKAGE_SUFFIX:
        target = target.with_name(target.name + PACKAGE_SUFFIX)
    return target


def manifest_path(package: Path) -> Path:
    return package / MANIFEST_NAME


def project_path(package: Path) -> Path:
    return package / PROJECT_NAME


__all__ = [
    "MANIFEST_NAME",
    "PACKAGE_SUFFIX",
    "PACKAGE_VERSION",
    "PROJECT_SCHEMA_VERSION",
    "PRESENTATION_SCHEMA_VERSION",
    "SLD_SCHEMA_VERSION",
    "CORE_SERIALIZATION_VERSION",
    "TRANSFORMER_CONFIGURATION_SCHEMA_VERSION",
    "CONNECTIVITY_SERIALIZATION_VERSION",
    "PROJECT_NAME",
    "manifest_path",
    "normalize_package_path",
    "project_path",
]
