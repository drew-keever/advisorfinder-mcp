import json

import pytest

from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_not_found_has_both_firm_search_links():
    result = server.get_firm(crd="9999999")
    _envelope_keys_present(result)
    assert result["found"] is False
    assert "iapd" in result["verify"]
    assert "brokercheck" in result["verify"]


def test_found_basic_identity_and_address():
    # Entity suffixes stay uppercase via title_case_firm_name ("LLC", not "Llc")
    # (copied verbatim from firm-intelligence's build_public_export.py) — see
    # the same note in test_get_advisor.py.
    result = server.get_firm(crd="100001")
    _envelope_keys_present(result)
    assert result["found"] is True
    assert result["name"] == "Alpha Wealth LLC"
    assert result["address"]["city"] == "New York"
    assert result["address"]["state"] == "NY"
    assert result["website"] == "https://alphawealth.com"  # scheme added (normalize_url)
    assert result["aum_band"] == "Not disclosed"


def test_client_types_withheld_and_fee_flags_rendered_as_list():
    result = server.get_firm(crd="100001")
    assert "serves" not in result
    assert "client_types_note" in result
    assert "Item 5.D" in result["client_types_note"]
    assert isinstance(result["fee_arrangements"], list)
    assert len(result["fee_arrangements"]) > 0


def test_get_firm_never_emits_sovereign_even_when_serves_sovereign_is_y(monkeypatch):
    # No fixture firm has serves_sovereign='Y' (and fixtures are not hand-edited),
    # so force it on the bundle. serves_sovereign is ADV 5G(9) pricing services,
    # not a client type; it must never surface as "sovereign".
    from advisorfinder_mcp import db

    real = db.get_firm

    def forced(crd):
        bundle = real(crd)
        bundle["firm"] = {**dict(bundle["firm"]), "serves_sovereign": "Y"}
        return bundle

    monkeypatch.setattr(db, "get_firm", forced)
    result = server.get_firm(crd="100001")
    assert "sovereign" not in json.dumps(result, default=str).lower()


def test_private_residence_locations_are_counted_not_listed():
    # 100002's only location is a private residence with no street.
    result = server.get_firm(crd="100002")
    assert result["locations"] == []
    assert result["private_residence_locations_withheld"] == 1
    assert "Address withheld — private residence (SEC privacy protection)" in (
        result["private_residence_note"]
    )


def test_no_private_residence_keys_when_none_withheld():
    result = server.get_firm(crd="100001")
    assert "private_residence_locations_withheld" not in result
    assert "private_residence_note" not in result
    assert all("address" not in loc for loc in result["locations"])


def test_normal_location_has_street():
    result = server.get_firm(crd="100001")
    normal = next(loc for loc in result["locations"] if loc.get("street1"))
    assert normal["street1"] == "2 BRANCH RD"
    assert normal["city"] == "Brooklyn"


def test_other_names_present():
    result = server.get_firm(crd="100001")
    assert "Alpha Wealth Management LLC" in result["other_names"]


def test_empty_roster_caveat_when_reps_declared_but_none_linked():
    result = server.get_firm(crd="100003")
    assert result["advisor_roster_count"] == 0
    assert any("don't have individual advisor records" in c for c in result["coverage_caveats"])


def test_nonempty_roster_has_no_empty_roster_caveat():
    result = server.get_firm(crd="100001")
    assert result["advisor_roster_count"] > 0
    assert not any("don't have individual advisor records" in c for c in result["coverage_caveats"])


def test_firms_state_fallback_reduced_profile():
    result = server.get_firm(crd="500001")
    assert result["found"] is True
    assert result.get("reduced_profile") is True
    assert result["name"] == "Delta State Advisers"
    assert any("state-registered" in c.lower() for c in result["coverage_caveats"])


def test_fee_block_estimate_present_for_firm_with_part2a():
    result = server.get_firm(crd="100001")
    assert result["fees"] is not None
    assert "estimated" in result["fees"]["basis"].lower()
    assert "disclaimer" in result["fees"]


def test_firm_content_needs_review_note():
    result = server.get_firm(crd="100001")
    assert result["content"]["tagline"] == "Wealth, simplified."
    assert "unverified" in result["content"]["note"].lower()


# ── advisor_count comes from the roster, never the Form ADV column ──────────

