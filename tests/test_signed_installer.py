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
foreach($records in @(@{},@{file=@{verified=$false}})){
 $rejected=$false;try{Assert-CapturedEngines $records}catch{$rejected=$true}
 if(-not $rejected){throw 'Missing/unknown engine accepted'}
}
Assert-CapturedEngines @{file=@{verified=$true}}
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
