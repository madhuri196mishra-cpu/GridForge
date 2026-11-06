"""Static coverage for the Fuse endpoint-resolution application boundary.

Runtime execution is intentionally deferred for this remediation. These tests
inspect source structure and contracts without importing the Application
runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]
APPLICATION = ROOT / "core" / "application"
COMMANDS = APPLICATION / "commands"
SERVICES = APPLICATION / "services"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path), filename=str(path))


def _function(path: Path, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing function: {name}")


def _function_source(path: Path, name: str) -> str:
    source = _source(path)
    tree = _tree(path)
    node = _function(path, name)
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def test_fuse_create_command_carries_only_endpoint_references():
    path = COMMANDS / "model_commands.py"
    source = _source(path)
    assert "EndpointReference" in source

    node = _function(path, "__init__")
    classes = [n for n in ast.walk(_tree(path)) if isinstance(n, ast.ClassDef) and n.name == "CreateFuseCommand"]
    assert len(classes) == 1
    init = next(n for n in classes[0].body if isinstance(n, ast.FunctionDef) and n.name == "__init__")

    annotations = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in init.args.kwonlyargs
        if arg.annotation is not None
    }
    assert annotations["endpoint_from"] == "EndpointReference | None"
    assert annotations["endpoint_to"] == "EndpointReference | None"
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_endpoint" for n in ast.walk(init))
    assert "from core.model import EndpointReference" in source
    assert "rated_current_a: float | None = None" in source
    assert "rated_voltage_v: float | None = None" in source
    assert "interrupting_rating_ka: float | None = None" in source


def test_fuse_create_handler_resolves_both_endpoints_before_service():
    path = APPLICATION / "command_handlers.py"
    section = _function_source(path, "create_fuse")
    assert 'self._resolve(dict(command.payload), context, "endpoint_from", "endpoint_to")' in section
    assert "self._model_service.switching_service.create_fuse" in section
    assert "**command.payload" not in section
    assert "EndpointResolver.resolve" not in section or "self._resolve" in section


def test_fuse_create_service_accepts_resolved_endpoints_and_uses_network_lifecycle():
    path = SERVICES / "switching_model_service.py"
    source = _source(path)
    section = _function_source(path, "create_fuse")
    assert "endpoint_from: Bus | Terminal | None" in section
    assert "endpoint_to: Bus | Terminal | None" in section
    assert "rated_current_a: float | None = None" in section
    assert "rated_voltage_v: float | None = None" in section
    assert "interrupting_rating_ka: float | None = None" in section
    assert "self._validate_endpoint(endpoint_from" in section
    assert "self._validate_endpoint(endpoint_to" in section
    assert "Fuse(" in section
    assert "endpoint_from=endpoint_from" in section
    assert "endpoint_to=endpoint_to" in section
    assert "self._network.add_fuse(fuse)" in section
    assert "transaction.record_undo(lambda fuse=fuse: self._network.remove_fuse(fuse))" in section
    assert "self._network.registry.add_fuse" not in source
    assert "self._network.registry.remove_fuse" not in source


def test_fuse_topology_mutations_invalidate_and_undo():
    path = SERVICES / "switching_model_service.py"
    source = _source(path)
    for method in ("blow_fuse", "reset_fuse", "_set_fuse_service"):
        section = _function_source(path, method)
        assert "self._network.invalidate_topology()" in section
        assert "transaction.record_undo" in section

    assert "self._network.remove_fuse(fuse)" in _function_source(path, "delete_fuse")
    assert "self._network.add_fuse(fuse)" in _function_source(path, "delete_fuse")


def test_fuse_topology_manager_interprets_conduction_state():
    path = ROOT / "core" / "network" / "topology.py"
    source = _source(path)
    assert "conduction_state(e)" in source
    assert "from .electrical_boundary import" in source


def test_four_switching_create_handlers_share_endpoint_resolution_boundary():
    path = APPLICATION / "command_handlers.py"
    source = _source(path)
    expected = {
        "create_switch": ('endpoint_a', 'endpoint_b'),
        "create_breaker": ('endpoint_from', 'endpoint_to'),
        "create_disconnector": ('endpoint_from', 'endpoint_to'),
        "create_fuse": ('endpoint_from', 'endpoint_to'),
    }
    for handler, keys in expected.items():
        section = _function_source(path, handler)
        key_text = ", ".join(f'\"{key}\"' for key in keys)
        assert f"self._resolve(dict(command.payload), context, {key_text})" in section
        assert f"def {handler}(" in source


def test_no_direct_fuse_create_payload_bypass_remains():
    source = _source(APPLICATION / "command_handlers.py")
    assert "create_fuse(transaction=transaction, **command.payload)" not in source
