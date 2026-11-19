#requires -Version 7.0
[CmdletBinding(DefaultParameterSetName='Payload')]
param(
    [Parameter(Mandatory,ParameterSetName='Payload')][string]$SignedDirectory,
    [Parameter(Mandatory,ParameterSetName='Payload')][string]$DestinationDirectory,
    [Parameter(Mandatory,ParameterSetName='Payload')][string]$InventoryPath,
    [Parameter(Mandatory,ParameterSetName='Installer')][string]$InstallerPath,
    [Parameter(Mandatory,ParameterSetName='Installer')][string]$UnsignedInstallerPath
)
$ErrorActionPreference = 'Stop'

function Assert-RegularPath([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item -is [IO.DirectoryInfo]) -ne $Directory) { throw "Unexpected path type: $Path" }
    $cursor = $item
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $cursor.LinkType) {
            throw "Links are not accepted: $Path"
        }
        $cursor = if ($cursor -is [IO.DirectoryInfo]) { $cursor.Parent } else { $cursor.Directory }
    }
    return $item.FullName
}

function Get-RegularTree([string]$Directory, [string[]]$ExactFiles) {
    $root = Assert-RegularPath $Directory $true
    $allowedDirectories = @()
    if ($null -ne $ExactFiles) {
        foreach ($relative in $ExactFiles) {
            $parent = $relative
            while ($parent.Contains('/')) {
                $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
                $allowedDirectories += $parent
            }
        }
    }
    $files = @{}
    foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
            throw "Links are not accepted: $($item.FullName)"
        }
        $relative = [IO.Path]::GetRelativePath($root, $item.FullName).Replace([char]92, [char]47)
        if ($item.PSIsContainer) {
            if ($null -ne $ExactFiles -and $allowedDirectories -cnotcontains $relative) {
                throw "Unexpected signed directory: $relative"
            }
        } else {
            if ($files.ContainsKey($relative)) { throw "Duplicate payload path: $relative" }
            $files[$relative] = $item.FullName
        }
    }
    if ($null -ne $ExactFiles -and
        ($files.Count -ne $ExactFiles.Count -or
         @(Compare-Object $ExactFiles @($files.Keys) -CaseSensitive).Count -ne 0)) {
        throw 'Signed output must contain exactly the expected relative paths'
    }
    return ,$files
}

function Assert-TestSignature([string]$Path) {
    if ($env:TEST_CERT_THUMBPRINT -notmatch '^[0-9A-Fa-f]{40}$') {
        throw 'TEST certificate thumbprint is missing or invalid'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Thumbprint -ine $env:TEST_CERT_THUMBPRINT -or
        $signature.SignatureType.ToString() -cne 'Authenticode') {
        throw "Unexpected embedded TEST signer: $Path"
    }
    # The pinned TEST certificate may be self-signed. Do not install trust.
    if ($signature.Status.ToString() -cnotin @('Valid', 'NotTrusted')) {
        throw "Rejected TEST signature status: $($signature.Status)"
    }
}

function Get-PeLayout([byte[]]$Bytes) {
    # Microsoft PE Format: CheckSum is optional-header+64; security directory
    # entry4 is a file offset, and WIN_CERTIFICATE entries are aligned to8.
    # https://learn.microsoft.com/windows/win32/debug/pe-format
    if ($Bytes.Length -lt 64 -or $Bytes[0] -ne 0x4D -or $Bytes[1] -ne 0x5A) { throw 'Invalid PE DOS header' }
    $pe = [long][BitConverter]::ToUInt32($Bytes, 60)
    if ($pe -gt $Bytes.Length - 24 -or [BitConverter]::ToUInt32($Bytes, $pe) -ne 0x4550) { throw 'Invalid PE header' }
    $optional = $pe + 24
    $size = [BitConverter]::ToUInt16($Bytes, $pe + 20)
    if ($optional + $size -gt $Bytes.Length -or $size -lt 2) { throw 'Truncated PE optional header' }
    $magic = [BitConverter]::ToUInt16($Bytes, $optional)
    $directories = switch ($magic) { 0x10B { 96 }; 0x20B { 112 }; default { throw 'Unsupported PE optional header' } }
    if ($size -lt $directories + 40 -or
        [BitConverter]::ToUInt32($Bytes, $optional + $directories - 4) -lt 5) { throw 'Missing PE security directory' }
    $security = $optional + $directories + 32
    return @{ Checksum=$optional + 64; Security=$security
        Certificate=[long][BitConverter]::ToUInt32($Bytes, $security)
        CertificateSize=[long][BitConverter]::ToUInt32($Bytes, $security + 4) }
}

function Get-ByteHash([byte[]]$Bytes) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($Bytes)).Replace('-', '') }
    finally { $algorithm.Dispose() }
}

