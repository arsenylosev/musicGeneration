"""Run-wide publication and consumed-input race regressions."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_audio_stems import _audio_render_available, _make_two_pianos_one_bass_midi


@unittest.skipUnless(_audio_render_available(), "requires pip install -e '.[audio]'")
class TestRunPublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = _make_two_pianos_one_bass_midi(self.root / 'source.mid')
        self.config = {'groove': {'spec': {}}, 'render': {'sample_rate': 8000}}

    def run_audio(self, out='out'):
        from aimusic.audio.orchestrator import Orchestrator
        return Orchestrator(self.config, self.root / 'cache').run(self.source, output_dir=self.root / out)

    def test_every_collision_is_rejected_before_any_run_artifact_is_written(self):
        names = ['source.analysis.json', 'source.analysis.json.json', 'source.expressive.mid',
                 'source.expressive.mid.diff.json', 'source.expressive.mid.json', 'render.json',
                 'stems/track_0001_piano.mid', 'stems/rough_mix.wav', 'stems/cache_manifest.json']
        for name in names:
            for kind in ('file', 'symlink', 'source-alias'):
                with self.subTest(name=name, kind=kind):
                    out = self.root / f'collision-{names.index(name)}-{kind}'
                    target = out / name
                    target.parent.mkdir(parents=True)
                    outside = self.root / 'outside'
                    outside.write_bytes(b'KEEP')
                    original = self.source.read_bytes()
                    if kind == 'file':
                        target.write_bytes(b'KEEP')
                    elif kind == 'symlink':
                        target.symlink_to(outside)
                    else:
                        target.hardlink_to(self.source)
                    with self.assertRaises(FileExistsError):
                        self.run_audio(out.name)
                    self.assertEqual(outside.read_bytes(), b'KEEP')
                    self.assertEqual(self.source.read_bytes(), original)
                    self.assertEqual([p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()], [name])

    def test_output_symlink_is_rejected_before_upstream_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'out').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_audio()
        self.assertEqual(list(outside.iterdir()), [])

    def test_upstream_restore_checks_copied_bytes_and_expected_fingerprint(self):
        from aimusic.audio.cache import cached_stage
        self.run_audio('cold')
        real_copy = shutil.copyfile
        for stage, name in (('analysis', 'analysis.json.json'), ('expressivization', 'expressive.mid.diff.json')):
            for change in ('corrupt', 'valid-substitution'):
                with self.subTest(stage=stage, change=change):
                    cache = next(p for p in (self.root / 'cache' / stage).iterdir() if not p.name.startswith('.'))
                    names = list(json.loads((cache / 'cache_manifest.json').read_text())['outputs'])

                    def produce(directory):
                        for artifact in names:
                            (directory / artifact).write_bytes((cache / artifact).read_bytes())
                    other, _ = cached_stage(self.root / 'other-cache', {'different': stage}, names, produce)

                    def copying(src, dst, *args, **kwargs):
                        src = Path(src)
                        if src.parent == cache:
                            if change == 'valid-substitution':
                                src = other / src.name
                            elif src.name == name:
                                Path(dst).write_bytes(b'CORRUPT')
                                return dst
                        return real_copy(src, dst, *args, **kwargs)

                    out = f'restore-{stage}-{change}'
                    with patch('shutil.copyfile', side_effect=copying), self.assertRaisesRegex(ValueError, 'cache changed'):
                        self.run_audio(out)
                    self.assertEqual([p for p in (self.root / out).rglob('*') if p.is_file()], [])

    def test_late_collision_rolls_back_reports_and_keeps_the_other_writer(self):
        import os
        self.run_audio('cold')
        out = self.root / 'out'
        real_link = os.link
        collided = False

        def linking(src, dst, **kwargs):
            nonlocal collided
            if Path(dst).name == 'rough_mix.wav' and kwargs.get('dst_dir_fd') is not None:
                # Only race publication into the user tree, not private staging.
                if (out / 'source.analysis.json').exists():
                    collided = True
                    (out / 'stems' / 'rough_mix.wav').symlink_to(self.source)
            return real_link(src, dst, **kwargs)

        original = self.source.read_bytes()
        with patch('os.link', side_effect=linking), self.assertRaises(FileExistsError):
            self.run_audio()
        self.assertTrue(collided)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual([p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()],
                         ['stems/rough_mix.wav'])

    def test_package_reports_refuse_collisions_before_writing_other_reports(self):
        from tests.test_audio_reconcile import TestAudioReconcile
        from aimusic.audio.orchestrator import Orchestrator
        fixture = TestAudioReconcile()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for name in ('score.analysis.json', 'score.reconciliation.json'):
            with self.subTest(name=name):
                out = self.root / name
                out.mkdir()
                (out / name).symlink_to(self.source)
                original = self.source.read_bytes()
                with self.assertRaises(FileExistsError):
                    Orchestrator({'stages': {'render': False, 'expressivization': False}}, self.root / 'cache').run(
                        fixture.ctx, output_dir=out)
                self.assertEqual(self.source.read_bytes(), original)
                self.assertEqual([p.name for p in out.iterdir()], [name])
