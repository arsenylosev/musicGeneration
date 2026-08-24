"""Tests for RenderPackage contract (AUDIO_PIPELINE_ARCHITECTURE.pdf §3)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aimusic.core.core_types import BeatState, NoteEvent, Score
from aimusic.core.diagnostics import RunManifest
from aimusic.render.package import (
    STRUCTURE_SCHEMA,
    assert_contract_invariants,
    build_structure,
    load_render_package,
    write_render_package,
)
from aimusic.theory.edo import EDO
from aimusic.core.config import EDOConfig, MicrotonalRendering
from aimusic.render import render_midi


def _tiny_score(*, edo: int = 12) -> tuple[Score, tuple[BeatState, ...]]:
    # For 19-EDO, use step 1 on lead (~63¢) so microtonal detection fires (>8¢).
    events = (
        NoteEvent(ton=0, toff=240, h=12 if edo == 12 else 19, v=0.8, track="bass"),
        NoteEvent(ton=0, toff=120, h=36 if edo == 12 else 1, v=0.7, track="lead"),
        NoteEvent(ton=0, toff=60, h=40, v=0.9, track="drums"),
        NoteEvent(ton=240, toff=480, h=16 if edo == 12 else 21, v=0.75, track="comping"),
    )
    score = Score(note_events=events, ticks_per_beat=480, tempo_bpm=120.0)
    path = tuple(
        BeatState(
            meter_id=0,
            beat_in_bar=i % 4,
            boundary_lvl=2 if i % 4 == 0 else 0,
            key_id=0,
            chord_id=0,
            role_id=0,
            head_id=0,
            groove_id=0,
        )
        for i in range(8)
    )
    return score, path


class TestRenderPackage(unittest.TestCase):
    def test_write_and_load_package_passes_invariants(self) -> None:
        score, path = _tiny_score()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            midi_path = tmp_path / "tmp.mid"
            edo = EDO(EDOConfig(n=12, base_tuning=0, pitch_bend_range=2,
                                microtonal_rendering_method=MicrotonalRendering.MPE))
            render_midi(score, edo, str(midi_path))
            manifest = RunManifest(seed=1, config_dump={"edo": 12})
            package = write_render_package(
                tmp_path,
                score=score,
                midi_path=midi_path,
                manifest=manifest,
                path=path,
                edo=12,
                run_id="fixture01",
            )
            self.assertTrue(package.root.is_dir())
            for name in ("score.mid", "score.json", "structure.json", "tuning.json", "manifest.json"):
                self.assertTrue((package.root / name).is_file(), msg=name)
            structure = json.loads(package.structure_path.read_text(encoding="utf-8"))
            self.assertEqual(structure["schema"], STRUCTURE_SCHEMA)
            self.assertEqual(structure["provenance"], "planner")
            loaded = load_render_package(package.root)
            assert_contract_invariants(loaded, score=score)

    def test_structure_marks_19edo_lead_microtonal(self) -> None:
        score, path = _tiny_score(edo=19)
        structure = build_structure(score, path, edo=19, base_tuning=0.0)
        by_name = {t["name"]: t for t in structure.tracks}
        self.assertTrue(by_name["lead"]["microtonal"] or by_name["bass"]["microtonal"])

    def test_cli_generate_emits_render_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aimusic.app.cli",
                    "generate",
                    "--seed",
                    "11",
                    "--beats",
                    "4",
                    "--out",
                    tmp,
                ],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            packages = list(Path(tmp).glob("run_*"))
            self.assertEqual(len(packages), 1, msg=result.stdout)
            load_render_package(packages[0])


if __name__ == "__main__":
    unittest.main()
