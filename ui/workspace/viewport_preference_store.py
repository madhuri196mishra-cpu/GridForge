"""UI-owned persistence for logical viewport preferences.

Viewport preferences are local presentation settings. They are deliberately
stored outside project packages, Application state, and Core engineering data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ui.core.qt import QSettings

from .viewport_state import ViewportState


class ViewportPreferenceStore:
    """Persist one toolkit-independent ViewportState per logical view identity."""

    def __init__(self, settings: Any | None = None) -> None:
        self._settings = settings if settings is not None else QSettings(
            "GridForge",
            "GridForge",
        )

    def load(self, view_id: str) -> ViewportState | None:
        key = self._key(view_id)
        value = self._settings.value(key, None)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            return None
        try:
            return ViewportState.from_dict(dict(value))
        except (TypeError, ValueError, OverflowError):
            # Invalid or obsolete preference data must not prevent a project
            # from opening; the caller retains the normal default viewport.
            return None

    def save(self, view_id: str, state: ViewportState) -> None:
        if not isinstance(state, ViewportState):
            raise TypeError("state must be a ViewportState.")
        self._settings.setValue(self._key(view_id), state.to_dict())
        self._settings.sync()

    @staticmethod
    def _key(view_id: str) -> str:
        if not isinstance(view_id, str) or not view_id.strip():
            raise ValueError("view_id must be a non-empty string.")
        return f"viewports/{view_id.strip()}"


__all__ = ["ViewportPreferenceStore"]
