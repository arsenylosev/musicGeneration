"""Portable, effective audio configuration (no native renderer required)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from aimusic.audio.config import load_audio_config
from aimusic.audio.orchestrator import audio_config_to_dict


class TestAudioConfig(unittest.TestCase):
    def test_packaged_profiles_have_safe_effective_defaults(self) -> None:
        for profile, backend, fallback in (
            (None, "simple", True), ("preview", "simple", True),
            ("listening", "fluidsynth", False),
        ):
            with self.subTest(profile=profile):
                config = load_audio_config(profile)
                raw = audio_config_to_dict(config)
                self.assertIsNone(config.source_path)
                self.assertEqual(config.render.backend, backend)
                self.assertEqual(config.render.allow_simple_fallback, fallback)
                self.assertEqual(raw["render"]["backend"], backend)
                self.assertEqual(raw["render"]["allow_simple_fallback"], fallback)
                for route in raw["render"]["routing"].values():
                    self.assertEqual(route, {"default": backend, "microtonal": backend})
                self.assertFalse(raw["stages"]["restyle"])
                self.assertFalse(raw["groove"]["models"]["midi_gpt"])
                self.assertEqual(raw["scoring"]["floors"]["tuning_cents_p95"], 15.0)

    def test_explicit_yaml_settings_survive_source_deletion_and_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.yaml"
            path.write_text(yaml.safe_dump({
                "render": {"backend": "custom", "sample_rate": 24000},
                "groove": {"spec": {"swing": {"ratio": 0.62}}},
                "budget": {"max_endpoint_calls": 3},
                "custom": {"options": ["one", "two"]},
            }))
            config = load_audio_config(path)
            self.assertEqual(config.source_path, path.resolve())
            path.unlink()
            with patch.object(Path, "read_text", side_effect=AssertionError("reread")):
                raw = audio_config_to_dict(config)
            self.assertEqual(raw["render"]["sample_rate"], 24000)
            self.assertEqual(raw["groove"]["spec"]["swing"]["ratio"], 0.62)
            self.assertEqual(raw["budget"]["max_endpoint_calls"], 3)
            self.assertEqual(raw["custom"]["options"], ["one", "two"])
            self.assertFalse(raw["stages"]["restyle"])
            self.assertEqual(raw["code_version"], "0.1.0")

    def test_effective_config_is_frozen_and_conversion_is_detached(self) -> None:
        config = load_audio_config()
        with self.assertRaises(FrozenInstanceError):
            setattr(config, "mode", "changed")
        raw: Any = config.raw
        with self.assertRaises(TypeError):
            raw["render"]["backend"] = "changed"
        converted = audio_config_to_dict(config)
        converted["render"]["backend"] = "changed"
        converted["restyle"]["endpoints"].append("changed")
        self.assertEqual(audio_config_to_dict(config)["render"]["backend"], "simple")
        self.assertNotIn("changed", audio_config_to_dict(config)["restyle"]["endpoints"])

    def test_typed_overrides_are_reflected_without_reopening_source(self) -> None:
        config = load_audio_config()
        modified = replace(config, render=replace(config.render, sample_rate=22050))
        self.assertEqual(audio_config_to_dict(modified)["render"]["sample_rate"], 22050)
        self.assertEqual(modified.raw["render"]["sample_rate"], 22050)

    def test_path_named_preview_remains_an_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview"
            path.write_text("render: {sample_rate: 16000}\n")
            self.assertEqual(load_audio_config(path).render.sample_rate, 16000)

    def test_invalid_profile_roots_and_missing_files_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            with self.assertRaisesRegex(FileNotFoundError, "Audio config not found"):
                load_audio_config(path)
            for text in ("- not-a-mapping", "render: []"):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, "mapping"):
                    load_audio_config(path)


class TestAudioRouting(unittest.TestCase):
    def load_render(self, render: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing.yaml"
            path.write_text(yaml.safe_dump({"render": render}, sort_keys=False))
            return audio_config_to_dict(load_audio_config(path))["render"]

    def test_legacy_scalar_expands_both_tuning_classes_and_role_alias(self) -> None:
        render = self.load_render({"role_map": {"comp": "custom"}})
        self.assertNotIn("role_map", render)
        self.assertNotIn("comp", render["routing"])
        self.assertEqual(render["routing"]["comping"], {
            "default": "custom", "microtonal": "custom",
        })

    def test_equivalent_aliases_merge_independent_of_order(self) -> None:
        canonical = {"comping": {"default": "custom", "microtonal": "custom"}}
        for render in (
            {"routing": canonical, "role_map": {"comp": "custom"}},
            {"role_map": {"comping": "custom", "comp": "custom"}, "routing": canonical},
            {"routing": {"comp": {"default": "custom"}, **canonical}},
        ):
            with self.subTest(render=render):
                normalized = self.load_render(render)
                self.assertNotIn("role_map", normalized)
                self.assertEqual(normalized["routing"]["comping"], canonical["comping"])

    def test_complementary_role_aliases_merge_by_tuning_class(self) -> None:
        render = self.load_render({"routing": {
            "comp": {"default": "simple"}, "comping": {"microtonal": "fluidsynth"},
        }})
        self.assertEqual(render["routing"]["comping"], {
            "default": "simple", "microtonal": "fluidsynth",
        })
        self.assertNotIn("comp", render["routing"])

    def test_contradictory_aliases_fail_with_role_class_and_values(self) -> None:
        cases = (
            {"role_map": {"comp": "simple", "comping": "fluidsynth"}},
            {"routing": {"comp": {"default": "simple"}, "comping": {"default": "fluidsynth"}}},
            {"role_map": {"comp": "simple"}, "routing": {"comping": {"default": "fluidsynth"}}},
            {"routing": {"comping": {"microtonal": "simple"}}, "role_map": {"comp": "fluidsynth"}},
        )
        for render in cases:
            with self.subTest(render=render), self.assertRaisesRegex(
                ValueError, r"conflict.*comping\.(default|microtonal)(?=.*simple)(?=.*fluidsynth)"
            ):
                self.load_render(render)

    def test_malformed_routes_are_rejected_not_stringified(self) -> None:
        cases: tuple[dict[str, Any], ...] = (
            {"routing": []}, {"role_map": []}, {"role_map": {"bass": 3}},
            {"routing": {"bass": "simple"}},
            {"routing": {"bass": {"unknown": "simple"}}},
            {"routing": {"bass": {"default": ""}}},
            {"routing": {"bass": {"default": []}}},
        )
        for render in cases:
            with self.subTest(render=render), self.assertRaises(ValueError):
                self.load_render(render)


class TestSoundFontResolution(unittest.TestCase):
    def test_cli_then_config_then_environment_precedence(self) -> None:
        import os
        from aimusic.audio.config import resolve_soundfont

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli, configured, env = (root / name for name in ("cli.sf2", "profile.sf2", "env.sf2"))
            for path in (cli, configured, env):
                path.write_bytes(b"asset fixture (not a native SoundFont)")
            profile = root / "profile.yaml"
            profile.write_text("paths: {soundfont: profile.sf2}\n")
            config = load_audio_config(profile)
            with patch.dict(os.environ, {"AIMUSIC_SOUNDFONT": str(env)}):
                self.assertEqual(resolve_soundfont(config, explicit=cli), cli.resolve())
                self.assertEqual(resolve_soundfont(config), configured.resolve())
                self.assertEqual(resolve_soundfont(load_audio_config("listening")), env.resolve())

    def test_invalid_higher_precedence_path_never_falls_through(self) -> None:
        import os
        from aimusic.audio.config import resolve_soundfont

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "good.sf2"
            asset.write_bytes(b"asset")
            config = load_audio_config()
            config = replace(config, paths=replace(config.paths, soundfont=str(asset)))
            missing = root / "missing.sf2"
            with patch.dict(os.environ, {"AIMUSIC_SOUNDFONT": str(asset)}):
                for bad in (missing, root, ""):
                    with self.subTest(bad=bad), self.assertRaisesRegex(
                        ValueError, r"SoundFont.*--soundfont.*(file|path)"
                    ):
                        resolve_soundfont(config, explicit=bad)
                invalid = replace(config, paths=replace(config.paths, soundfont=str(missing)))
                self.assertEqual(resolve_soundfont(invalid, explicit=asset), asset.resolve())
                with self.assertRaisesRegex(ValueError, r"SoundFont.*paths.soundfont.*missing.sf2"):
                    resolve_soundfont(invalid)

    def test_config_relative_asset_is_captured_before_source_or_cwd_changes(self) -> None:
        import os
        from aimusic.audio.config import resolve_soundfont

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = root / "bank.sf2"
            asset.write_bytes(b"asset")
            profile = root / "profile.yaml"
            profile.write_text("paths: {soundfont: bank.sf2}\n")
            config = load_audio_config(profile)
            profile.unlink()
            self.assertEqual(config.paths.soundfont, str(asset.resolve()))
            self.assertEqual(audio_config_to_dict(config)["paths"]["soundfont"], str(asset.resolve()))
            with patch.object(Path, "cwd", return_value=Path("/unrelated")), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(resolve_soundfont(config), asset.resolve())

    def test_environment_and_cli_relative_paths_use_working_directory(self) -> None:
        import os
        from aimusic.audio.config import resolve_soundfont

        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "bank.sf2"
            asset.write_bytes(b"asset")
            config = load_audio_config()
            with patch.object(Path, "cwd", return_value=Path(tmp)), patch.dict(
                os.environ, {"AIMUSIC_SOUNDFONT": "bank.sf2"}
            ):
                self.assertEqual(resolve_soundfont(config), asset.resolve())
                self.assertEqual(resolve_soundfont(config, explicit="bank.sf2"), asset.resolve())

    def test_unset_asset_is_optional_but_invalid_environment_is_not(self) -> None:
        import os
        from aimusic.audio.config import resolve_soundfont

        config = load_audio_config()
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolve_soundfont(config))
        with patch.dict(os.environ, {"AIMUSIC_SOUNDFONT": "/nonexistent/bank.sf2"}):
            with self.assertRaisesRegex(ValueError, r"SoundFont.*AIMUSIC_SOUNDFONT.*bank.sf2"):
                resolve_soundfont(config)

    def test_unreadable_asset_has_actionable_error(self) -> None:
        from aimusic.audio.config import resolve_soundfont

        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "bank.sf2"
            asset.write_bytes(b"asset")
            config = load_audio_config()
            with patch.object(Path, "open", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, r"SoundFont.*--soundfont.*readable"):
                    resolve_soundfont(config, explicit=asset)
