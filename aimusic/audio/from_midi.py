"""Read-only imported-MIDI admission; no Score, planner claims or DSP imports."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from aimusic.audio.analysis.from_midi import infer_role
from aimusic.audio.midi_events import MidiEvents, MidiEventError


@dataclass(frozen=True)
class ImportedMidiContext:
    midi_path: Path
    source_sha256: str
    events: MidiEvents
    provenance: str = 'inferred'

    @property
    def run_id(self) -> str:
        return f'imported_{self.source_sha256[:16]}'

    @property
    def verified_identity(self) -> dict[str, str]:
        return {'midi_sha256': self.source_sha256, 'input_kind': 'imported_midi'}

    @property
    def reconciliation(self) -> dict[str, str]:
        return {'status': 'not_applicable', 'reason': 'Imported MIDI has no planner/Score metadata'}

    def analysis_document(self) -> dict[str, Any]:
        source = self.events
        tempo = dict([(0, 500000), *source.tempo_map.changes])
        meters = {0: (4, 4)}
        evidence = []
        for event in source.events:
            msg = event.message
            if msg.type == 'time_signature':
                meters[event.tick] = (msg.numerator, msg.denominator)
            if msg.type in ('pitchwheel', 'sysex') or (msg.type == 'control_change' and
                    msg.control in (6, 38, 96, 97, 98, 99, 100, 101, 121)):
                evidence.append({'tick': event.tick, 'track': event.track, 'message': msg.dict()})
        end = max(e.tick for e in source.events)
        return {
            'schema': 'aimusic.imported-midi/1', 'provenance': self.provenance,
            'origin': 'inferred', 'source_midi': self.midi_path.name,
            'midi_type': source.type, 'resolution': source.ticks_per_beat,
            'verified_identity': self.verified_identity, 'inputs_hash': self.source_sha256,
            'planner_reconciliation': self.reconciliation,
            'tempo_bpm': 60000000 / tempo[0],
            'tempo_map': [[float(source.tempo_map.seconds(t)), 60000000 / us] for t, us in tempo.items()],
            'meter_map': [[t, n, d] for t, (n, d) in meters.items()],
            'time_signature': dict(zip(('numerator', 'denominator'), meters[0])),
            'duration_sec': float(source.tempo_map.seconds(end)),
            'bar_boundaries_sec': _bar_boundaries(source, meters, end),
            'tracks': _tracks(source), 'total_notes': len(source.notes),
            'tuning': {'source': 'MIDI events', 'intended_lattice': 'unknown',
                       'audio_pitch_accuracy': 'unmeasured', 'events': evidence},
        }


def _tracks(source: MidiEvents) -> list[dict[str, Any]]:
    rows = []
    for index, isolated in source.isolate_tracks().items():
        name = next((e.message.name for e in source.events
                     if e.track == index and e.message.type == 'track_name'), '')
        channels = sorted({n.channel for n in isolated.notes})
        current = {ch: 0 for ch in channels}
        used: set[int] = set()
        changes = []
        for event in isolated.events:
            msg = event.message
            if event.channel not in current:
                continue
            if msg.type == 'program_change':
                current[msg.channel] = msg.program
                changes.append({'tick': event.tick, 'channel': msg.channel, 'program': msg.program})
            if msg.type == 'note_on' and msg.velocity:
                used.add(current[msg.channel])
        programs = sorted(used)
        percussion = channels == [9]
        roles = {infer_role(name, p, percussion) for p in programs}
        role = next(iter(roles)) if len(roles) == 1 and (9 not in channels or percussion) else 'mixed'
        rows.append({'source_track_index': index, 'name': name, 'channels': channels,
                     'role': role, 'role_source': 'inferred', 'count_source': 'MIDI events',
                     'program': programs[0] if len(programs) == 1 else None,
                     'programs': programs, 'program_changes': changes, 'is_drum': percussion,
                     'note_count': len(isolated.notes)})
    return rows


def _bar_boundaries(source: MidiEvents, meters: dict[int, tuple[int, int]], end: int) -> list[float]:
    """A meter change starts a new inferred bar, including mid-bar changes."""
    ticks = [Fraction(0)]
    changes = list(meters.items())
    for i, (start, (numerator, denominator)) in enumerate(changes):
        stop = min(end, changes[i + 1][0]) if i + 1 < len(changes) else end
        at = Fraction(start)
        if at > ticks[-1]:
            ticks.append(at)
        step = Fraction(source.ticks_per_beat * numerator * 4, denominator)
        while at + step < stop:
            at += step
            ticks.append(at)
    if end > ticks[-1]:
        ticks.append(Fraction(end))
    return [float(source.tempo_map.seconds(t)) for t in ticks]


def from_midi(midi_path: Path | str) -> ImportedMidiContext:
    """Admit one byte snapshot; the orchestrator independently rechecks its identity."""
    path = Path(midi_path)
    if not path.is_file():
        raise FileNotFoundError(f'MIDI file not found: {path}')
    payload = path.read_bytes()
    try:
        events = MidiEvents.from_bytes(payload)
    except (EOFError, ValueError, KeyError, IndexError, OSError) as exc:
        raise MidiEventError(f'Malformed MIDI or unsupported input {path}: {exc}') from exc
    if not events.notes:
        raise MidiEventError('No paired notes in imported MIDI; provide a nonempty type-0/type-1 source')
    for event in events.events:
        if event.message.type == 'time_signature' and event.message.numerator <= 0:
            raise MidiEventError(f'Invalid meter at tick {event.tick}: numerator must be positive')
    events.isolate_tracks()  # Reject cross-track controllers that corrupt another instrument's channel.
    return ImportedMidiContext(path, hashlib.sha256(payload).hexdigest(), events)
