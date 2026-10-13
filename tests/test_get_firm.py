import json

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
    assert result["website"] == "alphawealth.com"
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


def test_private_residence_location_phrasing():
    result = server.get_firm(crd="100002")
    priv = next(loc for loc in result["locations"] if "address" in loc)
    assert priv["address"] == "Address withheld — private residence (SEC privacy protection)"


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
