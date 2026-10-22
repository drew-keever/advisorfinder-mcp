"""The marketplace pause switch (MCP_MARKETPLACE_ENABLED).

Marketplace data is served ONLY when the variable is exactly "true"
(case-insensitive, whitespace-trimmed). Absent or any other value means
paused: every tool behaves as if the export had no marketplace data, and
find_bookable_advisors says the listings are temporarily not available.
The regulatory data is unaffected.

conftest.py turns the switch ON for the rest of the suite; every test here
that checks the paused default removes the variable first. The fixture DB
HAS marketplace rows (members 1000002, 1000003, 1000010), so a leak would
show up here.
"""
import json

import pytest

from advisorfinder_mcp import db, resources, server

ENV = "MCP_MARKETPLACE_ENABLED"

_MARKETPLACE_KEYS = {
    "advisorfinder_listing",
    "self_reported",
    "designations_coverage_note",
    "credential_sources_note",
    "profile_url",
    "member_count",
    "snapshot_date",
}


@pytest.fixture
def paused(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def _all_tool_outputs() -> dict:
    """One or more calls of each of the seven tools, aimed at marketplace
    members where the tool takes a subject."""
    return {
        "search_advisors(name=smith)": server.search_advisors(name="smith"),
        "search_advisors(state=NY)": server.search_advisors(state="NY"),
        "search_advisors(state=MA)": server.search_advisors(state="MA"),
        "get_advisor(1000002)": server.get_advisor(crd="1000002"),
        "get_advisor(1000003)": server.get_advisor(crd="1000003"),
        "check_advisor(1000002)": server.check_advisor(name_or_crd="1000002"),
        "check_advisor(1000003)": server.check_advisor(name_or_crd="1000003"),
        "find_bookable_advisors()": server.find_bookable_advisors(),
        "find_bookable_advisors(state=NY)": server.find_bookable_advisors(state="NY"),
        "search_firms(state=NY)": server.search_firms(state="NY"),
        "get_firm(100001)": server.get_firm(crd="100001"),
        "get_database_stats()": server.get_database_stats(),
    }


def _keys(obj) -> set:
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            found |= _keys(v)
    elif isinstance(obj, list):
        for item in obj:
            found |= _keys(item)
    return found


# ── the switch ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["true", "TRUE", "True", "TRUE ", "  true\n"])
def test_switch_on_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert db.marketplace_enabled() is True


@pytest.mark.parametrize("value", ["1", "yes", "", "on", "false", "t", "truee", "y"])
def test_switch_off_values(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert db.marketplace_enabled() is False


def test_switch_absent_is_paused(paused):
    assert db.marketplace_enabled() is False


def test_switch_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv(ENV, "true")
    assert db.marketplace_enabled() is True
    monkeypatch.setenv(ENV, "no")
    assert db.marketplace_enabled() is False


# ── db layer: no marketplace row is read when paused ─────────────────────────

def test_db_readers_return_nothing_when_paused(paused):
    assert db.get_marketplace_by_crd("1000002") is None
    assert db.search_marketplace() == []
    assert db.search_marketplace_with_total() == ([], 0)
    assert db.marketplace_stats() is None


def test_db_readers_never_query_the_table_when_paused(paused, monkeypatch):
    def _boom():
        raise AssertionError("a marketplace reader opened the DB while paused")

    monkeypatch.setattr(db, "get_conn", _boom)
    assert db.get_marketplace_by_crd("1000002") is None
    assert db.search_marketplace_with_total(state="NY") == ([], 0)
    assert db.marketplace_stats() is None


def test_db_readers_return_rows_when_enabled():
    assert db.get_marketplace_by_crd("1000002") is not None
    assert db.marketplace_stats() is not None


# ── every tool, paused: no marketplace-derived field ─────────────────────────

def test_control_enabled_outputs_do_carry_marketplace_data():
    # Proves the paused sweep below is sensitive: the same calls, with the
    # switch ON, do produce marketplace fields.
    outputs = _all_tool_outputs()
    blob = json.dumps(outputs)
    assert "advisorfinder_listing" in blob
    assert "advisorfinder.com/app/advisor-profile" in blob
    assert outputs["get_advisor(1000002)"]["advisorfinder"]["link"] != "https://advisorfinder.com"


def test_no_tool_output_contains_marketplace_data_when_paused(paused):
    outputs = _all_tool_outputs()
    for label, result in outputs.items():
        leaked = _keys(result) & _MARKETPLACE_KEYS
        assert not leaked, f"{label} leaked marketplace keys {leaked}"
        blob = json.dumps(result)
        assert "advisor-profile" not in blob, label
        assert "advisorfinder_listing" not in blob, label
        assert result["advisorfinder"]["link"] == "https://advisorfinder.com", label


def test_regulatory_data_unchanged_when_paused(monkeypatch):
    # The regulatory part of each advisor tool is identical paused vs ON.
    def _regulatory(result):
        return {
            k: v for k, v in result.items()
            if k not in _MARKETPLACE_KEYS and k != "advisorfinder"
        }

    monkeypatch.setenv(ENV, "true")
    on_get = _regulatory(server.get_advisor(crd="1000002"))
    on_check = _regulatory(server.check_advisor(name_or_crd="1000002"))
    on_search = server.search_advisors(name="smith")
    monkeypatch.delenv(ENV)
    assert _regulatory(server.get_advisor(crd="1000002")) == on_get
    assert _regulatory(server.check_advisor(name_or_crd="1000002")) == on_check
    off_search = server.search_advisors(name="smith")
    assert [r["crd"] for r in off_search["results"]] == [r["crd"] for r in on_search["results"]]
    for off_r, on_r in zip(off_search["results"], on_search["results"]):
        on_r = {k: v for k, v in on_r.items() if k != "advisorfinder_listing"}
        assert off_r == on_r


# ── find_bookable_advisors, paused ───────────────────────────────────────────

def test_find_bookable_paused_payload(paused):
    for result in (
        server.find_bookable_advisors(),
        server.find_bookable_advisors(specialty="retirement", city="New York", state="NY"),
    ):
        assert result["available"] is False
        assert result["result_count"] == 0
        assert result["results"] == []
        assert "total_matches" not in result
        message = result["message"]
        assert "temporarily not available" in message
        assert "https://advisorfinder.com" in message
        lower = message.lower()
        assert "legal" not in lower and "review" not in lower and "counsel" not in lower


def test_find_bookable_description_says_it_can_be_unavailable():
    doc = " ".join((server.find_bookable_advisors.__doc__ or "").split())
    assert "temporarily unavailable" in doc
    assert "`available: false`" in doc


def test_find_bookable_still_listed_and_read_only():
    import asyncio

    from fastmcp import Client

    async def _run():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_run())}
    assert "find_bookable_advisors" in tools
    assert tools["find_bookable_advisors"].annotations.readOnlyHint is True


# ── get_database_stats, paused ───────────────────────────────────────────────

def test_stats_marketplace_block_paused(paused):
    result = server.get_database_stats()
    assert result["marketplace"] == {"status": "paused"}
    # Regulatory counts still present.
    assert result["advisors_count"] > 0
    assert result["firms_count"] > 0


def test_stats_marketplace_block_enabled_unchanged():
    result = server.get_database_stats()
    assert result["marketplace"]["member_count"] == 3
    assert "status" not in result["marketplace"]


# ── resources, paused ────────────────────────────────────────────────────────

_LISTINGS_SENTENCE = (
    "Some advisors are listed on AdvisorFinder's marketplace. Their listings "
    "add self-provided profile information and a link to contact them."
)


def test_coverage_resource_paused_note(paused):
    text = " ".join(resources.coverage_and_limitations().split())
    assert "temporarily not available" in text
    assert "https://advisorfinder.com" in text
    assert _LISTINGS_SENTENCE not in text


def test_coverage_resource_enabled_has_no_paused_note():
    text = " ".join(resources.coverage_and_limitations().split())
    assert _LISTINGS_SENTENCE in text
    assert "temporarily not available" not in text
