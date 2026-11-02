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
   SHA-256 via the manifest contract below. Once explicitly selected, missing
   bindings/native library/SoundFont or provenance are setup failures, not skips. This lane records native
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
selection, listening gates and C6+ implementation remain outstanding.

## Registry/cache contract checkpoint C5

[Renderer/cache contracts](audio-renderer-cache.md) describe routing, classified
fallback, actual-backend provenance and complete digest manifests. C5 tightens
simple admission: the C4 MPE/MTS event-isolation tests still preserve all payloads,
but simple rendering of those unsupported pitches is now rejected, not flattened.
The C4 multi-channel/silent preview test uses direct-12 channel content instead.

Recorded C5 verification (2026-11-01, existing Python 3.12.12 audio/dev venv):

- Lightweight audio + architecture + RenderPackage: **208 passed**, 224 subtests,
  **1 skipped** optional bridge integration; one retained non-track-0 metadata
  warning from the event-fidelity fixture.
- Full unittest discovery: **541 run**, **9 skipped**, no failures (63.732 s).
- `flake8 aimusic tests ui.py`: clean; `mypy aimusic tests ui.py`: no issues in
  118 files. Strict OpenSpec validation and `git diff --check`: clean.
- TDD reproduced registry bypass, broad bridge fallback, stale/corrupt cache reuse,
  output-report overwrite and valid-entry substitution during copying. An inverted
  capability condition was killed by registry tests and restored.
- A real subprocess exit during cache production left no complete entry; a retry
  produced a new verified result. Existing planner-handoff coverage now wraps
  actual stages instead of pretending missing sidecars are successful output.
- `python -m aimusic.app.cli generate --seed 11 --beats 4 --out <scratch>` followed
  by preview orchestration produced a 48 kHz, 108000-frame rough mix (RMS 0.05272).
  Cold expression/render misses became warm hits; mix bytes were identical.

No native FluidSynth lane, SoundFont, audio-f0 certification or human listening
acceptance is claimed by C5. Only tasks 6.1–6.4 are completed in this checkpoint.

## Clean-room FluidSynth / aligned-mix checkpoint C6 partial

[Provenance, public API sources, licensing and Windows/Linux setup](audio-fluidsynth.md)
cover **7.1–7.3 only**. Bindings are optional `pyfluidsynth==1.3.4`; wheel metadata
checks core requirements remain only `numpy` and `mido`. No pinned-repository
adapter/tests source was copied.

Recorded on 2026-11-01 in the existing Python 3.12.12 audio/dev environment:

```bash
python -m pytest tests/test_audio_fluidsynth.py tests/test_audio_mix.py tests/test_audio_stems.py tests/test_audio_wheel.py tests/test_audio_registry.py -q
python -m pytest tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q
python -m unittest discover -s tests -p 'test_*.py'
flake8 aimusic tests ui.py
mypy aimusic tests ui.py
openspec validate deliver-midi2audio-audible-demo --strict
```

- Focused renderer/mix/stems/wheel/registry: **49 passed**, 85 subtests.
- Config regression after the default-comment update: **23 passed**, 27 subtests.
- Lightweight audio + architecture + RenderPackage: **239 passed**, 290 subtests,
  **1 skipped** optional bridge integration; retained non-track-0 metadata warning.
- Full unittest: **572 run**, **9 skipped**, no failures (61.884 s).
- Lint/type checks, strict OpenSpec validation and `git diff --check`: clean.
- TDD red runs exposed the missing adapter, unequal frames, wrong mix gain and
  overly strict note-off status handling. An inverted peak-gain condition was
  killed by mix tests and restored before the green runs.
- Mocked native calls check tempo changes, absolute fractional-tick sample
  positions, same-tick order, banks/programs/drums, sustain/RPN/bends, fixed tail,
  rejected events/settings, cleanup and no realtime driver. They are **not**
  native sound, pitch, latency or instrument-quality evidence.

### Generation → simple WAV smoke

Reproduce with a fresh local `$SCRATCH` directory (never commit its outputs):

