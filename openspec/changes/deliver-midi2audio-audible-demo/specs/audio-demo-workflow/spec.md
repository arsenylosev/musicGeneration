# Spec Delta

## Purpose

Let users reproduce and review audible MIDI examples without making optional native audio tools or licensed assets prerequisites for the symbolic application.

## ADDED Requirements

### Requirement: Generated and imported entry paths
The CLI SHALL support generated RenderPackages through `render-audio` and supported imported MIDI through `render-midi`. Both SHALL expose output-directory, profile and SoundFont options. Existing `render-audio` validate-only default SHALL remain unchanged; imported MIDI rendering SHALL require explicit full-render selection with the same convention.

#### Scenario: Generated package remains validation-first
- **WHEN** a user invokes `render-audio` without explicitly disabling validate-only
- **THEN** input validation succeeds or reports diagnostics without allocating a synthesizer or writing WAVs

#### Scenario: Explicit imported rendering
- **WHEN** a user invokes `render-midi` with a supported source, listening profile, output directory, usable SoundFont and `--no-validate-only`
- **THEN** the command produces named stems and `stems/rough_mix.wav` under that output directory and reports artifact locations

### Requirement: Preserve optional audio quarantine
Core installation and symbolic imports SHALL remain free of audio DSP and native-render dependencies. Audio commands SHALL load audio functionality lazily, and paid restyle and MIDI-GPT SHALL remain disabled. The demo workflow SHALL require no network service or credentials.

#### Scenario: Core-only use
- **WHEN** a user installs only core dependencies and generates symbolic MIDI
- **THEN** generation succeeds without importing optional audio libraries or requiring FluidSynth, SoundFonts or service keys

### Requirement: Reproducible listening set and provenance
The demo SHALL provide reproducible sources and commands for the eight-bar 12-EDO fixture, a freshly generated 12-EDO passage, controlled 19-EDO reference tones and a short supported 19-EDO multitrack passage. Each run SHALL retain source MIDI, available package metadata, optional expressive MIDI, stems, mix, effective configuration, renderer/asset hashes and applicable reconciliation/tuning reports.

#### Scenario: Reproduce a listening run
- **WHEN** documented commands run in a pinned environment with the same seed and approved SoundFont bytes
- **THEN** the resulting listening set includes the specified examples and enough provenance to identify their sources, effective routes and applicable validation results

### Requirement: Separate verification lanes
Default verification SHALL run without native FluidSynth, SoundFonts, GPUs or live keys. An explicitly enabled real-render lane SHALL test FluidSynth output and controlled 19-EDO tuning using a pinned license-approved SoundFont. A human listening audit SHALL record role recognizability and gross timing/groove findings without claiming production quality.

#### Scenario: Asset-free CI
- **WHEN** default CI runs without FluidSynth or SoundFonts
- **THEN** core, package, event-preservation, cache, configuration, mocked renderer and input-safety tests run without native assets

#### Scenario: Real lane explicitly requested
- **WHEN** the real-render lane is explicitly enabled
- **THEN** missing assets fail setup rather than silently skip acceptance, and successful runs report sample rate, duration, RMS, peak, tuning evidence and elapsed time

### Requirement: Keep externally licensed and generated assets local
Reproduction documentation SHALL distinguish Python bindings, native FluidSynth and user-supplied SoundFont installation, include Windows/Linux setup guidance, and record SoundFont provenance requirements. SoundFonts, generated WAVs, credentials and large model assets SHALL NOT be committed as part of this milestone.

#### Scenario: Delivery review
- **WHEN** the demo is submitted for review
- **THEN** versioned changes contain fixture sources, profiles, tests, expected metrics and reproduction instructions while licensed rendering assets and generated listening files remain outside the commit
