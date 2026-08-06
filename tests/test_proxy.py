"""Tests for advisorfinder_mcp.proxy — the stdio->remote proxy that is the
PyPI package's actual entry point (`pip install advisorfinder-mcp` gets you
this, not the server). Monkeypatches FastMCP.as_proxy to capture what it was
called with; never actually calls .run() (that would block on stdio).
"""
from advisorfinder_mcp import proxy


class _FakeProxyApp:
    def __init__(self):
        self.ran = False

    def run(self):
        self.ran = True


def test_main_uses_default_url_when_no_env_override(monkeypatch):
    monkeypatch.delenv("ADVISORFINDER_MCP_URL", raising=False)
    captured = {}
    fake_app = _FakeProxyApp()

    def fake_as_proxy(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_app

    monkeypatch.setattr(proxy.FastMCP, "as_proxy", staticmethod(fake_as_proxy))

    proxy.main()

    assert captured["url"] == "https://mcp.advisorfinder.com/mcp"
    assert captured["kwargs"] == {"name": "advisorfinder"}
    assert fake_app.ran is True


def test_main_honors_url_env_override(monkeypatch):
    monkeypatch.setenv("ADVISORFINDER_MCP_URL", "https://staging.example.com/mcp")
    captured = {}
    fake_app = _FakeProxyApp()

    def fake_as_proxy(url, **kwargs):
        captured["url"] = url
        return fake_app

    monkeypatch.setattr(proxy.FastMCP, "as_proxy", staticmethod(fake_as_proxy))

    proxy.main()

    assert captured["url"] == "https://staging.example.com/mcp"
    assert fake_app.ran is True


def test_default_url_constant():
    assert proxy.DEFAULT_URL == "https://mcp.advisorfinder.com/mcp"