```bash
python -m aimusic.app.cli generate --seed 11 --beats 4 --out "$SCRATCH/generated"
python - "$SCRATCH" <<'PY'
import sys
from pathlib import Path
from aimusic.audio.config import load_audio_config
from aimusic.audio.from_score import from_score
from aimusic.audio.orchestrator import Orchestrator
root = Path(sys.argv[1])
context = from_score(next((root / 'generated').glob('run_*')))
for name in ('cold', 'warm'):
    runner = Orchestrator(load_audio_config('preview').to_dict(), root / 'cache')
    runner.run(context, output_dir=root / name)
    print(name, runner.cache_hits, runner.cache_misses)
PY
```

Measured on macOS arm64, mido 1.3.3, NumPy 2.5.4, PrettyMIDI 0.2.11.post0,
SoundFile 0.14.0. Four stems, click and mix are all **48 kHz / 108000 frames**
(2.0-second horizon + 0.25-second fixed tail), finite and peak <=0.95.

| Signal | Active RMS (0.1–0.8 s) | Peak |
|---|---:|---:|
| Bass | 0.104148 | 0.251189 |
| Comping | 0.039570 | 0.251189 |
| Drums (simple preview, not native percussion) | 0.044356 | 0.251189 |
| Lead | 0.092371 | 0.251189 |
| Rough mix | 0.151110 | 0.719104 |

Whole-mix RMS: **0.150934**. Cold expression/render missed; warm expression/render
hit and all six WAVs were byte-identical. Every effective renderer was `simple`,
with no fallback. This also exercises package reconciliation and publication;
unit tests independently assert click exclusion and exact-zero silent stems.

The local detailed report is `c6-smoke.KwN21L/report.json` under the session's
approved scratch directory. Native FluidSynth/SoundFont rendering, the eight-bar
native benchmark, controlled audio tuning and human listening were **not run**.
Tasks **7.4/7.5 remain unchecked**; MUS-34 completion is not proposed.

## Tuning requirements checkpoint (8.1 only)

`tests/test_audio_tuning.py` covers Score/EDO/base intent, misleading direct-12
labels, channel-local RPN/default/null/partial writes, all nonzero bends, MPE zone
and MTS evidence, percussion exclusion, and pre-groove/pre-publication admission.
See [capability policy and migration](audio-renderer-cache.md). The two cache
metadata tests use an explicitly fake tuning-capable provider; production simple
and FluidSynth still advertise only `direct_12`.

Recorded on 2026-11-01 in the existing Python 3.12.12 audio/dev environment:

- Focused tuning/registry/reconcile/orchestrator: **74 passed**, 112 subtests.
- `python -m pytest tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q`:
  **266 passed**, 342 subtests, **1 skipped** optional bridge test; retained
  non-track-0 metadata warning.
- `python -m unittest discover -s tests -p 'test_*.py'`: **599 run**, **9 skipped**,
  no failures (64.361 s).
- `flake8 aimusic tests ui.py` and `mypy aimusic tests ui.py`: clean (125 files).
- TDD red runs demonstrated range/MTS/Score/metadata-identity gaps and groove
  running before admission. Inverting the RPN default comparison failed tests;
  the original source and its Python bytecode cache were restored before checks.

These are asset-free admission tests, not native tuning/f0 evidence. Only **8.1**
is complete; **8.2/8.3 remain open**, with no MPE capability promotion.

## Controlled tuning checker (9.1–9.2 only)

`aimusic/audio/scoring/tuning_check.py` is a standalone NumPy-only checker. Supply
**original intended Hz**, independently derived from Score/EDO/base tuning or a
known reference sidecar, and half-open sample windows in real mono PCM. Select
steady regions excluding attacks/releases; do not select a new target from the
rendered MIDI/audio. Percussion uses `expected_hz=None, percussion=True` and is
explicitly excluded. There is no channel mixing, automatic window selection,
transcription, dependency/model download, or renderer allocation.

```python
from aimusic.audio.scoring.tuning_check import (
    ToneWindow, TuningCheckConfig, analyze_tuning,
)

# pcm is the caller's normalized mono buffer; intended_hz is original intent.
report = analyze_tuning(
    pcm, 48000, (ToneWindow("reference", 4800, 19200, intended_hz),),
    config=TuningCheckConfig(max_tone_cents=4, max_mean_cents=10, max_p95_cents=10),
)
data = report.to_dict()  # JSON-safe, including failures and exclusions
report.require_pass()   # raises TuningCheckError with .report on rejection
```

