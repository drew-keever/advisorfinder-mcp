"""Tests for advisorfinder_mcp.bootstrap's MCP_DB_PATH short-circuit and
advisorfinder_mcp.db. See test_bootstrap_r2.py for the R2-download branch
(fake S3 client, no network).

conftest.py has already set MCP_DB_PATH to the fixture and called
bootstrap.ensure_db() once (session-scoped autouse fixture) by the time these
tests run, so db.DB_PATH is already pointed at the fixture DB.
"""
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from advisorfinder_mcp import bootstrap, db

FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public.db"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Committed second fixture: schema_version=3, built by the SAME real
# build_mcp_public_db.py script but WITHOUT --marketplace -- i.e. a genuine
# v3 export that simply never had marketplace_advisors created. Regenerated
# by the command documented in tests/fixtures/README.md. Unlike FIXTURE_DB,
# this one is committed specifically so the graceful-absence contract below
# is enforced unconditionally (no other-worktree dependency, no skip, works
# in CI and on any machine) -- see the Important review finding this fixture
# was added to close.
NO_MARKETPLACE_FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public_no_marketplace.db"

# The other repo's worktree that owns the REAL export scripts (Tasks 1-2 of the
# marketplace-layer plan) -- see tests/fixtures/README.md for the full
# regeneration commands. Only test_marketplace_functions_*_when_table_absent
# below shells out to it (to build a genuinely v3-but-no---marketplace DB
# variant); every other marketplace test reads the already-committed
# tests/fixtures/mcp_public.db.
_OTHER_WORKTREE = Path(
    "/Users/lv/projects/advisorfinder/firm-intelligence-worktrees/marketplace"
)
_OTHER_VENV_PYTHON = _OTHER_WORKTREE / ".venv" / "bin" / "python"


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
    assert meta["advisors_count"] == "9"


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


# ── disclosure_tally: recomputed directly here as deliberate decoupling from
#    export_meta's disclosure_tally_* fields — not because those fields are
#    wrong (the export script now implements this exact same contract and its
#    tally matches value-for-value), but so this server's aggregate can never
#    silently drift from format.disclosure_status()'s per-advisor bucketing
#    even if a future export-script change stops matching it ─────────────────

def test_disclosure_tally_matches_corrected_four_state_contract():
    tally = db.disclosure_tally()
    # Fixture (9 advisors): 1000001/1000005/1000006/1000008/1000009 ->
    # none_reported; 1000002 -> disclosed_with_detail; 1000003/1000004 ->
    # disclosed_no_detail; 1000007 -> unknown.
    assert tally["none_reported"] == 5
    assert tally["disclosed_no_detail"] == 2
    assert tally["disclosed_with_detail"] == 1
    assert tally["unknown"] == 1
    assert sum(tally.values()) == 9


# ── marketplace: db.get_marketplace_by_crd / search_marketplace /
#    marketplace_stats. Fixture has 2 marketplace members (see
#    tests/fixtures/make_fixture_marketplace.py):
#      crd 1000002 (professionalId "qv3Y1g3y", short-format) -- RICH: prompts,
#        pricing (via pricingV2 fallback), aum, education, all fields populated.
#      crd 1000003 (professionalId a UUID) -- MINIMAL: only
#        displayName/companyName/city/state/memberSince populated.
#    crd 1000005 was in the marketplace xlsx but NOT in the sitemap -> proves
#    scoping (never reaches marketplace_advisors at all). ───────────────────────

def test_get_marketplace_by_crd_returns_row_for_rich_member():
    row = db.get_marketplace_by_crd("1000002")
    assert row is not None
    assert row["professionalId"] == "qv3Y1g3y"
    assert row["displayName"] == "John Q. Smith"
    assert row["profile_url"] == "https://advisorfinder.com/app/advisor-profile/qv3Y1g3y/john-q-smith"
    assert row["aum"] == 45_000_000
    assert row["education"] == "MBA, NYU Stern School of Business"
    # pricing falls back to pricingV2 when the raw `pricing` cell is empty (per
    # sanitize_marketplace._project_row) -- the raw xlsx's pricingV2 value.
    assert row["pricing"] == "$3,000/yr flat planning fee, or $250/hr for one-off consultations"
    assert json.loads(row["in_their_own_words"]) == [
        "I love helping clients retire early and confidently.",
        "My biggest professional achievement is growing my practice threefold in five years.",
    ]


def test_get_marketplace_by_crd_returns_row_for_minimal_member():
    row = db.get_marketplace_by_crd("1000003")
    assert row is not None
    assert row["displayName"] == "Mary Jones"
    assert row["city"] == "Boston"
    assert row["state"] == "MA"
    assert row["bio"] is None
    assert row["aum"] is None
    assert json.loads(row["in_their_own_words"]) == []


def test_get_marketplace_by_crd_returns_none_for_advisor_not_in_marketplace_at_all():
    # 1000001 (JANE SMITH) never appeared in the fixture marketplace xlsx.
    assert db.get_marketplace_by_crd("1000001") is None


def test_get_marketplace_by_crd_returns_none_for_advisor_excluded_by_sitemap_scoping():
    # 1000005 (PATRICK MCDONALD III) is in the fixture marketplace xlsx but its
    # professionalId ("excludedAdv1") does not appear in
    # tests/fixtures/marketplace_sitemap.xml -- the sitemap-scoping gate drops it.
    assert db.get_marketplace_by_crd("1000005") is None


def test_search_marketplace_by_state_is_case_insensitive():
    rows = db.search_marketplace(state="ny", limit=20)
    crds = {r["crd"] for r in rows}
    assert "1000002" in crds
    assert "1000003" not in crds  # MA, not NY


