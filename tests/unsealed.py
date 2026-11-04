"""The repository's ecosystem.yaml with `seal: false`, for the tests of everything that is not sealed boot (the real manifest seals by default).

The fakes in tests/fakes.py have no /_seal routes; `tests/test_seal.py` covers the sealed path against the kit's real ones.
"""
import tempfile
from pathlib import Path

REPO_MANIFEST = Path(__file__).resolve().parents[1] / "ecosystem.yaml"
_TMP = tempfile.mkdtemp(prefix="ecoctl-tests-")
UNSEALED = Path(_TMP) / "ecosystem.yaml"
_text = REPO_MANIFEST.read_text()
assert "\n  seal: true " in _text
UNSEALED.write_text(_text.replace("\n  seal: true ", "\n  seal: false", 1))
