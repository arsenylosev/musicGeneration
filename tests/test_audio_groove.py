"""Unit tests for groove apply (M1 PR3)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "audio"
QUANTIZED = FIXTURES / "eight_bar_quantized.mid"
EXPRESSIVE = FIXTURES / "already_expressive.mid"


def _audio_spine_available() -> bool:
    try:
        import pretty_midi  # noqa: F401
        import pydantic  # noqa: F401
        import yaml  # noqa: F401
        return QUANTIZED.is_file()
    except ImportError:
        return False


@unittest.skipUnless(_audio_spine_available(), "requires pip install -e '.[audio]' + fixtures")
class TestAudioGroove(unittest.TestCase):
    def test_note_count_preserved_without_ghosts(self) -> None:
        from aimusic.audio.groove.apply import apply_groove

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "g.mid"
            apply_groove(
                QUANTIZED,
                {
                    "swing": {
                        "ratio": 0.66,
                        "subdivision": 8,
                        "applies_to": ["drums", "bass", "comp"],
                    }
                },
                output_path=out,
            )
            report = json.loads(Path(str(out) + ".diff.json").read_text(encoding="utf-8"))
            self.assertEqual(report["ghost_inserted"], 0)
            self.assertEqual(report["notes_in"], report["notes_out"])

    def test_velocity_map_idempotent_at_depth_zero(self) -> None:
        import pretty_midi as pm
        from aimusic.audio.groove.apply import apply_groove

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "light.mid"
            apply_groove(
                QUANTIZED,
                {"velocity": {"accent_map_16": [1.2, 0.8] * 8}},
                output_path=out,
                light_cleanup=True,
                config={"code_version": "0.1.0"},
            )
            src = pm.PrettyMIDI(str(QUANTIZED))
            dst = pm.PrettyMIDI(str(out))
            self.assertEqual(
                sum(len(i.notes) for i in src.instruments),
                sum(len(i.notes) for i in dst.instruments),
            )
            for si, di in zip(src.instruments, dst.instruments, strict=True):
                for sn, dn in zip(si.notes, di.notes, strict=True):
                    self.assertEqual(sn.pitch, dn.pitch)
                    self.assertEqual(sn.velocity, dn.velocity)

    def test_already_expressive_nearly_untouched(self) -> None:
        from aimusic.audio.groove.apply import apply_groove

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ex.mid"
            apply_groove(
                EXPRESSIVE,
                {"swing": {"ratio": 0.55, "subdivision": 8}},
                output_path=out,
                light_cleanup=True,
                config={"groove": {"light_cleanup": True}},
            )
            report = json.loads(Path(str(out) + ".diff.json").read_text(encoding="utf-8"))
            self.assertLess(report["mean_abs_offset_ms"], 0.1)
            self.assertEqual(report["notes_in"], report["notes_out"])

    def test_invalid_spec_rejected(self) -> None:
        from pydantic import ValidationError

        from aimusic.audio.groove.apply import apply_groove

        with self.assertRaises(ValidationError):
            apply_groove(QUANTIZED, {"swing": {"ratio": 0.9, "subdivision": 8}})

    def test_swing_offsets_within_one_tick(self) -> None:
        import pretty_midi as pm

        from aimusic.audio.groove.apply import (
            _apply_to_midi,
            _seconds_per_beat,
            _swing_offset_sec,
        )
        from aimusic.audio.groove.spec import parse_groove_spec

        spec = parse_groove_spec(
            {
                "swing": {
                    "ratio": 0.66,
                    "subdivision": 8,
                    "applies_to": ["drums", "bass", "comp"],
                }
            }
        )
        src = pm.PrettyMIDI(str(QUANTIZED))
        spb = _seconds_per_beat(src)
        sec_per_tick = spb / float(src.resolution)
        _out, report = _apply_to_midi(src, spec, light_cleanup=False)
        for d, n_role in zip(
            report["note_diffs"],
            [
                (
                    "drums"
                    if (inst.is_drum or "drums" in (inst.name or "").lower())
                    else (
                        "bass"
                        if "bass" in (inst.name or "").lower()
                        else (
                            "lead"
                            if "lead" in (inst.name or "").lower()
                            else "comp"
                        )
                    )
                )
                for inst in src.instruments
                for _ in inst.notes
            ],
            strict=True,
        ):
            self.assertEqual(d["role"], n_role)
        # Spot-check first drum note offset matches swing helper within 1 tick
        first = src.instruments[0].notes[0]
        expected = _swing_offset_sec(first.start, spb, 0.66, 8)
        self.assertLessEqual(
            abs(report["note_diffs"][0]["offset_sec"] - expected),
            sec_per_tick + 1e-9,
        )


@unittest.skipUnless(_audio_spine_available(), "requires audio extras")
class TestEventGroove(unittest.TestCase):
    def apply(self, midi, spec, **kwargs):
        from aimusic.audio.groove.apply import apply_groove
        from aimusic.audio.midi_events import MidiEvents
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source, out = Path(tmp.name) / 'source.mid', Path(tmp.name) / 'out.mid'
        midi.save(source)
        original = source.read_bytes()
        apply_groove(source, spec, output_path=out, **kwargs)
        self.assertEqual(source.read_bytes(), original)
        return MidiEvents.load(out), json.loads(Path(str(out) + '.diff.json').read_text())

    def test_public_path_never_uses_pretty_midi_and_preserves_global_payloads(self):
        from unittest.mock import patch
        from tests.test_audio_midi_events import fixture, signature
        source = fixture()
        with patch('pretty_midi.PrettyMIDI', side_effect=AssertionError('lossy authority')):
            out, report = self.apply(source, {}, light_cleanup=True)
        self.assertEqual(signature(source), signature(out.to_mido()))
        self.assertEqual(report['notes_in'], report['notes_out'])

    def test_tempo_map_changes_grid_and_round_half_even_offsets(self):
        import mido
        from tests.test_audio_midi_events import track
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        midi.tracks = [track(
            (0, mido.MetaMessage('track_name', name='lead')),
            (0, mido.MetaMessage('set_tempo', tempo=500000)),
            (480, mido.MetaMessage('set_tempo', tempo=1000000)),
            (721, mido.Message('note_on', note=60, velocity=80)),
            (800, mido.Message('note_off', note=60)),
            (1200, mido.MetaMessage('end_of_track')))]
        out, report = self.apply(midi, {'microtiming_ms': {'lead': {'global_offset': 3.125}},
                                       'velocity': {'accent_map_16': [1, 1, 1, 1, 1, 1, .5, 1]}})
        self.assertEqual(out.notes[0].start, 723)  # 1.5 ticks -> +2, not round(722.5)
        self.assertEqual(out.notes[0].end, 802)
        self.assertEqual(out.notes[0].velocity, 40)  # beat grid, not initial-tempo seconds
        self.assertEqual(report['note_diffs'][0]['offset_ticks'], 2)
        self.assertEqual(out.tempo_map.changes, ((0, 500000), (480, 1000000)))

    def test_expression_and_note_endpoints_share_seconds_offset_across_tempo_change(self):
        from tests.test_audio_midi_events import fixture
        out, _ = self.apply(fixture(), {'microtiming_ms': {'lead': {'global_offset': 50}}})
        self.assertEqual((out.notes[0].start, out.notes[0].end), (148, 524))
        self.assertEqual(next(e.tick for e in out.events if e.message.type == 'aftertouch'), 198)
        self.assertEqual(next(e.tick for e in out.events if e.message.type == 'polytouch'), 223)
        self.assertEqual([e.tick for e in out.events if e.message.type == 'pitchwheel'], [148, 624])

    def test_ghosts_count_velocity_and_ambiguous_overlap_rejection(self):
        from tests.test_audio_midi_events import channel_fixture, message
        from aimusic.audio.midi_events import ChannelStateConflict
        spec = {'velocity': {'ghost_notes': {'lead': {'prob': 1, 'vel_scale': .5}}}}
        midi = channel_fixture((100, message('note_on', note=60, velocity=80)),
                               (130, message('note_off', note=60)))
        out, report = self.apply(midi, spec)
        self.assertEqual((report['notes_in'], report['notes_out'], report['ghost_inserted']), (1, 2, 1))
        self.assertEqual([n.velocity for n in out.notes], [80, 40])
        midi = channel_fixture((100, message('note_on', note=60)),
                               (400, message('note_off', note=60)))
        with self.assertRaisesRegex(ChannelStateConflict, 'ghost.*overlap'):
            self.apply(midi, spec)

    def test_input_cannot_be_overwritten_via_output_alias(self):
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.mid'
            fixture().save(path)
            original = path.read_bytes()
            alias = Path(tmp) / 'alias.mid'
            alias.hardlink_to(path)
            for out in (path, alias):
                with self.assertRaisesRegex(ValueError, 'source|input'):
                    apply_groove(path, {}, output_path=out)
                self.assertEqual(path.read_bytes(), original)

    def test_generated_multichannel_19edo_fixture_has_independent_pitch_evidence(self):
        import mido
        from aimusic.core.config import EDOConfig
        from aimusic.core.core_types import NoteEvent, Score
        from aimusic.render import render_midi
        from aimusic.theory.edo import EDO
        config = EDOConfig(n=19, base_tuning=60.125, pitch_bend_range=48)
        score = Score(note_events=(
            NoteEvent(ton=240, toff=480, h=1, v=.8, track='lead'),
            NoteEvent(ton=240, toff=480, h=5, v=.7, track='lead'),
            NoteEvent(ton=720, toff=960, h=8, v=.6, track='lead'),
        ))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'generated.mid'
            render_midi(score, EDO(config), str(path))
            out, report = self.apply(mido.MidiFile(path), {
                'microtiming_ms': {'lead': {'global_offset': 25}},
                'articulation': {'lead': {'duration_scale': .9}},
            })
        self.assertEqual((report['notes_in'], report['notes_out']), (3, 3))
        self.assertGreater(len({n.channel for n in out.notes}), 1)
        bends, ranges, selectors, observed = {}, {}, {}, []
        for e in out.events:
            msg = e.message
            if msg.type == 'control_change':
                selector = selectors.setdefault(msg.channel, [127, 127])
                if msg.control in (101, 100):
                    selector[msg.control == 100] = msg.value
                if msg.control == 6 and selector == [0, 0]:
                    ranges[msg.channel] = msg.value
            if msg.type == 'pitchwheel':
                bends[msg.channel] = msg.pitch
            if msg.type == 'note_on' and msg.velocity:
                observed.append(msg.note + bends.get(msg.channel, 0) / 8192 * ranges.get(msg.channel, 2))
        intended = [config.base_tuning + n.h * 12 / 19 for n in score.note_events]
        for expected, actual in zip(intended, observed, strict=True):
            self.assertLessEqual(abs(expected - actual) * 100, 1)


@unittest.skipUnless(_audio_spine_available(), "requires audio extras")
class TestReviewPublication(unittest.TestCase):
    def test_all_output_aliases_rejected_before_any_publication(self):
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        for suffix in ('', '.diff.json', '.json'):
            for link in ('symlink_to', 'hardlink_to'):
                with self.subTest(suffix=suffix, link=link), tempfile.TemporaryDirectory() as tmp:
                    source, out = Path(tmp) / 'source.mid', Path(tmp) / 'out.mid'
                    fixture().save(source)
                    original = source.read_bytes()
                    destinations = [Path(str(out) + ext) for ext in ('', '.diff.json', '.json')]
                    for destination in destinations:
                        if destination == Path(str(out) + suffix):
                            getattr(destination, link)(source)
                        else:
                            destination.write_bytes(b'previous artifact')
                    before = [p.read_bytes() for p in destinations]
                    with self.assertRaisesRegex(ValueError, 'source|input'):
                        apply_groove(source, {}, output_path=out)
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual([p.read_bytes() for p in destinations], before)

    def test_destinations_must_not_alias_each_other(self):
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'source.mid', Path(tmp) / 'out.mid'
            fixture().save(source)
            out.write_bytes(b'old midi')
            Path(str(out) + '.json').hardlink_to(out)
            with self.assertRaisesRegex(ValueError, 'alias'):
                apply_groove(source, {}, output_path=out)
            self.assertEqual(out.read_bytes(), b'old midi')

    def test_serialization_failure_does_not_publish_midi_or_sidecars(self):
        from unittest.mock import patch
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'source.mid', Path(tmp) / 'out.mid'
            fixture().save(source)
            original = source.read_bytes()
            with patch('aimusic.audio.groove.apply.json.dumps', side_effect=OSError('serialize failed')):
                with self.assertRaisesRegex(OSError, 'serialize failed'):
                    apply_groove(source, {}, output_path=out)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(Path(tmp).iterdir()), [source])

    def test_publication_failure_rolls_back_existing_bundle(self):
        import os
        from unittest.mock import patch
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                source, out = Path(tmp) / 'source.mid', Path(tmp) / 'out.mid'
                fixture().save(source)
                original = source.read_bytes()
                destinations = [Path(str(out) + ext) for ext in ('', '.diff.json', '.json')]
                if existing:
                    for destination in destinations:
                        destination.write_bytes(b'previous artifact')
                replace = os.replace
                failed = False

                def fail_manifest(src, dst, **kwargs):
                    nonlocal failed
                    if Path(dst).name == out.name + '.json' and not failed:
                        failed = True
                        raise OSError('publish failed')
                    return replace(src, dst, **kwargs)

                with patch('os.replace', side_effect=fail_manifest):
                    with self.assertRaisesRegex(OSError, 'publish failed'):
                        apply_groove(source, {}, output_path=out)
                self.assertTrue(failed)
                self.assertEqual(source.read_bytes(), original)
                for destination in destinations:
                    if existing:
                        self.assertEqual(destination.read_bytes(), b'previous artifact')
                    else:
                        self.assertFalse(destination.exists())
                self.assertEqual(len(list(Path(tmp).iterdir())), 4 if existing else 1)

    def test_replace_never_follows_a_late_source_link(self):
        import os
        from unittest.mock import patch
        from aimusic.audio.groove.apply import apply_groove
        from tests.test_audio_midi_events import fixture
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp) / 'source.mid', Path(tmp) / 'out.mid'
            fixture().save(source)
            original = source.read_bytes()
            replace = os.replace

            def link_then_replace(src, dst, **kwargs):
                if Path(dst).name == out.name + '.json':
                    Path(str(out) + '.json').symlink_to(source)
                return replace(src, dst, **kwargs)

            with patch('os.replace', side_effect=link_then_replace):
                apply_groove(source, {}, output_path=out)
            self.assertEqual(source.read_bytes(), original)
            manifest = json.loads(Path(str(out) + '.json').read_text())
            self.assertEqual(manifest['output_path'], str(out))
            self.assertEqual(manifest['metadata']['diff_report'], str(out) + '.diff.json')
            self.assertFalse(Path(str(out) + '.json').is_symlink())

    def test_meter_anticipation_uses_indexed_event_visits(self):
        from dataclasses import replace
        from aimusic.audio.groove.apply import _apply_to_events
        from aimusic.audio.groove.spec import parse_groove_spec
        from aimusic.audio.midi_events import MidiEvents
        from tests.test_audio_midi_scaling import representative
        visits = []
        for size in (2000, 4000):
            count = 0

            class CountedEvents(tuple):
                def __iter__(self):
                    nonlocal count
                    for event in super().__iter__():
                        count += 1
                        yield event

            source = MidiEvents.from_mido(representative(size))
            source = replace(source, events=CountedEvents(source.events))
            out, _ = _apply_to_events(source, parse_groove_spec(
                {'anticipation': {'comp': {'prob': 1, 'push_ms': [1, 1]}}}))
            self.assertEqual(len(out.notes), size // 4)
            visits.append(count)
        self.assertLessEqual(visits[1], visits[0] * 2.7, str(visits))

    def test_rational_swing_half_even_at_low_ppqn(self):
        import mido
        from tests.test_audio_midi_events import track
        from aimusic.audio.groove.apply import _apply_to_events
        from aimusic.audio.groove.spec import parse_groove_spec
        from aimusic.audio.midi_events import MidiEvents
        midi = mido.MidiFile(type=0, ticks_per_beat=15)
        midi.tracks = [track((8, mido.Message('note_on', note=60)),
                             (13, mido.Message('note_off', note=60)))]
        out, report = _apply_to_events(MidiEvents.from_mido(midi), parse_groove_spec(
            {'swing': {'ratio': .6, 'subdivision': 8, 'applies_to': ['comp']}}))
        self.assertEqual((out.notes[0].start, out.notes[0].end), (10, 15))
        self.assertEqual(report['note_diffs'][0]['offset_ticks'], 2)


if __name__ == "__main__":
    unittest.main()
