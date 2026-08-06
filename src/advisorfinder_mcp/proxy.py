"""stdio->remote proxy: the actual entry point installed by
`pip install advisorfinder-mcp` (see [project.scripts] in pyproject.toml).

This module is deliberately dependency-light — it imports only fastmcp, never
`.db`, `.bootstrap`, or `.server` — because the PyPI install has neither
boto3 nor a local mcp_public.db; it's a thin stdio<->HTTP bridge to the
hosted server at ADVISORFINDER_MCP_URL (default: the production remote).
"""
import os

from fastmcp import FastMCP

DEFAULT_URL = "https://mcp.advisorfinder.com/mcp"


def main() -> None:
    url = os.environ.get("ADVISORFINDER_MCP_URL", DEFAULT_URL)
    FastMCP.as_proxy(url, name="advisorfinder").run()  # stdio transport by default
