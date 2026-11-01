"""Imported MIDI role inference keeps GM pad and lead families distinct."""

import tempfile
import unittest
from pathlib import Path

import pretty_midi as pm

from aimusic.audio.analysis.from_midi import analyze_midi


class TestImportedRoles(unittest.TestCase):
    def test_program_boundaries_and_legacy_names(self) -> None:
        cases = [
            (0, "", "piano"), (7, "", "piano"), (8, "", "comping"),
            (31, "", "comping"), (32, "", "bass"), (39, "", "bass"),
            (40, "", "comping"), (51, "", "comping"), (79, "", "comping"),
            (80, "", "lead"), (87, "", "lead"), (88, "", "pads"),
            (95, "", "pads"), (96, "", "lead"), (103, "", "lead"),
            (104, "", "comping"), (127, "", "comping"),
            (80, "comp", "comping"), (80, "comping", "comping"),
            (88, "Lead solo", "lead"), (80, "Pads", "pads"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.mid"
            for program, name, expected in cases:
                with self.subTest(program=program, name=name):
                    midi = pm.PrettyMIDI()
                    inst = pm.Instrument(program, name=name)
                    inst.notes.append(pm.Note(90, 60, 0.0, 1.0))
                    midi.instruments.append(inst)
                    midi.write(str(path))
                    doc = analyze_midi(path, output_path=Path(tmp) / "analysis.json")
                    self.assertEqual(doc["tracks"][0]["role"], expected)
