"""
GridForge Transient-Stability Events
====================================

Event scheduling and dispatch for dynamic simulations.

Responsibilities
----------------
- Register simulation events.
- Maintain deterministic event ordering.
- Detect events occurring within a simulation interval.
- Dispatch each event exactly once.
- Support event cancellation and reset.
- Keep event handling independent of network and machine models.

Non-responsibilities
--------------------
This module does NOT:

- modify network topology directly;
- solve the electrical network;
- implement breakers;
- implement faults;
- implement machine dynamics;
- perform numerical integration.

An event is represented by a time, an action, and optional metadata.

Examples
--------

Fault application:

    events.add(
        time=1.0,
        action=apply_fault,
        event_type="fault_apply",
    )

Fault clearing:

    events.add(
        time=1.1,
        action=clear_fault,
        event_type="fault_clear",
    )

The event manager determines WHEN the action is dispatched.

The action determines WHAT happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping


# ======================================================================
# TYPES
# ======================================================================

EventAction = Callable[[], Any]


# ======================================================================
# ERRORS
# ======================================================================


class EventError(
    RuntimeError
):
    """Base exception for simulation-event errors.

    Failed event actions are recorded before this exception is raised. The
    corresponding immutable EventExecution is available on the execution
    attribute.
    """

    def __init__(self, message: str, *, execution: "EventExecution | None" = None) -> None:
        super().__init__(message)
        self.execution = execution


class DuplicateEventError(
    EventError
):
    """Raised when an event ID is duplicated."""


class UnknownEventError(
    EventError
):
    """Raised when an unknown event is referenced."""


class EventBatchConflictError(EventError):
    """Raised when one simultaneous electrical event batch has conflicting targets."""


# ======================================================================
# EVENT
# ======================================================================


@dataclass(frozen=True, slots=True)
class EventOperation:
    """Typed target/operation identity used to validate simultaneous mutations."""

    target_kind: str
    target_id: str
    operation: str

    def __post_init__(self) -> None:
        for name in ("target_kind", "target_id", "operation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Event operation {name} must be a non-empty string.")
            object.__setattr__(self, name, value.strip())

    @property
    def conflict_key(self) -> tuple[str, str]:
        kind = "switchable_equipment" if self.target_kind in {"switch", "equipment"} else self.target_kind
        return kind, self.target_id


@dataclass
class SimulationEvent:
    """
    Scheduled simulation event.

    Parameters
    ----------
    time:
        Event time [s].

    action:
        Callable executed when the event is dispatched.

    event_id:
        Unique event identifier.

    event_type:
        Optional semantic event type.

    description:
        Human-readable description.

    priority:
        Ordering priority for events occurring at the same time.

        Lower values execute first.

    enabled:
        Whether the event is active.

    triggered:
        Whether the event has already been dispatched.

    metadata:
        Optional event-specific metadata.

    Notes
    -----
    The event manager owns scheduling state, not engineering behavior.

    A fault, breaker operation, generator trip, load switching operation,
    or topology change is implemented elsewhere and supplied as an
    action.
    """

    time: float

    action: EventAction

    event_id: str

    event_type: str = "generic"

    description: str = ""

    priority: int = 0

    enabled: bool = True

    triggered: bool = False

    metadata: dict[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    revision_reader: Callable[[], int | None] | None = None
    operation: EventOperation | None = None

    def __post_init__(
        self,
    ) -> None:

        try:
            self.time = float(self.time)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EventError("Event time must be a finite numeric value.") from exc

        if not math.isfinite(self.time):
            raise EventError("Event time must be finite.")
        if self.time < 0.0:
            raise EventError("Event time cannot be negative.")

        if not str(
            self.event_id
        ).strip():

            raise EventError(
                "event_id cannot "
                "be empty."
            )

        if not callable(
            self.action
        ):

            raise EventError(
                f"Action for event "
                f"'{self.event_id}' "
                "must be callable."
            )

        self.event_id = str(
            self.event_id
        )

        self.event_type = str(
            self.event_type
        )

        self.description = str(
            self.description
        )

        self.priority = int(
            self.priority
        )
        if self.revision_reader is not None and not callable(self.revision_reader):
            raise EventError("revision_reader must be callable when supplied.")
        if self.operation is not None and not isinstance(self.operation, EventOperation):
            raise EventError("operation must be an EventOperation when supplied.")

    def reset(
        self,
    ) -> None:
        """Return the event to an untriggered state."""

        self.triggered = False


# ======================================================================
# EVENT RESULT
# ======================================================================


def _freeze_event_value(value: Any) -> Any:
    """Detach event evidence into immutable, serialization-safe values."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            key if isinstance(key, (str, int, float, bool)) else repr(key): _freeze_event_value(item)
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_event_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_event_value(item) for item in value)
    return repr(value)


