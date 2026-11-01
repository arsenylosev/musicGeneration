# Verified renderer and cache boundary (C5)

All contracts live in `aimusic.audio`; core imports and validate-only remain
asset-free. `render_role_stems` first builds a `StemRenderPlan`: isolated source
tracks, canonical roles (`comp` → `comping`), tuning requirements and **resolved**
renderers. Orchestration does this even on a warm cache lookup. There is no
hard-coded simple-render bypass.

## Renderer contract and failure policy

`audio/render/registry.py` defines frozen `RenderRequest`, `RenderResult`,
`RendererCapabilities`, `RendererBackend` and `ResolvedRenderer` records.

- Requests carry event-preserving isolated MIDI, effective tuning, sample rate,
  common duration/release-tail intent, output path, config and resolved asset.
- Providers probe resources without allocating a synth and return backend/code/
  native versions plus capabilities. Future adapters must only classify known
  missing dependencies/assets as `MissingRendererResource`; an arbitrary
  `ImportError`, `OSError` or native runtime exception is **not** a missing resource.
- Routing uses `render.routing.<canonical-role>.default` or `.microtonal`, then
  the global backend for a missing route. Unknown backend names are errors.
- Only an explicit `allow_simple_fallback: true` permits selection-time fallback,
  and only if simple supports the request. `listening` forbids fallback.
- Execution never retries with a different renderer, even if a resource disappears
  after selection. Each result must be a regular WAV at the requested path with
  nonempty finite samples and the requested sample rate. Its SHA-256 is recorded.
- Application backend selection is separate: native is the default;
  `AIMUSIC_AUDIO_BACKEND=m2a` explicitly selects the optional bridge. Native defects
  never trigger bridge failover. Unknown environment values now fail clearly.

The only shipped renderer remains the **simple integer-pitch sine preview**.
Its capability is `direct_12`, not instrument/controller fidelity. The admission
screen rejects non-12/fractional-base metadata, nonzero pitched-channel wheels,
tuning/unknown RPN/NRPN data and unverified SysEx (including MTS), rather than flattening
pitches. Bend-sensitivity setup with centered wheels is accepted (including the symbolic
exporter's 48-semitone range, which does not detune a zero wheel).
Percussion remains drum-key based. This conservative screen is not the later
Score-derived MPE proof or audio f0 measurement.

FluidSynth currently raises an actionable missing-adapter error. No bindings,
native library or SoundFont are installed/downloaded here. Common horizon/tail
fields prepare the adapter contract; native event dispatch, aligned frame counts,
release-tail enforcement and listening headroom remain C6 work. The existing
simple preview's fixed 0.25-second tail is unchanged.

SoundFont selection reuses config precedence (`paths.soundfont`, then
`AIMUSIC_SOUNDFONT`; the resolver also supports explicit CLI input). An explicitly
invalid higher-precedence path is an error, not an excuse to choose another asset.
Asset **bytes**, never just a filename, enter identity, and are rechecked before
execution. No SoundFont is needed for the default preview.

## Identity and provenance

`render.json` at the run root (also in `stems/`) records:

- verified source/package and structure/tuning digests when a package is supplied;
- original and expressive MIDI digests and semantic planner/inferred analysis;
- normalized config/routes, fallback policy and duration/tail settings;
- each source identity/role and its `requested_backend`, `effective_backend`,
  `fallback_reason`, required tuning and capability/backend/code/native versions;
- SoundFont content digests, audio source-code digests and runtime identity.

Per-WAV `.json` files repeat the actual renderer/capability/asset identity and
`output_sha256`, using relative output names. Application backend provenance is
separate from per-stem renderer provenance. Output/cache directory paths, config
source paths and SoundFont display paths are excluded from the fingerprint; equal
asset bytes at a new path can reuse a result. Legacy/canonical equivalent routes
normalize to the same identity. No cross-platform byte-equality claim is made.

## Complete caches, safe restoration

Analysis, expression and render use full SHA-256 directory keys. Producers run in
unique `.pending-*` directories, never the final key directory. Every expected
artifact (including sidecars) must exist as a regular file; the exact output set
and digests are verified before `cache_manifest.json` is written **last** and the
entry directory is atomically renamed into place.

Reuse requires a complete manifest, matching fingerprint/key, exact expected
files and matching SHA-256 for **every** file. Old `_complete` markers, missing or
modified WAVs/sidecars, invalid manifests, and symlink entries are misses. A killed
process may leave a `.pending-*` scratch directory; it is never selected as a
cache hit and may be removed when no producers are running.

Warm copies retain the original bytes of WAVs and all provenance. Every stage
is copied into private scratch and reverified against the requested fingerprint
and every output digest before use. Substituting another valid entry also fails.
The run preflights all artifact names (including package reports and sidecars)
and pins both output directories before stage execution. All computation stays
outside the output tree; complete bundles publish exclusively through the same
pinned mechanism, with the stem cache manifest last. Existing files, symlinks
and hardlink/source aliases are refused, not overwritten; choose a fresh output
directory to rerun. Existing C4 safe-stem naming,
role index and `stems/rough_mix.wav` remain unchanged.

## Asset-free evidence

```bash
python -m pytest tests/test_audio_registry.py tests/test_audio_orchestrator.py tests/test_audio_bridge.py tests/test_audio_stems.py tests/test_audio_stem_publication.py -q
```

Coverage includes canonical role overrides, forbidden tuning/fallback, missing
resources versus execution defects, resource availability changing before warm
lookup, each identity component, same-path SoundFont replacement, valid package
metadata changes with identical MIDI, corrupt/missing sidecars, process death,
invalid WAV results, output collision safety and byte-identical warm restoration.
These are contract/cache checks, **not** FluidSynth or audible tuning acceptance.