function Read-CheckedSignedPe([string]$UnsignedPath, [string]$SignedPath) {
    $unsignedFile = Get-Item -LiteralPath $UnsignedPath -Force
    $signedFile = Get-Item -LiteralPath $SignedPath -Force
    if ($signedFile.Length -gt $unsignedFile.Length + 1048583) { throw 'Unexpected signature growth' }
    [byte[]]$unsigned = [IO.File]::ReadAllBytes($UnsignedPath)
    [byte[]]$signed = [IO.File]::ReadAllBytes($SignedPath)
    $original = Get-PeLayout $unsigned
    $actual = Get-PeLayout $signed
    if ($original.Certificate -ne 0 -or $original.CertificateSize -ne 0) { throw 'Expected an unsigned original PE' }
    if ($actual.Security -ne $original.Security -or $actual.Checksum -ne $original.Checksum -or
        $actual.Certificate -lt $unsigned.Length -or $actual.Certificate -gt $unsigned.Length + 7 -or
        $actual.Certificate % 8 -ne 0 -or $actual.CertificateSize -lt 8 -or
        $actual.Certificate + $actual.CertificateSize -ne $signed.Length) {
        throw 'Signature must be an aligned certificate table appended to the original PE'
    }
    for ($i = $unsigned.Length; $i -lt $actual.Certificate; $i++) {
        if ($signed[$i] -ne 0) { throw 'Unexpected data before certificate table' }
    }
    $cursor = $actual.Certificate
    while ($cursor -lt $signed.Length) {
        if ($cursor -gt $signed.Length - 8) { throw 'Truncated WIN_CERTIFICATE' }
        $length = [long][BitConverter]::ToUInt32($signed, $cursor)
        if ($length -lt 8 -or $cursor + $length -gt $signed.Length -or
            [BitConverter]::ToUInt16($signed, $cursor + 4) -ne 0x0200 -or
            [BitConverter]::ToUInt16($signed, $cursor + 6) -ne 2) { throw 'Invalid WIN_CERTIFICATE' }
        $next = $cursor + (($length + 7) -band -8L)
        if ($next -gt $signed.Length) { throw 'Invalid certificate table padding' }
        for ($i = $cursor + $length; $i -lt $next; $i++) {
            if ($signed[$i] -ne 0) { throw 'Unexpected certificate padding' }
        }
        $cursor = $next
    }
    [byte[]]$prefix = [byte[]]::new($unsigned.Length)
    [Array]::Copy($signed, $prefix, $unsigned.Length)
    foreach ($bytes in @($unsigned, $prefix)) {
        [Array]::Clear($bytes, $original.Checksum, 4)
        [Array]::Clear($bytes, $original.Security, 8)
    }
    if ((Get-ByteHash $unsigned) -cne (Get-ByteHash $prefix)) { throw 'Signed PE content differs from original' }
    Assert-TestSignature $SignedPath
    $hash = Get-ByteHash $signed
    if ((Get-FileHash -LiteralPath $SignedPath -Algorithm SHA256).Hash -cne $hash) { throw 'Signed file changed during verification' }
    return [pscustomobject]@{ Bytes=$signed; Hash=$hash }
}

if ($PSCmdlet.ParameterSetName -eq 'Installer') {
    $installer = Assert-RegularPath $InstallerPath $false
    $unsignedInstaller = Assert-RegularPath $UnsignedInstallerPath $false
    if ([IO.Path]::GetFileName($installer) -cne 'HaloBattery-test-setup.exe' -or
        [IO.Path]::GetFileName($unsignedInstaller) -cne 'HaloBattery-test-setup.exe') { throw 'Unexpected TEST installer filename' }
    $null = Get-RegularTree ([IO.Path]::GetDirectoryName($installer)) @('HaloBattery-test-setup.exe')
    $null = Read-CheckedSignedPe $unsignedInstaller $installer
    return
}
$own = @('HaloBattery.exe', '_internal/settings-ui/HaloBattery.Settings.exe')
$signedRoot = Assert-RegularPath $SignedDirectory $true
$destinationRoot = Assert-RegularPath $DestinationDirectory $true
$separator = [IO.Path]::DirectorySeparatorChar
if ($signedRoot.Equals($destinationRoot, [StringComparison]::OrdinalIgnoreCase) -or
    ($signedRoot + $separator).StartsWith($destinationRoot + $separator, [StringComparison]::OrdinalIgnoreCase) -or
    ($destinationRoot + $separator).StartsWith($signedRoot + $separator, [StringComparison]::OrdinalIgnoreCase)) { throw 'Signed and destination trees must be disjoint' }
$signedFiles = Get-RegularTree $signedRoot $own
$null = Assert-RegularPath $InventoryPath $false
$before = Get-Content -LiteralPath $InventoryPath -Raw | ConvertFrom-Json -AsHashtable
if ($before -isnot [Collections.IDictionary]) { throw 'Invalid unsigned inventory' }
$destinationFiles = Get-RegularTree $destinationRoot $null
function Assert-Inventory($Files) {
    if ($Files.Count -ne $before.Count) { throw 'Unsigned package tree changed after inventory' }
    foreach ($relative in $Files.Keys) {
        if (@($before.Keys) -cnotcontains $relative -or $before[$relative] -notmatch '^[0-9A-Fa-f]{64}$' -or
            (Get-FileHash -LiteralPath $Files[$relative] -Algorithm SHA256).Hash -ine $before[$relative]) {
            throw "Unsigned package changed after inventory: $relative"
        }
    }
}
Assert-Inventory $destinationFiles
$checked = @{}
foreach ($relative in $own) {
    if (@($destinationFiles.Keys) -cnotcontains $relative) { throw "Missing original own PE: $relative" }
    $checked[$relative] = Read-CheckedSignedPe $destinationFiles[$relative] $signedFiles[$relative]
}
# Recheck after signature verification, before either owned file is replaced.
Assert-Inventory (Get-RegularTree $destinationRoot $null)
foreach ($relative in $own) { [IO.File]::WriteAllBytes($destinationFiles[$relative], $checked[$relative].Bytes) }
$after = Get-RegularTree $destinationRoot $null
if ($after.Count -ne $before.Count) { throw 'Accepted package tree has unexpected files' }
foreach ($relative in $after.Keys) {
    $expected = if ($own -ccontains $relative) { $checked[$relative].Hash } else { $before[$relative] }
    if ($null -eq $expected -or (Get-FileHash -LiteralPath $after[$relative] -Algorithm SHA256).Hash -ine $expected) {
        throw "Accepted package changed unexpectedly: $relative"
    }
}
