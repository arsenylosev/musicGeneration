"""Offline wheel smoke test: installed resources, no editable/checkout imports."""

from __future__ import annotations

import os
from email import message_from_bytes
from zipfile import ZipFile
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestAudioWheel(unittest.TestCase):
    def test_installed_profiles_outside_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source, wheels, installed = (root / name for name in ("source", "wheels", "installed"))
            source.mkdir()
            shutil.copytree(REPO_ROOT / "aimusic", source / "aimusic",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("pyproject.toml", "readme.md"):
                shutil.copy2(REPO_ROOT / name, source / name)
            env = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME"}}

            def run(*args: str) -> None:
                result = subprocess.run(
                    [sys.executable, *args], cwd=root, env=env,
                    text=True, capture_output=True, timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            run("-I", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                "--no-index", "--wheel-dir", str(wheels), str(source))
            built = list(wheels.glob("aimusic-*.whl"))
            self.assertEqual(len(built), 1)
            with ZipFile(built[0]) as wheel:
                meta = message_from_bytes(wheel.read('aimusic-0.1.0.dist-info/METADATA'))
            requirements = meta.get_all('Requires-Dist', [])
            self.assertEqual([r for r in requirements if ';' not in r], ['numpy', 'mido'])
            self.assertIn('pyfluidsynth==1.3.4; extra == "audio-render"', requirements)
            self.assertIn('aimusic[audio]; extra == "audio-render"', requirements)
            run("-I", "-m", "pip", "install", "--no-deps", "--no-index",
                "--target", str(installed), str(built[0]))
            # Supply only the already-installed YAML dependency, offline. Do not
            # expose the host site-packages (which may contain editable aimusic).
            shutil.copytree(Path(yaml.__file__).parent, installed / "yaml")
            shutil.rmtree(source)
            run("-I", "-S", "-c", textwrap.dedent("""\
                import os
                import sys
                from pathlib import Path
                installed, checkout = map(Path, sys.argv[1:])
                sys.path.insert(0, str(installed))
                assert 'PYTHONPATH' not in os.environ
                assert not Path.cwd().is_relative_to(checkout)
                assert all(not Path(p).resolve().is_relative_to(checkout) for p in sys.path)
                import aimusic
                from aimusic.audio.config import load_audio_config
                assert Path(aimusic.__file__).is_relative_to(installed)
                for profile, backend, fallback in (
                    (None, 'simple', True), ('preview', 'simple', True),
                    ('listening', 'fluidsynth', False),
                ):
                    config = load_audio_config(profile)
                    raw = config.to_dict()
                    assert config.source_path is None
                    assert raw['render']['backend'] == backend
                    assert raw['render']['allow_simple_fallback'] is fallback
                    assert not raw['stages']['restyle']
                    assert not raw['groove']['models']['midi_gpt']
                    assert raw['render']['routing']['comping']['default'] == backend
                assert not any(name in sys.modules for name in (
                    'fluidsynth', 'pretty_midi', 'soundfile', 'torch',
                ))
                print('installed wheel profiles OK')
                """), str(installed), str(REPO_ROOT))
