"""Acquire pinned NativeAOT build inputs without installing or executing payloads.

Only the CLI performs downloads. Unit tests inject local archives and a signature
checker; production always uses Windows Authenticode before returning tools.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

TRUSTED_HOSTS = {'download.visualstudio.microsoft.com', 'api.nuget.org'}
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def no_links(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError(f'Reparse point in toolchain path: {parent}')


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or any(ord(c) < 32 or c in '\\:<>"|?*' for c in name) or path.is_absolute()
            or any(p in ('', '.', '..') or p[-1:] in ('.', ' ')
                   or p.split('.')[0].upper() in RESERVED for p in name.rstrip('/').split('/'))):
        raise ValueError(f'Unsafe archive path: {name}')
    return path


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            sha.update(block)
    return sha.hexdigest()


def check_archive(path: Path, pin: dict) -> None:
    no_links(path)
    if not path.is_file() or path.stat().st_size != pin['bytes'] or digest(path) != pin['sha256']:
        raise ValueError(f'Toolchain archive size/hash mismatch: {path.name}')


class PinnedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            raise ValueError('Toolchain download redirected to a different host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in TRUSTED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise ValueError('Toolchain URL must use an approved HTTPS host')


def download(pin: dict, target: Path, offline: bool) -> None:
    if target.exists():
        check_archive(target, pin)
        return
    if offline:
        raise ValueError(f'Offline toolchain archive missing: {target.name}')
    validate_url(pin['url'])
    fd, partial_name = tempfile.mkstemp(prefix='download-', suffix='.partial', dir=target.parent)
    partial = Path(partial_name)
    try:
        with os.fdopen(fd, 'wb') as out, urllib.request.build_opener(PinnedRedirects()).open(pin['url'], timeout=60) as source:
            total = 0
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > pin['bytes']:
                    raise ValueError('Toolchain download exceeds its exact pinned size')
                out.write(block)
        check_archive(partial, pin)
        partial.rename(target)
    finally:
        if partial.exists():
            partial.unlink()


def plan_entries(lock: dict, archive_root: Path) -> list[tuple[Path, str, Path, int]]:
    entries = []
    seen = set()
    expanded = 0
    count = 0
    for pin in lock['inputs']:
        archive = archive_root / pin['name']
        check_archive(archive, pin)
        with zipfile.ZipFile(archive) as zf:
            for item in zf.infolist():
                count += 1
                expanded += item.file_size
                path = safe_relative(item.orig_filename)
                if item.orig_filename != item.filename:
                    raise ValueError('Unsafe archive path normalization')
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('Archive links are forbidden')
                if count > lock['maxEntries'] or expanded > lock['maxExpandedBytes']:
                    raise ValueError('Toolchain extraction bounds exceeded')
                if item.is_dir() or ('include' in pin and item.filename not in pin['include']):
                    continue
                if pin['kind'] == 'msvc':
                    if path.parts[0] != 'Contents':
                        continue
                    relative = Path('msvc', *path.parts[1:])
                else:
                    relative = Path('windows-sdk-x64', *path.parts)
                key = str(relative).casefold()
                if key in seen:
                    raise ValueError(f'Duplicate toolchain output: {relative}')
                seen.add(key)
                entries.append((archive, item.filename, relative, item.file_size))
    return entries


def verify_microsoft_signatures(paths: list[Path]) -> None:
    # JSON enters via stdin, never interpolated into PowerShell source or command text.
    command = r'''$ErrorActionPreference='Stop'; Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1') -ErrorAction Stop; $items=@([Console]::In.ReadToEnd() | ConvertFrom-Json); foreach($path in $items) { $signature=Get-AuthenticodeSignature -LiteralPath $path; if($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -notin @('Microsoft Corporation', 'Microsoft Windows', 'Microsoft Windows Software Compatibility Publisher') -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') { throw "Microsoft Authenticode verification failed: $path ($($signature.Status))" } }'''
    shell = shutil.which('pwsh.exe') or str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
    process = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', command],
                             input=json.dumps([str(p) for p in paths]), text=True,
                             capture_output=True, timeout=300, check=False)
    if process.returncode:
        raise ValueError('Toolchain Authenticode check failed: ' + process.stderr.strip())


def validate_lock(lock: dict) -> None:
    if (lock.get('version') != 1 or not lock.get('inputs') or not 1 <= lock.get('maxEntries', 0) <= 20000
            or not 1 <= lock.get('maxExpandedBytes', 0) <= 1000000000):
        raise ValueError('Invalid toolchain lock')
    safe_relative(lock['msvcLayout'])
    names = set()
    for pin in lock['inputs']:
        path = safe_relative(pin['name'])
        if len(path.parts) != 1 or pin['name'].casefold() in names:
            raise ValueError('Invalid or duplicate toolchain archive name')
        names.add(pin['name'].casefold())
        if (not re.fullmatch('[a-f0-9]{64}', pin['sha256']) or type(pin['bytes']) is not int
                or not 0 < pin['bytes'] <= 150000000 or pin['kind'] not in ('msvc', 'sdk')):
            raise ValueError('Invalid toolchain archive pin')
        validate_url(pin['url'])
        for name in pin.get('include', []):
            safe_relative(name)


def promote_generation(stage: Path, generation: Path) -> None:
    # Windows scanners can briefly hold newly created executable files. Retry
    # only the exact owned rename, never remove/replace an existing generation.
    if (stage.parent != generation.parent or not stage.name.startswith('extract-')
            or not generation.name.startswith('inputs-')):
        raise ValueError('Refusing an unrelated toolchain generation move')
    for attempt in range(5):
        no_links(stage)
        no_links(generation)
        if generation.exists():
            raise FileExistsError('Toolchain generation appeared during acquisition')
        try:
            stage.rename(generation)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.05 * (2 ** attempt))


def prepare(lock: dict, root: Path, *, offline: bool = False,
            signature_checker=verify_microsoft_signatures) -> dict:
    validate_lock(lock)
    root = root.absolute()
    no_links(root)
    if root == Path(root.anchor) or not root.name:
        raise ValueError('Use a dedicated toolchain cache directory')
    root.mkdir(parents=True, exist_ok=True)
    guard = root / '.acquire.lock'
    # A concurrent or interrupted acquisition fails closed; never remove a lock
    # owned by a different process or silently trust a partially extracted cache.
    with guard.open('x', encoding='ascii') as handle:
        handle.write(str(os.getpid()))
    stage = None
    try:
        archive_root = root / 'archives'
        no_links(archive_root)
        archive_root.mkdir(exist_ok=True)
        for pin in lock['inputs']:
            download(pin, archive_root / pin['name'], offline)
        entries = plan_entries(lock, archive_root)
        stage = Path(tempfile.mkdtemp(prefix='extract-', dir=root))
        for archive, name, relative, size in entries:
            out = stage / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as zf, zf.open(name) as source, out.open('xb') as target:
                copied = 0
                while block := source.read(1024 * 1024):
                    copied += len(block)
                    if copied > size:
                        raise ValueError('Toolchain entry exceeds advertised size')
                    target.write(block)
                if copied != size:
                    raise ValueError('Toolchain entry size mismatch')
        native = sorted(p for p in stage.rglob('*') if p.is_file() and p.suffix.lower() in ('.exe', '.dll'))
        if not native:
            raise ValueError('Toolchain contains no executable inputs')
        signature_checker(native)
        msvc = stage / 'msvc' / lock['msvcLayout']
        sdk = stage / 'windows-sdk-x64'
        for required in (msvc / 'bin/Hostx64/x64/link.exe', msvc / 'bin/Hostx64/x64/cl.exe',
                         msvc / 'lib/x64/libcmt.lib', msvc / 'lib/x64/oldnames.lib', msvc / 'include/vcruntime.h',
                         sdk / 'c/um/x64/kernel32.lib', sdk / 'c/ucrt/x64/ucrt.lib'):
            if not required.is_file():
                raise ValueError(f'Incomplete pinned toolchain: {required.relative_to(stage)}')
        # An immutable generation avoids accumulating hundreds of MB per build.
        # Reuse is permitted only after a fresh extraction/trust check and exact
        # byte comparison of every cached file; extra cache files fail closed.
        identity = hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()
        generation = root / ('inputs-' + identity)
        no_links(generation)
        if generation.exists():
            if not generation.is_dir():
                raise ValueError('Toolchain generation path is not a directory')
            expected = {p.relative_to(stage) for p in stage.rglob('*') if p.is_file()}
            actual = set()
            for item in generation.rglob('*'):
                no_links(item)
                if item.is_file():
                    actual.add(item.relative_to(generation))
            if actual != expected:
                raise ValueError('Toolchain generation has missing or unexpected files')
            for relative in expected:
                if digest(stage / relative) != digest(generation / relative):
                    raise ValueError('Toolchain generation file changed: ' + str(relative))
            shutil.rmtree(stage)
        else:
            promote_generation(stage, generation)
        msvc = generation / 'msvc' / lock['msvcLayout']
        sdk = generation / 'windows-sdk-x64'
        result = {'root': str(generation), 'msvc': str(msvc), 'sdk': str(sdk),
                  'authenticodeFiles': len(native), 'installed': False}
        stage = None
        return result
    finally:
        if stage is not None:
            no_links(stage)
            for item in stage.rglob('*'):
                no_links(item)
            shutil.rmtree(stage)
        guard.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding='utf-8'))
    print(json.dumps(prepare(lock, args.cache, offline=args.offline)))


if __name__ == '__main__':
    main()
