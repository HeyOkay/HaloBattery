"""Explicit diagnostic-only Tachi receiver tester using the existing native transport."""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from halo import device_diagnostics as _diagnostics


def _load_backend():
    return _diagnostics._load_backend()


def _create_runtime():
    return _diagnostics._create_runtime()


def _create_provider():
    return _diagnostics._create_provider("lamzu-tachi")


def run(*, acknowledged=False):
    return _diagnostics.run("lamzu-tachi", acknowledged=acknowledged,
                            load_backend=_load_backend, create_runtime=_create_runtime,
                            create_provider=_create_provider)


def main(argv=None):
    return _diagnostics.main(argv, device="lamzu-tachi", runner=run)


if __name__ == "__main__":
    raise SystemExit(main())
