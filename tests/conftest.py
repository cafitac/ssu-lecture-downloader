import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def loopback_test_client(monkeypatch):
    """The API rejects non-loopback Host headers, so test clients talk to 127.0.0.1."""
    original_init = TestClient.__init__

    def init(self, app, base_url="http://127.0.0.1:8000", *args, **kwargs):
        original_init(self, app, base_url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", init)
