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
    [string]$SignedCacheDirectory,
    [Parameter(Mandatory)][string]$SignedHelperPath
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
$SignedHelperPath = Resolve-Regular $SignedHelperPath $false
foreach ($path in @($WorkDirectory, $OutputDirectory)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $null = Resolve-Regular $path $true
}
$WorkDirectory = (Resolve-Path -LiteralPath $WorkDirectory).Path
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
if (Test-Path -LiteralPath (Join-Path $OutputDirectory "$OutputBaseFilename.exe")) {
    throw 'Installer output already exists; use a fresh output directory'
}
# Clone the verified compiler privately: only Setup.e32 is changed. Its original
# bytes and embedded helper are pinned by the patch utility; no signing credential
# or external signing command enters this compiler process.
$originalTemplate = Join-Path (Split-Path $CompilerPath -Parent) 'Setup.e32'
$null = Resolve-Regular $originalTemplate $false
$privateCompiler = Join-Path $WorkDirectory ('compiler-' + $Stage.ToLowerInvariant())
if (Test-Path -LiteralPath $privateCompiler) { throw 'Use a fresh private compiler directory' }
New-Item -ItemType Directory -Path $privateCompiler | Out-Null
foreach ($entry in Get-ChildItem -LiteralPath (Split-Path $CompilerPath -Parent) -Force -Recurse) {
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $entry.LinkType) { throw 'Linked compiler input' }
}
Copy-Item -Path (Join-Path (Split-Path $CompilerPath -Parent) '*') -Destination $privateCompiler -Recurse
$patchedTemplate = Join-Path $privateCompiler 'Setup.e32'
Remove-Item -LiteralPath $patchedTemplate
$patcher = Join-Path $repo 'tools/inno_helper.py'
$patchMessages = @(& python $patcher patch-template --template $originalTemplate --signed-helper $SignedHelperPath --output $patchedTemplate)
if ($LASTEXITCODE -ne 0) { throw 'Inno helper patch failed' }
$patchReceipt = ($patchMessages -join "`n") | ConvertFrom-Json
if ($patchReceipt.source_sha256 -cne (Get-FileHash -LiteralPath $originalTemplate -Algorithm SHA256).Hash.ToLowerInvariant() -or
    $patchReceipt.output_sha256 -cne (Get-FileHash -LiteralPath $patchedTemplate -Algorithm SHA256).Hash.ToLowerInvariant()) {
    throw 'Inno template receipt does not match files'
}
if ((Get-FileHash -LiteralPath (Join-Path $privateCompiler (Split-Path $CompilerPath -Leaf)) -Algorithm SHA256).Hash -cne
    (Get-FileHash -LiteralPath $CompilerPath -Algorithm SHA256).Hash) { throw 'Private compiler changed' }
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
    originalData=(Get-FileHash -LiteralPath $originalTemplate -Algorithm SHA256).Hash
    originalHelper="388A796580234EFC95F3B1C70AD4CB44BFDDC7BA0F9203BF4902B9929B136F95"
    signedHelper=(Get-FileHash -LiteralPath $SignedHelperPath -Algorithm SHA256).Hash
    patchedData=$patchReceipt.output_sha256
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
    "/DSignedUninstallerCache=$cache", "/DSignedInnoHelper=1", "/F$OutputBaseFilename", "/O$OutputDirectory", $script)
function Invoke-InnoCompiler {
    # Preparation deliberately stops with native exit 2. Collect it without
    # PowerShell turning that expected stop into a terminating native error.
    # This preference override is confined to this function's scope.
    $PSNativeCommandUseErrorActionPreference = $false
    $privateCompilerPath = Join-Path $privateCompiler (Split-Path $CompilerPath -Leaf)
    $messages = @(& $privateCompilerPath @arguments 2>&1)
    return @{ messages=$messages; code=$LASTEXITCODE }
}
$compiled = Invoke-InnoCompiler
$messages = $compiled.messages
$code = $compiled.code
if ((Get-SourceDigest) -cne $binding.payload) { throw 'Installer payload changed during compilation' }
$messages | ForEach-Object { "$_" } | Set-Content -LiteralPath (Join-Path $WorkDirectory ($Stage.ToLowerInvariant() + '.log')) -Encoding utf8
# Reject a style/template selection that bypassed our patched Setup.e32.
# Reading the resource is safe even before signing the generated engine.
$helperIndex = 0
foreach ($engine in Get-ChildItem -LiteralPath $cache -File) {
    $extracted = Join-Path $WorkDirectory ("embedded-helper-" + $Stage.ToLowerInvariant() + "-$helperIndex.exe")
    $null = & python $patcher extract-engine --engine $engine.FullName --output $extracted
    if ($LASTEXITCODE -ne 0 -or
        (Get-FileHash -LiteralPath $extracted -Algorithm SHA256).Hash -cne $binding.signedHelper) {
        throw 'Compiler engine did not retain the verified signed helper'
    }
    $helperIndex++
}
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
