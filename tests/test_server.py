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
