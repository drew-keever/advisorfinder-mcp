"""Abuse protection for tool calls (guard.py): the real client IP, the
per-IP rate limits by source class, and the one-line usage log per tool
call. The limiter is driven directly with a fake clock; the middleware is
driven over real HTTP (ASGI) requests against a small FastMCP server, so the
real header path is exercised without touching server.mcp's global state."""
import asyncio
import ipaddress
import logging

import httpx
import pytest
from fastmcp import Client, FastMCP

from advisorfinder_mcp import guard, server


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ── client IP ────────────────────────────────────────────────────────────────

def test_client_ip_prefers_cf_connecting_ip():
    headers = {
        "cf-connecting-ip": "1.2.3.7",
        "true-client-ip": "198.51.100.1",
        "x-forwarded-for": "192.0.2.1, 10.0.0.1",
    }
    assert guard.client_ip(headers, "10.1.2.3") == ("1.2.3.7", "cf-connecting-ip")


def test_client_ip_true_client_ip_second():
    headers = {"true-client-ip": "198.51.100.1", "x-forwarded-for": "192.0.2.1"}
    assert guard.client_ip(headers, "10.1.2.3") == ("198.51.100.1", "true-client-ip")


def test_client_ip_x_forwarded_for_last_resort_takes_first_entry():
    headers = {"x-forwarded-for": " 192.0.2.1 , 10.0.0.1"}
    assert guard.client_ip(headers, "10.1.2.3") == ("192.0.2.1", "x-forwarded-for")


def test_client_ip_falls_back_to_peer():
    assert guard.client_ip({}, "1.2.3.9") == ("1.2.3.9", "peer")


def test_client_ip_none_without_any_source():
    assert guard.client_ip({}, None) == (None, "none")


def test_client_ip_skips_unparseable_header_values():
    headers = {"cf-connecting-ip": "not-an-ip", "true-client-ip": "198.51.100.1"}
    assert guard.client_ip(headers, None) == ("198.51.100.1", "true-client-ip")
    assert guard.client_ip({"x-forwarded-for": "garbage, also bad"}, "1.2.3.9") == (
        "1.2.3.9", "peer",
    )


def test_client_ip_normalizes_ipv4_mapped_ipv6_and_case():
    assert guard.client_ip({"cf-connecting-ip": "::ffff:160.79.104.10"}, None) == (
        "160.79.104.10", "cf-connecting-ip",
    )
    assert guard.client_ip({"cf-connecting-ip": "2001:DB8::1"}, None) == (
        "2001:db8::1", "cf-connecting-ip",
    )


def test_client_ip_header_order_is_a_module_constant():
    assert guard.CLIENT_IP_HEADERS == ("cf-connecting-ip", "true-client-ip", "x-forwarded-for")
    headers = {"cf-connecting-ip": "1.2.3.7", "true-client-ip": "198.51.100.1"}
    assert guard.client_ip(headers, None, order=("true-client-ip",)) == (
        "198.51.100.1", "true-client-ip",
    )


def test_header_order_env_override(monkeypatch):
    monkeypatch.setenv("MCP_CLIENT_IP_HEADERS", " True-Client-IP , cf-connecting-ip ")
    assert guard.header_order_from_env() == ("true-client-ip", "cf-connecting-ip")
    monkeypatch.setenv("MCP_CLIENT_IP_HEADERS", "")
    assert guard.header_order_from_env() == guard.CLIENT_IP_HEADERS


# ── source class ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip,expected", [
    ("160.79.104.0", "anthropic"),
    ("160.79.104.1", "anthropic"),
    ("160.79.111.255", "anthropic"),
    ("160.79.103.255", "other"),
    ("160.79.112.0", "other"),
    ("2607:6bc0::1", "other"),  # documented as inbound only, not outbound
    ("1.2.3.7", "other"),
])
def test_source_class_cidr_boundary(ip, expected):
    assert guard.source_class(ip) == expected


def test_anthropic_networks_are_the_documented_outbound_range():
    assert guard.ANTHROPIC_NETWORKS == (ipaddress.ip_network("160.79.104.0/21"),)


# ── limiter ──────────────────────────────────────────────────────────────────

def _limiter(clock, **kw):
    limits = {
        "anthropic": guard.Limits(per_minute=kw.get("a_min", 600), per_day=kw.get("a_day", 0)),
        "other": guard.Limits(per_minute=kw.get("o_min", 60), per_day=kw.get("o_day", 1000)),
    }
    return guard.RateLimiter(limits, max_tracked=kw.get("max_tracked", 10_000), clock=clock)


