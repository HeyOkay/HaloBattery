#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Prepare','Complete')][string]$Stage,
    [Parameter(Mandatory)][string]$CompilerPath,
    [Parameter(Mandatory)][string]$SourceDirectory,
    [Parameter(Mandatory)][ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')][string]$AppVersion,
    [Parameter(Mandatory)][string]$WorkDirectory,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][ValidatePattern('^HaloBattery-[A-Za-z0-9.-]+$')][string]$OutputBaseFilename,
    [string]$SignedCacheDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$script = Join-Path $repo 'installer/HaloBattery.iss'
function Resolve-Regular([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -ne $Directory) { throw "Unexpected path type: $Path" }
    for ($cursor = $item; $null -ne $cursor; $cursor = $(if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory })) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse path: $Path" }
    }
    return $item.FullName
}
$CompilerPath = Resolve-Regular $CompilerPath $false
$SourceDirectory = Resolve-Regular $SourceDirectory $true
foreach ($path in @($WorkDirectory, $OutputDirectory)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $null = Resolve-Regular $path $true
}
$WorkDirectory = (Resolve-Path -LiteralPath $WorkDirectory).Path
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
if (Test-Path -LiteralPath (Join-Path $OutputDirectory "$OutputBaseFilename.exe")) {
    throw 'Installer output already exists; use a fresh output directory'
}
$manifestPath = Join-Path $WorkDirectory 'internal-manifest.json'
$bindingPath = Join-Path $WorkDirectory 'binding.json'
function Get-SourceDigest {
    $records = [Collections.Generic.List[string]]::new()
    foreach ($entry in Get-ChildItem -LiteralPath $SourceDirectory -Force -Recurse) {
        if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $entry.LinkType) { throw 'Linked payload entry' }
        if (-not $entry.PSIsContainer) {
            $relative = [IO.Path]::GetRelativePath($SourceDirectory, $entry.FullName).Replace([char]92, [char]47)
            $records.Add($relative + "`0" + (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash)
        }
    }
    if (-not $records.Count) { throw 'Empty installer payload' }
    $lines = $records.ToArray()
    [Array]::Sort($lines, [StringComparer]::Ordinal)
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))))
}
$binding = @{ version=$AppVersion; output=$OutputBaseFilename
    compiler=(Get-FileHash -LiteralPath $CompilerPath -Algorithm SHA256).Hash
    script=(Get-FileHash -LiteralPath $script -Algorithm SHA256).Hash
    icon=(Get-FileHash -LiteralPath (Join-Path $repo 'halo.ico') -Algorithm SHA256).Hash
    payload=(Get-SourceDigest) }
$cache = Join-Path $WorkDirectory $(if ($Stage -eq 'Prepare') { 'unsigned-cache' } else { 'compiler-cache' })
if (Test-Path -LiteralPath $cache) { throw 'Use a fresh compiler cache directory' }
New-Item -ItemType Directory -Path $cache | Out-Null
if ($Stage -eq 'Complete') {
    if (-not $SignedCacheDirectory) { throw 'Signed cache directory is required' }
    $SignedCacheDirectory = Resolve-Regular $SignedCacheDirectory $true
    $originalBinding = Get-Content -LiteralPath $bindingPath -Raw | ConvertFrom-Json -AsHashtable
    if ($originalBinding.Count -ne $binding.Count) { throw 'Unexpected Inno binding fields' }
    foreach ($key in $binding.Keys) {
        if ($originalBinding[$key] -cne $binding[$key]) { throw "Inno input changed: $key" }
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
    $entries = @(Get-ChildItem -LiteralPath $SignedCacheDirectory -Force)
    if ($manifest.Count -lt 1 -or $entries.Count -ne $manifest.Count) { throw 'Unexpected signed cache files' }
    foreach ($entry in $entries) {
        $null = Resolve-Regular $entry.FullName $false
        if ($entry.Name -cnotmatch '^uninst-6\.7\.4-[a-f0-9]{10}\.e(32|64)$' -or
            -not $manifest.ContainsKey($entry.Name)) { throw 'Unexpected signed cache name' }
        # Full pinned-signer and unchanged-PE acceptance is performed by the
        # workflow verifier before this helper. Inno inputs are data, not commands.
        $signature = Get-AuthenticodeSignature -LiteralPath $entry.FullName
        if ($null -eq $signature.SignerCertificate -or $signature.SignatureType.ToString() -cne 'Authenticode') {
            throw 'Inno cache is missing an embedded signature'
        }
        Copy-Item -LiteralPath $entry.FullName -Destination (Join-Path $cache $entry.Name)
    }
}
$arguments = @('/Q', "/DAppVersion=$AppVersion", "/DSourceDir=$SourceDirectory",
    "/DSignedUninstallerCache=$cache", "/F$OutputBaseFilename", "/O$OutputDirectory", $script)
function Invoke-InnoCompiler {
    # Preparation deliberately stops with native exit 2. Collect it without
    # PowerShell turning that expected stop into a terminating native error.
    # This preference override is confined to this function's scope.
    $PSNativeCommandUseErrorActionPreference = $false
    $messages = @(& $CompilerPath @arguments 2>&1)
    return @{ messages=$messages; code=$LASTEXITCODE }
}
$compiled = Invoke-InnoCompiler
$messages = $compiled.messages
$code = $compiled.code
if ((Get-SourceDigest) -cne $binding.payload) { throw 'Installer payload changed during compilation' }
$messages | ForEach-Object { "$_" } | Set-Content -LiteralPath (Join-Path $WorkDirectory ($Stage.ToLowerInvariant() + '.log')) -Encoding utf8
if ($Stage -eq 'Prepare') {
    if ($code -ne 2 -or ($messages -join "`n") -notmatch 'Signed uninstaller mode is enabled') {
        throw "Unexpected Inno preparation outcome (exit $code)"
    }
    $entries = @(Get-ChildItem -LiteralPath $cache -Force)
    $manifest = @{}
    foreach ($entry in $entries) {
        $null = Resolve-Regular $entry.FullName $false
        if ($entry.Name -cnotmatch '^uninst-6\.7\.4-[a-f0-9]{10}\.e(32|64)$') { throw 'Unexpected generated cache name' }
        $manifest[$entry.Name] = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash
    }
    if ($manifest.Count -lt 1 -or $manifest.Count -gt 2 -or (Test-Path -LiteralPath (Join-Path $OutputDirectory "$OutputBaseFilename.exe"))) {
        throw 'Unexpected generated Inno signing input'
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8
    $binding | ConvertTo-Json | Set-Content -LiteralPath $bindingPath -Encoding utf8
    Write-Output "Prepared unsigned Inno cache: $cache"
    # GitHub's pwsh wrapper exits using the last native exit code. Consume only
    # this fully validated expected stop; failures above must remain failures.
    $global:LASTEXITCODE = 0
} else {
    if ($code -ne 0) { throw "Signed-cache Inno compilation failed (exit $code)" }
    $actual = @(Get-ChildItem -LiteralPath $cache -Force)
    if ($actual.Count -ne $manifest.Count -or @(Compare-Object @($manifest.Keys) @($actual.Name) -CaseSensitive).Count) {
        throw 'Inno requested a different uninstaller cache'
    }
    foreach ($entry in $actual) {
        if ((Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath (Join-Path $SignedCacheDirectory $entry.Name) -Algorithm SHA256).Hash) {
            throw 'Compiler cache changed during compilation'
        }
    }
    $output = Join-Path $OutputDirectory "$OutputBaseFilename.exe"
    $null = Resolve-Regular $output $false
    Write-Output $output
}
