"""Every tool must declare a human title and MCP ToolAnnotations (the Claude
connector directory flags tools with "No annotations declared").

These assertions go through a real MCP `tools/list` round trip — an
in-process fastmcp Client connected to the actual `server.mcp` instance — so
they check what MCP clients actually receive on the wire, not just the
decorator arguments.
"""
import asyncio
import inspect

from fastmcp import Client

from advisorfinder_mcp import server

EXPECTED_TITLES = {
    "search_advisors": "Search advisors",
    "get_advisor": "Get advisor profile",
    "check_advisor": "Check an advisor",
    "find_bookable_advisors": "Find bookable advisors",
    "search_firms": "Search firms",
    "get_firm": "Get firm profile",
    "get_database_stats": "Get database stats",
}


def _list_tools_over_mcp():
    async def _run():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    return asyncio.run(_run())


def test_tools_list_exposes_titles_and_read_only_annotations():
    tools = _list_tools_over_mcp()
    assert len(tools) == 7
    assert {t.name for t in tools} == set(EXPECTED_TITLES)

    for tool in tools:
        expected = EXPECTED_TITLES[tool.name]
        assert tool.title == expected, tool.name
        ann = tool.annotations
        assert ann is not None, f"{tool.name}: no annotations declared"
        assert ann.title == expected, tool.name
        assert ann.readOnlyHint is True, tool.name
        assert ann.destructiveHint is False, tool.name
        assert ann.idempotentHint is True, tool.name
        assert ann.openWorldHint is False, tool.name


def test_tools_list_wire_json_carries_annotation_keys():
    # Serialize exactly as the protocol layer does (by_alias, exclude_none)
    # so a hint left at its default None would show up as a missing key.
    tools = _list_tools_over_mcp()
    for tool in tools:
        wire = tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert wire["title"] == EXPECTED_TITLES[tool.name]
        assert wire["annotations"] == {
            "title": EXPECTED_TITLES[tool.name],
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


def test_parametrized_decorator_still_returns_plain_function():
    # Other tests call tool functions directly; the parametrized
    # @mcp.tool(title=..., annotations=...) form must not wrap them.
    for name in EXPECTED_TITLES:
        fn = getattr(server, name)
        assert inspect.isfunction(fn), name
        assert fn.__module__ == server.__name__, name
