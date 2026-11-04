import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nothing in these tests may reach a real host: every HTTP client must carry a mock transport (see tests/fakes.py)."""
    def refuse(self, request):
        raise AssertionError(f"a test tried to reach the network: {request.method} {request.url}")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def _no_ambient_admin_password(monkeypatch):
    """The operator's own ECOCTL_ADMIN_PASSWORD must not leak into a test. ecoctl never stores or makes up the vault password, so tests that create
    a vault get one from the environment, as an operator does; the ones about its absence remove it."""
    monkeypatch.setenv("ECOCTL_ADMIN_PASSWORD", ADMIN_PASSWORD)


PASSPHRASE = "test-passphrase-for-the-state-file-0"
ADMIN_PASSWORD = "operator-held-admin-password-for-tests-0"


@pytest.fixture(autouse=True)
def _state_passphrase(monkeypatch):
    """The state file is encrypted: every test runs with a passphrase (and never the operator's own). The scrypt cost is lowered for speed (the blob does not
    record it; seal and open both read the same module value). `test_state.py` runs one test with the real cost."""
    from apgkit import sealed
    monkeypatch.setenv("ECOCTL_SEAL_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(sealed, "_KDF", (10, 8, 1))


def read_state(path) -> dict:
    """The decrypted content of a state file (tests only: ecoctl has no command that prints it)."""
    import json
    from pathlib import Path

    from apgkit import sealed
    return json.loads(sealed.open_blob(Path(path).read_text(), PASSPHRASE)["state"])
