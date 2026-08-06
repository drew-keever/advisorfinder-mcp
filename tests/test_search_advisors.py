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
    assert captured["limit"] == 1


def test_search_limit_clamped_high(monkeypatch):
    captured = {}

    def fake_search_advisors(**kwargs):
        captured["limit"] = kwargs["limit"]
        return []

    monkeypatch.setattr(server.db, "search_advisors", fake_search_advisors)
    server.search_advisors(city="New York", state="NY", limit=999)
    assert captured["limit"] == 50


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
