"""AdvisorFinder MCP server package."""

__version__ = "2.0.0"

# Bump whenever the on-disk mcp_public.db export schema changes shape in a way
# that this server's queries depend on. db.assert_schema_version() compares this
# against the fixture/production DB's export_meta.schema_version at startup.
SCHEMA_VERSION = 2
