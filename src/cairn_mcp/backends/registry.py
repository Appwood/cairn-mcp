from __future__ import annotations

from importlib import metadata
from typing import TYPE_CHECKING

from cairn_mcp.config import ConfigError

if TYPE_CHECKING:
    from cairn_mcp.backends.base import LogBackend


ENTRY_POINT_GROUP = "cairn_mcp.backends"

_REGISTRY: dict[str, type["LogBackend"]] = {}
_ENTRY_POINTS_LOADED = False


def register_backend(backend_cls: type["LogBackend"]) -> type["LogBackend"]:
    """Register a backend class under its ``type_name``.

    Usable as a decorator. Third-party packages can instead expose an entry
    point in the ``cairn_mcp.backends`` group and the adapter
    discovers them automatically — no core change required.
    """
    type_name = (backend_cls.type_name or "").strip().lower()
    if not type_name:
        raise ValueError(f"{backend_cls.__name__} must define a non-empty type_name")
    _REGISTRY[type_name] = backend_cls
    return backend_cls


def _load_entry_point_backends() -> list[str]:
    global _ENTRY_POINTS_LOADED
    warnings: list[str] = []
    if _ENTRY_POINTS_LOADED:
        return warnings
    _ENTRY_POINTS_LOADED = True
    try:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 - discovery must never break startup
        return [f"backend entry-point discovery failed: {exc}"]
    for entry_point in entry_points:
        try:
            backend_cls = entry_point.load()
            register_backend(backend_cls)
        except Exception as exc:  # noqa: BLE001 - one bad plugin shouldn't kill others
            warnings.append(f"failed to load backend plugin {entry_point.name!r}: {exc}")
    return warnings


def available_backends() -> list[str]:
    _load_entry_point_backends()
    return sorted(_REGISTRY)


def get_backend_class(type_name: str) -> type["LogBackend"]:
    _load_entry_point_backends()
    normalized = type_name.strip().lower()
    try:
        return _REGISTRY[normalized]
    except KeyError:
        available = ", ".join(available_backends()) or "(none)"
        raise ConfigError(
            f"unknown source type {type_name!r}; available types: {available}"
        ) from None
