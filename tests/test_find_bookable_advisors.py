"""Tests for the find_bookable_advisors tool -- the marketplace-only search
tool (Task 4, marketplace-layer). Calls the plain module-level function
directly, same pattern as the other tool test files.

Fixture marketplace members (tests/fixtures/make_fixture_marketplace.py):
  crd 1000002 (JOHN Q SMITH) -- RICH profile, city New York/NY, bio mentions
    "retirement", aum=45_000_000, disclosed_with_detail (3 disclosures, per
    make_fixture_source.py).
  crd 1000003 (MARY JONES) -- MINIMAL profile, city Boston/MA,
    disclosed_no_detail.
  crd 1000010 (REGGIE STATE) -- Gate A2 (2026-08-09) Ruling 1's crd-not-in-
    ia_reps case: a real, sitemap-listed marketplace member whose crd has NO
    row in make_fixture_source.py's ia_reps table (state-registered/BD-side
    advisor, legitimately absent from the SEC roster). sanitize_marketplace.py
    ships this row anyway (crd_mismatches is reported but no longer build-
    fatal) -- db.get_advisor("1000010") genuinely returns None, exercising
    server._regulatory_join's real (not defensive) None branch.
crd 1000001 (JANE SMITH) and crd 1000005 (PATRICK MCDONALD III) are NOT
marketplace members (1000005 is excluded by the sitemap-scoping gate even
though it was in the raw marketplace xlsx) -- used to prove ranking/absence
behavior elsewhere in this suite.
"""
from pathlib import Path

from advisorfinder_mcp import db, format, server

NO_MARKETPLACE_FIXTURE_DB = Path(__file__).parent / "fixtures" / "mcp_public_no_marketplace.db"


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_find_bookable_returns_members_with_deep_links():
    result = server.find_bookable_advisors()
    _envelope_keys_present(result)
    # `available: False` is the failure-path-only marker (see the graceful-
    # absence test at the bottom of this file) -- on the ordinary path it
    # must be absent (or truthy), never silently present-and-False, so a
    # future refactor that flips the gate can't silently degrade every
    # ordinary call to "not available" without a test catching it.
    assert result.get("available") is not False
    by_crd = {r["crd"]: r for r in result["results"]}
    assert "1000002" in by_crd
    rich = by_crd["1000002"]
    assert rich["profile_url"] == "https://advisorfinder.com/app/advisor-profile/qv3Y1g3y/john-q-smith"
    # regulatory disclosure status joined per result, same four-state contract
    # as check_advisor/get_advisor (1000002 has disclosure_count=3 in the
    # fixture source -> disclosed_with_detail).
    assert rich["disclosure"]["status"] == "disclosed_with_detail"
    assert rich["disclosure"]["disclosure_count"] == 3
    assert rich["registration"]["active"] is True


def test_find_bookable_specialty_filter():
    # "retirement" only appears in 1000002's bio -- same substring scan
    # db.search_marketplace() already exercises directly.
    result = server.find_bookable_advisors(specialty="retirement")
    crds = {r["crd"] for r in result["results"]}
    assert crds == {"1000002"}


def test_find_bookable_city_state_filters():
    result = server.find_bookable_advisors(city="Boston", state="MA")
    crds = {r["crd"] for r in result["results"]}
    assert crds == {"1000003"}


def test_find_bookable_no_filters_returns_results():
    # Browse mode is fine here -- unlike search_advisors, this tool's scope is
    # already narrow (AdvisorFinder members only), disclosed in the docstring.
    result = server.find_bookable_advisors()
    assert result["result_count"] >= 2
    crds = {r["crd"] for r in result["results"]}
    assert {"1000002", "1000003"}.issubset(crds)


def test_find_bookable_aum_labeled_self_reported():
    result = server.find_bookable_advisors()
    rich = next(r for r in result["results"] if r["crd"] == "1000002")
    assert rich["self_reported"]["aum"] == 45_000_000
    assert rich["self_reported"]["label"] == "as listed on their AdvisorFinder profile"


def test_find_bookable_minimal_profile_has_nulls_not_crash():
    result = server.find_bookable_advisors()
    minimal = next(r for r in result["results"] if r["crd"] == "1000003")
    assert minimal["bio"] is None
    assert minimal["self_reported"]["aum"] is None
    assert minimal["disclosure"]["status"] == "disclosed_no_detail"


# ── Gate A2 (2026-08-09) Ruling 1: crd-not-in-ia_reps ships, labeled ─────────