def test_other_class_per_minute_burst_then_blocked():
    clock = FakeClock()
    lim = _limiter(clock)
    for _ in range(60):
        assert lim.check("1.2.3.7", "other") is None
    retry = lim.check("1.2.3.7", "other")
    assert retry is not None and 0 < retry <= 1.0  # one token refills in 1 s
    # Another IP has its own bucket.
    assert lim.check("1.2.3.8", "other") is None


def test_per_minute_window_refills_with_time():
    clock = FakeClock()
    lim = _limiter(clock)
    for _ in range(60):
        lim.check("1.2.3.7", "other")
    assert lim.check("1.2.3.7", "other") is not None
    clock.advance(1.0)
    assert lim.check("1.2.3.7", "other") is None
    assert lim.check("1.2.3.7", "other") is not None
    clock.advance(60)
    for _ in range(60):
        assert lim.check("1.2.3.7", "other") is None


def test_anthropic_class_has_a_larger_per_minute_limit_and_no_daily_cap():
    clock = FakeClock()
    lim = _limiter(clock)
    for _ in range(600):
        assert lim.check("160.79.104.5", "anthropic") is None
    assert lim.check("160.79.104.5", "anthropic") is not None
    # No daily cap: 5,000 calls spread out over a day all pass.
    for _ in range(5_000):
        clock.advance(10)
        assert lim.check("160.79.104.6", "anthropic") is None


def test_other_class_daily_cap_and_rolling_reset():
    clock = FakeClock(0.0)
    lim = _limiter(clock)
    # 1,000 calls, 1 per 2 s: never near the per-minute limit.
    for _ in range(1_000):
        assert lim.check("1.2.3.7", "other") is None
        clock.advance(2)
    retry = lim.check("1.2.3.7", "other")
    assert retry is not None
    assert retry > 60  # a daily wait, not a per-minute one
    # Still blocked a few hours later (the window is a rolling 24 hours).
    clock.advance(3 * 3600)
    assert lim.check("1.2.3.7", "other") is not None
    # 24 hours after the first calls, the oldest ones have left the window.
    clock.t = 25 * 3600
    assert lim.check("1.2.3.7", "other") is None


def test_blocked_calls_do_not_count():
    clock = FakeClock()
    lim = _limiter(clock, o_min=2, o_day=5)
    assert lim.check("1.2.3.7", "other") is None
    assert lim.check("1.2.3.7", "other") is None
    for _ in range(50):
        assert lim.check("1.2.3.7", "other") is not None
    clock.advance(60)
    # Only 2 of the 5 daily calls were used.
    assert lim.check("1.2.3.7", "other") is None
    assert lim.check("1.2.3.7", "other") is None


def test_zero_means_no_limit():
    clock = FakeClock()
    lim = _limiter(clock, o_min=0, o_day=0)
    for _ in range(5_000):
        assert lim.check("1.2.3.7", "other") is None


def test_tracked_ips_are_capped():
    clock = FakeClock()
    lim = _limiter(clock, max_tracked=100)
    for i in range(1_000):
        lim.check(f"10.0.{i // 256}.{i % 256}", "other")
        assert lim.tracked_count() <= 100
    assert lim.tracked_count() == 100


def test_idle_entries_are_cleaned_up():
    clock = FakeClock()
    lim = _limiter(clock)
    for i in range(50):
        lim.check(f"1.2.3.{i}", "other")
        lim.check(f"160.79.104.{i}", "anthropic")
    assert lim.tracked_count() == 100
    clock.advance(120)  # anthropic entries (no daily window) are now idle
    lim.cleanup()
    assert lim.tracked_count() == 50
    clock.advance(25 * 3600)  # other entries have left the 24 h window
    lim.cleanup()
    assert lim.tracked_count() == 0


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("MCP_RATE_LIMIT_OTHER_PER_MINUTE", "5")
    monkeypatch.setenv("MCP_RATE_LIMIT_OTHER_PER_DAY", "50")
    monkeypatch.setenv("MCP_RATE_LIMIT_ANTHROPIC_PER_MINUTE", "not-a-number")
    monkeypatch.setenv("MCP_RATE_LIMIT_MAX_TRACKED_IPS", "-3")
    limits, max_tracked = guard.limits_from_env()
    assert limits["other"] == guard.Limits(per_minute=5, per_day=50)
    assert limits["anthropic"] == guard.Limits(
        per_minute=guard.ANTHROPIC_PER_MINUTE, per_day=guard.ANTHROPIC_PER_DAY,
    )
    assert max_tracked == guard.MAX_TRACKED_IPS


