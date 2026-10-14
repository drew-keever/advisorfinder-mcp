from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_search_by_primary_name():
    result = server.search_firms(name="alpha wealth")
    _envelope_keys_present(result)
    crds = {r["crd"] for r in result["results"]}
    assert "100001" in crds


def test_other_name_only_match_carries_matched_as():
    result = server.search_firms(name="gateway")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "100002"
    assert "matched_as" in row
    assert "Gateway Capital Partners" in row["matched_as"]


def test_primary_match_has_no_matched_as():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "matched_as" not in row


def test_state_firm_carries_caveat():
    result = server.search_firms(name="delta state")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "500001"
    assert "caveat" in row
    assert "state-registered" in row["caveat"].lower()


def test_sec_firm_has_no_state_caveat():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "caveat" not in row


def test_state_filter_by_address_state():
    result = server.search_firms(state="MA")
    crds = {r["crd"] for r in result["results"]}
    assert "100002" in crds  # BETA is in MA
    assert "100001" not in crds  # ALPHA is in NY


def test_no_results_has_guidance():
    result = server.search_firms(name="zzznomatchzzz")
    assert result["result_count"] == 0
    assert "not_found_guidance" in result
    assert "iapd" in result["verify"] and "brokercheck" in result["verify"]


def test_verify_populated_even_with_results():
    result = server.search_firms(name="alpha wealth")
    assert result["result_count"] > 0
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]


def test_aum_band_field_present_on_every_result():
    result = server.search_firms(state="NY")
    for row in result["results"]:
        assert "aum_band" in row


def test_search_firms_unsanitizable_name_errors_instead_of_browse():
    result = server.search_firms(name="!!!")
    assert "error" in result
    assert not result.get("results")


def test_search_firms_renders_llc_uppercase():
    result = server.search_firms(name="alpha wealth")
    names = {r["name"] for r in result["results"]}
    assert "Alpha Wealth LLC" in names


# ── advisor_count comes from the roster, never the Form ADV column ──────────
# firms.investment_adviser_reps is unreliable upstream (production: Fisher
# Investments CRD 107342 reports 0 but has 2,861 roster advisors). The fixture
# disagrees the same way: 100001 reports 5 but has a larger roster, 100003
# reports 5 but has no roster at all. Expectations are computed from the
# fixture's own ia_rep_firms, never hard-coded.

def _fixture_counts():
    from advisorfinder_mcp import db
    with db.get_conn() as conn:
        roster = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT crd_number, COUNT(DISTINCT ind_source_id) FROM ia_rep_firms "
                "WHERE ia_only = 'Y' GROUP BY crd_number"
            )
        }
        adv = {
            r[0]: r[1]
            for r in conn.execute("SELECT crd_number, investment_adviser_reps FROM firms")
        }
    return roster, adv


def test_advisor_count_is_distinct_roster_count_not_adv_figure():
    roster, adv = _fixture_counts()
    # Guard: the fixture really does disagree, so this test proves something.
    assert roster.get("100001", 0) != adv["100001"]
    assert roster.get("100003", 0) != adv["100003"]

    result = server.search_firms(limit=50)
    by_crd = {r["crd"]: r for r in result["results"]}
    for crd in ("100001", "100002", "100003"):
        assert by_crd[crd]["advisor_count"] == roster.get(crd, 0), crd


def test_advisor_count_is_none_for_state_only_firm():
    result = server.search_firms(limit=50)
    state_only = [r for r in result["results"] if "caveat" in r]
    assert state_only
    assert all(r["advisor_count"] is None for r in state_only)


def test_roster_counts_counts_distinct_advisers_only():
    # One advisor linked to the same firm twice (two branches, or the adviser
    # row plus the broker row of a dual registrant) is one person. A person
    # with only ia_only = 'N' (broker-dealer) or a NULL flag is not counted.
    import sqlite3

    from advisorfinder_mcp import db
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ia_rep_firms (ind_source_id TEXT, crd_number TEXT, ia_only TEXT)")
    conn.executemany(
        "INSERT INTO ia_rep_firms VALUES (?, ?, ?)",
        [
            ("1", "A", "Y"), ("1", "A", "Y"), ("1", "A", "N"),  # dual: one person
            ("2", "A", "Y"),
            ("4", "A", "N"),  # broker-only: not counted
            ("5", "A", None),  # unknown flag: not counted
            ("3", "B", "Y"),
            ("6", "C", "N"),  # broker-only firm: absent (callers default to 0)
        ],
    )
    assert db.roster_counts(conn, ["A", "B", "C"]) == {"A": 2, "B": 1}
    assert db.roster_counts(conn, []) == {}


# ── a CRD in both `firms` and `firms_state` is returned once ────────────────
# Production has 139 such CRDs (e.g. 306770 came back twice, once with a
# count and once with null). The fixture has none, so this test builds a copy
# with one dual-listed firm.

def test_crd_in_both_firms_and_firms_state_is_returned_once(tmp_path, monkeypatch):
    import shutil
    import sqlite3

    from advisorfinder_mcp import db
    copy = tmp_path / "dup.db"
    shutil.copy(db.DB_PATH, copy)
    conn = sqlite3.connect(copy)
    conn.execute(
        "INSERT INTO firms_state (crd_number, primary_name, address_city, address_state, aum_band) "
        "SELECT crd_number, primary_name, address_city, address_state, aum_band "
        "FROM firms WHERE crd_number = '100001'"
    )
    conn.execute(
        "INSERT INTO firm_fts (name, crd_number, kind) "
        "SELECT primary_name, crd_number, 'state' FROM firms WHERE crd_number = '100001'"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", copy)

    for kwargs in ({"name": "alpha wealth"}, {"state": "NY", "limit": 50}):
        result = server.search_firms(**kwargs)
        rows = [r for r in result["results"] if r["crd"] == "100001"]
        assert len(rows) == 1, kwargs
        assert rows[0]["advisor_count"] is not None  # the `firms` row wins
        assert "caveat" not in rows[0]
