"""Exclusive stem publication with a directory pinned for the whole operation.

POSIX mutations are descriptor-relative. Windows pins every path component with
non-write/non-delete-sharing handles before using portable path-based I/O.
Renderer scratch space must be outside the output tree; complete files are then
copied to exclusive, descriptor-relative staging files on the output filesystem.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator
from uuid import uuid4


class _FileInformation(ctypes.Structure):
    # BY_HANDLE_FILE_INFORMATION: DWORDs (including three FILETIME pairs).
    _fields_ = [('attributes', ctypes.c_uint32), ('times', ctypes.c_uint32 * 6),
                ('volume', ctypes.c_uint32), ('size_high', ctypes.c_uint32),
                ('size_low', ctypes.c_uint32), ('links', ctypes.c_uint32),
                ('index_high', ctypes.c_uint32), ('index_low', ctypes.c_uint32)]


def _windows_api() -> Any:
    from ctypes import wintypes
    api = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def _pin_windows_directory(path: Path) -> Iterator[None]:
    api = _windows_api()
    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES (metadata-only opens do not
    # enforce sharing), FILE_SHARE_READ (NOT WRITE/DELETE), OPEN_EXISTING,
    # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT.
    # https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew
    handle = api.CreateFileW(str(path), 0x81, 0x1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise getattr(ctypes, 'WinError')()
    try:
        info = _FileInformation()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise getattr(ctypes, 'WinError')()
        if info.attributes & 0x400 or not info.attributes & 0x10:
            raise ValueError(f'Stem output directory is a reparse point or not a directory: {path}')
        yield
    finally:
        api.CloseHandle(handle)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _validate_name(name: str) -> None:
    if (not name or name in ('.', '..') or any(c in name for c in '/\\:\x00') or
            name[-1] in ' .' or PureWindowsPath(name).is_reserved()):
        raise ValueError(f'Stem output violates containment or portable filename rules: {name}')


@dataclass(frozen=True)
class PinnedOutput:
    selected: Path
    path: Path
    fd: int | None
    identity: tuple[int, int]

    def verify(self) -> None:
        for path in {self.selected, self.path}:
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or _identity(info) != self.identity or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError(f'Stem output directory identity changed: {path}')

    def _target(self, name: str) -> Path | str:
        return self.path / name if self.fd is None else name

    def _dirfd(self) -> dict[str, Any]:
        return {} if self.fd is None else {'dir_fd': self.fd}

    def _stat(self, name: str) -> os.stat_result:
        return os.stat(self._target(name), follow_symlinks=False, **self._dirfd())

    def check_new_files(self, names: list[str]) -> None:
        self.verify()
        for name in names:
            _validate_name(name)
            try:
                self._stat(name)
            except FileNotFoundError:
                continue
            raise FileExistsError(f'Stem output already exists: {self.path / name}')

    def _remove_owned(self, name: str, identity: tuple[int, int]) -> None:
        # POSIX must clean the pinned directory even when its path was swapped.
        # Windows keeps all ancestors locked until after cleanup; fail closed if
        # a filesystem nevertheless breaks those locking semantics.
        if self.fd is None:
            self.verify()
        try:
            info = self._stat(name)
        except FileNotFoundError:
            return
        if stat.S_ISREG(info.st_mode) and _identity(info) == identity:
            os.unlink(self._target(name), **self._dirfd())

    def _publish_file(self, source: Path, name: str, published: list[tuple[str, tuple[int, int]]]) -> None:
        temporary = f'.stems-{uuid4().hex}'
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if self.fd is not None:
            flags |= os.O_NOFOLLOW
        else:
            flags |= getattr(os, 'O_BINARY')
        fd = os.open(self._target(temporary), flags, 0o600, **self._dirfd())
        identity = _identity(os.fstat(fd))
        try:
            with os.fdopen(fd, 'wb', closefd=False) as target, source.open('rb') as data:
                shutil.copyfileobj(data, target)
            self.verify()
            if self.fd is None:
                # os.link uses CreateHardLinkW on Windows: existing destinations
                # fail, including reparse points. No dirfd or POSIX flags needed.
                os.link(self.path / temporary, self.path / name)
            else:
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            published.append((name, identity))
            self.verify()
        finally:
            if self.fd is None:
                # CRT descriptors do not share delete access on Windows.
                os.close(fd)
                self._remove_owned(temporary, identity)
            else:
                try:
                    self._remove_owned(temporary, identity)
                finally:
                    os.close(fd)

    def publish(self, staging: Path, names: list[str]) -> None:
        """Publish complete files, rolling back only this invocation's inodes."""
        self.check_new_files(names)
        published: list[tuple[str, tuple[int, int]]] = []
        try:
            for name in names:
                self.verify()
                self._publish_file(staging / name, name, published)
            self.verify()  # Also detect changes during the final staging cleanup.
        except BaseException:
            for name, identity in reversed(published):
                self._remove_owned(name, identity)
            raise


@contextmanager
def pin_output(output: Path | str) -> Iterator[PinnedOutput]:
    selected = Path(os.path.abspath(output))
    if selected.is_symlink():
        raise ValueError(f'Stem output root is a symlink: {selected}')
    with ExitStack() as stack:
        fd = None
        if os.name == 'nt':
            # Do not resolve away junctions/reparse points. Lock parents before
            # opening/creating children so no intervening component can change.
            path = selected
            for directory in (*reversed(path.parents), path):
                directory.mkdir(exist_ok=True)
                stack.enter_context(_pin_windows_directory(directory))
            identity = _identity(path.lstat())
        else:
            path = selected.resolve()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(path.anchor, flags)
            stack.callback(os.close, fd)
            for part in path.parts[1:]:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            identity = _identity(os.fstat(fd))
        root = PinnedOutput(selected, path, fd, identity)
        root.verify()
        yield root
