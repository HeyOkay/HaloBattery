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
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$IntermediateDirectory = [IO.Path]::GetFullPath($IntermediateDirectory)
if (-not (Test-Path -LiteralPath (Join-Path $repository 'halo.ico'))) {
    throw 'Generate halo.ico first: python tools/make_icon.py halo.ico'
}
$project = Join-Path $repository 'ui/HaloBattery.Settings/HaloBattery.Settings.csproj'
$contained = $SelfContained.ToString().ToLowerInvariant()
$buildArguments = @(
    'publish', $project, '--configuration', $Configuration, '--runtime', 'win-x64',
    '--self-contained', $contained, '--output', $OutputDirectory,
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
Push-Location $sdkDirectory
try {
    $sdkVersion = & dotnet --version
    if ($LASTEXITCODE -ne 0 -or $sdkVersion.Trim() -ne '10.0.401') {
        throw 'The settings companion requires .NET SDK 10.0.401. Install/select it before building.'
    }
    & dotnet @buildArguments
    if ($LASTEXITCODE -ne 0) { throw "Settings publish failed (exit $LASTEXITCODE)." }
    if (-not (Test-Path -LiteralPath (Join-Path $OutputDirectory 'HaloBattery.Settings.exe'))) {
        throw 'Settings publish did not produce HaloBattery.Settings.exe.'
    }
    Write-Host "Native settings published to $OutputDirectory (self-contained: $contained)."
} finally {
    Pop-Location
}
