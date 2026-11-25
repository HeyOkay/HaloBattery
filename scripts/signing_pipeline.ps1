#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Initialize','StageBuild','Prepare','Package','Finalize')][string]$Stage,
    [string]$BundleDirectory,
    [string]$SignedDirectory,
    [string]$UnsignedDirectory,
    [string]$WorkDirectory,
    [string]$OutputDirectory,
    [string]$CompilerPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$testPin = '17F9F411C424674855A077F9A120A7A434990AF1'
$own = @('HaloBattery.exe', '_internal/settings-ui/HaloBattery.Settings.exe')
$verifier = Join-Path $repo 'tools/verify_test_signed_payload.ps1'

function Regular([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -ne $Directory) { throw "Unexpected path type: $Path" }
    for ($cursor = $item; $null -ne $cursor; $cursor = $(if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory })) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $cursor.LinkType) { throw "Links are not accepted: $Path" }
    }
    return $item.FullName
}
function Fresh([string]$Path) {
    if (Test-Path -LiteralPath $Path) { throw "Expected a fresh directory: $Path" }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    return Regular $Path $true
}
function Inventory([string]$Path) {
    $base = Regular $Path $true
    $files = @{}
    foreach ($entry in Get-ChildItem -LiteralPath $base -Recurse -Force) {
        $null = Regular $entry.FullName $entry.PSIsContainer
        if (-not $entry.PSIsContainer) {
            $relative = [IO.Path]::GetRelativePath($base, $entry.FullName).Replace([char]92, [char]47)
            $files[$relative] = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash
        }
    }
    if (-not $files.Count) { throw 'Empty application inventory' }
    return ,$files
}
function Assert-Inventory([string]$Path, [string]$Manifest) {
    $expected = Get-Content -LiteralPath (Regular $Manifest $false) -Raw | ConvertFrom-Json -AsHashtable
    $actual = Inventory $Path
    if ($expected -isnot [Collections.IDictionary] -or $expected.Count -ne $actual.Count) { throw 'Application inventory changed' }
    foreach ($name in $actual.Keys) {
        if (-not $expected.ContainsKey($name) -or $expected[$name] -cne $actual[$name]) { throw "Application file changed: $name" }
    }
}
function Output([string]$Name, [string]$Value) {
    if ($Value.Contains("`n") -or $Value.Contains("`r")) { throw 'Invalid workflow output' }
    if ($env:GITHUB_OUTPUT) { "$Name=$Value" >> $env:GITHUB_OUTPUT }
}
function Version {
    $matches = @(Select-String -LiteralPath (Join-Path $repo 'halo_battery.pyw') -Pattern '^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$')
    if ($matches.Count -ne 1) { throw 'Expected exactly one numeric application version' }
    return $matches[0].Matches[0].Groups[1].Value
}
function Context {
    if ($env:SIGNING_MODE -cnotin @('Test','Production') -or $env:CERTIFICATE_THUMBPRINT -cnotmatch '^[A-Fa-f0-9]{40}$') { throw 'Missing signing context' }
    if (($env:SIGNING_MODE -ceq 'Test' -and $env:CERTIFICATE_THUMBPRINT -cne $testPin) -or
        ($env:SIGNING_MODE -ceq 'Production' -and $env:CERTIFICATE_THUMBPRINT -ieq $testPin)) { throw 'Signing certificate mode mismatch' }
    if ($env:GITHUB_REPOSITORY -cne 'HeyOkay/HaloBattery' -or $env:GITHUB_SHA -cnotmatch '^[0-9a-f]{40}$') { throw 'Unexpected source identity' }
    return @{ mode=$env:SIGNING_MODE; certificate_thumbprint=$env:CERTIFICATE_THUMBPRINT; version=(Version); source_commit=$env:GITHUB_SHA }
}
function Read-Bundle([string]$Path) {
    $context = Context
    $metadata = Get-Content -LiteralPath (Regular (Join-Path $Path 'signing.json') $false) -Raw | ConvertFrom-Json -AsHashtable
    if ($metadata.Count -ne 5) { throw 'Unexpected signing metadata fields' }
    foreach ($name in $context.Keys) {
        if ($metadata[$name] -cne $context[$name]) { throw "Signing metadata changed: $name" }
    }
    if ($metadata.icon_sha256 -cnotmatch '^[A-F0-9]{64}$') { throw 'Invalid icon digest' }
    return $metadata
}
function Assert-Icon($Metadata, [string]$Path) {
    if ((Get-FileHash -LiteralPath (Regular (Join-Path $Path 'halo.ico') $false) -Algorithm SHA256).Hash -cne $Metadata.icon_sha256) { throw 'Installer icon changed' }
    Copy-Item -LiteralPath (Join-Path $Path 'halo.ico') -Destination (Join-Path $repo 'halo.ico')
}
function Names($Metadata) {
    if ($Metadata.mode -ceq 'Test') {
        return @{ zip="HaloBattery-$($Metadata.version)-signpath-test.zip"; setup='HaloBattery-test-setup' }
    }
    return @{ zip="HaloBattery-$($Metadata.version).zip"; setup="HaloBattery-$($Metadata.version)-setup" }
}

