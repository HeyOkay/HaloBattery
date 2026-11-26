"""Check bounded PE reads and the exact append-only signed helper replacement."""
import contextlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "tools/inno_helper.py"
spec = importlib.util.spec_from_file_location("inno_helper", MODULE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def fixture_pe(payload=None):
    resource_size = ((256 + len(payload) + 511) // 512 * 512) if payload else 512
    data = bytearray(512 + resource_size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x14C, 1)
    struct.pack_into("<H", data, 148, 224)
    optional = 152
    struct.pack_into("<H", data, optional, 0x10B)
    struct.pack_into("<I", data, optional + 8, resource_size)
    struct.pack_into("<II", data, optional + 32, 4096, 512)
    struct.pack_into("<I", data, optional + 56, 4096 + ((resource_size + 4095) // 4096 * 4096))
    section = 376
    data[section:section + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", data, section + 8, resource_size, 4096, resource_size, 512)
    if payload:
        struct.pack_into("<II", data, optional + 96 + 16, 4096, resource_size)
        for offset in (0, 32, 64):
            struct.pack_into("<HH", data, 512 + offset + 12, int(offset == 32), int(offset != 32))
        struct.pack_into("<II", data, 528, 10, 0x80000020)
        struct.pack_into("<II", data, 560, 0x80000070, 0x80000040)
        struct.pack_into("<II", data, 592, 1033, 96)
        struct.pack_into("<IIII", data, 608, 4352, len(payload), 0, 0)
        name = "HELPER_EXE_AMD64".encode("utf-16-le")
        struct.pack_into("<H", data, 624, len(name) // 2)
        data[626:626 + len(name)] = name
        data[768:768 + len(payload)] = payload
    return bytes(data)


def signed_fixture(unsigned):
    data = bytearray(unsigned)
    _, directory, _ = helper.pe_layout(data)
    struct.pack_into("<II", data, directory + 32, len(data), 16)
    return bytes(data) + struct.pack("<IHH8s", 16, 0x200, 2, b"fixture!")


class InnoHelperTests(unittest.TestCase):
    def setUp(self):
        self.unsigned = fixture_pe().ljust(6144, b"\0")
        self.template = fixture_pe(self.unsigned)
        self.signed = signed_fixture(self.unsigned)

    @contextlib.contextmanager
    def pinned_fixture(self):
        with mock.patch.object(helper, "TEMPLATE_SHA256", helper.digest(self.template)), \
                mock.patch.object(helper, "HELPER_SHA256", helper.digest(self.unsigned)):
            yield

    def test_extracts_exact_resource_and_rejects_truncated_inputs(self):
        self.assertEqual(helper.extract_helper(self.template)[0], self.unsigned)
        for data in (b"", self.template[:100], self.template[:-1]):
            with self.assertRaises(ValueError):
                helper.extract_helper(data)

    def test_rejects_resource_directory_escape(self):
        data = bytearray(self.template)
        struct.pack_into("<I", data, 532, 0x8FFFFFFF)
        with self.assertRaises(ValueError):
            helper.extract_helper(data)

    def test_signed_helper_must_preserve_unsigned_executable(self):
        helper.signed_equivalent(self.unsigned, self.signed)
        changed = bytearray(self.signed)
        changed[450] ^= 1
        for data in (self.unsigned, self.signed[:-1], bytes(changed)):
            with self.assertRaises(ValueError):
                helper.signed_equivalent(self.unsigned, data)

    def test_patch_preserves_every_byte_except_seven_explicit_fields(self):
        with self.pinned_fixture():
            patched = helper.patch_template(self.template, self.signed)
            self.assertEqual(patched, helper.patch_template(self.template, self.signed))
        self.assertEqual(helper.extract_helper(patched)[0], self.signed)
        optional, directory, sections = helper.pe_layout(self.template)
        entry = helper.extract_helper(self.template)[1]
        section = sections[-1][0]
        offsets = (entry, entry + 4, section + 8, section + 16, optional + 56, directory + 20, optional + 8)
        restored = bytearray(patched[:len(self.template)])
        for offset in offsets:
            restored[offset:offset + 4] = self.template[offset:offset + 4]
        self.assertEqual(restored, self.template)
        self.assertEqual(patched[len(self.template):len(self.template) + len(self.signed)], self.signed)
        self.assertEqual(len(patched) % 512, 0)

    def test_patch_refuses_unpinned_template_or_modified_helper(self):
        with self.assertRaises(ValueError):
            helper.patch_template(self.template, self.signed)
        with self.pinned_fixture(), self.assertRaises(ValueError):
            helper.patch_template(self.template, self.unsigned)

    def test_cli_rejects_unpinned_template_and_never_overwrites_output(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, output = root / "source.e32", root / "helper.exe"
            source.write_bytes(self.template)
            result = subprocess.run([sys.executable, str(MODULE), "extract-template", "--template", str(source),
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            output.write_bytes(b"keep")
            result = subprocess.run([sys.executable, str(MODULE), "extract-engine", "--engine", str(source),
                                     "--output", str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
