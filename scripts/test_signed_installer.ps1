#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [Parameter(Mandatory)][string]$SignedCacheDirectory,
    [Parameter(Mandatory)][ValidateSet('Test','Production')][string]$SigningMode,
    [Parameter(Mandatory)][ValidatePattern('^[0-9A-Fa-f]{40}$')][string]$CertificateThumbprint,
    [string]$ReportPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$verifier = Join-Path (Split-Path $PSScriptRoot -Parent) 'tools/verify_test_signed_payload.ps1'

function Assert-SmokeHost {
    if ($env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
        $env:RUNNER_OS -cne 'Windows' -or -not $env:RUNNER_TEMP) {
        throw 'Installer execution is restricted to an isolated GitHub-hosted Windows runner'
    }
}
function Assert-UnderRoot([string]$Path, [string]$Root) {
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Smoke path escapes its isolated root' }
    return $full
}
function Assert-NoExistingInstall {
    $key = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{1E3A3904-9F39-4150-82EA-25D23968E2D6}_is1'
    foreach ($path in @("HKCU:\$key", "HKLM:\$key", "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{1E3A3904-9F39-4150-82EA-25D23968E2D6}_is1",
        'HKCU:\Software\Classes\AppUserModelId\HaloBattery', (Join-Path $env:LOCALAPPDATA 'Programs/HaloBattery'), (Join-Path $env:APPDATA 'HaloBattery'))) {
        if (Test-Path -LiteralPath $path) { throw 'A HaloBattery installation or profile already exists; refusing smoke installation' }
    }
    if (@(Get-Process -Name HaloBattery,HaloBattery.Settings -ErrorAction SilentlyContinue).Count) { throw 'HaloBattery is already running' }
    $run = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name HaloBattery -ErrorAction SilentlyContinue
    if ($null -ne $run) { throw 'A HaloBattery autostart entry already exists' }
}
function Assert-CapturedEngines($Captures) {
    if ($Captures.Count -eq 0) { throw 'No actual temporary Inno executable was captured' }
    foreach ($capture in $Captures.Values) {
        if (-not $capture.verified) { throw 'A temporary executable did not match the verified signed Inno cache' }
    }
    if (@($Captures.Values | Where-Object { $_.role -ceq 'engine' }).Count -eq 0) { throw 'No verified temporary Inno engine was captured' }
}
function Get-SmokeCaptureRole([string]$Path, [string]$Hash, [string]$TempDirectory, $EngineHashes, $HelperHashes) {
    try { $full = Assert-UnderRoot $Path $TempDirectory } catch { return 'unknown' }
    if ($EngineHashes.Contains($Hash)) { return 'engine' }
    if ($null -ne $HelperHashes -and $HelperHashes.Contains($Hash)) {
        $relative = [IO.Path]::GetRelativePath([IO.Path]::GetFullPath($TempDirectory), $full).Replace('\','/')
        if ($relative -cmatch '(^|/)_isetup/_setup64\.tmp$') { return 'helper' }
    }
    return 'unknown'
}
function Get-VerifiedSmokeHelperHashes($EnginePaths, [string]$OutputDirectory, [string]$HelperTool, [string]$Verifier, [string]$Mode, [string]$Pin) {
    $helperHashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $index = 0
    foreach ($engine in $EnginePaths) {
        $helper = Join-Path $OutputDirectory ("inno-helper-amd64-$index.exe")
        if (Test-Path -LiteralPath $helper) { throw 'Helper extraction target already exists' }
        & python $HelperTool extract-engine --engine $engine --output $helper | Out-Host
        if ($LASTEXITCODE) { throw 'Cannot extract the helper from the verified signed engine' }
        $helper = Get-RegularSmokePath $helper $false
        # Extraction authenticates origin; an independent embedded signature is
        # also mandatory. An unsigned vendor resource is never accepted.
        & $Verifier -SignaturePath $helper -SigningMode $Mode -CertificateThumbprint $Pin | Out-Host
        $null = $helperHashes.Add((Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash)
        $index++
    }
    if ($helperHashes.Count -ne 1) { throw 'Verified engines do not contain one identical signed helper' }
    return ,$helperHashes
}
function Test-OwnedCleanupCandidate([string]$Path, [string]$Root, $AllowedHashes) {
    try {
        $null = Assert-UnderRoot $Path $Root
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
        $null = Get-RegularSmokePath $Path $false
        return $AllowedHashes.Contains((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash)
    } catch { return $false }
}
function Get-RegularSmokePath([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -ne $Directory) { throw 'Unexpected smoke input path type' }
    for ($cursor = $item; $null -ne $cursor; $cursor = $(if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory })) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $cursor.LinkType) { throw 'Linked smoke paths are not accepted' }
    }
    return $item.FullName
}
function Get-OwnedSmokeProcesses([string]$TempDirectory) {
    # Inno's uninstaller can hand off to a temporary /SECONDPHASE copy.
    # Source: jrsoftware/issrc, Projects/Src/Setup.Uninstall.pas.
    # Identify live copies by executable path, never by a shared process name.
    foreach ($candidate in Get-Process -ErrorAction Stop) {
        $owned = $false
        try {
            if (-not $candidate.HasExited -and $candidate.Path) {
                $null = Assert-UnderRoot $candidate.Path $TempDirectory
                $owned = $true
            }
        } catch { $owned = $false }
        if ($owned) { @{ process=$candidate; path=$candidate.Path } }
        else { $candidate.Dispose() }
    }
}
function Test-SmokeProcessPending([bool]$ParentExited, [int]$OwnedCount, $QuietSince, [datetime]$Now) {
    # A quiet interval closes the parent-exit / child-discovery race without
    # treating the launcher's exit as completion of the uninstall itself.
    return (-not $ParentExited -or $OwnedCount -gt 0 -or $null -eq $QuietSince -or
        ($Now - $QuietSince).TotalMilliseconds -lt 300)
}
function Test-OwnedHelperCleanupCandidate([string]$Path, [string]$Root, $EngineHashes, $HelperHashes) {
    try {
        $null = Get-RegularSmokePath $Path $false
        return (Get-SmokeCaptureRole $Path (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash $Root $EngineHashes $HelperHashes) -ceq 'helper'
    } catch { return $false }
}
function Stop-OwnedSmokeProcesses([string]$TempDirectory, $AllowedHashes, $HelperHashes=$null) {
    foreach ($owned in @(Get-OwnedSmokeProcesses $TempDirectory)) {
        try {
            if ((Test-OwnedCleanupCandidate $owned.path $TempDirectory $AllowedHashes) -or
                (Test-OwnedHelperCleanupCandidate $owned.path $TempDirectory $AllowedHashes $HelperHashes)) {
                if (-not $owned.process.HasExited) { $owned.process.Kill(); $owned.process.WaitForExit(10000) | Out-Null }
            }
        } finally { $owned.process.Dispose() }
    }
}
function Save-SmokeCapture([string]$SourcePath, [byte[]]$Bytes, [string]$CaptureDirectory, $AllowedHashes, $Captures, [string]$TempDirectory, $HelperHashes=$null) {
    $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes))
    $role = Get-SmokeCaptureRole $SourcePath $hash $TempDirectory $AllowedHashes $HelperHashes
    $verified = $role -cne 'unknown'
    if (-not $Captures.ContainsKey($SourcePath)) {
        if ($Captures.Count -ge 32) { throw 'Unexpected number of temporary executables' }
        $Captures[$SourcePath] = @{ verified=$false; source_path=$SourcePath; snapshots=@{} }
    }
    $capture = $Captures[$SourcePath]
    if (-not $capture.snapshots.ContainsKey($hash)) {
        # Unknown files are evidence only. Never launch a retained snapshot.
        $all = @($Captures.Values | ForEach-Object { $_.snapshots.Values })
        $total = 0L
        foreach ($snapshot in $all) { $total += $snapshot.length }
        if ($all.Count -ge 64 -or ([long]$total + $Bytes.Length) -gt 268435456) { throw 'Temporary executable evidence limit exceeded' }
        $retained = Join-Path $CaptureDirectory ($hash + '.bin')
        [IO.File]::WriteAllBytes($retained, $Bytes)
        $signature = @{ status=$null; signature_type=$null; signer_thumbprint=$null; signer_subject=$null;
            timestamp_thumbprint=$null; timestamp_subject=$null; error=$null }
        try {
            $auth = Get-AuthenticodeSignature -LiteralPath $retained
            $signature.status = $auth.Status.ToString()
            $signature.signature_type = $auth.SignatureType.ToString()
            if ($null -ne $auth.SignerCertificate) {
                $signature.signer_thumbprint = $auth.SignerCertificate.Thumbprint
                $signature.signer_subject = $auth.SignerCertificate.Subject
            }
            if ($null -ne $auth.TimeStamperCertificate) {
                $signature.timestamp_thumbprint = $auth.TimeStamperCertificate.Thumbprint
                $signature.timestamp_subject = $auth.TimeStamperCertificate.Subject
            }
        } catch { $signature.error = $_.Exception.Message }
        $capture.snapshots[$hash] = @{ verified=$verified; role=$role; hash=$hash; length=$Bytes.Length;
            source_path=$SourcePath; retained_path=$retained; signature=$signature }
    }
    # Preserve a verified complete read if a later read sees transient bytes.
    if ($verified -or -not $capture.verified) {
        $capture.verified=$verified; $capture.role=$role; $capture.hash=$hash; $capture.length=$Bytes.Length
        $capture.retained_path=$capture.snapshots[$hash].retained_path
        $capture.signature=$capture.snapshots[$hash].signature
    }
}
function Write-SmokeStageReceipt([string]$CaptureDirectory, $Captures, $StageRecord) {
    $receipt = Join-Path $CaptureDirectory 'capture-receipt.json'
    if ($null -ne $StageRecord) {
        $StageRecord.temporary_executable_count=$Captures.Count; $StageRecord.receipt_path=$receipt
        $StageRecord | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $receipt -Encoding utf8
    } else { $Captures | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $receipt -Encoding utf8 }
}
function Invoke-CapturedProcess([string]$Executable, [string[]]$Arguments, [string]$TempDirectory, [string]$CaptureDirectory, $AllowedHashes, $StageRecord=$null, $HelperHashes=$null) {
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $info.Environment['TEMP'] = $TempDirectory
    $info.Environment['TMP'] = $TempDirectory
    $process = [Diagnostics.Process]::Start($info)
    $captures = @{}
    if ($null -ne $StageRecord) { $StageRecord.temporary_executables=$captures; $StageRecord.status='running' }
    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    $quietSince = $null
    $completed = $false
    try {
        do {
            foreach ($file in Get-ChildItem -LiteralPath $TempDirectory -Recurse -File -Force -ErrorAction SilentlyContinue) {
                if ($file.Extension -notin @('.tmp', '.exe') -or $file.Length -lt 64 -or $file.Length -gt 67108864) { continue }
                try {
                    $stream = [IO.File]::Open($file.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
                    try {
                        $memory = [IO.MemoryStream]::new()
                        try { $stream.CopyTo($memory); $bytes = $memory.ToArray() } finally { $memory.Dispose() }
                    } finally { $stream.Dispose() }
                    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) { continue }
                    Save-SmokeCapture $file.FullName $bytes $CaptureDirectory $AllowedHashes $captures $TempDirectory $HelperHashes
                } catch [IO.IOException] { continue }
            }
            if ([DateTime]::UtcNow -gt $deadline) {
                throw 'Installer process timed out'
            }
            $owned = @(Get-OwnedSmokeProcesses $TempDirectory)
            try {
                $now = [DateTime]::UtcNow
                if (-not $process.HasExited -or $owned.Count -gt 0) { $quietSince = $null }
                elseif ($null -eq $quietSince) { $quietSince = $now }
                $pending = Test-SmokeProcessPending $process.HasExited $owned.Count $quietSince $now
            } finally { foreach ($child in $owned) { $child.process.Dispose() } }
            if ($pending) { Start-Sleep -Milliseconds 20 }
        } while ($pending)
        if ($process.ExitCode -ne 0) { throw "Installer failed with exit code $($process.ExitCode)" }
        Assert-CapturedEngines $captures
        $completed = $true
        if ($null -ne $StageRecord) { $StageRecord.status='success' }
        return $captures
    } catch {
        if ($null -ne $StageRecord) { $StageRecord.status='failed'; $StageRecord.error=$_.Exception.Message }
        throw
    } finally {
        try { Write-SmokeStageReceipt $CaptureDirectory $captures $StageRecord }
        finally {
            # The explicit launcher was already verified before invocation. Never
            # terminate a whole descendant tree or kill processes by reused PID.
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(10000) | Out-Null }
            if (-not $completed) { Stop-OwnedSmokeProcesses $TempDirectory $AllowedHashes $HelperHashes }
            $process.Dispose()
        }
    }
}

