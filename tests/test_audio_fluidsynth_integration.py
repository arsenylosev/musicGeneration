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