def test_find_bookable_unmatched_crd_gets_labeled_regulatory_block():
    """crd 1000010 ("Reggie State") has no row in ia_reps -- a real,
    reachable path per Gate A2 Ruling 1, not a crash or a silent 'unknown'.
    The disclosure block must carry EXACTLY the adjudicated note plus the
    standard IAPD/BrokerCheck verify links for that crd; registration must be
    unknown (None/[]), never fabricated."""
    result = server.find_bookable_advisors()
    unmatched = next(r for r in result["results"] if r["crd"] == "1000010")

    assert unmatched["disclosure"]["status"] == "not_in_regulatory_dataset"
    # Two independent checks, deliberately: the first pins the wiring (server
    # actually uses format's constant, not a hand-copied string that could
    # drift); the second pins the COPY itself, whitespace-normalized so an
    # incidental line-wrap on either side can't mask a real mismatch (same
    # pattern as test_find_bookable_note_copy_is_exact below).
    assert unmatched["disclosure"]["note"] == format._MARKETPLACE_CRD_UNMATCHED_NOTE
    assert " ".join(unmatched["disclosure"]["note"].split()) == " ".join(
        "Regulatory records for this advisor aren't in our SEC dataset "
        "(likely state-registered) — verify on FINRA BrokerCheck / SEC IAPD.".split()
    )
    assert unmatched["disclosure"]["verify"] == {
        "iapd": format.iapd_individual_url("1000010"),
        "brokercheck": format.brokercheck_individual_url("1000010"),
    }
    assert unmatched["registration"]["active"] is None
    assert unmatched["registration"]["registered_states"] == []
    # Still a fully-formed marketplace listing -- no crash, envelope unchanged.
    assert unmatched["name"] == "Reggie State"
    assert unmatched["profile_url"] == (
        "https://advisorfinder.com/app/advisor-profile/qv3Y4u6v/reggie-state"
    )


def test_find_bookable_matched_members_disclosure_unaffected_by_ruling_1():
    """The crd-not-in-ia_reps labeled block is scoped to the member it
    applies to -- matched members (1000002, 1000003) keep the ordinary
    four-state disclosure_status() shape, never the fallback block."""
    result = server.find_bookable_advisors()
    by_crd = {r["crd"]: r for r in result["results"]}
    for crd, expected_status in (("1000002", "disclosed_with_detail"), ("1000003", "disclosed_no_detail")):
        assert by_crd[crd]["disclosure"]["status"] == expected_status
        assert "verify" not in by_crd[crd]["disclosure"]
        assert "note" not in by_crd[crd]["disclosure"]


def test_find_bookable_note_copy_is_exact():
    # Verbatim requirement (task-4-brief.md) -- whitespace-normalize both
    # sides so an incidental line-wrap in either the source or this literal
    # can't mask a real copy mismatch (see the earlier line-wrap bug this
    # exact pattern was added to fix, in the resources-paragraph test).
    result = server.find_bookable_advisors()
    expected = " ".join(
        "This advisor is listed on AdvisorFinder — view their full "
        "profile and contact them.".split()
    )
    for row in result["results"]:
        assert " ".join(row["note"].split()) == expected


def test_find_bookable_docstring_discloses_marketplace_only_scope():
    doc = server.find_bookable_advisors.__doc__ or ""
    assert "search_advisors" in doc
    assert "AdvisorFinder" in doc
    assert "SEC roster" in doc


def test_find_bookable_limit_clamped_high(monkeypatch):
    captured = {}

    def fake_search_marketplace(**kwargs):
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(server.db, "search_marketplace", fake_search_marketplace)
    server.find_bookable_advisors(limit=999)
    assert captured["limit"] == 50


def test_find_bookable_limit_clamped_low(monkeypatch):
    captured = {}

    def fake_search_marketplace(**kwargs):
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(server.db, "search_marketplace", fake_search_marketplace)
    server.find_bookable_advisors(limit=0)
    assert captured["limit"] == 1


def test_find_bookable_no_match_has_guidance():
    result = server.find_bookable_advisors(city="Nowhere", state="ZZ")
    assert result["result_count"] == 0
    assert "not_found_guidance" in result


def test_find_bookable_zero_members_build_is_not_same_as_table_absent(monkeypatch):
    # A --marketplace build with zero sitemap-matched members is a genuinely
    # different case from marketplace_advisors being absent entirely: table
    # PRESENT (db.marketplace_stats() returns a real dict, count=0) vs table
    # ABSENT (db.marketplace_stats() returns None, per its own docstring).
    # The former must still get ordinary "no results" guidance, never the
    # "not available in this deployment" message -- only genuine table
    # absence should trip that branch.
    monkeypatch.setattr(server.db, "marketplace_stats", lambda: {"count": 0, "snapshot_date": "2026-01-01"})
    monkeypatch.setattr(server.db, "search_marketplace", lambda **kwargs: [])
    result = server.find_bookable_advisors()
    assert result.get("available") is not False
    assert result["result_count"] == 0
    assert "not_found_guidance" in result


# ── graceful behavior when marketplace_advisors is absent entirely ──────────

def test_find_bookable_advisors_graceful_when_marketplace_absent():
    assert NO_MARKETPLACE_FIXTURE_DB.exists(), (
        f"missing committed fixture {NO_MARKETPLACE_FIXTURE_DB} -- see "
        "tests/fixtures/README.md for the regeneration command"
    )
    # db.set_db_path() clears the module's _meta_cache as a side effect, so
    # repointing here and restoring in `finally` can't leak stale export_meta
    # into whichever test runs next -- same pattern as
    # test_bootstrap_and_db.py::test_marketplace_functions_are_graceful_when_table_absent.
    original_path = db.DB_PATH
    try:
        db.set_db_path(NO_MARKETPLACE_FIXTURE_DB)
        result = server.find_bookable_advisors()
        _envelope_keys_present(result)
        assert result["available"] is False
        assert "not available" in result["message"].lower()
        assert result["results"] == []
        assert result["result_count"] == 0
    finally:
        db.set_db_path(original_path)