def test_default_limits():
    assert guard.ANTHROPIC_PER_MINUTE == 600
    assert guard.ANTHROPIC_PER_DAY == 0
    assert guard.OTHER_PER_MINUTE == 60
    assert guard.OTHER_PER_DAY == 1000


# ── message ──────────────────────────────────────────────────────────────────

def test_rate_limit_message():
    msg = guard.rate_limit_message(2.2)
    assert "rate limit" in msg.lower()
    assert "3 seconds" in msg
    assert "interactive research" in msg
    assert "bulk" in msg
    assert "about 3 hours" in guard.rate_limit_message(10_000)
    assert "about 5 minutes" in guard.rate_limit_message(290)


# ── middleware over HTTP ─────────────────────────────────────────────────────

SENTINEL = "Zyxwvut-Sentinel-Term-4242"


def _small_server(limiter):
    app_server = FastMCP("t")

    @app_server.tool
    def lookup(term: str) -> dict:
        return {"result_count": 3, "results": [term]}

    @app_server.tool
    def broken(term: str) -> dict:
        raise ValueError("boom")

    app_server.add_middleware(guard.ToolCallGuard(limiter))
    return app_server


def _post_calls(app_server, calls, headers=None, peer=("10.20.0.5", 4444)):
    """Runs JSON-RPC requests over the stateless HTTP app and returns the
    decoded results (one per call)."""
    import json

    async def _run():
        app = app_server.http_app(stateless_http=True)
        out = []
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app, client=peer)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as h:
                hdr = {
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                    **(headers or {}),
                }
                for i, (method, params) in enumerate(calls):
                    r = await h.post("/mcp", headers=hdr, json={
                        "jsonrpc": "2.0", "id": i + 1, "method": method, "params": params,
                    })
                    data = [ln[5:].strip() for ln in r.text.splitlines() if ln.startswith("data:")]
                    out.append(json.loads(data[0]) if data else r.json())
        return out

    return asyncio.run(_run())


def _call(name="lookup", term="x"):
    return ("tools/call", {"name": name, "arguments": {"term": term}})


def test_middleware_limits_tool_calls_per_client_ip():
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, o_min=2, o_day=100))
    results = _post_calls(app_server, [_call(), _call(), _call()],
                          headers={"cf-connecting-ip": "1.2.3.7"})
    assert results[0]["result"]["isError"] is False
    assert results[1]["result"]["isError"] is False
    blocked = results[2]["result"]
    assert blocked["isError"] is True
    text = blocked["content"][0]["text"]
    assert "rate limit" in text.lower()
    assert "interactive research" in text
    assert "1.2.3.7" not in text


def test_middleware_uses_anthropic_limits_for_anthropic_ips():
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, a_min=5, o_min=2))
    results = _post_calls(app_server, [_call()] * 5,
                          headers={"cf-connecting-ip": "160.79.104.20"})
    assert all(r["result"]["isError"] is False for r in results)


def test_middleware_does_not_limit_non_tool_requests():
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, o_min=1, o_day=1))
    init = ("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    })
    calls = [init, ("tools/list", {}), ("tools/list", {}), _call(), ("tools/list", {}), _call()]
    results = _post_calls(app_server, calls, headers={"cf-connecting-ip": "1.2.3.7"})
    assert "result" in results[0] and "tools" in results[1]["result"]
    assert "tools" in results[2]["result"] and "tools" in results[4]["result"]
    assert results[3]["result"]["isError"] is False
    assert results[5]["result"]["isError"] is True


def test_middleware_does_not_limit_a_private_peer_without_client_headers():
    # Behind a proxy with no client-IP header, the peer is the proxy (Render's
    # 10.x): limiting it would put every user in one bucket.
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, o_min=1, o_day=1))
    results = _post_calls(app_server, [_call(), _call(), _call()])
    assert all(r["result"]["isError"] is False for r in results)


def test_middleware_without_http_request_is_not_limited():
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, o_min=1, o_day=1))

    async def _run():
        async with Client(app_server) as client:
            return [await client.call_tool("lookup", {"term": "x"}) for _ in range(3)]

    assert all(not r.is_error for r in asyncio.run(_run()))


