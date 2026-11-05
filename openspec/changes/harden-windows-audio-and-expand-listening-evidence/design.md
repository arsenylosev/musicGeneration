# Design

## Context

See `proposal.md` for motivation and the two capability specs for required behavior. The current code already has strong POSIX publication semantics: groove output uses descriptor-relative replacement with retained backups, while stem/source/cache publication uses a pinned directory, exclusive temporary files, hard-link publication, and inode-owned rollback. Those exact primitives do not transfer to Windows. `os.O_DIRECTORY` and descriptor-relative calls are unavailable, and the current Windows directory handles deliberately deny write sharing, so `os.link()` cannot publish a child. Allowing write sharing would make the operation run but would also permit in-place reparse conversion, weakening containment.

The current listening tool embeds exactly four rough mixes into one HTML file. It validates bounded WAV containers and refuses overwrite, but it neither admits stimuli from machine evidence nor blinds comparisons, records assignments/findings, or links a review to the full render provenance. Existing demo sources and native-integration reports provide useful deterministic inputs, but historical acceptance applies only to the old hashes.

The symbolic/audio boundary, optional dependency quarantine, lazy CLI loading, pinned native evidence, and fork-first contribution path remain constraints. Integrating `origin/main` also adds `aimusic.audio.preview`, whose direct overwrite-capable WAV writer must adopt the same create-only publication policy as other externally selected audio outputs.

## Goals / Non-Goals

**Goals:**

- Preserve the existing security properties through one publication abstraction with separate POSIX and Windows implementations.
- Make supported Windows behavior explicit, native-tested, and fail-closed outside the verified filesystem/runtime envelope.
- Turn native render outputs and reports into a digest-linked, blinded, offline review bundle with a maximum of 14 reviewer exposures.
- Keep objective admission, subjective findings, and unmeasured metrics as distinct records.
- Permit exact reproduction and audit without committing generated audio or licensed assets.

**Non-Goals:**

- Providing hostile multi-user/network-share guarantees, universal filesystem support, or set-wide crash atomicity beyond the documented recovery protocol.
- Adding mastering, paid restyle, new model dependencies, B4 host planning, or the unfinished MUS-36 scorer suite.
- Claiming polyphonic pitch accuracy from the controlled mono tuning check or requiring byte-identical PCM across platforms.
- Replacing the historical four-demo page until the new workflow has generated and passed its own evidence.

## Decisions

### 1. Centralize publication policy and keep platform mechanics separate

Create an internal `aimusic.audio` publication layer used by groove bundles, stems, generated demo sources, MIDI previews, cache restoration, review bundles, and related final artifacts. Shared policy owns portable-name checks, destination classification, ownership records, collision semantics, transaction state, cleanup, and diagnostics. A POSIX backend retains directory-descriptor and no-follow operations. A Windows backend owns native handles and never emulates descriptor safety with resolve/check/use path sequences.

This consolidates currently duplicated assumptions while preserving capability-specific policy: stems/review bundles are create-only; groove bundles may replace a related set with recoverable backups. A single lowest-common-denominator `shutil` implementation was rejected because it cannot preserve no-overwrite, containment, or owned rollback under races.

### 2. Use same-directory retained-handle Windows rename publication, not writable sharing

Native probes showed that `FILE_RENAME_INFO.RootDirectory` does not reuse an exclusively pinned directory handle as mutation authority: Windows reopens the target directory and returns a sharing violation. On supported local NTFS, the backend therefore uses a verified two-stage handoff. It first opens every ancestor without following reparse points, records the publication-directory volume/file identity, and creates complete invocation-private staging and journal leaves directly in that directory with `CREATE_NEW`. It opens existing replacement leaves and all private leaves with the exact read/write/delete access required later, flushes staged bytes, and retains those handles. It then acquires the non-write/non-delete-sharing publication-directory pin and proves that its identity matches the pre-stage identity before any public rename.

