# ============================================================
# File: core/analysis/power_flow_preparation.py
# GridForge V2 — Power Flow Preparation
# Author: Subhendu Mishra
# ============================================================

"""Prepare detached numerical Power Flow snapshots.

Author: Subhendu Mishra
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping

from core.analysis.power_flow_configuration import PowerFlowStudyConfiguration
from core.base.per_unit import PerUnitSystem
from core.model.cable import Cable
from core.model.capacitor import Capacitor
from core.model.injection import Injection
from core.model.line import Line
from core.model.reactor import Reactor
from core.model.transformer import Transformer
from core.network.topology_snapshot import TopologySnapshot
from core.numerical.ybus import YBus, YBusBuilder
from core.solver.power_flow.input import PowerFlowBusType, PowerFlowInput


@dataclass(frozen=True, slots=True)
class PreparedBranch:
    """Detached PU representation of a standard two-terminal branch."""

    branch_id: str
    from_bus_id: str
    to_bus_id: str
    r_pu: float
    x_pu: float
    b_pu: float
    in_service: bool = True

    def __post_init__(self) -> None:
        if not self.branch_id or not self.from_bus_id or not self.to_bus_id:
            raise ValueError("Prepared branch identifiers must be non-empty.")
        for value, name in ((self.r_pu, "r_pu"), (self.x_pu, "x_pu"), (self.b_pu, "b_pu")):
            if not math.isfinite(float(value)):
                raise ValueError(f"Prepared branch {name} must be finite.")
        if float(self.r_pu) == 0.0 and float(self.x_pu) == 0.0:
            raise ValueError("Prepared branch series impedance cannot be zero.")
        object.__setattr__(self, "r_pu", float(self.r_pu))
        object.__setattr__(self, "x_pu", float(self.x_pu))
        object.__setattr__(self, "b_pu", float(self.b_pu))


@dataclass(frozen=True, slots=True)
class PreparedTransformer:
    """Detached PU representation of a transformer branch."""

    branch_id: str
    from_bus_id: str
    to_bus_id: str
    r_pu: float
    x_pu: float
    b_pu: float
    tap: float
    shift: float
    in_service: bool = True

    def __post_init__(self) -> None:
        if not self.branch_id or not self.from_bus_id or not self.to_bus_id:
            raise ValueError("Prepared transformer identifiers must be non-empty.")
        for value, name in ((self.r_pu, "r_pu"), (self.x_pu, "x_pu"), (self.b_pu, "b_pu"), (self.tap, "tap"), (self.shift, "shift")):
            if not math.isfinite(float(value)):
                raise ValueError(f"Prepared transformer {name} must be finite.")
        if float(self.r_pu) == 0.0 and float(self.x_pu) == 0.0:
            raise ValueError("Prepared transformer series impedance cannot be zero.")
        if float(self.tap) <= 0.0:
            raise ValueError("Prepared transformer tap must be positive.")
        object.__setattr__(self, "r_pu", float(self.r_pu))
        object.__setattr__(self, "x_pu", float(self.x_pu))
        object.__setattr__(self, "b_pu", float(self.b_pu))
        object.__setattr__(self, "tap", float(self.tap))
        object.__setattr__(self, "shift", float(self.shift))


@dataclass(frozen=True, slots=True)
class PreparedShunt:
    """Detached PU representation of a shunt admittance."""

    shunt_id: str
    bus_id: str
    g_pu: float
    b_pu: float
    in_service: bool = True

    def __post_init__(self) -> None:
        if not self.shunt_id or not self.bus_id:
            raise ValueError("Prepared shunt identifiers must be non-empty.")
        for value, name in ((self.g_pu, "g_pu"), (self.b_pu, "b_pu")):
            if not math.isfinite(float(value)):
                raise ValueError(f"Prepared shunt {name} must be finite.")
        object.__setattr__(self, "g_pu", float(self.g_pu))
        object.__setattr__(self, "b_pu", float(self.b_pu))


@dataclass(frozen=True, slots=True)
class PreparedPowerFlow:
    """Immutable, detached numerical Power Flow problem snapshot."""

    input: PowerFlowInput
    ybus: YBus
    base_mva: float
    bus_voltage_bases: Mapping[str, float]
    branches: tuple[PreparedBranch, ...] = ()
    transformers: tuple[PreparedTransformer, ...] = ()
    shunts: tuple[PreparedShunt, ...] = ()
    project_id: str | None = None
    activation_generation: int | None = None
    topology_revision: int | None = None
    source_revision: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input, PowerFlowInput):
            raise TypeError("input must be a PowerFlowInput instance.")
        if not isinstance(self.ybus, YBus):
            raise TypeError("ybus must be a YBus instance.")
        if self.input.bus_ids != self.ybus.bus_ids:
            raise ValueError("PowerFlowInput and YBus must use identical bus ordering.")
        base_mva = float(self.base_mva)
        if not math.isfinite(base_mva) or base_mva <= 0.0:
            raise ValueError("Prepared Power Flow base MVA must be positive and finite.")
        bases = {str(bus_id): float(value) for bus_id, value in self.bus_voltage_bases.items()}
        if set(bases) != set(self.input.bus_ids):
            raise ValueError("Prepared voltage bases must match PowerFlowInput bus IDs.")
        if any(not math.isfinite(value) or value <= 0.0 for value in bases.values()):
            raise ValueError("Prepared voltage bases must be positive and finite.")
        object.__setattr__(self, "base_mva", base_mva)
        object.__setattr__(self, "bus_voltage_bases", MappingProxyType(bases))
        object.__setattr__(self, "branches", tuple(self.branches))
        object.__setattr__(self, "transformers", tuple(self.transformers))
        object.__setattr__(self, "shunts", tuple(self.shunts))
        if self.project_id is not None:
            if not isinstance(self.project_id, str):
                raise TypeError("Prepared Power Flow project_id must be a string when supplied.")
            if not self.project_id.strip():
                raise ValueError("Prepared Power Flow project_id must be non-empty when supplied.")
        if self.activation_generation is not None:
            if isinstance(self.activation_generation, bool) or not isinstance(self.activation_generation, int):
                raise TypeError("Prepared Power Flow activation_generation must be an integer when supplied.")
            if self.activation_generation < 1:
                raise ValueError("Prepared Power Flow activation_generation must be positive when supplied.")
        if self.topology_revision is not None:
            if isinstance(self.topology_revision, bool) or not isinstance(self.topology_revision, int):
                raise TypeError("Prepared Power Flow topology_revision must be an integer when supplied.")
            if self.topology_revision < 0:
                raise ValueError("Prepared Power Flow topology_revision must be non-negative.")
        if self.source_revision is not None:
            if (
                not isinstance(self.source_revision, tuple)
                or len(self.source_revision) != 4
                or any(
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                    for value in self.source_revision
                )
            ):
                raise ValueError(
                    "Prepared Power Flow source_revision must contain four non-negative revision integers."
                )
            object.__setattr__(self, "source_revision", tuple(self.source_revision))
        ybus_revision = self.ybus.topology_revision
        if (
            self.topology_revision is not None
            and ybus_revision is not None
            and self.topology_revision != ybus_revision
        ):
            raise ValueError(
                "Prepared Power Flow and YBus topology revisions must match when both are supplied."
            )

    @property
    def bus_ids(self) -> tuple[str, ...]:
        """Return the detached authoritative numerical bus ordering."""
        return self.input.bus_ids


class PowerFlowPreparation:
    """Own the live-model to detached numerical Power Flow boundary."""

    _INJECTION_COLLECTIONS = (
        "grids",
        "generators",
        "synchronous_machines",
        "loads",
        "motors",
        "solar",
        "batteries",
    )

    @staticmethod
    def prepare(network: Any, power_flow_configuration: PowerFlowStudyConfiguration, *, topology_snapshot: TopologySnapshot | None = None) -> PreparedPowerFlow:
        return PowerFlowPreparation(network, power_flow_configuration, topology_snapshot=topology_snapshot)._prepare()

    def __init__(self, network: Any, power_flow_configuration: PowerFlowStudyConfiguration, *, topology_snapshot: TopologySnapshot | None = None) -> None:
        self.network = network
        self.power_flow_configuration = power_flow_configuration
        self.topology_snapshot = topology_snapshot
        if self.topology_snapshot is None:
            self.network.rebuild_topology()
            self.topology_snapshot = self.network.topology_snapshot
            if self.topology_snapshot is None:
                raise ValueError('Power Flow requires a canonical TopologySnapshot.')
        self._validate_network()
        if not isinstance(power_flow_configuration, PowerFlowStudyConfiguration):
            raise TypeError("power_flow_configuration must be a PowerFlowStudyConfiguration.")
        self._per_unit = PerUnitSystem(power_flow_configuration.base_mva)

    def _prepare(self) -> PreparedPowerFlow:
        bus_ids = tuple(self.topology_snapshot.bus_ids)
        if not bus_ids:
            raise ValueError("Power Flow preparation requires at least one bus.")
        buses = tuple(self.network.get_by_identity(bus_id) for bus_id in bus_ids)
        classification = self._prepare_bus_types(bus_ids)
        if len(self.topology_snapshot.islands) > 1:
            raise ValueError("Power Flow policy rejects multiple energized islands because PowerFlowInput requires exactly one slack bus.")
        voltage_bases = self._prepare_voltage_bases(buses)

        p_spec: list[float] = []
        q_spec: list[float] = []
        q_min: list[float | None] = []
        q_max: list[float | None] = []
        initial_vm: list[float] = []
        initial_va: list[float] = []
        for bus in buses:
            p, q, minimum_q, maximum_q = self._bus_power_spec(bus)
            s_pu = self._per_unit.to_pu_power(p, q)
            p_spec.append(s_pu.real)
            q_spec.append(s_pu.imag)
            q_min.append(self._to_pu_reactive_power(minimum_q))
            q_max.append(self._to_pu_reactive_power(maximum_q))
            initial_vm.append(self._initial_voltage_pu(bus))
            initial_va.append(math.radians(self._finite(getattr(bus, "angle_deg", 0.0), f"Bus '{bus.id}' angle_deg")))

        input_data = PowerFlowInput(
            bus_ids=bus_ids,
            bus_types=classification,
            p_spec=tuple(p_spec),
            q_spec=tuple(q_spec),
            q_min=tuple(q_min),
            q_max=tuple(q_max),
            initial_vm=tuple(initial_vm),
            initial_va=tuple(initial_va),
        )

        branches = self._prepare_branches(voltage_bases)
        transformers = self._prepare_transformers(voltage_bases)
        shunts = self._prepare_shunts(voltage_bases)
        topology_revision = self.topology_snapshot.topology_revision
        snapshot = PreparedPowerFlow(
            input=input_data,
            ybus=YBus(matrix=self._empty_ybus_matrix(len(bus_ids)), bus_ids=bus_ids, topology_revision=topology_revision),
            base_mva=self._per_unit.base_mva,
            bus_voltage_bases=voltage_bases,
            branches=branches,
            transformers=transformers,
            shunts=shunts,
            project_id=self.topology_snapshot.project_id,
            activation_generation=self.topology_snapshot.activation_generation,
            topology_revision=topology_revision,
        )
        ybus = YBusBuilder().build(snapshot)
        return PreparedPowerFlow(
            input=input_data,
            ybus=ybus,
            base_mva=snapshot.base_mva,
            bus_voltage_bases=snapshot.bus_voltage_bases,
            branches=branches,
            transformers=transformers,
            shunts=shunts,
            project_id=snapshot.project_id,
            activation_generation=snapshot.activation_generation,
            topology_revision=snapshot.topology_revision,
        )

    @staticmethod
    def _empty_ybus_matrix(size: int):
        import numpy as np
        from scipy.sparse import csr_matrix
        return csr_matrix((size, size), dtype=np.complex128)

    def _prepare_voltage_bases(self, buses: tuple[Any, ...]) -> dict[str, float]:
        return {str(bus.id): self._finite_positive(getattr(bus, "nominal_voltage_kv", 0.0), f"Bus '{bus.id}' nominal_voltage_kv") for bus in buses}

    def _prepare_branches(self, voltage_bases: Mapping[str, float]) -> tuple[PreparedBranch, ...]:
        prepared: list[PreparedBranch] = []
        for branch in getattr(self.network, "lines", ()):
            if not getattr(branch, "in_service", True):
                continue
            if self._branch_outside_active_topology(branch):
                continue
            if not isinstance(branch, Line):
                raise TypeError(f"Network line '{getattr(branch, 'id', branch)}' is not a Line model.")
            from_bus, to_bus = self._resolve_branch_endpoints(branch)
            kv = self._common_branch_voltage(from_bus, to_bus, voltage_bases, branch)
            z_pu = self._per_unit.to_pu_impedance(branch.series_impedance, kv)
            b_pu = self._per_unit.to_pu_admittance(complex(0.0, branch.shunt_susceptance_siemens), kv).imag
            prepared.append(PreparedBranch(str(branch.id), str(from_bus.id), str(to_bus.id), z_pu.real, z_pu.imag, b_pu, True))

        for cable in getattr(self.network, "cables", ()):
            if not getattr(cable, "in_service", True):
                continue
            if self._branch_outside_active_topology(cable):
                continue
            if not isinstance(cable, Cable):
                raise TypeError(f"Network cable '{getattr(cable, 'id', cable)}' is not a Cable model.")
            from_bus, to_bus = self._resolve_branch_endpoints(cable)
            kv = self._common_branch_voltage(from_bus, to_bus, voltage_bases, cable)
            z_pu = self._per_unit.to_pu_impedance(complex(cable.resistance_ohm, cable.reactance_ohm), kv)
            b_pu = self._per_unit.to_pu_admittance(complex(0.0, cable.shunt_susceptance_siemens), kv).imag
            prepared.append(PreparedBranch(str(cable.id), str(from_bus.id), str(to_bus.id), z_pu.real, z_pu.imag, b_pu, True))
        return tuple(prepared)

    def _prepare_transformers(self, voltage_bases: Mapping[str, float]) -> tuple[PreparedTransformer, ...]:
        prepared: list[PreparedTransformer] = []
        for transformer in getattr(self.network, "transformers", ()):
            if not getattr(transformer, "in_service", True):
                continue
            if self._branch_outside_active_topology(transformer):
                continue
            if not isinstance(transformer, Transformer):
                raise TypeError(f"Network transformer '{getattr(transformer, 'id', transformer)}' is not a Transformer model.")
            from_bus, to_bus = self._resolve_branch_endpoints(transformer)
            reference_kv = transformer.impedance_base_voltage_kv
            from_kv = voltage_bases[str(from_bus.id)]
            if not math.isclose(reference_kv, from_kv, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(
                    f"Transformer '{transformer.id}' impedance reference voltage {reference_kv:g} kV "
                    f"does not match FROM bus voltage base {from_kv:g} kV. "
                    "Declare engineering/PU impedance on the actual transformer-side voltage base."
                )

            if transformer.impedance_basis == "pu":
                z_pu = self._per_unit.convert_impedance_base(
                    complex(transformer.r, transformer.x),
                    transformer.impedance_base_mva,
                    reference_kv,
                    from_kv,
                )
                # Transformer b is an admittance quantity, not an impedance.
                # Convert it from the declared original PU basis using the
                # inverse MVA and corresponding voltage-base relationship.
                b_pu = self._per_unit.convert_admittance_base(
                    complex(0.0, transformer.b),
                    transformer.impedance_base_mva,
                    reference_kv,
                    from_kv,
                ).imag
            elif transformer.impedance_basis == "engineering":
                z_pu = self._per_unit.to_pu_impedance(
                    complex(transformer.r, transformer.x),
                    reference_kv,
                )
                b_pu = self._per_unit.to_pu_admittance(
                    complex(0.0, transformer.b),
                    reference_kv,
                ).imag
            else:
                raise ValueError(
                    f"Transformer '{transformer.id}' has unsupported impedance basis {transformer.impedance_basis!r}."
                )

            prepared.append(
                PreparedTransformer(
                    str(transformer.id),
                    str(from_bus.id),
                    str(to_bus.id),
                    z_pu.real,
                    z_pu.imag,
                    b_pu,
                    transformer.tap,
                    transformer.shift,
                    True,
                )
            )
        return tuple(prepared)

    def _prepare_shunts(self, voltage_bases: Mapping[str, float]) -> tuple[PreparedShunt, ...]:
        prepared: list[PreparedShunt] = []

        for shunt in getattr(self.network, "shunts", ()):
            if not getattr(shunt, "in_service", True):
                continue
            bus = self._resolve_shunt_bus(shunt)
            prepared.append(
                PreparedShunt(
                    str(shunt.id),
                    str(bus.id),
                    float(shunt.g_pu),
                    float(shunt.b_pu),
                    True,
                )
            )

        for equipment in (
            *getattr(self.network, "capacitors", ()),
            *getattr(self.network, "reactors", ()),
        ):
            if not getattr(equipment, "in_service", True):
                continue
            if not isinstance(equipment, (Capacitor, Reactor)):
                raise TypeError(
                    f"Reactive shunt '{getattr(equipment, 'id', equipment)}' is not a supported Capacitor/Reactor model."
                )
            bus = self._resolve_shunt_bus(equipment)
            bus_kv = voltage_bases[str(bus.id)]
            q_mvar = self._finite(
                equipment.get_power()[1],
                f"Reactive shunt '{equipment.id}' reactive power",
            )
            q_pu = self._per_unit.to_pu_power(0.0, q_mvar).imag
            if bus_kv <= 0.0:
                raise ValueError(f"Reactive shunt '{equipment.id}' has invalid bus voltage base.")
            prepared.append(
                PreparedShunt(
                    str(equipment.id),
                    str(bus.id),
                    0.0,
                    -q_pu,
                    True,
                )
            )
        return tuple(prepared)

    def _branch_outside_active_topology(self, branch: Any) -> bool:
        """Return whether the canonical topology snapshot excludes a branch endpoint."""
        active_bus_ids = frozenset(str(bus_id) for bus_id in self.topology_snapshot.bus_ids)
        endpoint_ids = []
        for terminal in (getattr(branch, "from_terminal", None), getattr(branch, "to_terminal", None)):
            endpoint = getattr(terminal, "endpoint", None)
            endpoint_id = getattr(endpoint, "id", None)
            if endpoint_id is not None:
                endpoint_ids.append(str(endpoint_id))
        return bool(endpoint_ids) and any(endpoint_id not in active_bus_ids for endpoint_id in endpoint_ids)

    def _resolve_branch_endpoints(self, branch: Any) -> tuple[Any, Any]:
        records = {(r.equipment_id, r.terminal_role): r.bus_id for r in self.topology_snapshot.equipment_bus_attachments}
        from_id = records.get((str(branch.id), getattr(branch.from_terminal, 'role', '')))
        to_id = records.get((str(branch.id), getattr(branch.to_terminal, 'role', '')))
        if from_id and to_id:
            return self.network.get_by_identity(from_id), self.network.get_by_identity(to_id)
        raise ValueError(f"Element '{getattr(branch, 'id', branch)}' has unresolved branch terminals in the canonical TopologySnapshot.")

    def _resolve_shunt_bus(self, shunt: Any) -> Any:
        role = getattr(getattr(shunt, 'terminal', None), 'role', '')
        bus_id = next((r.bus_id for r in self.topology_snapshot.equipment_bus_attachments if r.equipment_id == str(shunt.id) and r.terminal_role == role), None)
        if bus_id: return self.network.get_by_identity(bus_id)
        raise ValueError(f"Shunt '{getattr(shunt, 'id', shunt)}' has an unresolved terminal in the canonical TopologySnapshot.")

    def _common_branch_voltage(self, from_bus: Any, to_bus: Any, voltage_bases: Mapping[str, float], branch: Any) -> float:
        from_kv = voltage_bases[str(from_bus.id)]
        to_kv = voltage_bases[str(to_bus.id)]
        if not math.isclose(from_kv, to_kv, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"Branch '{getattr(branch, 'id', branch)}' connects incompatible voltage bases ({from_kv:g} kV and {to_kv:g} kV); use a Transformer for a voltage change.")
        return from_kv

    def _to_pu_reactive_power(self, value: float | None) -> float | None:
        return None if value is None else self._per_unit.to_pu_power(0.0, value).imag

    def _prepare_bus_types(self, bus_ids: tuple[str, ...]) -> tuple[PowerFlowBusType, ...]:
        configured = self.power_flow_configuration.bus_types
        expected = set(bus_ids)
        supplied = set(configured)
        missing = expected - supplied
        extra = supplied - expected
        if missing or extra:
            raise ValueError("Power Flow study configuration must match the case Network buses; " f"missing={sorted(missing)!r}, extra={sorted(extra)!r}.")
        return tuple(configured[bus_id] for bus_id in bus_ids)

    def _initial_voltage_pu(self, bus: Any) -> float:
        return self._finite_positive(getattr(bus, "voltage_pu", 1.0), f"Bus '{bus.id}' voltage_pu")

    def _bus_power_spec(self, bus: Any) -> tuple[float, float, float | None, float | None]:
        p = q = 0.0
        attachment_keys = {
            (record.equipment_id, record.terminal_role): record.bus_id
            for record in self.topology_snapshot.equipment_bus_attachments
        }
        bus_id = str(bus.id)
        q_min_total = 0.0
        q_max_total = 0.0
        q_min_seen = q_max_seen = False
        q_min_unbounded = q_max_unbounded = False
        for collection_name in self._INJECTION_COLLECTIONS:
            for equipment in getattr(self.network, collection_name, ()):
                if not isinstance(equipment, Injection) or not getattr(equipment, "in_service", True):
                    continue
                terminal = getattr(equipment, "terminal", None)
                role = getattr(terminal, "role", "")
                attached_bus_id = attachment_keys.get((str(getattr(equipment, "id", equipment)), role))
                if attached_bus_id != bus_id:
                    continue
                ep, eq = equipment.get_power()
                p += self._finite(ep, f"Injection '{getattr(equipment, 'id', equipment)}' active power")
                q += self._finite(eq, f"Injection '{getattr(equipment, 'id', equipment)}' reactive power")
                if hasattr(equipment, "q_min"):
                    value = self._optional_reactive_limit(
                        getattr(equipment, "q_min"),
                        f"Injection '{getattr(equipment, 'id', equipment)}' q_min",
                        lower=True,
                    )
                    q_min_seen = True
                    if value is None:
                        q_min_unbounded = True
                    else:
                        q_min_total += value
                if hasattr(equipment, "q_max"):
                    value = self._optional_reactive_limit(
                        getattr(equipment, "q_max"),
                        f"Injection '{getattr(equipment, 'id', equipment)}' q_max",
                        lower=False,
                    )
                    q_max_seen = True
                    if value is None:
                        q_max_unbounded = True
                    else:
                        q_max_total += value

        # A bus aggregate is bounded on one side only when every
        # contributing capability model supplies a finite bound on that side.
        q_min = q_min_total if q_min_seen and not q_min_unbounded else None
        q_max = q_max_total if q_max_seen and not q_max_unbounded else None
        if q_min is not None and q_max is not None and q_min > q_max:
            raise ValueError(f"Aggregated reactive limits at bus '{bus.id}' are invalid.")
        return p, q, q_min, q_max

    @staticmethod
    def _optional_reactive_limit(value: Any, name: str, *, lower: bool) -> float | None:
        """Normalize an explicitly unbounded Q-limit side to the solver's None contract."""
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric or None.") from exc
        if math.isnan(numeric):
            raise ValueError(f"{name} cannot be NaN.")
        if lower and numeric == -math.inf:
            return None
        if not lower and numeric == math.inf:
            return None
        if not math.isfinite(numeric):
            raise ValueError(f"{name} uses infinity in the wrong bound direction.")
        return numeric

    @staticmethod
    def _finite(value: Any, name: str) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric.") from exc
        if not math.isfinite(numeric):
            raise ValueError(f"{name} must be finite.")
        return numeric

    @classmethod
    def _finite_positive(cls, value: Any, name: str) -> float:
        numeric = cls._finite(value, name)
        if numeric <= 0.0:
            raise ValueError(f"{name} must be greater than zero.")
        return numeric

    def _validate_network(self) -> None:
        if self.network is None:
            raise ValueError("network is required for Power Flow preparation.")
        if not hasattr(self.network, "buses"):
            raise TypeError("network must expose buses.")


__all__ = ["PowerFlowPreparation", "PreparedBranch", "PreparedTransformer", "PreparedShunt", "PreparedPowerFlow"]
