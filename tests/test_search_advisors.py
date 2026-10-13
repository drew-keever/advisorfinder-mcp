"""Tests for the search_advisors tool. Calls the plain module-level function
directly — on fastmcp 3.4.6, @mcp.tool returns the original function unchanged
(verified: `mcp.tool(fn) is fn`), so server.search_advisors IS the function
FastMCP dispatches to; no `.fn` indirection needed."""
from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_no_filters_returns_error_envelope():
    result = server.search_advisors()
    _envelope_keys_present(result)
    assert "error" in result
    assert "hint" in result


def test_search_by_name_token_swapped_order():
    a = server.search_advisors(name="smith jane")
    b = server.search_advisors(name="jane smith")
    _envelope_keys_present(a)
    ids_a = {r["crd"] for r in a["results"]}
    ids_b = {r["crd"] for r in b["results"]}
    assert ids_a == ids_b
    assert "1000001" in ids_a  # JANE SMITH


def test_search_by_city_and_state_browse_mode_no_name():
    result = server.search_advisors(city="New York", state="NY")
    _envelope_keys_present(result)
    assert result["result_count"] > 0
    ids = {r["crd"] for r in result["results"]}
    assert "1000001" in ids  # JANE SMITH, branch NEW YORK/NY


def test_search_advisor_at_two_firms_lists_both():
    result = server.search_advisors(name="mcdonald")
    assert result["result_count"] == 1
    firms = result["results"][0]["firms"]
    assert len(firms) == 2
    crds = {f["crd_number"] for f in firms}
    assert crds == {"100001", "100002"}


def test_search_name_title_cased_in_result():
    # "ohearn" (no apostrophe) deliberately NOT used here: the FTS5 unicode61
    # tokenizer (built by the export script, not this server) splits on
    # apostrophe, so the indexed name tokenizes to "o" + "hearn" — a search
    # must include the apostrophe (or just "hearn") to match, same as a real
    # consumer typing the advisor's actual name.
    result = server.search_advisors(name="o'hearn")
    assert result["result_count"] == 1
    assert result["results"][0]["name"] == "Sam O'Hearn"


def test_search_result_has_four_state_disclosure_and_iapd_link():
    result = server.search_advisors(name="jane smith")
    row = result["results"][0]
    assert row["disclosure"] == "none_reported"
    assert row["iapd_link"] == "https://adviserinfo.sec.gov/individual/summary/1000001"


def test_search_no_results_has_guidance_and_iapd_link():
    result = server.search_advisors(name="zzznomatchzzz")
    assert result["result_count"] == 0
    assert "not_found_guidance" in result
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]


def test_search_verify_populated_even_with_results():
    # envelope()'s `verify` should never default to {} on a search-type tool —
    # generic name-search fallback links are always present, result count aside.
    result = server.search_advisors(name="jane smith")
    assert result["result_count"] > 0
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]
    assert "brokercheck.finra.org" in result["verify"]["brokercheck"]


def test_search_limit_clamped_low(monkeypatch):
    # Assert on the clamped value actually passed to db.search_advisors, not
    # on the resulting row count — with only 6 fixture advisors, an
    # unclamped limit=0 and a correctly-clamped limit=1 both yield <=1 row,
    # so a row-count assertion alone can't tell clamping apart from no clamp.
    captured = {}

    def fake_search_advisors(**kwargs):
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(server.db, "search_advisors", fake_search_advisors)
    server.search_advisors(city="New York", state="NY", limit=0)
    # clamped to 1, plus the one extra row fetched to detect truncation
    assert captured["limit"] == 1 + 1


def test_search_limit_clamped_high(monkeypatch):
    captured = {}

    def fake_search_advisors(**kwargs):
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(server.db, "search_advisors", fake_search_advisors)
    server.search_advisors(city="New York", state="NY", limit=999)
    # clamped to 50, plus the one extra row fetched to detect truncation
    assert captured["limit"] == 50 + 1


def test_search_fts_injection_input_returns_cleanly():
    result = server.search_advisors(name='"foo" OR 1; DROP TABLE')
    _envelope_keys_present(result)
    assert result["result_count"] == 0


def test_search_by_firm_name():
    result = server.search_advisors(firm="alpha wealth")
    ids = {r["crd"] for r in result["results"]}
    assert "1000001" in ids
    assert "1000003" not in ids  # MARY JONES is at BETA, not ALPHA


