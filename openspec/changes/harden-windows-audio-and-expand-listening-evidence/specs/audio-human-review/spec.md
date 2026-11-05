# Spec Delta

## Purpose

Provide bounded, reproducible human-listening evaluations whose exact audio stimuli, objective admission evidence, blinding assignments, and reviewer findings remain traceable without implying unsupported quality claims.

## ADDED Requirements

### Requirement: Bounded representative review set
The review workflow SHALL produce a versioned, bounded set covering canonical mixes, role stems, package/import parity, a meaningful timing comparison, a valid-versus-flattened tuning control, and the supported polyphonic microtonal boundary.

#### Scenario: Complete broad review generation
- **WHEN** approved native prerequisites are available and every required source renders successfully
- **THEN** the bundle contains all declared panels with stable stimulus identities and no more than the configured reviewer exposure limit

#### Scenario: Required panel cannot be produced
- **WHEN** any required stimulus fails rendering or admission
- **THEN** no complete review bundle is reported and the failed panel remains explicitly identified

### Requirement: Objective admission before presentation
Every presented stimulus SHALL pass its applicable integrity, provenance, renderer, signal, reconciliation, event, and controlled-tuning gates before entering a reviewer-visible page.

#### Scenario: Admitted direct-12 stimulus
- **WHEN** a direct-12 render has verified source identity, expected backend and asset, preserved event/routing evidence, finite aligned audio, non-silence where expected, and bounded peak
- **THEN** the stimulus may be included with its gate evidence and exact WAV digest

#### Scenario: Controlled tuning stimulus
- **WHEN** a controlled 19-EDO reference has complete measurable windows and satisfies the configured per-tone and aggregate cent-error floors
- **THEN** it may be included as the valid member of the tuning comparison

#### Scenario: Flattened tuning control
- **WHEN** a deliberately flattened control is measured against the unchanged original frequency targets
- **THEN** it is included only if it fails the acceptance floors as expected and is labelled privately as a calibration control

#### Scenario: Unmeasured metric
- **WHEN** a metric such as CLAP, FAD, LUFS, rhythm F-score, note F1, or polyphonic f0 has not been implemented and run
- **THEN** the bundle records it as `unmeasured` rather than absent, passed, zero, or inferred from human feedback

### Requirement: Blinded comparison presentation
Comparison panels SHALL use opaque stimulus identifiers and reviewer-specific balanced ordering so that labels, file names, paths, and page text do not reveal the intended control or production route.

#### Scenario: Reviewer opens a comparison page
- **WHEN** a reviewer receives a generated assignment
- **THEN** the page presents opaque IDs in that assignment's order and excludes the private blind map and source-revealing metadata

#### Scenario: Review closes
- **WHEN** all intended reviewer findings are finalized
- **THEN** an authorized unblinded report may map findings to stimuli while preserving the original assignments and hashes

### Requirement: Structured human findings
The workflow SHALL collect findings separately from machine evidence and SHALL link each response to the bundle, assignment, reviewer pseudonym, stimulus or pair, and exact media digests.

#### Scenario: Reviewer records a pairwise judgment
- **WHEN** a reviewer evaluates an A/B pair
- **THEN** the finding records difference perception, defects, preference, confidence, optional notes, and completion state without modifying the source bundle

#### Scenario: Incomplete form
- **WHEN** required judgments are missing or a finding references an unknown or changed stimulus
- **THEN** the finding remains incomplete or is rejected and cannot contribute to acceptance totals

### Requirement: Immutable provenance graph
The review bundle SHALL provide machine-readable links from each stimulus to its source, render, presentation transform, effective configuration, backend, asset, runtime, applicable reports, and cryptographic digests.

#### Scenario: Stimulus verification
- **WHEN** a recipient verifies a stimulus from the bundle
- **THEN** its bytes and every referenced evidence document match the declared digest chain or verification fails

#### Scenario: Presentation gain is applied
- **WHEN** a clip is gain-matched, trimmed, or otherwise transformed for presentation
- **THEN** the transform parameters, source WAV digest, output digest, and non-destructive relationship are recorded

### Requirement: Portable bounded delivery
The workflow SHALL create an offline review bundle with bounded media, safe relative paths, exclusive output creation, and pages grouped by review purpose rather than embedding an unbounded corpus in one document.

#### Scenario: Portable extracted bundle
- **WHEN** a recipient opens the bundle using the documented local method
- **THEN** all admitted media and pages are available without network access, credentials, repository checkout, or the licensed SoundFont

#### Scenario: Existing or unsafe output
- **WHEN** the selected bundle destination exists, aliases a source, escapes containment, or contains a disallowed link or reparse point
- **THEN** generation fails without overwriting the existing entry

### Requirement: Claims remain scoped to new evidence
Generated reports SHALL distinguish objective gates from subjective findings, retain disagreements, and SHALL NOT transfer historical acceptance to new audio hashes or describe rough mixes as mastered or production quality.

#### Scenario: New render after earlier acceptance
- **WHEN** any stimulus bytes, source, configuration, backend, runtime, asset, or presentation transform change
- **THEN** prior human acceptance is historical only and the new stimulus begins with no human approval

#### Scenario: Multiple reviewers disagree
- **WHEN** reviewers provide conflicting defect or preference judgments
- **THEN** the report presents reviewer counts and individual linked findings without converting disagreement into unanimous acceptance

### Requirement: Windows review evidence
The canonical and broad review workflows SHALL be reproducible on native Windows with platform-specific provenance while semantic gates remain comparable across supported operating systems.

#### Scenario: Windows bundle generation
- **WHEN** the pinned Windows native-render lane completes using paths with spaces and non-ASCII characters from an installed wheel
- **THEN** it produces a verifiable review bundle and records OS build, architecture, Python, native DLLs, binding, libsndfile, SoundFont, commands, browser checks, and same-machine repeat evidence

#### Scenario: Cross-platform comparison
- **WHEN** Windows and another supported platform render the same declared sources
- **THEN** reports compare semantic gates and provenance without requiring cross-platform PCM or WAV byte equality
