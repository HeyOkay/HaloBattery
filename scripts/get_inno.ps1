# Return a compiler extracted from a verified official portable installer.
[CmdletBinding()]
param([string]$ToolsDirectory, [string]$InstallerPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ToolsDirectory) { $ToolsDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) '.build/tools' }
$ToolsDirectory = [IO.Path]::GetFullPath($ToolsDirectory)
for ($cursor = $ToolsDirectory; $cursor; $cursor = [IO.Path]::GetDirectoryName($cursor)) {
    $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
    if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Tool directories cannot traverse a reparse point: $cursor"
    }
}
[IO.Directory]::CreateDirectory($ToolsDirectory) | Out-Null
$nonce = [Guid]::NewGuid().ToString('N')
$url = 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_4/innosetup-6.7.4.exe'
$expected = 'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c'
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $ToolsDirectory "innosetup-6.7.4-$nonce.exe"
    Invoke-WebRequest -Uri $url -OutFile $InstallerPath -UseBasicParsing
}
$InstallerPath = [IO.Path]::GetFullPath($InstallerPath)
if ((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Inno Setup installer SHA-256 does not match the pinned release.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Pyrsys B\.V\.(,|$)') {
    throw 'Inno Setup installer does not have the expected valid publisher signature.'
}
# A fresh directory prevents leftovers from a prior tool install entering a build.
# /PORTABLE=1 disables uninstall/association registration in the official setup.
$destination = Join-Path $ToolsDirectory "inno-6.7.4-$nonce"
New-Item -ItemType Directory -Path $destination | Out-Null
$arguments = @('/PORTABLE=1', '/CURRENTUSER', '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES',
               '/NORESTART', '/NOICONS', ('/DIR="' + $destination + '"'))
$process = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw "Portable Inno Setup extraction failed (exit $($process.ExitCode))." }
$compiler = Join-Path $destination 'ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Portable extraction did not produce ISCC.exe.' }
Write-Output $compiler