def test_search_cjk_name_finds_advisor_not_unfiltered_browse():
    # Regression for the fts_query() unicode bug: a [A-Za-z0-9]-only sanitizer
    # mangled "李明" to None, which the (old) code path treated as "no name
    # filter" and returned an unfiltered browse -- i.e. arbitrary advisors
    # presented as if they matched. There's no CJK-named advisor in the
    # fixture, so the real regression check is: a genuinely non-matching CJK
    # name must return ZERO results, not a browse's worth of unrelated rows.
    result = server.search_advisors(name="李明")
    _envelope_keys_present(result)
    assert "error" not in result
    assert result["result_count"] == 0


def test_search_accented_name_matches_fixture_advisor():
    # 1000009 JOSÉ GARCÍA is indexed with accents; a consumer typing the
    # plain-ASCII form must still find them (fts_query() folds diacritics to
    # match the export's remove_diacritics=2 FTS index).
    result = server.search_advisors(name="jose garcia")
    _envelope_keys_present(result)
    assert result["result_count"] == 1
    assert result["results"][0]["crd"] == "1000009"
    assert result["results"][0]["name"] == "José García"


def test_search_accented_query_also_matches():
    # The caller's own input may already carry the accents -- must match too.
    result = server.search_advisors(name="José García")
    assert result["result_count"] == 1
    assert result["results"][0]["crd"] == "1000009"


def test_search_supplied_name_unsanitizable_returns_error_not_browse():
    # A SUPPLIED name that sanitizes to nothing (punctuation-only) must never
    # silently fall through to an unfiltered browse presenting arbitrary rows
    # as if they matched -- it must surface a clear error instead.
    result = server.search_advisors(name="!!!")
    _envelope_keys_present(result)
    assert "error" in result
    assert result.get("result_count") is None
    assert "results" not in result


def test_search_supplied_firm_unsanitizable_returns_error_not_browse():
    result = server.search_advisors(name="jane smith", firm="***")
    _envelope_keys_present(result)
    assert "error" in result
    assert "results" not in result


def test_search_curly_apostrophe_matches_ohearn():
    result = server.search_advisors(name="O’Hearn")
    crds = {r["crd"] for r in result["results"]}
    assert "1000004" in crds


# ── name_suffix (post-sweep resume-round): ia_reps.name_suffix ('JR.'/'III'/
#    NULL). 1000011 ROBERT JONES JR. exercises both the display path (suffix
#    appended to the rendered name) and the FTS search path (the export's
#    advisor_fts index carries the suffix in the indexed name -- tokenizing
#    'JR.' to the plain token "jr", NOT "junior"). ───────────────────────────

def test_search_name_includes_suffix():
    result = server.search_advisors(name="robert jones")
    assert result["result_count"] == 1
    assert result["results"][0]["crd"] == "1000011"
    assert result["results"][0]["name"] == "Robert Jones Jr."


def test_search_by_jr_token_matches_suffixed_advisor():
    result = server.search_advisors(name="jones jr")
    crds = {r["crd"] for r in result["results"]}
    assert "1000011" in crds


def test_search_by_junior_does_not_match_suffixed_advisor():
    # "junior" is a different word from the indexed "jr" token -- the
    # tokenizer doesn't expand/normalize one to the other.
    result = server.search_advisors(name="jones junior")
    crds = {r["crd"] for r in result["results"]}
    assert "1000011" not in crds


# ── marketplace enrichment (Task 4): a labeled listing block is joined per
#    result row (one db.get_marketplace_by_crd lookup per returned row, AFTER
#    ranking/limit) -- but must never change WHICH rows match or their ORDER.
#    1000001 (JANE SMITH, non-member) and 1000002 (JOHN Q SMITH, marketplace
#    member) share last name SMITH and both work at ALPHA WEALTH LLC/NEW
#    YORK/NY -- searching "smith" hits both, ordered by last_name/first_name
#    (JANE before JOHN) same as before enrichment existed. The name-ranking
#    fixture advisors 1000015 BRENDAN JOHN SMITH (exact last name, sorts
#    before JANE) and 1000016 JOHN SMITHSON (prefix-only match, always last)
#    also match "smith". ───────────────────

def test_search_advisors_ranking_unchanged_by_membership():
    result = server.search_advisors(name="smith")
    crds = [r["crd"] for r in result["results"]]
    # exact-last-name SMITHs alphabetically by first name, SMITHSON last
    assert crds == ["1000015", "1000001", "1000002", "1000016"]


def test_search_advisors_member_row_has_listing_block_nonmember_does_not():
    result = server.search_advisors(name="smith")
    by_crd = {r["crd"]: r for r in result["results"]}
    assert "advisorfinder_listing" not in by_crd["1000001"]
    listing = by_crd["1000002"]["advisorfinder_listing"]
    assert listing["note"] == (
        "This advisor is listed on AdvisorFinder — view their full profile and contact them."
    )