@dataclass(frozen=True, slots=True)
class EventTransition:
    """Typed immutable summary of one detached electrical-state transition."""

    transition_type: str
    target_kind: str | None = None
    target_id: str | None = None
    previous_state: Any = None
    new_state: Any = None
    source_topology_revision: int | None = None
    pre_event_revision: int | None = None
    post_event_revision: int | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.transition_type, str) or not self.transition_type.strip():
            raise ValueError("Event transition type must be non-empty.")
        for name in ("target_kind", "target_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"Event transition {name} must be non-empty when supplied.")
        for name in ("source_topology_revision", "pre_event_revision", "post_event_revision"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"Event transition {name} must be a non-negative integer when supplied.")
        if not isinstance(self.details, Mapping):
            raise TypeError("Event transition details must be a mapping.")
        object.__setattr__(self, "transition_type", self.transition_type.strip())
        object.__setattr__(self, "target_kind", None if self.target_kind is None else self.target_kind.strip())
        object.__setattr__(self, "target_id", None if self.target_id is None else self.target_id.strip())
        object.__setattr__(self, "previous_state", _freeze_event_value(self.previous_state))
        object.__setattr__(self, "new_state", _freeze_event_value(self.new_state))
        object.__setattr__(self, "details", _freeze_event_value(self.details))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventTransition | None":
        """Build a typed transition only from an explicit transition payload."""
        transition_type = value.get("transition_type")
        if not isinstance(transition_type, str) or not transition_type.strip():
            return None
        target_kind = value.get("target_kind")
        target_id = value.get("target_id")
        if target_id is None:
            for key, kind in (("switch_id", "switch"), ("equipment_id", "equipment"), ("fault_id", "fault"), ("fault_bus_id", "fault")):
                if value.get(key) is not None:
                    target_id = str(value[key])
                    if target_kind is None:
                        target_kind = kind
                    break
        known = {
            "transition_type", "target_kind", "target_id", "previous_state", "new_state",
            "source_topology_revision", "pre_event_revision", "post_event_revision",
            "switch_id", "equipment_id", "fault_id",
            "previous_fault_active", "new_fault_active",
        }
        previous_state = value.get("previous_state", value.get("previous_fault_active"))
        new_state = value.get("new_state", value.get("new_fault_active"))
        return cls(
            transition_type=transition_type,
            target_kind=None if target_kind is None else str(target_kind),
            target_id=None if target_id is None else str(target_id),
            previous_state=previous_state,
            new_state=new_state,
            source_topology_revision=value.get("source_topology_revision"),
            pre_event_revision=value.get("pre_event_revision"),
            post_event_revision=value.get("post_event_revision"),
            details={str(key): item for key, item in value.items() if key not in known},
        )


@dataclass(frozen=True, slots=True)
class EventExecution:
    """Immutable evidence for one event dispatch attempt.

    The legacy time field remains the scheduled timestamp. scheduled_time and
    dispatch_time make the distinction explicit for new consumers.
    """

    event_id: str
    event_type: str
    time: float
    result: Any = None
    scheduled_time: float | None = None
    dispatch_time: float | None = None
    priority: int = 0
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    status: str = "succeeded"
    failure_type: str | None = None
    failure_message: str | None = None
    pre_event_revision: int | None = None
    post_event_revision: int | None = None
    post_event_re_evaluation_status: str = "not_run"
    post_event_re_evaluation_failure_type: str | None = None
    post_event_re_evaluation_failure_message: str | None = None
    transition: EventTransition | None = None

    def __post_init__(self) -> None:
        scheduled = float(self.time if self.scheduled_time is None else self.scheduled_time)
        dispatched = float(scheduled if self.dispatch_time is None else self.dispatch_time)
        if not all(math.isfinite(value) for value in (scheduled, dispatched)) or scheduled < 0.0 or dispatched < 0.0:
            raise ValueError("Event execution timestamps must be finite and non-negative.")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("Event execution event_id must be non-empty.")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("Event execution event_type must be non-empty.")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Event execution metadata must be a mapping.")
        if self.status not in {"succeeded", "failed"}:
            raise ValueError("Event execution status must be succeeded or failed.")
        if self.status == "failed" and (not self.failure_type or self.failure_message is None):
            raise ValueError("Failed event executions require failure type and message evidence.")
        if self.status == "succeeded" and (self.failure_type is not None or self.failure_message is not None):
            raise ValueError("Successful event executions cannot carry action-failure evidence.")
        if self.post_event_re_evaluation_status not in {"not_run", "succeeded", "failed"}:
            raise ValueError("Post-event re-evaluation status must be not_run, succeeded or failed.")
        if self.post_event_re_evaluation_status == "failed" and (
            not self.post_event_re_evaluation_failure_type
            or self.post_event_re_evaluation_failure_message is None
        ):
            raise ValueError("Failed post-event re-evaluation requires failure type and message evidence.")
        for revision in (self.pre_event_revision, self.post_event_revision):
            if revision is not None and (
                isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
            ):
                raise ValueError("Event execution revisions must be non-negative integers when supplied.")
        object.__setattr__(self, "event_id", self.event_id.strip())
        object.__setattr__(self, "event_type", self.event_type.strip())
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "description", str(self.description))
        object.__setattr__(self, "time", scheduled)
        object.__setattr__(self, "scheduled_time", scheduled)
        object.__setattr__(self, "dispatch_time", dispatched)
        transition = self.transition
        if transition is None and isinstance(self.result, Mapping):
            transition = EventTransition.from_mapping(self.result)
        if transition is not None and not isinstance(transition, EventTransition):
            raise TypeError("Event execution transition must be an EventTransition or None.")
        object.__setattr__(self, "metadata", _freeze_event_value(self.metadata))
        object.__setattr__(self, "result", _freeze_event_value(self.result))
        object.__setattr__(self, "transition", transition)