def _roster_and_adv(crd):
    from advisorfinder_mcp import db
    with db.get_conn() as conn:
        # Investment adviser representatives only: ia_only = 'Y' (see
        # db.roster_counts -- 'N' rows are broker-dealer registrations).
        roster = conn.execute(
            "SELECT COUNT(DISTINCT ind_source_id) FROM ia_rep_firms "
            "WHERE crd_number = ? AND ia_only = 'Y'",
            (crd,),
        ).fetchone()[0]
        adv = conn.execute(
            "SELECT investment_adviser_reps FROM firms WHERE crd_number = ?", (crd,)
        ).fetchone()[0]
    return roster, adv


def test_advisor_count_is_roster_count_when_adv_figure_disagrees():
    roster, adv = _roster_and_adv("100001")
    assert roster != adv  # guard: the fixture disagrees, as production does
    result = server.get_firm(crd="100001")
    assert result["advisor_count"] == roster
    assert result["advisor_roster_count"] == roster


def test_adv_rep_figure_is_never_exposed():
    roster, adv = _roster_and_adv("100003")
    assert roster == 0 and adv > 0
    result = server.get_firm(crd="100003")
    assert "investment_adviser_reps" not in result
    assert result["advisor_count"] == 0
    # The empty-roster caveat must not quote the unreliable ADV figure.
    assert not any(str(adv) in c for c in result["coverage_caveats"])
    assert not any("investment adviser reps" in c for c in result["coverage_caveats"])


def test_state_only_reduced_profile_has_null_advisor_count():
    result = server.get_firm(crd="500001")
    assert result["advisor_count"] is None


def test_broker_only_person_is_not_counted():
    # A person whose only link to the firm is an ia_only = 'N' (broker-dealer)
    # row is not an investment adviser representative there and must not be
    # counted. Computed from the fixture: 100001 has such a person.
    from advisorfinder_mcp import db
    with db.get_conn() as conn:
        everyone = conn.execute(
            "SELECT COUNT(DISTINCT ind_source_id) FROM ia_rep_firms WHERE crd_number = '100001'"
        ).fetchone()[0]
        broker_only = conn.execute(
            "SELECT COUNT(DISTINCT ind_source_id) FROM ia_rep_firms f "
            "WHERE crd_number = '100001' AND NOT EXISTS ("
            "  SELECT 1 FROM ia_rep_firms y WHERE y.crd_number = f.crd_number "
            "  AND y.ind_source_id = f.ind_source_id AND y.ia_only = 'Y')"
        ).fetchone()[0]
    assert broker_only >= 1  # guard: the fixture has a broker-only person
    result = server.get_firm(crd="100001")
    assert result["advisor_count"] == everyone - broker_only


# ── lost characters (U+FFFD) are shown as is and labeled ────────────────────

def _fffd_copy(tmp_path, monkeypatch, sql_statements):
    import shutil
    import sqlite3

    from advisorfinder_mcp import db
    copy = tmp_path / "fffd.db"
    shutil.copy(db.DB_PATH, copy)
    conn = sqlite3.connect(copy)
    for sql, params in sql_statements:
        conn.execute(sql, params)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", copy)


def test_get_firm_no_unreadable_caveat_for_clean_record():
    from advisorfinder_mcp import format
    result = server.get_firm(crd="100001")
    assert format.UNREADABLE_CHARS_CAVEAT not in result["coverage_caveats"]


def test_get_firm_location_with_fffd_gets_one_caveat(tmp_path, monkeypatch):
    from advisorfinder_mcp import format
    _fffd_copy(tmp_path, monkeypatch, [
        ("INSERT INTO firm_locations (crd_number, location_type, street1, city, state, private_res) "
         "VALUES ('100001', 'branch', ?, ?, NULL, 'N')",
         ("R. LEOPOLDO COUTO DE MAGALH�ES J�NIOR", "S�O PAULO")),
        ("INSERT INTO firm_other_names (crd_number, other_name) VALUES ('100001', ?)",
         ("LEALT� PRIVATE WEALTH",)),
    ])
    result = server.get_firm(crd="100001")
    streets = [loc.get("street1") for loc in result["locations"]]
    assert "R. LEOPOLDO COUTO DE MAGALH�ES J�NIOR" in streets  # never guessed
    assert result["coverage_caveats"].count(format.UNREADABLE_CHARS_CAVEAT) == 1


