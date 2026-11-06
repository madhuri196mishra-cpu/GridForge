# ============================================================
# File: core/application/services/switching_model_service.py
# GridForge V2 — Switching Model Service
# Author: Subhendu Mishra
# ============================================================

"""Application service boundary for switching-device mutations."""

from __future__ import annotations

from typing import Callable

from core.application.errors import DomainError
from core.application.results import ApplicationResult
from core.application.services._model_service_support import ModelServiceSupport
from core.application.transaction import Transaction
from core.model.breaker import Breaker
from core.model.bus import Bus
from core.model.disconnector import Disconnector
from core.model.fuse import Fuse
from core.model.switch import Switch
from core.model.terminal import Terminal
from core.network.network import Network


class SwitchingModelService(ModelServiceSupport):
    """Owns Application-layer mutation use cases for switching devices."""

    def __init__(
        self,
        network: Network,
        *,
        breaker_close_authorizer: Callable[[Breaker, str], bool] | None = None,
    ) -> None:
        if not isinstance(network, Network):
            raise TypeError("network must be a Network.")
        if breaker_close_authorizer is not None and not callable(breaker_close_authorizer):
            raise TypeError("breaker_close_authorizer must be callable when provided.")
        self._network = network
        self._breaker_close_authorizer = breaker_close_authorizer

    def _authorize_breaker_close(self, breaker: Breaker, *, operation: str) -> None:
        """Fail closed unless Application supplies an explicit ANSI 25 decision."""
        authorizer = self._breaker_close_authorizer
        if authorizer is None:
            raise DomainError(
                code="ANSI25_AUTHORIZATION_UNAVAILABLE",
                message="Breaker closing is denied because no ANSI 25 authorizer is configured.",
                details={"breaker_id": breaker.id, "operation": operation},
            )
        try:
            authorized = authorizer(breaker, operation)
        except Exception as exc:
            raise DomainError(
                code="ANSI25_AUTHORIZATION_FAILED",
                message="Breaker closing is denied because ANSI 25 authorization failed.",
                details={"breaker_id": breaker.id, "operation": operation, "cause": str(exc)},
            ) from exc
        if authorized is not True:
            raise DomainError(
                code="ANSI25_CLOSE_DENIED",
                message="Breaker closing was denied by the ANSI 25 synchronism check.",
                details={"breaker_id": breaker.id, "operation": operation},
            )

    @property
    def network(self) -> Network:
        return self._network

    def create_switch(
        self, *, switch_id: str, name: str = "",
        endpoint_a: Bus | Terminal | None = None,
        endpoint_b: Bus | Terminal | None = None,
        closed: bool = False, in_service: bool = True,
        normally_closed: bool | None = None,
        rated_voltage_kv: float | None = None,
        rated_current_a: float | None = None,
        transaction: Transaction,
    ) -> ApplicationResult[Switch]:
        self._require_transaction(transaction)
        self._require_id(switch_id, "switch_id")
        if endpoint_a is not None:
            self._validate_endpoint(endpoint_a, "endpoint_a")
        if endpoint_b is not None:
            self._validate_endpoint(endpoint_b, "endpoint_b")
        if endpoint_a is not None and endpoint_b is not None:
            self._require_distinct_endpoints(endpoint_a, endpoint_b, "INVALID_SWITCH_ENDPOINTS", "Switch", switch_id)
        self._ensure_not_exists("switch", switch_id, "Switch")
        switch = Switch(id=switch_id, name=name, endpoint_from=endpoint_a, endpoint_to=endpoint_b, closed=closed, in_service=in_service, normally_closed=normally_closed, rated_voltage_kv=rated_voltage_kv, rated_current_a=rated_current_a)
        self._network.add_switch(switch); transaction.record_undo(lambda switch=switch: self._network.remove_switch(switch)); return self._success(switch, "switch", switch_id, f"Switch created: {switch_id}")

    def update_switch(self, *, switch_id: str, name: str | None = None, closed: bool | None = None, in_service: bool | None = None, normally_closed: bool | None = None, rated_voltage_kv: float | None = None, rated_current_a: float | None = None, transaction: Transaction) -> ApplicationResult[Switch]:
        self._require_transaction(transaction)
        self._require_id(switch_id, "switch_id")
        switch = self._get_required("switch", switch_id, "Switch")
        self._require_type(switch, Switch, switch_id, "Switch")
        values = (name, closed, in_service, normally_closed, rated_voltage_kv, rated_current_a)
        if all(value is None for value in values):
            raise DomainError(code="NO_SWITCH_UPDATE", message="At least one mutable Switch property must be specified.", details={"switch_id": switch_id})

        old = {"name": switch.name, "closed": switch.closed, "in_service": switch.in_service, "normally_closed": switch.normally_closed, "rated_voltage_kv": switch.rated_voltage_kv, "rated_current_a": switch.rated_current_a}

        def restore_snapshot() -> None:
            switch.name = old["name"]
            switch.closed = old["closed"]
            switch.in_service = old["in_service"]
            switch.normally_closed = old["normally_closed"]
            switch.rated_voltage_kv = old["rated_voltage_kv"]
            switch.rated_current_a = old["rated_current_a"]

        try:
            for key, value in (("name", name), ("closed", closed), ("in_service", in_service), ("normally_closed", normally_closed), ("rated_voltage_kv", rated_voltage_kv), ("rated_current_a", rated_current_a)):
                if value is not None:
                    if key == "closed":
                        switch.closed = value
                    elif key == "in_service":
                        switch.in_service = value
                    elif key == "normally_closed":
                        switch.normally_closed = value
                    else:
                        setattr(switch, key, value)
            switch.validate_parameters()
        except Exception:
            restore_snapshot()
            raise

        topology_changed = switch.closed != old["closed"] or switch.in_service != old["in_service"]
        if topology_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            restore_snapshot()
            switch.validate_parameters()
            if topology_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(switch, "switch", switch_id, f"Switch updated: {switch_id}")

    def delete_switch(self, *, switch_id: str, transaction: Transaction) -> ApplicationResult[Switch]:
        self._require_transaction(transaction); self._require_id(switch_id, "switch_id"); switch = self._get_required("switch", switch_id, "Switch"); self._require_type(switch, Switch, switch_id, "Switch"); self._network.remove_switch(switch); transaction.record_undo(lambda switch=switch: self._network.add_switch(switch)); return self._success(switch, "switch", switch_id, f"Switch deleted: {switch_id}")

    def open_switch(self, *, switch_id: str, transaction: Transaction) -> ApplicationResult[Switch]: return self._set_switch_closed(switch_id=switch_id, closed=False, transaction=transaction)
    def close_switch(self, *, switch_id: str, transaction: Transaction) -> ApplicationResult[Switch]: return self._set_switch_closed(switch_id=switch_id, closed=True, transaction=transaction)
    def put_switch_in_service(self, *, switch_id: str, transaction: Transaction) -> ApplicationResult[Switch]: return self._set_switch_service(switch_id=switch_id, in_service=True, transaction=transaction)
    def take_switch_out_of_service(self, *, switch_id: str, transaction: Transaction) -> ApplicationResult[Switch]: return self._set_switch_service(switch_id=switch_id, in_service=False, transaction=transaction)

    def _set_switch_closed(self, *, switch_id: str, closed: bool, transaction: Transaction) -> ApplicationResult[Switch]:
        self._require_transaction(transaction); self._require_id(switch_id, "switch_id"); switch = self._get_required("switch", switch_id, "Switch"); self._require_type(switch, Switch, switch_id, "Switch"); old = switch.closed; switch.closed = closed; topology_changed = old != switch.closed
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            switch.closed = old
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(switch, "switch", switch_id, f"Switch {'closed' if closed else 'opened'}: {switch_id}")

    def _set_switch_service(self, *, switch_id: str, in_service: bool, transaction: Transaction) -> ApplicationResult[Switch]:
        self._require_transaction(transaction); self._require_id(switch_id, "switch_id"); switch = self._get_required("switch", switch_id, "Switch"); self._require_type(switch, Switch, switch_id, "Switch"); old = switch.in_service; switch.in_service = in_service; topology_changed = old != switch.in_service
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            switch.in_service = old
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(switch, "switch", switch_id, f"Switch {'put in service' if in_service else 'taken out of service'}: {switch_id}")

    def create_breaker(self, *, breaker_id: str, endpoint_from: Bus | Terminal | None = None, endpoint_to: Bus | Terminal | None = None, name: str = "", in_service: bool = True, closed: bool = True, failed: bool = False, voltage_kv: float | None = None, current_a: float | None = None, interrupting_ka: float | None = None, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction); self._require_id(breaker_id, "breaker_id")
        if endpoint_from is not None: self._validate_endpoint(endpoint_from, "endpoint_from")
        if endpoint_to is not None: self._validate_endpoint(endpoint_to, "endpoint_to")
        if endpoint_from is not None and endpoint_to is not None: self._require_distinct_endpoints(endpoint_from, endpoint_to, "INVALID_BREAKER_ENDPOINTS", "Breaker", breaker_id)
        self._ensure_not_exists("breaker", breaker_id, "Breaker")
        breaker = Breaker(id=breaker_id, endpoint_from=endpoint_from, endpoint_to=endpoint_to, name=name, in_service=in_service, closed=closed, failed=failed, voltage_kv=voltage_kv, current_a=current_a, interrupting_ka=interrupting_ka)
        if closed:
            self._authorize_breaker_close(breaker, operation="create")
        self._network.add_breaker(breaker); transaction.record_undo(lambda breaker=breaker: self._network.remove_breaker(breaker)); return self._success(breaker, "breaker", breaker_id, f"Breaker created: {breaker_id}")

    def update_breaker(self, *, breaker_id: str, name: str | None = None, in_service: bool | None = None, closed: bool | None = None, failed: bool | None = None, voltage_kv: float | None = None, current_a: float | None = None, interrupting_ka: float | None = None, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction)
        self._require_id(breaker_id, "breaker_id")
        breaker = self._get_required("breaker", breaker_id, "Breaker")
        self._require_type(breaker, Breaker, breaker_id, "Breaker")
        values = (name, in_service, closed, failed, voltage_kv, current_a, interrupting_ka)
        if all(value is None for value in values):
            raise DomainError(code="NO_BREAKER_UPDATE", message="At least one mutable Breaker property must be specified.", details={"breaker_id": breaker_id})

        old = {"name": breaker.name, "in_service": breaker.in_service, "closed": breaker.closed, "failed": breaker.failed, "voltage_kv": breaker.voltage_kv, "current_a": breaker.current_a, "interrupting_ka": breaker.interrupting_ka}
        target_closed = breaker.closed if closed is None else closed
        target_in_service = breaker.in_service if in_service is None else in_service
        if target_closed and target_in_service and not (breaker.closed and breaker.in_service):
            self._authorize_breaker_close(breaker, operation="update_or_reenergize")

        def restore_snapshot() -> None:
            for key, value in old.items():
                setattr(breaker, key, value)

        try:
            for key, value in (("name", name), ("in_service", in_service), ("closed", closed), ("failed", failed), ("voltage_kv", voltage_kv), ("current_a", current_a), ("interrupting_ka", interrupting_ka)):
                if value is not None:
                    setattr(breaker, key, value)
            breaker.validate_parameters()
        except Exception:
            restore_snapshot()
            raise

        topology_changed = breaker.closed != old["closed"] or breaker.in_service != old["in_service"]
        if topology_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            if old["closed"] and old["in_service"] and not (breaker.closed and breaker.in_service):
                self._authorize_breaker_close(breaker, operation="undo_update_reenergize")
            restore_snapshot()
            breaker.validate_parameters()
            if topology_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(breaker, "breaker", breaker_id, f"Breaker updated: {breaker_id}")

    def delete_breaker(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction)
        self._require_id(breaker_id, "breaker_id")
        breaker = self._get_required("breaker", breaker_id, "Breaker")
        self._require_type(breaker, Breaker, breaker_id, "Breaker")
        self._network.remove_breaker(breaker)

        def restore() -> None:
            if breaker.closed and breaker.in_service:
                self._authorize_breaker_close(breaker, operation="undo_delete_reenergize")
            self._network.add_breaker(breaker)

        transaction.record_undo(restore)
        return self._success(breaker, "breaker", breaker_id, f"Breaker deleted: {breaker_id}")

    def open_breaker(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]: return self._set_breaker_closed(breaker_id=breaker_id, closed=False, transaction=transaction)
    def close_breaker(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]: return self._set_breaker_closed(breaker_id=breaker_id, closed=True, transaction=transaction)
    def put_breaker_in_service(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]: return self._set_breaker_service(breaker_id=breaker_id, in_service=True, transaction=transaction)
    def take_breaker_out_of_service(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]: return self._set_breaker_service(breaker_id=breaker_id, in_service=False, transaction=transaction)

    def trip_breaker(self, *, breaker_id: str, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction); self._require_id(breaker_id, "breaker_id"); breaker = self._get_required("breaker", breaker_id, "Breaker"); self._require_type(breaker, Breaker, breaker_id, "Breaker"); old = breaker.closed; breaker.trip(); topology_changed = old != breaker.closed
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            breaker.close() if old else breaker.open()
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(breaker, "breaker", breaker_id, f"Breaker tripped: {breaker_id}")

    def _set_breaker_closed(self, *, breaker_id: str, closed: bool, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction); self._require_id(breaker_id, "breaker_id"); breaker = self._get_required("breaker", breaker_id, "Breaker"); self._require_type(breaker, Breaker, breaker_id, "Breaker"); old = breaker.closed
        if closed and not old:
            self._authorize_breaker_close(breaker, operation="close")
        breaker.close() if closed else breaker.open(); topology_changed = old != breaker.closed
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            breaker.close() if old else breaker.open()
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(breaker, "breaker", breaker_id, f"Breaker {'closed' if closed else 'opened'}: {breaker_id}")

    def _set_breaker_service(self, *, breaker_id: str, in_service: bool, transaction: Transaction) -> ApplicationResult[Breaker]:
        self._require_transaction(transaction)
        self._require_id(breaker_id, "breaker_id")
        breaker = self._get_required("breaker", breaker_id, "Breaker")
        self._require_type(breaker, Breaker, breaker_id, "Breaker")
        old = breaker.in_service
        if in_service and not old and breaker.closed:
            self._authorize_breaker_close(breaker, operation="reenergize_in_service")
        breaker.in_service = in_service
        topology_changed = old != breaker.in_service
        if topology_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            if old and not breaker.in_service and breaker.closed:
                self._authorize_breaker_close(breaker, operation="undo_reenergize_in_service")
            breaker.in_service = old
            if topology_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(breaker, "breaker", breaker_id, f"Breaker {'put in service' if in_service else 'taken out of service'}: {breaker_id}")

    def create_disconnector(self, *, disconnector_id: str, voltage_kv: float, rated_current_a: float, endpoint_from: Bus | Terminal | None = None, endpoint_to: Bus | Terminal | None = None, operating_time: float | None = None, closed: bool = True, in_service: bool = True, normally_closed: bool | None = None, name: str = "", transaction: Transaction) -> ApplicationResult[Disconnector]:
        self._require_transaction(transaction); self._require_id(disconnector_id, "disconnector_id")
        if endpoint_from is not None: self._validate_endpoint(endpoint_from, "endpoint_from")
        if endpoint_to is not None: self._validate_endpoint(endpoint_to, "endpoint_to")
        if endpoint_from is not None and endpoint_to is not None: self._require_distinct_endpoints(endpoint_from, endpoint_to, "INVALID_DISCONNECTOR_ENDPOINTS", "Disconnector", disconnector_id)
        self._ensure_not_exists("disconnector", disconnector_id, "Disconnector")
        disconnector = Disconnector(id=disconnector_id, voltage_kv=voltage_kv, rated_current_a=rated_current_a, endpoint_from=endpoint_from, endpoint_to=endpoint_to, operating_time=operating_time, closed=closed, in_service=in_service, normally_closed=normally_closed, name=name)
        self._network.add_disconnector(disconnector); transaction.record_undo(lambda disconnector=disconnector: self._network.remove_disconnector(disconnector)); return self._success(disconnector, "disconnector", disconnector_id, f"Disconnector created: {disconnector_id}")

    def update_disconnector(self, *, disconnector_id: str, voltage_kv: float | None = None, rated_current_a: float | None = None, operating_time: float | None = None, closed: bool | None = None, in_service: bool | None = None, normally_closed: bool | None = None, name: str | None = None, transaction: Transaction) -> ApplicationResult[Disconnector]:
        self._require_transaction(transaction)
        self._require_id(disconnector_id, "disconnector_id")
        disconnector = self._get_required("disconnector", disconnector_id, "Disconnector")
        self._require_type(disconnector, Disconnector, disconnector_id, "Disconnector")
        values = (voltage_kv, rated_current_a, operating_time, closed, in_service, normally_closed, name)
        if all(value is None for value in values):
            raise DomainError(code="NO_DISCONNECTOR_UPDATE", message="At least one mutable Disconnector property must be specified.", details={"disconnector_id": disconnector_id})

        old = {"voltage_kv": disconnector.voltage_kv, "rated_current_a": disconnector.rated_current_a, "operating_time": disconnector.operating_time, "closed": disconnector.closed, "in_service": disconnector.in_service, "normally_closed": disconnector.normally_closed, "name": disconnector.name}

        def restore_snapshot() -> None:
            disconnector.voltage_kv = old["voltage_kv"]
            disconnector.rated_current_a = old["rated_current_a"]
            disconnector.operating_time = old["operating_time"]
            disconnector.closed = old["closed"]
            disconnector.in_service = old["in_service"]
            disconnector.normally_closed = old["normally_closed"]
            disconnector.name = old["name"]

        try:
            for key, value in (("voltage_kv", voltage_kv), ("rated_current_a", rated_current_a), ("operating_time", operating_time), ("name", name), ("normally_closed", normally_closed)):
                if value is not None:
                    setattr(disconnector, key, value)
            if closed is not None:
                disconnector.closed = closed
            if in_service is not None:
                disconnector.in_service = in_service
            disconnector.validate_parameters()
        except Exception:
            restore_snapshot()
            raise

        topology_changed = disconnector.closed != old["closed"] or disconnector.in_service != old["in_service"]
        if topology_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            restore_snapshot()
            disconnector.validate_parameters()
            if topology_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(disconnector, "disconnector", disconnector_id, f"Disconnector updated: {disconnector_id}")

    def delete_disconnector(self, *, disconnector_id: str, transaction: Transaction) -> ApplicationResult[Disconnector]:
        self._require_transaction(transaction); self._require_id(disconnector_id, "disconnector_id"); disconnector = self._get_required("disconnector", disconnector_id, "Disconnector"); self._require_type(disconnector, Disconnector, disconnector_id, "Disconnector"); self._network.remove_disconnector(disconnector); transaction.record_undo(lambda disconnector=disconnector: self._network.add_disconnector(disconnector)); return self._success(disconnector, "disconnector", disconnector_id, f"Disconnector deleted: {disconnector_id}")

    def open_disconnector(self, *, disconnector_id: str, transaction: Transaction) -> ApplicationResult[Disconnector]: return self._set_disconnector_closed(disconnector_id=disconnector_id, closed=False, transaction=transaction)
    def close_disconnector(self, *, disconnector_id: str, transaction: Transaction) -> ApplicationResult[Disconnector]: return self._set_disconnector_closed(disconnector_id=disconnector_id, closed=True, transaction=transaction)
    def put_disconnector_in_service(self, *, disconnector_id: str, transaction: Transaction) -> ApplicationResult[Disconnector]: return self._set_disconnector_service(disconnector_id=disconnector_id, in_service=True, transaction=transaction)
    def take_disconnector_out_of_service(self, *, disconnector_id: str, transaction: Transaction) -> ApplicationResult[Disconnector]: return self._set_disconnector_service(disconnector_id=disconnector_id, in_service=False, transaction=transaction)

    def _set_disconnector_closed(self, *, disconnector_id: str, closed: bool, transaction: Transaction) -> ApplicationResult[Disconnector]:
        self._require_transaction(transaction); self._require_id(disconnector_id, "disconnector_id"); disconnector = self._get_required("disconnector", disconnector_id, "Disconnector"); self._require_type(disconnector, Disconnector, disconnector_id, "Disconnector"); old = disconnector.closed; disconnector.closed = closed; topology_changed = old != disconnector.closed
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            disconnector.closed = old
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(disconnector, "disconnector", disconnector_id, f"Disconnector {'closed' if closed else 'opened'}: {disconnector_id}")

    def _set_disconnector_service(self, *, disconnector_id: str, in_service: bool, transaction: Transaction) -> ApplicationResult[Disconnector]:
        self._require_transaction(transaction); self._require_id(disconnector_id, "disconnector_id"); disconnector = self._get_required("disconnector", disconnector_id, "Disconnector"); self._require_type(disconnector, Disconnector, disconnector_id, "Disconnector"); old = disconnector.in_service; disconnector.in_service = in_service; topology_changed = old != disconnector.in_service
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            disconnector.in_service = old
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(disconnector, "disconnector", disconnector_id, f"Disconnector {'put in service' if in_service else 'taken out of service'}: {disconnector_id}")

    def create_fuse(self, *, fuse_id: str, endpoint_from: Bus | Terminal | None = None, endpoint_to: Bus | Terminal | None = None, name: str = "", rated_current_a: float | None = None, rated_voltage_v: float | None = None, interrupting_rating_ka: float | None = None, in_service: bool = True, blown: bool = False, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction); self._require_id(fuse_id, "fuse_id")
        if endpoint_from is not None: self._validate_endpoint(endpoint_from, "endpoint_from")
        if endpoint_to is not None: self._validate_endpoint(endpoint_to, "endpoint_to")
        if endpoint_from is not None and endpoint_to is not None: self._require_distinct_endpoints(endpoint_from, endpoint_to, "INVALID_FUSE_ENDPOINTS", "Fuse", fuse_id)
        self._ensure_not_exists("fuse", fuse_id, "Fuse")
        fuse = Fuse(id=fuse_id, endpoint_from=endpoint_from, endpoint_to=endpoint_to, name=name, rated_current_a=rated_current_a, rated_voltage_v=rated_voltage_v, interrupting_rating_ka=interrupting_rating_ka, in_service=in_service, blown=blown)
        self._network.add_fuse(fuse); transaction.record_undo(lambda fuse=fuse: self._network.remove_fuse(fuse)); return self._success(fuse, "fuse", fuse_id, f"Fuse created: {fuse_id}")

    def update_fuse(self, *, fuse_id: str, name: str | None = None, rated_current_a: float | None = None, rated_voltage_v: float | None = None, interrupting_rating_ka: float | None = None, in_service: bool | None = None, blown: bool | None = None, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction)
        self._require_id(fuse_id, "fuse_id")
        fuse = self._get_required("fuse", fuse_id, "Fuse")
        self._require_type(fuse, Fuse, fuse_id, "Fuse")
        values = (name, rated_current_a, rated_voltage_v, interrupting_rating_ka, in_service, blown)
        if all(value is None for value in values):
            raise DomainError(code="NO_FUSE_UPDATE", message="At least one mutable Fuse property must be specified.", details={"fuse_id": fuse_id})

        old = {"name": fuse.name, "rated_current_a": fuse.rated_current_a, "rated_voltage_v": fuse.rated_voltage_v, "interrupting_rating_ka": fuse.interrupting_rating_ka, "in_service": fuse.in_service, "blown": fuse.blown}

        def restore_snapshot() -> None:
            for key, value in old.items():
                setattr(fuse, key, value)

        try:
            for key, value in (("name", name), ("rated_current_a", rated_current_a), ("rated_voltage_v", rated_voltage_v), ("interrupting_rating_ka", interrupting_rating_ka), ("in_service", in_service), ("blown", blown)):
                if value is not None:
                    setattr(fuse, key, value)
            fuse.validate_parameters()
        except Exception:
            restore_snapshot()
            raise

        topology_changed = fuse.in_service != old["in_service"] or fuse.blown != old["blown"]
        if topology_changed:
            self._network.invalidate_topology()

        def restore() -> None:
            restore_snapshot()
            fuse.validate_parameters()
            if topology_changed:
                self._network.invalidate_topology()

        transaction.record_undo(restore)
        return self._success(fuse, "fuse", fuse_id, f"Fuse updated: {fuse_id}")

    def delete_fuse(self, *, fuse_id: str, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction); self._require_id(fuse_id, "fuse_id"); fuse = self._get_required("fuse", fuse_id, "Fuse"); self._require_type(fuse, Fuse, fuse_id, "Fuse"); self._network.remove_fuse(fuse); transaction.record_undo(lambda fuse=fuse: self._network.add_fuse(fuse)); return self._success(fuse, "fuse", fuse_id, f"Fuse deleted: {fuse_id}")

    def blow_fuse(self, *, fuse_id: str, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction); self._require_id(fuse_id, "fuse_id"); fuse = self._get_required("fuse", fuse_id, "Fuse"); self._require_type(fuse, Fuse, fuse_id, "Fuse"); old = fuse.blown; fuse.blow(); topology_changed = old != fuse.blown
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            fuse.reset() if not old else fuse.blow()
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(fuse, "fuse", fuse_id, f"Fuse blown: {fuse_id}")

    def reset_fuse(self, *, fuse_id: str, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction); self._require_id(fuse_id, "fuse_id"); fuse = self._get_required("fuse", fuse_id, "Fuse"); self._require_type(fuse, Fuse, fuse_id, "Fuse"); old = fuse.blown; fuse.reset(); topology_changed = old != fuse.blown
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            fuse.blow() if old else fuse.reset()
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(fuse, "fuse", fuse_id, f"Fuse reset: {fuse_id}")

    def put_fuse_in_service(self, *, fuse_id: str, transaction: Transaction) -> ApplicationResult[Fuse]: return self._set_fuse_service(fuse_id=fuse_id, in_service=True, transaction=transaction)
    def take_fuse_out_of_service(self, *, fuse_id: str, transaction: Transaction) -> ApplicationResult[Fuse]: return self._set_fuse_service(fuse_id=fuse_id, in_service=False, transaction=transaction)

    def _set_fuse_service(self, *, fuse_id: str, in_service: bool, transaction: Transaction) -> ApplicationResult[Fuse]:
        self._require_transaction(transaction); self._require_id(fuse_id, "fuse_id"); fuse = self._get_required("fuse", fuse_id, "Fuse"); self._require_type(fuse, Fuse, fuse_id, "Fuse"); old = fuse.in_service; fuse.set_in_service(in_service); topology_changed = old != fuse.in_service
        if topology_changed:
            self._network.invalidate_topology()
        def restore() -> None:
            fuse.set_in_service(old)
            if topology_changed:
                self._network.invalidate_topology()
        transaction.record_undo(restore); return self._success(fuse, "fuse", fuse_id, f"Fuse {'put in service' if in_service else 'taken out of service'}: {fuse_id}")


__all__ = ["SwitchingModelService"]
