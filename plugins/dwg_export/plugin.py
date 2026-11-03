"""Engineering-plugin lifecycle for optional DWG export."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import ClassVar

from core.plugin_sdk import (
    SUPPORTED_PLUGIN_API_VERSION,
    GridForgePlugin,
    HostPluginContext,
    PluginDescriptor,
    validate_plugin_descriptor,
)

from .backends import ODAFileConverterBackend
from .service import DWGExportService, ExportResult
from .drawing_plan import DrawingPlan
from .symbol_registry import SymbolRegistry


PLUGIN_DESCRIPTOR = PluginDescriptor(
    plugin_id="gridforge.plugin.dwg_export",
    version="1.0.0",
    api_version=SUPPORTED_PLUGIN_API_VERSION,
    display_name="DWG Export",
    capabilities=frozenset({"cad_export", "dwg_export", "drawing_plan"}),
)


@dataclass(frozen=True, slots=True)
class DWGExportConfiguration:
    """Explicit host-supplied configuration for the optional ODA backend."""

    executable: str | Path
    symbol_registry: SymbolRegistry
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.executable, (str, Path)) or not str(self.executable).strip():
            raise ValueError("executable must be an explicit non-empty ODA path")
        if not isinstance(self.symbol_registry, SymbolRegistry):
            raise TypeError("symbol_registry must be an explicit SymbolRegistry")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


class DWGExportPlugin(GridForgePlugin):
    """Own the optional CAD backend/service for one explicit plugin activation."""

    descriptor: ClassVar[PluginDescriptor] = PLUGIN_DESCRIPTOR

    def __init__(self) -> None:
        self._configuration: DWGExportConfiguration | None = None
        self._backend: ODAFileConverterBackend | None = None
        self._export_service: DWGExportService | None = None

    @property
    def plugin_id(self) -> str:
        return PLUGIN_DESCRIPTOR.plugin_id

    @property
    def export_service(self) -> DWGExportService:
        if self._export_service is None:
            raise RuntimeError("DWG Export plugin is not active")
        return self._export_service

    @property
    def configuration(self) -> DWGExportConfiguration:
        if self._configuration is None:
            raise RuntimeError("DWG Export plugin is not active")
        return self._configuration

    def initialize(self, context: HostPluginContext) -> None:
        if self._export_service is not None:
            raise RuntimeError("DWG Export plugin is already active")
        if context is None or getattr(context, "application", None) is None:
            raise TypeError("DWGExportPlugin.initialize requires a host EngineeringPluginContext and Application")
        metadata = getattr(context, "metadata", {})
        services = getattr(context, "services", None)
        if not isinstance(metadata, Mapping) or not isinstance(services, Mapping):
            raise TypeError("PluginContext metadata and services must be mappings")
        supported = metadata.get(
            "host_supported_plugin_api_versions",
            frozenset({SUPPORTED_PLUGIN_API_VERSION}),
        )
        if isinstance(supported, str) or not isinstance(supported, (tuple, list, set, frozenset)):
            raise TypeError("host_supported_plugin_api_versions must be a collection of version strings")
        supported_versions = frozenset(supported)
        if not supported_versions or any(not isinstance(item, str) or not item.strip() for item in supported_versions):
            raise ValueError("host_supported_plugin_api_versions must contain non-empty version strings")
        validate_plugin_descriptor(
            self.descriptor,
            supported_api_versions=supported_versions,
            expected_plugin_id=self.plugin_id,
        )
        configuration = services.get("dwg_export_configuration")
        if not isinstance(configuration, DWGExportConfiguration):
            raise RuntimeError(
                "DWG Export activation requires PluginContext.services['dwg_export_configuration'] "
                "with an explicit ODA executable and SymbolRegistry"
            )

        # Construct everything locally; publish plugin state only after backend
        # validation succeeds, so a failed activation leaves no partial state.
        backend = ODAFileConverterBackend(
            configuration.executable,
            timeout_seconds=configuration.timeout_seconds,
            symbol_registry=configuration.symbol_registry,
        )
        export_service = DWGExportService(backend)
        self._configuration = configuration
        self._backend = backend
        self._export_service = export_service

    def export(
        self,
        plan: DrawingPlan,
        destination: str | Path,
        *,
        target_version: str | None = None,
    ) -> ExportResult:
        return self.export_service.export(
            plan,
            destination,
            format="dwg",
            target_version=target_version,
        )

    def shutdown(self) -> None:
        # The backend owns no persistent process or file handle between calls.
        self._export_service = None
        self._backend = None
        self._configuration = None


__all__ = ["DWGExportConfiguration", "DWGExportPlugin", "PLUGIN_DESCRIPTOR"]
