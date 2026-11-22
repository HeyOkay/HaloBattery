"""Offline failures/trust boundaries for the NativeAOT input preparer."""
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aot_tools', ROOT / 'tools/get_settings_aot_toolchain.py')
aot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aot)


class PinnedToolchainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='halo-aot-inputs-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache'
        (self.cache / 'archives').mkdir(parents=True)
        self.lock = {'version': 1, 'msvcLayout': 'VC/Tools/MSVC/test',
                     'maxEntries': 100, 'maxExpandedBytes': 10000, 'inputs': []}
        self.native_checked = []
        self.add_archive('tools.vsix', 'msvc', {
            'Contents/VC/Tools/MSVC/test/bin/Hostx64/x64/link.exe': b'link',
            'Contents/VC/Tools/MSVC/test/bin/Hostx64/x64/cl.exe': b'cl',
            'Contents/VC/Tools/MSVC/test/bin/Hostx64/x64/compiler.dll': b'dll',
            'Contents/VC/Tools/MSVC/test/lib/x64/libcmt.lib': b'lib',
            'Contents/VC/Tools/MSVC/test/include/vcruntime.h': b'header'})
        oldnames = 'Contents/VC/Tools/MSVC/test/lib/x64/oldnames.lib'
        self.add_archive('store.vsix', 'msvc', {oldnames: b'oldnames',
                         'Contents/VC/Tools/MSVC/test/lib/x64/store/unneeded.lib': b'unneeded'})
        self.lock['inputs'][-1]['include'] = [oldnames]
        self.add_archive('sdk.nupkg', 'sdk', {'c/um/x64/kernel32.lib': b'kernel', 'c/ucrt/x64/ucrt.lib': b'ucrt'})

    def add_archive(self, name, kind, files):
        path = self.cache / 'archives' / name
        with zipfile.ZipFile(path, 'w') as archive:
            for file, content in files.items():
                archive.writestr(file, content)
        # Python's Windows ZIP writer normalizes backslashes. Construct the raw
        # spelling so the reader's orig_filename validation is exercised.
        data = path.read_bytes()
        for file in files:
            if '\\' in file:
                data = data.replace(file.replace('\\', '/').encode(), file.encode())
        path.write_bytes(data)
        self.lock['inputs'].append({'name': name, 'kind': kind,
            'url': 'https://download.visualstudio.microsoft.com/' + name,
            'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return path

    def prepare(self, **kwargs):
        return aot.prepare(self.lock, self.cache, offline=True,
                           signature_checker=kwargs.pop('signature_checker', self.native_checked.extend), **kwargs)

    def test_success_uses_exact_missing_store_complement_and_checks_every_exe_dll(self):
        result = self.prepare()
        self.assertFalse(result['installed'])
        self.assertEqual(result['authenticodeFiles'], 3)
        self.assertEqual({p.name for p in self.native_checked}, {'cl.exe', 'link.exe', 'compiler.dll'})
        self.assertTrue((Path(result['msvc']) / 'lib/x64/oldnames.lib').is_file())
        self.assertFalse((Path(result['msvc']) / 'lib/x64/store/unneeded.lib').exists())
        self.assertFalse((self.cache / '.acquire.lock').exists())

    def test_cached_generation_is_exactly_revalidated_and_reused(self):
        result = self.prepare()
        self.assertEqual(self.prepare()['root'], result['root'])
        self.assertEqual(len(list(self.cache.glob('inputs-*'))), 1)
        target = Path(result['msvc']) / 'bin/Hostx64/x64/link.exe'
        target.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'generation file changed'):
            self.prepare()
        self.assertEqual(target.read_bytes(), b'corrupt')
        self.assertFalse(list(self.cache.glob('extract-*')))

    def test_unexpected_cached_file_and_incomplete_inputs_fail_closed(self):
        result = self.prepare()
        (Path(result['root']) / 'unexpected.exe').write_bytes(b'x')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            self.prepare()
        self.lock['inputs'][1]['include'] = []
        with self.assertRaisesRegex(ValueError, 'Incomplete pinned toolchain'):
            self.prepare()

    def test_corrupt_hash_and_wrong_size_fail_before_extract_or_authenticode(self):
        for field, value in (('sha256', '0' * 64), ('bytes', 1)):
            original = self.lock['inputs'][0][field]
            self.lock['inputs'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'size/hash mismatch'):
                self.prepare()
            self.lock['inputs'][0][field] = original
            self.assertFalse(self.native_checked)
            self.assertFalse(list(self.cache.glob('extract-*')))

    def test_unsafe_entry_is_rejected_even_if_excluded_from_extraction(self):
        for name in ('../outside.exe', '/absolute.exe', 'C:/drive.exe', 'Contents/../escape.exe',
                     'Contents\\escape.exe', 'Contents/NUL.exe', 'Contents/name. ', 'Contents/a:stream',
                     'Contents/question?.exe'):
            path = self.add_archive('unsafe.vsix', 'msvc', {name: b'x'})
            self.lock['inputs'][-1]['include'] = []
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
                self.prepare()
            self.lock['inputs'].pop()
            path.unlink()
        self.assertFalse(self.native_checked)
        self.assertFalse(list(self.cache.glob('extract-*')))

    def test_symlink_archive_entry_and_duplicate_outputs_are_rejected(self):
        path = self.add_archive('link.vsix', 'msvc', {})
        with zipfile.ZipFile(path, 'w') as zf:
            item = zipfile.ZipInfo('Contents/link')
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(item, '../target')
        pin = self.lock['inputs'][-1]
        pin.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'links are forbidden'):
            self.prepare()
        self.lock['inputs'].pop()
        self.add_archive('duplicate.vsix', 'msvc', {'Contents/VC/Tools/MSVC/test/bin/Hostx64/x64/LINK.EXE': b'different'})
        with self.assertRaisesRegex(ValueError, 'Duplicate toolchain output'):
            self.prepare()

    def test_aggregate_entry_and_expansion_limits_are_checked_before_writes(self):
        for field in ('maxEntries', 'maxExpandedBytes'):
            previous = self.lock[field]
            self.lock[field] = 1
            with self.assertRaisesRegex(ValueError, 'bounds exceeded'):
                self.prepare()
            self.lock[field] = previous
        self.assertFalse(self.native_checked)
        self.assertFalse(list(self.cache.glob('extract-*')))

    def test_signature_failure_never_returns_or_caches_executable_generation(self):
        def reject(_):
            raise ValueError('untrusted signer')
        with self.assertRaisesRegex(ValueError, 'untrusted signer'):
            self.prepare(signature_checker=reject)
        self.assertFalse(list(self.cache.glob('inputs-*')))
        self.assertFalse(list(self.cache.glob('extract-*')))
        self.assertFalse((self.cache / '.acquire.lock').exists())

    def test_offline_missing_and_concurrent_acquisition_do_not_mutate_existing_data(self):
        (self.cache / '.acquire.lock').write_text('another process')
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual((self.cache / '.acquire.lock').read_text(), 'another process')
        (self.cache / '.acquire.lock').unlink()
        (self.cache / 'archives' / 'tools.vsix').unlink()
        with mock.patch.object(aot.urllib.request, 'build_opener') as network:
            with self.assertRaisesRegex(ValueError, 'Offline toolchain archive missing'):
                self.prepare()
            network.assert_not_called()

    def test_url_and_archive_pin_validation(self):
        for url in ('http://download.visualstudio.microsoft.com/x', 'https://evil.example/x',
                    'https://api.nuget.org:444/x', 'https://user@api.nuget.org/x',
                    'https://api.nuget.org/x?query'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                aot.validate_url(url)
        self.lock['inputs'][0]['name'] = '../escape'
        with self.assertRaises(ValueError):
            self.prepare()

    def test_production_authenticode_checker_is_mandatory_and_fail_closed(self):
        with mock.patch.object(aot.subprocess, 'run') as command:
            command.return_value.returncode = 1
            command.return_value.stderr = 'NotSigned'
            with self.assertRaisesRegex(ValueError, 'Authenticode check failed'):
                aot.verify_microsoft_signatures([Path('unsafe.exe')])
            args, options = command.call_args
            self.assertIn('Get-AuthenticodeSignature', args[0][-1])
            self.assertIn("$signature.Status -ne 'Valid'", args[0][-1])
            self.assertIn('O=Microsoft Corporation', args[0][-1])
            self.assertIn('Microsoft Windows Software Compatibility Publisher', args[0][-1])
            self.assertEqual(json.loads(options['input']), ['unsafe.exe'])


    def test_download_bounds_and_hash_failures_never_promote_partial_input(self):
        pin = dict(self.lock['inputs'][0])
        target = self.cache / 'archives' / 'downloaded.vsix'
        for data in (b'x' * (pin['bytes'] + 1), b'x' * pin['bytes']):
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(data)
            with mock.patch.object(aot.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(ValueError):
                    aot.download(pin, target, offline=False)
            self.assertFalse(target.exists())
            self.assertFalse(list(target.parent.glob('download-*.partial')))
        opener = mock.Mock()
        opener.open.side_effect = OSError('Injected HTTP open failure')
        with mock.patch.object(aot.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(OSError):
                aot.download(pin, target, offline=False)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob('download-*.partial')))

    def test_download_promotes_only_exact_bytes_and_never_overwrites_invalid_cache(self):
        pin = dict(self.lock['inputs'][0])
        target = self.cache / 'archives' / 'downloaded.vsix'
        data = (self.cache / 'archives' / pin['name']).read_bytes()
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(data)
        with mock.patch.object(aot.urllib.request, 'build_opener', return_value=opener):
            aot.download(pin, target, offline=False)
        self.assertEqual(target.read_bytes(), data)
        target.write_bytes(b'corrupt cache')
        with self.assertRaises(ValueError):
            aot.download(pin, target, offline=False)
        self.assertEqual(target.read_bytes(), b'corrupt cache')

    def test_cross_host_https_redirect_is_refused(self):
        request = aot.urllib.request.Request('https://download.visualstudio.microsoft.com/input')
        with self.assertRaisesRegex(ValueError, 'different host'):
            aot.PinnedRedirects().redirect_request(request, None, 302, 'redirect', {},
                                                  'https://api.nuget.org/substitute')

    def test_owned_generation_rename_retries_only_transient_windows_denial(self):
        stage = self.cache / 'extract-fixture'
        stage.mkdir()
        generation = self.cache / 'inputs-fixture'
        error = PermissionError('Injected Windows sharing denial')
        error.winerror = 32
        original = Path.rename
        calls = []
        def rename(path, destination):
            calls.append((path, destination))
            if len(calls) == 1:
                raise error
            return original(path, destination)
        with mock.patch.object(Path, 'rename', rename), mock.patch.object(aot.time, 'sleep') as wait:
            aot.promote_generation(stage, generation)
        self.assertEqual(calls, [(stage, generation), (stage, generation)])
        wait.assert_called_once_with(0.05)
        self.assertTrue(generation.is_dir())
        self.assertFalse(stage.exists())

    def test_generation_retry_preserves_persistent_denial_collision_and_reparse_failures(self):
        stage = self.cache / 'extract-fixture'
        stage.mkdir()
        marker = stage / 'keep.txt'
        marker.write_text('owned')
        generation = self.cache / 'inputs-fixture'
        error = PermissionError('Injected persistent Windows denial')
        error.winerror = 5
        with mock.patch.object(Path, 'rename', side_effect=error) as move, mock.patch.object(aot.time, 'sleep') as wait:
            with self.assertRaises(PermissionError):
                aot.promote_generation(stage, generation)
            self.assertEqual(move.call_count, 5)
            self.assertEqual(wait.call_count, 4)
        self.assertEqual(marker.read_text(), 'owned')
        generation.mkdir()
        external = generation / 'external.txt'
        external.write_text('external')
        with mock.patch.object(Path, 'rename') as move:
            with self.assertRaises(FileExistsError):
                aot.promote_generation(stage, generation)
            move.assert_not_called()
        self.assertEqual(external.read_text(), 'external')
        external.unlink()
        generation.rmdir()
        with mock.patch.object(Path, 'rename', side_effect=error) as move, mock.patch.object(aot.time, 'sleep'), \
             mock.patch.object(aot, 'no_links', side_effect=[None, None, ValueError('Injected reparse point')]):
            with self.assertRaisesRegex(ValueError, 'reparse point'):
                aot.promote_generation(stage, generation)
            self.assertEqual(move.call_count, 1)
        self.assertEqual(marker.read_text(), 'owned')
        self.assertFalse(generation.exists())
        with mock.patch.object(Path, 'rename', side_effect=error) as move, \
             mock.patch.object(aot.time, 'sleep', side_effect=lambda _: generation.mkdir()):
            with self.assertRaises(FileExistsError):
                aot.promote_generation(stage, generation)
            self.assertEqual(move.call_count, 1)
        self.assertTrue(generation.is_dir())
        generation.rmdir()
        with mock.patch.object(Path, 'rename', side_effect=PermissionError('Unrelated denial')) as move, \
             mock.patch.object(aot.time, 'sleep') as wait:
            with self.assertRaises(PermissionError):
                aot.promote_generation(stage, generation)
            self.assertEqual(move.call_count, 1)
            wait.assert_not_called()

    def test_committed_aot_graph_has_exact_raw_archive_pins_without_rewriting_jit_locks(self):
        import base64
        project = ROOT / "ui/HaloBattery.Settings"
        archive_pins = json.loads((project / "packages.aot.archives.lock.json").read_text())["dependencies"]
        graph = json.loads((project / "packages.aot.true.lock.json").read_text())["dependencies"]
        for framework in graph.values():
            for name, package in framework.items():
                self.assertIn(name, archive_pins)
                self.assertEqual(package["resolved"], archive_pins[name]["resolved"])
                self.assertEqual(len(base64.b64decode(archive_pins[name]["archiveSha512"], validate=True)), 64)
        for name in ("Microsoft.DotNet.ILCompiler", "runtime.win-x64.Microsoft.DotNet.ILCompiler",
                     "microsoft.netcore.app.runtime.nativeaot.win-x64"):
            self.assertEqual(archive_pins[name]["resolved"], "10.0.15")
        for name, pin in json.loads((project / "packages.archives.lock.json").read_text())["dependencies"].items():
            self.assertEqual(archive_pins.get(name) or archive_pins.get(name.lower()), pin)


if __name__ == '__main__':
    unittest.main()
