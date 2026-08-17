"""tension.py — Documented, versioned tension model.

Replaces three previously-duplicated, disconnected tension formulas
(`aimusic.core.diagnostics.compute_tension_curve` [dead code, tested against
a fictional role vocabulary], `aimusic.app.cli.ROLE_TENSION`, and the
role+boundary-only heuristic that had crept into the CLI) with a single pure
function that also accounts for tonal distance — key motion and chord
motion — via `aimusic.theory.tonal.tonal_distance` / `basic_space_distance`.

Relationship to `aimusic.scoring.gttm_features`
-------------------------------------------------------------------------
`gttm_features.py` already has `harmonic_key_proximity_feature`,
`harmonic_chord_proximity_feature`, and `cadential_harmonic_motion_feature`,
built on the same `tonal_distance` / `basic_space_distance` primitives, over
the same `(prev_state, next_state)` transition-pair convention used here.
They are currently dead in the actual pipeline (only exercised by
`tests/test_gttm_beatstate.py`; nothing outside that module imports them)
just like the old `compute_tension_curve` was.

This module deliberately does not import those feature functions. They are
*proximity* scores intended for GTTM-style prior scoring: unbounded decay
`1 / (1 + distance)`, not clamped, and returning negative sentinel values
(-1.0, -0.5, -0.8...) when a token can't be resolved — semantics suited to
scoring candidate transitions, not to a `[0, 1]` diagnostics tension value
that gets serialized straight into a manifest. Reusing the private
underscore-prefixed resolver helpers (`_key_token`, `_chord_token`,
`_cached_basic_space_distance`, ...) across modules was also avoided since
they aren't part of that module's public surface.

What *is* reused is the same decay family: this module's key- and
chord-motion components use `distance / (1 + distance)`, the same functional
shape as `gttm_features`'s `1 / (1 + distance)`, just inverted (rises with
distance instead of falling) and naturally bounded in `[0, 1)` without a
hand-picked cap. See §4.3's worked example below and
docs/tension-diagnostics-spec.md §3 for the explicit "why not reuse"
rationale.

`aimusic.decode._tension_level` is a *different* concern (it drives note
velocity/expression during MIDI rendering) and is intentionally not unified
with this module. See docs/tension-diagnostics-spec.md §2, §7.

Per-transition, not per-state
-------------------------------------------------------------------------
The previous formulas were per-state: `tension_curve[i]` was "tension of
state i" from role/boundary alone. `beat_tension` is per-transition: it
takes `(prev_state, state)` and returns the tension of transitioning *into*
`state`. `realized_tension_curve(path)[i]` is therefore "tension of the
transition into `path[i]`", informed by `(path[i-1], path[i])` — for `i=0`
there is no incoming transition, so it falls back to role/boundary only
(see `beat_tension`'s docstring). The time-indexing convention (index i ==
beat i) is unchanged, so nothing downstream that only reads
time/length/keys breaks, but the *meaning* of "tension at index i" has
shifted from "of this beat" to "of arriving at this beat" — worth knowing
if you're comparing curves across the old and new formulas by eye.

--------------------------------------------------------------------------
Version history
--------------------------------------------------------------------------
1.0.0 — initial versioned model. Combines role, boundary, key motion
        (circle-of-fifths distance between consecutive key roots) and chord
        motion (Lerdahl TPS distance between consecutive chords), with an
        optional, off-by-default head/groove term. Key- and chord-motion
        normalization uses `distance / (1 + distance)` (unbounded decay,
        matching gttm_features' proximity-scoring convention) rather than a
        hand-picked division cap, so it can't saturate to 1.0 early for
        wide chord vocabularies (e.g. 7-tone extended chords) the way a
        fixed divisor would.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from aimusic.core.core_types import BeatState
from aimusic.core.vocab import Vocabularies
from aimusic.theory.tonal import basic_space_distance, tonal_distance

if TYPE_CHECKING:  # pragma: no cover - import-time cycle avoidance only
    from aimusic.planning.plans import PlanningSection

TENSION_MODEL_VERSION = "1.0.0"

# Role labels are documented in aimusic/core/vocab.py:388-391. Values are
# ordered hold < prep < change < cad — a monotonically increasing sense of
# harmonic urgency — and are the base contribution before boundary/tonal
# terms are added.
_ROLE_BASE_TENSION: Dict[str, float] = {
    "hold": 0.0,
    "prep": 0.33,
    "change": 0.66,
    "cad": 1.0,
}


@dataclass(frozen=True)
class TensionWeights:
    """Weights for each contribution to `beat_tension`."""

    role: float = 0.40
    boundary: float = 0.15
    key_motion: float = 0.20
    chord_motion: float = 0.25
    head_groove: float = 0.0

    def __post_init__(self) -> None:
        for name in ("role", "boundary", "key_motion", "chord_motion", "head_groove"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"TensionWeights.{name} must be a real number.")
            if value < 0.0:
                raise ValueError(f"TensionWeights.{name} must be >= 0.")


DEFAULT_WEIGHTS = TensionWeights()


def _role_label(state: BeatState, vocabularies: Vocabularies) -> str:
    return vocabularies.roles.token_for_id(state.role_id).label


def _max_boundary_level(vocabularies: Vocabularies) -> int:
    return max(token.level for token in vocabularies.boundaries.tokens)


def _role_component(state: BeatState, vocabularies: Vocabularies) -> float:
    label = _role_label(state, vocabularies)
    return _ROLE_BASE_TENSION.get(label, 0.5)


def _boundary_component(state: BeatState, vocabularies: Vocabularies) -> float:
    max_level = _max_boundary_level(vocabularies)
    if max_level <= 0:
        return 0.0
    return min(1.0, state.boundary_lvl / max_level)


def _decay_normalize(distance: float) -> float:
    """Map a non-negative distance to [0, 1) via distance / (1 + distance)."""
    return distance / (1.0 + distance)


def _key_motion_component(
    prev_state: Optional[BeatState],
    state: BeatState,
    vocabularies: Vocabularies,
    edo: int,
) -> float:
    if prev_state is None:
        return 0.0
    prev_root = vocabularies.keys.token_for_id(prev_state.key_id).root_pc
    root = vocabularies.keys.token_for_id(state.key_id).root_pc
    distance = tonal_distance(prev_root, root, edo)
    return _decay_normalize(distance)


def _chord_motion_component(
    prev_state: Optional[BeatState],
    state: BeatState,
    vocabularies: Vocabularies,
    edo: int,
) -> float:
    if prev_state is None:
        return 0.0
    prev_chord = vocabularies.chords.token_for_id(prev_state.chord_id)
    chord = vocabularies.chords.token_for_id(state.chord_id)
    distance = basic_space_distance(
        prev_chord.root_pc, prev_chord.quality,
        chord.root_pc, chord.quality,
        edo,
    )
    return _decay_normalize(distance)


def _head_groove_component(
    prev_state: Optional[BeatState],
    state: BeatState,
    vocabularies: Vocabularies,
) -> float:
    if prev_state is None:
        return 0.0
    changed = 0.0
    if prev_state.head_id != state.head_id:
        changed += 0.5
    if prev_state.groove_id != state.groove_id:
        changed += 0.5
    return min(1.0, changed)


def beat_tension(
    prev_state: Optional[BeatState],
    state: BeatState,
    vocabularies: Vocabularies,
    edo: int,
    weights: TensionWeights = DEFAULT_WEIGHTS,
) -> float:
    """Compute tension in [0, 1] for `state`, given the beat that precedes it.

    Pure function: no I/O, no mutation, no shared state. Same inputs always
    produce the same output.

    Combines, as a weighted sum:
      - role component: authored harmonic function (hold < prep < change < cad)
      - boundary component: structural boundary level, normalized by the
        vocabulary's max boundary level
      - key motion component: circle-of-fifths distance (Lerdahl's *j*)
        between the previous and current key root, normalized by edo // 2
      - chord motion component: full Lerdahl TPS distance (*j + k*) between
        the previous and current chord, normalized by a generous fixed cap
      - head/groove component (off by default): whether the head or groove
        token changed beat-to-beat

    """
    role = _role_component(state, vocabularies)
    boundary = _boundary_component(state, vocabularies)
    key_motion = _key_motion_component(prev_state, state, vocabularies, edo)
    chord_motion = _chord_motion_component(prev_state, state, vocabularies, edo)
    head_groove = _head_groove_component(prev_state, state, vocabularies)

    total = (
        weights.role * role
        + weights.boundary * boundary
        + weights.key_motion * key_motion
        + weights.chord_motion * chord_motion
        + weights.head_groove * head_groove
    )
    return max(0.0, min(1.0, total))


def realized_tension_curve(
    path: Sequence[BeatState],
    vocabularies: Vocabularies,
    edo: int,
    weights: TensionWeights = DEFAULT_WEIGHTS,
) -> List[Tuple[float, float]]:
    """Time-indexed realized tension curve from a selected BeatState path.

    One (time, tension) sample per beat in `path`, time indexed 0..len-1 to
    match `aimusic.app.cli._segment_timeline`'s indexing of the other
    structural timelines.
    """
    curve: List[Tuple[float, float]] = []
    prev_state: Optional[BeatState] = None
    for index, state in enumerate(path):
        tension = beat_tension(prev_state, state, vocabularies, edo, weights)
        curve.append((float(index), tension))
        prev_state = state
    return curve


def target_tension_curve(sections: Sequence["PlanningSection"]) -> List[Tuple[float, float]]:
    """Time-indexed target tension curve sampled from section arcs.

    Each section's `target_tension_arc` (>= 2 control points, evenly spaced
    across [start_time, end_time)) is linearly interpolated to produce one
    sample per beat, so the result lines up point-for-point with
    `realized_tension_curve`'s time indexing.
    """
    curve: List[Tuple[float, float]] = []
    for section in sections:
        arc = section.target_tension_arc
        span = section.end_time - section.start_time
        n_segments = len(arc) - 1
        for offset in range(span):
            # Position within the section, in [0, 1).
            frac = offset / span if span > 0 else 0.0
            # Which arc segment this position falls into.
            seg_pos = frac * n_segments
            seg_index = min(int(seg_pos), n_segments - 1)
            seg_frac = seg_pos - seg_index
            start_val = arc[seg_index]
            end_val = arc[seg_index + 1]
            value = start_val + (end_val - start_val) * seg_frac
            curve.append((float(section.start_time + offset), value))
    return curve


@dataclass(frozen=True)
class TensionDeviationReport:
    """Comparison of a target curve against a realized curve."""

    mean_absolute_error: float
    max_absolute_error: float
    section_errors: Dict[str, float]
    target_peak_time: float
    realized_peak_time: float
    peak_timing_offset: float
    shape_correlation: float


def _as_time_value_map(curve: Sequence[Tuple[float, float]]) -> Dict[float, float]:
    return {time: value for time, value in curve}


def _pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        # A constant series has undefined correlation; treat as no signal.
        return 0.0
    return statistics.correlation(xs, ys)


def compare_tension_curves(
    target: Sequence[Tuple[float, float]],
    realized: Sequence[Tuple[float, float]],
    sections: Sequence["PlanningSection"],
) -> TensionDeviationReport:
    """Compare target vs. realized tension curves.

    Pure function: takes two curves and the section plan, returns a report.
    Comparison is over the intersection of time points present in both
    curves (curves are expected to share time indexing, but this makes the
    function robust to minor length mismatches rather than raising).
    """
    target_map = _as_time_value_map(target)
    realized_map = _as_time_value_map(realized)
    shared_times = sorted(set(target_map) & set(realized_map))

    if not shared_times:
        return TensionDeviationReport(
            mean_absolute_error=0.0,
            max_absolute_error=0.0,
            section_errors={},
            target_peak_time=0.0,
            realized_peak_time=0.0,
            peak_timing_offset=0.0,
            shape_correlation=0.0,
        )

    abs_errors = [abs(target_map[t] - realized_map[t]) for t in shared_times]
    mean_abs_error = sum(abs_errors) / len(abs_errors)
    max_abs_error = max(abs_errors)

    section_errors: Dict[str, float] = {}
    for section in sections:
        section_times = [
            t for t in shared_times
            if section.start_time <= t < section.end_time
        ]
        if not section_times:
            continue
        section_abs_errors = [abs(target_map[t] - realized_map[t]) for t in section_times]
        section_errors[section.name] = sum(section_abs_errors) / len(section_abs_errors)

    target_peak_time = max(shared_times, key=lambda t: target_map[t])
    realized_peak_time = max(shared_times, key=lambda t: realized_map[t])

    target_series = [target_map[t] for t in shared_times]
    realized_series = [realized_map[t] for t in shared_times]
    shape_correlation = _pearson_correlation(target_series, realized_series)

    return TensionDeviationReport(
        mean_absolute_error=mean_abs_error,
        max_absolute_error=max_abs_error,
        section_errors=section_errors,
        target_peak_time=target_peak_time,
        realized_peak_time=realized_peak_time,
        peak_timing_offset=realized_peak_time - target_peak_time,
        shape_correlation=shape_correlation,
    )
