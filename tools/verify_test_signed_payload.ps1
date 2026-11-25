#requires -Version 7.0
[CmdletBinding(DefaultParameterSetName='Payload')]
param(
    [Parameter(Mandatory,ParameterSetName='Payload')][Parameter(Mandatory,ParameterSetName='Internal')][string]$SignedDirectory,
    [Parameter(Mandatory,ParameterSetName='Payload')][Parameter(Mandatory,ParameterSetName='Internal')][string]$DestinationDirectory,
    [Parameter(Mandatory,ParameterSetName='Payload')][string]$InventoryPath,
    [Parameter(Mandatory,ParameterSetName='Installer')][string]$InstallerPath,
    [Parameter(Mandatory,ParameterSetName='Installer')][string]$UnsignedInstallerPath,
    [Parameter(Mandatory,ParameterSetName='Signature')][string]$SignaturePath,
    [Parameter(Mandatory,ParameterSetName='InnoHelper')][string]$InnoHelperPath,
    [Parameter(Mandatory,ParameterSetName='InnoHelper')][string]$UnsignedInnoHelperPath,
    [ValidateSet('Test','Production')][string]$SigningMode = 'Test',
    [string]$CertificateThumbprint,
    [Parameter(Mandatory,ParameterSetName='Internal')][string]$InternalManifestPath
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

function Get-TestSignatureTrustStatus([string]$Path, [bool]$Production = $false) {
    if (-not ('Halo.TestSignatureTrust' -as [type])) {
        # Read the native result: PowerShell maps CERT_E_UNTRUSTEDROOT to
        # UnknownError on some Windows versions. Never match localized messages.
        # https://learn.microsoft.com/windows/win32/api/wintrust/nf-wintrust-winverifytrust
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace Halo {
    public static class TestSignatureTrust {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct FileInfo {
            public uint Size;
            [MarshalAs(UnmanagedType.LPWStr)] public string Path;
            public IntPtr File;
            public IntPtr KnownSubject;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct TrustData {
            public uint Size;
            public IntPtr PolicyCallbackData;
            public IntPtr SIPClientData;
            public uint UIChoice;
            public uint RevocationChecks;
            public uint UnionChoice;
            public IntPtr FileInfo;
            public uint StateAction;
            public IntPtr StateData;
            public IntPtr URLReference;
            public uint ProviderFlags;
            public uint UIContext;
            public IntPtr SignatureSettings;
        }
        [DllImport("wintrust.dll", ExactSpelling = true)]
        private static extern int WinVerifyTrust(IntPtr window, ref Guid action, ref TrustData data);
        public static int Verify(string path) { return VerifyCore(path, 0x1000 | 0x10); }
        public static int VerifyProduction(string path) { return VerifyCore(path, 0x80); }
        private static int VerifyCore(string path, uint flags) {
            var file = new FileInfo { Size = (uint)Marshal.SizeOf<FileInfo>(), Path = path };
            IntPtr pointer = Marshal.AllocHGlobal(Marshal.SizeOf<FileInfo>());
            var data = new TrustData {
                Size = (uint)Marshal.SizeOf<TrustData>(), UIChoice = 2, UnionChoice = 1,
                FileInfo = pointer, StateAction = 1,
                // TEST is offline; production checks revocation for the chain except root.
                ProviderFlags = flags
            };
            var action = new Guid("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");
            bool initialized = false;
            try {
                Marshal.StructureToPtr(file, pointer, false);
                initialized = true;
                return WinVerifyTrust(new IntPtr(-1), ref action, ref data);
            } finally {
                try {
                    data.StateAction = 2;
                    WinVerifyTrust(new IntPtr(-1), ref action, ref data);
                } finally {
                    if (initialized) Marshal.DestroyStructure<FileInfo>(pointer);
                    Marshal.FreeHGlobal(pointer);
                }
            }
        }
    }
}
'@
    }
    if ($Production) { return [Halo.TestSignatureTrust]::VerifyProduction($Path) }
    return [Halo.TestSignatureTrust]::Verify($Path)
}

function Assert-ProductionTimestamp([string]$Path) {
    # Verify the RFC3161 token's binding explicitly; Windows also validates
    # the timestamp chain through WinVerifyTrust. Legacy countersignatures are rejected.
    [byte[]]$bytes = [IO.File]::ReadAllBytes($Path)
    $layout = Get-PeLayout $bytes
    if ($layout.Certificate -le 0 -or $layout.CertificateSize -lt 9 -or
        $layout.Certificate + $layout.CertificateSize -ne $bytes.Length) { throw 'Missing embedded signing data' }
    $length = [BitConverter]::ToUInt32($bytes, $layout.Certificate)
    if ($length -lt 9 -or $length -gt $layout.CertificateSize) { throw 'Invalid embedded signing data' }
    $cms = [Security.Cryptography.Pkcs.SignedCms]::new()
    $cms.Decode([byte[]]$bytes[($layout.Certificate + 8)..($layout.Certificate + $length - 1)])
    $cms.CheckSignature($true)
    if ($cms.SignerInfos.Count -ne 1 -or $cms.SignerInfos[0].Certificate.Thumbprint -ine $CertificateThumbprint -or
        $cms.SignerInfos[0].DigestAlgorithm.Value -cne '2.16.840.1.101.3.4.2.1') { throw 'Production signature must use SHA-256 and the pinned signer' }
    $indirect = [System.Formats.Asn1.AsnReader]::new([ReadOnlyMemory[byte]]::new($cms.ContentInfo.Content), [System.Formats.Asn1.AsnEncodingRules]::BER).ReadSequence()
    $null = $indirect.ReadEncodedValue() # SpcAttributeTypeAndOptionalValue
    $fileDigest = $indirect.ReadSequence().ReadSequence().ReadObjectIdentifier()
    if ($cms.ContentInfo.ContentType.Value -cne '1.3.6.1.4.1.311.2.1.4' -or
        $fileDigest -cne '2.16.840.1.101.3.4.2.1') { throw 'Production PE digest must use SHA-256' }
    $tokens = @($cms.SignerInfos[0].UnsignedAttributes | Where-Object {
        $_.Oid.Value -cin @('1.3.6.1.4.1.311.3.3.1', '1.2.840.113549.1.9.16.2.14')
    })
    if (@($cms.SignerInfos[0].UnsignedAttributes | Where-Object { $_.Oid.Value -ceq '1.2.840.113549.1.9.6' }).Count) {
        throw 'Legacy timestamp countersignatures are not accepted'
    }
    if ($tokens.Count -ne 1 -or $tokens[0].Values.Count -ne 1) { throw 'Production requires one RFC3161 timestamp' }
    $token = $null
    $tokenBytes = $tokens[0].Values[0].RawData
    $tokenConsumed = 0
    $tokenDecoded = [Security.Cryptography.Pkcs.Rfc3161TimestampToken]::TryDecode(
        [ReadOnlyMemory[byte]]::new($tokenBytes), [ref]$token, [ref]$tokenConsumed)
    $tsa = $null
    if (-not $tokenDecoded -or $tokenConsumed -ne $tokenBytes.Length -or
        -not $token.VerifySignatureForSignerInfo($cms.SignerInfos[0], [ref]$tsa, $null)) {
        throw 'RFC3161 timestamp does not bind to the embedded signer signature'
    }
    $timestamp = [Security.Cryptography.Pkcs.SignedCms]::new()
    $timestamp.Decode($tokens[0].Values[0].RawData)
    $timestamp.CheckSignature($true)
    $consumed = 0
    $timestampInfo = $null
    $decoded = [Security.Cryptography.Pkcs.Rfc3161TimestampTokenInfo]::TryDecode(
        [ReadOnlyMemory[byte]]::new($timestamp.ContentInfo.Content), [ref]$timestampInfo, [ref]$consumed)
    if (-not $decoded -or $timestamp.ContentInfo.ContentType.Value -cne '1.2.840.113549.1.9.16.1.4' -or
        $consumed -ne $timestamp.ContentInfo.Content.Length -or
        $timestampInfo.HashAlgorithmId.Value -cne '2.16.840.1.101.3.4.2.1' -or
        $timestamp.SignerInfos.Count -ne 1 -or
        $timestamp.SignerInfos[0].DigestAlgorithm.Value -cne '2.16.840.1.101.3.4.2.1') { throw 'Production timestamp must use RFC3161 and SHA-256' }
}

function Assert-TestSignature([string]$Path) {
    $pin = if ($SigningMode -ceq 'Production') { $CertificateThumbprint } elseif ($CertificateThumbprint) { $CertificateThumbprint } else { $env:TEST_CERT_THUMBPRINT }
    if ($pin -notmatch '^[0-9A-Fa-f]{40}$' -or
        ($SigningMode -ceq 'Production' -and $pin -ieq '17F9F411C424674855A077F9A120A7A434990AF1')) {
        throw 'TEST certificate thumbprint is missing or invalid'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($null -eq $signature.SignerCertificate -or
        $signature.SignerCertificate.Thumbprint -ine $pin -or
        $signature.SignatureType.ToString() -cne 'Authenticode') {
        throw "Unexpected embedded TEST signer: $Path"
    }
    # The pinned TEST certificate may be self-signed. Do not install trust.
    $status = $signature.Status.ToString()
    if ($SigningMode -ceq 'Production') {
        if ($status -cne 'Valid' -or $null -eq $signature.TimeStamperCertificate -or
            (Get-TestSignatureTrustStatus $Path $true) -ne 0) { throw 'Production requires a trusted, timestamped embedded signature' }
        Assert-ProductionTimestamp $Path
        return
    }
    if ($status -cnotin @('Valid', 'NotTrusted', 'UnknownError')) {
        throw "Rejected TEST signature status: $($signature.Status)"
    }
    $nativeStatus = Get-TestSignatureTrustStatus $Path
    if ($status -ceq 'Valid' -and $nativeStatus -eq 0) { return }
    # CERT_E_UNTRUSTEDROOT only, after pinning the embedded TEST signer.
    # This is not Windows trust success; every other native failure is fatal.
    if ($status -cin @('NotTrusted', 'UnknownError') -and $nativeStatus -eq -2146762487) { return }
    throw ('Rejected TEST signature native status: 0x{0:X8}' -f $nativeStatus)
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

if ($PSCmdlet.ParameterSetName -eq 'InnoHelper') {
    $helper = Assert-RegularPath $InnoHelperPath $false
    $unsignedHelper = Assert-RegularPath $UnsignedInnoHelperPath $false
    $name = 'inno-helper-amd64.exe'
    if ([IO.Path]::GetFileName($helper) -cne $name -or
        [IO.Path]::GetFileName($unsignedHelper) -cne $name) { throw 'Unexpected Inno helper filename' }
    foreach ($path in @($helper, $unsignedHelper)) {
        $null = Get-RegularTree ([IO.Path]::GetDirectoryName($path)) @($name)
    }
    $null = Read-CheckedSignedPe $unsignedHelper $helper
    return
}
if ($PSCmdlet.ParameterSetName -eq 'Signature') {
    # Additional installed-file smoke checks only; packaging must still use
    # Payload/Internal/Installer comparison against its unsigned originals.
    $path = Assert-RegularPath $SignaturePath $false
    Assert-TestSignature $path
    return
}
if ($PSCmdlet.ParameterSetName -eq 'Installer') {
    $installer = Assert-RegularPath $InstallerPath $false
    $unsignedInstaller = Assert-RegularPath $UnsignedInstallerPath $false
    $name = [IO.Path]::GetFileName($installer)
    if (($SigningMode -ceq 'Test' -and $name -cne 'HaloBattery-test-setup.exe') -or
        ($SigningMode -ceq 'Production' -and $name -cnotmatch '^HaloBattery-[0-9]+\.[0-9]+\.[0-9]+-setup\.exe$') -or
        [IO.Path]::GetFileName($unsignedInstaller) -cne $name) { throw 'Unexpected installer filename' }
    $null = Get-RegularTree ([IO.Path]::GetDirectoryName($installer)) @($name)
    $null = Read-CheckedSignedPe $unsignedInstaller $installer
    return
}
$own = @('HaloBattery.exe', '_internal/settings-ui/HaloBattery.Settings.exe')
if ($InternalManifestPath) {
    $null = Assert-RegularPath $InternalManifestPath $false
    $internal = Get-Content -LiteralPath $InternalManifestPath -Raw | ConvertFrom-Json -AsHashtable
    if ($internal -isnot [Collections.IDictionary] -or $internal.Count -lt 1 -or $internal.Count -gt 4) { throw 'Invalid internal PE manifest' }
    foreach ($name in $internal.Keys) {
        if ($name -cnotmatch '^uninst-6\.7\.4-[a-f0-9]{10}\.e(32|64)$' -or $internal[$name] -isnot [string] -or
            $internal[$name] -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'Invalid internal PE filename or hash' }
    }
    $own = @($internal.Keys)
}
$signedRoot = Assert-RegularPath $SignedDirectory $true
$destinationRoot = Assert-RegularPath $DestinationDirectory $true
$separator = [IO.Path]::DirectorySeparatorChar
if ($signedRoot.Equals($destinationRoot, [StringComparison]::OrdinalIgnoreCase) -or
    ($signedRoot + $separator).StartsWith($destinationRoot + $separator, [StringComparison]::OrdinalIgnoreCase) -or
    ($destinationRoot + $separator).StartsWith($signedRoot + $separator, [StringComparison]::OrdinalIgnoreCase)) { throw 'Signed and destination trees must be disjoint' }
$signedFiles = Get-RegularTree $signedRoot $own
$inventory = if ($InternalManifestPath) { $InternalManifestPath } else { $InventoryPath }
$null = Assert-RegularPath $inventory $false
$before = Get-Content -LiteralPath $inventory -Raw | ConvertFrom-Json -AsHashtable
if ($before -isnot [Collections.IDictionary]) { throw 'Invalid unsigned inventory' }
if ($InternalManifestPath -and ($before.Count -ne $internal.Count -or
    @(Compare-Object @($before.Keys) @($internal.Keys) -CaseSensitive).Count)) { throw 'Internal manifest differs from inventory' }
if ($InternalManifestPath) {
    foreach ($name in $own) {
        if ($before[$name] -cne $internal[$name]) { throw 'Internal manifest hash differs from inventory' }
    }
}
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
