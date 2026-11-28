"""Bounded Inno 6.7.4 helper extraction and append-only template preparation.

Signatures are authenticated by verify_test_signed_payload.ps1 before patching.
This module additionally requires the signed helper to preserve the exact pinned
unsigned executable. It never executes any input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

TEMPLATE_SHA256 = "0c94d43e2c0f4d8371a2e1813b5446f3bc85ece3211dc4a6676159b534bbc483"
HELPER_SHA256 = "388a796580234efc95f3b1c70ad4cb44bfddc7ba0f9203bf4902b9929b136f95"
MAX_PE_SIZE = 32 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("Truncated PE field")
    return struct.unpack_from("<I", data, offset)[0]


def pe_layout(data):
    if not 64 <= len(data) <= MAX_PE_SIZE or data[:2] != b"MZ":
        raise ValueError("Invalid PE size or DOS header")
    header = u32(data, 60)
    if header + 24 > len(data) or data[header:header + 4] != b"PE\0\0":
        raise ValueError("Invalid PE header")
    count, optional_size = struct.unpack_from("<H12xH", data, header + 6)
    optional = header + 24
    if count < 1 or count > 96 or optional + optional_size > len(data):
        raise ValueError("Invalid PE section inventory")
    magic = struct.unpack_from("<H", data, optional)[0]
    directory = optional + ({0x10B: 96, 0x20B: 112}.get(magic, -100000))
    if directory < optional or directory + 40 > optional + optional_size:
        raise ValueError("Invalid PE optional header")
    sections = []
    table = optional + optional_size
    if table + count * 40 > len(data):
        raise ValueError("Truncated PE sections")
    for index in range(count):
        offset = table + index * 40
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, offset + 8)
        if raw_size and (raw + raw_size > len(data) or raw < table + count * 40):
            raise ValueError("Invalid PE section bounds")
        sections.append((offset, virtual_size, rva, raw_size, raw))
    return optional, directory, sections


def extract_helper(data):
    _, directory, sections = pe_layout(data)
    resource_rva, resource_size = struct.unpack_from("<II", data, directory + 16)

    def mapped(rva, size):
        candidates = [raw + rva - start for _, _, start, length, raw in sections
                      if start <= rva and rva + size <= start + length]
        if len(candidates) != 1:
            raise ValueError("Unmapped or overlapping resource RVA")
        return candidates[0]

    base = mapped(resource_rva, resource_size)

    def bounded(relative, length):
        if relative < 0 or relative + length > resource_size:
            raise ValueError("Resource escapes directory")
        return base + relative

    def child(relative, wanted, named=False):
        offset = bounded(relative, 16)
        named_count, id_count = struct.unpack_from("<HH", data, offset + 12)
        total = named_count + id_count
        if total > 4096:
            raise ValueError("Excessive resource entries")
        bounded(relative + 16, total * 8)
        matches = []
        for index in range(total):
            name, target = struct.unpack_from("<II", data, offset + 16 + index * 8)
            if name & 0x80000000:
                string_offset = name & 0x7FFFFFFF
                length = struct.unpack_from("<H", data, bounded(string_offset, 2))[0]
                value = data[bounded(string_offset + 2, length * 2):base + string_offset + 2 + length * 2].decode("utf-16-le")
            else:
                value = name
            if value == wanted and bool(name & 0x80000000) == named:
                matches.append(target)
        if len(matches) != 1:
            raise ValueError("Missing or duplicate helper resource")
        return matches[0]

    kind = child(0, 10)
    if not kind & 0x80000000:
        raise ValueError("Invalid resource type directory")
    name = child(kind & 0x7FFFFFFF, "HELPER_EXE_AMD64", True)
    if not name & 0x80000000:
        raise ValueError("Invalid resource name directory")
    language = child(name & 0x7FFFFFFF, 1033)
    if language & 0x80000000:
        raise ValueError("Invalid resource data entry")
    entry = bounded(language, 16)
    rva, size = struct.unpack_from("<II", data, entry)
    if not 512 <= size <= 1024 * 1024:
        raise ValueError("Invalid helper size")
    raw = mapped(rva, size)
    return data[raw:raw + size], entry


def pinned_template(data):
    if digest(data) != TEMPLATE_SHA256:
        raise ValueError("Template does not match pinned Inno 6.7.4 Setup.e32")
    helper, entry = extract_helper(data)
    if len(helper) != 6144 or digest(helper) != HELPER_SHA256:
        raise ValueError("Unexpected pinned helper")
    return helper, entry


def signed_equivalent(unsigned, signed):
    optional, directory, _ = pe_layout(unsigned)
    signed_optional, signed_directory, _ = pe_layout(signed)
    if (optional, directory) != (signed_optional, signed_directory):
        raise ValueError("Signed helper headers changed")
    address, size = struct.unpack_from("<II", signed, directory + 32)
    if address != len(unsigned) or size < 8 or size > 1024 * 1024 or address + size != len(signed):
        raise ValueError("Helper lacks an exact appended certificate")
    normalized = bytearray(signed[:len(unsigned)])
    normalized[optional + 64:optional + 68] = unsigned[optional + 64:optional + 68]
    normalized[directory + 32:directory + 40] = unsigned[directory + 32:directory + 40]
    if normalized != unsigned:
        raise ValueError("Signed helper executable changed")


def patch_template(template, signed):
    unsigned, entry = pinned_template(template)
    signed_equivalent(unsigned, signed)
    optional, directory, sections = pe_layout(template)
    last = sections[-1]
    section, virtual_size, rva, raw_size, raw = last
    if template[section:section + 8] != b".rsrc\0\0\0" or raw + raw_size != len(template):
        raise ValueError("Template resource section is not final")
    if any(start + max(length, virtual) > rva for _, virtual, start, length, _ in sections[:-1]):
        raise ValueError("Overlapping final resource section")
    if u32(template, directory + 32) or u32(template, directory + 36):
        raise ValueError("Unexpected template signature")
    file_align, section_align = u32(template, optional + 36), u32(template, optional + 32)
    if file_align != 512 or section_align != 4096:
        raise ValueError("Unexpected template alignment")
    new_virtual_size = len(template) - raw + len(signed)
    new_raw_size = (new_virtual_size + file_align - 1) // file_align * file_align
    output = bytearray(template)
    output.extend(signed)
    output.extend(bytes(raw + new_raw_size - len(output)))
    changes = {entry: rva + len(template) - raw, entry + 4: len(signed),
               section + 8: new_virtual_size, section + 16: new_raw_size,
               optional + 56: (rva + new_virtual_size + section_align - 1) // section_align * section_align,
               directory + 20: new_virtual_size,
               optional + 8: u32(template, optional + 8) + new_raw_size - raw_size}
    for offset, value in changes.items():
        struct.pack_into("<I", output, offset, value)
    if extract_helper(output)[0] != signed:
        raise ValueError("Patched resource did not round trip")
    return bytes(output)


def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PE_SIZE:
        raise ValueError("Invalid input file")
    return path.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("extract-template", "patch-template", "extract-engine"):
        sub = commands.add_parser(command)
        sub.add_argument("--engine" if command == "extract-engine" else "--template", required=True)
        sub.add_argument("--output", required=True)
        if command == "patch-template":
            sub.add_argument("--signed-helper", required=True)
    args = parser.parse_args()
    source = read(args.engine if args.command == "extract-engine" else args.template)
    if args.command == "patch-template":
        signed = read(args.signed_helper)
        result = patch_template(source, signed)
    elif args.command == "extract-template":
        result = pinned_template(source)[0]
    else:
        result = extract_helper(source)[0]
        pe_layout(result)
    with Path(args.output).open("xb") as output:
        output.write(result)
    print(json.dumps({"source_sha256": digest(source), "output_sha256": digest(result),
                      "output_size": len(result)}))


if __name__ == "__main__":
    main()
