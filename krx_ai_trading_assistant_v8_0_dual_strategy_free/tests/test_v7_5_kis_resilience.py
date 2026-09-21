import time
from pathlib import Path

import requests
import pytest

from stockbot.kis import KISClient, KISNetworkError


class DummySettings:
    config = {}
    def path(self, key):
        return Path('/tmp/nonexistent-kis-token.json')


def test_kis_network_error_type_exists():
    assert issubclass(KISNetworkError, RuntimeError)


def test_kis_circuit_breaker_short_circuits(monkeypatch):
    monkeypatch.setenv('KIS_APP_KEY', 'x')
    monkeypatch.setenv('KIS_APP_SECRET', 'y')
    client = KISClient(DummySettings())
    client._network_down_until = time.monotonic() + 30
    with pytest.raises(KISNetworkError):
        client._get('/x', 'TR', {})
