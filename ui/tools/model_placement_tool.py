# ============================================================
# File: ui/tools/model_placement_tool.py
# GridForge V2 — Model Placement Tool
# Author: Subhendu Mishra
# ============================================================
"""Shared position-first placement behavior for concrete model tools."""

from __future__ import annotations

from collections.abc import Mapping

from typing import Any, Optional, Tuple
from uuid import uuid4

from .tool_base import ToolBase
from ui.canvas.symbol_preview_item import SymbolPreviewItem
from ui.creation.creation_context import CreationContext, CreationDraft
from core.application.commands.draft_commands import AddDraftEquipmentCommand
from .endpoint_identity_adapter import EndpointIdentityAdapter


class ModelPlacementTool(ToolBase):
    """Reusable UI-only placement interaction for concrete SLD model tools.

    Placement captures a canvas position and creates the immutable Application
    command. Required endpoint/terminal acquisition is definition-driven and
    remains transient until the final Application command is prepared.
    """

    MODEL_NAME = "Model"
    TOOL_ID = "model"
    SYMBOL_ID = ""

    def __init__(
        self,
        controller: Any,
        application: Any,
        selection_manager: Any,
        snap_system: Any,
        preview_layer: Any = None,
        symbol_registry: Any = None,
    ) -> None:
        super().__init__(
            controller=controller,
            application=application,
            selection_manager=selection_manager,
            snap_system=snap_system,
        )
        self._position: Optional[Tuple[float, float]] = None
        self._preview_active = False
        self._preview_layer = preview_layer
        self._symbol_registry = symbol_registry
        self._creation_context: CreationContext | None = None
        self._endpoint_acquired_this_interaction = False
        self._accepted_endpoint_snap: Any | None = None
        self._active_draft_id: str | None = None
        self._placement_dragging = False

    def bind_creation_context(self, creation_context: CreationContext) -> None:
        if not isinstance(creation_context, CreationContext):
            raise TypeError("creation_context must be a CreationContext.")
        self._creation_context = creation_context

    def set_engineering_parameters(self, **parameters: Any) -> None:
        """Compatibility adapter into the canonical CreationDraft."""
        if not parameters:
            raise ValueError("Engineering parameters must not be empty.")
        self._require_creation_context().update_many(parameters)

    def _require_creation_context(self) -> CreationContext:
        if self._creation_context is None:
            raise RuntimeError("ModelPlacementTool is not bound to CreationContext.")
        return self._creation_context

    @property
    def tool_id(self) -> str:
        return self.TOOL_ID

    @property
    def name(self) -> str:
        return self.MODEL_NAME

    @property
    def description(self) -> str:
        return f"Place a {self.MODEL_NAME.lower()} on the SLD canvas."

    def on_activate(self) -> None:
        self._clear_state()

    def on_deactivate(self) -> None:
        self._clear_state()

    def on_mouse_press(self, event: Any) -> bool:
        self._ensure_active()
        draft = self._require_creation_context().require_draft()

        # An equipment tool keeps its normal position-first placement semantics.
        # When the initial press lands on an existing Simple Wire, however, the
        # same equipment gesture is temporarily delegated to the canonical
        # ElectricalInsertionTool. No draft/Core/SLD state is created by this
        # decision; the insertion tool only submits an immutable command on
        # release.
        if draft.placement_position is None and self._try_begin_wire_insertion(event, draft):
            return True

        if draft.placement_position is None:
            snap = self._snap_result(event)
        else:
            if not draft.configuration_complete:
                self._report_feedback("Required engineering parameter missing.")
                return False
            # A physical terminal does not imply that an initial target
            # endpoint must be acquired. Only an explicitly required
            # creation role enters endpoint snapping/progression.
            if self._pending_creation_role(draft) is None:
                return False
            snap = self._snap_endpoint_result(event)
        if snap is None:
            return False

        # First interaction establishes the presentation placement.  Required
        # terminal/topology acquisition is then performed by subsequent
        # object-snap interactions against canonical terminal identities.
        if draft.placement_position is None:
            if self._active_draft_id is None:
                self._active_draft_id = f"{draft.definition.tool_id}-draft-{uuid4().hex}"
            position = self._position_tuple(snap.position)
            self._position = position
            draft.set_placement(position)
            draft.mark_previewing()
            self._preview_active = True
            self._endpoint_acquired_this_interaction = False
            self._placement_dragging = True
            self._show_preview(position)
            return True

        if not self._acquire_terminal(draft, snap):
            return False
        self._show_preview(self._position or draft.placement_position)
        return True

    def place_creation_draft(self) -> bool:
        """Persist the current placement as Application-owned DraftNetwork state."""
        self._ensure_active()
        draft = self._require_creation_context().require_draft()
        equipment_type = draft.equipment_type
        equipment_id = self._active_draft_id or f"{draft.definition.tool_id}-draft-{uuid4().hex}"
        # Application owns the trusted creation contract. The UI's creation
        # definition supplies current values and presentation intent only; it
        # must not author schema metadata that the commit boundary later trusts.
        registry = getattr(self.application, "creation_contract_registry", None)
        if registry is None:
            self._report_feedback("CREATION_INTENT_FAILED: Application creation contract registry is unavailable.")
            return False
        try:
            canonical_contract = registry.resolve(equipment_type)
        except (KeyError, TypeError, ValueError) as exc:
            self._report_feedback(f"CREATION_INTENT_FAILED: no trusted contract for {equipment_type!r}: {exc}")
            return False
        # Position-first placement: incomplete engineering configuration is
        # valid DraftNetwork authoring state and must not block visual placement.
        # Engineering validation remains authoritative for later commit readiness.
        if draft.placement_position is None:
            self._report_feedback(
                f"CREATION_INTENT_FAILED: {equipment_type} placement position is required."
            )
            return False
        try:
            equipment = {
                "draft_id": equipment_id,
                "equipment_type": equipment_type,
                "display_name": draft.definition.equipment_type,
                "terminal_contract": tuple(canonical_contract["terminal_contract"]),
                "engineering_data": dict(draft.snapshot_values()),
                # Preserve every explicitly acquired creation endpoint using
                # the canonical persisted DraftEndpointReference. The UI
                # endpoint object itself never becomes draft/Core identity.
                "endpoints": {
                    str(role): self._to_draft_endpoint_reference(
                        endpoint,
                        draft_id=equipment_id,
                        equipment_type=equipment_type,
                        terminal_role=str(role),
                    ).to_dict()
                    for role, endpoint in draft.snapshot_endpoints().items()
                },
                "placement": draft.placement_position,
                "presentation": dict(draft.preview_state),
                "validation_state": dict(draft.validation_state),
                "command_type": canonical_contract["command_type"],
                "id_field": canonical_contract["id_field"],
                "parameter_mapping": dict(canonical_contract["parameter_mapping"]),
                "endpoint_mapping": dict(canonical_contract["endpoint_mapping"]),
                "required_connection_terminals": tuple(canonical_contract["required_connection_terminals"]),
                "parameter_contract": tuple(dict(item) for item in canonical_contract["parameter_contract"]),
                "conditional_requirements": tuple(
                    dict(item) for item in canonical_contract["conditional_requirements"]
                ),
                "command_field_dispositions": {
                    str(field_name): dict(disposition)
                    for field_name, disposition in canonical_contract["command_field_dispositions"].items()
                },
            }
            command = AddDraftEquipmentCommand(equipment=equipment)
            result = self.execute_command(command)
        except Exception as exc:
            self._report_feedback(
                self._draft_failure_message(
                    equipment_type=equipment_type,
                    equipment_id=equipment_id,
                    error=exc,
                )
            )
            return False
        if not getattr(result, "success", False):
            self._report_feedback(
                self._draft_failure_message(
                    equipment_type=equipment_type,
                    equipment_id=equipment_id,
                    error=result,
                )
            )
            return False
        # AddDraftEquipmentCommand ends only the transient placement interaction.
        # It does NOT cross the DraftNetwork -> Core boundary. The authoritative
        # draft identity remains in Application.draft_network until the explicit
        # CommitNetworkCommand operation is invoked.
        self.get_selection_manager().select_single(equipment_id)
        self._require_creation_context().end_interaction()
        self._clear_state()
        self._report_feedback(
            f"{equipment_type} draft placed. Use Commit Network to commit the drawing."
        )
        return True
    def _to_draft_endpoint_reference(
        self,
        endpoint: Any,
        *,
        draft_id: str,
        equipment_type: str,
        terminal_role: str,
    ):
        """Persist only canonical DraftEndpointReference values."""
        from core.application.draft.network import DraftEndpointReference
        from core.model import EndpointReference

        if isinstance(endpoint, DraftEndpointReference):
            return endpoint
        if not isinstance(endpoint, EndpointReference):
            raise TypeError(
                f"Creation endpoint for {terminal_role!r} must be an EndpointReference."
            )
        # Scope is resolved against Application-owned draft identities, never
        # inferred from a graphics-item or terminal-registry identifier.
        draft_equipment = ()
        read_draft_network = getattr(self.application, "read_draft_network", None)
        if callable(read_draft_network):
            snapshot = read_draft_network()
            if isinstance(snapshot, dict):
                draft_equipment = tuple(snapshot.get("equipment", ()))
        draft_item = next(
            (
                item for item in draft_equipment
                if str(item.get("draft_id", "")) == str(endpoint.object_id)
                and (
                    endpoint.is_bus
                    and str(item.get("equipment_type", "")).strip().lower() == "bus"
                    or endpoint.is_terminal
                    and str(item.get("equipment_type", "")).strip().lower()
                    == str(endpoint.equipment_type.value).strip().lower()
                )
            ),
            None,
        )
        scope = "draft" if draft_item is not None else "core"
        if endpoint.is_bus:
            return DraftEndpointReference.bus(
                bus_id=str(endpoint.object_id),
                attachment_id=str(endpoint.attachment_id),
                scope=scope,
            )
        # Preserve the snapped equipment identity and scope. The role is the
        # external terminal role, not the newly placed equipment's terminal.
        return DraftEndpointReference.terminal(
            draft_id=str(endpoint.object_id),
            equipment_type=str(endpoint.equipment_type.value),
            terminal_role=str(endpoint.terminal_role),
            scope=scope,
        )

    @staticmethod
    def _pending_creation_role(draft: CreationDraft) -> str | None:
        """Return the first missing explicitly required initial endpoint role."""
        # Topology requirements are validated from DraftConnection at commit;
        # placement must not force the engineer to wire equipment immediately.
        for requirement in draft.definition.terminal_requirements:
            if requirement.initial_endpoint_required and draft.endpoints.get(requirement.terminal_name) is None:
                return requirement.terminal_name
        return None

    def _acquire_terminal(self, draft: CreationDraft, snap: Any) -> bool:
        pending_role = self._pending_creation_role(draft)
        if pending_role is None:
            return False
        try:
            endpoint = EndpointIdentityAdapter.from_snap_result(snap)
        except (TypeError, ValueError) as exc:
            self._report_feedback(f"CREATION_INTENT_FAILED: {exc}")
            return False
        draft.set_endpoint(pending_role, endpoint)
        self._accepted_endpoint_snap = snap
        self._endpoint_acquired_this_interaction = True
        return True

    def _try_begin_wire_insertion(self, event: Any, draft: CreationDraft) -> bool:
        manager_getter = getattr(self.controller, "get_tool_manager", None)
        if callable(manager_getter):
            tool_manager = manager_getter()
        else:
            tool_manager = getattr(self.controller, "_tool_manager", None)
        if tool_manager is None:
            return False
        begin = getattr(tool_manager, "begin_equipment_insertion", None)
        if not callable(begin):
            return False
        try:
            parameters = dict(draft.snapshot_values())
            orientation = float(draft.preview_state.get("rotation", 0.0))
            return bool(
                begin(
                    draft.equipment_type,
                    creation_parameters=parameters,
                    orientation=orientation,
                    equipment_id=None,
                    initial_event=event,
                )
            )
        except (KeyError, TypeError, ValueError, RuntimeError):
            return False

    @staticmethod
    def _draft_failure_message(
        *,
        equipment_type: str,
        equipment_id: str,
        error: Any,
    ) -> str:
        """Expose the structured command cause in placement feedback."""
        parts = [
            f"DRAFT_COMMAND_FAILED: equipment={equipment_type}",
            f"id={equipment_id}",
        ]
        current = error
        seen: set[int] = set()
        depth = 0
        while current is not None and id(current) not in seen and depth < 5:
            seen.add(id(current))
            error_type = type(current).__name__
            code = getattr(current, "code", None)
            message = getattr(current, "message", None) or str(current)
            parts.append(
                f"cause[{depth}]={error_type}"
                + (f" code={code}" if code is not None else "")
                + f": {message}"
            )
            details = getattr(current, "details", None)
            if isinstance(details, Mapping):
                safe_details = {
                    str(key): value
                    for key, value in details.items()
                    if isinstance(value, (str, int, float, bool, type(None), tuple, list, dict))
                }
                if safe_details:
                    parts.append(f"details[{depth}]={safe_details!r}")
            elif details is not None:
                parts.append(f"details[{depth}]={type(details).__name__}")
            if getattr(current, "code", None) == "COMMAND_HANDLER_NOT_FOUND":
                details = getattr(current, "details", None)
                if isinstance(details, Mapping):
                    diagnostic = details.get("diagnostic")
                    registration_state = details.get("registration_state")
                    candidates = details.get("same_namespace_candidates", ())
                    if diagnostic:
                        parts.append(f"handler_diagnostic={diagnostic}")
                    if registration_state:
                        parts.append(f"handler_registration_state={registration_state}")
                    if candidates:
                        parts.append(
                            "same_namespace_candidates="
                            + ", ".join(str(value) for value in candidates)
                        )
                    elif details.get("command_namespace"):
                        parts.append(
                            "handler_namespace="
                            + str(details["command_namespace"])
                        )
            current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
            depth += 1
        return " | ".join(parts)

    def _report_feedback(self, message: str) -> None:
        """Use an existing presentation feedback hook when the composition provides one."""
        for name in ("show_status_message", "set_status_message", "notify_user"):
            callback = getattr(self.controller, name, None)
            if callable(callback):
                callback(message)
                return

    def on_mouse_move(self, event: Any) -> bool:
        self._ensure_active()
        draft = self._require_creation_context().require_draft()
        if draft.placement_position is not None:
            if self._placement_dragging:
                if self._try_begin_wire_insertion(event, draft):
                    return True
                # While the initial placement gesture is still active, follow
                # the cursor and keep the transient CreationDraft and symbol
                # preview at the same snapped position. Persisting the draft
                # only happens on release through place_creation_draft().
                position = self._snap_position(event)
                if position is None:
                    return False
                self._position = position
                draft.set_placement(position)
                self._preview_active = True
                self._show_preview(position)
                return True
            self._preview_active = True
            self._show_preview(self._position or draft.placement_position)
            return True
        position = self._snap_position(event)
        if position is None:
            return False
        self._position = position
        self._preview_active = True
        self._show_preview(position)
        return True

    def on_mouse_release(self, event: Any) -> bool:
        self._ensure_active()
        draft = self._require_creation_context().require_draft()

        # Endpoint acquisition is press-authoritative: release must not
        # perform a second snap that can replace the accepted target.
        if self._endpoint_acquired_this_interaction and self._accepted_endpoint_snap is not None:
            self._show_preview(self._position or draft.placement_position)
            self._endpoint_acquired_this_interaction = False
            self._accepted_endpoint_snap = None
            return True

        # The initial press already accepted and stored the placement position.
        # Persist that position on release before attempting any second snap:
        # release may occur over a non-snappable area, and a second snap must
        # not be required to complete a valid position-first placement.
        if self._placement_dragging and draft.placement_position is not None:
            self._position = draft.placement_position
            self._placement_dragging = False
            self._show_preview(self._position)
            return self.place_creation_draft()

        position = self._snap_position(event)
        if position is None:
            return False
        if draft.placement_position is None:
            # Preserve support for release-only event adapters that do not
            # deliver a preceding press callback.
            self._position = position
            draft.set_placement(position)
            self._preview_active = True
            self._show_preview(position)
            return self.place_creation_draft()
        self._position = draft.placement_position
        self._show_preview(self._position)
        return True

    def on_mouse_double_click(self, event: Any) -> bool:
        return self.on_mouse_press(event)

    def on_key_press(self, event: Any) -> bool:
        self._ensure_active()
        if self._is_escape_event(event):
            return self.on_cancel()
        return False

    def on_cancel(self) -> bool:
        self._ensure_active()
        creation_context = self._require_creation_context()
        draft = creation_context.snapshot_draft()
        had_state = (
            self._preview_active
            or self._position is not None
            or (draft is not None and draft.placement_position is not None)
            or (draft is not None and bool(draft.endpoints))
        )
        self._clear_state()
        # ToolManager owns CreationContext cancellation and its controller
        # notification; this hook clears only the tool's transient visuals.
        return had_state

    def on_reset(self) -> None:
        self._ensure_active()
        self._clear_state()

    def _snap_result(self, event: Any) -> Any:
        scene_position = self.event_position(event)
        snap = getattr(self.get_snap_system(), "snap", None)
        if not callable(snap):
            raise TypeError("SnapSystem must provide snap().")
        result = snap(scene_position, allow_grid=True, allow_object=True, intent="PLACE")
        if getattr(result, "position", None) is None:
            return None
        return result

    def _snap_endpoint_result(self, event: Any) -> Any:
        scene_position = self.event_position(event)
        snap_endpoint = getattr(self.get_snap_system(), "snap_endpoint", None)
        if not callable(snap_endpoint):
            raise TypeError("SnapSystem must provide snap_endpoint().")
        result = snap_endpoint(scene_position)
        if getattr(result, "position", None) is None:
            return None
        return result

    def _snap_position(self, event: Any) -> Optional[Tuple[float, float]]:
        result = self._snap_result(event)
        position = getattr(result, "position", None) if result is not None else None
        if position is None:
            return None
        return self._position_tuple(position)

    def _show_preview(self, position: Tuple[float, float]) -> None:
        """Show a transient SymbolDefinition-driven equipment preview."""
        if self._preview_layer is None:
            return
        if self._symbol_registry is None:
            raise RuntimeError("Model placement requires the canonical SymbolRegistry for preview.")
        if not self.SYMBOL_ID:
            raise RuntimeError(f"{self.MODEL_NAME} has no canonical SYMBOL_ID.")
        definition = self._symbol_registry.require(self.SYMBOL_ID)
        draft = self._require_creation_context().require_draft()
        item = SymbolPreviewItem(
            definition,
            position=position,
            rotation=draft.orientation,
            presentation_state=draft.preview_state,
            draft_id=self._active_draft_id,
            terminal_names=tuple(item.terminal_name for item in draft.definition.terminal_requirements),
            element_type=draft.equipment_type,
        )
        replace = getattr(self._preview_layer, "replace", None)
        if not callable(replace):
            raise TypeError("PreviewLayer must provide replace().")
        replace((item,))

    @staticmethod
    def _position_tuple(position: Any) -> Tuple[float, float]:
        if hasattr(position, "x") and hasattr(position, "y"):
            return float(position.x()), float(position.y())
        if isinstance(position, (tuple, list)) and len(position) >= 2:
            return float(position[0]), float(position[1])
        raise TypeError("SnapResult.position must provide x/y coordinates or a two-element position.")

    @staticmethod
    def _is_escape_event(event: Any) -> bool:
        if event is None:
            return False
        key = getattr(event, "key", None)
        if callable(key):
            key = key()
        if isinstance(event, dict):
            key = event.get("key", key)
        return key in ("Escape", "escape", 0x01000000)

    def _clear_state(self) -> None:
        self._position = None
        self._preview_active = False
        self._endpoint_acquired_this_interaction = False
        self._accepted_endpoint_snap = None
        self._active_draft_id = None
        self._placement_dragging = False
        if self._preview_layer is not None:
            clear_preview = getattr(self._preview_layer, "clear_preview", None)
            if callable(clear_preview):
                clear_preview()
            else:
                clear = getattr(self._preview_layer, "clear", None)
                if callable(clear):
                    clear()

    def get_state(self) -> dict[str, Any]:
        state = super().get_state()
        state.update({
            "position": self._position,
            "preview_active": self._preview_active,
            "creation_active": self._creation_context is not None and self._creation_context.active,
            "placement_phase": (
                self._creation_context.draft.phase.value
                if self._creation_context is not None and self._creation_context.draft is not None
                else "INACTIVE"
            ),
            "required_endpoints": (
                tuple(item.terminal_name for item in self._creation_context.draft.definition.terminal_requirements
                      if self._creation_context.draft.endpoints.get(item.terminal_name) is None)
                if self._creation_context is not None and self._creation_context.draft is not None
                else ()
            ),
        })
        return state


__all__ = ["ModelPlacementTool"]