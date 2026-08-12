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
    assert result["advisors_count"] == 10  # post-sweep resume-round: +1000011


def test_vintages_present():
    result = server.get_database_stats()
    assert result["vintages"]["advisors_as_of"] == "2026-05-20"
    assert result["vintages"]["firms_as_of"] == "2026-05-01"


def test_disclosure_tally_includes_unknown_bucket():
    # CORRECTED CONTRACT (keyed on has_disclosure only — see
    # format.disclosure_status() / db.disclosure_tally()):
    #   none_reported=6 (1000001,1000005,1000006,1000008,1000009,1000011 — all 'N', row or not;
    #                    1000011 ROBERT JONES JR. added in the post-sweep resume-round)
    #   disclosed_no_detail=2 (1000003 row+count0, 1000004 'Y' no row at all)
    #   disclosed_with_detail=1 (1000002)
    #   unknown=1 (1000007 — has_disclosure NULL/missing, no row)
    # This tally is recomputed directly (db.disclosure_tally()), NOT read from
    # export_meta's disclosure_tally_* fields — not because those are wrong
    # (the export script now implements this exact same contract and its
    # tally matches value-for-value) but as deliberate decoupling: this
    # server owns format.disclosure_status()'s bucketing and shouldn't have
    # to trust a separately-owned script to keep matching it.
    result = server.get_database_stats()
    tally = result["disclosure_tally"]
    assert tally["none_reported"] == 6
    assert tally["disclosed_no_detail"] == 2
    assert tally["disclosed_with_detail"] == 1
    assert tally["unknown"] == 1
    assert sum(tally.values()) == result["advisors_count"]


def test_coverage_block_present_with_state_firm_line_and_no_national_claim():
    result = server.get_database_stats()
    coverage = result["coverage"]
    assert "state_firms_with_advisor_rosters" in coverage
    assert "/" in coverage["state_firms_with_advisor_rosters"]
    note = coverage["note"].lower()
    assert "state-registered" in note
    assert "empty roster does not mean" in note


# ── marketplace stats (Task 4): count + snapshot date, sourced from
#    db.marketplace_stats() -- fixture has 3 committed marketplace members as
#    of the Gate A2 (2026-08-09) fixture regen (see
#    tests/fixtures/make_fixture_marketplace.py: the third, crd 1000010, is
#    Ruling 1's crd-not-in-ia_reps case). ────────────────────────────────────

def test_stats_reports_marketplace_count():
    result = server.get_database_stats()
    marketplace = result["marketplace"]
    assert marketplace["member_count"] == 3
    assert marketplace["snapshot_date"]  # present, non-empty (mtime-derived, not pinned)
