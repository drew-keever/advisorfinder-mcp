from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
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


def test_employment_current_first_then_previous():
    # "Llc" (not "LLC") is the honest output of title_case_name(), copied
    # verbatim from firm-intelligence's build_public_export.py — it has no
    # entity-suffix special-casing. If that helper is ever taught to render
    # entity suffixes properly, these two assertions are expected to change.
    result = server.get_advisor(crd="1000002")
    employment = result["employment"]
    assert len(employment) == 2
    assert employment[0]["is_current"] is True
    assert employment[0]["firm_name"] == "Alpha Wealth Llc"
    assert employment[1]["is_current"] is False
    assert employment[1]["firm_name"] == "Old Legacy Brokerage Llc"


def test_exams_parsed_from_json():
    result = server.get_advisor(crd="1000002")
    assert result["exams"]["state"][0]["name"] == "Series 63"
    assert result["exams"]["product"][0]["name"] == "Series 65"
    assert result["exams"]["principal"] == []


def test_registered_states_split_from_csv():
    result = server.get_advisor(crd="1000002")
    assert set(result["registered_states"]) == {"NY", "FL"}


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
    with_detail = server.get_advisor(crd="1000002")
    no_detail = server.get_advisor(crd="1000003")
    none_reported = server.get_advisor(crd="1000001")
    unknown = server.get_advisor(crd="1000004")

    assert with_detail["disclosure"]["status"] == "disclosed_with_detail"
    assert with_detail["disclosure"]["disclosure_count"] == 3
    assert no_detail["disclosure"]["status"] == "disclosed_no_detail"
    assert none_reported["disclosure"]["status"] == "none_reported"
    assert unknown["disclosure"]["status"] == "unknown"
    assert "no disclosures" not in unknown["disclosure"]["guidance"].lower()


def test_advisor_with_no_designations_or_content_still_works():
    result = server.get_advisor(crd="1000006")
    assert result["found"] is True
    assert result["designations"] == []
    assert result["bio"] is None
