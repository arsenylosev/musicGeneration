"""Deterministic stem rendering helpers (simple backend)."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pretty_midi as pm
import soundfile as sf

from aimusic.audio.analysis.from_midi import _infer_role
from aimusic.audio.midi_events import MidiEvents, MidiEventError
from aimusic.audio.render.simple_r import _peak_dbfs, render_stem
from aimusic.audio.render.util import as_mono, peak_normalize


@dataclass(frozen=True)
class SourceStem:
    source_track_index: int
    name: str
    role: str
    role_source: str
    channels: tuple[int, ...]
    midi: MidiEvents


def isolate_source_stems(source: MidiEvents, *, analysis: dict[str, Any] | None = None) -> tuple[SourceStem, ...]:
    """Use source identities, never PrettyMIDI instrument indices, for stems."""
    declarations = {}
    for row in (analysis or {}).get('tracks', []):
        index = row.get('source_track_index')
        if index is None:
            if row.get('role_source') == 'structure.json':
                raise MidiEventError('Authoritative role requires source_track_index; reconcile first')
            continue  # Legacy inferred PrettyMIDI indices are not SMF identities.
        if not isinstance(index, int) or index in declarations:
            raise MidiEventError('Invalid/duplicate source_track_index in stem roles')
        declarations[index] = row
    result = []
    for index, midi in source.isolate_tracks(tuple(declarations)).items():
        name = next((e.message.name for e in source.events
                     if e.track == index and e.message.type == 'track_name'), '')
        channels = tuple(sorted({e.channel for e in source.events if e.track == index and e.channel is not None}))
        program = next((e.message.program for e in source.events
                        if e.channel in channels and e.message.type == 'program_change'), 0)
        inferred = _infer_role(pm.Instrument(program=program, is_drum=channels == (9,), name=name))
        row = declarations.get(index, {})
        role = str(row.get('role') or inferred)
        result.append(SourceStem(index, name, 'comping' if role == 'comp' else role,
                                 str(row.get('role_source', 'inferred')), channels, midi))
    return tuple(result)


def render_click(
    bar_boundaries_sec: list[float],
    *,
    sample_rate: int = 48000,
    output_path: Path | str,
    peak_dbfs: float = -12.0,
) -> Path:
    """Simple metronome click at bar starts."""
    if len(bar_boundaries_sec) < 2:
        duration = 1.0
    else:
        duration = float(bar_boundaries_sec[-1])
    n = max(1, int(duration * sample_rate))
    audio = np.zeros(n, dtype=np.float64)
    click_len = int(0.01 * sample_rate)
    t = np.arange(click_len) / sample_rate
    click = np.sin(2 * np.pi * 1000.0 * t) * np.linspace(1.0, 0.0, click_len)
    for b in bar_boundaries_sec[:-1]:
        i = int(b * sample_rate)
        if 0 <= i < n:
            j = min(n, i + click_len)
            audio[i:j] += click[: j - i]
    audio = peak_normalize(audio, peak_dbfs)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), audio.astype(np.float32), sample_rate, subtype="FLOAT")
    return out


def rough_mix(
    stem_paths: list[Path | str], *, output_path: Path | str, peak_dbfs: float = -12.0
) -> Path:
    """Sum stems to a rough mono mix for listening."""
    waves: list[np.ndarray] = []
    sr = None
    for p in stem_paths:
        data, file_sr = sf.read(str(p), always_2d=False)
        data = as_mono(np.asarray(data, dtype=np.float64))
        if sr is None:
            sr = int(file_sr)
        waves.append(data)
    if not waves or sr is None:
        raise ValueError("no stems to mix")
    max_len = max(w.shape[0] for w in waves)
    mix = np.zeros(max_len, dtype=np.float64)
    for w in waves:
        mix[: w.shape[0]] += w
    mix = peak_normalize(mix, peak_dbfs)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), mix.astype(np.float32), sr, subtype="FLOAT")
    return out


def write_bar_boundary_table(
    bar_boundaries_sec: list[float],
    *,
    output_path: Path | str,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"bar": i, "start_sec": float(t)} for i, t in enumerate(bar_boundaries_sec)]
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return out


# Layout revision invalidates old name-keyed stem caches, not renderer policy.
STEM_LAYOUT_VERSION = 2
GENERATED = {'click': 'click.wav', 'rough_mix': 'rough_mix.wav',
             'bar_boundaries': 'bar_boundaries.json', 'role_index': 'role_index.json'}


def _stem_id(stem: SourceStem) -> str:
    label = unicodedata.normalize('NFKD', stem.name or stem.role).encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:48] or 'stem'
    return f'track_{stem.source_track_index:04d}_{slug}'


def _output_root(output: Path | str) -> Path:
    path = Path(output)
    if path.is_symlink():
        raise ValueError(f'Stem output root is a symlink: {path}')
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def _check_new_files(root: Path, names: list[str]) -> None:
    for name in names:
        target = root / name
        if Path(name).name != name or target.resolve().parent != root:
            raise ValueError(f'Stem output violates containment: {target}')
        if target.exists() or target.is_symlink():
            raise FileExistsError(f'Stem output already exists: {target}')


def _publish_staged(staging: Path, root: Path, names: list[str]) -> None:
    """Link complete files exclusively; never truncate/follow a destination.

    A pinned directory descriptor protects the publication root. This is not
    crash-atomic as a set; concurrent writers to the same output are unsupported.
    """
    _check_new_files(root, names)
    parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    published = []
    try:
        for name in names:
            os.link(staging / name, name, dst_dir_fd=parent_fd, follow_symlinks=False)
            published.append(name)
    except BaseException:
        for name in reversed(published):
            os.unlink(name, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)


def copy_stem_artifacts(source: Path, output: Path) -> None:
    """Restore a stem directory without deleting or overwriting user files."""
    files = sorted(p for p in source.iterdir() if p.name != '_complete')
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('Stem artifacts must be regular files, not symlinks/directories')
    root = _output_root(output)
    names = [p.name for p in files]
    _check_new_files(root, names)
    with tempfile.TemporaryDirectory(prefix='.stems-', dir=root) as tmp:
        staging = Path(tmp)
        for path in files:
            shutil.copyfile(path, staging / path.name)
        _publish_staged(staging, root, names)


def render_role_stems(
    midi_path: Path | str,
    *,
    output_dir: Path | str,
    config: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Render each logical source once; publish safe IDs plus a role index.

    Preserved event MIDI is the renderer input. Simple still provides only
    integer-pitch sine previews, not instrument/controller/tuning certification.
    """
    source = MidiEvents.load(midi_path)
    logical = isolate_source_stems(source, analysis=analysis)
    if not logical:
        raise ValueError('no source stems to render')
    cfg = config or {}
    sr = int(cfg.get('render', {}).get('sample_rate', 48000))
    peak_dbfs = _peak_dbfs(cfg)
    rows: list[dict[str, Any]] = []
    roles: dict[str, list[str]] = {}
    for stem in logical:
        key = _stem_id(stem)
        rows.append({'id': key, 'source_track_index': stem.source_track_index,
                     'name': stem.name, 'role': stem.role, 'role_source': stem.role_source,
                     'channels': list(stem.channels), 'note_count': len(stem.midi.notes),
                     'midi': f'{key}.mid', 'wav': f'{key}.wav'})
        roles.setdefault(stem.role, []).append(key)
    names = [row['id'] + suffix for row in rows for suffix in ('.mid', '.wav', '.wav.json')]
    names.extend(GENERATED.values())
    root = _output_root(output_dir)
    _check_new_files(root, names)

    bounds = (analysis or {}).get('bar_boundaries_sec')
    if not bounds:
        end = float(source.tempo_map.seconds(max(e.tick for e in source.events)))
        tempo = next((value for tick, value in source.tempo_map.changes if tick == 0), 500000)
        bar = 4 * tempo / 1000000
        bounds = [i * bar for i in range(max(1, round(end / bar)) + 1)]
        bounds[-1] = end

    with tempfile.TemporaryDirectory(prefix='.stems-', dir=root) as tmp:
        staging = Path(tmp)
        waves: list[Path | str] = []
        for stem, row in zip(logical, rows):
            midi_path = staging / row['midi']
            stem.midi.save(midi_path)
            wav_path = staging / row['wav']
            render_stem(midi_path, output_path=wav_path, config=cfg, sample_rate=sr)
            # Relative artifact references survive cache-directory copies.
            sidecar = Path(str(wav_path) + '.json')
            manifest = json.loads(sidecar.read_text(encoding='utf-8'))
            manifest['output_path'] = row['wav']
            sidecar.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8')
            waves.append(wav_path)
        render_click(bounds, sample_rate=sr, output_path=staging / GENERATED['click'], peak_dbfs=peak_dbfs)
        rough_mix(waves, output_path=staging / GENERATED['rough_mix'], peak_dbfs=peak_dbfs)
        write_bar_boundary_table(bounds, output_path=staging / GENERATED['bar_boundaries'])
        (staging / GENERATED['role_index']).write_text(json.dumps(
            {'schema_version': STEM_LAYOUT_VERSION, 'stems': rows, 'roles': roles,
             'generated': GENERATED}, indent=2, sort_keys=True), encoding='utf-8')
        _publish_staged(staging, root, names)
    return {**{row['id']: Path(output_dir) / row['wav'] for row in rows},
            **{key: Path(output_dir) / name for key, name in GENERATED.items()}}
