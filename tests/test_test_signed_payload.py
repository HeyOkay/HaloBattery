"""Execute the TEST acceptance helper using fake signatures and inert PE bytes.

No certificate is created or trusted, and no PE, signing tool, network or app is
executed. The independent binary fixture includes an overlay that must survive.
"""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/verify_test_signed_payload.ps1"
PWSH = shutil.which("pwsh") or shutil.which("pwsh.exe")
OWN = ("HaloBattery.exe", "_internal/settings-ui/HaloBattery.Settings.exe")
THUMBPRINT = "A" * 40


def unsigned_pe(tag=b"app", *, pe32=False, unaligned=False):
    """Independent minimal PE headers plus section bytes and an application overlay."""
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x14C if pe32 else 0x8664, 1,
                     0, 0, 0, 0xE0 if pe32 else 0xF0, 0x22)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x10B if pe32 else 0x20B)
    struct.pack_into("<I", data, optional + (92 if pe32 else 108), 16)
    data[0x200:0x210] = b"INERT-SECTION!!!"
    data += b"PYINSTALLER-OR-INNO-OVERLAY:" + tag
    if not unaligned:
        data += b"\0" * (-len(data) % 8)
    return bytes(data)


def signed_pe(original, *, pe32=False):
    """Only signing-mutable header bytes, zero alignment, and a WIN_CERTIFICATE."""
    data = bytearray(original)
    data += b"\0" * (-len(data) % 8)
    offset = len(data)
    certificate = struct.pack("<IHH", 24, 0x200, 2) + b"FAKE-PKCS7-BYTES"
    assert len(certificate) == 24
    struct.pack_into("<I", data, 0x98 + 64, 0x12345678)
    struct.pack_into("<II", data, 0x98 + (96 if pe32 else 112) + 32,
                     offset, len(certificate))
    return bytes(data + certificate)


STUB = r"""
$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
namespace Halo {
    public static class TestSignatureTrust {
        public static int Verify(string path) {
            return int.Parse(System.Environment.GetEnvironmentVariable("FAKE_TRUST_RESULT"));
        }
        public static int VerifyProduction(string path) { return Verify(path); }
    }
}
'@
function global:Get-AuthenticodeSignature {
    param([string]$LiteralPath)
    $signer=$env:FAKE_SIGNER
    if($env:FAKE_BAD_SECOND -eq 'true' -and [IO.Path]::GetFileName($LiteralPath) -eq 'HaloBattery.Settings.exe') {
        $signer='B'*40
    }
    $certificate = if($env:FAKE_NULL_CERT -eq 'true') {$null} else {
        [pscustomobject]@{Thumbprint=$signer}
    }
    $timestamp = if($env:FAKE_TIMESTAMP -eq 'true') {[pscustomobject]@{Thumbprint='C'*40}} else {$null}
    [pscustomobject]@{Status=$env:FAKE_STATUS;SignerCertificate=$certificate;SignatureType=$env:FAKE_TYPE;TimeStamperCertificate=$timestamp}
}
try {
    $options = @{SigningMode=$env:SIGNING_MODE}
    if($env:PRODUCTION_PIN) {$options.CertificateThumbprint=$env:PRODUCTION_PIN}
    if($env:INNO_HELPER) {
        & $env:VERIFIER -InnoHelperPath $env:INNO_HELPER -UnsignedInnoHelperPath $env:UNSIGNED_INNO_HELPER @options
    } elseif($env:CASE_MODE -eq 'installer') {
        & $env:VERIFIER -InstallerPath $env:INSTALLER -UnsignedInstallerPath $env:UNSIGNED_INSTALLER @options
    } elseif($env:SIGNATURE_PATH) {
        & $env:VERIFIER -SignaturePath $env:SIGNATURE_PATH @options
    } elseif($env:INTERNAL_MANIFEST) {
        & $env:VERIFIER -SignedDirectory $env:SIGNED -DestinationDirectory $env:DESTINATION -InternalManifestPath $env:INTERNAL_MANIFEST @options
    } else {
        & $env:VERIFIER -SignedDirectory $env:SIGNED -DestinationDirectory $env:DESTINATION -InventoryPath $env:INVENTORY @options
    }
    exit 0
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
"""


