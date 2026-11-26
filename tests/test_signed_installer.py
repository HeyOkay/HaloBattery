"""Smoke harness admission/capture/cleanup safeguards; no installer is executed."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/test_signed_installer.ps1'
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')


@unittest.skipUnless(PWSH, 'PowerShell 7 required')
class InstallerSmokeSafetyTests(unittest.TestCase):
    def invoke(self, body):
        loader = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:SMOKE_SCRIPT,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($node in $ast.EndBlock.Statements){
    if($node -is [Management.Automation.Language.FunctionDefinitionAst]){ . ([scriptblock]::Create($node.Extent.Text)) }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'check.ps1'
            path.write_text(loader + body, encoding='utf-8')
            env = dict(os.environ, SMOKE_SCRIPT=str(SCRIPT))
            return subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', str(path)],
                                  env=env, text=True, capture_output=True, timeout=30)

    def assert_ok(self, body):
        result = self.invoke(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_or_self_hosted_execution_is_rejected(self):
        self.assert_ok(r'''
foreach($kind in @('local','self-hosted')){
 $env:GITHUB_ACTIONS=if($kind -eq 'local'){'false'}else{'true'}
 $env:RUNNER_ENVIRONMENT=$kind; $env:RUNNER_OS='Windows'; $env:RUNNER_TEMP='C:\runner'
 $rejected=$false; try { Assert-SmokeHost } catch { $rejected=$true }
 if(-not $rejected){throw 'Unsafe host accepted'}
}
''')

    def test_paths_cannot_escape_runner_root(self):
        self.assert_ok(r'''
$root=Join-Path ([IO.Path]::GetTempPath()) 'smoke-root'
$null=Assert-UnderRoot (Join-Path $root 'child') $root
foreach($bad in @($root,($root+'-neighbor'),(Join-Path $root '../escape'))){
 $rejected=$false; try{$null=Assert-UnderRoot $bad $root}catch{$rejected=$true}
 if(-not $rejected){throw 'Escaping path accepted'}
}
''')

    def test_existing_install_or_process_blocks_installation(self):
        self.assert_ok(r'''
$env:LOCALAPPDATA='C:\local'; $env:APPDATA='C:\roaming'
function Test-Path {param($LiteralPath) return $true}
$rejected=$false; try{Assert-NoExistingInstall}catch{$rejected=$true}
if(-not $rejected){throw 'Existing install accepted'}
function Test-Path {param($LiteralPath) return $false}
function Get-Process {param($Name,$ErrorAction) return @([pscustomobject]@{Id=42})}
$rejected=$false; try{Assert-NoExistingInstall}catch{$rejected=$true}
if(-not $rejected){throw 'Running app accepted'}
''')

    def test_clean_host_passes_admission(self):
        self.assert_ok(r'''
$env:GITHUB_ACTIONS='true';$env:RUNNER_ENVIRONMENT='github-hosted';$env:RUNNER_OS='Windows';$env:RUNNER_TEMP='C:\runner'
$env:LOCALAPPDATA='C:\local';$env:APPDATA='C:\roaming'
function Test-Path {param($LiteralPath) return $false}
function Get-Process {param($Name,$ErrorAction) return @()}
function Get-ItemProperty {param($LiteralPath,$Name,$ErrorAction) return $null}
Assert-SmokeHost
Assert-NoExistingInstall
''')

    def test_absent_or_unrecognized_temporary_engine_fails(self):
        self.assert_ok(r'''
foreach($records in @(@{},@{file=@{verified=$false}},@{file=@{verified=$true;role='helper'}})){
 $rejected=$false;try{Assert-CapturedEngines $records}catch{$rejected=$true}
 if(-not $rejected){throw 'Missing/unknown engine accepted'}
}
Assert-CapturedEngines @{file=@{verified=$true;role='engine'};helper=@{verified=$true;role='helper'}}
''')

    def test_cleanup_never_accepts_outside_or_unrecognized_executable(self):
        self.assert_ok(r'''
$root=Join-Path ([IO.Path]::GetTempPath()) 'smoke-root'
$hashes=[Collections.Generic.HashSet[string]]::new();$null=$hashes.Add('GOOD')
function Test-Path {param($LiteralPath,$PathType) return $true}
function Get-RegularSmokePath {param($Path,$Directory) return $Path}
function Get-FileHash {param($LiteralPath,$Algorithm) return @{Hash='BAD'}}
if(Test-OwnedCleanupCandidate (Join-Path $root 'unins000.exe') $root $hashes){throw 'Unknown cleanup accepted'}
function Get-FileHash {param($LiteralPath,$Algorithm) return @{Hash='GOOD'}}
if(Test-OwnedCleanupCandidate (Join-Path ([IO.Path]::GetTempPath()) 'outside.exe') $root $hashes){throw 'Outside cleanup accepted'}
if(-not (Test-OwnedCleanupCandidate (Join-Path $root 'unins000.exe') $root $hashes)){throw 'Verified owned cleanup rejected'}
''')

    def test_parent_exit_does_not_complete_a_live_temporary_child(self):
        self.assert_ok(r'''
$now=[datetime]::UtcNow
if(-not (Test-SmokeProcessPending $true 1 $null $now)){throw 'Live child ignored after launcher exit'}
if(-not (Test-SmokeProcessPending $true 0 $now $now)){throw 'Discovery grace skipped'}
if(Test-SmokeProcessPending $true 0 ($now.AddMilliseconds(-301)) $now){throw 'Completed child never settles'}
if(-not (Test-SmokeProcessPending $false 0 ($now.AddSeconds(-1)) $now)){throw 'Live launcher ignored'}
''')

    def test_unknown_capture_is_retained_with_signer_metadata_in_failed_stage_receipt(self):
        self.assert_ok(r'''
Set-StrictMode -Version Latest
$directory=Join-Path ([IO.Path]::GetTempPath()) ('smoke-evidence-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $directory | Out-Null
try {
 function Get-AuthenticodeSignature {param($LiteralPath)
  return [pscustomobject]@{Status='Valid';SignatureType='Authenticode';
   SignerCertificate=[pscustomobject]@{Thumbprint='SIGNER';Subject='CN=fixture'};
   TimeStamperCertificate=[pscustomobject]@{Thumbprint='TIME';Subject='CN=timestamp'}}
 }
 $captures=@{};$hashes=[Collections.Generic.HashSet[string]]::new()
 $record=@{stage='install';status='failed';error='unknown engine';temporary_executables=$captures}
 $bytes=[byte[]]::new(128);$bytes[0]=0x4d;$bytes[1]=0x5a
 Save-SmokeCapture 'C:\isolated\unknown.tmp' $bytes $directory $hashes $captures 'C:\isolated'
 $rejected=$false;try{Assert-CapturedEngines $captures}catch{$rejected=$true}
 if(-not $rejected){throw 'Diagnostic retention allowed unknown execution'}
 Write-SmokeStageReceipt $directory $captures $record
 $receipt=Get-Content -LiteralPath (Join-Path $directory 'capture-receipt.json') -Raw | ConvertFrom-Json -AsHashtable
 if($receipt.stage -ne 'install' -or $receipt.status -ne 'failed' -or $receipt.error -ne 'unknown engine'){throw 'Failure stage omitted'}
 $capture=$receipt.temporary_executables['C:\isolated\unknown.tmp']
 if($capture.verified -or $capture.length -ne 128 -or $capture.signature.signer_thumbprint -ne 'SIGNER' -or
    $capture.signature.timestamp_thumbprint -ne 'TIME'){throw 'Incomplete capture metadata'}
 if([IO.Path]::GetExtension($capture.retained_path) -ne '.bin' -or
    -not (Test-Path -LiteralPath $capture.retained_path)){throw 'Unknown bytes not retained safely'}
 if((Get-FileHash -LiteralPath $capture.retained_path -Algorithm SHA256).Hash -ne $capture.hash){throw 'Receipt does not match retained bytes'}
 # Distinct reads remain available, and a later complete verified read wins.
 $bytes[127]=1
 $known=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes));$null=$hashes.Add($known)
 Save-SmokeCapture 'C:\isolated\unknown.tmp' $bytes $directory $hashes $captures 'C:\isolated'
 if($captures['C:\isolated\unknown.tmp'].snapshots.Count -ne 2 -or -not $captures['C:\isolated\unknown.tmp'].verified){throw 'Snapshot history lost'}
} finally {Remove-Item -LiteralPath $directory -Recurse -Force}
''')

    def test_helper_requires_exact_hash_and_private_expected_path(self):
        self.assert_ok(r'''
$root=Join-Path ([IO.Path]::GetTempPath()) 'isolated-stage'
$engines=[Collections.Generic.HashSet[string]]::new();$null=$engines.Add('ENGINE')
$helpers=[Collections.Generic.HashSet[string]]::new();$null=$helpers.Add('HELPER')
$good=Join-Path $root 'is-123.tmp/_isetup/_setup64.tmp'
if((Get-SmokeCaptureRole $good 'HELPER' $root $engines $helpers) -ne 'helper'){throw 'Verified helper rejected'}
foreach($path in @((Join-Path $root 'other/_setup64.tmp'),(Join-Path $root '_isetup/_setup32.tmp'),(Join-Path ($root+'-neighbor') '_isetup/_setup64.tmp'))){
 if((Get-SmokeCaptureRole $path 'HELPER' $root $engines $helpers) -ne 'unknown'){throw 'Wrong helper path accepted'}
}
if((Get-SmokeCaptureRole $good 'WRONG' $root $engines $helpers) -ne 'unknown'){throw 'Wrong helper bytes accepted'}
if((Get-SmokeCaptureRole $good 'ENGINE' $root $engines $helpers) -ne 'engine'){throw 'Engine identity lost'}
if((Get-SmokeCaptureRole $good 'HELPER' $root $engines $null) -ne 'unknown'){throw 'Unverified helper accepted'}
''')

    def test_helper_extraction_requires_independent_signature_and_identical_resources(self):
        self.assert_ok(r'''
Set-StrictMode -Version Latest
$directory=Join-Path ([IO.Path]::GetTempPath()) ('smoke-helper-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $directory | Out-Null
try {
 function python {
  $output=$args[[array]::IndexOf($args,'--output')+1]
  $engine=$args[[array]::IndexOf($args,'--engine')+1]
  $byte=if($engine -eq 'unsigned'){0}elseif($engine -eq 'different'){2}else{1}
  [IO.File]::WriteAllBytes($output,[byte[]]@($byte));$global:LASTEXITCODE=0
 }
 function Verify-FixtureHelper {param($SignaturePath,$SigningMode,$CertificateThumbprint)
  if($SigningMode -ne 'Test' -or $CertificateThumbprint -ne 'fixture-pin'){throw 'Signing policy not forwarded'}
  if([IO.File]::ReadAllBytes($SignaturePath)[0] -eq 0){throw 'Unsigned helper rejected'}
 }
 foreach($kind in @('unsigned','mismatch','valid')){
  $output=Join-Path $directory $kind;New-Item -ItemType Directory -Path $output | Out-Null
  $inputs=if($kind -eq 'unsigned'){@('unsigned')}elseif($kind -eq 'mismatch'){@('signed','different')}else{@('signed','signed')}
  $rejected=$false
  try{$hashes=Get-VerifiedSmokeHelperHashes $inputs $output 'fake-helper.py' 'Verify-FixtureHelper' 'Test' 'fixture-pin'}catch{$rejected=$true}
  if($kind -eq 'valid'){
   if($rejected -or $hashes.Count -ne 1){throw 'Identical independently signed resources rejected'}
  }elseif(-not $rejected){throw 'Unsigned or inconsistent helper accepted'}
 }
}finally{Remove-Item -LiteralPath $directory -Recurse -Force}
''')

    def test_only_paths_in_isolated_temp_are_joined_and_verified_children_stopped(self):
        self.assert_ok(r'''
$root=Join-Path ([IO.Path]::GetTempPath()) 'smoke-root'
function New-FakeProcess($path){
 $p=[pscustomobject]@{Path=$path;HasExited=$false}
 $p | Add-Member ScriptMethod Dispose {}
 $p | Add-Member ScriptMethod Kill {$script:killed+=@($this.Path)}
 $p | Add-Member ScriptMethod WaitForExit {param($timeout) return $true}
 return $p
}
$script:known=Join-Path $root 'known.tmp'
$script:unknown=Join-Path $root 'unknown.tmp'
$script:outside=Join-Path ($root+'-neighbor') 'outside.tmp'
function Get-Process {param($ErrorAction) @((New-FakeProcess $script:known),(New-FakeProcess $script:unknown),(New-FakeProcess $script:outside))}
$owned=@(Get-OwnedSmokeProcesses $root)
if($owned.Count -ne 2 -or $owned.path -contains $script:outside){throw 'Unrelated process joined'}
foreach($item in $owned){$item.process.Dispose()}
function Test-OwnedCleanupCandidate {param($Path,$Root,$AllowedHashes) return $Path -eq $script:known}
$script:killed=@()
Stop-OwnedSmokeProcesses $root @('GOOD')
if($script:killed.Count -ne 1 -or $script:killed[0] -ne $script:known){throw 'Unverified/unrelated process killed'}
''')


if __name__ == '__main__':
    unittest.main()
