"""Directory policy: tool descriptions contain no instructions about other
tools, so no description names another tool. Checked over a real MCP
`tools/list` round trip, the same way as test_tool_annotations.py."""
import asyncio

from fastmcp import Client

from advisorfinder_mcp import server


def _list_tools_over_mcp():
    async def _run():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    return asyncio.run(_run())


def test_no_tool_description_names_another_tool():
    tools = _list_tools_over_mcp()
    assert len(tools) == 7
    names = [t.name for t in tools]
    for tool in tools:
        description = tool.description or ""
        others = [n for n in names if n != tool.name and n in description]
        assert others == [], f"{tool.name} description names {others}"
