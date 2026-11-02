from core.application import Application
from core.application.bootstrap import create_application
from core.network.network import Network
from core.application.project import ProjectContext
from ui.sld.sld_document import SLDDocument
from ui.workspace.project_workspace import ProjectWorkspaceLifecycle
from ui.workspace.project_workspace_adapter import ProjectWorkspaceApplicationAdapter
from ui.workspace.workspace_controller import WorkspaceController


class _ApplicationStub(Application):
    def new_project(self, name="Untitled Project", *, project_id=None):
        return ProjectContext(project_id=project_id or "project-1", name=name, path=None)


class _WorkspaceControllerStub(WorkspaceController):
    def __init__(self):
        self._workspace_id = None
        self._manager = type("Manager", (), {"active_workspace_id": None, "clear_active": lambda self: None, "state": type("State", (), {"active_workspace_id": None})()})()
        self._realizer = type("Realizer", (), {"realized_layout": None, "clear_realization": lambda self: None})()

    @property
    def active_workspace_id(self):
        return self._workspace_id

    def activate_default(self):
        self._workspace_id = "sld"

    def deactivate(self):
        self._workspace_id = None


def test_new_project_uses_supplied_document_without_replacement():
    lifecycle = ProjectWorkspaceLifecycle(_WorkspaceControllerStub())
    adapter = ProjectWorkspaceApplicationAdapter(create_application(Network()), lifecycle)
    document = SLDDocument(
        document_id="sld-document",
        project_id="project-1",
        name="GridForge SLD",
    )

    adapter.new_project(
        name="GridForge Project",
        project_id="project-1",
        document=document,
    )

    assert lifecycle.document is document
    assert lifecycle.document.project_id == "project-1"
    assert lifecycle.document.document_type == "sld"



def test_collection_deserializer_uses_configured_symbol_presentation_factory(monkeypatch):
    factory = lambda identifier: {"identifier": identifier}
    captured = {}

    def from_dict(cls, data, *, default_symbol_presentation_factory=None):
        captured["factory"] = default_symbol_presentation_factory
        return SLDDocument(
            document_id=data["document_id"],
            project_id=data["project_id"],
        )

    monkeypatch.setattr(SLDDocument, "from_dict", classmethod(from_dict))
    lifecycle = ProjectWorkspaceLifecycle(_WorkspaceControllerStub())
    adapter = ProjectWorkspaceApplicationAdapter(
        create_application(Network()),
        lifecycle,
        default_symbol_presentation_factory=factory,
    )
    adapter.new_project(name="GridForge Project", project_id="project-1")

    payload = SLDDocument(
        document_id="restored-sld",
        project_id="project-1",
        name="Restored SLD",
    ).to_dict()
    collection_deserializer = adapter.project_lifecycle._presentation_collection_deserializer
    restored = collection_deserializer({
        "documents": [payload],
        "active_document_id": "restored-sld",
    })

    assert captured["factory"] is factory
    assert restored.active_document.document_id == "restored-sld"
