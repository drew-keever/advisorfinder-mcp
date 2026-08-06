from advisorfinder_mcp import server


def test_envelope_keys_present():
    result = server.get_database_stats()
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_verify_is_deliberately_empty_no_specific_crd_to_check():
    # Unlike the advisor/firm tools, get_database_stats isn't about any one
    # CRD — there's no specific IAPD/BrokerCheck record to point at, so
    # `verify` staying {} here is a deliberate choice, not an oversight.
    result = server.get_database_stats()
    assert result["verify"] == {}


def test_counts_match_fixture():
    result = server.get_database_stats()
    assert result["firms_count"] == 3
    assert result["state_firms_count"] == 1
    assert result["advisors_count"] == 6


def test_vintages_present():
    result = server.get_database_stats()
    assert result["vintages"]["advisors_as_of"] == "2026-05-20"
    assert result["vintages"]["firms_as_of"] == "2026-05-01"


def test_disclosure_tally_includes_unknown_bucket():
    result = server.get_database_stats()
    tally = result["disclosure_tally"]
    assert tally["none_reported"] == 3
    assert tally["disclosed_no_detail"] == 1
    assert tally["disclosed_with_detail"] == 1
    assert tally["unknown"] == 1  # SAM O'HEARN: has_disclosure='Y', no iar_details row
    assert sum(tally.values()) == result["advisors_count"]


def test_coverage_block_present_with_state_firm_line_and_no_national_claim():
    result = server.get_database_stats()
    coverage = result["coverage"]
    assert "state_firms_with_advisor_rosters" in coverage
    assert "/" in coverage["state_firms_with_advisor_rosters"]
    note = coverage["note"].lower()
    assert "state-registered" in note
    assert "empty roster does not mean" in note