@pytest.mark.parametrize("column, value", [
    ("primary_name", "M�TIER CAPITAL, LLC"),
    ("legal_name", "LINDA S. LUBITZ, CFP�, PA"),
    ("address_street1", "31 W 52ND�STREET"),
    ("address_city", "S�O PAULO"),
])
def test_get_firm_identity_field_with_fffd_gets_caveat(tmp_path, monkeypatch, column, value):
    from advisorfinder_mcp import format
    _fffd_copy(tmp_path, monkeypatch, [
        (f"UPDATE firms SET {column} = ? WHERE crd_number = '100001'", (value,)),
    ])
    result = server.get_firm(crd="100001")
    assert format.UNREADABLE_CHARS_CAVEAT in result["coverage_caveats"]


def test_get_firm_state_only_with_fffd_gets_caveat(tmp_path, monkeypatch):
    from advisorfinder_mcp import format
    _fffd_copy(tmp_path, monkeypatch, [
        ("UPDATE firms_state SET primary_name = ? WHERE crd_number = '500001'", ("DELTA ST�TE",)),
    ])
    result = server.get_firm(crd="500001")
    assert result["reduced_profile"] is True
    assert format.UNREADABLE_CHARS_CAVEAT in result["coverage_caveats"]


# ── state registrations, fee block, dates ──────────────────────────────────

def _forced_firm(monkeypatch, **changes):
    from advisorfinder_mcp import db

    real = db.get_firm

    def forced(crd):
        bundle = real(crd)
        for key, value in changes.items():
            if key == "firm":
                bundle["firm"] = {**dict(bundle["firm"]), **value}
            else:
                bundle[key] = value
        return bundle

    monkeypatch.setattr(db, "get_firm", forced)


def test_state_registrations_are_objects_without_dates_in_fixture():
    result = server.get_firm(crd="100001")  # fixture value "NY,FL"
    assert result["state_registrations"] == [
        {"state": "NY", "date": None}, {"state": "FL", "date": None},
    ]


def test_state_registrations_production_format(monkeypatch):
    _forced_firm(monkeypatch, firm={"state_registrations": "AK-04/14/2009, CA-03/13/2025"})
    result = server.get_firm(crd="100001")
    assert result["state_registrations"] == [
        {"state": "AK", "date": "2009-04-14"}, {"state": "CA", "date": "2025-03-13"},
    ]


def test_reduced_profile_states_approved_production_format(monkeypatch):
    from advisorfinder_mcp import db

    real = db.get_firm

    def forced(crd):
        bundle = real(crd)
        bundle["state_firm"] = {**bundle["state_firm"], "states_approved": "VA:2023-01-03,MD:2024-02-02"}
        return bundle

    monkeypatch.setattr(db, "get_firm", forced)
    result = server.get_firm(crd="500001")
    assert result["states_approved"] == [
        {"state": "VA", "date": "2023-01-03"}, {"state": "MD", "date": "2024-02-02"},
    ]


def test_fee_brochure_date_is_iso(monkeypatch):
    part2a = {
        "crd_number": "100001", "brochure_name": "X", "brochure_date": "3/30/2026",
        "fee_rate_blended": 0.01, "fee_rate_is_estimate": 0, "fee_tiers_json": None,
        "fee_notes": None, "account_minimum": None, "account_minimum_waivable": 0,
        "needs_review": 0,
    }
    _forced_firm(monkeypatch, part2a=part2a)
    assert server.get_firm(crd="100001")["fees"]["brochure_date"] == "2026-03-30"


def test_fee_block_nothing_extracted(monkeypatch):
    # Vanguard Advisers (CRD 106715) in the 2026-08-28 export.
    part2a = {
        "crd_number": "100001", "brochure_name": "STABLE VALUE", "brochure_date": "3/30/2026",
        "fee_rate_blended": None, "fee_rate_is_estimate": 0, "fee_tiers_json": "[]",
        "fee_notes": None, "account_minimum": None, "account_minimum_waivable": 0,
        "needs_review": 1,
    }
    _forced_firm(monkeypatch, part2a=part2a)
    fees = server.get_firm(crd="100001")["fees"]
    assert fees["basis"] == "not extracted"
    assert "disclaimer" not in fees
    assert "account_minimum_waivable" not in fees
    assert fees["brochure_date"] == "2026-03-30"
    assert "SEC IAPD" in fees["note"]


def test_get_firm_description_names_new_shapes():
    import asyncio
    tool = asyncio.run(server.mcp.get_tool("get_firm"))
    text = " ".join((tool.description or "").split())
    assert "private_residence_locations_withheld" in text
    assert "{state, date}" in text
