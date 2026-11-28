"""Real public timestamp cryptography, isolated from Windows/file trust."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_test_signed_payload import HELPER, PWSH, ROOT, unsigned_pe

FIXTURE = ROOT / 'tests/fixtures/signing/timestamped-cms.b64'
RUNNER = r'''
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile($env:HELPER,[ref]$null,[ref]$null)
foreach($name in @('Get-PeLayout','Assert-ProductionTimestamp')) {
    $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    . ([scriptblock]::Create($node.Extent.Text))
}
$cms=[Security.Cryptography.Pkcs.SignedCms]::new()
$cms.Decode([Convert]::FromBase64String([IO.File]::ReadAllText($env:FIXTURE)))
$CertificateThumbprint=$cms.SignerInfos[0].Certificate.Thumbprint
$rsa=$null; $cert=$null
try {
    if($env:CASE -ne 'positive') {
        $rsa=[Security.Cryptography.RSA]::Create(2048)
        $request=[Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=Offline timestamp binding test', $rsa,
            [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $cert=$request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddDays(1))
        $signer=[Security.Cryptography.Pkcs.CmsSigner]::new($cert)
        $signer.DigestAlgorithm=[Security.Cryptography.Oid]::new('2.16.840.1.101.3.4.2.1')
        if($env:CASE -eq 'foreign') {
            $attribute=@($cms.SignerInfos[0].UnsignedAttributes | Where-Object {$_.Oid.Value -eq '1.3.6.1.4.1.311.3.3.1'})[0]
            $other=[Security.Cryptography.Pkcs.SignedCms]::new($cms.ContentInfo)
            $other.ComputeSignature($signer)
            $other.SignerInfos[0].AddUnsignedAttribute($attribute.Values[0])
            $cms=$other
            $CertificateThumbprint=$cert.Thumbprint
        } else {
            $cms.SignerInfos[0].ComputeCounterSignature($signer)
        }
    }
    $encoded=$cms.Encode()
    $original=[IO.File]::ReadAllBytes($env:PE)
    $length=8+$encoded.Length
    $table=[byte[]]::new(($length+7)-band -8)
    [BitConverter]::GetBytes([int]$length).CopyTo($table,0)
    [BitConverter]::GetBytes([uint16]0x200).CopyTo($table,4)
    [BitConverter]::GetBytes([uint16]2).CopyTo($table,6)
    $encoded.CopyTo($table,8)
    [BitConverter]::GetBytes([int]$original.Length).CopyTo($original,0x98+112+32)
    [BitConverter]::GetBytes([int]$table.Length).CopyTo($original,0x98+112+36)
    [IO.File]::WriteAllBytes($env:PE,[byte[]]($original+$table))
    Assert-ProductionTimestamp $env:PE
    'accepted'
} catch { [Console]::Error.WriteLine($_.Exception.Message);exit 1 }
finally {if($cert){$cert.Dispose()};if($rsa){$rsa.Dispose()}}
'''


@unittest.skipUnless(os.name == 'nt' and PWSH, 'PowerShell 7 on Windows required')
class Rfc3161BindingTests(unittest.TestCase):
    def invoke(self, case):
        with tempfile.TemporaryDirectory(prefix='halo timestamp binding ') as temporary:
            root = Path(temporary)
            pe = root / 'inert.exe'
            pe.write_bytes(unsigned_pe(b'timestamp fixture'))
            runner = root / 'runner.ps1'
            runner.write_text(RUNNER, encoding='utf-8')
            return subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', str(runner)],
                                  env=dict(os.environ, HELPER=str(HELPER), FIXTURE=str(FIXTURE),
                                           PE=str(pe), CASE=case),
                                  text=True, capture_output=True, timeout=30)

    def test_real_public_token_matches_its_original_signer(self):
        decoded = base64.b64decode(FIXTURE.read_text(encoding='ascii'))
        self.assertEqual(hashlib.sha256(decoded).hexdigest(),
                         '7141765a8b1dfe52abac5dcc7df176236a4e32d3d4d5a68978872ab09c4708dd')
        result = self.invoke('positive')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_token_cannot_timestamp_a_different_valid_signature(self):
        result = self.invoke('foreign')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not bind', result.stderr)

    def test_mixed_valid_legacy_and_rfc3161_timestamps_are_rejected(self):
        result = self.invoke('legacy')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Legacy timestamp', result.stderr)
