# Logical stems and artifact names (layout 2)

Stem isolation uses the audio-local absolute-tick event model, **not**
PrettyMIDI's instrument list. One note-bearing SMF source track becomes one
stem, even when it uses several MPE member channels or program changes. Type-0
MIDI therefore remains one logical stem. Declared silent source tracks survive;
undeclared named/unused-channel silent instrument tracks are retained when they
are not conductor/setup-only tracks. An empty conductor is not audible content.

Every stem's `.mid` retains the original track slots and equal-tick ordering,
all channel-less events (including conductor metadata and opaque SysEx), and
its channels' complete event streams, including setup in other tracks.
Unowned conductor/setup channel events are retained in every stem as well. Empty
track slots are intentional: renumbering/merging them could reorder setup at
note boundaries. Banks/programs, drums on channel 9 (zero-based), sustain,
RPN/bends and aftertouch are not reconstructed from notes. Distinct instruments
must not own the same channel or write another instrument's channel state;
conflicts fail before rendering. Dedicated non-instrument setup tracks may
supply channel state. No channel reallocation or MPE master-zone interpretation
is performed.

Reconciled package rows associate roles using `source_track_index`; their
`role_source` remains `structure.json`, and `comp` becomes `comping`. Old inferred
PrettyMIDI `index` values are deliberately **not** treated as source-track IDs.
Without an authoritative association, the source name/program/drum heuristic
supplies a role labeled `inferred`.

## Compatibility change

Individual WAV names and `render_role_stems()` source dictionary keys change:

```text
piano.wav, piano_2.wav  ->  track_0001_piano.wav, track_0002_piano.wav
```

The number is the **zero-based original SMF track index**, padded to at least four
digits. The suffix is the decoded source name (or role if unnamed), NFKD/ASCII
normalized to lowercase letters, digits and underscores, truncated to 48
characters. Empty slugs use `stem`. Names are labels, never paths. Duplicate
names, Unicode normalization collisions, separators, traversal syntax and
Windows reserved names cannot collide because the ordinal is always present.
Unicode metadata is retained in the index as decoded by mido's source charset;
SMF does not declare a universal text encoding.

Use `role_index.json`, not guessed filenames or bare role keys:

- `stems`: ordered rows containing `id`, `source_track_index`, original `name`,
  canonical `role`, `role_source`, `channels`, `note_count`, relative `midi` and
  `wav` paths.
- `roles`: role → list of source IDs (two comping tracks remain separate).
- `generated`: reserved artifact keys → relative filenames.
- `schema_version`: `2` (stem layout, not RenderPackage schema).

Each source has `<id>.mid`, `<id>.wav` and `<id>.wav.json`. The returned dictionary
contains source WAVs plus generated artifacts. Existing generated paths stay:
`click.wav`, `rough_mix.wav`, `bar_boundaries.json`; `role_index.json` is additive.
`click`, `rough_mix`, `bar_boundaries` and `role_index` are reserved generated keys.
The rough mix sums the explicit source-WAV list exactly once each, including a
source named `click`; the generated metronome is excluded.

## Filesystem boundary

Stem creation and cache restoration preflight **all** destination filenames,
including MIDI and JSON sidecars. Existing files, directories, hardlinks and
symlinks (including dangling links) at those destinations are errors, not targets
to replace. Resolved containment is checked and a symlink at the selected stem
output root is refused. Unrelated files in a real output directory are retained.
Choose a fresh output directory for another run; stems are never recursively
deleted merely to restore a cache hit.

Rendering occurs in a private temporary directory. Exclusive hard-link
publication of complete files uses a pinned destination directory descriptor;
files appearing after preflight cannot be overwritten. Ordinary publication
failures remove files published by that invocation. Publication is not
crash-atomic as an entire set; concurrent writers to one output directory are
unsupported. This is a stem-publication guarantee, not a redesign of the other
orchestrator stages. The layout revision invalidates old name-based stem caches;
full renderer/asset/digest cache contracts remain C5 work.

## Evidence boundary

See the [C4 review evidence](audio-testing.md#logical-stem-review-gate-a-c4).
Retained MIDI proves event fidelity only. The unchanged simple renderer still
makes integer-pitch sine previews: it does not certify programs, sustain,
controllers, MPE/MTS tuning or native listening quality. PrettyMIDI can warn when
an imported global signature occurs outside track 0; the retained event MIDI
still preserves that event exactly. Native rendering/capability gates and
listening acceptance are separate later tasks; no SoundFont or listening approval
is claimed here.
