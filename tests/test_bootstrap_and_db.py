"""Tests for advisorfinder_mcp.bootstrap's MCP_DB_PATH short-circuit and
advisorfinder_mcp.db. See test_bootstrap_r2.py for the R2-download branch
(fake S3 client, no network).

conftest.py has already set MCP_DB_PATH to the fixture and called
bootstrap.ensure_db() once (session-scoped autouse fixture) by the time these
tests run, so db.DB_PATH is already pointed at the fixture DB.
"""
import sqlite3
from pathlib import Path

import pytest

from advisorfinder_mcp import bootstrap, db

FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public.db"


# ── bootstrap: MCP_DB_PATH short-circuit ─────────────────────────────────────

def test_ensure_db_honors_mcp_db_path(monkeypatch):
    monkeypatch.setenv("MCP_DB_PATH", str(FIXTURE_DB))
    path = bootstrap.ensure_db()
    assert Path(path) == FIXTURE_DB
    assert db.DB_PATH == FIXTURE_DB


def test_ensure_db_missing_file_falls_through_to_r2_and_raises(monkeypatch):
    """MCP_DB_PATH pointing at a nonexistent file does NOT short-circuit —
    ensure_db() falls through to the R2 branch, which raises because none of
    the R2_* env vars are set in this test."""
    monkeypatch.setenv("MCP_DB_PATH", "/nonexistent/path/mcp_public.db")
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        bootstrap.ensure_db()


def test_ensure_db_no_env_var_raises(monkeypatch):
    monkeypatch.delenv("MCP_DB_PATH", raising=False)
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        bootstrap.ensure_db()


def test_ensure_db_restores_after_failed_attempts(monkeypatch):
    """A failed ensure_db() call must not corrupt the working DB_PATH for later tests."""
    monkeypatch.setenv("MCP_DB_PATH", "/nonexistent/path/mcp_public.db")
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
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
    assert meta["advisors_count"] == "8"


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


# ── disclosure_tally: recomputed directly, decoupled from export_meta's
#    stale disclosure_tally_* fields (which encode the OLD, now-superseded
#    four-state contract from the export script — see task-2-report.md's
#    post-review-fix section) ──────────────────────────────────────────────

def test_disclosure_tally_matches_corrected_four_state_contract():
    tally = db.disclosure_tally()
    # Fixture (8 advisors): 1000001/1000005/1000006/1000008 -> none_reported;
    # 1000002 -> disclosed_with_detail; 1000003/1000004 -> disclosed_no_detail;
    # 1000007 -> unknown.
    assert tally["none_reported"] == 4
    assert tally["disclosed_no_detail"] == 2
    assert tally["disclosed_with_detail"] == 1
    assert tally["unknown"] == 1
    assert sum(tally.values()) == 8