# ── name ranking + more-results signal ───────────────────────────────────────
#    Fixture: 1000002 JOHN Q SMITH, 1000015 BRENDAN JOHN SMITH, 1000001 JANE
#    SMITH, 1000016 JOHN SMITHSON (prefix-only match for "smith").

def _crds(result):
    return [r["crd"] for r in result["results"]]


def test_name_two_tokens_exact_first_and_last_ranks_first():
    # tier 0 JOHN SMITH, tier 1 other exact-last-name SMITHs, tier 2 SMITHSON.
    # (Old ORDER BY last_name, first_name would give 1000015 first.)
    crds = _crds(server.search_advisors(name="john smith"))
    assert crds[0] == "1000002"
    assert crds[1] == "1000015"
    assert crds[-1] == "1000016"


def test_name_single_token_exact_last_name_before_prefix_match():
    crds = _crds(server.search_advisors(name="smith"))
    assert crds.index("1000016") == len(crds) - 1
    assert set(crds[:-1]) == {"1000001", "1000002", "1000015"}


def test_suffix_token_gets_identical_rank_params_to_plain_name(monkeypatch):
    """"John Smith Jr." must rank like "john smith": the bound ORDER BY
    parameters (tokens) are identical; only the FTS MATCH term differs."""
    from advisorfinder_mcp import db

    captured = []
    real = db.get_conn

    class Spy:
        def __init__(self, c): self.c = c
        def execute(self, sql, params=()):
            if "FROM ia_reps WHERE" in sql:
                captured.append((sql, list(params)))
            return self.c.execute(sql, params)

    from contextlib import contextmanager

    @contextmanager
    def spy_conn():
        with real() as c:
            yield Spy(c)

    monkeypatch.setattr(db, "get_conn", spy_conn)
    db.search_advisors(name="John Smith Jr.", limit=5)
    db.search_advisors(name="john smith", limit=5)
    (sql_a, p_a), (sql_b, p_b) = captured
    assert sql_a.split("ORDER BY")[1] == sql_b.split("ORDER BY")[1]
    assert p_a[-4:] == p_b[-4:] == ["john", "smith", "smith", 5]


def test_no_name_filter_keeps_alphabetical_order():
    result = server.search_advisors(state="NY", limit=50)
    rows = [(r["name"]) for r in result["results"]]
    assert len(rows) >= 5
    from advisorfinder_mcp import db
    got = db.search_advisors(state="NY", limit=50)
    keys = [(r["last_name"], r["first_name"]) for r in got]
    assert keys == sorted(keys)


def test_more_results_available_present_when_truncated():
    result = server.search_advisors(name="smith", limit=2)
    assert result["result_count"] == 2
    assert result["more_results_available"] is True
    assert "firm" in result["more_results_hint"].lower()


def test_more_results_available_absent_when_not_truncated():
    result = server.search_advisors(name="smith", limit=4)  # exactly 4 match
    assert result["result_count"] == 4
    assert "more_results_available" not in result
    result = server.search_advisors(name="smith", limit=20)
    assert "more_results_available" not in result


def test_single_token_binds_last_name_tier_param(monkeypatch):
    """One token -> tier CASE on lower(last_name) with the raw token bound."""
    from contextlib import contextmanager
    from advisorfinder_mcp import db

    captured = []
    real = db.get_conn

    class Spy:
        def __init__(self, c): self.c = c
        def execute(self, sql, params=()):
            if "FROM ia_reps WHERE" in sql:
                captured.append((sql, list(params)))
            return self.c.execute(sql, params)

    @contextmanager
    def spy_conn():
        with real() as c:
            yield Spy(c)

    monkeypatch.setattr(db, "get_conn", spy_conn)
    db.search_advisors(name="smith", limit=5)
    (sql, params), = captured
    assert "CASE WHEN lower(last_name) = ?" in sql
    assert "lower(first_name)" not in sql  # no tier 0 for one token
    assert params[-2:] == ["smith", 5]


def test_ties_break_plain_name_before_middle_name_and_deterministically():
    from advisorfinder_mcp import db
    # No middle name sorts before a middle name within the same first/last.
    rows = db.search_advisors(name="smith", limit=10)
    smiths = [r for r in rows if (r["first_name"], r["last_name"]) == ("JOHN", "SMITH")]
    assert [r["middle_name"] for r in smiths] == sorted(
        [r["middle_name"] for r in smiths], key=lambda m: (bool(m), m or ""))
    assert [r["ind_source_id"] for r in rows] == [
        r["ind_source_id"] for r in db.search_advisors(name="smith", limit=10)]
