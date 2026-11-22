"""Release availability of provider controls; never authorizes device I/O.

Providers retain their own target allowlists and diagnostics. These entries
cover only provider families with no enabled release target, so mixed families
keep their useful switches. Stored preferences are deliberately not migrated.
"""
from collections.abc import Mapping
from types import MappingProxyType


HELD_PROVIDER_REASONS = MappingProxyType({
    "endgame_4k": "No enabled 4K target; correction and recovery review pending (#321/#324).",
    "lamzu_sw": "Aurora query is sourced; Tachi receiver verification is pending (#262).",
})


def provider_available(name: str, labels: Mapping[str, str]) -> bool:
    return isinstance(name, str) and name in labels and name not in HELD_PROVIDER_REASONS


def available_provider_labels(labels: Mapping[str, str]) -> dict[str, str]:
    return {name: label for name, label in labels.items() if provider_available(name, labels)}
