# Audio pipeline testing and risks

From `AUDIO_PIPELINE_ARCHITECTURE.pdf` §§8–9.

## Testing strategy

### Verification lanes

1. **Core / architecture (required):** run the symbolic suite, contract tests,
   lint, and type checks with no audio-native dependencies loaded.
2. **Lightweight audio (required):** run config, MIDI-event, reconciliation,
   cache, safety, and mocked-renderer tests with the CI-safe `simple` backend.
   This lane requires no FluidSynth installation, SoundFont, GPU, credentials,
   or network service.
3. **Real FluidSynth (explicit opt-in):** set
   `AIMUSIC_RUN_FLUIDSYNTH_TESTS=1` and provide the approved SoundFont path and
   SHA-256. Once explicitly selected, missing FluidSynth bindings/native
   library/SoundFont are setup failures, not skips. This lane records native
   versions, asset license/hash, signal metrics, tuning evidence, and elapsed
   time.

The real lane must use a locally supplied, license-approved SoundFont selected
by the reviewer. The repository does not download, bundle, or commit one.

| Component | Test approach |
|-----------|---------------|
| `render_package` | Contract invariants on every fixture; schema validation |
| `analysis/reconcile` | Planner vs inferred analysis on own MIDI, per-field tolerances |
| `groove/apply` | Hypothesis: offsets within 1 tick; note-count rules; velocity idempotence |
| Metrics | Monotonicity under synthetic corruption; failing metrics leave the objective |
| `tuning_check` | Pure 19-EDO tones ≈ 0¢; 12-TET flattening exceeds threshold |
| `chunking` | Seams on downbeats; equal-power; unmodified reassembly sample-identical |
| Endpoint wrappers | vcrpy record/replay; never hit paid APIs in default CI |
| Orchestrator | Cache hit ⇒ bit-identical; budget abort reports best-so-far |
| Architecture | Import-direction + core-import-weight tests |
| E2E | 8-bar fixture through M1+M2 in CI, fluidsynth/simple, under ~60s |

Human audit: HTML report per run (stems, spectrograms, metrics, prompts).

## Package integrity / reconciliation regression lane

```bash
python -m pytest tests/test_render_package.py tests/test_audio_entry.py tests/test_audio_reconcile.py tests/test_architecture.py -q
```

These tests need no native synth or SoundFont. Contract tests corrupt each
payload, exercise aggregate-only v1 packages and aliases, and verify that a
reused directory is not trusted or overwritten. New manifests identify the
file and expected/actual SHA-256; legacy failures identify the aggregate's
input group instead (see [contract](render-package-contract.md)).

Reconciliation fixtures check original MIDI before groove: exact discrete
fields, inclusive **0.001 BPM** and **one local source tick** tolerances,
complete tempo/meter maps, per-track EOT, and trailing planner silence distinct
from Score occupancy. A multi-channel 19-EDO fixture verifies logical source
track counts, not audible tuning. Unknown name-derived roles are explicitly
unavailable; declared planner roles remain authoritative. Program/count/drum
mismatches fail even with valid package hashes. Warm-cache and stale-context
tests prove validation cannot be bypassed; validate-only is checked in a fresh
process for absence of synth/DSP imports.

This lane does not certify downstream expressive-MIDI fidelity, recognizable
instruments or f0 accuracy. Those require the later event/render/tuning checks
and the separately enabled native lane.

## Event-preserving groove regression lane

The implemented [event/groove semantics](audio-midi-events.md) have asset-free
round-trip, tempo-map, expression, sustain-conflict and independent 19-EDO pitch
reconstruction tests. These are MIDI-event checks, not native audio measurements.

```bash
python -m pytest tests/test_audio_midi_events.py tests/test_audio_midi_scaling.py tests/test_audio_groove.py tests/test_architecture.py -q
```

### Deterministic scaling guard

`test_audio_midi_scaling.py` profiles module operation counts, not elapsed time,
for approximately 2k/4k source events (500/1000 notes, bends, CC74, tempo changes).
Parsing, retiming and one ghost per note must each grow by at most **2.7×** when
input doubles. Groove anticipation separately counts event visits to guard meter
lookups. Batched ghosts are also compared with incremental insertion, and the
expression sweep is checked against the lifetime-based association definition.

Measured with Python 3.12 during review remediation:

