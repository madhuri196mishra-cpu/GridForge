# ============================================================
# File: core/application/services/measurement_channel_service.py
# GridForge V2 — Application Measurement Channel Lifecycle Service
# Author: Subhendu Mishra
# ============================================================

"""Application-owned, project-scoped logical measurement lifecycle."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from threading import RLock
from typing import Any, Callable

from core.application.endpoint_resolver import resolve_terminal_reference
from core.application.results import ApplicationResult
from core.application.instrumentation import ProjectInstrumentationCollection, validate_instrumentation_targets
from core.application.transaction import Transaction
from core.model import CurrentTransformer, EndpointReference, EquipmentType
from core.measurement.measurement_channel import (
    MeasurementChannel,
    MeasurementPhase,
    MeasurementQuality,
    MeasurementSignalType,
)
from core.measurement.measurement_provisioning import MeasurementProvisioning
from core.measurement.measurement_source_reference import MeasurementSourceReference
from core.network.network import Network


@dataclass(frozen=True, slots=True)
class MeasurementSampleAttestation:
    """Signed producer envelope; verification is delegated to a deployment trust adapter."""

    producer_id: str
    project_id: str
    activation_generation: int
    timestamp: float
    timestamp_domain: str
    sample_sequence: int
    signature: str
    channel_id: str
    quality: MeasurementQuality
    available: bool
    diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("producer_id", "project_id", "timestamp_domain", "signature", "channel_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a canonical non-empty string.")
        if isinstance(self.activation_generation, bool) or not isinstance(self.activation_generation, int) or self.activation_generation < 1:
            raise ValueError("activation_generation must be a positive integer.")
        if isinstance(self.sample_sequence, bool) or not isinstance(self.sample_sequence, int) or self.sample_sequence < 0:
            raise ValueError("sample_sequence must be a non-negative integer.")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, (int, float)) or not math.isfinite(self.timestamp):
            raise ValueError("timestamp must be finite.")
        if not isinstance(self.quality, MeasurementQuality):
            raise TypeError("quality must be a MeasurementQuality.")
        if not isinstance(self.available, bool):
            raise TypeError("available must be bool.")
        if isinstance(self.diagnostic_codes, (str, bytes)):
            raise TypeError("diagnostic_codes must be a tuple of canonical strings.")
        codes = tuple(self.diagnostic_codes)
        if any(not isinstance(code, str) or not code.strip() or code != code.strip() for code in codes):
            raise ValueError("diagnostic_codes must contain canonical non-empty strings.")
        if len(set(codes)) != len(codes):
            raise ValueError("diagnostic_codes cannot contain duplicates.")
        object.__setattr__(self, "diagnostic_codes", codes)


class MeasurementChannelService:
    """Own the active project's authoritative MeasurementChannel collection.

    This service owns lifecycle/orchestration only. Core construction remains
    exclusively behind MeasurementProvisioning and the physical Network owns
    the source equipment and terminals.
    """

    def __init__(
        self,
        *,
        source_verifier: Callable[[MeasurementSampleAttestation, MeasurementChannel, float | complex], bool] | None = None,
        clock: Callable[[], float] | None = None,
        clock_domain: str | None = None,
        max_source_clock_skew_seconds: float = 2.0,
        execution_lock: Any | None = None,
    ) -> None:
        if source_verifier is not None and not callable(source_verifier):
            raise TypeError("source_verifier must be callable when provided.")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable when provided.")
        if clock_domain is not None and (not isinstance(clock_domain, str) or not clock_domain.strip() or clock_domain != clock_domain.strip()):
            raise ValueError("clock_domain must be a canonical non-empty string.")
        if isinstance(max_source_clock_skew_seconds, bool) or not isinstance(max_source_clock_skew_seconds, (int, float)) or not math.isfinite(max_source_clock_skew_seconds) or max_source_clock_skew_seconds <= 0:
            raise ValueError("max_source_clock_skew_seconds must be finite and positive.")
        self._source_verifier = source_verifier
        self._clock = clock
        self._clock_domain = clock_domain
        self._max_source_clock_skew_seconds = float(max_source_clock_skew_seconds)
        self._execution_lock = execution_lock if execution_lock is not None else RLock()
        self._sample_update_authority = object()
        self._project_id: str | None = None
        self._context: Any | None = None
        self._activation_generation = 0
        self._channel_usage_validator: Callable[[str], bool] | None = None
        self._network: Network | None = None
        self._channels: dict[str, MeasurementChannel] = {}
        self._sample_published_handler: Callable[[str, float, float, str], Any] | None = None

    def bind_sample_published_handler(
        self,
        handler: Callable[[str, float, float, str], Any] | None,
    ) -> None:
        """Bind the Application-owned protection trigger to trusted sample ingress."""
        if handler is not None and not callable(handler):
            raise TypeError("sample-published handler must be callable or None.")
        self._sample_published_handler = handler

    def bind_execution_lock(self, execution_lock: Any) -> None:
        """Use the Application command lock so publication cannot race breaker authorization."""
        if not callable(getattr(execution_lock, "__enter__", None)) or not callable(getattr(execution_lock, "__exit__", None)):
            raise TypeError("execution_lock must be a context-manager lock.")
        self._execution_lock = execution_lock

    def bind_channel_usage_validator(self, validator: Callable[[str], bool] | None) -> None:
        """Bind the existing Protection configuration authority's channel-reference check."""
        if validator is not None and not callable(validator):
            raise TypeError("channel usage validator must be callable or None.")
        self._channel_usage_validator = validator

    @property
    def clock_domain(self) -> str | None:
        """Trusted clock-domain identity used for authenticated live sample ingress."""
        return self._clock_domain

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def activation_generation(self) -> int:
        return self._activation_generation

    @property
    def network(self) -> Network | None:
        """Network identity to which the active channel registry was provisioned."""
        return self._network

    @property
    def channels(self) -> Mapping[str, MeasurementChannel]:
        return dict(self._channels)

    def get(self, channel_id: str) -> MeasurementChannel | None:
        return self._channels.get(self._normalize_id(channel_id))

    def require(self, channel_id: str) -> MeasurementChannel:
        normalized = self._normalize_id(channel_id)
        try:
            return self._channels[normalized]
        except KeyError as exc:
            raise KeyError(f"PROTECTION_CHANNEL_NOT_FOUND: MeasurementChannel '{normalized}' is not available.") from exc

    def validate_references(self, channel_ids: Sequence[str]) -> None:
        for channel_id in channel_ids:
            self.require(channel_id)

    def publish_sample(
        self,
        channel_id: str,
        value: float | complex,
        *,
        attestation: MeasurementSampleAttestation,
        quality: MeasurementQuality,
        available: bool = True,
    ) -> Any:
        """Publish only a signed, project-bound sample with trusted clock validation.

        The deployment verifier must authenticate producer identity and the
        complete attestation/value tuple (for example, using a device certificate
        and signature). Caller-supplied clock labels alone are never trusted.
        """
        with self._execution_lock:
            if self._source_verifier is None or self._clock is None or self._clock_domain is None:
                raise PermissionError("Trusted measurement source verification and clock authority are not configured.")
            if not isinstance(attestation, MeasurementSampleAttestation):
                raise TypeError("attestation must be MeasurementSampleAttestation.")
            if not isinstance(quality, MeasurementQuality):
                raise TypeError("quality must be MeasurementQuality.")
            if not isinstance(available, bool):
                raise TypeError("available must be bool.")
            channel = self.require(channel_id)
            if attestation.channel_id != channel.id:
                raise PermissionError("Measurement attestation is bound to a different channel identity.")
            if attestation.quality is not quality or attestation.available is not available:
                raise PermissionError("Published quality/availability must exactly match the signed measurement attestation.")
            if getattr(channel, "producer_id", None) != attestation.producer_id:
                raise PermissionError("Measurement producer identity does not match the configured channel producer.")
            if attestation.project_id != self._project_id or attestation.activation_generation != self._activation_generation:
                raise PermissionError("Measurement attestation belongs to a different project activation.")
            if attestation.timestamp_domain != self._clock_domain or channel.timestamp_domain != self._clock_domain:
                raise PermissionError("Measurement source clock domain does not match the trusted Application clock domain.")
            try:
                trusted_now = self._clock()
            except Exception as exc:
                raise PermissionError("Trusted measurement clock is unavailable.") from exc
            if isinstance(trusted_now, bool) or not isinstance(trusted_now, (int, float)) or not math.isfinite(trusted_now):
                raise PermissionError("Trusted measurement clock returned an invalid timestamp.")
            if attestation.timestamp > trusted_now + self._max_source_clock_skew_seconds:
                raise PermissionError("Measurement source timestamp is too far ahead of the trusted clock.")
            try:
                verified = self._source_verifier(attestation, channel, value)
            except Exception as exc:
                raise PermissionError("Measurement source attestation verification failed.") from exc
            if verified is not True:
                raise PermissionError("Measurement source attestation was not authenticated.")
            if channel.sample_sequence is not None and attestation.sample_sequence <= channel.sample_sequence:
                raise ValueError("Measurement sample sequence must increase monotonically.")
            if channel.timestamp is not None and attestation.timestamp < channel.timestamp:
                raise ValueError("Measurement source timestamp cannot move backwards.")
            validated_value = channel.validate_source_value(value)
            channel.update(
                validated_value,
                _authority=self._sample_update_authority,
                timestamp=float(attestation.timestamp),
                timestamp_domain=attestation.timestamp_domain,
                quality=quality,
                available=available,
                sample_sequence=attestation.sample_sequence,
                diagnostic_codes=attestation.diagnostic_codes,
            )
            handler = self._sample_published_handler
            if handler is not None:
                return handler(
                    channel.id,
                    float(attestation.timestamp),
                    float(trusted_now),
                )
            return None

    def activate(
        self,
        context: Any | None,
        network: Network,
        definitions: Sequence[Mapping[str, Any]] = (),
        generation: int = 0,
        instrumentation: ProjectInstrumentationCollection | None = None,
    ) -> Any:
        """Reconstruct candidate project channels and return one rollback callback.

        Candidate instrumentation is supplied explicitly by the project
        lifecycle. It is never read from the currently active Application
        facade, which may still represent the project being replaced.
        """
        if context is None:
            if definitions:
                raise ValueError("A project-less measurement activation cannot contain channel definitions.")
            if instrumentation is not None:
                raise ValueError("A project-less measurement activation cannot contain instrumentation.")
            next_channels: dict[str, MeasurementChannel] = {}
            next_project_id = None
        else:
            if not isinstance(network, Network):
                raise TypeError("network must be a Network.")
            if instrumentation is None:
                instrumentation = ProjectInstrumentationCollection(context.project_id)
            if not isinstance(instrumentation, ProjectInstrumentationCollection):
                raise TypeError("instrumentation must be a ProjectInstrumentationCollection.")
            if instrumentation.project_id != context.project_id:
                raise ValueError("Candidate instrumentation belongs to a different project.")
            validate_instrumentation_targets(instrumentation, network)
            next_channels = {}
            next_project_id = context.project_id
            provisioned_source_installations: set[str] = set()
            for definition in definitions:
                channel = self._provision_definition(context, network, definition, instrumentation=instrumentation)
                if channel.id in next_channels:
                    raise ValueError(f"Duplicate measurement channel ID: {channel.id}")
                if isinstance(channel.source_reference, MeasurementSourceReference):
                    installation_id = channel.source_installation_id
                    if installation_id in provisioned_source_installations:
                        raise ValueError(
                            f"Physical source installation {installation_id!r} is assigned to more than one MeasurementChannel."
                        )
                    provisioned_source_installations.add(installation_id)
                if isinstance(channel.source, CurrentTransformer):
                    self._validate_ct_channel_definition(
                        definition, channel, existing_channels=next_channels
                    )
                channel.producer_id = self._normalize_optional_id(definition.get("producer_id"))
                channel._bind_sample_update_authority(self._sample_update_authority)
                next_channels[channel.id] = channel

        previous = self.snapshot_state()
        self._project_id = next_project_id
        self._context = context
        self._activation_generation = generation if context is not None else 0
        self._network = network if context is not None else None
        self._channels = next_channels

        def rollback() -> None:
            self.restore_state(previous)

        return rollback

    def snapshot_state(self) -> tuple[Any | None, str | None, int, Network | None, dict[str, MeasurementChannel]]:
        """Capture the exact live channel authority for transactional compensation.

        The registry and channel objects are retained by reference. This is
        intentionally not a serialized definition snapshot: channel identity,
        live sample values, quality, timestamps, availability and sequence state
        remain untouched and are restored together with project provenance.
        """
        return (
            self._context,
            self._project_id,
            self._activation_generation,
            self._network,
            self._channels,
        )

    def restore_state(
        self,
        snapshot: tuple[Any | None, str | None, int, Network | None, dict[str, MeasurementChannel]],
    ) -> None:
        """Restore an exact previously captured authority; safe to repeat."""
        if not isinstance(snapshot, tuple) or len(snapshot) != 5:
            raise TypeError("Measurement channel snapshot must be a five-item state tuple.")
        context, project_id, generation, network, channels = snapshot
        if project_id is not None and not isinstance(project_id, str):
            raise TypeError("Measurement channel snapshot project identity is invalid.")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise TypeError("Measurement channel snapshot activation generation is invalid.")
        if not isinstance(channels, dict):
            raise TypeError("Measurement channel snapshot registry is invalid.")
        if project_id is not None and not isinstance(network, Network):
            raise TypeError("Active measurement channel snapshot requires its original Network.")
        self._context = context
        self._project_id = project_id
        self._activation_generation = generation
        self._network = network
        self._channels = channels

    def deactivate(self) -> None:
        self._context = None
        self._project_id = None
        self._activation_generation = 0
        self._network = None
        self._channels = {}

    def serialize_definitions(self) -> list[dict[str, Any]]:
        """Return persistent configuration only; live sample state is excluded."""
        return [
            self._serialize_channel(self._channels[channel_id])
            for channel_id in sorted(self._channels)
        ]

    def create_channel(
        self, *, definition: Mapping[str, Any], transaction: Transaction,
    ) -> ApplicationResult[MeasurementChannel]:
        """Author one CT secondary channel through the active Application transaction."""
        self._require_transaction(transaction)
        with self._execution_lock:
            channel = self._build_ct_channel(definition)
            if channel.id in self._channels:
                raise ValueError(f"Measurement channel already exists: {channel.id}")
            previous = self._channels
            next_channels = dict(previous)
            next_channels[channel.id] = channel
            transaction.record_undo(lambda previous=previous: self._restore_channels(previous))
            self._channels = next_channels
            return ApplicationResult.success_result(
                value=channel,
                message=f"CT secondary measurement channel created: {channel.id}",
                metadata={"channel_id": channel.id, "source_id": channel.source_id,
                          "terminal_role": channel.source_terminal.terminal_role},
            )

    def update_channel(
        self, *, channel_id: str, definition: Mapping[str, Any], transaction: Transaction,
    ) -> ApplicationResult[MeasurementChannel]:
        """Replace an unbound CT channel definition without mutating a sealed channel."""
        self._require_transaction(transaction)
        normalized_id = self._normalize_id(channel_id)
        with self._execution_lock:
            previous_channel = self.require(normalized_id)
            self._ensure_channel_unbound(normalized_id)
            if not isinstance(definition, Mapping):
                raise TypeError("Measurement channel definition must be a mapping.")
            supplied_id = definition.get("channel_id", definition.get("id", normalized_id))
            if supplied_id is not None and self._normalize_id(supplied_id) != normalized_id:
                raise ValueError("Updating a measurement channel cannot change its identity.")
            candidate_data = self._serialize_channel(previous_channel)
            candidate_data.update(dict(definition))
            candidate_data["channel_id"] = normalized_id
            channel = self._build_ct_channel(candidate_data)
            previous = self._channels
            next_channels = dict(previous)
            next_channels[normalized_id] = channel
            transaction.record_undo(lambda previous=previous: self._restore_channels(previous))
            self._channels = next_channels
            return ApplicationResult.success_result(
                value=channel,
                message=f"CT secondary measurement channel updated: {normalized_id}",
                metadata={"channel_id": normalized_id},
            )

    def delete_channel(
        self, *, channel_id: str, transaction: Transaction,
    ) -> ApplicationResult[MeasurementChannel]:
        """Delete an unbound CT channel; references must be explicitly unbound first."""
        self._require_transaction(transaction)
        normalized_id = self._normalize_id(channel_id)
        with self._execution_lock:
            channel = self.require(normalized_id)
            self._ensure_channel_unbound(normalized_id)
            previous = self._channels
            next_channels = dict(previous)
            del next_channels[normalized_id]
            transaction.record_undo(lambda previous=previous: self._restore_channels(previous))
            self._channels = next_channels
            return ApplicationResult.success_result(
                value=channel,
                message=f"CT secondary measurement channel deleted: {normalized_id}",
                metadata={"channel_id": normalized_id},
            )

    @staticmethod
    def _require_transaction(transaction: Transaction) -> None:
        if not isinstance(transaction, Transaction) or not transaction.active:
            raise TypeError("Measurement channel authoring requires an active Application Transaction.")

    def _restore_channels(self, channels: dict[str, MeasurementChannel]) -> None:
        with self._execution_lock:
            self._channels = channels

    def _ensure_channel_unbound(self, channel_id: str) -> None:
        validator = self._channel_usage_validator
        if validator is None:
            raise RuntimeError(
                "Protection channel-reference validation is not configured; editing or deleting a channel is denied."
            )
        if validator(channel_id):
            raise ValueError(
                f"Measurement channel {channel_id!r} is still referenced by Protection configuration; "
                "unbind every consumer before editing or deleting it."
            )

    def _ensure_unique_ct_source(self, source_id: str, terminal_role: str, channel_id: str) -> None:
        for existing_id, existing in self._channels.items():
            if existing_id == channel_id:
                continue
            reference = existing.source_terminal
            if (
                getattr(existing.source, "id", None) == source_id
                and isinstance(reference, EndpointReference)
                and reference.terminal_role == terminal_role
            ):
                raise ValueError(
                    f"CT secondary terminal {source_id}:{terminal_role} already has measurement channel {existing_id!r}."
                )

    def _build_ct_channel(self, definition: Mapping[str, Any]) -> MeasurementChannel:
        if not isinstance(definition, Mapping):
            raise TypeError("Measurement channel definition must be a mapping.")
        if self._context is None or self._network is None or self._project_id != self._context.project_id:
            raise RuntimeError("CT channel authoring requires an active project and Network.")
        channel_id = self._normalize_id(definition.get("channel_id", definition.get("id")))
        source_id = self._normalize_id(definition.get("source", definition.get("source_id")))
        terminal_data = definition.get("source_terminal", definition.get("source_terminal_reference"))
        if not isinstance(terminal_data, Mapping):
            raise ValueError("CT channel authoring requires an explicit source_terminal mapping.")
        reference = self._endpoint_reference(terminal_data)
        if reference.terminal_role not in {"S1", "S2"}:
            raise ValueError("CT measurement channels must originate from physical secondary terminal S1 or S2.")
        if getattr(reference.equipment_type, "value", None) != "ct":
            raise ValueError("CT secondary measurement channels require a CURRENT_TRANSFORMER terminal reference.")
        source = self._network.get_by_identity(source_id)
        if not isinstance(source, CurrentTransformer):
            raise ValueError(f"Measurement source {source_id!r} is not an authoritative CurrentTransformer.")
        if reference.equipment_id != source_id:
            raise ValueError("CT source equipment identity does not match source_terminal equipment identity.")
        signal_type = self._enum_value(MeasurementSignalType, definition.get("signal_type"), "signal_type")
        if signal_type is not MeasurementSignalType.CURRENT:
            raise ValueError("CT secondary channels must expose a CURRENT signal.")
        producer_id = self._normalize_optional_id(definition.get("producer_id"))
        if producer_id is None:
            raise ValueError("CT secondary channels require an explicit producer_id; no producer is inferred.")
        channel = self._provision_definition(self._context, self._network, definition)
        self._validate_ct_channel_definition(
            definition, channel, existing_channels=self._channels
        )
        channel.producer_id = producer_id
        channel._bind_sample_update_authority(self._sample_update_authority)
        return channel

    def _validate_ct_channel_definition(
        self,
        definition: Mapping[str, Any],
        channel: MeasurementChannel,
        *,
        existing_channels: Mapping[str, MeasurementChannel],
    ) -> None:
        """Apply one CT secondary-channel contract during authoring and restore."""
        if not isinstance(definition, Mapping):
            raise TypeError("CT channel definition must be a mapping.")
        if not isinstance(channel.source, CurrentTransformer):
            raise TypeError("CT channel validation requires an authoritative CurrentTransformer.")
        reference = channel.source_terminal
        if not isinstance(reference, EndpointReference) or reference.terminal_role not in {"S1", "S2"}:
            raise ValueError("CT measurement channels must originate from physical secondary terminal S1 or S2.")
        if channel.signal_type is not MeasurementSignalType.CURRENT:
            raise ValueError("CT secondary channels must expose a CURRENT signal.")
        if self._normalize_optional_id(definition.get("producer_id")) is None:
            raise ValueError("CT secondary channels require an explicit producer_id; no producer is inferred.")
        timestamp_domain = definition.get("timestamp_domain")
        if (
            not isinstance(timestamp_domain, str)
            or not timestamp_domain.strip()
            or timestamp_domain != timestamp_domain.strip()
        ):
            raise ValueError("CT secondary channels require an explicit canonical timestamp_domain.")
        if self._clock_domain is not None and timestamp_domain != self._clock_domain:
            raise ValueError("CT channel timestamp_domain must match the configured trusted Application clock domain.")
        if "phase" not in definition:
            raise ValueError("CT secondary channels require an explicit phase setting.")
        if not isinstance(definition.get("unit"), str) or definition["unit"].strip().upper() != "A":
            raise ValueError("CT secondary current channel unit must be 'A'.")
        for field in ("scale", "polarity", "stale_after"):
            if field not in definition:
                raise ValueError(f"CT secondary channels require an explicit {field} setting.")
        try:
            nominal_value = float(channel.nominal_value)
            scale = float(channel.scale)
            polarity = float(channel.polarity)
            stale_after = float(channel.stale_after)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("CT nominal_value, scale, polarity and stale_after must be numeric.") from exc
        if not math.isfinite(nominal_value) or nominal_value <= 0.0:
            raise ValueError("CT secondary nominal_value must be finite and positive (normally 1 A or 5 A).")
        if not math.isfinite(scale) or scale <= 0.0:
            raise ValueError("CT channel scale must be finite and positive.")
        if polarity not in (-1.0, 1.0):
            raise ValueError("CT channel polarity must be +1.0 or -1.0.")
        if not math.isfinite(stale_after) or stale_after <= 0.0:
            raise ValueError("CT channel stale_after must be a finite positive duration.")
        for existing_id, existing in existing_channels.items():
            existing_reference = existing.source_terminal
            if (
                existing_id != channel.id
                and isinstance(existing.source, CurrentTransformer)
                and getattr(existing.source, "id", None) == channel.source.id
                and isinstance(existing_reference, EndpointReference)
                and existing_reference.terminal_role == reference.terminal_role
            ):
                raise ValueError(
                    f"CT secondary terminal {channel.source.id}:{reference.terminal_role} "
                    f"already has measurement channel {existing_id!r}."
                )

    def _provision_definition(
        self,
        context: Any,
        network: Network,
        definition: Mapping[str, Any],
        *,
        instrumentation: ProjectInstrumentationCollection | None = None,
    ) -> MeasurementChannel:
        if not isinstance(definition, Mapping):
            raise TypeError("Measurement channel definition must be a mapping.")
        channel_id = self._normalize_id(definition.get("channel_id", definition.get("id")))
        physical_reference_data = definition.get("source_reference")
        if physical_reference_data is not None:
            if definition.get("source_terminal") is not None or definition.get("source_terminal_reference") is not None:
                raise ValueError("A physical source channel cannot also declare an electrical source terminal.")
            supplied_source_id = definition.get("source", definition.get("source_id"))
            if supplied_source_id is not None and self._normalize_id(supplied_source_id) != MeasurementSourceReference.from_mapping(physical_reference_data).source_id:
                raise ValueError("Physical source ID conflicts with the canonical source_reference.")
            if not isinstance(instrumentation, ProjectInstrumentationCollection):
                raise ValueError("Physical source channel provisioning requires the candidate project instrumentation collection.")
            reference = MeasurementSourceReference.from_mapping(physical_reference_data)
            source = instrumentation.source(reference.source_id)
            installation_id = self._normalize_id(definition.get("source_installation_id"))
            matches = [
                item for item in instrumentation.installations
                if item.installation_id == installation_id
            ]
            if len(matches) != 1:
                raise ValueError(f"Physical source installation {installation_id!r} is not uniquely registered in this project.")
            installation = matches[0]
            if (
                installation.source_id != reference.source_id
                or installation.interface_id != reference.interface_id
            ):
                raise ValueError("Physical source installation does not match the configured source interface.")
            interface = source.interface(reference.interface_id)
            if interface.signal_kind == "analog":
                if interface.quantity.endswith("_temperature"):
                    signal_type = MeasurementSignalType.TEMPERATURE
                else:
                    signal_type = {
                        "current": MeasurementSignalType.CURRENT,
                        "voltage": MeasurementSignalType.VOLTAGE,
                        "frequency": MeasurementSignalType.FREQUENCY,
                        "phase_angle": MeasurementSignalType.PHASE_ANGLE,
                        "power": MeasurementSignalType.POWER,
                        "impedance": MeasurementSignalType.IMPEDANCE,
                    }.get(interface.quantity, MeasurementSignalType.CUSTOM)
            elif interface.signal_kind == "digital":
                if interface.quantity.endswith("_temperature") or interface.quantity in {
                    "current", "voltage", "frequency", "phase_angle", "power", "impedance"
                }:
                    raise ValueError("Digital physical interfaces cannot declare continuous analog quantities.")
                signal_type = MeasurementSignalType.DIGITAL
            else:
                # Counter and diagnostic outputs have no dedicated Core
                # scalar signal classification; preserve them as CUSTOM
                # without pretending they are analog or digital states.
                signal_type = MeasurementSignalType.CUSTOM
            if "signal_type" in definition:
                declared_signal = self._enum_value(
                    MeasurementSignalType, definition["signal_type"], "signal_type"
                )
                if declared_signal is not signal_type:
                    raise ValueError("Configured signal_type conflicts with the physical source interface quantity.")
            producer_id = self._normalize_optional_id(definition.get("producer_id"))
            if producer_id is None:
                raise ValueError("Physical sensor channels require an explicit producer_id.")
            timestamp_domain = definition.get("timestamp_domain")
            if (
                not isinstance(timestamp_domain, str)
                or not timestamp_domain.strip()
                or timestamp_domain != timestamp_domain.strip()
            ):
                raise ValueError("Physical sensor channels require an explicit canonical timestamp_domain.")
            if self._clock_domain is not None and timestamp_domain != self._clock_domain:
                raise ValueError("Physical sensor timestamp_domain must match the configured trusted Application clock domain.")
            if "stale_after" not in definition:
                raise ValueError("Physical sensor channels require an explicit stale_after duration.")
            try:
                stale_after = float(definition["stale_after"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Physical sensor stale_after must be numeric.") from exc
            if not math.isfinite(stale_after) or stale_after <= 0:
                raise ValueError("Physical sensor stale_after must be finite and positive.")
            phase = self._enum_value(
                MeasurementPhase,
                definition.get("phase", interface.phase or MeasurementPhase.NONE.value),
                "phase",
            )
            if interface.phase is not None and phase.value != interface.phase.upper():
                raise ValueError("Configured channel phase conflicts with the physical source interface.")
            return MeasurementProvisioning.provision_physical_source_channel(
                source=source,
                source_reference=reference,
                source_installation_id=installation_id,
                channel_id=channel_id,
                signal_type=signal_type,
                name=str(definition.get("name", "")),
                phase=phase,
                unit=interface.unit,
                nominal_value=float(definition.get("nominal_value", 0.0)),
                scale=float(definition.get("scale", 1.0)),
                polarity=float(definition.get("polarity", 1.0)),
                timestamp_domain=timestamp_domain,
                stale_after=stale_after,
                available=False,
                quality=MeasurementQuality.UNKNOWN,
            )
        source_id = self._normalize_id(definition.get("source", definition.get("source_id")))
        terminal_data = definition.get("source_terminal", definition.get("source_terminal_reference"))
        if not isinstance(terminal_data, Mapping):
            raise ValueError(f"MeasurementChannel '{channel_id}' requires a source_terminal EndpointReference mapping.")
        reference = self._endpoint_reference(terminal_data)
        source = network.get_by_identity(source_id)
        if getattr(source, "id", None) != source_id:
            raise ValueError(f"Measurement source '{source_id}' could not be resolved.")
        if isinstance(source, CurrentTransformer) and (
            getattr(reference.equipment_type, "value", None) != "ct"
            or reference.terminal_role not in {"S1", "S2"}
        ):
            raise ValueError("CT measurement channels must resolve to the physical S1 or S2 secondary terminal.")
        signal_type = self._enum_value(MeasurementSignalType, definition.get("signal_type"), "signal_type")
        phase = self._enum_value(MeasurementPhase, definition.get("phase", MeasurementPhase.NONE.value), "phase")
        terminal = resolve_terminal_reference(context, reference)
        return MeasurementProvisioning.provision_channel(
            source=source,
            source_terminal=terminal,
            source_terminal_reference=reference,
            channel_id=channel_id,
            signal_type=signal_type,
            name=str(definition.get("name", "")),
            phase=phase,
            unit=str(definition.get("unit", "")),
            nominal_value=float(definition.get("nominal_value", 0.0)),
            scale=float(definition.get("scale", 1.0)),
            polarity=float(definition.get("polarity", 1.0)),
            timestamp_domain=definition.get("timestamp_domain"),
            stale_after=definition.get("stale_after"),
            available=False,
            quality=MeasurementQuality.UNKNOWN,
        )

    @staticmethod
    def _endpoint_reference(data: Mapping[str, Any]) -> EndpointReference:
        if data.get("kind") != "terminal":
            raise ValueError("Measurement channel source must use a terminal EndpointReference.")
        equipment_type = data.get("equipment_type")
        try:
            equipment_type = EquipmentType(equipment_type)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid measurement source equipment_type: {equipment_type!r}") from exc
        return EndpointReference.terminal(
            equipment_type=equipment_type,
            equipment_id=str(data["object_id"]),
            terminal_role=str(data["terminal_role"]),
        )

    @staticmethod
    def _enum_value(enum_type: type, value: Any, field: str) -> Any:
        try:
            return value if isinstance(value, enum_type) else enum_type(str(value).strip().upper())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid measurement {field}: {value!r}") from exc

    @staticmethod
    def _normalize_optional_id(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("Measurement producer_id must be a canonical non-empty string.")
        return value

    @staticmethod
    def _normalize_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Measurement channel IDs and source IDs must be non-empty strings.")
        return value.strip()

    @staticmethod
    def _serialize_channel(channel: MeasurementChannel) -> dict[str, Any]:
        data = {
            "channel_id": channel.id,
            "signal_type": channel.signal_type.value,
            "name": channel.name,
            "source": channel.source_id,
            "phase": channel.phase.value,
            "unit": channel.unit,
            "nominal_value": channel.nominal_value,
            "scale": channel.scale,
            "polarity": channel.polarity,
            "timestamp_domain": channel.timestamp_domain,
            "stale_after": channel.stale_after,
            "producer_id": getattr(channel, "producer_id", None),
        }
        physical_reference = getattr(channel, "source_reference", None)
        if isinstance(physical_reference, MeasurementSourceReference):
            data["source_reference"] = physical_reference.to_mapping()
            data["source_installation_id"] = channel.source_installation_id
            return data
        terminal_reference = channel.source_terminal
        if not isinstance(terminal_reference, EndpointReference):
            raise ValueError(f"MeasurementChannel '{channel.id}' has no canonical source reference.")
        data["source_terminal"] = dict(terminal_reference.to_mapping())
        return data


__all__ = ["MeasurementChannelService"]