# ======================================================================
# EVENT MANAGER
# ======================================================================


class EventManager:
    """
    Deterministic transient-stability event scheduler.

    Parameters
    ----------
    tolerance:
        Time tolerance used when determining whether an event lies
        inside a simulation interval.

    Notes
    -----
    Events are processed by interval rather than exact floating-point
    time equality.

    For example, if the solver moves from:

        t = 0.99

    to:

        t = 1.01

    an event scheduled at:

        t = 1.00

    is still detected.

    This is essential for numerical transient-stability simulation.
    """

    def __init__(
        self,
        tolerance: float = 1e-9,
    ) -> None:

        try:
            resolved_tolerance = float(tolerance)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EventError("Event tolerance must be a finite numeric value.") from exc

        if not math.isfinite(resolved_tolerance):
            raise EventError("Event tolerance must be finite.")
        if resolved_tolerance < 0.0:
            raise EventError("Event tolerance cannot be negative.")

        self._tolerance = resolved_tolerance

        self._events: dict[
            str,
            SimulationEvent,
        ] = {}

        self._execution_order = 0

        self._history: list[
            EventExecution
        ] = []

    # ==================================================================
    # PROPERTIES
    # ==================================================================

    @property
    def tolerance(
        self,
    ) -> float:
        """Event-time tolerance."""

        return self._tolerance

    @property
    def events(
        self,
    ) -> tuple[
        SimulationEvent,
        ...,
    ]:
        """
        Return events in deterministic execution order.
        """

        return tuple(
            sorted(
                self._events.values(),
                key=self._sort_key,
            )
        )

    @property
    def history(
        self,
    ) -> tuple[
        EventExecution,
        ...,
    ]:
        """Return immutable execution history."""

        return tuple(
            self._history
        )

    @property
    def pending_events(
        self,
    ) -> tuple[
        SimulationEvent,
        ...,
    ]:
        """Return enabled events that have not executed."""

        return tuple(
            event
            for event in self.events
            if (
                event.enabled
                and not event.triggered
            )
        )

    # ==================================================================
    # REGISTRATION
    # ==================================================================

    def add(
        self,
        time: float,
        action: EventAction,
        *,
        event_id: str,
        event_type: str = "generic",
        description: str = "",
        priority: int = 0,
        metadata: dict[
            str,
            Any,
        ] | None = None,
        revision_reader: Callable[[], int | None] | None = None,
        operation: EventOperation | None = None,
    ) -> SimulationEvent:
        """
        Register a simulation event.

        Event IDs must be unique.
        """

        event_id = str(
            event_id
        )

        if event_id in self._events:

            raise DuplicateEventError(
                f"Event '{event_id}' "
                "already exists."
            )

        event = SimulationEvent(
            time=time,
            action=action,
            event_id=event_id,
            event_type=event_type,
            description=description,
            priority=priority,
            metadata=(
                {}
                if metadata is None
                else dict(metadata)
            ),
            revision_reader=revision_reader,
            operation=operation,
        )

        self._events[
            event_id
        ] = event

        return event

    # ==================================================================
    # BACKWARD-COMPATIBLE REGISTRATION
    # ==================================================================

    def add_event(
        self,
        time: float,
        action: EventAction,
        *,
        event_id: str | None = None,
        event_type: str = "generic",
        description: str = "",
        priority: int = 0,
        metadata: dict[
            str,
            Any,
        ] | None = None,
        revision_reader: Callable[[], int | None] | None = None,
        operation: EventOperation | None = None,
    ) -> SimulationEvent:
        """
        Register an event using the legacy method name.

        ``event_id`` is generated when omitted.
        """

        if event_id is None:

            event_id = (
                f"event_{len(self._events):06d}"
            )

        return self.add(
            time=time,
            action=action,
            event_id=event_id,
            event_type=event_type,
            description=description,
            priority=priority,
            metadata=metadata,
            revision_reader=revision_reader,
            operation=operation,
        )

    # ==================================================================
    # ENABLE / DISABLE
    # ==================================================================

    def enable(
        self,
        event_id: str,
    ) -> None:
        """Enable a scheduled event."""

        event = self._get_event(
            event_id
        )

        event.enabled = True

    def disable(
        self,
        event_id: str,
    ) -> None:
        """Disable a scheduled event."""

        event = self._get_event(
            event_id
        )

        event.enabled = False

    def cancel(
        self,
        event_id: str,
    ) -> None:
        """
        Disable and remove an event.

        A cancelled event is removed permanently from the scheduler.
        """

        if event_id not in self._events:

            raise UnknownEventError(
                f"Unknown event "
                f"'{event_id}'."
            )

        del self._events[
            event_id
        ]

    # ==================================================================
    # PROCESSING
    # ==================================================================

    def process(
        self,
        t: float,
    ) -> tuple[
        EventExecution,
        ...,
    ]:
        """
        Process all events scheduled at or before ``t``.

        This method is retained for compatibility with a simulation loop
        that advances to exact event times.

        Events are dispatched once.
        """

        current_time = self._validate_time(
            t
        )

        executions: list[EventExecution] = []
        candidates = [
            event for event in self.events
            if event.enabled and not event.triggered
            and event.time <= current_time + self._tolerance
        ]
        self._validate_candidate_batches(candidates)
        # A validated batch is dispatched sequentially in canonical
        # time/priority/ID order. Mutations are visible to later actions;
        # an already-applied prefix is not rolled back if a later action fails.
        for event in candidates:
            executions.append(self._execute_event(event, dispatch_time=current_time))

        return tuple(
            executions
        )

    def process_interval(
        self,
        t_start: float,
        t_end: float,
    ) -> tuple[
        EventExecution,
        ...,
    ]:
        """
        Process all events lying inside a simulation interval.

        Parameters
        ----------
        t_start:
            Beginning of numerical integration interval.

        t_end:
            End of numerical integration interval.

        Returns
        -------
        tuple
            Events executed in deterministic order.

        Notes
        -----
        Events satisfy:

            t_start < event_time <= t_end

        subject to the configured tolerance.

        An event exactly at the beginning of the interval is normally
        considered already handled by the previous step.
        """

        start = self._validate_time(
            t_start
        )

        end = self._validate_time(
            t_end
        )

        if end < start:

            raise EventError(
                "t_end cannot be earlier "
                "than t_start."
            )

        executions: list[
            EventExecution
        ] = []

        candidates = [
            event
            for event in self.events
            if (
                event.enabled
                and not event.triggered
                and event.time
                > start
                - self._tolerance
                and event.time
                <= end
                + self._tolerance
            )
        ]

        self._validate_candidate_batches(candidates)
        # The batch is sequential and prevalidated; the DAE solver re-evaluates
        # the network once after the complete batch returns.
        for event in candidates:
            executions.append(self._execute_event(event, dispatch_time=end))

        return tuple(
            executions
        )

    def _validate_candidate_batches(self, candidates: list[SimulationEvent]) -> None:
        """Reject duplicate target mutations before any event in a batch executes."""
        ordered = sorted(candidates, key=self._sort_key)
        batches: list[list[SimulationEvent]] = []
        for event in ordered:
            if not batches or event.time - batches[-1][0].time > self._tolerance:
                batches.append([event])
            else:
                batches[-1].append(event)
        for batch in batches:
            seen: dict[tuple[str, str], SimulationEvent] = {}
            for event in batch:
                operation = event.operation
                if operation is None:
                    continue
                key = operation.conflict_key
                previous = seen.get(key)
                if previous is not None:
                    raise EventBatchConflictError(
                        f"Conflicting simultaneous operations at t={event.time:.12g} s "
                        f"for {key[0]} target '{key[1]}': events "
                        f"'{previous.event_id}' ({previous.operation.operation}) and "
                        f"'{event.event_id}' ({operation.operation})."
                    )
                seen[key] = event

    # ==================================================================
    # RESET
    # ==================================================================

    def reset(
        self,
    ) -> None:
        """
        Reset all event-trigger state and execution history.

        This permits a new simulation run using the same event schedule.
        """

        for event in (
            self._events.values()
        ):

            event.reset()

        self._history.clear()

    # ==================================================================
    # QUERY
    # ==================================================================

    def next_event_time(
        self,
        current_time: float,
    ) -> float | None:
        """
        Return the next pending event time after ``current_time``.
        """

        current_time = (
            self._validate_time(
                current_time
            )
        )

        pending = [
            event
            for event in self.events
            if (
                event.enabled
                and not event.triggered
                and event.time
                > current_time
                + self._tolerance
            )
        ]

        if not pending:

            return None

        return min(
            event.time
            for event in pending
        )

    def has_pending_events(
        self,
    ) -> bool:
        """Return True if at least one event remains pending."""

        return any(
            event.enabled
            and not event.triggered
            for event in self._events.values()
        )

    # ==================================================================
    # INTERNAL EXECUTION
    # ==================================================================

    @staticmethod
    def _read_revision(event: SimulationEvent) -> int | None:
        if event.revision_reader is None:
            return None
        try:
            value = event.revision_reader()
        except Exception:
            return None
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def retry(self, event_id: str) -> SimulationEvent:
        """Explicitly re-arm a failed event without deleting its failure history."""
        event = self._get_event(event_id)
        latest = next(
            (item for item in reversed(self._history) if item.event_id == event.event_id),
            None,
        )
        if not event.triggered:
            raise EventError(f"Event '{event.event_id}' is already pending and does not require retry().")
        if latest is None or latest.status != "failed":
            raise EventError(
                f"Event '{event.event_id}' can only be retried after a recorded failed attempt."
            )
        event.triggered = False
        return event

    def annotate_post_event_re_evaluation(
        self,
        executions: tuple[EventExecution, ...],
        *,
        succeeded: bool,
        failure: Exception | None = None,
    ) -> tuple[EventExecution, ...]:
        """Attach the same-time post-event algebraic re-evaluation outcome."""
        status = "succeeded" if succeeded else "failed"
        failure_type = None if failure is None else type(failure).__name__
        failure_message = None if failure is None else str(failure)
        updated_items: list[EventExecution] = []
        for execution in executions:
            updated = replace(
                execution,
                post_event_re_evaluation_status=status,
                post_event_re_evaluation_failure_type=failure_type,
                post_event_re_evaluation_failure_message=failure_message,
            )
            history_index = next(
                (
                    index
                    for index in range(len(self._history) - 1, -1, -1)
                    if self._history[index] is execution
                ),
                None,
            )
            if history_index is None:
                raise EventError(
                    f"Event '{execution.event_id}' is not present in this EventManager history."
                )
            self._history[history_index] = updated
            updated_items.append(updated)
        return tuple(updated_items)

    def _execute_event(
        self,
        event: SimulationEvent,
        *,
        dispatch_time: float,
    ) -> EventExecution:
        """Execute one event, preserving both successful and failed evidence."""
        pre_revision = self._read_revision(event)
        try:
            result = event.action()
        except Exception as exc:
            post_revision = self._read_revision(event)
            execution = EventExecution(
                event_id=event.event_id,
                event_type=event.event_type,
                time=event.time,
                scheduled_time=event.time,
                dispatch_time=dispatch_time,
                priority=event.priority,
                description=event.description,
                metadata=event.metadata,
                status="failed",
                failure_type=type(exc).__name__,
                failure_message=str(exc),
                pre_event_revision=pre_revision,
                post_event_revision=post_revision,
            )
            # At-most-once by default. A retry requires the explicit retry()
            # API, which leaves this failed attempt in immutable history.
            event.triggered = True
            self._history.append(execution)
            raise EventError(
                f"Event '{event.event_id}' failed during execution at "
                f"t={dispatch_time:.12g} s; failure evidence was recorded.",
                execution=execution,
            ) from exc

        post_revision = self._read_revision(event)
        event.triggered = True
        execution = EventExecution(
            event_id=event.event_id,
            event_type=event.event_type,
            time=event.time,
            scheduled_time=event.time,
            dispatch_time=dispatch_time,
            priority=event.priority,
            description=event.description,
            metadata=event.metadata,
            status="succeeded",
            result=result,
            pre_event_revision=pre_revision,
            post_event_revision=post_revision,
        )
        self._history.append(execution)
        return execution

    def _get_event(
        self,
        event_id: str,
    ) -> SimulationEvent:
        """Return an event by ID."""

        try:

            return self._events[
                str(event_id)
            ]

        except KeyError as exc:

            raise UnknownEventError(
                f"Unknown event "
                f"'{event_id}'."
            ) from exc

    @staticmethod
    def _sort_key(
        event: SimulationEvent,
    ) -> tuple[
        float,
        int,
        str,
    ]:
        """
        Deterministic event ordering.

        Ordering:

            1. event time
            2. priority
            3. event ID
        """

        return (
            event.time,
            event.priority,
            event.event_id,
        )

    @staticmethod
    def _validate_time(
        time: float,
    ) -> float:
        """Validate a simulation time."""

        try:
            value = float(time)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EventError("Simulation time must be a finite numeric value.") from exc

        if not math.isfinite(value):
            raise EventError("Simulation time must be finite.")
        if value < 0.0:
            raise EventError("Simulation time cannot be negative.")

        return value


# ======================================================================
# COMMON EVENT FACTORY
# ======================================================================


def create_event(
    time: float,
    action: EventAction,
    *,
    event_id: str,
    event_type: str = "generic",
    description: str = "",
    priority: int = 0,
    metadata: dict[
        str,
        Any,
    ] | None = None,
    operation: EventOperation | None = None,
) -> SimulationEvent:
    """
    Convenience factory for creating a SimulationEvent.
    """

    return SimulationEvent(
        time=time,
        action=action,
        event_id=event_id,
        event_type=event_type,
        description=description,
        priority=priority,
        metadata=(
            {}
            if metadata is None
            else dict(metadata)
        ),
        operation=operation,
    )


__all__ = [
    "EventAction",
    "SimulationEvent",
    "EventOperation",
    "EventExecution",
    "EventTransition",
    "EventError",
    "DuplicateEventError",
    "UnknownEventError",
    "EventBatchConflictError",
    "EventManager",
    "create_event",
]
