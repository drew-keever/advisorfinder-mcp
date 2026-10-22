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
    # `verify` is present only when there are links to give (format.envelope).
    assert result.get("verify", True) != {}
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
    assert unmatched["registration"]["advisory_states"] == []
    # Unmatched-CRD means "not in the SEC roster", NOT "no advisory
    # registrations on record" — it must not borrow the zero-state note.
    assert "advisory_states_note" not in unmatched["registration"]
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
    assert "AdvisorFinder" in doc
    assert "not the full SEC roster" in " ".join(doc.split())


def test_find_bookable_limit_clamped_high(monkeypatch):
    captured = {}

    def fake_search_marketplace(**kwargs):
        captured["limit"] = kwargs["limit"]
        return [], 0

    monkeypatch.setattr(server.db, "search_marketplace_with_total", fake_search_marketplace)
    server.find_bookable_advisors(limit=999)
    assert captured["limit"] == 50


def test_find_bookable_limit_clamped_low(monkeypatch):
    captured = {}

    def fake_search_marketplace(**kwargs):
        captured["limit"] = kwargs["limit"]
        return [], 0

    monkeypatch.setattr(server.db, "search_marketplace_with_total", fake_search_marketplace)
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
    monkeypatch.setattr(server.db, "search_marketplace_with_total", lambda **kwargs: ([], 0))
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


# ── production-shaped rows: DynamoDB JSON + mojibake are cleaned on output ───
# The fixture's marketplace rows hold plain text, but production rows hold
# DynamoDB attribute-value JSON and some mojibake. These tests feed a row in
# the production shape through the real tool code (db.search_marketplace and
# db.get_marketplace_by_crd are monkeypatched; the .db files are untouched).
import json  # noqa: E402
import re  # noqa: E402

_PROD_SHAPED_ROW = {
    "crd": "1000002",
    "displayName": "John Q. Smith",
    "companyName": "Alpha Wealth LLC",
    "jobTitle": "CERTIFIED FINANCIAL PLANNERÂ® Advisor",
    "city": "New York",
    "state": "NY",
    "bio": "ðŸ‘‹ Iâ€™m John, a CFPÂ® professional.",
    "credentials": json.dumps([
        {"M": {"acronym": {"S": "CFA"}, "value": {"S": "Chartered Financial Analyst"},
               "description": {"S": "Generic copy."}, "requirements": {"S": "Exams."},
               "quickFact": {"S": "Fact."}, "sortingPriority": {"N": "1"}, "id": {"N": "3"}}},
        {"M": {"acronym": {"S": "CFP®"}, "value": {"S": "Certified Financial Planner™"},
               "description": {"S": "A CFP professional is bound to be a fiduciary."},
               "requirements": {"S": "Exams."}, "quickFact": {"S": "Fact."},
               "sortingPriority": {"N": "0"}, "id": {"S": "1"}}},
    ]),
    "clientDescription": "Pre-retirees â€” and families.",
    "quickFacts": json.dumps([{"S": "aum"}, {"S": "averageAccountSize"}]),
    "pricing": json.dumps([
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "0.50"}, "maxRange": {"S": "1.00"}}},
        {"M": {"typeName": {"S": "HOURLY"}, "rate": {"S": "250"}}},
    ]),
    "minAccountSize": 250000.0,
    "yearsOfExperience": 15,
    "virtualMeetingsOffered": None,
    "allowedStates": json.dumps([{"S": "NY"}, {"S": "NJ"}]),
    "memberSince": "2022-03-01",
    "advisorWebsiteURL": None,
    "linkedInURL": None,
    "twitterURL": None,
    "bioVideoLink": None,
    "education": json.dumps([
        {"M": {"institution": {"S": "NYU"}, "degreeObtained": {"S": "MBA"},
               "completedDate": {"S": "Fri May 15 2009 04:00:00 GMT+0000 (Coordinated Universal Time)"},
               "educationId": {"S": "e1"}}},
    ]),
    "aum": 45000000.0,
    "clientNumber": 120,
    "in_their_own_words": json.dumps(["Lucasâ€™s â€œfun timeâ€\x9d is sailing."]),
    "profile_url": "https://advisorfinder.com/app/advisor-profile/qv3Y1g3y/john-q-smith",
}

_RAW_OR_GARBLED = re.compile(r'\{"S":|\{"M":|"N":|Ã|â€|ðŸ|Â®')


