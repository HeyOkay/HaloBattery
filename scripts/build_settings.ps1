# Publish the native settings companion; keep its complete output folder together.
[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release',
    [bool]$SelfContained = $true,
    [switch]$EnableVerification,
    [ValidateSet('Auto', 'Aot', 'Jit')][string]$PublishMode = 'Auto',
    [string]$ToolchainCache,
    [switch]$OfflineToolchain,
    [string]$OutputDirectory,
    [string]$IntermediateDirectory,
    [string]$NuGetConfig,
    [string]$PackagesDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = Split-Path $PSScriptRoot -Parent
if ($ToolchainCache) { $ToolchainCache = [IO.Path]::GetFullPath($ToolchainCache) }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repository '.build/settings-ui' }
if (-not $IntermediateDirectory) { $IntermediateDirectory = Join-Path $repository '.build/settings-ui-obj' }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
$IntermediateDirectory = [IO.Path]::GetFullPath($IntermediateDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)
# Auto enables native release only after source/IPC adoption gates are satisfied.
$useAot = $PublishMode -eq 'Aot' -or ($PublishMode -eq 'Auto' -and
    $Configuration -eq 'Release' -and $SelfContained -and -not $EnableVerification)
if ($useAot -and ($Configuration -ne 'Release' -or -not $SelfContained -or $EnableVerification)) {
    throw 'AOT requires self-contained Release with verification disabled; use -PublishMode Jit for QA/Debug.'
}
$aot = $useAot.ToString().ToLowerInvariant()
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
        $marker = Join-Path $OutputDirectory 'HaloBattery.Settings.publish.json'
        if (Test-Path -LiteralPath $marker -PathType Leaf) {
            try { $publication = [IO.File]::ReadAllText($marker) | ConvertFrom-Json }
            catch { throw 'Invalid settings publication marker.' }
            $exe = Join-Path $OutputDirectory 'HaloBattery.Settings.exe'
            if ($publication.schema -ne 1 -or $publication.project -cne 'HaloBattery.Settings' -or
                $publication.mode -cne 'Aot' -or -not (Test-Path -LiteralPath $exe -PathType Leaf) -or
                (Get-Item -LiteralPath $exe).Length -eq 0) {
                throw 'Invalid AOT settings publication marker or executable.'
            }
            return
        }
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
$versionText = [IO.File]::ReadAllText((Join-Path $repository 'halo_battery.pyw'))
$versions = [regex]::Matches($versionText, '(?m)^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$')
if ($versions.Count -ne 1) { throw 'Expected one numeric VERSION in halo_battery.pyw.' }
$applicationVersion = $versions[0].Groups[1].Value
foreach ($part in $applicationVersion.Split('.')) {
    $number = 0
    if (-not [int]::TryParse($part, [ref]$number) -or $number -gt 65534) {
        throw 'VERSION cannot be represented in Windows executable metadata.'
    }
}
$project = Join-Path $repository 'ui/HaloBattery.Settings/HaloBattery.Settings.csproj'
$contained = $SelfContained.ToString().ToLowerInvariant()
$lockPath = Join-Path $projectDirectory "packages.$contained.lock.json"
$archivesLockPath = Join-Path $projectDirectory 'packages.archives.lock.json'
if ($useAot) {
    $lockPath = Join-Path $projectDirectory 'packages.aot.true.lock.json'
    $archivesLockPath = Join-Path $projectDirectory 'packages.aot.archives.lock.json'
}
$lockSnapshots = @{}
foreach ($path in @($lockPath, $archivesLockPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing committed dependency lock: $path" }
    $lockSnapshots[$path] = [IO.File]::ReadAllText($path)
    $data = $lockSnapshots[$path] | ConvertFrom-Json
    if ($data.version -ne 1 -or @($data.dependencies.PSObject.Properties).Count -eq 0) {
        throw "Invalid dependency lock: $path"
    }
}
function Assert-LockedPackageArchive([string]$Name, $Pin, [string[]]$Folders) {
    if ($Name -notmatch '^[A-Za-z0-9_.-]+$' -or $Pin.resolved -notmatch '^[A-Za-z0-9_.+-]+$' -or
        $Pin.archiveSha512 -notmatch '^[A-Za-z0-9+/]{86}==$') { throw "Invalid locked package: $Name" }
    $relative = '{0}/{1}/{0}.{1}.nupkg' -f $Name.ToLowerInvariant(), $Pin.resolved.ToLowerInvariant()
    foreach ($folder in $Folders) {
        $archive = Join-Path $folder $relative
        if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { continue }
        $stream = [IO.File]::OpenRead($archive)
        $sha = [Security.Cryptography.SHA512]::Create()
        try { $actual = [Convert]::ToBase64String($sha.ComputeHash($stream)) }
        finally { $sha.Dispose(); $stream.Dispose() }
        if ($actual -cne $Pin.archiveSha512) { throw "Dependency archive hash mismatch: $Name $($Pin.resolved)" }
        return
    }
    throw "Locked dependency archive is missing: $Name $($Pin.resolved)"
}
function Assert-RestoredPackageHashes {
    foreach ($path in @($lockPath, $archivesLockPath)) {
        if ([IO.File]::ReadAllText($path) -cne $lockSnapshots[$path]) { throw "Restore changed a committed lock: $path" }
    }
    $assets = [IO.File]::ReadAllText((Join-Path $IntermediateDirectory 'project.assets.json')) | ConvertFrom-Json
    $folders = @($assets.packageFolders.PSObject.Properties.Name)
    $locked = $lockSnapshots[$lockPath] | ConvertFrom-Json
    $archives = ($lockSnapshots[$archivesLockPath] | ConvertFrom-Json).dependencies
    foreach ($framework in $locked.dependencies.PSObject.Properties) {
        foreach ($package in $framework.Value.PSObject.Properties) {
            $pin = $archives.PSObject.Properties[$package.Name]
            if (-not $pin -or $pin.Value.resolved -cne $package.Value.resolved) {
                throw "Unpinned dependency archive: $($package.Name) $($package.Value.resolved)"
            }
            Assert-LockedPackageArchive $package.Name $pin.Value $folders
        }
    }
    # SDK PackageDownload inputs are omitted by NuGet's packages.lock.json.
    # Pin and verify those archives as well, before executing build targets.
    # NuGet contentHash differs from the raw signed nupkg hash. archiveSha512
    # covers the complete downloaded archive, including its signature.
    foreach ($framework in $assets.project.frameworks.PSObject.Properties) {
        foreach ($dependency in @($framework.Value.downloadDependencies)) {
            $pin = $archives.PSObject.Properties[$dependency.name]
            if (-not $pin -or $dependency.version -cne "[$($pin.Value.resolved), $($pin.Value.resolved)]") {
                throw "Unpinned SDK dependency: $($dependency.name) $($dependency.version)"
            }
            Assert-LockedPackageArchive $dependency.name $pin.Value $folders
        }
    }
}
$commonArguments = @(
    '-p:Platform=x64', "-p:BaseIntermediateOutputPath=$IntermediateDirectory/",
    "-p:MSBuildProjectExtensionsPath=$IntermediateDirectory/",
    "-p:BaseOutputPath=$IntermediateDirectory/bin/",
    "-p:Version=$applicationVersion", "-p:FileVersion=$applicationVersion.0",
    '-p:Product=HaloBattery', '-p:IncludeSourceRevisionInInformationalVersion=false',
    '-p:RestoreLockedMode=true', "-p:NuGetLockFilePath=$lockPath",
    '-p:PublishSingleFile=false', "-p:PublishAot=$aot", '--disable-build-servers'
)
if ($useAot) {
    $commonArguments += @('-p:IlcUseEnvironmentalTools=true', '-p:CsWinRTUseEnvironmentalTools=true')
}
if ($EnableVerification) {
    $commonArguments += '-p:HaloBatteryVerification=true'
}
if (-not $NuGetConfig) { $NuGetConfig = Join-Path $repository 'ui/NuGet.Config' }
if (-not (Test-Path -LiteralPath $NuGetConfig -PathType Leaf)) { throw "Missing NuGet configuration: $NuGetConfig" }
if ($PackagesDirectory) {
    $commonArguments += "-p:RestorePackagesPath=$([IO.Path]::GetFullPath($PackagesDirectory))"
}
$restoreArguments = @('restore', $project, '--runtime', 'win-x64',
    "-p:Configuration=$Configuration", "-p:SelfContained=$contained",
    '--configfile', [IO.Path]::GetFullPath($NuGetConfig)) + $commonArguments
$buildArguments = @('publish', $project, '--configuration', $Configuration, '--runtime', 'win-x64',
    '--self-contained', $contained, '--output', $stageDirectory, '--no-restore') + $commonArguments
# Select the exact SDK without changing the repository or user-wide SDK settings.
$sdkDirectory = Join-Path $IntermediateDirectory 'sdk'
[IO.Directory]::CreateDirectory($sdkDirectory) | Out-Null
[IO.File]::WriteAllText((Join-Path $sdkDirectory 'global.json'),
    [IO.File]::ReadAllText((Join-Path $repository 'ui/global.json')),
    [Text.UTF8Encoding]::new($false))
$stageCreated = $false
$originalEnvironment = @{ PATH = $env:PATH; LIB = $env:LIB; INCLUDE = $env:INCLUDE }
Push-Location -LiteralPath $sdkDirectory
try {
    $sdkVersion = & dotnet --version
    if ($LASTEXITCODE -ne 0 -or $sdkVersion.Trim() -ne '10.0.404') {
        throw 'The settings companion requires .NET SDK 10.0.404. Install/select it before building.'
    }
    & dotnet @restoreArguments
    if ($LASTEXITCODE -ne 0) { throw "Locked settings restore failed (exit $LASTEXITCODE)." }
    Assert-RestoredPackageHashes
    if ($useAot) {
        if (-not $ToolchainCache) { $ToolchainCache = Join-Path $repository '.build/settings-aot-toolchain' }
        $toolchainArguments = @((Join-Path $repository 'tools/get_settings_aot_toolchain.py'),
            '--lock', (Join-Path $PSScriptRoot 'settings-aot-toolchain.lock.json'), '--cache', $ToolchainCache)
        if ($OfflineToolchain) { $toolchainArguments += '--offline' }
        $toolchainText = & python @toolchainArguments
        if ($LASTEXITCODE -ne 0) { throw "Pinned AOT toolchain acquisition failed (exit $LASTEXITCODE)." }
        $toolchain = $toolchainText | ConvertFrom-Json
        $env:PATH = (Join-Path $toolchain.msvc 'bin/Hostx64/x64') + ';' + $originalEnvironment.PATH
        $env:LIB = (Join-Path $toolchain.msvc 'lib/x64') + ';' +
            (Join-Path $toolchain.sdk 'c/um/x64') + ';' + (Join-Path $toolchain.sdk 'c/ucrt/x64')
        $env:INCLUDE = Join-Path $toolchain.msvc 'include'
    }
    New-Item -ItemType Directory -Path $stageDirectory | Out-Null
    $stageCreated = $true
    & dotnet @buildArguments
    if ($LASTEXITCODE -ne 0) { throw "Settings publish failed (exit $LASTEXITCODE)." }
    if (-not (Test-Path -LiteralPath (Join-Path $stageDirectory 'HaloBattery.Settings.exe') -PathType Leaf)) {
        throw 'Settings publish did not produce HaloBattery.Settings.exe.'
    }
    $publishedExe = Join-Path $stageDirectory 'HaloBattery.Settings.exe'
    if ((Get-Item -LiteralPath $publishedExe).Length -eq 0) { throw 'Settings publish produced an empty executable.' }
    if ($useAot) {
        [IO.File]::WriteAllText((Join-Path $stageDirectory 'HaloBattery.Settings.publish.json'),
            '{"schema":1,"project":"HaloBattery.Settings","mode":"Aot"}', [Text.UTF8Encoding]::new($false))
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
    Write-Host "Native settings published to $OutputDirectory (self-contained: $contained, AOT: $aot)."
} finally {
    $env:PATH = $originalEnvironment.PATH
    $env:LIB = $originalEnvironment.LIB
    $env:INCLUDE = $originalEnvironment.INCLUDE
    Pop-Location
    if ($stageCreated -and (Test-Path -LiteralPath $stageDirectory)) {
        try { Remove-OwnedPublishDirectory $stageDirectory }
        catch { Write-Warning "Temporary publish output remains at $stageDirectory. $_" }
    }
}
