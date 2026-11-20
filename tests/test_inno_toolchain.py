"""The pinned Inno loader must reject unverified tools before process creation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class InnoToolchainTests(unittest.TestCase):
    def run_loader(self, mode, download=False):
        with tempfile.TemporaryDirectory(prefix="halo inno [test] ") as temporary:
            root = Path(temporary)
            installer = root / "setup.exe"
            installer.write_bytes(b"test-owned fake installer; never executed")
            runner = root / "runner.ps1"
            runner.write_text(r'''
$ErrorActionPreference='Stop'
function global:Get-FileHash {
    param($LiteralPath,$Algorithm)
    if($Algorithm -ne 'SHA256'){throw 'Unexpected hash algorithm'}
    $hash='a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'
    if($env:MODE -eq 'hashMismatch'){$hash='wrong'}
    [pscustomobject]@{Hash=$hash}
}
function global:Get-AuthenticodeSignature {
    param($LiteralPath)
    $status='Valid';$subject='CN=Pyrsys B.V., O=Pyrsys B.V.'
    if($env:MODE -eq 'badSignature'){$status='NotSigned'}
    if($env:MODE -eq 'wrongPublisher'){$subject='CN=Someone Else'}
    [pscustomobject]@{Status=$status;SignerCertificate=[pscustomobject]@{Subject=$subject}}
}
function global:Invoke-WebRequest {
    param($Uri,$OutFile,[switch]$UseBasicParsing)
    if($Uri -ne 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe'){throw 'Unpinned URL'}
    [IO.File]::WriteAllText($OutFile,'test-owned download')
}
function global:Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,[switch]$Wait)
    [IO.File]::WriteAllText($env:TRACE,(ConvertTo-Json -InputObject @($ArgumentList)))
    if($WindowStyle -ne 'Hidden' -or -not $Wait -or -not $PassThru){throw 'Invalid process options'}
    $target=($ArgumentList | Where-Object {$_ -like '/DIR=*'}).Substring(6).TrimEnd('"')
    if($env:MODE -ne 'missingCompiler'){[IO.File]::WriteAllText((Join-Path $target 'ISCC.exe'),'fake compiler')}
    $code=0;if($env:MODE -eq 'extractionFailed'){$code=9}
    [pscustomobject]@{ExitCode=$code}
}
try {
    $options=@{ToolsDirectory=$env:TOOLS}
    if($env:DOWNLOAD -ne 'true'){$options.InstallerPath=$env:INSTALLER}
    $path=& $env:LOADER @options
    [IO.File]::WriteAllText($env:RESULT,$path)
    exit 0
} catch {[Console]::Error.WriteLine($_.Exception.Message);exit 1}
''', encoding="utf-8")
            trace, result_path = root / "trace.json", root / "result.txt"
            result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(runner)],
                env=dict(os.environ, MODE=mode, TRACE=str(trace), RESULT=str(result_path),
                         TOOLS=str(root / "tools [portable]"), INSTALLER=str(installer),
                         DOWNLOAD=str(download).lower(), LOADER=str(ROOT / "scripts/get_inno.ps1")),
                capture_output=True, text=True, timeout=30)
            args = json.loads(trace.read_text(encoding="utf-8-sig")) if trace.exists() else None
            path = result_path.read_text(encoding="utf-8") if result_path.exists() else None
            self.assertTrue(installer.is_file())
            return result, args, path

    def test_verified_local_or_downloaded_installer_uses_only_portable_hidden_extraction(self):
        for download in (False, True):
            with self.subTest(download=download):
                result, args, path = self.run_loader("success", download)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("/PORTABLE=1", args)
                self.assertIn("/CURRENTUSER", args)
                self.assertIn("/NOICONS", args)
                self.assertTrue(path.endswith("ISCC.exe"))

    def test_bad_digest_signature_or_publisher_never_executes_installer(self):
        for mode in ("hashMismatch", "badSignature", "wrongPublisher"):
            with self.subTest(mode=mode):
                result, args, path = self.run_loader(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(args)
                self.assertIsNone(path)

    def test_failed_extraction_or_missing_compiler_returns_no_build_tool(self):
        for mode in ("extractionFailed", "missingCompiler"):
            with self.subTest(mode=mode):
                result, args, path = self.run_loader(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNotNone(args)
                self.assertIsNone(path)
