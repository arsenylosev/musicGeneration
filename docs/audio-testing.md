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
