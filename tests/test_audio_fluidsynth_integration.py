"""Opt-in real licensed-asset tests. Missing prerequisites FAIL after opting in."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import tempfile
import time
import unittest

import numpy as np
import soundfile as sf

from aimusic.audio.config import load_audio_config
from aimusic.audio.demo_sources import write_source
from aimusic.audio.from_midi import from_midi
from aimusic.audio.midi_events import MidiEvents
from aimusic.audio.orchestrator import Orchestrator
from aimusic.audio.render.registry import file_digest
from tests.audio_native_support import load_native_manifest, verify_native_runtime


@unittest.skipUnless(os.environ.get('AIMUSIC_RUN_FLUIDSYNTH_TESTS') == '1',
                     'opt in with AIMUSIC_RUN_FLUIDSYNTH_TESTS=1; see docs/audio-testing.md')
class TestFluidSynthIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_native_manifest()
        cls.runtime = verify_native_runtime(cls.manifest)
        parent = os.environ.get('AIMUSIC_FLUIDSYNTH_ARTIFACTS')
        cls.root = Path(tempfile.mkdtemp(prefix='fluidsynth-evidence-', dir=parent))
        print(f'Native evidence (local only): {cls.root}', flush=True)
        (cls.root / 'asset.json').write_text(json.dumps(cls.manifest, indent=2))
        cls.config = load_audio_config('listening').to_dict()
        cls.config.setdefault('paths', {})['soundfont'] = cls.manifest['soundfont_path']
        cls.config['groove'] = {'spec': {}}

    def test_eight_bar_real_instruments_cold_repeat_and_warm_cache(self):
        source = write_source('fixture-12', self.root / 'fixture-12')
        original = source.read_bytes()
        context = from_midi(source)
        expected = [('drums', [9], 0, 96), ('bass', [0], 33, 16),
                    ('comping', [1], 0, 48), ('lead', [2], 80, 64)]
        self.assertEqual([(t['role'], t['channels'], t['program'], t['note_count'])
                          for t in context.analysis_document()['tracks']], expected)
        evidence = {'runtime': self.runtime, 'platform': platform.platform(), 'python': platform.python_version(),
                    'source_sha256': file_digest(source), 'runs': {}, 'signals': {}}
        hashes = []
        for name, cache in [('cold', 'cache-a'), ('cold-repeat', 'cache-b'), ('warm', 'cache-a')]:
            out = self.root / name
            start = time.perf_counter()
            runner = Orchestrator(self.config, self.root / cache)
            runner.run(context, output_dir=out)
            elapsed = time.perf_counter() - start
            evidence['runs'][name] = {'elapsed_sec': elapsed, 'hits': runner.cache_hits, 'misses': runner.cache_misses}
            hashes.append({p.name: file_digest(p) for p in (out / 'stems').glob('*.wav')})
            self.assertEqual(runner.cache_hits.get('render', 0), int(name == 'warm'))
            self.assertEqual(runner.cache_misses.get('render', 0), int(name != 'warm'))
            manifest = json.loads((out / 'render.json').read_text())
            self.assertEqual(manifest['duration_sec'], 16)
            self.assertEqual(manifest['release_tail_sec'], .25)
            for renderer in manifest['renderers']:
                self.assertEqual(renderer['requested_backend'], 'fluidsynth')
                self.assertEqual(renderer['effective_backend'], 'fluidsynth')
                self.assertIsNone(renderer['fallback_reason'])
                self.assertEqual(renderer['soundfont_sha256'], self.manifest['soundfont_sha256'])
                self.assertEqual(dict(renderer['backend']['native_versions']), self.runtime)
            index = json.loads((out / 'stems/role_index.json').read_text())
            self.assertEqual([(r['role'], r['channels'], r['note_count']) for r in index['stems']],
                             [(r, c, n) for r, c, p, n in expected])
            waves = []
            for row, (_, channels, program, count) in zip(index['stems'], expected):
                stem = MidiEvents.load(out / 'stems' / row['midi'])
                self.assertEqual(len(stem.notes), count)
                self.assertEqual({n.channel for n in stem.notes}, set(channels))
                self.assertEqual({e.message.program for e in stem.events if e.message.type == 'program_change'}, {program})
                self.assertEqual([(n.start, n.end, n.pitch, n.velocity) for n in stem.notes],
                                 [(n.start, n.end, n.pitch, n.velocity) for n in context.events.notes
                                  if n.track == row['source_track_index']])
                pcm, rate = sf.read(out / 'stems' / row['wav'], always_2d=True)
                waves.append(pcm.mean(axis=1))
                self.check_signal(pcm, rate)
                evidence['signals'][row['role']] = self.metrics(pcm)
                sidecar = json.loads((out / 'stems' / (row['wav'] + '.json')).read_text())
                self.assertEqual(sidecar['frames'], 780000)
                self.assertEqual(sidecar['soundfont_sha256'], self.manifest['soundfont_sha256'])
            mix, rate = sf.read(out / 'stems/rough_mix.wav')
            self.check_signal(mix, rate)
            evidence['signals']['rough_mix'] = self.metrics(mix)
            total = sum(waves)
            peak = float(np.max(np.abs(total)))
            total *= min(1, .95 / peak)
            # Exact FLOAT result from source stems only, never click.
            np.testing.assert_array_equal(mix, total.astype(np.float32))
            click, rate = sf.read(out / 'stems/click.wav')
            self.assertEqual((len(click), rate), (780000, 48000))
            self.assertGreater(float(np.max(np.abs(click))), 0)
        evidence['wav_hashes'] = hashes
        evidence['cold_repeat_equal'] = hashes[0] == hashes[1]
        evidence['warm_equal'] = hashes[0] == hashes[2]
        (self.root / 'fixture-12-report.json').write_text(json.dumps(evidence, indent=2))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(hashes[0], hashes[1], 'cold-repeat native bytes differ; do not claim determinism')
        self.assertEqual(hashes[0], hashes[2], 'warm-cache bytes differ')
        for run in evidence['runs'].values():
            self.assertLessEqual(run['elapsed_sec'], 60, 'eight-bar ~60s target exceeded; retain measured evidence')

    def check_signal(self, pcm, rate):
        self.assertEqual((len(pcm), rate), (780000, 48000))
        self.assertTrue(np.isfinite(pcm).all())
        metrics = self.metrics(pcm)
        self.assertGreater(metrics['active_rms'], 1e-5)
        self.assertLessEqual(metrics['peak'], .95)

    @staticmethod
    def metrics(pcm):
        return {'active_window_sec': [.1, .4], 'active_rms': float(np.sqrt(np.mean(pcm[4800:19200] ** 2))),
                'peak': float(np.max(np.abs(pcm)))}

    def test_raw_native_channel_pitch_proof_before_capability_promotion(self):
        """Public adapter probe, not a registry capability bypass in production."""
        import io
        import mido
        from aimusic.audio.demo_sources import build_source
        from aimusic.audio.render import fluidsynth_r
        from aimusic.audio.render.registry import RenderRequest
        from aimusic.audio.scoring.tuning_check import ToneWindow, analyze_tuning
        from tests.test_audio_stems import _event_track
        payload, intent = build_source('reference-19')
        source = self.root / 'raw-reference.mid'
        source.write_bytes(payload)
        request = RenderRequest(source, self.root / 'raw-reference.wav', {}, 'channel_pitch',
                                48000, 4.5, .25, Path(self.manifest['soundfont_path']), {})
        fluidsynth_r.render(request)
        pcm, rate = sf.read(request.output_path, always_2d=True)
        report = analyze_tuning(pcm.mean(axis=1), rate, tuple(ToneWindow(**w) for w in intent['windows']))
        (self.root / 'raw-reference-tuning.json').write_text(json.dumps(report.to_dict(), indent=2))
        report.require_pass()

        # Two independently bent voices in ONE Synth at the SAME time. Diagnostic
        # hard-pan CC10 separates mono windows; do not claim polyphonic f0 scoring.
        mono = mido.MidiFile(file=io.BytesIO(payload))
        events = []
        targets = [intent['notes'][i] for i in (0, 2)]
        wheels = [msg.pitch for msg in mono.tracks[1] if msg.type == 'pitchwheel']
        for channel, target, bend, pan in zip((1, 2), targets, (wheels[0], wheels[2]), (0, 127)):
            events.append((0, mido.Message('program_change', channel=channel, program=80)))
            for control, value in ((101, 0), (100, 0), (6, 2), (38, 0), (101, 127), (100, 127), (10, pan)):
                events.append((0, mido.Message('control_change', channel=channel, control=control, value=value)))
            events += [(0, mido.Message('pitchwheel', channel=channel, pitch=bend)),
                       (0, mido.Message('note_on', channel=channel, note=target['midi_note'], velocity=96)),
                       (960, mido.Message('note_off', channel=channel, note=target['midi_note']))]
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        midi.tracks = [_event_track(*sorted(events, key=lambda e: e[0]))]
        midi.save(self.root / 'simultaneous.mid')
        request = RenderRequest(self.root / 'simultaneous.mid', self.root / 'simultaneous.wav', {}, 'channel_pitch',
                                48000, 1, .25, Path(self.manifest['soundfont_path']), {})
        from aimusic.audio.render import registry
        selected = registry.resolve_renderer('lead', config=self.config, soundfont=request.soundfont, tuning='channel_pitch')
        registry.render_request(request, selected)
        pcm, rate = sf.read(request.output_path, always_2d=True)
        reports = []
        for column, target in enumerate(targets):
            report = analyze_tuning(pcm[:, column], rate, (
                ToneWindow(f'channel-{column + 1}', 12000, 36000, target['expected_hz']),))
            reports.append(report.to_dict())
        (self.root / 'simultaneous-tuning.json').write_text(json.dumps(reports, indent=2))
        for row in reports:
            self.assertTrue(row['passed'], row)

    def test_production_19edo_original_groove_and_flattened_same_targets(self):
        from copy import deepcopy
        import mido
        from aimusic.audio.scoring.tuning_check import ToneWindow, TuningCheckError, analyze_tuning
        source = write_source('reference-19', self.root / 'reference-19')
        expected_path = source.parent / 'expected.json'
        intent = json.loads(expected_path.read_text())
        summary = {'expected_sha256': file_digest(expected_path), 'reports': {}}
        dry_hash = None
        for name, offset, flat in [('original', 0, False), ('groove', 600, False),
                                   ('flat', 0, True), ('warm-reference', 0, False)]:
            config = deepcopy(self.config)
            config['stages']['expressivization'] = bool(offset)
            config['groove'] = {'spec': {'microtiming_ms': {'comp': {'global_offset': 12.5}}}}
            path = source
            if flat:
                midi = mido.MidiFile(source)
                for track in midi.tracks:
                    for msg in track:
                        if msg.type == 'pitchwheel':
                            msg.pitch = 0
                path = self.root / 'flattened.mid'
                midi.save(path)
            out = self.root / name
            runner = Orchestrator(config, self.root / 'reference-cache')
            runner.run(from_midi(path), output_dir=out)
            manifest = json.loads((out / 'render.json').read_text())
            cache_manifest = json.loads((out / 'stems/cache_manifest.json').read_text())
            self.assertEqual(cache_manifest['fingerprint'], manifest)
            choice = manifest['renderers'][0]
            self.assertEqual(choice['effective_backend'], 'fluidsynth')
            self.assertIsNone(choice['fallback_reason'])
            self.assertEqual(choice['soundfont_sha256'], self.manifest['soundfont_sha256'])
            self.assertEqual(dict(choice['backend']['native_versions']), self.runtime)
            index = json.loads((out / 'stems/role_index.json').read_text())
            self.assertEqual(len(index['stems']), 1)
            wav = out / 'stems' / index['stems'][0]['wav']
            pcm, rate = sf.read(wav, always_2d=True)
            self.assertEqual((len(pcm), rate), (228000, 48000))
            self.assertTrue(np.isfinite(pcm).all())
            self.assertLessEqual(float(np.max(np.abs(pcm))), .95)
            windows = tuple(ToneWindow(**{**w, 'start_sample': w['start_sample'] + offset,
                                         'stop_sample': w['stop_sample'] + offset}) for w in intent['windows'])
            report = analyze_tuning(pcm.mean(axis=1), rate, windows)
            (out / 'tuning-check.json').write_text(json.dumps(report.to_dict(), indent=2))
            summary['reports'][name] = report.to_dict()
            if flat:
                with self.assertRaises(TuningCheckError):
                    report.require_pass()
                # Aggregate floors must fail too, not only the five-cent gate.
                general = analyze_tuning(pcm.mean(axis=1), rate, windows, controlled=False)
                with self.assertRaises(TuningCheckError):
                    general.require_pass()
                (out / 'tuning-check-general.json').write_text(json.dumps(general.to_dict(), indent=2))
            else:
                report.require_pass()
                self.assertEqual(choice['required_tuning'], 'channel_pitch')
                evidence = choice['tuning_evidence']
                self.assertEqual([n['tick'] for n in evidence['notes']],
                                 [n['start_tick'] + offset // 50 for n in intent['notes']])
                sidecar = json.loads(Path(str(wav) + '.json').read_text())
                self.assertEqual(sidecar['required_tuning'], 'channel_pitch')
                self.assertEqual(sidecar['tuning']['event_evidence'], evidence)
                self.assertEqual(sidecar['tuning']['metadata'], manifest['analysis']['tuning'])
                self.assertTrue(sidecar['tuning']['metadata']['events'])
            if name == 'original':
                dry_hash = file_digest(wav)
            self.assertEqual(runner.cache_hits.get('render', 0), int(name == 'warm-reference'))
            if name == 'warm-reference':
                self.assertEqual(file_digest(wav), dry_hash)
        (self.root / 'reference-19-report.json').write_text(json.dumps(summary, indent=2))

    def test_mts_and_cross_track_ownership_never_publish_success(self):
        import io
        import mido
        from aimusic.audio.demo_sources import build_source
        payload, _ = build_source('reference-19')
        for name in ('mts', 'ambiguous'):
            midi = mido.MidiFile(file=io.BytesIO(payload))
            if name == 'mts':
                midi.tracks[0].insert(0, mido.Message('sysex', data=(0x7e, 127, 8, 2)))
            else:
                midi.tracks.append(midi.tracks[1].copy())
            source = self.root / (name + '.mid')
            midi.save(source)
            out = self.root / (name + '-rejected')
            runner = Orchestrator(self.config, self.root / (name + '-cache'))
            with self.assertRaisesRegex(ValueError, 'mts|channel ownership'):
                runner.run(from_midi(source), output_dir=out)
            self.assertFalse((out / 'render.json').exists())
            self.assertFalse(list(out.rglob('*.wav')))
            self.assertFalse(list((runner.cache_dir / 'render').rglob('cache_manifest.json')))
