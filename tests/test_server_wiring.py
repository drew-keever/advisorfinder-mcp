"""Tests for server.py's top-level wiring: the /health custom route and the
3 resources being registered on the actual `mcp` instance the module builds
(not just on a throwaway FastMCP instance, as in test_resources.py)."""
import asyncio
import json

from advisorfinder_mcp import server


def test_health_route_registered_on_http_app():
    app = server.mcp.http_app()
    paths = [getattr(route, "path", None) for route in app.routes]
    assert "/health" in paths


def test_health_route_returns_200_json():
    # custom_route (like tool/resource) returns the plain function unchanged
    # on fastmcp 3.4.6, so call it directly rather than pulling in an ASGI
    # test client just to exercise a one-line handler.
    response = asyncio.run(server.health(None))
    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok"}


def test_resources_registered_on_real_mcp_instance():
    regs = asyncio.run(server.mcp.list_resources())
    uris = {str(r.uri) for r in regs}
    assert uris == {
        "advisorfinder://credentials-guide",
        "advisorfinder://data-sources",
        "advisorfinder://coverage-and-limitations",
    }


def test_all_six_tools_registered():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "search_advisors", "get_advisor", "check_advisor",
        "search_firms", "get_firm", "get_database_stats",
    }
