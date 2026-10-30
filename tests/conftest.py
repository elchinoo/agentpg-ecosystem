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
    """The operator's own ECOCTL_ADMIN_PASSWORD must not leak into a test."""
    monkeypatch.delenv("ECOCTL_ADMIN_PASSWORD", raising=False)