def test_find_bookable_cleans_production_shaped_row(monkeypatch):
    monkeypatch.setattr(db, "search_marketplace_with_total", lambda **kw: ([_PROD_SHAPED_ROW], 1))
    result = server.find_bookable_advisors()
    entry = result["results"][0]
    assert entry["credentials"] == [
        {"acronym": "CFP®", "name": "Certified Financial Planner™", "status": "self-reported"},
        {"acronym": "CFA", "name": "Chartered Financial Analyst", "status": "self-reported"},
    ]
    assert entry["education"] == [{"institution": "NYU", "degree": "MBA", "year": 2009}]
    assert entry["pricing"] == [
        {"type": "aum", "min": "0.50", "max": "1.00", "unit": "percent of assets"},
        {"type": "hourly", "rate": "250"},
    ]
    assert entry["allowed_states"] == ["NY", "NJ"]
    assert "quick_facts" not in entry
    assert entry["bio"] == "👋 I’m John, a CFP® professional."
    assert entry["client_description"] == "Pre-retirees — and families."
    assert entry["job_title"] == "CERTIFIED FINANCIAL PLANNER® Advisor"
    assert entry["in_their_own_words"] == ["Lucas’s “fun time” is sailing."]
    # The listing labels are still there.
    assert entry["note"] == format._MARKETPLACE_NOTE
    assert entry["self_reported"]["label"] == format.SELF_REPORTED_LABEL
    dumped = json.dumps(entry, ensure_ascii=False)
    assert not _RAW_OR_GARBLED.search(dumped)
    assert "fiduciary" not in dumped


def test_get_advisor_listing_pricing_is_cleaned(monkeypatch):
    monkeypatch.setattr(db, "get_marketplace_by_crd", lambda crd: _PROD_SHAPED_ROW)
    listing = server.get_advisor(crd="1000002")["advisorfinder_listing"]
    assert listing["pricing"][0] == {"type": "aum", "min": "0.50", "max": "1.00", "unit": "percent of assets"}
    assert listing["job_title"] == "CERTIFIED FINANCIAL PLANNER® Advisor"


def test_specialty_matches_credential_acronym():
    # Fixture credentials are plain text ("CFP, CFA" / "CFP"); "cfp" must
    # still find every CFP holder, case-insensitively.
    crds = {r["crd"] for r in server.find_bookable_advisors(specialty="cfp")["results"]}
    assert crds == {"1000002", "1000010"}


def test_specialty_ignores_generic_credential_copy_and_json_keys(monkeypatch, tmp_path):
    # The real db.search_marketplace filter over a throwaway DB (in tmp_path,
    # NOT the committed fixtures) holding one production-shaped row: the old
    # SQL LIKE over raw JSON matched "fiduciary" through the generic CFP
    # description, and matched JSON keys such as "acronym".
    import sqlite3

    path = tmp_path / "prod_shaped.db"
    conn = sqlite3.connect(path)
    cols = list(_PROD_SHAPED_ROW)
    conn.execute(f"CREATE TABLE marketplace_advisors ({', '.join(cols)})")
    conn.execute(
        f"INSERT INTO marketplace_advisors VALUES ({', '.join('?' * len(cols))})",
        [_PROD_SHAPED_ROW[c] for c in cols],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)

    def crds(specialty):
        return [r["crd"] for r in db.search_marketplace(specialty=specialty)]

    for needle in ("fiduciary", "acronym", "sortingPriority", '{"S"', "Generic copy", "averageAccountSize"):
        assert crds(needle) == [], needle
    for needle in ("CFP", "certified financial planner", "Lucas’s", "pre-retirees"):
        assert crds(needle) == ["1000002"], needle


def test_specialty_limit_applies_after_filter():
    # "cfp" matches 1000002 and 1000010; limit=1 returns the first by
    # displayName among the MATCHES, not the first row overall (1000003,
    # "Mary Jones", sorts between them and has no credentials).
    rows = db.search_marketplace(specialty="cfp", limit=1)
    assert [r["crd"] for r in rows] == ["1000002"]


# ── marketplace links: scheme added, unsafe schemes dropped ─────────────────
# Production profiles hold links with no scheme (60/163 websites, 25/141
# LinkedIn, 2/19 Twitter: "linkedin.com/in/jakehughesnm", "mgrwealth.com").

def test_find_bookable_links_get_https_scheme(monkeypatch):
    row = dict(_PROD_SHAPED_ROW)
    row.update({
        "advisorWebsiteURL": "  mgrwealth.com ",
        "linkedInURL": "linkedin.com/in/Jake-Hughes-CFP®",
        "twitterURL": "www.twitter.com/7saturdaysaweek",
        "bioVideoLink": "javascript:alert(1)",
        "profile_url": "advisorfinder.com/app/advisor-profile/x/y",
    })
    monkeypatch.setattr(db, "search_marketplace_with_total", lambda **kw: ([row], 1))
    entry = server.find_bookable_advisors()["results"][0]
    assert entry["website"] == "https://mgrwealth.com"
    assert entry["linkedin"] == "https://linkedin.com/in/Jake-Hughes-CFP®"  # case and ® kept
    assert entry["twitter"] == "https://www.twitter.com/7saturdaysaweek"
    assert entry["bio_video"] is None  # non-http scheme dropped
    assert entry["profile_url"] == "https://advisorfinder.com/app/advisor-profile/x/y"


