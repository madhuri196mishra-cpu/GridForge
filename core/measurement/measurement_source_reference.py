"""Serializable identity for non-electrical physical measurement interfaces.

This value object deliberately does not extend EndpointReference: electrical
Bus/Terminal identity and physical instrumentation identity are separate
domains. It stores identity only; it does not resolve, register, own, install,
or publish a measurement source.

Application code must resolve this reference against the active project's
authoritative source owner and validate its installation/monitoring
association before provisioning a MeasurementChannel. Existing CT/PT/CVT
terminal-backed channels continue to use EndpointReference unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


_REFERENCE_KIND = "measurement_source_interface"
_FAMILY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]*\\Z")
_FIELDS = frozenset({"kind", "source_family", "source_id", "interface_id"})


def _canonical_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string.")
    if not value or value != value.strip():
        raise ValueError(f"{field} must be a canonical non-empty string.")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} cannot contain control characters.")
    return value


@dataclass(frozen=True, slots=True)
class MeasurementSourceReference:
    """Immutable identity of one named output interface on a physical source.

    source_id is resolved only inside the owning project's authoritative
    source catalogue. interface_id distinguishes multiple outputs on the
    same instrument. source_family is a stable lowercase family token,
    not a Python class name or an electrical EquipmentType.
    """

    source_family: str
    source_id: str
    interface_id: str

    def __post_init__(self) -> None:
        family = _canonical_identifier(self.source_family, "source_family")
        if not _FAMILY_PATTERN.fullmatch(family):
            raise ValueError(
                "source_family must be a lowercase canonical family token."
            )
        object.__setattr__(
            self, "source_id", _canonical_identifier(self.source_id, "source_id")
        )
        object.__setattr__(
            self, "interface_id",
            _canonical_identifier(self.interface_id, "interface_id"),
        )

    def to_mapping(self) -> dict[str, str]:
        """Return a deterministic JSON-compatible identity mapping."""
        return {
            "kind": _REFERENCE_KIND,
            "source_family": self.source_family,
            "source_id": self.source_id,
            "interface_id": self.interface_id,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "MeasurementSourceReference":
        """Parse only the complete canonical mapping; reject ambiguous extras."""
        if not isinstance(value, Mapping):
            raise TypeError("Measurement source reference must be a mapping.")
        if set(value) != _FIELDS:
            raise ValueError(
                "Measurement source reference must contain exactly "
                "kind, source_family, source_id and interface_id."
            )
        if value.get("kind") != _REFERENCE_KIND:
            raise ValueError(
                "Measurement source reference kind is not canonical."
            )
        return cls(
            source_family=value["source_family"],
            source_id=value["source_id"],
            interface_id=value["interface_id"],
        )


__all__ = ["MeasurementSourceReference"]
