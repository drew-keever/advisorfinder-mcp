"""AdvisorFinder MCP server package."""

__version__ = "2.1.0"

# Bump whenever the on-disk mcp_public.db export schema changes shape in a way
# that this server's queries depend on. db.assert_schema_version() compares this
# against the fixture/production DB's export_meta.schema_version at startup.
#
# v3 (Task 3, marketplace-layer): adds the OPTIONAL marketplace_advisors table
# (present only when the export was built with --marketplace) plus the
# unconditional export_meta.marketplace_count / marketplace_snapshot_date keys
# (written on every v3 build, "0"/None when --marketplace was omitted). schema_version
# is bumped to 3 regardless of whether a given export actually included marketplace
# data — see build_mcp_public_db.py's write_export_meta()/write_manifest() docstrings
# in the firm-intelligence repo: v3 is the server CONTRACT, not a data-presence flag.
SCHEMA_VERSION = 3
