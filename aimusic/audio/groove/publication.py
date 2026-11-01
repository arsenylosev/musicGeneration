"""Stage groove artifacts before rename; never open a destination for writing."""
from __future__ import annotations

import os
import stat
import shutil
import tempfile
from pathlib import Path


def publish_bundle(source: Path, output: Path, payloads: dict[str, bytes]) -> None:
    """Publish complete files with rollback on errors (not crash-atomic as a set).

    All artifacts share one pinned parent directory. Renames replace leaf links,
    rather than following them; existing entries are retained for rollback.
    Concurrent writers to the same bundle are not supported.
    """
    parent = output.parent.resolve(strict=True)
    destinations = [parent / (output.name + suffix) for suffix in payloads]
    checked = [source]
    for destination in destinations:
        for previous in checked:
            if destination.resolve() == previous.resolve() or (
                destination.exists() and previous.exists() and destination.samefile(previous)
            ):
                label = 'source MIDI' if previous == source else 'another output'
                raise ValueError(f'Groove output aliases {label}: {destination}')
        if destination.exists() and not destination.is_file():
            raise ValueError(f'Groove output is not a regular file: {destination}')
        checked.append(destination)

    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    staging: Path | None = None
    retain_recovery = False
    try:
        staging = Path(tempfile.mkdtemp(prefix='.groove-', dir=parent))
        # Serialize/stage everything before touching any public artifact.
        for i, payload in enumerate(payloads.values()):
            with (staging / str(i)).open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        backups: list[tuple[str, Path]] = []
        published: list[str] = []
        try:
            for i, destination in enumerate(destinations):
                name = destination.name
                try:
                    existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if not (stat.S_ISREG(existing.st_mode) or stat.S_ISLNK(existing.st_mode)):
                        raise ValueError(f'Groove output is not a file: {destination}')
                    backup = staging / f'backup-{i}'
                    os.link(name, backup, src_dir_fd=parent_fd, follow_symlinks=False)
                    backups.append((name, backup))
                os.replace(staging / str(i), name, dst_dir_fd=parent_fd)
                published.append(name)
        except BaseException:
            try:
                backed_up = {name for name, _ in backups}
                for name in reversed(published):
                    if name not in backed_up:
                        os.unlink(name, dir_fd=parent_fd)
                for name, backup in reversed(backups):
                    os.replace(backup, name, dst_dir_fd=parent_fd)
            except OSError as exc:
                # Never destroy the only old copy when the filesystem also
                # refuses rollback. Leave the private directory for recovery.
                retain_recovery = True
                raise OSError(f'Groove rollback failed; recovery files retained in {staging}') from exc
            raise
    finally:
        os.close(parent_fd)
        if staging is not None and not retain_recovery:
            shutil.rmtree(staging)
