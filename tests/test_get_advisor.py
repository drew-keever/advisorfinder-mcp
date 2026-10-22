from advisorfinder_mcp import format, server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    # Task 4 (marketplace-layer): the link deep-links to a marketplace
    # member's own profile instead of the generic homepage -- both are valid
    # advisorfinder.com URLs. See test_get_advisor_member_has_listing_block /
    # test_get_advisor_nonmember_has_no_listing_block below for the exact
    # member-vs-nonmember pin.
    assert result["advisorfinder"]["link"].startswith("https://advisorfinder.com")
    assert "verify" in result
    assert "coverage_caveats" in result


def test_not_found_has_verify_links():
    result = server.get_advisor(crd="9999999")
    _envelope_keys_present(result)
    assert result["found"] is False
    assert result["verify"]["iapd"] == "https://adviserinfo.sec.gov/individual/summary/9999999"
    assert result["verify"]["brokercheck"] == "https://brokercheck.finra.org/individual/summary/9999999"


def test_found_basic_shape():
    result = server.get_advisor(crd="1000002")
    _envelope_keys_present(result)
    assert result["found"] is True
    assert result["name"] == "John Q Smith"
    assert result["crd"] == "1000002"


def test_name_includes_suffix_when_present():
    # Post-sweep resume-round: ia_reps.name_suffix (e.g. 'JR.'/'III'/NULL).
    # 1000011 ROBERT JONES JR. carries a suffix; 1000002 (above) has none, so
    # this also pins that a NULL suffix doesn't add a trailing space/None.
    result = server.get_advisor(crd="1000011")
    assert result["found"] is True
    assert result["name"] == "Robert Jones Jr."


def test_employment_current_first_then_previous():
    # Entity suffixes stay uppercase via title_case_firm_name ("LLC", not "Llc").
    result = server.get_advisor(crd="1000002")
    employment = result["employment"]
    assert len(employment) == 2
    assert employment[0]["is_current"] is True
    assert employment[0]["firm_name"] == "Alpha Wealth LLC"
    assert employment[1]["is_current"] is False
    assert employment[1]["firm_name"] == "Old Legacy Brokerage LLC"


def test_exams_parsed_from_json():
    result = server.get_advisor(crd="1000002")
    assert result["exams"]["state"][0]["name"] == "Series 63"
    assert result["exams"]["product"][0]["name"] == "Series 65"
    assert result["exams"]["principal"] == []


def test_advisory_states_split_from_csv():
    result = server.get_advisor(crd="1000002")
    assert set(result["advisory_states"]) == {"NY", "FL"}


# ── advisory-state capacity: the tools report investment-adviser state
#    registrations only, and label broker-dealer ones separately ─────────────

def test_advisory_states_are_ia_only_and_broker_count_is_labeled():
    # 1000002 is dual-registered: IA in NY+FL, broker in NY/NJ/CT (3 distinct).
    result = server.get_advisor(crd="1000002")
    assert set(result["advisory_states"]) == {"NY", "FL"}
    assert "registered_states" not in result
    assert result["also_registered_as_broker"]["state_count"] == 3
    assert "3" in result["also_registered_as_broker"]["note"]


def test_pending_ce_state_appears_in_both_arrays_with_a_note():
    result = server.get_advisor(crd="1000012")
    assert set(result["advisory_states"]) == {"CA", "TX"}
    assert result["advisory_states_pending_ce"] == ["CA"]
    assert "advisory_states_pending_ce_note" in result


def test_zero_advisory_states_says_none_on_record_not_unknown():
    result = server.get_advisor(crd="1000013")
    assert result["advisory_states"] == []
    assert result["advisory_states_note"] == format.ADVISORY_STATES_NONE_NOTE
    # The broker registration is still surfaced, so a consumer is not told a
    # flat "not registered" about someone who is a registered broker.
    assert result["also_registered_as_broker"]["state_count"] == 3


def test_adviser_only_advisor_has_no_broker_block():
    result = server.get_advisor(crd="1000003")
    assert "also_registered_as_broker" not in result


def test_no_iar_details_row_gets_unavailable_note_not_none_note():
    # 1000004 (O'HEARN) has no iar_details row at all -- a THIRD meaning,
    # distinct from "none on record": we hold no detail record for this
    # person, so the tool must not claim a positive finding of zero
    # registrations.
    result = server.get_advisor(crd="1000004")
    assert result["advisory_states"] == []
    assert result["advisory_states_note"] == format.ADVISORY_STATES_UNAVAILABLE_NOTE
    assert result["advisory_states_note"] != format.ADVISORY_STATES_NONE_NOTE


def test_duplicate_job_is_merged_with_a_capacity_label():
    result = server.get_advisor(crd="1000014")
    current = [e for e in result["employment"] if e["is_current"]]
    assert len(current) == 1
    assert current[0]["capacity"] == "investment adviser and broker-dealer"
    assert current[0]["branch_city"] == "Naples"


