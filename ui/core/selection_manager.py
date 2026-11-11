# ============================================================
# File: ui/core/selection_manager.py
# GridForge V2 — Selection Manager
# ============================================================
"""Central transient UI selection authority and graphics projection."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional

from ui.core.qt import QGraphicsScene, QObject, Signal


class SelectionManager(QObject):
    """Own transient UI selection state and project it to graphics.

    Selection is UI interaction state. The electrical Core is not involved.
    ``selection_changed`` is the UI-local observation point for projections.
    """

    selection_changed = Signal(object)
    selection_cleared = Signal()

    NODE = "node"
    CONNECTION = "connection"

    def __init__(self, scene: Optional[QGraphicsScene] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._selected_ids_by_domain: dict[str, list[Any]] = {"default": []}
        self.scene = scene

    @staticmethod
    def _require_domain(domain: str) -> str:
        if not isinstance(domain, str) or not domain:
            raise ValueError("domain must be a non-empty string.")
        return domain

    def get_selected_ids(self, domain: Optional[str] = None) -> tuple[Any, ...]:
        if domain is not None:
            domain = self._require_domain(domain)
            return tuple(self._selected_ids_by_domain.get(domain, ()))
        selected: list[Any] = []
        for ids in self._selected_ids_by_domain.values():
            selected.extend(ids)
        return tuple(selected)

    @property
    def selected_ids(self) -> tuple[Any, ...]:
        return self.get_selected_ids()

    def snapshot_selection(self) -> dict[str, tuple[Any, ...]]:
        """Return a detached snapshot of every transient selection domain."""
        return {domain: tuple(ids) for domain, ids in self._selected_ids_by_domain.items()}

    def restore_selection(self, snapshot: dict[str, tuple[Any, ...]]) -> None:
        """Restore a previously captured selection without losing domain ownership."""
        if not isinstance(snapshot, dict):
            raise TypeError("selection snapshot must be a dict.")
        restored: dict[str, list[Any]] = {}
        for domain, ids in snapshot.items():
            self._require_domain(domain)
            values = tuple(ids)
            if any(value is None for value in values) or len(set(values)) != len(values):
                raise ValueError("selection snapshot contains invalid or duplicate identities.")
            restored[domain] = list(values)
        previous = self.get_selected_ids()
        self._selected_ids_by_domain = restored or {"default": []}
        self._emit_if_changed(previous)

    @contextmanager
    def temporary_single_selection(self, object_id: Any, *, domain: str = "default") -> Iterator[None]:
        """Temporarily expose one identity to a projection without changing graphics selection."""
        if object_id is None:
            raise ValueError("object_id must not be None.")
        domain = self._require_domain(domain)
        snapshot = self.snapshot_selection()
        try:
            for ids in self._selected_ids_by_domain.values():
                ids.clear()
            self._selected_ids_by_domain[domain] = [object_id]
            yield
        finally:
            self._selected_ids_by_domain = {
                key: list(values) for key, values in snapshot.items()
            } or {"default": []}

    def has_selection(self, domain: Optional[str] = None) -> bool:
        return bool(self.get_selected_ids(domain=domain))

    def is_selected(self, object_id: Any, domain: Optional[str] = None) -> bool:
        if object_id is None:
            return False
        return any(selected_id == object_id for selected_id in self.get_selected_ids(domain=domain))

    def select(self, object_id: Any, multi: bool = False, *, domain: str = "default") -> None:
        if object_id is None:
            raise ValueError("object_id must not be None.")
        if not isinstance(multi, bool):
            raise TypeError("multi must be a bool.")
        domain = self._require_domain(domain)
        previous = self.get_selected_ids()
        selected_ids = self._selected_ids_by_domain.setdefault(domain, [])
        if multi:
            if not self.is_selected(object_id, domain=domain):
                selected_ids.append(object_id)
        else:
            # A non-additive selection is globally single-selection, even
            # when the selected object belongs to a typed presentation
            # domain. This prevents a connection ID from coexisting with a
            # node ID unless the caller explicitly requests additive
            # selection.
            for ids in self._selected_ids_by_domain.values():
                ids.clear()
            self._selected_ids_by_domain[domain] = [object_id]
        self._emit_if_changed(previous)

    def select_single(self, object_id: Any, *, domain: str = "default") -> None:
        self.select(object_id, multi=False, domain=domain)

    def add_to_selection(self, object_id: Any, *, domain: str = "default") -> None:
        self.select(object_id, multi=True, domain=domain)

    def toggle_selection(self, object_id: Any, *, domain: str = "default") -> None:
        if object_id is None:
            raise ValueError("object_id must not be None.")
        domain = self._require_domain(domain)
        previous = self.get_selected_ids()
        selected_ids = self._selected_ids_by_domain.setdefault(domain, [])
        if self.is_selected(object_id, domain=domain):
            self._selected_ids_by_domain[domain] = [selected_id for selected_id in selected_ids if selected_id != object_id]
        else:
            selected_ids.append(object_id)
        self._emit_if_changed(previous)

    def clear(self) -> None:
        previous = self.get_selected_ids()
        if not previous:
            return
        for selected_ids in self._selected_ids_by_domain.values():
            selected_ids.clear()
        self._emit_if_changed(previous)

    def _emit_if_changed(self, previous: tuple[Any, ...]) -> None:
        current = self.get_selected_ids()
        if current == previous:
            return
        self.sync_graphics()
        self.selection_changed.emit(current)
        if not current:
            self.selection_cleared.emit()

    def sync_graphics(self, scene: Optional[QGraphicsScene] = None) -> None:
        target_scene = scene if scene is not None else self.scene
        if target_scene is None:
            return
        items_method = getattr(target_scene, "items", None)
        if not callable(items_method):
            raise TypeError("scene must provide items().")
        selected_ids = self.get_selected_ids()
        for item in tuple(items_method()):
            set_selected = getattr(item, "setSelected", None)
            if not callable(set_selected):
                continue
            object_id = getattr(item, "object_id", None)
            set_selected(object_id is not None and any(selected_id == object_id for selected_id in selected_ids))

    def reconcile(self, scene: Optional[QGraphicsScene] = None) -> None:
        self.sync_graphics(scene=scene)

    def get_item_for_id(self, object_id: Any, scene: Optional[QGraphicsScene] = None) -> Optional[Any]:
        if object_id is None:
            return None
        target_scene = scene if scene is not None else self.scene
        if target_scene is None:
            return None
        items_method = getattr(target_scene, "items", None)
        if not callable(items_method):
            raise TypeError("scene must provide items().")
        for item in tuple(items_method()):
            if getattr(item, "object_id", None) == object_id:
                return item
        return None

    def get_items_for_ids(self, object_ids: Iterable[Any], scene: Optional[QGraphicsScene] = None) -> tuple[Any, ...]:
        if object_ids is None:
            raise ValueError("object_ids must not be None.")
        requested_ids = tuple(object_ids)
        if not requested_ids:
            return ()
        target_scene = scene if scene is not None else self.scene
        if target_scene is None:
            return ()
        items_method = getattr(target_scene, "items", None)
        if not callable(items_method):
            raise TypeError("scene must provide items().")
        return tuple(item for item in tuple(items_method()) if any(requested_id == getattr(item, "object_id", None) for requested_id in requested_ids))

    def get_selected_items(self, scene: Optional[QGraphicsScene] = None) -> tuple[Any, ...]:
        return self.get_items_for_ids(self.get_selected_ids(), scene=scene)

    def set_scene(self, scene: Optional[QGraphicsScene]) -> None:
        self.scene = scene
        self.sync_graphics(scene=scene)

    def get_scene(self) -> Optional[QGraphicsScene]:
        return self.scene

    def reset_graphics(self, scene: Optional[QGraphicsScene] = None) -> None:
        target_scene = scene if scene is not None else self.scene
        if target_scene is None:
            return
        items_method = getattr(target_scene, "items", None)
        if not callable(items_method):
            raise TypeError("scene must provide items().")
        for item in tuple(items_method()):
            set_selected = getattr(item, "setSelected", None)
            if callable(set_selected):
                set_selected(False)

    def get_state(self) -> dict[str, Any]:
        selected_ids = self.get_selected_ids()
        return {
            "selected_count": len(selected_ids),
            "selected_ids": selected_ids,
            "has_selection": bool(selected_ids),
            "has_scene": self.scene is not None,
        }

    def __repr__(self) -> str:
        return f"SelectionManager(selected={len(self.get_selected_ids())}, scene={self.scene is not None})"


__all__ = ["SelectionManager"]
