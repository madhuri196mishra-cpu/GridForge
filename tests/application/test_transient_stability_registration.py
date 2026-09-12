from core.application.bootstrap import create_application
from core.network import Network


def test_application_registers_transient_stability_study():
    application = create_application(Network())

    assert "transient_stability" in application.study_service.registered_study_types