def test_designations_always_self_reported():
    result = server.get_advisor(crd="1000002")
    assert len(result["designations"]) == 1
    d = result["designations"][0]
    assert d["code"] == "CFP"
    assert d["status"] == "self-reported"
    assert "verified" not in str(d["status"]).lower() or d["status"] == "self-reported"


def test_bio_provided_no_caveat():
    result = server.get_advisor(crd="1000002")
    assert result["bio"] == "Experienced wealth advisor specializing in retirement planning."
    assert not any("matched by name" in c for c in result["coverage_caveats"])


def test_bio_name_unique_has_caveat():
    result = server.get_advisor(crd="1000003")
    assert result["bio"]
    assert any("matched by name" in c for c in result["coverage_caveats"])


def test_years_in_industry_computed_when_parseable():
    result = server.get_advisor(crd="1000002")  # industry_start_date=2011-06-15
    assert isinstance(result["years_in_industry"], int)
    assert 10 <= result["years_in_industry"] <= 40


def test_years_in_industry_none_when_unparseable():
    result = server.get_advisor(crd="1000001")  # industry_start_date="not-a-real-date"
    assert result["years_in_industry"] is None


def test_disclosure_four_states_across_fixture_advisors():
    # CORRECTED CONTRACT: keyed on has_disclosure only. 1000004 (O'HEARN) has
    # has_disclosure='Y' but NO iar_details row -> disclosed_no_detail (a
    # known disclosure must never soften to 'unknown' for lack of detail).
    # 1000007 (the only fixture advisor with a NULL/missing has_disclosure
    # flag, also with no row) is the true 'unknown' pin.
    with_detail = server.get_advisor(crd="1000002")
    no_detail_with_row = server.get_advisor(crd="1000003")
    no_detail_no_row = server.get_advisor(crd="1000004")
    none_reported = server.get_advisor(crd="1000001")
    none_reported_no_row = server.get_advisor(crd="1000008")
    unknown = server.get_advisor(crd="1000007")

    assert with_detail["disclosure"]["status"] == "disclosed_with_detail"
    assert with_detail["disclosure"]["disclosure_count"] == 3
    assert no_detail_with_row["disclosure"]["status"] == "disclosed_no_detail"
    assert no_detail_no_row["disclosure"]["status"] == "disclosed_no_detail"
    assert none_reported["disclosure"]["status"] == "none_reported"
    assert none_reported_no_row["disclosure"]["status"] == "none_reported"
    assert unknown["disclosure"]["status"] == "unknown"
    assert "no disclosures" not in unknown["disclosure"]["guidance"].lower()


def test_advisor_with_no_designations_or_content_still_works():
    result = server.get_advisor(crd="1000006")
    assert result["found"] is True
    assert result["designations"] == []
    assert result["bio"] is None


# ── marketplace enrichment (Task 4) -- labeled listing block for members,
#    absent entirely for non-members. Fixture member: crd 1000002 (see
#    tests/fixtures/make_fixture_marketplace.py). Non-member: crd 1000001
#    (JANE SMITH never appears in the fixture marketplace xlsx). ────────────

def test_get_advisor_member_has_listing_block():
    result = server.get_advisor(crd="1000002")
    listing = result["advisorfinder_listing"]
    assert listing["profile_url"] == "https://advisorfinder.com/app/advisor-profile/qv3Y1g3y/john-q-smith"
    assert listing["job_title"] == "Senior Financial Advisor"
    assert listing["pricing"] == "$3,000/yr flat planning fee, or $250/hr for one-off consultations"
    assert listing["note"] == (
        "This advisor is listed on AdvisorFinder — view their full profile and contact them."
    )
    # the envelope's own advisorfinder link should deep-link to the member's
    # own profile rather than the generic homepage.
    assert result["advisorfinder"]["link"] == listing["profile_url"]


def test_get_advisor_nonmember_has_no_listing_block():
    result = server.get_advisor(crd="1000001")
    assert "advisorfinder_listing" not in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"


# ── dates are YYYY-MM-DD ────────────────────────────────────────────────────

def test_unparseable_industry_start_date_is_null_not_raw():
    result = server.get_advisor(crd="1000001")  # industry_start_date="not-a-real-date"
    assert result["industry_start_date"] is None