@unittest.skipUnless(os.name == "nt" and PWSH, "PowerShell 7 on Windows is required")
class SignedPayloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="halo signed acceptance [test] ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "unsigned"
        self.signed = self.root / "signed"
        self.inventory = self.root / "inventory.json"
        self.destination.mkdir()
        self.signed.mkdir()
        self.original = {}
        self.expected_signed = {}
        for index, relative in enumerate(OWN):
            original = unsigned_pe(str(index).encode(), pe32=bool(index), unaligned=bool(index))
            self.original[relative] = original
            self.expected_signed[relative] = signed_pe(original, pe32=bool(index))
            self.write(self.destination, relative, original)
            self.write(self.signed, relative, self.expected_signed[relative])
        self.write(self.destination, "_internal/vendor.dll", b"THIRD-PARTY-BYTES\0\xff")
        self.write(self.destination, "_internal/settings-ui/dependency.dll", b"NATIVE-DEPENDENCY")
        self.write(self.destination, ".hidden-resource", b"HIDDEN-DEPENDENCY")
        self.initial = self.snapshot(self.destination)
        self.inventory.write_text(json.dumps({name: hashlib.sha256(data).hexdigest()
            for name, data in self.initial.items()}), encoding="utf-8")
        self.runner = self.root / "runner.ps1"
        self.runner.write_text(STUB, encoding="utf-8")

    @staticmethod
    def write(root, relative, data):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob("*") if p.is_file()}

    @staticmethod
    def hide(path):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetFileAttributesW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
        kernel.SetFileAttributesW.restype = ctypes.c_int32
        if not kernel.SetFileAttributesW(str(path), 0x02):
            raise ctypes.WinError(ctypes.get_last_error())

    def invoke(self, *, status="NotTrusted", signer=THUMBPRINT, thumbprint=THUMBPRINT,
               signature_type="Authenticode", null_certificate=False, installer=None,
               unsigned_installer=None, bad_second=False, native_status=None,
               signing_mode='Test', production_pin='', internal_manifest='', signature_path='', timestamp=False,
               inno_helper='', unsigned_inno_helper=''):
        if native_status is None:
            native_status = {"Valid": 0, "NotTrusted": -2146762487}.get(status, -2146762485)
        environment = dict(os.environ, PSExecutionPolicyPreference="Bypass",
            TEST_CERT_THUMBPRINT=thumbprint, FAKE_SIGNER=signer, FAKE_STATUS=status,
            FAKE_TYPE=signature_type, FAKE_NULL_CERT=str(null_certificate).lower(),
            FAKE_BAD_SECOND=str(bad_second).lower(),
            FAKE_TRUST_RESULT=str(native_status),
            SIGNING_MODE=signing_mode, PRODUCTION_PIN=production_pin, INTERNAL_MANIFEST=str(internal_manifest),
            SIGNATURE_PATH=str(signature_path),
            INNO_HELPER=str(inno_helper), UNSIGNED_INNO_HELPER=str(unsigned_inno_helper),
            FAKE_TIMESTAMP=str(timestamp).lower(),
            VERIFIER=str(HELPER), SIGNED=str(self.signed), DESTINATION=str(self.destination),
            INVENTORY=str(self.inventory), CASE_MODE="installer" if installer else "payload",
            INSTALLER=str(installer or ""), UNSIGNED_INSTALLER=str(unsigned_installer or ""))
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(self.runner)],
                              env=environment, capture_output=True, text=True, timeout=30)

    def assert_rejected(self, **options):
        before = self.snapshot(self.destination)
        result = self.invoke(**options)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot(self.destination), before,
                         "Rejected payload changed destination bytes")
        return result

    def test_exact_two_pes_accept_pinned_self_signed_test_certificate(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.snapshot(self.destination)
        self.assertEqual(set(after), set(self.initial))
        for relative, data in after.items():
            self.assertEqual(data, self.expected_signed.get(relative, self.initial[relative]))

    def test_valid_status_with_case_insensitive_exact_thumbprint_is_accepted(self):
        result = self.invoke(status="Valid", signer=THUMBPRINT.lower())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_extra_hidden_files_and_empty_directories_are_rejected(self):
        for name in ("extra.dll", ".hidden-extra"):
            with self.subTest(name=name):
                path = self.write(self.signed, name, b"UNEXPECTED")
                if name.startswith("."):
                    self.hide(path)
                self.assert_rejected()
                path.unlink()
        directory = self.signed / "empty-extra"
        directory.mkdir()
        self.assert_rejected()

    def test_missing_or_wrong_relative_owned_path_is_rejected(self):
        path = self.signed / OWN[1]
        path.unlink()
        self.assert_rejected()
        self.write(self.signed, "HaloBattery.Settings.exe", self.expected_signed[OWN[1]])
        self.assert_rejected()

    def test_wrong_signer_missing_certificate_and_bad_pin_are_rejected(self):
        for options in (dict(signer="B" * 40), dict(null_certificate=True),
                        dict(thumbprint=""), dict(thumbprint="A" * 39)):
            with self.subTest(options=options):
                self.assert_rejected(**options)

    def test_second_pe_signer_failure_does_not_partially_overlay_the_first(self):
        self.assert_rejected(bad_second=True)

    def test_hash_mismatch_and_every_nonaccepted_signature_status_are_rejected(self):
        for status in ("HashMismatch", "NotSigned", "UnknownError", "NotSupportedFileFormat",
                       "Incompatible", "Other"):
            with self.subTest(status=status):
                self.assert_rejected(status=status)

    def test_catalog_signature_cannot_substitute_for_embedded_signature(self):
        self.assert_rejected(signature_type="Catalog")

    def test_unknown_error_accepts_only_pinned_test_untrusted_root(self):
        result = self.invoke(status="UnknownError", native_status=-2146762487)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_untrusted_root_cannot_replace_the_embedded_signer_pin(self):
        for options in (dict(signer="B" * 40), dict(null_certificate=True),
                        dict(signature_type="Catalog"), dict(bad_second=True)):
            with self.subTest(options=options):
                self.assert_rejected(status="UnknownError", native_status=-2146762487, **options)

    def test_other_native_errors_and_inconsistent_success_are_rejected(self):
        for status in (0, 0x80096010, 0x800B0111, 0x800B0101, 0x80096004):
            signed_status = status if status < 0x80000000 else status - 0x100000000
            with self.subTest(native_status=hex(status)):
                self.assert_rejected(status="UnknownError", native_status=signed_status)

    def test_not_trusted_or_valid_cannot_hide_other_native_failures(self):
        for status in ("Valid", "NotTrusted"):
            for native in (0x80096010, 0x800B0111, 0x800B0101, 0x80096004):
                with self.subTest(status=status, native_status=hex(native)):
                    self.assert_rejected(status=status, native_status=native - 0x100000000)

    def test_primary_digest_failure_cannot_use_the_untrusted_root_exception(self):
        self.assert_rejected(status="HashMismatch", native_status=-2146762487)

    def test_installer_accepts_pinned_test_untrusted_root(self):
        unsigned, signed = self.installer_fixture()
        result = self.invoke(status="UnknownError", native_status=-2146762487,
                             installer=signed, unsigned_installer=unsigned)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tampered_or_missing_dependency_is_rejected_before_owned_files_change(self):
        dependency = self.destination / "_internal/vendor.dll"
        dependency.write_bytes(b"TAMPERED")
        self.assert_rejected()
        dependency.unlink()
        self.assert_rejected()

    def test_extra_unsigned_dependency_and_tampered_unsigned_owned_pe_are_rejected(self):
        extra = self.write(self.destination, "_internal/extra.dll", b"EXTRA")
        self.assert_rejected()
        extra.unlink()
        self.write(self.destination, OWN[0], b"TAMPERED")
        self.assert_rejected()

    def test_noncanonical_destination_owned_path_is_rejected_before_copy(self):
        path = self.destination / OWN[0]
        temporary = path.with_name("rename-temporary.exe")
        path.rename(temporary)
        temporary.rename(path.with_name("halobattery.exe"))
        self.assert_rejected()

    def test_signed_section_and_original_overlay_must_match_even_with_expected_signer(self):
        for offset in (0x205, len(self.original[OWN[0]]) - 1):
            with self.subTest(offset=offset):
                damaged = bytearray(self.expected_signed[OWN[0]])
                damaged[offset] ^= 0x01
                self.write(self.signed, OWN[0], damaged)
                self.assert_rejected()
        self.write(self.signed, OWN[0], self.expected_signed[OWN[0]])

    def test_unsigned_bytes_cannot_be_accepted_as_signed_by_a_stubbed_status(self):
        self.write(self.signed, OWN[0], self.original[OWN[0]])
        self.assert_rejected()

    def test_trailing_bytes_and_malformed_certificate_boundaries_are_rejected(self):
        original = self.expected_signed[OWN[0]]
        for data in (original + b"EXTRA", original[:-1], original[:100]):
            with self.subTest(length=len(data)):
                self.write(self.signed, OWN[0], data)
                self.assert_rejected()

    def test_hard_links_in_signed_or_destination_tree_are_rejected(self):
        for root, relative in ((self.signed, OWN[0]), (self.destination, "_internal/vendor.dll")):
            with self.subTest(root=root.name):
                path = root / relative
                extra = self.root / (root.name + "-hardlink")
                os.link(path, extra)
                try:
                    self.assert_rejected()
                finally:
                    extra.unlink()

    def test_junction_directory_is_rejected_even_when_empty(self):
        target = self.root / "empty-target"
        target.mkdir()
        link = self.signed / "junction"
        environment = dict(os.environ, LINK=str(link), TARGET=str(target))
        made = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction -Path $env:LINK -Target $env:TARGET | Out-Null"],
            env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(made.returncode, 0, made.stderr)
        try:
            self.assert_rejected()
        finally:
            link.rmdir()

    def installer_fixture(self):
        unsigned = self.write(self.root / "installer-input", "HaloBattery-test-setup.exe", unsigned_pe(b"installer"))
        signed = self.write(self.root / "installer-output", "HaloBattery-test-setup.exe", signed_pe(unsigned.read_bytes()))
        return unsigned, signed

    def inno_helper_fixture(self):
        original = unsigned_pe(b'inno-helper')
        unsigned = self.write(self.root / 'helper-input', 'inno-helper-amd64.exe', original)
        signed = self.write(self.root / 'helper-output', 'inno-helper-amd64.exe', signed_pe(original))
        return unsigned, signed

    def test_inno_helper_accepts_exact_original_and_does_not_modify_files(self):
        unsigned, signed = self.inno_helper_fixture()
        before = (unsigned.read_bytes(), signed.read_bytes())
        result = self.invoke(inno_helper=signed, unsigned_inno_helper=unsigned)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, (unsigned.read_bytes(), signed.read_bytes()))

    def test_inno_helper_rejects_wrong_names_and_extra_files(self):
        unsigned, signed = self.inno_helper_fixture()
        for path in (unsigned, signed):
            with self.subTest(path=path, defect='name'):
                wrong = path.with_name('other.exe')
                path.rename(wrong)
                self.assert_rejected(inno_helper=wrong if path == signed else signed,
                                     unsigned_inno_helper=wrong if path == unsigned else unsigned)
                wrong.rename(path)
            with self.subTest(path=path, defect='extra'):
                extra = self.write(path.parent, '.unexpected', b'extra')
                self.assert_rejected(inno_helper=signed, unsigned_inno_helper=unsigned)
                extra.unlink()

    def test_inno_helper_rejects_body_changes_mismatched_source_and_bad_signatures(self):
        unsigned, signed = self.inno_helper_fixture()
        before = signed.read_bytes()
        damaged = bytearray(before)
        damaged[0x205] ^= 1
        signed.write_bytes(damaged)
        self.assert_rejected(inno_helper=signed, unsigned_inno_helper=unsigned)
        signed.write_bytes(before)
        original = unsigned.read_bytes()
        unsigned.write_bytes(unsigned_pe(b'foreign-helper'))
        self.assert_rejected(inno_helper=signed, unsigned_inno_helper=unsigned)
        unsigned.write_bytes(original)
        for options in (dict(signer='B'*40), dict(status='HashMismatch'),
                        dict(signing_mode='Production', production_pin=THUMBPRINT)):
            with self.subTest(options=options):
                self.assert_rejected(inno_helper=signed, unsigned_inno_helper=unsigned, **options)

    def test_installer_exact_path_and_original_content_are_accepted(self):
        unsigned, signed = self.installer_fixture()
        result = self.invoke(installer=signed, unsigned_installer=unsigned)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_rejects_missing_test_or_untrusted_pin(self):
        for pin in ('', '17F9F411C424674855A077F9A120A7A434990AF1', THUMBPRINT):
            with self.subTest(pin=pin):
                self.assert_rejected(signing_mode='Production', production_pin=pin)

    def test_additional_signature_smoke_check_accepts_pin_and_rejects_errors(self):
        path = self.signed / OWN[0]
        self.assertEqual(self.invoke(signature_path=path).returncode, 0)
        for options in (dict(signer='B'*40), dict(status='HashMismatch'),
                        dict(signing_mode='Production', production_pin=THUMBPRINT)):
            with self.subTest(options=options):
                self.assert_rejected(signature_path=path, **options)

    def test_production_rejects_trusted_but_untimestamped_signature(self):
        self.assert_rejected(signing_mode='Production', production_pin=THUMBPRINT, status='Valid')

    def test_production_checks_native_trust_and_real_timestamp_content(self):
        for native in (0, -2146762487, -2146762485):
            with self.subTest(native=native):
                self.assert_rejected(signing_mode='Production', production_pin=THUMBPRINT,
                                     status='Valid', timestamp=True, native_status=native)

    def internal_fixture(self):
        name = 'uninst-6.7.4-a39a7062cf.e32'
        self.signed = self.root / 'internal-signed'
        self.destination = self.root / 'internal-original'
        original = unsigned_pe(b'inno')
        self.write(self.signed, name, signed_pe(original))
        self.write(self.destination, name, original)
        self.inventory.write_text(json.dumps({name: hashlib.sha256(original).hexdigest()}), encoding='utf-8')
        return name

    def test_internal_cache_manifest_accepts_only_exact_generated_content(self):
        name = self.internal_fixture()
        result = self.invoke(internal_manifest=self.inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.destination / name).read_bytes(), (self.signed / name).read_bytes())

    def test_internal_manifest_rejects_traversal_unknown_names_and_wrong_hash(self):
        self.internal_fixture()
        for name, digest in (('../evil.exe', 'A'*64), ('evil.exe', 'A'*64),
                             ('uninst-6.7.4-a39a7062cf.e32', 'B'*64)):
            self.inventory.write_text(json.dumps({name: digest}), encoding='utf-8')
            with self.subTest(name=name):
                self.assert_rejected(internal_manifest=self.inventory)

    def test_installer_wrong_filename_extra_file_or_changed_content_is_rejected(self):
        unsigned, signed = self.installer_fixture()
        wrong = signed.with_name("other-setup.exe")
        signed.rename(wrong)
        self.assert_rejected(installer=wrong, unsigned_installer=unsigned)
        wrong.rename(signed)
        extra = self.write(signed.parent, ".extra", b"EXTRA")
        self.hide(extra)
        self.assert_rejected(installer=signed, unsigned_installer=unsigned)
        extra.unlink()
        damaged = bytearray(signed.read_bytes())
        damaged[0x205] ^= 1
        signed.write_bytes(damaged)
        self.assert_rejected(installer=signed, unsigned_installer=unsigned)


if __name__ == "__main__":
    unittest.main()