Assert-SmokeHost
$records = [Collections.Generic.List[object]]::new()
$record = $null
$root = $null
if ($ReportPath) {
    $ReportPath = [IO.Path]::GetFullPath($ReportPath)
    if ([IO.Path]::GetExtension($ReportPath) -cne '.json' -or (Test-Path -LiteralPath $ReportPath)) { throw 'Use a new JSON smoke report path' }
    New-Item -ItemType Directory -Force -Path (Split-Path $ReportPath -Parent) | Out-Null
    $null = Get-RegularSmokePath (Split-Path $ReportPath -Parent) $true
}
try {
Assert-NoExistingInstall
$runner = Get-RegularSmokePath $env:RUNNER_TEMP $true
$root = Join-Path $runner ('halo-signed-install-' + [Guid]::NewGuid().ToString('N'))
$null = Assert-UnderRoot $root $runner
New-Item -ItemType Directory -Path $root | Out-Null
$root = Get-RegularSmokePath $root $true
if (-not $ReportPath) { $ReportPath = Join-Path $root 'smoke-report.json' }
$evidenceRoot = Join-Path (Split-Path $ReportPath -Parent) 'installer-smoke-evidence'
if (Test-Path -LiteralPath $evidenceRoot) { throw 'Smoke evidence directory already exists' }
New-Item -ItemType Directory -Path $evidenceRoot | Out-Null
$evidenceRoot = Get-RegularSmokePath $evidenceRoot $true
$InstallerPath = Get-RegularSmokePath $InstallerPath $false
$SignedCacheDirectory = Get-RegularSmokePath $SignedCacheDirectory $true
$cache = @(Get-ChildItem -LiteralPath $SignedCacheDirectory -Force)
if ($cache.Count -lt 1 -or $cache.Count -gt 2) { throw 'Unexpected signed Inno cache count' }
$hashes = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$enginePaths = [Collections.Generic.List[string]]::new()
foreach ($file in $cache) {
    if ($file.Name -cnotmatch '^uninst-6\.7\.4-[a-f0-9]{10}\.e(32|64)$') { throw 'Unexpected signed Inno cache filename' }
    $path = Get-RegularSmokePath $file.FullName $false
    & $verifier -SignaturePath $path -SigningMode $SigningMode -CertificateThumbprint $CertificateThumbprint
    $null = $hashes.Add((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash)
    $enginePaths.Add($path)
}
$helperDirectory = Join-Path $evidenceRoot 'verified-helpers'
New-Item -ItemType Directory -Path $helperDirectory | Out-Null
$helperHashes = Get-VerifiedSmokeHelperHashes $enginePaths $helperDirectory (Join-Path (Split-Path $PSScriptRoot -Parent) 'tools/inno_helper.py') $verifier $SigningMode $CertificateThumbprint
& $verifier -SignaturePath $InstallerPath -SigningMode $SigningMode -CertificateThumbprint $CertificateThumbprint
$install = Assert-UnderRoot (Join-Path $root 'installed') $root
$uninstalled = $false
try {
foreach ($stage in @('install', 'upgrade')) {
    $temp = Assert-UnderRoot (Join-Path $root ($stage + '-temp')) $root
    $capture = Assert-UnderRoot (Join-Path $evidenceRoot $stage) $evidenceRoot
    New-Item -ItemType Directory -Path $temp,$capture | Out-Null
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/TASKS=', "/DIR=$install", "/LOG=$(Join-Path $root ($stage + '.log'))")
    $record = @{stage=$stage; temporary_executable_count=0; temporary_executables=@{}; status='starting'}
    $records.Add($record)
    $observed = Invoke-CapturedProcess $InstallerPath $arguments $temp $capture $hashes $record $helperHashes
    $autostart = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name HaloBattery -ErrorAction SilentlyContinue
    if ($null -ne $autostart) { throw 'Silent smoke install enabled autostart' }
    $uninstaller = Join-Path $install 'unins000.exe'
    $null = Get-RegularSmokePath $uninstaller $false
    & $verifier -SignaturePath $uninstaller -SigningMode $SigningMode -CertificateThumbprint $CertificateThumbprint
    if (-not $hashes.Contains((Get-FileHash -LiteralPath $uninstaller -Algorithm SHA256).Hash)) { throw 'Installed uninstaller differs from the verified signed Inno cache' }
    $record.uninstaller_sha256=(Get-FileHash -LiteralPath $uninstaller -Algorithm SHA256).Hash
}
$temp = Join-Path $root 'uninstall-temp'
$capture = Assert-UnderRoot (Join-Path $evidenceRoot 'uninstall') $evidenceRoot
New-Item -ItemType Directory -Path $temp,$capture | Out-Null
$record = @{stage='uninstall'; temporary_executable_count=0; temporary_executables=@{}; status='starting'}
$records.Add($record)
$observed = Invoke-CapturedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $root 'uninstall.log')") $temp $capture $hashes $record $helperHashes
if ((Test-Path -LiteralPath (Join-Path $install 'HaloBattery.exe')) -or (Test-Path -LiteralPath $uninstaller)) { throw 'Uninstall left application executable files' }
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{1E3A3904-9F39-4150-82EA-25D23968E2D6}_is1'
if (Test-Path -LiteralPath $key) { throw 'Uninstall left the registration entry' }
$uninstalled = $true
@{status='success'; signing_mode=$SigningMode; root=$root; stages=@($records.ToArray())} |
    ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $ReportPath -Encoding utf8
Write-Output "Signed installer install/upgrade/uninstall verified: $root"
} finally {
    $cleanup = Join-Path $install 'unins000.exe'
    if (-not $uninstalled -and (Test-OwnedCleanupCandidate $cleanup $root $hashes)) {
        try {
            & $verifier -SignaturePath $cleanup -SigningMode $SigningMode -CertificateThumbprint $CertificateThumbprint
            $cleanupTemp = Join-Path $root 'cleanup-temp'
            $cleanupCapture = Assert-UnderRoot (Join-Path $evidenceRoot 'cleanup') $evidenceRoot
            New-Item -ItemType Directory -Force -Path $cleanupTemp,$cleanupCapture | Out-Null
            $cleanupRecord = @{stage='cleanup'; temporary_executable_count=0; temporary_executables=@{}; status='starting'}
            $records.Add($cleanupRecord)
            $null = Invoke-CapturedProcess $cleanup @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') $cleanupTemp $cleanupCapture $hashes $cleanupRecord $helperHashes
        } catch { Write-Warning 'Isolated smoke install could not be cleaned up; runner evidence retained' }
    }
}
} catch {
    if ($null -ne $record) { $record.status='failed'; $record.error=$_.Exception.Message }
    if ($ReportPath) {
        @{status='failed'; error=$_.Exception.Message; signing_mode=$SigningMode; root=$root; stages=@($records.ToArray())} |
            ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $ReportPath -Encoding utf8
    }
    throw
}
