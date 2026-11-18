# Publish the native settings companion; keep its complete output folder together.
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [bool]$SelfContained = $true,
    [string]$OutputDirectory,
    [string]$IntermediateDirectory,
    [string]$NuGetConfig,
    [string]$PackagesDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = Split-Path $PSScriptRoot -Parent
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repository '.build/settings-ui' }
if (-not $IntermediateDirectory) { $IntermediateDirectory = Join-Path $repository '.build/settings-ui-obj' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
$IntermediateDirectory = [IO.Path]::GetFullPath($IntermediateDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
$projectDirectory = Join-Path $repository 'ui/HaloBattery.Settings'
function Test-Within([string]$Path, [string]$Root) {
    return $Path.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}
function Assert-NoReparsePoint([string]$Path) {
    for ($cursor = $Path; $cursor; $cursor = [IO.Path]::GetDirectoryName($cursor)) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Publish paths cannot traverse a reparse point: $cursor"
        }
    }
}
foreach ($path in @($OutputDirectory, $IntermediateDirectory)) {
    $volumeRoot = [IO.Path]::GetPathRoot($path).TrimEnd([IO.Path]::DirectorySeparatorChar)
    if ($path -eq $volumeRoot -or (Test-Within $repository $path) -or
        (Test-Within $path $projectDirectory) -or (Test-Within $projectDirectory $path)) {
        throw "Use a dedicated build directory, separate from repository sources: $path"
    }
    Assert-NoReparsePoint $path
    if ((Test-Path -LiteralPath $path) -and -not (Test-Path -LiteralPath $path -PathType Container)) {
        throw "Build directory is an existing file: $path"
    }
}
if ((Test-Within $OutputDirectory $IntermediateDirectory) -or
    (Test-Within $IntermediateDirectory $OutputDirectory)) {
    throw 'OutputDirectory and IntermediateDirectory must not overlap.'
}
function Assert-PublishDestination {
    Assert-NoReparsePoint $OutputDirectory
    if ((Test-Path -LiteralPath $OutputDirectory) -and
        -not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
        throw "OutputDirectory changed into a file: $OutputDirectory"
    }
    if ((Test-Path -LiteralPath $OutputDirectory) -and
        @(Get-ChildItem -LiteralPath $OutputDirectory -Force).Count -gt 0) {
        foreach ($name in @('HaloBattery.Settings.exe', 'HaloBattery.Settings.deps.json', 'HaloBattery.Settings.runtimeconfig.json')) {
            if (-not (Test-Path -LiteralPath (Join-Path $OutputDirectory $name) -PathType Leaf)) {
                throw "OutputDirectory must be empty or a previous settings publication: $OutputDirectory"
            }
        }
    }
}
Assert-PublishDestination
# Sibling directories keep promotion on the same volume. Only these exact,
# uniquely named directories may be recursively removed by this invocation.
$outputParent = [IO.Path]::GetDirectoryName($OutputDirectory)
$outputName = [IO.Path]::GetFileName($OutputDirectory)
$nonce = [Guid]::NewGuid().ToString('N')
$stageDirectory = Join-Path $outputParent "$outputName.stage-$nonce"
$backupDirectory = Join-Path $outputParent "$outputName.backup-$nonce"
function Remove-OwnedPublishDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (($full -ne $stageDirectory -and $full -ne $backupDirectory) -or
        [IO.Path]::GetDirectoryName($full) -ne $outputParent) {
        throw "Refusing to remove an unrelated directory: $full"
    }
    Assert-NoReparsePoint $full
    # Refuse junctions inside an old publication as well (including on PS 5.1).
    $links = @(Get-ChildItem -LiteralPath $full -Force -Recurse |
        Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
    if ($links.Count) { throw "Refusing to traverse reparse points in $full" }
    Remove-Item -LiteralPath $full -Recurse -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $repository 'halo.ico'))) {
    throw 'Generate halo.ico first: python tools/make_icon.py halo.ico'
}
$project = Join-Path $repository 'ui/HaloBattery.Settings/HaloBattery.Settings.csproj'
$contained = $SelfContained.ToString().ToLowerInvariant()
$buildArguments = @(
    'publish', $project, '--configuration', $Configuration, '--runtime', 'win-x64',
    '--self-contained', $contained, '--output', $stageDirectory,
    '-p:Platform=x64', "-p:BaseIntermediateOutputPath=$IntermediateDirectory/",
    "-p:MSBuildProjectExtensionsPath=$IntermediateDirectory/",
    "-p:BaseOutputPath=$IntermediateDirectory/bin/",
    '-p:PublishSingleFile=false', '-p:PublishAot=false', '--disable-build-servers'
)
if ($NuGetConfig) {
    $buildArguments += @('--configfile', [IO.Path]::GetFullPath($NuGetConfig))
}
if ($PackagesDirectory) {
    $buildArguments += "-p:RestorePackagesPath=$([IO.Path]::GetFullPath($PackagesDirectory))"
}
# Select the exact SDK without changing the repository or user-wide SDK settings.
$sdkDirectory = Join-Path $IntermediateDirectory 'sdk'
[IO.Directory]::CreateDirectory($sdkDirectory) | Out-Null
[IO.File]::WriteAllText((Join-Path $sdkDirectory 'global.json'),
    '{"sdk":{"version":"10.0.401","rollForward":"disable","allowPrerelease":false}}',
    [Text.UTF8Encoding]::new($false))
$stageCreated = $false
Push-Location -LiteralPath $sdkDirectory
try {
    $sdkVersion = & dotnet --version
    if ($LASTEXITCODE -ne 0 -or $sdkVersion.Trim() -ne '10.0.401') {
        throw 'The settings companion requires .NET SDK 10.0.401. Install/select it before building.'
    }
    New-Item -ItemType Directory -Path $stageDirectory | Out-Null
    $stageCreated = $true
    & dotnet @buildArguments
    if ($LASTEXITCODE -ne 0) { throw "Settings publish failed (exit $LASTEXITCODE)." }
    if (-not (Test-Path -LiteralPath (Join-Path $stageDirectory 'HaloBattery.Settings.exe') -PathType Leaf)) {
        throw 'Settings publish did not produce HaloBattery.Settings.exe.'
    }
    Assert-PublishDestination
    Assert-NoReparsePoint $stageDirectory
    $previousMoved = $false
    if (Test-Path -LiteralPath $OutputDirectory) {
        Rename-Item -LiteralPath $OutputDirectory -NewName ([IO.Path]::GetFileName($backupDirectory))
        $previousMoved = $true
    }
    try {
        if (Test-Path -LiteralPath $OutputDirectory) { throw "OutputDirectory changed during publish: $OutputDirectory" }
        Rename-Item -LiteralPath $stageDirectory -NewName $outputName
        $stageCreated = $false
    } catch {
        $promotionError = $_
        if ($previousMoved) {
            try { Rename-Item -LiteralPath $backupDirectory -NewName $outputName }
            catch { throw "Publish promotion failed; previous output is preserved at $backupDirectory. $promotionError $_" }
        }
        throw $promotionError
    }
    if ($previousMoved) {
        try { Remove-OwnedPublishDirectory $backupDirectory }
        catch { Write-Warning "Published successfully; old output remains at $backupDirectory. $_" }
    }
    Write-Host "Native settings published to $OutputDirectory (self-contained: $contained)."
} finally {
    Pop-Location
    if ($stageCreated -and (Test-Path -LiteralPath $stageDirectory)) {
        try { Remove-OwnedPublishDirectory $stageDirectory }
        catch { Write-Warning "Temporary publish output remains at $stageDirectory. $_" }
    }
}
