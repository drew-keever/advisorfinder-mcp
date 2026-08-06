"""Tests for advisorfinder_mcp.bootstrap (stub) and advisorfinder_mcp.db.

conftest.py has already set MCP_DB_PATH to the fixture and called
bootstrap.ensure_db() once (session-scoped autouse fixture) by the time these
tests run, so db.DB_PATH is already pointed at the fixture DB.
"""
import sqlite3
from pathlib import Path

import pytest

from advisorfinder_mcp import bootstrap, db

FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public.db"


# ── bootstrap stub ───────────────────────────────────────────────────────────

def test_ensure_db_honors_mcp_db_path(monkeypatch):
    monkeypatch.setenv("MCP_DB_PATH", str(FIXTURE_DB))
    path = bootstrap.ensure_db()
    assert Path(path) == FIXTURE_DB
    assert db.DB_PATH == FIXTURE_DB


def test_ensure_db_missing_file_raises(monkeypatch):
    monkeypatch.setenv("MCP_DB_PATH", "/nonexistent/path/mcp_public.db")
    with pytest.raises(RuntimeError, match="R2 download not implemented"):
        bootstrap.ensure_db()


def test_ensure_db_no_env_var_raises(monkeypatch):
    monkeypatch.delenv("MCP_DB_PATH", raising=False)
    with pytest.raises(RuntimeError, match="R2 download not implemented"):
        bootstrap.ensure_db()


def test_ensure_db_restores_after_failed_attempts(monkeypatch):
    """A failed ensure_db() call must not corrupt the working DB_PATH for later tests."""
    monkeypatch.setenv("MCP_DB_PATH", "/nonexistent/path/mcp_public.db")
    with pytest.raises(RuntimeError):
        bootstrap.ensure_db()
    # restore real path and confirm it still works
    monkeypatch.setenv("MCP_DB_PATH", str(FIXTURE_DB))
    bootstrap.ensure_db()
    assert db.DB_PATH == FIXTURE_DB


# ── db.get_conn ───────────────────────────────────────────────────────────────

def test_get_conn_yields_row_factory_connection():
    with db.get_conn() as conn:
        assert conn.row_factory is sqlite3.Row
        row = conn.execute("SELECT 1 AS one").fetchone()
        assert row["one"] == 1


def test_get_conn_is_read_only():
    with db.get_conn() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE nope (x INTEGER)")


def test_get_conn_applies_cache_pragma():
    with db.get_conn() as conn:
        cache_size = conn.execute("PRAGMA cache_size").fetchone()[0]
        assert cache_size == -32000


def test_get_conn_applies_mmap_pragma():
    with db.get_conn() as conn:
        mmap_size = conn.execute("PRAGMA mmap_size").fetchone()[0]
        assert mmap_size > 0


def test_get_conn_closes_after_context():
    with db.get_conn() as conn:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


# ── schema version + meta ────────────────────────────────────────────────────

def test_assert_schema_version_passes_on_fixture():
    db.assert_schema_version()  # must not raise


def test_assert_schema_version_mismatch_raises(monkeypatch):
    monkeypatch.setattr(db, "_meta_cache", {"schema_version": "999"})
    with pytest.raises(RuntimeError, match="schema version"):
        db.assert_schema_version()


def test_get_meta_returns_export_meta_dict():
    meta = db.get_meta()
    assert meta["ia_reps_as_of"] == "2026-05-20"
    assert meta["firms_as_of"] == "2026-05-01"
    assert meta["advisors_count"] == "6"


def test_get_meta_is_cached():
    first = db.get_meta()
    second = db.get_meta()
    assert first is second


def test_set_db_path_clears_meta_cache():
    before = db.get_meta()
    assert before is db.get_meta()
    db.set_db_path(FIXTURE_DB)  # re-point at the same file
    after = db.get_meta()
    assert after is not before  # cache was invalidated, recomputed fresh
    assert after == before  # but the content is identical
