# ============================================================
# File: core/application/study_preparation.py
# GridForge V2 — Application Study Preparation Boundary
# ============================================================
"""Application-owned bridge from active project state to detached study inputs."""

from __future__ import annotations

from typing import Any, Callable

from core.analysis.dynamic_model_association import DynamicMachineModelRegistry
from core.analysis.power_flow_preparation import PreparedPowerFlow, PowerFlowPreparation
from core.analysis.power_flow_configuration import PowerFlowStudyConfiguration
from core.analysis.short_circuit_preparation import ShortCircuitPreparation
from core.analysis.short_circuit_configuration import ShortCircuitStudyConfiguration
from core.analysis.transient_network import PreparedTransientStability
from core.analysis.transient_stability import TransientStabilityStudyConfiguration
from core.solver.power_flow.result import PowerFlowResult
from core.solver.short_circuit.input import ShortCircuitInput


NetworkProvider = Callable[[], Any]


class StudyPreparationService:
    """Prepare detached Core study snapshots without exposing live Core objects to handlers."""

    def __init__(self, network_provider: NetworkProvider) -> None:
        if not callable(network_provider):
            raise TypeError("network_provider must be callable.")
        self._network_provider = network_provider

    def prepare_power_flow(self, configuration: PowerFlowStudyConfiguration) -> PreparedPowerFlow:
        if not isinstance(configuration, PowerFlowStudyConfiguration):
            raise TypeError("configuration must be PowerFlowStudyConfiguration.")
        return PowerFlowPreparation.prepare(self._network_provider(), configuration)

    def prepare_short_circuit(self, configuration: ShortCircuitStudyConfiguration) -> ShortCircuitInput:
        if not isinstance(configuration, ShortCircuitStudyConfiguration):
            raise TypeError("configuration must be ShortCircuitStudyConfiguration.")
        preparation = ShortCircuitPreparation(
            self._network_provider(),
            base_mva=configuration.metadata.get("base_mva"),
        )
        return preparation.prepare(
            configuration.fault_type,
            configuration.fault_bus_id,
            configuration.fault_impedance,
            elements=configuration.element_ids or None,
        )

    def prepare_transient_stability(
        self,
        configuration: TransientStabilityStudyConfiguration,
        prepared_power_flow: PreparedPowerFlow,
        power_flow_result: PowerFlowResult,
        dynamic_models: DynamicMachineModelRegistry,
    ) -> PreparedTransientStability:
        """Prepare a detached transient study from an already-solved PF operating point."""
        if not isinstance(configuration, TransientStabilityStudyConfiguration):
            raise TypeError("configuration must be TransientStabilityStudyConfiguration.")
        if not isinstance(prepared_power_flow, PreparedPowerFlow):
            raise TypeError("prepared_power_flow must be PreparedPowerFlow.")
        if not isinstance(power_flow_result, PowerFlowResult):
            raise TypeError("power_flow_result must be PowerFlowResult.")
        if not isinstance(dynamic_models, DynamicMachineModelRegistry):
            raise TypeError("dynamic_models must be DynamicMachineModelRegistry.")
        return PreparedTransientStability.from_power_flow(
            prepared_power_flow,
            power_flow_result,
            dynamic_models,
        )


__all__ = ["NetworkProvider", "StudyPreparationService"]