if ($Stage -eq 'Initialize') {
    if ($env:GITHUB_REPOSITORY -cne 'HeyOkay/HaloBattery' -or $env:GITHUB_SHA -cnotmatch '^[0-9a-f]{40}$') { throw 'Unexpected signing repository or source' }
    $version = Version
    $test = $env:CONFIRMED_TEST -ceq 'true'
    $release = $env:CONFIRMED_RELEASE -ceq 'true'
    if ($test -and $release) { throw 'Select only one signing mode' }
    $productionTag = $env:GITHUB_EVENT_NAME -ceq 'push' -and $env:GITHUB_REF_TYPE -ceq 'tag'
    if ($productionTag) {
        if ($env:GITHUB_REF_NAME -cne "v$version") { throw 'Stable tag must exactly match VERSION' }
        $mode = 'Production'
    } elseif ($env:GITHUB_EVENT_NAME -ceq 'workflow_dispatch' -and $test -and
              $env:GITHUB_REF -ceq 'refs/heads/pre-release' -and $env:TEST_ENABLED -ceq 'true') {
        $mode = 'Test'
    } elseif ($env:GITHUB_EVENT_NAME -ceq 'workflow_dispatch' -and $release -and $env:GITHUB_REF -ceq 'refs/heads/main') {
        $mode = 'Production'
    } else { throw 'Signing requires an enabled explicit TEST on pre-release, production dry run on main, or a stable tag' }
    if ($mode -ceq 'Production') {
        if ($env:RELEASE_ENABLED -cne 'true') { throw 'Production signing is not enabled' }
        $settings = Get-Content -LiteralPath (Join-Path $repo '.github/signpath/production.json') -Raw | ConvertFrom-Json -AsHashtable
        if ($settings.Count -ne 1 -or -not $settings.ContainsKey('certificate_thumbprint')) { throw 'Invalid production certificate configuration' }
        $pin = $settings.certificate_thumbprint
        if ($pin -isnot [string] -or $pin -cnotmatch '^[A-F0-9]{40}$' -or $pin -ceq $testPin) { throw 'Review and pin the issued production certificate before signing' }
        & git fetch origin main
        if ($LASTEXITCODE) { throw 'Could not fetch main' }
        & git merge-base --is-ancestor $env:GITHUB_SHA origin/main
        if ($LASTEXITCODE) { throw 'Production signing source must belong to main' }
    } else { $pin = $testPin }
    Output 'mode' $mode
    Output 'certificate-thumbprint' $pin
    Output 'version' $version
    return
}
$context = Context
if ($Stage -eq 'StageBuild') {
    $output = Fresh $OutputDirectory
    $source = Join-Path $repo 'dist/HaloBattery'
    $inventory = Inventory $source
    New-Item -ItemType Directory -Path (Join-Path $output 'bundle'), (Join-Path $output 'payload') | Out-Null
    Copy-Item -LiteralPath $source -Destination (Join-Path $output 'bundle/HaloBattery') -Recurse
    Copy-Item -LiteralPath (Join-Path $repo 'halo.ico') -Destination (Join-Path $output 'bundle/halo.ico')
    $context.icon_sha256 = (Get-FileHash -LiteralPath (Join-Path $repo 'halo.ico') -Algorithm SHA256).Hash
    $context | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'bundle/signing.json') -Encoding utf8
    $inventory | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'bundle/unsigned-tree.json') -Encoding utf8
    foreach ($relative in $own) {
        $file = Regular (Join-Path $source $relative) $false
        if ((Get-Item -LiteralPath $file).VersionInfo.ProductName -cne 'HaloBattery') { throw 'Unexpected own executable' }
        $destination = Join-Path (Join-Path $output 'payload') $relative
        New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
        Copy-Item -LiteralPath $file -Destination $destination
    }
    return
}
if ($Stage -eq 'Prepare') {
    $metadata = Read-Bundle $BundleDirectory
    Assert-Icon $metadata $BundleDirectory
    & $verifier -SignedDirectory $SignedDirectory -DestinationDirectory (Join-Path $BundleDirectory 'HaloBattery') -InventoryPath (Join-Path $BundleDirectory 'unsigned-tree.json') -SigningMode $context.mode -CertificateThumbprint $context.certificate_thumbprint
    $output = Fresh $OutputDirectory
    Copy-Item -LiteralPath $BundleDirectory -Destination (Join-Path $output 'bundle') -Recurse
    $app = Join-Path $output 'bundle/HaloBattery'
    (Inventory $app) | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'bundle/accepted-tree.json') -Encoding utf8
    $names = Names $metadata
    & (Join-Path $repo 'scripts/inno_signing.ps1') -Stage Prepare -CompilerPath $CompilerPath -SourceDirectory $app -AppVersion $metadata.version -WorkDirectory (Join-Path $output 'inno') -OutputDirectory (Join-Path $output 'unused-output') -OutputBaseFilename $names.setup
    return
}
if ($Stage -eq 'Package') {
    $bundle = Join-Path $BundleDirectory 'bundle'
    $metadata = Read-Bundle $bundle
    Assert-Icon $metadata $bundle
    $app = Join-Path $bundle 'HaloBattery'
    Assert-Inventory $app (Join-Path $bundle 'accepted-tree.json')
    $inno = Join-Path $BundleDirectory 'inno'
    $verified = Fresh (Join-Path $inno 'verified-cache')
    Get-ChildItem -LiteralPath (Join-Path $inno 'unsigned-cache') -File -Force | Copy-Item -Destination $verified
    & $verifier -SignedDirectory $SignedDirectory -DestinationDirectory $verified -InternalManifestPath (Join-Path $inno 'internal-manifest.json') -SigningMode $context.mode -CertificateThumbprint $context.certificate_thumbprint
    $output = Fresh $OutputDirectory
    $names = Names $metadata
    & (Join-Path $repo 'scripts/inno_signing.ps1') -Stage Complete -CompilerPath $CompilerPath -SourceDirectory $app -AppVersion $metadata.version -WorkDirectory $inno -OutputDirectory (Join-Path $output 'installer') -OutputBaseFilename $names.setup -SignedCacheDirectory $verified
    $package = Fresh (Join-Path $output 'packaged')
    [IO.Compression.ZipFile]::CreateFromDirectory((Regular $app $true), (Join-Path $package $names.zip), [IO.Compression.CompressionLevel]::Optimal, $true)
    foreach ($file in @('signing.json','accepted-tree.json')) { Copy-Item -LiteralPath (Join-Path $bundle $file) -Destination (Join-Path $package $file) }
    if ($context.mode -ceq 'Production') {
        & python (Join-Path $repo 'tools/release_notes.py') $metadata.version --out (Join-Path $package 'release_notes.md')
        if ($LASTEXITCODE) { throw 'Release notes validation failed' }
    }
    return
}
if ($Stage -eq 'Finalize') {
    $metadata = Read-Bundle $BundleDirectory
    $names = Names $metadata
    $installer = $names.setup + '.exe'
    & $verifier -InstallerPath (Join-Path $SignedDirectory $installer) -UnsignedInstallerPath (Join-Path $UnsignedDirectory $installer) -SigningMode $context.mode -CertificateThumbprint $context.certificate_thumbprint
    $verified = Fresh (Join-Path $WorkDirectory 'final-verified-cache')
    Get-ChildItem -LiteralPath (Join-Path $WorkDirectory 'unsigned-cache') -File -Force | Copy-Item -Destination $verified
    & $verifier -SignedDirectory (Join-Path $WorkDirectory 'verified-cache') -DestinationDirectory $verified -InternalManifestPath (Join-Path $WorkDirectory 'internal-manifest.json') -SigningMode $context.mode -CertificateThumbprint $context.certificate_thumbprint
    $zipPath = Regular (Join-Path $BundleDirectory $names.zip) $false
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $paths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith('HaloBattery/', [StringComparison]::Ordinal) -or
                $entry.FullName.Contains('\') -or $entry.FullName.Split('/') -contains '..' -or
                $entry.FullName.Contains(':') -or -not $paths.Add($entry.FullName) -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Unexpected signed application ZIP path' }
        }
    } finally { $archive.Dispose() }
    $extracted = Fresh (Join-Path $WorkDirectory 'extracted')
    [IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $extracted)
    $app = Join-Path $extracted 'HaloBattery'
    Assert-Inventory $app (Join-Path $BundleDirectory 'accepted-tree.json')
    foreach ($relative in $own) { & $verifier -SignaturePath (Join-Path $app $relative) -SigningMode $context.mode -CertificateThumbprint $context.certificate_thumbprint }
    $output = Fresh $OutputDirectory
    Copy-Item -LiteralPath $zipPath, (Join-Path $SignedDirectory $installer) -Destination $output
    $lines = foreach ($file in @($names.zip, $installer)) {
        '{0} *{1}' -f (Get-FileHash -LiteralPath (Join-Path $output $file) -Algorithm SHA256).Hash.ToLowerInvariant(), $file
    }
    $lines | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
    $report = @{ test_only=($context.mode -ceq 'Test'); mode=$context.mode; source_commit=$env:GITHUB_SHA;
        run_id=$env:GITHUB_RUN_ID; run_attempt=$env:GITHUB_RUN_ATTEMPT; certificate_thumbprint=$context.certificate_thumbprint;
        policy=$(if ($context.mode -ceq 'Test') {'test-signing'} else {'release-signing'});
        internal_files=(Get-Content -LiteralPath (Join-Path $WorkDirectory 'internal-manifest.json') -Raw | ConvertFrom-Json -AsHashtable) }
    foreach ($kind in @('PAYLOAD','INTERNAL','INSTALLER')) {
        $id = [Environment]::GetEnvironmentVariable($kind + '_ARTIFACT')
        $digest = [Environment]::GetEnvironmentVariable($kind + '_DIGEST')
        $request = [Environment]::GetEnvironmentVariable($kind + '_REQUEST')
        if ($id -cnotmatch '^[0-9]+$' -or $digest -cnotmatch '^(sha256:)?[a-f0-9]{64}$' -or $request -cnotmatch '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$') { throw 'Missing signing provenance' }
        $report[$kind.ToLowerInvariant() + '_artifact_id'] = $id
        $report[$kind.ToLowerInvariant() + '_artifact_digest'] = $digest
        $report[$kind.ToLowerInvariant() + '_request_id'] = $request
    }
    $audit = Fresh (Join-Path $WorkDirectory 'audit')
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $audit 'signing-report.json') -Encoding utf8
    if ($context.mode -ceq 'Test') {
        Copy-Item -LiteralPath (Join-Path $audit 'signing-report.json') -Destination $output
        'SELF-SIGNED TEST ONLY. Application, Inno internal engine and outer setup are signed with the TEST certificate.' | Set-Content -LiteralPath (Join-Path $output 'TEST-ONLY.txt') -Encoding utf8
    } else { Copy-Item -LiteralPath (Join-Path $BundleDirectory 'release_notes.md') -Destination $output }
}
