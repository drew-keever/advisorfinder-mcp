"""Every caller gets the fair-use notice: in each tool response, in the
server instructions sent at initialize, and in the rate-limit message.
Checked over a real MCP round trip, like test_tool_annotations.py."""
import asyncio

from fastmcp import Client

from advisorfinder_mcp import format, guard, server

NOTICE = (
    "This connector is for interactive research. Automated bulk extraction "
    "is not permitted. See https://advisorfinder.com/mcp."
)


def test_notice_text_is_exact():
    assert format.FAIR_USE_NOTICE == NOTICE


def test_every_tool_response_carries_the_notice():
    calls = [
        ("search_advisors", {"state": "TX"}),
        ("get_advisor", {"crd": "999999999"}),
        ("check_advisor", {"name_or_crd": "999999999"}),
        ("find_bookable_advisors", {}),
        ("search_firms", {"state": "TX"}),
        ("get_firm", {"crd": "999999999"}),
        ("get_database_stats", {}),
    ]

    async def _run():
        async with Client(server.mcp) as client:
            assert {t.name for t in await client.list_tools()} == {n for n, _ in calls}
            return [await client.call_tool(n, a) for n, a in calls]

    for (name, _), result in zip(calls, asyncio.run(_run())):
        assert result.structured_content["advisorfinder"]["fair_use"] == NOTICE, name


def test_server_instructions_are_the_notice():
    async def _run():
        async with Client(server.mcp) as client:
            return client.initialize_result.instructions

    assert asyncio.run(_run()) == NOTICE


def test_rate_limit_message_links_the_fair_use_page():
    msg = guard.rate_limit_message(30)
    assert "https://advisorfinder.com/mcp" in msg
    assert "retry in 30 seconds" in msg
    assert "retry in 1 second." in guard.rate_limit_message(0.4)
