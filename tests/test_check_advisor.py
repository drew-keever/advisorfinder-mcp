import json

from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    # Task 4 (marketplace-layer): the link deep-links to a marketplace
    # member's own profile instead of the generic homepage -- both are valid
    # advisorfinder.com URLs. See test_check_advisor_member_has_listing_block_
    # and_deep_link / test_check_advisor_nonmember_has_no_listing_block below
    # for the exact member-vs-nonmember pin.
    assert result["advisorfinder"]["link"].startswith("https://advisorfinder.com")
    assert "verify" in result
    assert "coverage_caveats" in result


def _no_numeric_score_anywhere(obj):
    """Recursively assert nothing looks like a risk/score field."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert "score" not in k.lower(), f"found score-like key: {k}"
            _no_numeric_score_anywhere(v)
    elif isinstance(obj, list):
        for item in obj:
            _no_numeric_score_anywhere(item)


def test_check_by_crd():
    result = server.check_advisor(name_or_crd="1000002")
    _envelope_keys_present(result)
    assert result["found"] is True
    assert result["registration"]["active"] is True
    assert set(result["registration"]["advisory_states"]) == {"NY", "FL"}
    assert result["disclosure"]["status"] == "disclosed_with_detail"
    _no_numeric_score_anywhere(result)


def test_check_advisor_reports_advisory_states_and_the_broker_count():
    result = server.check_advisor(name_or_crd="1000002")
    assert set(result["registration"]["advisory_states"]) == {"NY", "FL"}
    assert "registered_states" not in result["registration"]
    # check_advisor is the "can this person advise me in Ohio?" tool, so the
    # broker fact must be here too, not only in get_advisor.
    assert result["registration"]["also_registered_as_broker"]["state_count"] == 3


def test_check_by_unique_name():
    result = server.check_advisor(name_or_crd="mary jones")
    assert result["found"] is True
    assert result["crd"] == "1000003"
    assert result["disclosure"]["status"] == "disclosed_no_detail"


def test_check_by_crd_includes_name_suffix():
    # Post-sweep resume-round: ia_reps.name_suffix. 1000011 ROBERT JONES JR.
    # shares last name JONES with 1000003 MARY JONES (deliberately, see
    # tests/fixtures/make_fixture_source.py) but is a distinct advisor, found
    # directly by CRD here (not via the name-search ambiguity path).
    result = server.check_advisor(name_or_crd="1000011")
    assert result["found"] is True
    assert result["name"] == "Robert Jones Jr."


def test_check_by_name_finds_suffixed_advisor_via_jr_not_junior():
    # FTS tokenizes 'JR.' (indexed, export-side) to the plain token "jr" --
    # "jr" must match, "junior" must not (it's simply a different word, not a
    # synonym the tokenizer expands).
    result = server.check_advisor(name_or_crd="robert jones jr")
    assert result["found"] is True
    assert result["crd"] == "1000011"
    assert result["name"] == "Robert Jones Jr."

    result = server.check_advisor(name_or_crd="robert jones junior")
    assert result["found"] is False


def test_check_ambiguous_two_smiths():
    result = server.check_advisor(name_or_crd="smith")
    _envelope_keys_present(result)
    assert result["ambiguous"] is True
    assert len(result["candidates"]) <= 5
    crds = {c["crd"] for c in result["candidates"]}
    assert {"1000001", "1000002"}.issubset(crds)
    assert "hint" in result
    # verify must not silently default to {} just because there's no single CRD yet
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]


def test_check_narrowed_by_firm_resolves_ambiguity():
    # both SMITHs are at ALPHA WEALTH LLC (100001) in the fixture, so firm
    # alone won't disambiguate them — narrow by a firm+name combo that DOES
    # resolve to exactly one: "smith" + firm "beta" matches nobody (neither
    # SMITH is at BETA), proving the firm filter is actually applied.
    result = server.check_advisor(name_or_crd="smith", firm="beta advisors")
    assert result.get("found") is False or result.get("ambiguous") is not True


def test_check_not_found():
    result = server.check_advisor(name_or_crd="zzznomatchzzz")
    _envelope_keys_present(result)
    assert result["found"] is False


def test_check_crd_not_found_has_verify_links():
    result = server.check_advisor(name_or_crd="9999999")
    assert result["found"] is False
    assert "iapd" in result["verify"]
    assert "brokercheck" in result["verify"]


def test_check_no_numeric_score_in_any_branch():
    _no_numeric_score_anywhere(server.check_advisor(name_or_crd="1000002"))
    _no_numeric_score_anywhere(server.check_advisor(name_or_crd="smith"))
    _no_numeric_score_anywhere(server.check_advisor(name_or_crd="zzznomatchzzz"))


def test_check_accented_name_matches_fixture_advisor():
    result = server.check_advisor(name_or_crd="jose garcia")
    assert result["found"] is True
    assert result["crd"] == "1000009"


def test_check_unsanitizable_name_returns_error_not_browse():
    # A SUPPLIED name that sanitizes to nothing must never fall through to
    # db.search_advisors() and present a browse's worth of arbitrary rows as
    # "ambiguous candidates" -- it must surface a clear error instead.
    result = server.check_advisor(name_or_crd="!!!")
    _envelope_keys_present(result)
    assert "error" in result
    assert result.get("found") is None
    assert result.get("ambiguous") is not True


def test_check_unsanitizable_firm_returns_error_not_browse():
    result = server.check_advisor(name_or_crd="smith", firm="***")
    _envelope_keys_present(result)
    assert "error" in result
    assert result.get("ambiguous") is not True


# ── marketplace enrichment (Task 4): check_advisor also joins marketplace
#    membership -- envelope link deep-links to the member's own profile, and a
#    labeled listing block is present, same shape as get_advisor's. ─────────

def test_check_advisor_member_has_listing_block_and_deep_link():
    result = server.check_advisor(name_or_crd="1000002")
    listing = result["advisorfinder_listing"]
    assert listing["note"] == (
        "This advisor is listed on AdvisorFinder — view their full profile and contact them."
    )
    assert result["advisorfinder"]["link"] == listing["profile_url"]


def test_check_advisor_nonmember_has_no_listing_block():
    result = server.check_advisor(name_or_crd="1000001")
    assert "advisorfinder_listing" not in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"


# ── more_candidates signal ───────────────────────────────────────────────────

def _fake_rows(n):
    return [
        {
            "ind_source_id": str(2000000 + i),
            "first_name": "JOHN", "middle_name": None, "last_name": "SMITH",
            "name_suffix": None, "has_disclosure": "N",
            "firms": [{"crd_number": "100001", "firm_name": "ALPHA WEALTH LLC",
                       "branch_city": "NEW YORK", "branch_state": "NY"}],
            "iar_row": None,
        }
        for i in range(n)
    ]


def test_check_more_candidates_flag_when_six_rows(monkeypatch):
    from advisorfinder_mcp import db
    monkeypatch.setattr(db, "search_advisors", lambda **kw: _fake_rows(6))
    result = server.check_advisor(name_or_crd="john smith")
    assert result["ambiguous"] is True
    assert result["more_candidates"] is True
    assert len(result["candidates"]) == 5
    assert "firm" in result["hint"] and "state" in result["hint"]
    assert "more" in result["hint"].lower()


def test_check_no_more_candidates_flag_when_two_rows(monkeypatch):
    from advisorfinder_mcp import db
    monkeypatch.setattr(db, "search_advisors", lambda **kw: _fake_rows(2))
    result = server.check_advisor(name_or_crd="john smith")
    assert result["ambiguous"] is True
    assert "more_candidates" not in result
    assert len(result["candidates"]) == 2


def test_check_does_not_auto_select_when_six_rows(monkeypatch):
    from advisorfinder_mcp import db
    monkeypatch.setattr(db, "search_advisors", lambda **kw: _fake_rows(6))
    result = server.check_advisor(name_or_crd="john smith")
    assert "found" not in result or result.get("ambiguous") is True
