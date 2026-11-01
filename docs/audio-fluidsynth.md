# Optional offline FluidSynth renderer (C6 partial)

Implemented scope: OpenSpec **7.1–7.3 only**. No native SoundFont render,
12-EDO instrument listening approval, native timing benchmark, or audible tuning
accuracy is claimed. Tasks 7.4/7.5 and MUS-34 acceptance remain open. The real-lane
flag described in `audio-testing.md` is a future acceptance contract, not a test
harness delivered here. Do not interpret mocked tests as native acceptance.

## Clean-room provenance and dependency review (2026-11-01)

- Historical pin: `arsenylosev/midi2audio_generative` at
  `46b3dfea37e15fecb84386ee91912cdeebcd9aec` (`46b3dfe`). The earlier apply-session
  adapter/tests review is recorded in the OpenSpec design §4. Relevant paths were
  `m2a/render/fluidsynth_r.py`, `tests/test_fluidsynth_render.py`, and
  `pyproject.toml`. That review found no license/permission notice. The public
  [recursive tree](https://api.github.com/repos/arsenylosev/midi2audio_generative/git/trees/46b3dfe?recursive=1)
  and [package metadata](https://github.com/arsenylosev/midi2audio_generative/blob/46b3dfe/pyproject.toml)
  were independently checked here: no LICENSE/COPYING/NOTICE or package license.
  Public availability is **not** permission to copy. This implementation did not
  read/copy that adapter or its tests; the pin is historical provenance only.
  The historical review also found a PrettyMIDI solo/delegated render, not a
  lossless event dispatcher. Native tests skipped without prerequisites and
  checked rate/peak, not event ordering, tempo-map sample scheduling or native
  lifecycle. Those findings explain why reuse would not satisfy this contract
  even with permission; they are review evidence, not implementation source.
- `aimusic/audio/render/fluidsynth_r.py` is newly written against this repo's
  `MidiEvents`/renderer contracts and the documented public APIs below. No
  submodule, vendored adapter, subprocess synth, or private synth handle is used.
- Optional distribution **`pyfluidsynth==1.3.4`**, imported as **`fluidsynth`**:
  [PyPI release metadata](https://pypi.org/pypi/pyFluidSynth/1.3.4/json),
  [tagged README/license statement](https://github.com/nwhitehead/pyfluidsynth/blob/v1.3.4/README.md#license),
  [tagged LICENSE](https://github.com/nwhitehead/pyfluidsynth/blob/v1.3.4/LICENSE).
  LGPL-2.1-or-later; copyright Nathan Whitehead and contributors. Only NumPy is
  required by this wheel. No PyAudio/device dependency is added.
  The downloaded wheel was inspected without installation/execution:
  `pyfluidsynth-1.3.4-py3-none-any.whl`, SHA-256
  `c6990329db7cfb35f5e65d523dd4f0c971d928e70df3a6bceec8864827edf246`.
  Its bundled `dist-info/LICENSE` is LGPL-2.1; embedded README specifies “or any
  later version”. PyPI's bare `license` field is null, not evidence of no license.
- Deliberately **not** selecting 1.4.0: its wheel metadata declares MIT while
  `dist-info/licenses/LICENSE` and README still say LGPL. Resolve that upstream
  discrepancy before upgrading. This is not a claim that the newer package lacks
  all licensing, nor a legal opinion.
- Native FluidSynth is a separate LGPL-2.1-or-later shared library; see the
  [official license](https://github.com/FluidSynth/fluidsynth/blob/master/LICENSE).
  It and each SoundFont have their own distribution obligations. We bundle
  neither. Keep notices/source availability obligations when redistributing
  dependencies; an optional extra does not waive their licenses.

### Authoritative API sources

1. [pyFluidSynth v1.3.4 “Managing Audio”](https://github.com/nwhitehead/pyfluidsynth/blob/v1.3.4/README.md#managing-audio):
   create `Synth`, **do not call `start()`**, call `get_samples(n)` for stereo
   interleaved frames, and `delete()` after use.
2. [Tagged public Synth API/docstrings](https://github.com/nwhitehead/pyfluidsynth/blob/v1.3.4/fluidsynth.py):
   constructor settings, `get_setting`, `sfload`, `program_select`, `cc`,
   `pitch_bend`, `noteon`, `noteoff`, `get_samples`, `delete`; public
   `fluid_version` version query. `pitch_bend` takes **-8192..8191**, unlike the
   native C API's unsigned 0..16383. Its semitone amount comes from RPN range,
   not the binding's historical fixed-range example.
3. [FluidSynth MIDI channel API](https://www.fluidsynth.org/api/group__midi__messages.html):
   CC/bank/program behavior and status returns. A `noteoff` failure may merely
   mean no voices matched (a drum/sample already decayed); this status is allowed
   only for already-paired note-offs. Other failed dispatch statuses abort.
4. [Synth settings](https://www.fluidsynth.org/api/settings_synth.html):
   MMA bank selection `CC0*128+CC32`, CPU cores, sample rate, effects and polyphony.
5. [Native audio rendering API](https://www.fluidsynth.org/api/group__audio__rendering.html):
   signed 16-bit frames/dithering. Public `get_samples` returns those samples;
   convert by `/32768` and write FLOAT WAV using existing SoundFile/libsndfile.

## Behavior and evidence limits

- A fresh synth per isolated source stem; no realtime driver/player/sequencer,
  sleeps, background playback, network, or fallback during rendering.
- Absolute tick → rational tempo-map seconds → round-half-even sample position.
  Stable same-tick order; blocks at most 1024 frames split at every event.
  This proves requested dispatch positions, **not** sub-block native acoustic
  latency (FluidSynth has its own internal synthesis buffering).
- Pending bank CCs latch at program changes, using the public `program_select`
  API and **MMA `CC0*128+CC32` on every channel**, including channel **9**.
  With neither bank controller received, program changes use bank 0 on melodic
  channels and bank 128 on channel 9. Explicit zero selects bank 0, not a default.
  CC0/CC32 are independent, channel-local state: an unseen half is zero; a partial
  update retains the other half. Thus CC32=3 alone selects bank 3; CC0=1 plus CC32=3
  selects bank 131; subsequent CC0=2 selects 259, then CC32=4 selects 260.
  Program changes and CC121 do not clear bank state. Bank CCs alone never change
  the current preset: without a program change, the initial note uses program 0
  in the default bank. Channel 9 retains drum keys. Missing exact bank/program
  presets fail; the adapter never substitutes bank 128 for an explicit bank.
- All CCs, including sustain and RPN select/data, are delivered in source order;
  pitch wheels remain signed. No hand-reconstructed RPN approximation.
- SysEx (including MTS/reset) and pressure messages fail before allocation. The
  pinned public `Synth` has no pressure methods; we do not bypass it with private
  handles. Events are still preserved by groove/isolation, not silently discarded.
- The registry advertises **direct_12 only**. Dispatching pitch/RPN in a mock does
  not prove native MPE/19-EDO support; that admission gate remains for C7.
- Fixed settings: gain 0.2, 16 MIDI channels, polyphony 256, one CPU, one stereo
  output/group, reverb/chorus off, minimum note length 0, MMA banks, dynamic sample
  loading off. Public `get_setting` readback must match. Settings/code/native and
  binding versions enter cache identity; sample rate, duration/tail, actual frame
  count, post-render peak gain and hashes enter per-stem sidecars. Preset envelopes
  and supported CC effects still depend on the supplied SoundFont.
- Cleanup is guaranteed for each returned `Synth`, including load, setting,
  dispatch and sample errors. A cleanup exception cannot replace a primary error.
  WAV writing happens after native cleanup. Constructor-internal failures and
  native process crashes remain limitations of the binding, not recoverable Python
  render failures; no native success is published after such a failure.

## Common horizon and rough mix

`render.duration_sec` optionally extends (never shortens) the greater of MIDI EOT
and planner bar extent. Add `render.release_tail_sec` (default **0.25 s**) once;
use `ceil((horizon + tail) * sample_rate)` for every source WAV, mix, and click.
A short backend output is zero-padded; an overlong one is rejected, not truncated.
This fixed tail may cut off long SoundFont envelopes: choose a longer tail when
needed rather than claiming complete decay. Silent declared tracks remain present;
no-note native output is zeroed to avoid treating int16 dither as music.

`render.mix_peak` defaults to **0.95** (linear amplitude, finite and in `(0, 1]`).
Stems are capped before hashing. Sum each source stem once in source order,
including distinct same-role stems, average stereo channels for the existing mono
rough mix, and apply **one attenuation-only gain if needed**. Do not include the
separate click. Quiet content/silence is not boosted by mixing. Simple's legacy
preview normalization remains separate. `headroom_dbfs`/`peak_dbfs` still control
simple preview/click level, not rough-mix normalization. Layout revision 3
invalidates older fixed-normalization/unequal-length caches; filenames are unchanged.

Tests check designated nonempty **active windows** have RMS >1e-5, finite samples,
peak <=0.95, equal frames and fixed tails; exact-zero silent stems, click exclusion,
stereo handling, padding, rate mismatch, deterministic mix bytes and error cleanup
are also covered. These are simple/mock signals, not licensed native instruments,
f0/CLAP/FAD scoring, LUFS mastering, or subjective quality certification.

## Installation: Python, native library and asset are separate

These are local setup instructions, **not commands executed or native success
claimed by this change**. The lightweight lane only needs `.[audio,dev]`.

### Linux (Debian/Ubuntu example)

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[audio-render]"
# Optional native library, installed separately by the user:
sudo apt-get install --no-install-recommends libfluidsynth3
export AIMUSIC_SOUNDFONT=/absolute/path/to/your-approved.sf2
sha256sum "$AIMUSIC_SOUNDFONT"
```

Use your distribution's shared library package on other systems; official
[distribution instructions](https://github.com/FluidSynth/fluidsynth/wiki/Download#distributions).
The package does not acquire a SoundFont. Record the library package version and
user-selected asset's source URL, license text/permission, version and SHA-256.

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[audio-render]"
$env:AIMUSIC_SOUNDFONT = 'C:\Assets\your-approved.sf2'
Get-FileHash $env:AIMUSIC_SOUNDFONT -Algorithm SHA256
```

Separately obtain an official [FluidSynth release](https://github.com/FluidSynth/fluidsynth/releases)
matching Python's architecture, with its dependent DLLs. For pinned pyFluidSynth
1.3.4, place the release's DLLs in **`C:\tools\fluidsynth\bin`**: that binding
unconditionally registers this directory on Windows, so it must exist. Keep DLLs
in trusted locations and run from a trusted working directory (the binding also
searches it). A CLI executable alone is not the required shared library. Do not
install the unrelated PyPI package named `fluidsynth` instead of `pyfluidsynth`.

### Opt-in selection

`load_audio_config('listening')` selects FluidSynth and forbids simple fallback;
use `paths.soundfont` in an explicit profile or `AIMUSIC_SOUNDFONT`. See
[audio-config-skeleton.md](audio-config-skeleton.md) for precedence. The current generated-package CLI
supports `--profile listening --no-validate-only`; output/SoundFont CLI extensions
and the imported-MIDI CLI remain future C9 scope. Missing bindings/library/asset
fail with setup guidance. Native execution defects never trigger a bridge.

**No asset is selected or licensed here.** Obtain user approval before the native
lane/listening tests (7.4/7.5); keep WAVs, SoundFonts and credentials untracked.
