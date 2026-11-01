"""Compatibility from loaded effective profiles to the existing role registry."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

from aimusic.audio.config import load_audio_config
from aimusic.audio.orchestrator import audio_config_to_dict
from aimusic.audio.render.registry import get_renderer, get_renderer_name


class TestAudioRegistryCompatibility(unittest.TestCase):
    def load_config(self, render: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.yaml"
            path.write_text(yaml.safe_dump({"render": render}))
            return audio_config_to_dict(load_audio_config(path))

    def test_loaded_legacy_and_canonical_routes_keep_role_overrides(self) -> None:
        for render in (
            {"role_map": {"comp": "custom"}},
            {"routing": {"comping": {"default": "custom", "microtonal": "other"}}},
        ):
            config = self.load_config(render)
            self.assertNotIn("role_map", config["render"])
            for role in ("comp", "comping"):
                with self.subTest(render=render, role=role):
                    self.assertEqual(get_renderer_name(role, config=config), "custom")
                    with self.assertRaisesRegex(KeyError, "custom"):
                        get_renderer(role, config=config)

    def test_loaded_role_route_honors_existing_fallback_policy(self) -> None:
        for allow in (True, False):
            config = self.load_config({
                "backend": "simple", "allow_simple_fallback": allow,
                "routing": {"bass": {"default": "fluidsynth"}},
            })
            with self.subTest(allow=allow):
                if allow:
                    self.assertEqual(get_renderer_name("bass", config=config), "simple")
                else:
                    with self.assertRaisesRegex(NotImplementedError, "fluidsynth"):
                        get_renderer_name("bass", config=config)

    def test_direct_legacy_dict_and_role_alias_remain_supported(self) -> None:
        for configured_role in ("comp", "comping"):
            config = {"render": {"backend": "default", "role_map": {configured_role: "custom"}}}
            for requested_role in ("comp", "comping"):
                with self.subTest(configured=configured_role, requested=requested_role):
                    self.assertEqual(get_renderer_name(requested_role, config=config), "custom")
            self.assertEqual(get_renderer_name("bass", config=config), "default")

    def test_direct_canonical_dict_uses_default_route_or_global_backend(self) -> None:
        config = {"render": {"backend": "global", "routing": {
            "comp": {"default": "custom", "microtonal": "other"},
            "bass": {"microtonal": "other"},
        }}}
        self.assertEqual(get_renderer_name("comping", config=config), "custom")
        self.assertEqual(get_renderer_name("bass", config=config), "global")
        self.assertEqual(get_renderer_name("lead", config=config), "global")
        self.assertEqual(get_renderer_name("comp"), "simple")
        self.assertEqual(get_renderer_name("comping", config={}), "simple")