def test_middleware_fails_open_when_the_limiter_breaks(caplog):
    class Broken(guard.RateLimiter):
        def check(self, ip, cls):
            raise RuntimeError("limiter bug")

    clock = FakeClock()
    limiter = Broken(_limiter(clock).limits, max_tracked=10, clock=clock)
    app_server = _small_server(limiter)
    with caplog.at_level(logging.WARNING, logger="advisorfinder_mcp"):
        results = _post_calls(app_server, [_call()], headers={"cf-connecting-ip": "1.2.3.7"})
    assert results[0]["result"]["isError"] is False


# ── usage log ────────────────────────────────────────────────────────────────

def _usage_records(caplog):
    return [r for r in caplog.records if r.name == "advisorfinder_mcp.usage"]


def _all_text(records):
    parts = []
    for r in records:
        parts.append(r.getMessage())
        parts.append(repr(r.args))
    return "\n".join(parts)


def test_usage_log_one_line_per_tool_call_without_arguments_or_ip(caplog):
    clock = FakeClock()
    app_server = _small_server(_limiter(clock, o_min=2, o_day=100))
    calls = [("tools/list", {}), _call(term=SENTINEL), _call("broken", SENTINEL), _call(term=SENTINEL)]
    with caplog.at_level(logging.INFO, logger="advisorfinder_mcp.usage"):
        _post_calls(app_server, calls, headers={"cf-connecting-ip": "1.2.3.77"})
    records = _usage_records(caplog)
    assert len(records) == 3  # tools/list is not logged
    text = _all_text(records)
    assert SENTINEL not in text
    assert SENTINEL.lower() not in text.lower()
    assert "1.2.3.77" not in text
    ok, err, limited = (r.getMessage() for r in records)
    assert "tool=lookup" in ok and "outcome=ok" in ok and "result_count=3" in ok
    assert "class=other" in ok and "ip_header=cf-connecting-ip" in ok
    assert "duration_ms=" in ok
    assert "tool=broken" in err and "outcome=error" in err
    assert "outcome=rate_limited" in limited
    cid = guard.client_id("1.2.3.77")
    assert len(cid) == 10 and all(c in "0123456789abcdef" for c in cid)
    assert f"client={cid}" in ok


def test_client_id_is_stable_in_process_and_not_the_ip():
    assert guard.client_id("1.2.3.77") == guard.client_id("1.2.3.77")
    assert guard.client_id("1.2.3.77") != guard.client_id("1.2.3.78")
    assert guard.client_id("1.2.3.77") != "1.2.3.77"


def test_usage_log_unknown_tool_name_is_not_echoed(caplog):
    clock = FakeClock()
    app_server = _small_server(_limiter(clock))
    with caplog.at_level(logging.INFO, logger="advisorfinder_mcp.usage"):
        _post_calls(app_server, [("tools/call", {"name": "bad name\nINJECT", "arguments": {}})],
                    headers={"cf-connecting-ip": "1.2.3.7"})
    text = _all_text(_usage_records(caplog))
    assert "INJECT" not in text


def test_usage_log_records_x_forwarded_for_use(caplog):
    clock = FakeClock()
    app_server = _small_server(_limiter(clock))
    with caplog.at_level(logging.INFO, logger="advisorfinder_mcp"):
        _post_calls(app_server, [_call()], headers={"x-forwarded-for": "1.2.3.50, 10.0.0.1"})
    usage = [r.getMessage() for r in _usage_records(caplog)]
    assert "ip_header=x-forwarded-for" in usage[0]
    assert any("X-Forwarded-For" in r.getMessage() for r in caplog.records
               if r.name == "advisorfinder_mcp.guard")


# ── wiring into the real server ──────────────────────────────────────────────

def test_server_has_the_tool_call_guard():
    assert any(isinstance(m, guard.ToolCallGuard) for m in server.mcp.middleware)


def test_real_server_tool_call_is_logged_without_arguments(caplog):
    async def _run():
        async with Client(server.mcp) as client:
            return await client.call_tool("search_advisors", {"name": SENTINEL})

    with caplog.at_level(logging.INFO, logger="advisorfinder_mcp.usage"):
        result = asyncio.run(_run())
    assert not result.is_error
    records = _usage_records(caplog)
    assert len(records) == 1
    msg = records[0].getMessage()
    assert "tool=search_advisors" in msg and "outcome=ok" in msg and "result_count=0" in msg
    assert "ip_header=none" in msg
    assert SENTINEL not in _all_text(records)