While the pin prevents competing child creation, deletion, directory replacement, and reparse conversion, the backend re-enumerates case-insensitive destinations and uses `NtSetInformationFile(FileRenameInformation)` with `RootDirectory = NULL` and a simple leaf name to rename each already-open source handle within its unchanged directory. Create-only publication refuses every existing destination. Replacement first moves already-open old leaves to invocation-owned flat recovery names, then moves complete staged leaves to public names. Journal updates and cleanup use retained handles; each remove/restore operation verifies the recorded file identity. The final public directory never receives an open-for-write public leaf.

The pre-pin phase is not a trust gap: private names are unguessable and create-only, their handles and identities remain retained, and the operation aborts if the publication-directory identity changes before the exclusive pin. No public entry changes before that identity handoff completes. Merely adding `FILE_SHARE_WRITE` to the existing pin was rejected because another handle could convert the directory into a junction. A non-null rename root, path-based `MoveFileExW`, hard links, and copy-to-destination were rejected because native probes showed sharing conflicts or they reintroduce ancestor races/incomplete public files.

If Python/runtime or filesystem behavior cannot supply and verify these semantics, the backend reports an unsupported publication environment. Windows crash residue uses flat invocation-prefixed leaves because the retained-handle rename must stay within one directory; POSIX may continue using a private transaction directory. Both forms include machine-readable ownership/state metadata, and later runs report them rather than guessing that they are safe to remove.

### 3. Model publication as an explicit transaction journal

Each multi-artifact operation records planned leaf names, original identities, staged identities, completed moves, and transaction phase in private state. The private state may be a POSIX transaction directory or flat invocation-prefixed Windows leaves. Normal exceptions replay the journal in reverse and remove only identity-matching entries. If rollback cannot restore an original, its only copy and journal remain in place and the error reports recovery locations. Startup never auto-commits, auto-deletes, or reuses ambiguous residue.

The journal is not presented as crash-atomic durability; it is evidence for deterministic rollback and operator recovery. A database or background recovery service was rejected as disproportionate for local artifact publication.

### 4. Verify Windows in two intentionally separate lanes

Add a required asset-free Windows job that installs a wheel into a path containing spaces and non-ASCII characters and runs native publication, collision, rollback, long-path/Unicode, case-folding, ordinary workflow, and mocked reparse tests. Link tests that need privileges use an explicit capability probe and a non-link substitute for the invariant; they are never hidden by broad platform skips.

Keep FluidSynth/SoundFont execution opt-in because licensed assets and native libraries do not belong in default CI. The pinned lane records OS, architecture, Python, binding and DLL versions, libsndfile, SoundFont digest, commands, outputs, cache results, and same-machine repetition. Simulated Windows unit tests remain useful but cannot satisfy native coverage.

### 5. Build review bundles from an immutable manifest graph

Introduce a versioned review-bundle manifest with: bundle identity; source/render/presentation nodes; SHA-256 digests; effective configuration and tool/runtime/asset identities; objective gate records; panel definitions; opaque stimulus IDs; reviewer assignments; and references to separately stored findings. Canonical JSON serialization makes bundle identity reproducible. Media and public pages contain safe relative paths only; a private directory holds the blind map and provenance that would reveal conditions.

Generation is two-phase: validate and admit every candidate into a private staging tree, then publish the complete directory create-only through the shared publication layer. No partial bundle is called complete. Review findings are append-only external documents keyed to the immutable bundle and assignment; they do not mutate the source manifest.

A single self-contained HTML data-URI page was rejected for the expanded set because it obscures provenance, makes blinding harder, and scales poorly. A server-backed application was rejected because offline portability and zero credentials are requirements.

### 6. Fix the broad review matrix at 14 exposures

The first schema revision uses these panels:

1. Four canonical rough mixes: fixture-12, generated-12, controlled reference-19, and polyphonic multitrack-19 (4 exposures).
2. Four role stems from the fixture-12 render: drums, bass, comping, and lead (4 exposures).
3. A blinded package-versus-import parity pair using musically identical input and normalized presentation (2 exposures).
4. A blinded groove-on/off timing pair using the same source, renderer, configuration, and presentation gain (2 exposures).
5. A blinded valid-19 versus deliberately flattened-12 tuning calibration pair measured against unchanged 19-EDO targets (2 exposures).

