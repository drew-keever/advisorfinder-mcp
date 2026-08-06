"""Shared pytest setup for advisorfinder_mcp tests.

Points MCP_DB_PATH at the committed fixture DB (tests/fixtures/mcp_public.db,
built by the real export script — see tests/fixtures/README.md) BEFORE any test
module imports advisorfinder_mcp, then calls bootstrap.ensure_db() once per
session and asserts the fixture's schema version matches the package's.
"""
import os
from pathlib import Path

import pytest

FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public.db"
os.environ["MCP_DB_PATH"] = str(FIXTURE_DB)

import advisorfinder_mcp  # noqa: E402
from advisorfinder_mcp import bootstrap, db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _fixture_db_ready():
    bootstrap.ensure_db()
    meta = db.get_meta()
    assert int(meta["schema_version"]) == advisorfinder_mcp.SCHEMA_VERSION, (
        f"fixture schema_version={meta['schema_version']!r} != "
        f"advisorfinder_mcp.SCHEMA_VERSION={advisorfinder_mcp.SCHEMA_VERSION!r} "
        "— regenerate tests/fixtures/mcp_public.db (see tests/fixtures/README.md)"
    )
    yield