- Target-independent normalized autocorrelation compares **interpolated peak
  heights**, avoiding integer-lag bias toward period multiples. Near-best period
  candidates must also have spectral support; a Hann FFT with at least 8× zero
  padding and log-parabolic interpolation refines f0. Search defaults are
  40–2000 Hz, at least four periods/64 samples, centered RMS >1e-5, and
  periodic-similarity confidence >=0.9. Confidence is **not a probability**.
- Inclusive frequency-range admission allows a small interpolation uncertainty:
  **0.002 × sample_rate / window_samples Hz**, or 0.2% of an unpadded Fourier bin
  (0.004 Hz for a half-second window). The report records this bin fraction and
  method `interpolated-autocorrelation+hann-fft-v2`. Actual measured Hz is never
  clamped to a boundary: cents and their unchanged hard floors use that value.
  This margin is not an accuracy guarantee for arbitrary signals.
- Each designated window is one measurement unit: its coverage is 1 when reliably
  measured, otherwise 0. Aggregate coverage is measured / expected **pitched
  windows**, not voiced time or polyphonic-note coverage. Every expected pitched
  window must be measured; empty/percussion-only sets cannot pass.
- Reports retain original targets, measured Hz, confidence, absolute cents,
  sample bounds, settings, exclusions and failure reasons. Missing measurements
  are null; mean/P95 use only available measurements and are null when none exist.
  Partial coverage fails even if its available statistics look good.
- Public result/report constructors validate finite positive measurements/targets,
  finite confidence in [0, 1], settings and consistent missing/percussion reasons.
  The report independently applies its configured minimum confidence and range to
  coverage/statistics and rejects any missing/nonfinite metric. A directly
  constructed low-confidence observation cannot pass or be hidden by good rows.
  Standalone `TuningWindowResult.coverage` indicates measurement presence; report
  coverage (including each serialized row) additionally applies these gates.
- Defaults enforce <=5 cents **for every controlled tone**, plus mean <=15 and
  P95 <=15 cents. `controlled=False` applies only the aggregate gates to the same
  explicit mono windows; it does not enable polyphonic analysis. P95 uses NumPy's
  linear percentile, with equal weight per measured window. Typed config can
  tighten each cents limit down to zero, never relax it. Inclusive comparisons
  allow only 1e-9 cents for floating-point roundoff, not DSP error tolerance.
- This slice does **not** wire the checker into orchestrator publication/cache or
  promote renderer capabilities. A caller must use `require_pass()` before
  claiming a successful validated check; failure preserves its structured report.

Recorded **2026-11-02**, Python 3.12.12 / NumPy 2.5.4, entirely synthetic PCM:

| 19-EDO steps above A4 (1, 4, 7) | Coverage | Mean absolute cents | P95 absolute cents | Result |
|---|---:|---:|---:|---|
| Original intended sine tones | 1.0 | 0.000120 | 0.000147 | pass |
| Nearest 12-TET corruption, same original targets | 1.0 | 42.105396 | 46.842246 | reject |

The corruption tones individually deviate by 36.842224, 47.368563 and 42.105401
cents; none relies on a near-grid choice. `tests/test_audio_tuning_check.py` also
covers exact boundaries, detuning signs/octaves, stronger second harmonics, DC,
44.1/48 kHz buffers, silence, short/truncated/nonfinite/noisy windows, percussion,
partial evidence, strict settings and a bad tone hidden by aggregate statistics.

Initial feature checkpoint verification (existing asset-free audio/dev environment):

```bash
python -m pytest tests/test_audio_tuning_check.py -q
python -m pytest tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q
python -m unittest discover -s tests -p 'test_*.py'
flake8 aimusic tests ui.py
mypy aimusic tests ui.py
```

- Focused: **22 passed**, 90 subtests. Lightweight + architecture + contract:
  **292 passed**, 456 subtests, **1 skipped** optional bridge test; retained
  non-track-0 metadata warning. Full unittest: **625 run**, **9 skipped**, no
  failures (63.410 s). Lint/type checks clean (128 files).
