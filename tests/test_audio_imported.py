"""Imported input is event evidence, never fabricated planner authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import mido
import numpy as np
import soundfile as sf


def imported_fixture(path: Path, *, midi_type: int = 1) -> Path:
    midi = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    conductor = mido.MidiTrack([
        mido.MetaMessage('set_tempo', tempo=500000),
        mido.MetaMessage('time_signature', numerator=4, denominator=4),
    ])
    voice = mido.MidiTrack([
        mido.MetaMessage('track_name', name='../rough_mix'),
        mido.Message('program_change', program=33, channel=1),
        mido.Message('note_on', note=57, velocity=100, channel=1),
        mido.Message('note_off', note=57, channel=1, time=480),
        mido.MetaMessage('set_tempo', tempo=750000),
        mido.MetaMessage('time_signature', numerator=3, denominator=4),
        mido.Message('note_on', note=60, velocity=100, channel=1),
        mido.Message('note_off', note=60, channel=1, time=480),
    ])
    midi.tracks = [conductor + voice] if midi_type == 0 else [conductor, voice]
    midi.save(path)
    return path


class TestImportedContext(unittest.TestCase):
    def test_context_records_maps_programs_and_absence_of_planner(self) -> None:
        from aimusic.audio.from_midi import from_midi

        with tempfile.TemporaryDirectory() as tmp:
            for midi_type in (0, 1):
                with self.subTest(midi_type=midi_type):
                    path = imported_fixture(Path(tmp) / f'{midi_type}.mid', midi_type=midi_type)
                    original = path.read_bytes()
                    ctx = from_midi(path)
                    doc = ctx.analysis_document()
                    self.assertEqual(ctx.provenance, 'inferred')
                    self.assertEqual(doc['midi_type'], midi_type)
                    self.assertEqual(doc['planner_reconciliation']['status'], 'not_applicable')
                    self.assertNotIn('passed', doc['planner_reconciliation'])
                    self.assertEqual(doc['tempo_map'], [[0.0, 120.0], [0.5, 80.0]])
                    self.assertEqual(doc['meter_map'], [[0, 4, 4], [480, 3, 4]])
                    self.assertEqual(doc['bar_boundaries_sec'], [0.0, 0.5, 1.25])
                    self.assertEqual(doc['total_notes'], 2)
                    self.assertEqual(doc['tracks'][0]['programs'], [33])
                    self.assertEqual(doc['tracks'][0]['role'], 'bass')
                    self.assertEqual(doc['tracks'][0]['source_track_index'], midi_type)
                    self.assertEqual(doc['tuning']['intended_lattice'], 'unknown')
                    self.assertEqual(doc['tuning']['audio_pitch_accuracy'], 'unmeasured')
                    self.assertEqual(ctx.verified_identity['midi_sha256'], hashlib.sha256(original).hexdigest())
                    self.assertEqual(path.read_bytes(), original)

    def test_type0_multiple_channels_and_default_maps_are_explicit(self) -> None:
        from aimusic.audio.from_midi import from_midi

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mixed.mid'
            midi = mido.MidiFile(type=0)
            midi.tracks.append(mido.MidiTrack([
                mido.Message('note_on', channel=0, note=60, velocity=80),
                mido.Message('note_on', channel=9, note=36, velocity=80),
                mido.Message('note_off', channel=0, note=60, time=480),
                mido.Message('note_off', channel=9, note=36),
            ]))
            midi.save(path)
            doc = from_midi(path).analysis_document()
            self.assertEqual(doc['tempo_map'], [[0.0, 120.0]])
            self.assertEqual(doc['meter_map'], [[0, 4, 4]])
            self.assertEqual(doc['tracks'][0]['channels'], [0, 9])
            self.assertEqual(doc['tracks'][0]['role'], 'mixed')
            self.assertEqual(doc['tracks'][0]['programs'], [0])
            self.assertFalse(doc['tracks'][0]['is_drum'])

    def test_tuning_evidence_retains_bends_rpn_and_sysex(self) -> None:
        from aimusic.audio.from_midi import from_midi

        with tempfile.TemporaryDirectory() as tmp:
            path = imported_fixture(Path(tmp) / 'tuning.mid')
            midi = mido.MidiFile(path)
            setup = [mido.Message('control_change', channel=1, control=c, value=v)
                     for c, v in ((101, 0), (100, 0), (6, 12), (38, 0))]
            setup += [mido.Message('pitchwheel', channel=1, pitch=2048),
                      mido.Message('sysex', data=(0x7e, 0x7f, 8, 1))]
            midi.tracks[0][0:0] = setup
            midi.save(path)
            doc = from_midi(path).analysis_document()
            evidence = doc['tuning']['events']
            self.assertEqual([row['message']['type'] for row in evidence],
                             ['control_change'] * 4 + ['pitchwheel', 'sysex'])
            self.assertEqual(evidence[4]['message']['pitch'], 2048)
            self.assertEqual(evidence[4]['track'], 0)
            self.assertEqual(evidence[4]['tick'], 0)
            json.dumps(doc)  # Persistable, not native objects or a pitch certificate.

    def test_rejects_malformed_type2_and_cross_track_state_ownership(self) -> None:
        from aimusic.audio.from_midi import from_midi

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / 'malformed.mid'
            bad.write_bytes(b'not MIDI')
            type2 = imported_fixture(root / 'type2.mid', midi_type=2)
            conflict = imported_fixture(root / 'conflict.mid')
            midi = mido.MidiFile(conflict)
            midi.tracks.append(mido.MidiTrack([
                mido.Message('control_change', channel=1, control=7, value=20),
                mido.Message('note_on', channel=2, note=65, velocity=90),
                mido.Message('note_off', channel=2, note=65, time=480),
            ]))
            midi.save(conflict)
            for path, diagnostic in ((bad, 'Malformed MIDI'), (type2, 'type 2'),
                                     (conflict, 'channel ownership')):
                original = path.read_bytes()
                with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, diagnostic):
                    from_midi(path)
                self.assertEqual(path.read_bytes(), original)

    def test_orchestrator_renders_import_context_and_preserves_source(self) -> None:
        from aimusic.audio.config import load_audio_config
        from aimusic.audio.from_midi import from_midi
        from aimusic.audio.orchestrator import Orchestrator

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for midi_type in (0, 1):
                with self.subTest(midi_type=midi_type):
                    source = imported_fixture(root / f'source{midi_type}.mid', midi_type=midi_type)
                    original = source.read_bytes()
                    ctx = from_midi(source)
                    cfg = load_audio_config('preview').to_dict()
                    out = Orchestrator(cfg, root / 'cache').run(ctx, output_dir=root / f'out{midi_type}')
                    doc = json.loads((out / f'{source.stem}.analysis.json').read_text())
                    self.assertEqual(doc['provenance'], 'inferred')
                    report = json.loads((out / f'{source.stem}.reconciliation.json').read_text())
                    self.assertEqual(report['status'], 'not_applicable')
                    self.assertNotIn('passed', report)
                    self.assertEqual((out / 'source.mid').read_bytes(), original)
                    waves = list((out / 'stems').glob('track_*.wav'))
                    self.assertEqual(len(waves), 1)
                    for wav in waves + [out / 'stems/rough_mix.wav']:
                        self.assertGreater(wav.stat().st_size, 44)
                        audio, sr = sf.read(wav)
                        self.assertEqual(sr, 48000)
                        self.assertEqual(len(audio), 72000)  # 1.25 sec + .25 tail
                        self.assertTrue(np.isfinite(audio).all())
                        self.assertGreater(float(np.sqrt(np.mean(audio ** 2))), 1e-5)
                        # A tempo change on track 1 must not be ignored by preview.
                        self.assertGreater(float(np.max(np.abs(audio[55000:57000]))), 1e-5)
                    self.assertEqual(source.read_bytes(), original)

    def test_stale_context_rejected_before_publication(self) -> None:
        from aimusic.audio.config import load_audio_config
        from aimusic.audio.from_midi import from_midi
        from aimusic.audio.orchestrator import Orchestrator

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = imported_fixture(root / 'source.mid')
            ctx = from_midi(source)
            midi = mido.MidiFile(source)
            midi.tracks[1][1].program = 34
            midi.save(source)
            changed = source.read_bytes()
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                Orchestrator(load_audio_config('preview').to_dict(), root / 'cache').run(ctx, output_dir=root / 'out')
            self.assertFalse((root / 'out').exists())
            self.assertEqual(source.read_bytes(), changed)
