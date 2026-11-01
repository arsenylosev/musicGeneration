"""Role -> renderer routing (CI-safe simple backend only in this PR)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aimusic.audio.config import normalize_routing
from aimusic.audio.render import simple_r

RendererFn = Callable[..., Any]

_REGISTRY: dict[str, str] = {
    "drums": "simple",
    "bass": "simple",
    "comp": "simple",
    "lead": "simple",
    "pads": "simple",
    "ornament": "simple",
    "piano": "simple",
}

_BACKENDS: dict[str, RendererFn] = {
    "simple": simple_r.render_stem,
}


def get_renderer_name(role: str, *, config: dict[str, Any] | None = None) -> str:
    """Look up the default route; tuning capability dispatch remains separate."""
    if config:
        render = normalize_routing(config.get("render", {}))
        canonical_role = "comping" if role == "comp" else role
        route = render["routing"].get(canonical_role, {})
        backend = str(route.get("default", render.get("backend", "simple")))
        if backend == "fluidsynth":
            if bool(render.get("allow_simple_fallback", True)):
                return "simple"
            raise NotImplementedError(
                "fluidsynth backend is deferred to a later PR; "
                "set render.backend: simple for CI-safe stems"
            )
        return backend
    return _REGISTRY.get(role, "simple")


def get_renderer(role: str, *, config: dict[str, Any] | None = None) -> RendererFn:
    """Return the callable renderer for a role."""
    name = get_renderer_name(role, config=config)
    if name not in _BACKENDS:
        raise KeyError(f"renderer backend not registered: {name}")
    return _BACKENDS[name]


def list_roles() -> list[str]:
    return sorted(_REGISTRY.keys())
