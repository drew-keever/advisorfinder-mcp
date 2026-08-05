"""Tests for the advisorfinder MCP server. All API calls are mocked."""
import copy

import advisorfinder_mcp as srv

SAMPLE_ADVISOR_RESPONSE = {
    "advisor": {
        "crd_number": 123456,
        "first_name": "Jane", "last_name": "Smith",
        "active_registration": True,
        "firm_name": "Example Wealth", "firm_crd": 999,
        "city": "Austin", "state": "TX",
        "has_criminal": False, "has_regulatory_action": False,
        "has_customer_complaint": True, "has_termination": False,
        "has_civil_judicial": True, "has_judgment": False,
        "has_investigation": False, "has_bankruptcy": False, "has_bond": False,
    },
    "exams": [{"exam": "Series 65", "exam_date": "2010-06-15"}],
    "employment_history": [
        {"firm_name": "Example Wealth", "from_date": "06/2012",
         "city": "Austin", "state": "TX"},
    ],
    "registrations": [{"registration_authority": "TX", "status": "APPROVED"}],
    "designations": [{"designation": "CFP"}],
    "previous_registrations": [],
    "other_business": [],
}


def patch_api(monkeypatch, response):
    """Replace api_get with a stub returning `response`; records requested endpoints."""
    calls = []

    def fake_api_get(endpoint):
        calls.append(endpoint)
        return copy.deepcopy(response)

    monkeypatch.setattr(srv, "api_get", fake_api_get)
    return calls


def test_lookup_advisor_disclosures_covers_all_nine_flags(monkeypatch):
    """Regression: _RISK_WEIGHTS must be defined in ALL import paths (NameError bug),
    and the disclosures dict must contain every scored flag."""
    patch_api(monkeypatch, SAMPLE_ADVISOR_RESPONSE)
    result = srv.lookup_advisor(123456)
    assert result["found"] is True
    assert set(result["disclosures"]) == set(srv._RISK_WEIGHTS)
    # complaint (25) + civil/judicial (15) = 40 → High
    assert result["risk_score"] == 40
    assert result["risk_level"] == "High"


def test_verify_advisor_reports_all_disclosure_types(monkeypatch):
    """civil_judicial alone must set has_disclosures=True (was missed pre-1.2.0)."""
    resp = copy.deepcopy(SAMPLE_ADVISOR_RESPONSE)
    resp["advisor"]["has_customer_complaint"] = False  # leave only civil_judicial
    patch_api(monkeypatch, resp)
    result = srv.verify_advisor(123456)
    assert result["has_disclosures"] is True
    assert result["disclosure_types"] == ["Civil/Judicial Action"]
    assert result["risk_score"] == 15


def test_risk_profile_factor_points_sum_to_score(monkeypatch):
    """Every scored flag must appear as a factor; points must sum to risk_score."""
    resp = copy.deepcopy(SAMPLE_ADVISOR_RESPONSE)
    for flag in srv._RISK_WEIGHTS:
        resp["advisor"][flag] = True
    patch_api(monkeypatch, resp)
    result = srv.get_risk_profile(123456)
    assert len(result["risk_factors"]) == len(srv._RISK_WEIGHTS)
    assert sum(f["points"] for f in result["risk_factors"]) == result["risk_score"]


def test_search_has_disclosures_uses_all_flags(monkeypatch):
    resp = {"advisors": [dict(SAMPLE_ADVISOR_RESPONSE["advisor"],
                              has_customer_complaint=False)]}  # only civil_judicial
    patch_api(monkeypatch, resp)
    result = srv.search_advisors(name="Smith")
    assert result["results"][0]["has_disclosures"] is True


def test_search_full_name_splits_into_first_and_last(monkeypatch):
    calls = patch_api(monkeypatch, {"advisors": []})
    srv.search_advisors(name="Joseph Montgomery")
    assert "first_name=Joseph" in calls[0] and "last_name=Montgomery" in calls[0]


def test_search_limit_is_clamped(monkeypatch):
    calls = patch_api(monkeypatch, {"advisors": []})
    srv.search_advisors(name="Smith", limit=500)
    assert "limit=100" in calls[0]
    srv.search_advisors(name="Smith", limit=-5)
    assert "limit=1" in calls[1]


def test_lookup_not_found_returns_official_links(monkeypatch):
    patch_api(monkeypatch, {"error": "not found"})
    result = srv.lookup_advisor(999999)
    assert result["found"] is False
    assert "sec_iapd" in result["try_these"]