- TDD red runs exposed absent analysis/gates, negative-correlation peak selection,
  extreme-target arithmetic and invalid policy flags. Inverting the cents gate
  failed the suite; original code was restored before final verification.
- Local report/logs: `tuning-check-synthetic.json`, `tuning-check-audio.log`,
  `tuning-check-full.log` under the session's approved scratch directory.

Review corrections verified **2026-11-02**, same asset-free environment/commands:

- Focused checker: **34 passed**, 213 subtests; lightweight/architecture/contract:
  **304 passed**, 579 subtests, **1 optional bridge skip**, retained metadata warning.
  Full unittest: **637 run**, **9 skipped**, no failures (63.672 s). Lint and mypy
  clean (128 files). No task checkboxes changed.
- Red regressions reproduced invalid direct construction, NaN/missing metrics,
  confidence-zero acceptance, all 18 high-19-EDO/fractional-period phase cases,
  and inclusive frequency-boundary failures. Disabling the report confidence
  gate failed the regression suite; original code was restored before full checks.
- Coverage includes `440 * 2**(40/19)` at 16 kHz, periods 8.5/9.5 samples, six
  phases each, and 40/2000 Hz boundaries across six phases and three lengths.
  Values remain identical to estimation with wider bounds. At 0.5 seconds,
  genuinely outside 39.99/2000.01 Hz tones reject despite errors smaller than the
  5-cent floor; stricter cents limits still reject admitted boundary measurements.
- Logs: `tuning-evidence-red.log`, `tuning-fractional-red.log`,
  `tuning-range-red.log`, `tuning-evidence-mutation.log`,
  `tuning-evidence-audio.log`, `tuning-evidence-full.log` in approved scratch.

**Evidence limits:** dry controlled mono tones only. Octave-ambiguous, vibrato,
inharmonic, overlapping or arbitrary instrument audio is unproven; every report
labels polyphonic accuracy **unmeasured**. No native SoundFont/runtime hashes,
19-EDO groove-on/off render, or listening samples were produced. **9.3 and 9.4
remain open**. MUS-35 is still partial: Scala/MTS-helper breadth and pre-endpoint
diffusion gating remain follow-up work, with MUS-34 → MUS-35 → MUS-36 → MUS-37 →
MUS-38 dependencies unchanged. No native acceptance, human listening approval,
CLAP/FAD or production-quality claim is made.

## Final asset-free verification (11.1)

Re-run on 2026-11-02 with Python 3.12.12 and no FluidSynth library, SoundFont,
GPU, credentials, or live service required:

```bash
python -m pytest tests/test_architecture.py tests/test_render_package.py -q
python -m pytest tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q
python -m unittest discover -s tests -p 'test_*.py'
python -m pytest tests/test_audio_wheel.py -q
flake8 aimusic tests ui.py
mypy aimusic tests ui.py
openspec validate --all --strict --no-interactive
```

- Core architecture/contract: **17 passed**, 13 subtests.
- Lightweight audio/architecture/contract: **340 passed**, 642 subtests;
  **1 skipped** optional bridge test.
- Full unittest discovery: **673 run**, **9 skipped**, no failures (79.701 s).
- Isolated wheel/profile/demo-source smoke: **1 passed**.
- Flake8, mypy (133 source files), and strict OpenSpec validation: clean.

These results complete only the asset-free cross-lane check. Skips are not native
evidence. Tasks 1.3, 7.4–7.5, 8.2–8.3, 9.3–9.4, 10.3–10.4 and 11.2–11.4 remain
open pending an approved SoundFont, native FluidSynth evidence, complete four-run
artifacts, and human listening review.

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

## Native opt-in manifest contract and 12-EDO evidence (2026-11-02)

