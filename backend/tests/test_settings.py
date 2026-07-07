"""Settings load hermetically — ignore any local backend/.env and stray env vars."""
from app.settings import Settings

_VARS = ("NAI_API__HOST", "NAI_API__PORT", "NAI_LOG__LEVEL")


def test_defaults(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.api.host == "127.0.0.1"
    assert s.api.port == 8787
    assert s.log.level == "INFO"


def test_port_override_from_env(monkeypatch):
    monkeypatch.setenv("NAI_API__PORT", "9999")
    s = Settings(_env_file=None)
    assert s.api.port == 9999
