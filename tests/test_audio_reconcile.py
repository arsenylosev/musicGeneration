"""Source-MIDI reconciliation: planner facts are not MIDI inference."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import mido

from aimusic.audio.from_score import from_score
from aimusic.core.config import EDOConfig
from aimusic.core.core_types import NoteEvent, Score
from aimusic.core.diagnostics import RunManifest
from aimusic.core.render_package import write_render_package
from aimusic.render import render_midi
from aimusic.theory.edo import EDO


class TestAudioReconcile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = self.package()

    def package(self, *, edo=12, name='texture', notes=None):
        score = Score(note_events=notes or (
            NoteEvent(ton=0, toff=480, h=19, v=0.8, track=name),
            NoteEvent(ton=240, toff=720, h=21, v=0.8, track=name),
        ))
        midi = self.root / f'{edo}.mid'
        render_midi(score, EDO(EDOConfig(n=edo)), str(midi))
        package = write_render_package(
            self.root, score=score, midi_path=midi, manifest=RunManifest(seed=1, config_dump={}),
            edo=edo, track_programs={name: 0} if name == 'texture' else None,
        )
        return from_score(package.root)

    def reconcile(self, ctx=None):
        from aimusic.audio.analysis.reconcile import reconcile
        return reconcile(ctx or self.ctx)

    def test_reports_authoritative_roles_counts_and_trailing_silence(self) -> None:
        report = self.reconcile()
        self.assertTrue(report.passed)
        self.assertEqual(report.score_occupancy_sec, 0.75)
        self.assertEqual(report.planner_extent_sec, 2.0)
        track = report.tracks[0]
        self.assertEqual(track['name'], 'texture')
        self.assertEqual(track['source_track_index'], 1)
        self.assertEqual(track['role'], 'lead')
        self.assertEqual(track['note_count'], 2)
        self.assertEqual(track['role_source'], 'structure.json')
        self.assertEqual(track['count_source'], 'score.json')
        unavailable = [c for c in report.checks if c.field == 'role_inference']
        self.assertEqual(unavailable[0].outcome, 'unavailable')
        self.assertIsNone(unavailable[0].observed)
        for check in report.to_dict()['checks']:
            self.assertTrue({'field', 'expected', 'observed', 'tolerance', 'outcome'} <= check.keys())

    def test_mpe_member_channels_count_as_one_source_track(self) -> None:
        report = self.reconcile(self.package(edo=19))
        self.assertTrue(report.passed)
        self.assertEqual(len(report.tracks), 1)
        self.assertEqual(report.tracks[0]['note_count'], 2)
        self.assertEqual(len(report.tracks[0]['channels']), 2)

    def test_role_alias_and_nonbinding_heuristic(self) -> None:
        ctx = self.package(name='lead')
        structure = replace(ctx.structure, tracks=[{**ctx.structure.tracks[0], 'role': 'comp'}])
        report = self.reconcile(replace(ctx, structure=structure))
        self.assertTrue(report.passed)
        self.assertEqual(report.tracks[0]['role'], 'comping')
        inferred = next(c for c in report.checks if c.field == 'role_inference')
        self.assertEqual(inferred.observed, 'lead')
        self.assertEqual(inferred.outcome, 'informational')

    def test_exact_program_count_identity_and_drum_metadata(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        for field, value in (('program', 1), ('percussion', True), ('name', 'other')):
            with self.subTest(field=field):
                structure = replace(self.ctx.structure, tracks=[{**self.ctx.structure.tracks[0], field: value}])
                with self.assertRaisesRegex(ReconciliationError, field) as error:
                    self.reconcile(replace(self.ctx, structure=structure))
                self.assertFalse(error.exception.report.passed)
        score = replace(self.ctx.score, note_events=self.ctx.score.note_events[:1])
        with self.assertRaisesRegex(ReconciliationError, 'texture.*note_count'):
            self.reconcile(replace(self.ctx, score=score))
        drums = self.package(name='drums')
        structure = replace(drums.structure, tracks=[{**drums.structure.tracks[0], 'program': 1}])
        with self.assertRaisesRegex(ReconciliationError, 'drums.*program'):
            self.reconcile(replace(drums, structure=structure))

    def test_tempo_tolerance_is_001_bpm(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        for delta in (0.0009, 0.001):
            structure = replace(self.ctx.structure, tempo_map=[[0.0, 120.0 + delta]])
            self.assertTrue(self.reconcile(replace(self.ctx, structure=structure)).passed)
        structure = replace(self.ctx.structure, tempo_map=[[0.0, 120.0011]])
        with self.assertRaisesRegex(ReconciliationError, 'tempo_map.*bpm'):
            self.reconcile(replace(self.ctx, structure=structure))

    def test_bar_boundary_one_source_tick_inclusive(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        tick = 0.5 / 480
        for offset in (-tick, tick):
            structure = replace(self.ctx.structure, bar_table=[[0, 0.0], [1, 2.0 + offset]])
            report = self.reconcile(replace(self.ctx, structure=structure))
            self.assertTrue(report.passed)
            boundary = next(c for c in report.checks if c.field == 'bar_table[1].time_sec')
            self.assertAlmostEqual(boundary.tolerance, tick)
        structure = replace(self.ctx.structure, bar_table=[[0, 0.0], [1, 2.0 + tick * 1.1]])
        with self.assertRaisesRegex(ReconciliationError, 'bar_table'):
            self.reconcile(replace(self.ctx, structure=structure))

    def test_note_endpoints_and_end_of_track_do_not_use_planner_extent(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        midi = mido.MidiFile(self.ctx.package.midi_path)
        track = midi.tracks[1]
        track[-2].time += 1  # final note-off, one tick inclusive
        midi.save(self.ctx.package.midi_path)
        self.assertTrue(self.reconcile().passed)
        track[-2].time += 1
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, 'note.*end|occupancy'):
            self.reconcile()
        track[-2].time -= 2
        track[-1].time = 1200  # EOT at planner end is not Score occupancy.
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, 'end_of_track'):
            self.reconcile()

    def test_complete_tempo_meter_maps_and_local_tick_tolerance(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        midi = mido.MidiFile(self.ctx.package.midi_path)
        conductor = midi.tracks[0]
        conductor.insert(-1, mido.MetaMessage('set_tempo', tempo=1000000, time=1920))
        conductor.insert(-1, mido.MetaMessage('time_signature', numerator=3, denominator=4, time=0))
        midi.save(self.ctx.package.midi_path)
        structure = replace(
            self.ctx.structure, tempo_map=[[0.0, 120.0], [2.0, 60.0]],
            meter=[[0, '4/4'], [1, '3/4']], bar_table=[[0, 0.0], [1, 2.0], [2, 5.0]],
            sections=[{'bars': [0, 2]}],
        )
        ctx = replace(self.ctx, structure=structure)
        self.assertTrue(self.reconcile(ctx).passed)
        structure = replace(structure, bar_table=[[0, 0.0], [1, 2.0], [2, 5.0 + 1 / 480]])
        self.assertTrue(self.reconcile(replace(ctx, structure=structure)).passed)
        with self.assertRaisesRegex(ReconciliationError, 'tempo_map.*count|meter.*count'):
            self.reconcile()
        structure = replace(ctx.structure, meter=[[0, '4/4'], [1, '6/8']])
        with self.assertRaisesRegex(ReconciliationError, 'meter.*signature'):
            self.reconcile(replace(ctx, structure=structure))

    def test_resolution_and_bar_indices_are_exact(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        midi = mido.MidiFile(self.ctx.package.midi_path)
        midi.ticks_per_beat = 481
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, 'ticks_per_beat'):
            self.reconcile()
        midi.ticks_per_beat = 480
        midi.save(self.ctx.package.midi_path)
        structure = replace(self.ctx.structure, bar_table=[[0, 0.0], [2, 2.0]])
        with self.assertRaisesRegex(ReconciliationError, 'bar.*indices'):
            self.reconcile(replace(self.ctx, structure=structure))

    def test_note_onset_and_change_point_tolerances(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        midi = mido.MidiFile(self.ctx.package.midi_path)
        note_on = next(m for m in midi.tracks[1] if m.type == 'note_on')
        note_on.time += 1
        midi.save(self.ctx.package.midi_path)
        self.assertTrue(self.reconcile().passed)
        note_on.time += 1
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, r'note\[0\].start_sec'):
            self.reconcile()
        note_on.time -= 2
        change = mido.MetaMessage('set_tempo', tempo=1000000, time=480)
        midi.tracks[0].insert(-1, change)
        midi.save(self.ctx.package.midi_path)
        # Move declared change by one source tick, retaining its BPM.
        structure = replace(self.ctx.structure, tempo_map=[[0, 120], [0.5 + 1 / 480, 60]],
                            bar_table=[[0, 0], [1, 3.5]])
        self.assertTrue(self.reconcile(replace(self.ctx, structure=structure)).passed)
        structure = replace(structure, tempo_map=[[0, 120], [0.5 + 2 / 480, 60]])
        with self.assertRaisesRegex(ReconciliationError, r'tempo_map\[1\].time_sec'):
            self.reconcile(replace(self.ctx, structure=structure))

    def test_drum_program_changes_rejected_not_treated_as_pitched_program(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        ctx = self.package(name='drums')
        midi = mido.MidiFile(ctx.package.midi_path)
        midi.tracks[1].insert(1, mido.Message('program_change', program=12, channel=9))
        midi.save(ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, 'drums.*program'):
            self.reconcile(ctx)

    def test_mixed_pitched_and_drum_channels_reject_pitched_metadata(self) -> None:
        from aimusic.audio.analysis.reconcile import ReconciliationError

        ctx = self.package(edo=19)
        midi = mido.MidiFile(ctx.package.midi_path)
        channels = sorted({m.channel for m in midi.tracks[1] if m.type == 'note_on'})
        self.assertEqual(len(channels), 2)
        for msg in midi.tracks[1]:
            if getattr(msg, 'channel', None) == channels[-1]:
                msg.channel = 9
        midi.save(ctx.package.midi_path)
        with self.assertRaisesRegex(ReconciliationError, 'texture.*percussion'):
            self.reconcile(ctx)

    def test_duplicate_tracks_and_ambiguous_channel_ownership_fail(self) -> None:
        from aimusic.core.render_package import ContractViolation

        midi = mido.MidiFile(self.ctx.package.midi_path)
        duplicate = mido.MidiTrack(msg.copy() for msg in midi.tracks[1])
        for msg in duplicate:
            if hasattr(msg, 'channel'):
                msg.channel = 2
        midi.tracks.append(duplicate)
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ContractViolation, 'track_names|source_track_identity'):
            self.reconcile()
        duplicate[0].name = 'other'
        for msg in duplicate:
            if hasattr(msg, 'channel'):
                msg.channel = 1
        midi.save(self.ctx.package.midi_path)
        with self.assertRaisesRegex(ContractViolation, 'channel|pairing'):
            self.reconcile()

    def test_full_pipeline_receives_planner_context_and_warm_corruption_fails(self) -> None:
        import json
        import shutil
        from unittest.mock import patch
        from aimusic.audio.orchestrator import Orchestrator
        from aimusic.core.render_package import ContractViolation

        config = {'stages': {'analysis': False, 'expressivization': True, 'render': True}}
        orch = Orchestrator(config=config, cache_dir=self.root / 'cache')

        def groove(path, spec, *, output_path, **kwargs):
            shutil.copyfile(path, output_path)
            return output_path

        with patch('aimusic.audio.analysis.from_midi.analyze_midi') as infer, patch(
            'aimusic.audio.groove.apply.apply_groove', side_effect=groove,
        ) as apply, patch('aimusic.audio.render.stems.render_role_stems') as render:
            out = orch.run(self.ctx, output_dir=self.root / 'out')
            infer.assert_not_called()
            for stage in (apply, render):
                doc = stage.call_args.kwargs['analysis']
                self.assertEqual(doc['provenance'], 'planner')
                self.assertEqual(doc['tracks'][0]['source_track_index'], 1)
                self.assertEqual(doc['tracks'][0]['role'], 'lead')
                self.assertEqual(doc['tuning'], self.ctx.tuning)
                self.assertEqual(doc['verified_identity'], self.ctx.verified_identity)
            self.assertTrue(json.loads((out / 'score.reconciliation.json').read_text())['passed'])
            self.ctx.package.midi_path.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ContractViolation, 'score.mid.*expected=.*actual='):
                orch.run(self.ctx, output_dir=self.root / 'warm')
            self.assertFalse((self.root / 'warm').exists())
            self.assertEqual(apply.call_count, 1)
            self.assertEqual(render.call_count, 1)

    def test_valid_hashes_do_not_bypass_reconciliation_before_groove(self) -> None:
        from unittest.mock import patch
        from aimusic.audio.analysis.reconcile import ReconciliationError
        from aimusic.audio.orchestrator import Orchestrator

        midi = mido.MidiFile(self.ctx.package.midi_path)
        next(m for m in midi.tracks[1] if m.type == 'program_change').program = 1
        midi.save(self.root / 'mismatch.mid')
        package = write_render_package(
            self.root, score=self.ctx.score, midi_path=self.root / 'mismatch.mid',
            manifest=RunManifest(seed=2, config_dump={}), track_programs={'texture': 0},
        )
        with patch('aimusic.audio.groove.apply.apply_groove') as apply, patch(
            'aimusic.audio.render.stems.render_role_stems'
        ) as render:
            orch = Orchestrator(config={}, cache_dir=self.root / 'cache')
            with self.assertRaisesRegex(ReconciliationError, 'texture.*program'):
                orch.run(from_score(package.root), output_dir=self.root / 'out')
            apply.assert_not_called()
            render.assert_not_called()
            self.assertFalse((self.root / 'out').exists())
