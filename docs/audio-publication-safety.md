# Audio output publication safety

aimusic.audio.publication is the publication boundary for externally selected audio outputs. It stages complete regular files privately on the output filesystem and fails closed when required primitives are unavailable. This is a trusted-local-output protocol, not protection from an attacker able to mutate the private transaction directory.

## Modes and portable names

- **Create-only** (replace=False) covers stems, final mixes, previews, generated sources, and cache restores. Every public leaf must be absent. Existing files, directories, links/reparse entries, and differently-cased casefold aliases are collisions. Atomic no-replace publication exposes either no leaf or the complete staged bytes.
- **Transactional replacement** (replace=True) is reserved for related groove MIDI/sidecar bundles. Every old leaf is retained privately before the first replacement. Complete new leaves are replaced one at a time; backups are discarded only after the caller accepts the publication and identities are revalidated.

Names must be single nonempty portable leaf components: no dot or dot-dot components, separators, traversal, controls, Windows-forbidden characters, reserved device basenames (including COM/LPT superscript forms), trailing space/dot, or reserved publication prefix. Duplicate names and Unicode casefold aliases are rejected within a plan and against existing entries, even on case-sensitive filesystems. Inputs and outputs must not alias by resolved path or file identity.

## Pinned containment and no-follow behavior

On currently supported POSIX hosts, every output ancestor and the output directory is opened with O_DIRECTORY | O_NOFOLLOW. Descriptors stay open through publication, rollback, and cleanup, while (st_dev, st_ino) identities are rechecked. Leaf stat, open, hard-link, replace, unlink, and private-directory operations are descriptor-relative and do not follow leaf links. Staged inputs must be regular files opened with O_NOFOLLOW; the selected output root cannot be a symlink.

This requires a local POSIX filesystem/runtime with those flags, directory-relative operations, same-filesystem hard links, atomic leaf replacement, and meaningful device/inode identities. Unsupported primitives produce an actionable error; no path-copy or overwrite-capable fallback is used.

## Immutable ownership, phases, and exception rollback

FileIdentity, Ownership, and Journal are frozen records. Journal updates create new values. Canonical JSON records the plan; original, staged, backup, and published identities; and these normal phases:

preparing -> prepared -> publishing -> published -> committed

Exception handling records rolling_back; incomplete rollback records recovery when possible. Journal writes are flushed and fsynced, but interruption can leave a partial or stale record.

Any pre-commit exception, including one raised by the caller inside the context, replays ownership in reverse. A new public leaf is removed only while its identity matches. An original is restored only if its destination is absent or still has the identity published by this invocation. Unrelated and substituted entries are preserved. If restoration fails, publication fails and retains journal and recovery backups. A backup that may be the only old copy is never deleted merely to make cleanup succeed; the error identifies its transaction directory.

## Crash residue and explicit recovery

Process exit and power loss bypass exception rollback. Any inactive .stems-*, .groove-*, or .publication-* entry is ambiguous residue. Later pin_output() calls report exact names and refuse all publication; they never reuse, commit, restore, or delete residue automatically.

Recovery is an explicit operator decision:

1. Stop writers and preserve or copy the complete output tree.
2. Inspect each reported transaction directory. Treat journal.json as possibly truncated or one ownership step behind; inventory public leaves and private staged/backup-* entries by no-follow identity and content.
3. Reconcile the inventory with original, staged, backup, and published ownership records. Never overwrite an unrelated or identity-mismatched public leaf. Preserve every backup that may be the only old copy.
4. Explicitly choose and validate one coherent state: restore verified old leaves, or retain verified complete new leaves. A mixed groove bundle is not complete.
5. Archive evidence as needed, then manually remove only verified transaction-owned residue. Publication stays blocked until every ambiguous private-prefix entry is resolved.

There is no automatic recovery command because a damaged or partial journal cannot safely choose between old and new operator data.

## Non-atomic crash boundary and Windows support

Each public leaf has complete-file visibility, but a related set is **not** a single crash-atomic filesystem transaction. During replacement or after a crash, a set may contain old leaves, new leaves, absent leaves, and private backups. Normal exceptions provide rollback; process/filesystem crashes do not. A committed journal with failed cleanup is also residue to inspect, not permission to roll back after old copies may have been discarded.

Windows publication is supported only on writable, fixed, local NTFS volumes. UNC, network/device, read-only, reparse-point, and non-NTFS output paths fail closed with `Publication requires ...` diagnostics. No fallback attempts to approximate the contract.

The Windows backend reserves empty private leaves with `CREATE_NEW`, retains their handles, pins and revalidates the publication-directory identity and reparse state, then writes and flushes content only through those handles. Public names are created by same-directory retained-handle atomic no-replace renames. Directory pins do not lock the child namespace, so case-fold checks, atomic collision refusal, and identity-owned rollback handle competing child changes.

`FILE_SHARE_WRITE` on directory pins is rejected because it permits directory replacement or reparse conversion. Path-based copy, hard-link, `MoveFileExW`, and overwrite-capable rename fallbacks are rejected because they reopen ancestor races, expose partial public bytes, conflict with the pin, or can replace unrelated entries. Windows residue uses flat invocation-prefixed leaves; it is ambiguous after interruption and is never deleted automatically. Use the same explicit inventory and recovery procedure above, preserving every possible only copy.

The original `WinError 32` hard-link conflict and unavailable `os.O_DIRECTORY` route are regression-tested through native retained-handle publication. This native support does not make a related multi-file set crash-atomic, and native CI evidence remains distinct from optional FluidSynth renderer evidence.

## POSIX verification

From the repository root in the project dev environment, on a POSIX host:

    python -m pytest tests/test_audio_publication.py tests/test_audio_publication_records.py tests/test_audio_stem_publication.py -q
    python -m pytest tests/test_audio_groove.py tests/test_audio_stems.py tests/test_audio_demo_sources.py tests/test_audio_imported.py tests/test_audio_orchestrator.py tests/test_audio_mix.py -q

No FluidSynth, SoundFont, GPU, credentials, or network is needed. The first suite covers policy, residue, rollback, and adversarial POSIX mechanics. The second verifies migrated groove, stem/mix, source/preview, orchestration, and cache-facing workflows.



