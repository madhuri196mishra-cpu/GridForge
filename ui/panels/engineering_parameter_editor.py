# ============================================================
# File: ui/panels/engineering_parameter_editor.py
# GridForge V2 — Generic Engineering Parameter Editor
# Author: Subhendu Mishra
# ============================================================

"""Generic presentation-to-Application adapter for typed engineering edits."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from ui.projection.projection_state import ProjectionState


@dataclass(frozen=True, slots=True)
class EngineeringConfigurationIntent:
    """Immutable UI intent containing typed engineering values only."""

    element_type: str
    element_id: str
    values: Mapping[str, Any]
    identity_kind: str = "element"

    def __post_init__(self) -> None:
        if not isinstance(self.element_type, str) or not self.element_type.strip():
            raise ValueError("element_type must be non-empty.")
        if not isinstance(self.element_id, str) or not self.element_id.strip():
            raise ValueError("element_id must be non-empty.")
        object.__setattr__(self, "element_type", self.element_type.strip().lower())
        object.__setattr__(self, "element_id", self.element_id.strip())
        if self.identity_kind not in {"element", "draft"}:
            raise ValueError("identity_kind must be element or draft.")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))


class EngineeringParameterEditor:
    """Emit typed engineering intent; Application owns command construction.

    The editor has no equipment-specific command builders and no engineering
    state. ProjectionState supplies parameter metadata; Application decides how
    the resulting intent becomes the authoritative update command.
    """

    def __init__(self, application: Any) -> None:
        if application is None or not callable(getattr(application, "execute", None)):
            raise TypeError("application must provide execute(command).")
        if not callable(getattr(application, "prepare_engineering_update", None)):
            raise TypeError("application must provide prepare_engineering_update(intent).")
        self._application = application

    def intent_from_projection(
        self,
        projection: ProjectionState,
        changes: Mapping[str, Any],
    ) -> EngineeringConfigurationIntent:
        if not isinstance(projection, ProjectionState):
            raise TypeError("projection must be a ProjectionState.")
        if not changes:
            raise ValueError("At least one engineering parameter change is required.")

        editable = {
            item.parameter_id: item
            for item in projection.engineering_parameters
            if item.editable and not item.derived
        }
        unknown = [key for key in changes if key not in editable]
        if unknown:
            raise ValueError(
                "Engineering parameter(s) are not editable or not projected: "
                + ", ".join(sorted(unknown))
            )

        for parameter_id in changes:
            paired_with = editable[parameter_id].validation.get("paired_with")
            if paired_with and paired_with not in changes:
                raise ValueError(
                    f"{parameter_id} must be edited together with {paired_with}."
                )

        typed_values: dict[str, Any] = {}
        for parameter_id, value in changes.items():
            parameter = editable[parameter_id]
            typed_values[parameter_id] = self._coerce_typed_value(
                parameter.datatype, value, parameter_id, parameter.validation
            )

        return EngineeringConfigurationIntent(
            element_type=projection.display_type,
            element_id=projection.object_id,
            values=typed_values,
            identity_kind=projection.identity_kind,
        )

    def submit(self, intent: EngineeringConfigurationIntent) -> Any:
        if not isinstance(intent, EngineeringConfigurationIntent):
            raise TypeError("intent must be an EngineeringConfigurationIntent.")
        if intent.identity_kind == "draft":
            command = self._application.prepare_draft_engineering_update(intent)
        else:
            command = self._application.prepare_engineering_update(intent)
        return self._application.execute(command)

    def submit_batch(
        self,
        projections: tuple[ProjectionState, ...] | list[ProjectionState],
        changes: Mapping[str, Any],
    ) -> Any:
        """Apply one common set of values to all compatible selected equipment."""
        targets = tuple(projections)
        return self.submit_batch_per_target(targets, tuple(dict(changes) for _ in targets))

    def submit_batch_per_target(
        self,
        projections: tuple[ProjectionState, ...] | list[ProjectionState],
        changes_by_target: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    ) -> Any:
        """Prepare every target-specific update before one atomic Application execution."""
        targets = tuple(projections)
        per_target = tuple(dict(values) for values in changes_by_target)
        if len(targets) < 2 or len(per_target) != len(targets):
            raise ValueError("Batch editing requires matching changes for at least two selected equipment items.")
        if any(not isinstance(target, ProjectionState) for target in targets):
            raise TypeError("Every batch target must be a ProjectionState.")
        if len({(target.identity_kind, target.object_id) for target in targets}) != len(targets):
            raise ValueError("Batch editing contains duplicate equipment identities.")
        if any(target.identity_kind != "element" for target in targets):
            raise ValueError("Batch engineering updates support committed equipment only.")
        if len({target.display_type.strip().lower() for target in targets}) != 1:
            raise ValueError("Batch engineering updates require compatible equipment types.")
        if not any(per_target):
            raise ValueError("At least one engineering parameter change is required.")

        # Prepare and validate every target before dispatch. No Core mutation
        # occurs if any target rejects a field, paired value or typed conversion.
        intents = []
        for target, changes in zip(targets, per_target):
            if not changes:
                continue
            intent = self.intent_from_projection(target, changes)
            editable = {
                item.parameter_id: item
                for item in target.engineering_parameters
                if item.editable and not item.derived
            }
            if any(editable[key].topology_impact for key in changes):
                raise ValueError("Batch editing does not permit topology-impacting parameters.")
            intents.append(intent)
        if not intents:
            raise ValueError("No selected equipment item contains a valid change.")
        if len(intents) == 1:
            command = self._application.prepare_engineering_update(intents[0])
        else:
            command = self._application.prepare_engineering_update_batch(tuple(intents))
        return self._application.execute(command)

    @staticmethod
    def _coerce_typed_value(
        datatype: str,
        value: Any,
        parameter_id: str,
        validation: Mapping[str, Any] | None = None,
    ) -> Any:
        normalized = str(datatype or "unknown").strip().lower()
        rules = dict(validation or {})
        if value is None:
            if rules.get("nullable") is True:
                return None
            raise TypeError(f"{parameter_id} does not allow clearing or an empty value.")
        if normalized in {"float", "number"}:
            if isinstance(value, bool):
                raise TypeError(f"{parameter_id} requires a numeric value.")
            return float(value)
        if normalized in {"int", "integer"}:
            if isinstance(value, bool):
                raise TypeError(f"{parameter_id} requires an integer value.")
            try:
                converted = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise TypeError(f"{parameter_id} requires an integer value.") from exc
            if isinstance(value, float) and converted != value:
                raise TypeError(f"{parameter_id} requires an integer value.")
            if isinstance(value, str) and str(converted) != value.strip():
                raise TypeError(f"{parameter_id} requires an integer value.")
            return converted
        if normalized == "complex":
            if isinstance(value, bool):
                raise TypeError(f"{parameter_id} requires a complex value.")
            if isinstance(value, Mapping):
                if set(value) != {"real", "imag"}:
                    raise TypeError(f"{parameter_id} requires exactly real and imag components.")
                real, imag = value["real"], value["imag"]
            elif isinstance(value, (tuple, list)) and len(value) == 2:
                real, imag = value
            elif isinstance(value, complex):
                return value
            else:
                raise TypeError(f"{parameter_id} requires a complex value with real and imaginary components.")
            if isinstance(real, bool) or isinstance(imag, bool):
                raise TypeError(f"{parameter_id} complex components must be numeric.")
            try:
                return complex(float(real), float(imag))
            except (TypeError, ValueError, OverflowError) as exc:
                raise TypeError(f"{parameter_id} complex components must be numeric.") from exc
        if normalized in {"bool", "boolean"}:
            if not isinstance(value, bool):
                raise TypeError(f"{parameter_id} requires a boolean value.")
            return value
        if normalized == "enum":
            return str(value).strip()
        return value


__all__ = ["EngineeringConfigurationIntent", "EngineeringParameterEditor"]
