from types import SimpleNamespace

import pytest

from core.plugin_sdk import SUPPORTED_PLUGIN_API_VERSION, EngineeringPluginHost
from plugins.dwg_export import DWGExportConfiguration, DWGExportPlugin, PLUGIN_DESCRIPTOR, SymbolRegistry


def test_dwg_export_plugin_has_stable_engineering_plugin_descriptor():
    assert PLUGIN_DESCRIPTOR.plugin_id == "gridforge.plugin.dwg_export"
    assert PLUGIN_DESCRIPTOR.api_version == SUPPORTED_PLUGIN_API_VERSION
    assert "dwg_export" in PLUGIN_DESCRIPTOR.capabilities


def test_dwg_export_configuration_requires_explicit_symbol_registry():
    with pytest.raises(TypeError, match="SymbolRegistry"):
        DWGExportConfiguration(executable="oda-file-converter", symbol_registry=object())


def test_dwg_export_plugin_fails_closed_without_host_configuration():
    application = object()
    plugin = DWGExportPlugin()
    host = EngineeringPluginHost(application)

    with pytest.raises(RuntimeError, match="dwg_export_configuration"):
        host.activate(plugin, services={})

    assert host.active_plugin_ids == ()
    assert plugin.plugin_id == "gridforge.plugin.dwg_export"
    with pytest.raises(RuntimeError, match="not active"):
        _ = plugin.export_service


def test_dwg_export_plugin_rejects_incompatible_host_api_before_backend_setup():
    plugin = DWGExportPlugin()
    context = SimpleNamespace(
        application=object(),
        services={},
        metadata={"host_supported_plugin_api_versions": frozenset({"V1.0"})},
    )

    with pytest.raises(RuntimeError, match="requires SDK API"):
        plugin.initialize(context)


def test_dwg_export_plugin_shutdown_is_safe_before_activation():
    plugin = DWGExportPlugin()
    plugin.shutdown()
    with pytest.raises(RuntimeError, match="not active"):
        _ = plugin.export_service