def test_production_date_formats_are_normalized(monkeypatch):
    from advisorfinder_mcp import db

    real = db.get_advisor

    def forced(crd):
        bundle = real(crd)
        bundle["iar"] = {
            **bundle["iar"],
            "industry_start_date": "8/19/2003",
            "state_exams": '[{"name": "Series 63", "date": "1/17/2006", "scope": "BOTH"}]',
            "product_exams": '[{"name": "Series 65", "date": "12/11/2015", "scope": "IA"},'
                             ' {"name": "X", "date": null, "scope": "IA"}]',
        }
        return bundle

    monkeypatch.setattr(db, "get_advisor", forced)
    result = server.get_advisor(crd="1000002")
    assert result["industry_start_date"] == "2003-08-19"
    assert isinstance(result["years_in_industry"], int) and result["years_in_industry"] >= 20
    assert result["exams"]["state"] == [{"name": "Series 63", "date": "2006-01-17", "scope": "BOTH"}]
    assert [e["date"] for e in result["exams"]["product"]] == ["2015-12-11", None]
    for job in result["employment"]:
        for key in ("start_date", "end_date"):
            assert job[key] is None or len(job[key]) == 10


# ── marketplace listing: the profile's own credentials and experience ──────

def test_listing_carries_self_reported_profile_credentials_and_experience():
    listing = server.get_advisor(crd="1000002")["advisorfinder_listing"]
    profile = listing["self_reported"]
    assert profile["credentials"] == "CFP, CFA"  # fixture: plain text
    assert profile["years_of_experience"] == 15
    assert profile["label"] == format.SELF_REPORTED_LABEL


def test_credential_sources_note_when_both_lists_present_and_differ():
    # Fixture: designations [CFP]; profile credentials "CFP, CFA".
    result = server.get_advisor(crd="1000002")
    assert result["credential_sources_note"] == format.CREDENTIAL_SOURCES_NOTE
    note = " ".join(result["credential_sources_note"].split())
    assert "are not the same list" in note
    assert "not independently verified" in note
    assert "a difference is not by itself a problem" in note
    assert "designations_coverage_note" not in result


def test_designations_carry_a_source_label(monkeypatch):
    # Fixture source value "cfp_board" is not a production value: passed as is.
    assert server.get_advisor(crd="1000002")["designations"][0]["source"] == "cfp_board"
    from advisorfinder_mcp import db
    real = db.get_advisor

    def forced(crd):
        bundle = real(crd)
        bundle["designations"] = [
            {**bundle["designations"][0], "source": "part2b"},
            {**bundle["designations"][0], "code": "CFA", "source": "website_title"},
        ]
        return bundle

    monkeypatch.setattr(db, "get_advisor", forced)
    sources = [d["source"] for d in server.get_advisor(crd="1000002")["designations"]]
    assert sources == ["Form ADV Part 2B brochure supplement", "firm website"]


def test_no_credential_notes_for_nonmember_or_matching_lists(monkeypatch):
    nonmember = server.get_advisor(crd="1000001")
    assert "credential_sources_note" not in nonmember
    assert "designations_coverage_note" not in nonmember
    from advisorfinder_mcp import db
    row = dict(db.get_marketplace_by_crd("1000002"))
    row["credentials"] = "CFP, MBA"  # a degree is not compared
    monkeypatch.setattr(db, "get_marketplace_by_crd", lambda crd: row)
    result = server.get_advisor(crd="1000002")
    assert "credential_sources_note" not in result
    assert "designations_coverage_note" not in result


def test_designations_coverage_note_for_production_shape(monkeypatch):
    # David Calliott (CRD 5820086) in production: no designations on record,
    # CFP® on the AdvisorFinder profile. Missing public data is not a
    # difference between the lists.
    import json

    from advisorfinder_mcp import db
    real = db.get_advisor

    def forced(crd):
        bundle = real(crd)
        bundle["designations"] = []
        return bundle

    row = dict(db.get_marketplace_by_crd("1000002"))
    row["credentials"] = json.dumps([
        {"M": {"acronym": {"S": "CFP®"}, "value": {"S": "Certified Financial Planner™"},
               "sortingPriority": {"N": "0"}}},
        {"M": {"acronym": {"S": "Series 7"}, "value": {"S": "Series 7"},
               "sortingPriority": {"N": "1"}}},
    ])
    row["yearsOfExperience"] = 13
    monkeypatch.setattr(db, "get_advisor", forced)
    monkeypatch.setattr(db, "get_marketplace_by_crd", lambda crd: row)
    result = server.get_advisor(crd="1000002")
    assert result["designations"] == []
    profile = result["advisorfinder_listing"]["self_reported"]
    assert profile["credentials"][0] == {
        "acronym": "CFP®", "name": "Certified Financial Planner™", "status": "self-reported",
    }
    assert profile["years_of_experience"] == 13
    assert "credential_sources_note" not in result
    assert result["designations_coverage_note"] == format.DESIGNATIONS_COVERAGE_NOTE
    note = " ".join(result["designations_coverage_note"].split())
    assert "does not mean this advisor holds no designations" in note
    assert "Form ADV Part 2B" in note


def test_get_advisor_description_names_both_notes():
    import asyncio
    tool = asyncio.run(server.mcp.get_tool("get_advisor"))
    text = " ".join((tool.description or "").split())
    assert "designations_coverage_note" in text
    assert "credential_sources_note" in text