def test_find_bookable_links_with_scheme_unchanged():
    result = server.find_bookable_advisors()
    by_crd = {r["crd"]: r for r in result["results"]}
    assert by_crd["1000002"]["website"] == "https://johnsmithadvisors.example.com"
    assert by_crd["1000002"]["linkedin"] == "https://linkedin.com/in/johnqsmith"
    assert by_crd["1000003"]["website"] is None  # empty stays None


# ── total_matches, order, and the description ───────────────────────────────

def test_find_bookable_total_matches():
    full = server.find_bookable_advisors()
    assert full["total_matches"] == full["result_count"] == 3
    one = server.find_bookable_advisors(limit=1)
    assert one["result_count"] == 1
    assert one["total_matches"] == 3
    assert server.find_bookable_advisors(specialty="cfp", limit=1)["total_matches"] == 2
    assert server.find_bookable_advisors(specialty="zzznomatch")["total_matches"] == 0


def test_find_bookable_results_are_alphabetical_by_name():
    names = [r["name"] for r in server.find_bookable_advisors()["results"]]
    assert names == sorted(names, key=str.casefold)


def _description(name):
    import asyncio
    tool = asyncio.run(server.mcp.get_tool(name))
    return " ".join((tool.description or "").split())


def test_find_bookable_description_states_order_and_extra_status():
    text = _description("find_bookable_advisors")
    assert "alphabetical order by name" in text
    assert "no effect on the order" in text
    assert "not_in_regulatory_dataset" in text
    assert "not stated on the profile" in text


# ── dates, unset profile fields, display names ──────────────────────────────

def test_find_bookable_member_since_is_iso(monkeypatch):
    for raw, expected in (
        ("Mon May 05 2025 12:59:41 GMT+0000 (Coordinated Universal Time)", "2025-05-05"),
        ("2024-08-14 20:25:09", "2024-08-14"),
        (None, None),
    ):
        row = {**_PROD_SHAPED_ROW, "memberSince": raw}
        monkeypatch.setattr(db, "search_marketplace_with_total", lambda row=row, **kw: ([row], 1))
        assert server.find_bookable_advisors()["results"][0]["member_since"] == expected


def test_find_bookable_unset_profile_fields_are_null_not_empty():
    result = server.find_bookable_advisors()
    by_crd = {r["crd"]: r for r in result["results"]}
    minimal = by_crd["1000003"]  # allowedStates "" and no virtual-meetings value
    assert minimal["allowed_states"] is None
    assert minimal["virtual_meetings_offered"] is None
    rich = by_crd["1000002"]
    assert rich["allowed_states"] == ["NY", "NJ", "CT"]
    assert rich["virtual_meetings_offered"] is True  # "Yes"
    assert by_crd["1000010"]["virtual_meetings_offered"] is False  # "No"
    note = result["profile_fields_note"]
    assert "not stated on the profile" in note
    assert "allowed_states" in note and "virtual_meetings_offered" in note


def test_find_bookable_production_flag_and_states(monkeypatch):
    row = {**_PROD_SHAPED_ROW, "virtualMeetingsOffered": "1",
           "allowedStates": '[{"S":"TX"},{"S":"OK"},{"S":"NM"},{"S":"LA"}]'}
    monkeypatch.setattr(db, "search_marketplace_with_total", lambda **kw: ([row], 1))
    entry = server.find_bookable_advisors()["results"][0]
    assert entry["virtual_meetings_offered"] is True
    assert entry["allowed_states"] == ["TX", "OK", "NM", "LA"]


def test_find_bookable_strips_own_credential_suffix_from_name(monkeypatch):
    row = {**_PROD_SHAPED_ROW, "displayName": "John Q. Smith, CFP®, CFA"}
    monkeypatch.setattr(db, "search_marketplace_with_total", lambda **kw: ([row], 1))
    assert server.find_bookable_advisors()["results"][0]["name"] == "John Q. Smith"


def test_find_bookable_keeps_suffix_that_is_not_an_own_credential(monkeypatch):
    row = {**_PROD_SHAPED_ROW, "displayName": "John Q. Smith, CPWA®"}
    monkeypatch.setattr(db, "search_marketplace_with_total", lambda **kw: ([row], 1))
    assert server.find_bookable_advisors()["results"][0]["name"] == "John Q. Smith, CPWA®"


def test_find_bookable_has_no_empty_verify():
    assert "verify" not in server.find_bookable_advisors()


def test_find_bookable_miss_gets_official_search_home_links():
    result = server.find_bookable_advisors(city="Nowhere", state="ZZ")
    assert result["result_count"] == 0
    assert result["verify"] == {
        "iapd": "https://adviserinfo.sec.gov/",
        "brokercheck": "https://brokercheck.finra.org/",
    }
