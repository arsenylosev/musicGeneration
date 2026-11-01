"""Race regressions and Windows-API simulation (not a native Windows run)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_audio_stems import _audio_render_available, _make_two_pianos_one_bass_midi


@unittest.skipUnless(_audio_render_available(), "requires pip install -e '.[audio]'")
@unittest.skipIf(os.name == 'nt', 'POSIX rename races; Windows pins deny renames')
class TestStemPublicationRaces(unittest.TestCase):
    def test_ancestor_swap_cannot_redirect_staging_publication_or_cleanup(self):
        from aimusic.audio.render import stems
        for phase in ('pin', 'render', 'link'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                ancestor, moved, outside = (base / n for n in ('parent', 'moved', 'outside'))
                out = ancestor / 'stems'
                out.mkdir(parents=True)
                (outside / 'stems').mkdir(parents=True)
                source = _make_two_pianos_one_bass_midi(base / 'src.mid')
                swapped = False
                snapshot = {}

                def swap():
                    nonlocal swapped, snapshot
                    if swapped:
                        return
                    swapped = True
                    # A decoy tree makes path-based publication/cleanup dangerous,
                    # rather than merely making the source file disappear.
                    shutil.copytree(out, outside / 'stems', dirs_exist_ok=True)
                    (outside / 'stems' / 'KEEP').write_bytes(b'OUTSIDE')
                    snapshot = {p.relative_to(outside): p.read_bytes()
                                for p in outside.rglob('*') if p.is_file()}
                    ancestor.rename(moved)
                    ancestor.symlink_to(outside, target_is_directory=True)

                real_open, real_render, real_link = os.open, stems.render_stem, os.link

                def opening(path, flags, *args, **kwargs):
                    if phase == 'pin' and Path(path).name == 'stems' and flags & os.O_DIRECTORY:
                        swap()
                    return real_open(path, flags, *args, **kwargs)

                def rendering(*args, **kwargs):
                    result = real_render(*args, **kwargs)
                    if phase == 'render':
                        swap()
                    return result

                def linking(*args, **kwargs):
                    if phase == 'link':
                        swap()
                    return real_link(*args, **kwargs)

                with patch('os.open', side_effect=opening), patch.object(stems, 'render_stem', rendering), \
                        patch('os.link', side_effect=linking):
                    with self.assertRaises((ValueError, OSError)):
                        stems.render_role_stems(source, output_dir=out)
                self.assertTrue(swapped)
                self.assertEqual(list((moved / 'stems').iterdir()), [])
                self.assertEqual({p.relative_to(outside): p.read_bytes()
                                  for p in outside.rglob('*') if p.is_file()}, snapshot)

    def test_cache_copy_ancestor_swap_does_not_leave_or_delete_artifacts(self):
        from aimusic.audio.render import stems
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source, ancestor, moved, outside = (base / n for n in ('cache', 'parent', 'moved', 'outside'))
            source.mkdir()
            (source / 'a.wav').write_bytes(b'AUDIO')
            out = ancestor / 'stems'
            out.mkdir(parents=True)
            (outside / 'stems').mkdir(parents=True)
            (outside / 'stems' / 'KEEP').write_bytes(b'OUTSIDE')
            real_copy = shutil.copyfile

            def copying(*args, **kwargs):
                ancestor.rename(moved)
                ancestor.symlink_to(outside, target_is_directory=True)
                return real_copy(*args, **kwargs)

            with patch.object(stems.shutil, 'copyfile', side_effect=copying):
                with self.assertRaises((ValueError, OSError)):
                    stems.copy_stem_artifacts(source, out)
            self.assertEqual(list((moved / 'stems').iterdir()), [])
            self.assertEqual([p.name for p in (outside / 'stems').iterdir()], ['KEEP'])
            self.assertEqual((outside / 'stems' / 'KEEP').read_bytes(), b'OUTSIDE')

    def test_ancestor_swap_during_final_staging_cleanup_rolls_back(self):
        from aimusic.audio.render import stems
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source, ancestor, moved, outside = (base / n for n in ('cache', 'parent', 'moved', 'outside'))
            source.mkdir()
            (source / 'a.wav').write_bytes(b'AUDIO')
            out = ancestor / 'stems'
            out.mkdir(parents=True)
            (outside / 'stems').mkdir(parents=True)
            real_unlink = os.unlink
            swapped = False

            def unlink(path, **kwargs):
                nonlocal swapped
                if Path(path).name.startswith('.stems-') and not swapped:
                    swapped = True
                    ancestor.rename(moved)
                    ancestor.symlink_to(outside, target_is_directory=True)
                    (outside / 'stems' / Path(path).name).write_bytes(b'KEEP')
                return real_unlink(path, **kwargs)

            with patch('os.unlink', side_effect=unlink):
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    stems.copy_stem_artifacts(source, out)
            self.assertTrue(swapped)
            self.assertEqual(list((moved / 'stems').iterdir()), [])
            self.assertEqual([p.read_bytes() for p in (outside / 'stems').iterdir()], [b'KEEP'])


class _WindowsOS:
    """Real portable I/O; fail if the implementation asks for POSIX-only APIs."""
    name = 'nt'

    def __init__(self):
        self.open_files = {}

    def open(self, path, flags, mode=0o777, **kwargs):
        if kwargs:
            raise NotImplementedError('Windows has no dirfd open')
        fd = os.open(path, flags, mode)
        self.open_files[fd] = Path(path)
        return fd

    def close(self, fd):
        os.close(fd)
        self.open_files.pop(fd, None)

    def unlink(self, path, **kwargs):
        if kwargs:
            raise NotImplementedError('Windows has no dirfd unlink')
        if Path(path) in self.open_files.values():
            raise PermissionError('Windows CRT file handles do not share delete access')
        os.unlink(path)

    def __getattr__(self, name):
        if name == 'O_BINARY':
            return 0  # POSIX files are already binary; Windows production uses O_BINARY.
        if name in ('O_DIRECTORY', 'O_NOFOLLOW'):
            raise AttributeError(f'Windows has no {name}')
        original = getattr(os, name)
        if name not in ('open', 'link', 'unlink', 'stat', 'mkdir'):
            return original

        def portable(*args, **kwargs):
            if any(key.endswith('dir_fd') for key in kwargs):
                raise NotImplementedError('Windows has no dirfd support')
            return original(*args, **kwargs)
        return portable


class _WindowsKernel:
    """Simulate only the Win32 handle boundary; portable file operations are real."""
    def __init__(self, case, reparse=None):
        self.case, self.reparse = case, reparse
        self.handles = {}
        self.opened = []

    def CreateFileW(self, path, access, share, security, disposition, flags, template):
        self.case.assertEqual((access, share, disposition, flags), (0x81, 1, 3, 0x02200000))
        path = Path(path)
        handle = len(self.opened) + 2 ** 33
        self.handles[handle] = path
        self.opened.append(path)
        return handle

    def GetFileInformationByHandle(self, handle, info):
        path = self.handles[handle]
        info._obj.attributes = 0x10 if path.is_dir() else 0
        if path.is_symlink() or path == self.reparse:
            info._obj.attributes |= 0x400
        return True

    def CloseHandle(self, handle):
        del self.handles[handle]
        return True


@unittest.skipUnless(_audio_render_available(), "requires pip install -e '.[audio]'")
class TestWindowsStemPublication(unittest.TestCase):
    def test_portable_publication_collision_and_rollback(self):
        from aimusic.audio.render import stems, stem_publication
        for operation in ('render', 'copy', 'collision', 'rollback'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                out, source = base / 'nested' / 'stems', base / 'cache'
                source.mkdir()
                (source / 'a.wav').write_bytes(b'AUDIO A')
                (source / 'b.wav').write_bytes(b'AUDIO B')
                midi = _make_two_pianos_one_bass_midi(base / 'src.mid')
                kernel = _WindowsKernel(self)
                real_link = os.link
                calls = 0

                def linking(src, dst, **kwargs):
                    nonlocal calls
                    self.assertIn(out, kernel.handles.values())
                    calls += 1
                    if calls == 2:
                        if operation == 'collision':
                            Path(dst).write_bytes(b'OTHER WRITER')
                        if operation == 'rollback':
                            raise OSError('publication failed')
                    return real_link(src, dst, **kwargs)

                with patch.object(stem_publication, 'os', _WindowsOS()), \
                        patch.object(stem_publication, '_windows_api', return_value=kernel), \
                        patch('os.link', side_effect=linking):
                    if operation == 'render':
                        outputs = stems.render_role_stems(midi, output_dir=out)
                        self.assertTrue(outputs['rough_mix'].is_file())
                    elif operation in ('collision', 'rollback'):
                        with self.assertRaises(OSError):
                            stems.copy_stem_artifacts(source, out)
                    else:
                        stems.copy_stem_artifacts(source, out)
                self.assertIn(out, kernel.opened)
                self.assertIn(out.parent, kernel.opened)
                self.assertEqual(kernel.handles, {})
                if operation == 'collision':
                    self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, {'b.wav': b'OTHER WRITER'})
                elif operation == 'rollback':
                    self.assertEqual(list(out.iterdir()), [])
                elif operation == 'copy':
                    self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()},
                                     {'a.wav': b'AUDIO A', 'b.wav': b'AUDIO B'})

    def test_root_and_ancestor_reparse_points_fail_before_writes(self):
        from aimusic.audio.render import stems, stem_publication
        for reparse_at in ('parent', 'root'):
            with self.subTest(reparse_at=reparse_at), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                out = base / 'parent' / 'stems'
                out.mkdir(parents=True)
                source = base / 'cache'
                source.mkdir()
                (source / 'a.wav').write_bytes(b'AUDIO')
                kernel = _WindowsKernel(self, out if reparse_at == 'root' else out.parent)
                with patch.object(stem_publication, 'os', _WindowsOS()), \
                        patch.object(stem_publication, '_windows_api', return_value=kernel):
                    with self.assertRaisesRegex(ValueError, 'reparse'):
                        stems.copy_stem_artifacts(source, out)
                self.assertEqual(list(out.iterdir()), [])
                self.assertEqual(kernel.handles, {})

    def test_directory_handles_stay_locked_through_rendering(self):
        from aimusic.audio.render import stems, stem_publication
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            out = base / 'parent' / 'stems'
            source = _make_two_pianos_one_bass_midi(base / 'src.mid')
            kernel = _WindowsKernel(self)
            real_render, real_rename = stems.render_stem, os.rename
            attempts = []

            def rename(src, dst, **kwargs):
                if Path(src) in kernel.handles.values():
                    attempts.append(src)
                    raise PermissionError('directory handle denies FILE_SHARE_DELETE')
                return real_rename(src, dst, **kwargs)

            def render(*args, **kwargs):
                with self.assertRaises(PermissionError):
                    out.parent.rename(base / 'moved')
                return real_render(*args, **kwargs)

            with patch.object(stem_publication, 'os', _WindowsOS()), \
                    patch.object(stem_publication, '_windows_api', return_value=kernel), \
                    patch('os.rename', side_effect=rename), patch.object(stems, 'render_stem', render):
                outputs = stems.render_role_stems(source, output_dir=out)
            self.assertEqual(len(attempts), 3)
            self.assertTrue(outputs['rough_mix'].is_file())
            self.assertEqual(kernel.handles, {})

    def test_reparse_attribute_change_is_not_mistaken_for_same_directory(self):
        from types import SimpleNamespace
        from aimusic.audio.render.stem_publication import PinnedOutput
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve()
            info = path.lstat()
            root = PinnedOutput(path, path, None, (info.st_dev, info.st_ino))
            reparse = SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev,
                                      st_ino=info.st_ino, st_file_attributes=0x410)
            with patch.object(Path, 'lstat', return_value=reparse):
                with self.assertRaisesRegex(ValueError, 'identity changed|reparse'):
                    root.verify()
