"""Audio-owned, immutable SMF events. No note-only serialization or DSP.

Times are absolute source ticks; message payloads are frozen and time-free.
Equal-tick order is (source track, source order). Type 2, SMPTE, multiple
ports and ambiguous note/channel ownership are deliberately unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

import mido
from mido.frozen import freeze_message


class MidiEventError(ValueError):
    """Malformed or ambiguous source MIDI."""


class ChannelStateConflict(MidiEventError):
    """Retiming cannot preserve the source channel state."""


def rational(value: float | int | Fraction) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(str(value))


@dataclass(frozen=True)
class MidiEvent:
    tick: int
    track: int
    order: int
    message: Any
    note_ids: tuple[int, ...] = ()
    sort_order: Fraction | None = None

    @property
    def channel(self) -> int | None:
        return getattr(self.message, 'channel', None)

    @property
    def key(self) -> tuple[int, int, Fraction]:
        return self.tick, self.track, self.sort_order if self.sort_order is not None else Fraction(self.order)


@dataclass(frozen=True)
class MidiNote:
    id: int
    track: int
    channel: int
    pitch: int
    velocity: int
    start: int
    end: int
    sounding_end: int
    on: int
    off: int


@dataclass(frozen=True)
class TempoMap:
    ticks_per_beat: int
    changes: tuple[tuple[int, int], ...]

    def seconds(self, tick: int | Fraction) -> Fraction:
        elapsed = Fraction(0)
        previous, tempo = 0, 500000
        for at, new_tempo in self.changes:
            if at > tick:
                break
            elapsed += Fraction((at - previous) * tempo, 1000000 * self.ticks_per_beat)
            previous, tempo = at, new_tempo
        return elapsed + (tick - previous) * Fraction(tempo, 1000000 * self.ticks_per_beat)

    def tick(self, seconds: float | Fraction) -> Fraction:
        remaining = rational(seconds)
        previous, tempo = 0, 500000
        for at, new_tempo in self.changes:
            span = Fraction((at - previous) * tempo, 1000000 * self.ticks_per_beat)
            if remaining < span:
                break
            remaining -= span
            previous, tempo = at, new_tempo
        return previous + remaining * Fraction(1000000 * self.ticks_per_beat, tempo)

    def shift(self, tick: int, seconds: float | Fraction) -> int:
        target = self.tick(max(Fraction(0), self.seconds(tick) + rational(seconds)))
        return tick + round(target - tick)


def is_expression(message: Any) -> bool:
    return message.type in ('pitchwheel', 'aftertouch', 'polytouch') or (
        message.type == 'control_change' and message.control == 74
    )


@dataclass(frozen=True)
class MidiEvents:
    type: int
    ticks_per_beat: int
    track_count: int
    charset: str
    events: tuple[MidiEvent, ...]
    notes: tuple[MidiNote, ...]

    @classmethod
    def load(cls, path: Path | str) -> MidiEvents:
        try:
            midi = mido.MidiFile(str(path))
        except (OSError, EOFError, ValueError, KeyError) as exc:
            raise MidiEventError(f'Malformed MIDI {path}: {exc}') from exc
        return cls.from_mido(midi)

    @classmethod
    def from_mido(cls, midi: Any) -> MidiEvents:
        if midi.type not in (0, 1):
            raise MidiEventError(f'Unsupported MIDI type {midi.type}; use type 0 or 1')
        if midi.ticks_per_beat <= 0:
            raise MidiEventError('SMPTE/invalid tick division is unsupported; use PPQN')
        if not midi.tracks or (midi.type == 0 and len(midi.tracks) != 1):
            raise MidiEventError('Invalid track count for MIDI type')
        events = []
        globals_seen: dict[tuple[int, str], Any] = {}
        for track_index, tr in enumerate(midi.tracks):
            tick, ended = 0, False
            for order, msg in enumerate(tr):
                if ended:
                    raise MidiEventError(f'track {track_index}: event after end_of_track')
                if not isinstance(msg.time, int) or msg.time < 0:
                    raise MidiEventError(f'track {track_index}: invalid delta time {msg.time}')
                tick += msg.time
                if msg.type == 'midi_port' and msg.port != 0:
                    raise MidiEventError('Multiple MIDI ports are unsupported')
                if msg.type in ('set_tempo', 'time_signature'):
                    key = tick, msg.type
                    payload = msg.copy(time=0)
                    if key in globals_seen and globals_seen[key] != payload:
                        raise MidiEventError(f'Conflicting {msg.type} at tick {tick}')
                    if msg.type == 'set_tempo' and msg.tempo <= 0:
                        raise MidiEventError('Invalid tempo')
                    globals_seen[key] = payload
                events.append(MidiEvent(tick, track_index, order, freeze_message(msg.copy(time=0))))
                ended = msg.type == 'end_of_track'
        events.sort(key=lambda e: e.key)
        active: dict[tuple[int, int], int] = {}
        owners: dict[int, int] = {}
        pending: dict[int, tuple[int, MidiEvent]] = {}
        notes: dict[int, MidiNote] = {}
        held: dict[int, list[int]] = {}
        sustain: dict[int, bool] = {}
        for i, event in enumerate(events):
            msg, channel = event.message, event.channel
            if channel is None:
                continue
            if msg.type == 'note_on' and msg.velocity > 0:
                if channel in owners and owners[channel] != event.track:
                    raise MidiEventError(f'Conflicting channel ownership: track {event.track}, channel {channel}')
                owners[channel] = event.track
                key = channel, msg.note
                if key in active:
                    raise MidiEventError(f'ambiguous overlapping note: track {event.track}, channel {channel}, note {msg.note}')
                note_id = len(pending)
                active[key] = note_id
                pending[note_id] = i, event
                events[i] = replace(event, note_ids=(note_id,))
            elif msg.type in ('note_off', 'note_on'):
                key = channel, msg.note
                if key not in active:
                    raise MidiEventError(f'unmatched note-off: track {event.track}, channel {channel}, note {msg.note}')
                note_id = active.pop(key)
                on_index, on_event = pending[note_id]
                if on_event.track != event.track or event.tick <= on_event.tick:
                    raise MidiEventError(f'Ambiguous note pairing: channel {channel}, note {msg.note}')
                notes[note_id] = MidiNote(note_id, event.track, channel, msg.note,
                                         on_event.message.velocity, on_event.tick,
                                         event.tick, event.tick, on_index, i)
                events[i] = replace(event, note_ids=(note_id,))
                if sustain.get(channel, False):
                    held.setdefault(channel, []).append(note_id)
            elif msg.type == 'control_change' and msg.control == 64:
                sustain[channel] = msg.value >= 64
                if not sustain[channel]:
                    for note_id in held.pop(channel, []):
                        notes[note_id] = replace(notes[note_id], sounding_end=event.tick)
        if active:
            raise MidiEventError(f'unmatched note-on: {sorted(active)}')
        if any(held.values()):
            raise MidiEventError('Unreleased sustain: sounding notes have no pedal release')
        ordered_notes = tuple(notes[i] for i in sorted(notes))
        for i, event in enumerate(events):
            if not is_expression(event.message):
                continue
            ids = tuple(n.id for n in ordered_notes if n.channel == event.channel
                        and ((n.on < i < n.off) or (n.end <= event.tick < n.sounding_end)
                             or (n.start == event.tick and i < n.on))
                        and (event.message.type != 'polytouch' or n.pitch == event.message.note))
            if not ids:
                # A release reset immediately following a key-off belongs to
                # that lifetime, unless a new note at this tick owns the setup.
                ids = tuple(n.id for n in ordered_notes if n.channel == event.channel
                            and n.end == n.sounding_end == event.tick and n.off < i
                            and (event.message.type != 'polytouch' or n.pitch == event.message.note))
            events[i] = replace(event, note_ids=ids)
        return cls(midi.type, midi.ticks_per_beat, len(midi.tracks), midi.charset,
                   tuple(events), ordered_notes)

    @property
    def tempo_map(self) -> TempoMap:
        return TempoMap(self.ticks_per_beat, tuple(
            (e.tick, e.message.tempo) for e in self.events if e.message.type == 'set_tempo'))

    def to_mido(self) -> Any:
        midi = mido.MidiFile(type=self.type, ticks_per_beat=self.ticks_per_beat, charset=self.charset)
        for tr in range(self.track_count):
            track = mido.MidiTrack()
            previous = 0
            for event in sorted((e for e in self.events if e.track == tr), key=lambda e: e.key):
                track.append(event.message.copy(time=event.tick - previous))
                previous = event.tick
            midi.tracks.append(track)
        return midi

    def save(self, path: Path | str) -> None:
        self.to_mido().save(str(path))


@dataclass(frozen=True)
class NoteTiming:
    start: int
    end: int
    velocity: int


# Parameter values are raw 14-bit MIDI values, not rounded semitone estimates.
State = dict[tuple[str, int], int]
_PARAMETER_CCS = (6, 38, 96, 97, 98, 99, 100, 101)
_DEFAULTS: State = {('pitchwheel', 0): 0, ('aftertouch', 0): 0,
                    ('program_change', 0): 0, ('cc', 0): 0, ('cc', 32): 0,
                    ('bank', 0): 0, ('bank', 32): 0,
                    ('cc', 7): 100, ('cc', 10): 64, ('cc', 11): 127,
                    ('cc', 64): 0, ('cc', 74): 0,
                    ('rpn', 0): 256, ('rpn', 1): 8192, ('rpn', 2): 8192,
                    ('cc', 100): 127, ('cc', 101): 127,
                    ('cc', 98): 127, ('cc', 99): 127, ('selection', 0): 0}


def _update(state: State, msg: Any) -> None:
    if msg.type in ('pitchwheel', 'aftertouch', 'program_change'):
        state[(msg.type, 0)] = getattr(msg, {'pitchwheel': 'pitch', 'aftertouch': 'value',
                                            'program_change': 'program'}[msg.type])
        if msg.type == 'program_change':
            for number in (0, 32):
                state[('bank', number)] = state.get(('cc', number), 0)
    elif msg.type == 'polytouch':
        state[('poly', msg.note)] = msg.value
    elif msg.type == 'control_change':
        cc, value = msg.control, msg.value
        if cc in (98, 99, 100, 101):
            state[('selection', 0)] = int(cc in (98, 99))
        if cc in (96, 97):
            raise ChannelStateConflict('Ambiguous relative parameter increment/decrement in retiming')
        if cc in (6, 38):
            nrpn = state.get(('selection', 0), 0)
            msb, lsb = (99, 98) if nrpn else (101, 100)
            parameter = state.get(('cc', msb), 127) * 128 + state.get(('cc', lsb), 127)
            if parameter != 16383:
                key = ('nrpn' if nrpn else 'rpn', parameter)
                old = state.get(key, 0)
                state[key] = value * 128 + old % 128 if cc == 6 else old // 128 * 128 + value
                if key == ('rpn', 6) and state[key] != 0:
                    raise ChannelStateConflict('MPE master-zone interpretation is unsupported in retiming; use per-channel expression')
        else:
            state[('cc', cc)] = value


def _snapshots(events: tuple[MidiEvent, ...]) -> list[State]:
    channels: dict[int, State] = {}
    result = []
    for e in events:
        state = channels.setdefault(e.channel, dict(_DEFAULTS)) if e.channel is not None else {}
        _update(state, e.message)
        result.append(dict(state))
    return result


def _protected(state: State, pitch: int) -> State:
    """Persistent CC automation stays on its timeline; pitch/expression cannot drift."""
    result = {k: v for k, v in state.items()
            if k[0] in ('pitchwheel', 'aftertouch', 'program_change', 'bank', 'rpn', 'nrpn')
            or k in (('cc', 74), ('poly', pitch))}
    result.setdefault(('poly', pitch), 0)
    return result


def _replay(current: State, wanted: State, channel: int) -> list[Any]:
    result = []

    def cc(number: int, value: int) -> None:
        result.append(mido.Message('control_change', channel=channel, control=number, value=value))

    instrument_keys = (('bank', 0), ('bank', 32), ('program_change', 0))
    if any(current.get(k, 0) != wanted.get(k, 0) for k in instrument_keys):
        for number in (0, 32):
            cc(number, wanted.get(('bank', number), 0))
        result.append(mido.Message('program_change', channel=channel,
                                   program=wanted.get(('program_change', 0), 0)))
        # Bank selects latch on program change. Restore pending source selects,
        # not the active bank, for subsequent program automation.
        for number in (0, 32):
            cc(number, current.get(('cc', number), 0))
    # Bank selects precede programs; parameter selection/data entry are replayed
    # as transactions, never as an unordered bag of controller values.
    for key in sorted(current.keys() | wanted.keys()):
        kind, number = key
        value = wanted.get(key, _DEFAULTS.get(key, 0))
        if current.get(key, _DEFAULTS.get(key, 0)) == value:
            continue
        if kind == 'cc' and number not in (*_PARAMETER_CCS, 0, 32):
            cc(number, value)
        elif kind in ('rpn', 'nrpn'):
            if kind != 'rpn' or number not in (0, 1, 2):
                raise ChannelStateConflict(f'Cannot replay unknown/device-specific parameter {kind} {number}')
            msb, lsb = (101, 100) if kind == 'rpn' else (99, 98)
            cc(msb, number // 128); cc(lsb, number % 128)
            cc(6, value // 128); cc(38, value % 128)
        elif kind in ('pitchwheel', 'aftertouch'):
            field = {'pitchwheel': 'pitch', 'aftertouch': 'value', 'program_change': 'program'}[kind]
            result.append(mido.Message(kind, channel=channel, **{field: value}))
        elif kind == 'poly':
            result.append(mido.Message('polytouch', channel=channel, note=number, value=value))
    if result:
        # Preserve both selectors and which family was selected last.
        selectors = (101, 100, 99, 98) if current.get(('selection', 0)) else (99, 98, 101, 100)
        for number in selectors:
            cc(number, current.get(('cc', number), 127))
    return result


def _expression_tick(source: MidiEvents, note: MidiNote, timing: NoteTiming, tick: int) -> int:
    tempo = source.tempo_map
    old_start, old_end = tempo.seconds(note.start), tempo.seconds(note.end)
    new_start, new_end = tempo.seconds(timing.start), tempo.seconds(timing.end)
    when = tempo.seconds(tick)
    # Articulation scales key-down expression. Sustain-tail expression follows
    # the moved key release; the pedal release itself remains absolute.
    seconds = (new_start + (when - old_start) * (new_end - new_start) / (old_end - old_start)
               if tick <= note.end else new_end + when - old_end)
    return tick + round(tempo.tick(seconds) - tick)


def _conflict(note: MidiNote, detail: str) -> ChannelStateConflict:
    return ChannelStateConflict(f'track {note.track}, channel {note.channel}, note {note.pitch} '
                                f'(id {note.id}): {detail}; retry with groove disabled')


def _order_boundaries(source: MidiEvents, moved: list[MidiEvent]) -> list[MidiEvent]:
    """Stable order except release bundles that must precede a reused channel."""
    changed = {(e.track, e.tick) for e, old in zip(moved, source.events)
               if e.tick != old.tick and e.note_ids}
    for track, tick in changed:
        group = sorted((e for e in moved if e.track == track and e.tick == tick), key=lambda e: e.key)
        starts = [e for e in group if e.message.type == 'note_on' and e.message.velocity]
        ends = [e for e in group if e.message.type == 'note_off' or
                (e.message.type == 'note_on' and not e.message.velocity)]
        for end in ends:
            note_id = end.note_ids[0]
            release = [e for e in group if note_id in e.note_ids]
            for start in starts:
                if start.channel != end.channel:
                    continue
                setup = [e for e in group if start.note_ids[0] in e.note_ids]
                before = min(group.index(e) for e in setup)
                if max(group.index(e) for e in release) < before:
                    continue
                if any(e in release for e in setup):
                    raise _conflict(source.notes[note_id], 'ambiguous shared expression at note boundary')
                group = [e for e in group if e not in release]
                before = min(group.index(e) for e in setup)
                group[before:before] = release
        positions = {e.order: Fraction(i * 2) for i, e in enumerate(group)}
        moved = [replace(e, sort_order=positions[e.order]) if e.track == track and e.tick == tick else e
                 for e in moved]
    return moved


def retime(source: MidiEvents, timings: dict[int, NoteTiming]) -> MidiEvents:
    """Retime paired notes/associated expression, replay setup, audit every voice.

    No channel reallocation. Unsafe overlaps (including pedal-held notes),
    ambiguous shared expression and opaque state-boundary crossings fail closed.
    Original events retain their source track/order IDs; replay IDs are negative.
    """
    targets = {n.id: timings.get(n.id, NoteTiming(n.start, n.end, n.velocity)) for n in source.notes}
    if timings.keys() - targets.keys():
        raise MidiEventError('Unknown note identity in retiming request')
    for n in source.notes:
        t = targets[n.id]
        if (not all(isinstance(v, int) for v in (t.start, t.end, t.velocity))
                or t.start < 0 or t.end <= t.start or not 1 <= t.velocity <= 127):
            raise _conflict(n, 'invalid note timing/velocity')
    if all(targets[n.id] == NoteTiming(n.start, n.end, n.velocity) for n in source.notes):
        return source
    original_states = _snapshots(source.events)
    moved = []
    for i, e in enumerate(source.events):
        at, msg = e.tick, e.message
        if msg.type in ('note_on', 'note_off'):
            n = source.notes[e.note_ids[0]]
            t = targets[n.id]
            at = t.start if i == n.on else t.end
            if i == n.on:
                msg = freeze_message(msg.copy(velocity=t.velocity))
        elif is_expression(msg) and e.note_ids:
            ticks = {_expression_tick(source, source.notes[n], targets[n], e.tick) for n in e.note_ids}
            if len(ticks) != 1:
                raise _conflict(source.notes[e.note_ids[0]], 'ambiguous shared expression retiming')
            at = ticks.pop()
        elif msg.type == 'control_change' and msg.control in (66, 69, 120, 121, 123, 124, 125, 126, 127):
            raise ChannelStateConflict(f'Unsupported pedal/channel-mode CC {msg.control} in retiming')
        moved.append(replace(e, tick=at, message=msg))
    # EOT is a container boundary, not automation: extend only if necessary.
    for i, e in enumerate(moved):
        if e.message.type == 'end_of_track':
            moved[i] = replace(e, tick=max(e.tick, max(x.tick for x in moved if x.track == e.track)))
    moved = _order_boundaries(source, moved)
    moved_by_id = {(e.track, e.order): e for e in moved}
    for sysex in (e for e in source.events if e.message.type == 'sysex'):
        new_sysex = moved_by_id[(sysex.track, sysex.order)]
        for n in source.notes:
            for index in (n.on, n.off):
                old = source.events[index]
                new = moved_by_id[(old.track, old.order)]
                if (old.key < sysex.key) != (new.key < new_sysex.key):
                    raise _conflict(n, 'opaque SysEx boundary crossing')
    channels: dict[int, State] = {}
    replayed: list[MidiEvent] = []
    replay_order = min(e.order for e in source.events) - 1
    for e in sorted(moved, key=lambda e: e.key):
        state = channels.setdefault(e.channel, dict(_DEFAULTS)) if e.channel is not None else {}
        if e.message.type == 'note_on' and e.message.velocity:
            n = source.notes[e.note_ids[0]]
            wanted = dict(original_states[n.on])
            for key, value in state.items():
                # Ordinary automation is absolute. Only an earlier onset needs
                # its future initial controls replayed; never rewind later CCs.
                if ((e.tick >= n.start and key[0] == 'cc' and key[1] not in (0, 32, 74))
                        or (key[0] == 'poly' and key[1] != n.pitch)):
                    wanted[key] = value
            setup = _replay(state, wanted, n.channel)
            for j, msg in enumerate(setup):
                anchor = e.sort_order if e.sort_order is not None else Fraction(e.order)
                replayed.append(MidiEvent(e.tick, e.track, replay_order,
                                          freeze_message(msg), (n.id,),
                                          anchor - Fraction(len(setup) - j, len(setup) + 1)))
                replay_order -= 1
                _update(state, msg)
        replayed.append(e)
        _update(state, e.message)
    replayed.sort(key=lambda e: e.key)
    candidate = replace(source, events=tuple(replayed))
    try:
        parsed = MidiEvents.from_mido(candidate.to_mido())
    except MidiEventError as exc:
        raise ChannelStateConflict(f'Invalid retimed channel state/pairing: {exc}') from exc
    # Pair by source event identity, not pitch or chronological note index.
    notes = []
    parsed_by_key = {(n.track, n.channel, n.pitch, n.start): n for n in parsed.notes}
    for n in source.notes:
        t = targets[n.id]
        observed = parsed_by_key[(n.track, n.channel, n.pitch, t.start)]
        expected_release = n.sounding_end if n.sounding_end > n.end else t.end
        if observed.sounding_end != expected_release:
            raise _conflict(n, 'sustain lifetime changed')
        on = next(i for i, e in enumerate(replayed) if e.track == n.track and e.order == source.events[n.on].order)
        off = next(i for i, e in enumerate(replayed) if e.track == n.track and e.order == source.events[n.off].order)
        notes.append(replace(n, start=t.start, end=t.end, velocity=t.velocity,
                             sounding_end=observed.sounding_end, on=on, off=off))
    candidate = replace(candidate, notes=tuple(notes))
    actual_states = _snapshots(candidate.events)
    for n, new_note in zip(source.notes, candidate.notes):
        expected = _protected(original_states[n.on], n.pitch)
        if _protected(actual_states[new_note.on], n.pitch) != expected:
            raise _conflict(n, 'initial pitch/expression state changed')
        changes = []
        previous = expected
        for i in range(n.on + 1, len(source.events)):
            e = source.events[i]
            if e.tick >= n.sounding_end:
                break
            if e.channel != n.channel:
                continue
            current = _protected(original_states[i], n.pitch)
            delta = {k: v for k, v in current.items() if previous.get(k, 0) != v}
            if delta:
                at = moved_by_id[(e.track, e.order)].tick
                if not new_note.start <= at < new_note.sounding_end:
                    raise _conflict(n, 'pitch/expression trajectory crosses note lifetime')
                changes.append((at, i, delta))
            previous = current
        changes.sort(key=lambda change: change[:2])
        change_index = 0
        # Compare at every channel-event boundary, including other voices' bends,
        # replay transactions and automation; transient setup at one tick is atomic.
        audit_ticks = sorted({e.tick for e in candidate.events if e.channel == n.channel
                        and new_note.start <= e.tick < new_note.sounding_end})
        for tick in audit_ticks:
            while change_index < len(changes) and changes[change_index][0] <= tick:
                expected.update(changes[change_index][2]); change_index += 1
            last = max(i for i, e in enumerate(candidate.events) if e.channel == n.channel and e.tick == tick)
            actual = _protected(actual_states[last], n.pitch)
            if any(actual.get(k, 0) != expected.get(k, 0) for k in actual.keys() | expected.keys()):
                raise _conflict(n, f'incompatible simultaneous pitch/expression state at tick {tick}')
    return candidate


def add_ghosts(source: MidiEvents, ghosts: list[tuple[int, NoteTiming]]) -> MidiEvents:
    """Insert optional ghosts only in unambiguous channel gaps, restoring state.

    A duplicate key during an existing voice is not a portable MIDI note pair.
    Neither a new channel nor a shortened original voice is silently invented.
    """
    result = source
    for note_id, timing in ghosts:
        note = source.notes[note_id]
        if any(n.channel == note.channel and n.start < timing.end and timing.start < n.sounding_end
               for n in result.notes):
            raise _conflict(note, 'ghost note overlap with an existing sounding note')
        if timing.start < 0 or timing.end <= timing.start or not 1 <= timing.velocity <= 127:
            raise _conflict(note, 'invalid ghost timing/velocity')
        if any(timing.start < e.tick < timing.end and
               (e.channel == note.channel or e.message.type == 'sysex') for e in result.events):
            raise _conflict(note, 'ghost crosses channel state automation')
        snapshots = _snapshots(result.events)
        current = dict(_DEFAULTS)
        for e, state in zip(result.events, snapshots):
            if e.tick <= timing.start and e.channel == note.channel:
                current = state
        wanted = _snapshots(source.events)[note.on]
        events = list(result.events)
        order = max(e.order for e in events) + 1
        setup = _replay(current, wanted, note.channel)
        ghost_state = dict(current)
        for msg in setup:
            _update(ghost_state, msg)
        restore = _replay(ghost_state, current, note.channel)
        bundle = [(timing.start, m) for m in setup]
        bundle.append((timing.start, mido.Message('note_on', channel=note.channel,
                                                 note=note.pitch, velocity=timing.velocity)))
        bundle.append((timing.end, mido.Message('note_off', channel=note.channel, note=note.pitch)))
        bundle.extend((timing.end, m) for m in restore)
        ghost_id = len(result.notes)
        for i, (tick, msg) in enumerate(bundle):
            priority = Fraction(i - len(bundle) - 1) if tick == timing.end else None
            events.append(MidiEvent(tick, note.track, order + i, freeze_message(msg), (ghost_id,), priority))
        for i, e in enumerate(events):
            if e.track == note.track and e.message.type == 'end_of_track':
                events[i] = replace(e, tick=max(e.tick, timing.end), sort_order=Fraction(order + len(bundle)))
        candidate = replace(result, events=tuple(sorted(events, key=lambda e: e.key)))
        try:
            parsed = MidiEvents.from_mido(candidate.to_mido())
        except MidiEventError as exc:
            raise _conflict(note, f'ghost pairing/state invalid: {exc}') from exc
        positions = {(e.track, e.order): i for i, e in enumerate(candidate.events)}
        notes = []
        for old in result.notes:
            on, off = result.events[old.on], result.events[old.off]
            notes.append(replace(old, on=positions[(on.track, on.order)],
                                 off=positions[(off.track, off.order)]))
        ghost = next(n for n in parsed.notes if n.track == note.track and
                     n.channel == note.channel and n.pitch == note.pitch and n.start == timing.start)
        if ghost.sounding_end != timing.end:
            raise _conflict(note, 'ghost sustain lifetime exceeds its safe channel gap')
        notes.append(replace(ghost, id=ghost_id,
                             on=positions[(note.track, order + len(setup))],
                             off=positions[(note.track, order + len(setup) + 1)]))
        result = replace(candidate, notes=tuple(notes))
    return result