| Phase | Before 2k → 4k calls | Indexed 2k → 4k calls |
|-------|---------------------|-----------------------|
| Parse | 510,546 → 2,021,086 | 9,548 → 19,088 |
| Retime | 3,625,270 → 14,300,500 | 93,297 → 186,547 |
| Ghost batch | not used as a timing gate | 104,045 → 208,175 |

These are deterministic regression signals, not synth throughput or audio-quality
claims. Work still scales with actual simultaneous voice/event intersections;
unbounded sustained polyphony is not promised linear cost.

## Logical-stem review gate A (C4)

[Layout and migration](audio-stems.md) documents source IDs, role lookup,
reserved artifacts and refusal of existing output paths. Review evidence is
asset-free and concerns **MIDI events**, not native listening or audio f0:

| Regression evidence (`tests/test_audio_stems.py`) | Assertion |
|---|---|
| Multi-channel logical track plus drums | Two stems, all member-channel notes retained; authoritative `comp` → `comping`, independent of a misleading name |
| Serialized isolated MIDI | Exact absolute ticks, source-track slots, event payloads and global streams; banks/programs, RPN range, bends, sustain, aftertouch and opaque MTS payload retained |
| Sustain / tempo | Both held notes release at tick 700; tempo/meter changes at tick 480 unchanged; drum keys remain on channel 9 |
| Independent channel-state oracle | Full before/after program, bank, pedal and pitch-offset trajectories equal (12.5-semitone RPN range included) |
| Three 19-EDO tones through retiming + serialization/isolation | Correct note identities/counts; each reconstructed onset within **1 cent** of independent EDO targets |
| Cross-track ownership | Conflicting notes/programs/bends/CCs fail instead of duplicating or silently changing channels |
| Artifact names and mix | 13 duplicate/reserved/Unicode/separator/traversal/long labels remain unique and indexed; mix equals normalized sum of every source WAV |
| Pre-existing artifacts | Seven output kinds × file/directory/inside/outside/dangling-link cases refused without modification; symlink root refused |
| Integration | Silent stem retained; all MPE members feed simple preview; cold/warm cache copies retain index and unrelated files, refuse collisions |

Reproduce with the project's lightweight audio/dev environment:

```bash
python -m pytest tests/test_audio_stems.py -q
python -m pytest tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q
python -m unittest discover -s tests -p 'test_*.py'
flake8 aimusic tests ui.py
mypy aimusic tests ui.py
```

Recorded on 2026-11-01 with Python 3.12.12, mido 1.3.3, NumPy 2.5.4,
PrettyMIDI 0.2.11.post0 and SoundFile 0.14.0:

- Focused stems: **18 passed**, 44 subtests.
- Lightweight audio + architecture + RenderPackage: **173 passed**, 172 subtests;
  **1 skipped** optional `audio-bridge` test (not installed).
- Full unittest discovery: **506 run**, **9 skipped**, no failures (60.840 s).
- `flake8 aimusic tests ui.py`: clean; `mypy aimusic tests ui.py`: no issues in
  115 files; strict OpenSpec validation and `git diff --check`: clean.
- One retained PrettyMIDI warning identifies the deliberate non-track-0 key
  signature fixture; exact global-event preservation is separately asserted.
- TDD red runs reproduced missing isolation, unsafe/reserved names and overwrite
  behavior before fixes. An inverted ownership guard was killed by regression
  tests; the guard was restored before final verification.

This evidence is presented for human review of event fidelity (task 5.3). It is
**not** human approval, native instrument recognizability, monophonic/polyphonic
f0 measurement, CLAP/FAD scoring or a mastered-audio quality claim. Native asset
selection, listening gates and C5+ implementation remain outstanding.

## Risks

| Risk | Mitigation |
|------|------------|
| Diffusion pulls 19-EDO → 12-TET | B3 routing; f0-following; tuning floor; skip-restyle default |
| Endpoint terms/pricing change | RestyleEndpoint protocol ≥2 impls; local MusicGen for search logic |
| Spend overruns | Budget guard; open-weight for broad search |
| madmom/demucs install pain | Extras groups; light deterministic spine; pin/containerize heavy path |
| Symbolic blockers | Fixture corpus + contract decoupling |
| Invalid LLM groove specs | pydantic validate; reject-and-retry; measured templates override |
| Scorer gaming | Hard floors; monotonicity tests; milestone human audits |
| Rights / watermarks | Provenance manifest; MIDI-GPT flagged off by default |