Total: 14. Each reviewer sees every exposure once, but A/B order is deterministically balanced from bundle ID plus reviewer pseudonym. The matrix covers the required boundaries without an unbounded corpus. Adding more examples requires a new schema revision rather than silently expanding reviewer load.

### 7. Admission is declarative and metric states are explicit

Every stimulus must satisfy common gates (digest/provenance integrity, expected backend and asset, finite aligned audio, bounded peak, expected non-silence, event/routing/reconciliation checks) plus panel-specific gates. Controlled reference-19 uses the existing intent windows and the DECISIONS.md 15-cent P95 floor, with complete-window and aggregate checks. The flattened control must demonstrably fail those same unchanged targets. Presentation transforms are limited to declared trim/gain operations and produce new digest-linked nodes.

Metrics use `passed`, `failed`, `unmeasured`, or `not_applicable`; missing is invalid. Human forms separately capture audibility/difference, defects, preference, confidence, notes, and completion. Reports retain per-reviewer disagreement and never infer machine metric success from listening responses.

### 8. Keep generation and evidence commands reproducible but asset-neutral

Provide module/CLI entry points under `aimusic.audio` for candidate generation, bundle assembly/verification, assignment creation, finding validation, and unblinded reporting. They consume explicit roots and files; no asset discovery, paid endpoint, or network access occurs during bundle assembly. Documentation supplies PowerShell and POSIX command sequences, prerequisites, expected digests/reports, browser checks, and evidence-recording templates. Generated WAVs, licensed SoundFonts, and private reviewer identities stay out of Git.

## Risks / Trade-offs

- **[Same-directory Windows staging briefly precedes the exclusive pin]** → Retain handles and identities for every private/original leaf, mutate no public name before pin acquisition, require the pre/post directory identity to match, and fail closed on any mismatch.
- **[Native Windows rename details vary by filesystem/runtime]** → Restrict the supported envelope to tested local NTFS, probe required APIs, add native adversarial tests, and fail closed elsewhere.
- **[A platform abstraction could accidentally weaken POSIX behavior]** → Keep backend-specific invariant tests plus existing POSIX race/rollback tests; do not route POSIX through Windows-compatible path operations.
- **[Crash recovery is necessarily more complex than exception rollback]** → Journal every ownership-changing step, retain the only recovery copy, document manual recovery, and never advertise set-wide crash atomicity.
- **[Windows CI cannot create every reparse type without privilege]** → Test ordinary native behavior always, capability-probe privileged cases, and cover invariant decisions with mocked native API responses plus manual pinned evidence.
- **[Fourteen exposures may still cause fatigue]** → Group pages by purpose, randomize only within comparison panels, keep clips bounded, and preserve the fixed cap.
- **[Gain matching can alter what reviewers perceive]** → Store both source and presentation digests and exact transforms; never overwrite or describe transformed files as renderer output.
- **[Blinding can leak through audible content or metadata]** → Remove ancillary WAV metadata, use opaque filenames/page text, isolate the blind map, and document that content-derived inference cannot be eliminated.
- **[A calibrated negative control may be mistaken for product output]** → Keep its identity private during review but explicitly mark it as a synthetic calibration control in unblinded reports.

## Migration Plan

1. Integrate current `origin/main` into the feature branch and run the existing core/audio baseline before changing behavior.
2. Add shared contracts and characterization tests around current POSIX publication, then move callers without changing POSIX outcomes.
3. Implement and native-test the Windows backend, replacing the current path-based Windows stem branch only after collision, containment, rollback, and recovery tests pass.
4. Add the required Windows asset-free CI lane and prove installed-wheel execution.
5. Add the review schema, admission/verification tools, fixed sample matrix, assignments, findings, and documentation while retaining the old four-demo evidence as historical.
6. Run the optional pinned Windows FluidSynth workflow, generate a fresh bundle, complete browser/tamper/repeat checks, and collect new blinded findings. No old approval transfers.
7. Update status documents only with measured results and commit each phase as a sequential Conventional Commit checkpoint.

Rollback is code-level: revert the relevant checkpoint and keep the prior POSIX and historical review path available. Artifact-level rollback never overwrites an existing destination; failed or superseded evidence remains identified by digest, and retained recovery files are removed only after explicit verification.