`tests/test_audio_fluidsynth_integration.py` skips **only** when
`AIMUSIC_RUN_FLUIDSYNTH_TESTS` is not exactly `1`. Set
`AIMUSIC_FLUIDSYNTH_MANIFEST` to an external UTF-8 JSON object. All fields below
are required and nonempty; `license_approved` must be boolean true. The asset must
be an absolute existing local file, and its actual bytes must match the lowercase
64-digit SHA-256. Source commit must be a full lowercase commit hash. Source and
license URLs must be HTTPS. Native/binding versions must match runtime queries.
Missing binding/library/path/license/expected hash, modified asset bytes or wrong
versions **fail** the selected lane, never skip. The manifest records the user's
approval; it cannot independently establish legal ownership of samples.

Example (replace only the local path with the already-approved external asset):

```json
{
  "soundfont_path": "/absolute/local/path/GeneralUser-GS.sf2",
  "soundfont_sha256": "9575028c7a1f589f5770fccc8cff2734566af40cd26ed836944e9a5152688cfe",
  "soundfont_name": "GeneralUser GS",
  "soundfont_version": "2.0.3",
  "source_url": "https://github.com/mrbumpy409/GeneralUser-GS",
  "source_commit": "684543d5e5efaef08d02be50dcda8d552478fa60",
  "license_url": "https://github.com/mrbumpy409/GeneralUser-GS/blob/684543d5e5efaef08d02be50dcda8d552478fa60/documentation/LICENSE.txt",
  "license_summary": "Private/commercial music and software-project use/modification permitted; historical sample-origin uncertainty disclosed; local-only approval",
  "license_approved": true,
  "native_version": "2.6.2",
  "binding_version": "1.3.4"
}
```

```bash
# Activated audio-render/dev venv. No download, service key or GPU.
export AIMUSIC_RUN_FLUIDSYNTH_TESTS=1
export AIMUSIC_FLUIDSYNTH_MANIFEST="$LOCAL_ASSETS/native-manifest.json"
export AIMUSIC_FLUIDSYNTH_ARTIFACTS="$LOCAL_OUTPUTS"  # existing external directory
python -m pytest tests/test_audio_fluidsynth_integration.py -q -s
# Default/asset-free lane, including subprocess proof of fail-closed opt-in:
unset AIMUSIC_RUN_FLUIDSYNTH_TESTS AIMUSIC_FLUIDSYNTH_MANIFEST AIMUSIC_SOUNDFONT
python -m pytest tests/test_audio_native_contract.py tests/test_audio_*.py tests/test_architecture.py tests/test_render_package.py -q
```

`AIMUSIC_FLUIDSYNTH_ARTIFACTS` is optional (defaults to OS temp); each run prints a
fresh `fluidsynth-evidence-*` directory and retains source/expected JSON, actual
asset manifest, config/backend provenance, original/expressive/isolated MIDI,
WAVs, separate caches and reports. No machine-specific path is tracked.

First passing native checkpoint: `fluidsynth-evidence-wnd_i84v` in local scratch.
All **780000 frames / 48 kHz**: 16-second eight-bar horizon + fixed **0.25 s** tail.
Roles/programs/channels/counts match `demo_sources fixture-12` exactly:
drums 0/9/96, bass 33/0/16, comping 0/1/48, lead 80/2/64 (zero-based).
Source and isolated note endpoints/velocities match; the no-op expression path is
not claimed as audible groove improvement. Click is generated but excluded by an
exact reconstructed-mix assertion. Every designated active window passes >1e-5;
all samples finite and peaks <=0.95.

| Signal | RMS, 0.1–0.4 s | Peak |
|---|---:|---:|
| Drums | 0.000967488 | 0.064514160 |
| Bass | 0.014692156 | 0.041717529 |
| Comping | 0.012433238 | 0.054138184 |
| Lead | 0.010266098 | 0.033386230 |
| Rough mix | 0.021610964 | 0.118316650 |

Cold **0.931 s**, independent cold-repeat **0.852 s**, warm **0.354 s**;
all six WAVs byte-identical across runs. Both cold runs miss expression/render
caches, warm hits both. The test retains timings and fails above **60 s**, rather
than relaxing the target. Native/binding/asset provenance is checked on each run.

**7.5 remains open**: samples are available for listening, not reviewed by a human.
No MUS-34 completion, role recognizability, mastered quality, CLAP/FAD, or broad
polyphonic tuning certification follows from these signal measurements.