def test_search_marketplace_by_city():
    rows = db.search_marketplace(city="Boston", limit=20)
    crds = {r["crd"] for r in rows}
    assert crds == {"1000003"}


def test_search_marketplace_by_specialty_substring_across_bio_and_client_description():
    # "retirement" only appears in 1000002's bio ("...build durable retirement
    # plans.") -- proves the LIKE scan covers bio, not just clientDescription.
    rows = db.search_marketplace(specialty="retirement", limit=20)
    crds = {r["crd"] for r in rows}
    assert crds == {"1000002"}


def test_search_marketplace_specialty_is_case_insensitive_substring():
    rows = db.search_marketplace(specialty="RETIREMENT", limit=20)
    assert any(r["crd"] == "1000002" for r in rows)


def test_search_marketplace_no_filters_returns_all_members_up_to_limit():
    # 3 members as of the Gate A2 (2026-08-09) fixture regen: 1000002/1000003
    # (both cross-check against ia_reps) plus 1000010 ("Reggie State" — Gate
    # A2 Ruling 1's crd-not-in-ia_reps case, which ships flagged rather than
    # failing the build; see make_fixture_marketplace.py's docstring).
    rows = db.search_marketplace(limit=20)
    assert {r["crd"] for r in rows} == {"1000002", "1000003", "1000010"}


def test_search_marketplace_limit_is_honored():
    rows = db.search_marketplace(limit=1)
    assert len(rows) == 1


def test_marketplace_stats_count_and_snapshot_date():
    stats = db.marketplace_stats()
    assert stats is not None
    # 3, not 2, as of the Gate A2 fixture regen (see comment above).
    assert stats["count"] == 3
    # snapshot_date is derived from the fixture marketplace xlsx's mtime at
    # regeneration time (see tests/fixtures/README.md) -- not hardcoded here
    # since it legitimately shifts on every regen. date.fromisoformat() pins
    # the declared str/ISO-date contract without pinning the mtime-derived value.
    assert date.fromisoformat(stats["snapshot_date"])


# ── marketplace: graceful None/empty when marketplace_advisors is absent
#    (a genuinely v2-shaped-in-substance v3 build -- schema_version=3 but the
#    build ran WITHOUT --marketplace, so the table itself never exists).
#
#    This is the load-bearing, UNCONDITIONAL test for that contract: it reads
#    the committed tests/fixtures/mcp_public_no_marketplace.db fixture (built
#    once by the real build_mcp_public_db.py, no --marketplace flag -- see
#    tests/fixtures/README.md for the exact command) and runs with no path
#    gate, no skip, on any machine including CI. See
#    test_marketplace_functions_graceful_absence_provenance_via_real_build_script
#    below for the separate (skippable) test that proves this fixture's
#    shape by rebuilding an equivalent one live via the real script. ─────────

def test_marketplace_functions_are_graceful_when_table_absent():
    assert NO_MARKETPLACE_FIXTURE_DB.exists(), (
        f"missing committed fixture {NO_MARKETPLACE_FIXTURE_DB} -- see "
        "tests/fixtures/README.md for the regeneration command"
    )

    # db.set_db_path() clears the module's _meta_cache as a side effect (see
    # db.py), so repointing here and restoring in `finally` can't leak stale
    # export_meta into whichever test runs next -- same pattern
    # test_set_db_path_clears_meta_cache above exercises directly.
    original_path = db.DB_PATH
    try:
        db.set_db_path(NO_MARKETPLACE_FIXTURE_DB)
        assert db.get_marketplace_by_crd("1000002") is None
        assert db.search_marketplace(state="NY", limit=20) == []
        assert db.marketplace_stats() is None
    finally:
        db.set_db_path(original_path)


# ── marketplace: provenance test for the fixture above. Rebuilds an
#    equivalent no---marketplace DB LIVE via the real script from the sibling
#    firm-intelligence worktree, to prove the committed
#    mcp_public_no_marketplace.db fixture's shape actually matches what that
#    real script produces (not just a hand-maintained stand-in). This one MAY
#    skip when the sibling worktree/venv is absent (e.g. after that worktree
#    is merged and removed) -- that's fine here specifically, because the
#    contract itself is already enforced unconditionally by the test above;
#    this one only re-validates provenance, not the contract. ────────────────

def test_marketplace_functions_graceful_absence_provenance_via_real_build_script(tmp_path):
    if not _OTHER_VENV_PYTHON.exists():
        pytest.skip(
            f"other worktree venv not found at {_OTHER_VENV_PYTHON} -- "
            "cannot re-derive the no---marketplace fixture from the real build script"
        )

    source_db = tmp_path / "fixture_source.db"
    subprocess.run(
        [sys.executable, str(FIXTURES_DIR / "make_fixture_source.py"), str(source_db)],
        check=True,
    )
    out_dir = tmp_path / "out_no_marketplace"
    subprocess.run(
        [
            str(_OTHER_VENV_PYTHON),
            str(_OTHER_WORKTREE / "scripts" / "build_mcp_public_db.py"),
            "--source", str(source_db),
            "--out", str(out_dir),
        ],
        check=True,
        cwd=str(_OTHER_WORKTREE),
    )
    no_marketplace_db = out_dir / "mcp_public.db"
    assert no_marketplace_db.exists()

    original_path = db.DB_PATH
    try:
        db.set_db_path(no_marketplace_db)
        assert db.get_marketplace_by_crd("1000002") is None
        assert db.search_marketplace(state="NY", limit=20) == []
        assert db.marketplace_stats() is None
    finally:
        db.set_db_path(original_path)
