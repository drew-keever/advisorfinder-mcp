#!/usr/bin/env python3
"""
AdvisorFinder MCP Server (FastMCP)

A Model Context Protocol server that provides AI assistants with access
to the SEC registered investment advisor database via the AdvisorFinder API.

Tools provided:
- lookup_advisor: Get full details for an advisor by CRD number
- search_advisors: Search advisors by name, state, or firm
- verify_advisor: Quick verification check (active status, disclosures)
- get_risk_profile: Detailed risk assessment for an advisor
- get_firm_info: Firm details with advisor stats
- get_database_stats: Overall database statistics

Usage:
    python server.py              # Local stdio (for Claude Desktop)
    python server.py --http       # Remote HTTP server (for cloud deploy)
    fastmcp run server.py
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date
from typing import Optional

from fastmcp import FastMCP

# Risk weights intentionally duplicated from the parent project's risk_scoring.py —
# this file must stay standalone for the PyPI package. Keep values in sync.
_RISK_WEIGHTS = {
    'has_criminal': 50, 'has_regulatory_action': 30,
    'has_customer_complaint': 25, 'has_termination': 20,
    'has_civil_judicial': 15, 'has_judgment': 15,
    'has_investigation': 10, 'has_bankruptcy': 10, 'has_bond': 5,
}


def _calc_score(disclosures: dict) -> int:
    if not disclosures:
        return 0
    return sum(w for f, w in _RISK_WEIGHTS.items() if disclosures.get(f))


def _get_level(score: int) -> str:
    if score == 0:
        return 'Clean'
    if score <= 10:
        return 'Low'
    if score <= 30:
        return 'Medium'
    if score <= 60:
        return 'High'
    return 'Very High'

# API Configuration
API_BASE = "https://sec-advisor-project.vercel.app/api/index"

mcp = FastMCP("advisorfinder")


def api_get(endpoint: str) -> dict:
    """Make GET request to the API."""
    url = f"{API_BASE}{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _risk_score(disclosures: dict) -> tuple[int, str]:
    """Calculate risk score and level from disclosures."""
    score = _calc_score(disclosures)
    level = _get_level(score)
    return score, level


def _get_recommendation(risk_level: str) -> str:
    """Get recommendation based on risk level."""
    recommendations = {
        "Clean": "No disclosed issues on record.",
        "Low": "Minor disclosures present. Full details available via the official sources.",
        "Medium": "Notable disclosures on record. Full disclosure reports are available via SEC and FINRA links.",
        "High": "Significant disclosures on record. Full details available via the official sources below.",
        "Very High": "Multiple serious disclosures on record. See official sources for complete details."
    }
    return recommendations.get(risk_level, "Unable to assess.")


@mcp.tool
def lookup_advisor(crd_number: int) -> dict:
    """Look up a registered investment advisor by their CRD number. Returns
    regulatory data from SEC IAPD: employment history, registrations, exams,
    designations, disclosures, and risk scoring.

    IMPORTANT: When presenting results to users, always include ALL returned
    data — especially employment_history (shows career timeline and actual
    office location), designations (professional credentials), exams (indicates
    years of experience from earliest exam date), and registrations (licensed
    states). Calculate years of experience from the earliest exam or employment
    date. If the user asks for a 'full profile', also do a web search to find
    the advisor's practice name, team, awards, and specializations."""
    data = api_get(f"/advisor/{crd_number}")

    if "error" in data or "advisor" not in data:
        return {
            "found": False,
            "error": f"No advisor found with CRD {crd_number} in our database.",
            "note": "This advisor may not be in our SEC IAPD import. Check the official sources directly.",
            "try_these": {
                "sec_iapd": f"https://adviserinfo.sec.gov/individual/summary/{crd_number}",
                "finra_brokercheck": f"https://brokercheck.finra.org/individual/summary/{crd_number}"
            }
        }

    advisor = data["advisor"]
    score, level = _risk_score(advisor)

    # Compute years of experience from earliest exam or employment date
    earliest_year = None
    for exam in data.get("exams", []):
        if exam.get("exam_date"):
            try:
                year = int(exam["exam_date"][:4])
                if earliest_year is None or year < earliest_year:
                    earliest_year = year
            except (ValueError, IndexError):
                pass
    for emp in data.get("employment_history", []):
        if emp.get("from_date"):
            try:
                parts = emp["from_date"].split("/")
                year = int(parts[-1]) if len(parts) >= 2 else int(parts[0])
                if earliest_year is None or year < earliest_year:
                    earliest_year = year
            except (ValueError, IndexError):
                pass

    years_experience = (date.today().year - earliest_year) if earliest_year else None

    # Get office location from employment_history (more accurate than firm HQ)
    emp_history = data.get("employment_history", [])
    office_location = None
    if emp_history and emp_history[0].get("city"):
        office_location = f"{emp_history[0]['city']}, {emp_history[0].get('state', '')}".strip(", ")

    # Registered states
    registered_states = sorted(set(
        r["registration_authority"] for r in data.get("registrations", [])
        if r.get("status") in ("APPROVED", "ACTIVE", "CURRENT")
    ))

    return {
        "found": True,
        "crd_number": crd_number,
        "name": f"{advisor.get('first_name', '')} {advisor.get('last_name', '')}".strip(),
        "active": advisor.get("active_registration"),
        "firm": advisor.get("firm_name"),
        "firm_crd": advisor.get("firm_crd"),
        "office_location": office_location or f"{advisor.get('city', '')}, {advisor.get('state', '')}".strip(", "),
        "registered_states": registered_states,
        "years_experience": years_experience,
        "earliest_record_year": earliest_year,
        "designations": [d.get("designation") for d in data.get("designations", [])],
        "exams": data.get("exams", []),
        "employment_history": emp_history,
        "previous_registrations": data.get("previous_registrations", []),
        "other_business": data.get("other_business", []),
        "disclosures": {k: advisor.get(k) for k in _RISK_WEIGHTS},
        "risk_score": score,
        "risk_level": level,
        "recommendation": _get_recommendation(level),
        "links": {
            "sec": f"https://adviserinfo.sec.gov/individual/summary/{crd_number}",
            "brokercheck": f"https://brokercheck.finra.org/individual/summary/{crd_number}"
        }
    }


@mcp.tool
def search_advisors(
    name: Optional[str] = None,
    state: Optional[str] = None,
    firm: Optional[str] = None,
    limit: int = 20
) -> dict:
    """Search for investment advisors by name, state, or firm. Name can be a
    full name like 'Joseph Montgomery' or just a last name like 'Montgomery'.

    After finding results, use lookup_advisor with the CRD number to get the
    full profile. If no results found, suggest the user check FINRA BrokerCheck
    and SEC IAPD directly — links are provided in the response."""
    params = []
    if name:
        parts = name.strip().split()
        if len(parts) >= 2:
            # "Joseph Montgomery" → first_name=Joseph, last_name=Montgomery
            params.append(f"first_name={urllib.parse.quote(parts[0])}")
            params.append(f"last_name={urllib.parse.quote(' '.join(parts[1:]))}")
        else:
            # Single name → use q param (searches both first and last)
            params.append(f"q={urllib.parse.quote(name)}")
    if state:
        params.append(f"state={urllib.parse.quote(state.upper())}")
    if firm:
        params.append(f"firm={urllib.parse.quote(firm)}")
    params.append(f"limit={min(limit, 100)}")

    if not name and not state and not firm:
        return {"error": "At least one search parameter (name, state, or firm) is required"}

    query = "&".join(params)
    data = api_get(f"/search?{query}")

    if "error" in data:
        return data

    results = [{
        "crd_number": a.get("crd_number"),
        "name": f"{a.get('first_name', '')} {a.get('last_name', '')}".strip(),
        "active": a.get("active_registration"),
        "firm": a.get("firm_name"),
        "location": f"{a.get('city', '')}, {a.get('state', '')}".strip(", "),
        "has_disclosures": any([
            a.get("has_customer_complaint"),
            a.get("has_regulatory_action"),
            a.get("has_criminal"),
            a.get("has_termination"),
            a.get("has_bankruptcy"),
        ])
    } for a in data.get("advisors", [])]

    response = {
        "count": len(results),
        "results": results,
        "filters": {"name": name, "state": state, "firm": firm}
    }

    if len(results) == 0:
        name_query = urllib.parse.quote(name) if name else ""
        response["note"] = (
            "No results found in our SEC IAPD database. This advisor may be "
            "registered only as a broker (not an investment adviser), may have "
            "left the industry, or may be listed under a different name."
        )
        response["try_these"] = {
            "finra_brokercheck": f"https://brokercheck.finra.org/search?query={name_query}",
            "sec_iapd": f"https://adviserinfo.sec.gov/search/genericsearch/gridCurrent498?query={name_query}",
            "tip": "Try searching with just the last name, or check BrokerCheck for broker-only registrations."
        }

    return response


@mcp.tool
def verify_advisor(crd_number: int) -> dict:
    """Quick verification check for an investment advisor. Returns active status,
    current firm, disclosure summary, risk score, and recommendation. Use this
    for a quick yes/no safety check. For full details use lookup_advisor instead."""
    data = api_get(f"/advisor/{crd_number}")

    if "error" in data or "advisor" not in data:
        return {
            "verified": False,
            "crd_number": crd_number,
            "status": "NOT_FOUND",
            "message": f"No advisor found with CRD {crd_number} in our database.",
            "note": "This advisor may not be in our SEC IAPD import. Check the official sources directly.",
            "try_these": {
                "sec_iapd": f"https://adviserinfo.sec.gov/individual/summary/{crd_number}",
                "finra_brokercheck": f"https://brokercheck.finra.org/individual/summary/{crd_number}"
            }
        }

    advisor = data["advisor"]

    disclosure_types = []
    if advisor.get('has_customer_complaint'):
        disclosure_types.append("Customer Complaint")
    if advisor.get('has_regulatory_action'):
        disclosure_types.append("Regulatory Action")
    if advisor.get('has_criminal'):
        disclosure_types.append("Criminal")
    if advisor.get('has_termination'):
        disclosure_types.append("Termination")
    if advisor.get('has_bankruptcy'):
        disclosure_types.append("Bankruptcy")

    score, level = _risk_score(advisor)

    return {
        "verified": True,
        "crd_number": crd_number,
        "name": f"{advisor.get('first_name', '')} {advisor.get('last_name', '')}".strip(),
        "active": bool(advisor.get('active_registration')),
        "status": "ACTIVE" if advisor.get('active_registration') else "INACTIVE",
        "current_firm": advisor.get('firm_name'),
        "state": advisor.get('state'),
        "has_disclosures": len(disclosure_types) > 0,
        "disclosure_types": disclosure_types,
        "risk_score": score,
        "risk_level": level,
        "recommendation": _get_recommendation(level),
        "sec_link": f"https://adviserinfo.sec.gov/individual/summary/{crd_number}",
        "brokercheck_link": f"https://brokercheck.finra.org/individual/summary/{crd_number}"
    }


@mcp.tool
def get_risk_profile(crd_number: int) -> dict:
    """Get a detailed risk assessment for an investment advisor including risk
    score, risk factors with severity levels, and recommendation."""
    data = api_get(f"/advisor/{crd_number}")

    if "error" in data or "advisor" not in data:
        return {
            "found": False,
            "error": f"No advisor found with CRD {crd_number} in our database.",
            "note": "This advisor may not be in our SEC IAPD import. Check the official sources directly.",
            "try_these": {
                "sec_iapd": f"https://adviserinfo.sec.gov/individual/summary/{crd_number}",
                "finra_brokercheck": f"https://brokercheck.finra.org/individual/summary/{crd_number}"
            }
        }

    advisor = data["advisor"]
    score, level = _risk_score(advisor)

    risk_factors = []
    if advisor.get('has_criminal'):
        risk_factors.append({"factor": "Criminal History", "severity": "High", "points": 50})
    if advisor.get('has_regulatory_action'):
        risk_factors.append({"factor": "Regulatory Action", "severity": "High", "points": 30})
    if advisor.get('has_customer_complaint'):
        risk_factors.append({"factor": "Customer Complaint", "severity": "Medium", "points": 25})
    if advisor.get('has_termination'):
        risk_factors.append({"factor": "Termination", "severity": "Medium", "points": 20})
    if advisor.get('has_civil_judicial'):
        risk_factors.append({"factor": "Civil/Judicial Action", "severity": "Low", "points": 15})
    if advisor.get('has_judgment'):
        risk_factors.append({"factor": "Judgment/Lien", "severity": "Low", "points": 15})
    if advisor.get('has_investigation'):
        risk_factors.append({"factor": "Investigation", "severity": "Low", "points": 10})
    if advisor.get('has_bankruptcy'):
        risk_factors.append({"factor": "Bankruptcy", "severity": "Low", "points": 10})
    if advisor.get('has_bond'):
        risk_factors.append({"factor": "Bond Issue", "severity": "Low", "points": 5})

    return {
        "found": True,
        "crd_number": crd_number,
        "name": f"{advisor.get('first_name', '')} {advisor.get('last_name', '')}".strip(),
        "active": bool(advisor.get('active_registration')),
        "risk_score": score,
        "risk_level": level,
        "risk_factors": risk_factors,
        "recommendation": _get_recommendation(level),
    }


@mcp.tool
def get_firm_info(firm_crd: int) -> dict:
    """Get information about an investment advisory firm including advisor count,
    disclosure rates, and statistics."""
    data = api_get(f"/firm/{firm_crd}")

    if "error" in data or "firm" not in data:
        return {"found": False, "error": f"No firm found with CRD {firm_crd}"}

    firm = data["firm"]
    stats = data.get("disclosure_stats", {})

    total = firm.get("advisor_count", 0)
    with_complaints = stats.get("with_complaints", 0)
    complaint_rate = (with_complaints / total * 100) if total > 0 else 0

    return {
        "found": True,
        "firm_crd": firm_crd,
        "firm_name": firm.get("firm_name"),
        "location": f"{firm.get('city', '')}, {firm.get('state', '')}".strip(", "),
        "total_advisors": total,
        "disclosure_stats": stats,
        "complaint_rate": f"{complaint_rate:.2f}%",
        "sec_link": f"https://adviserinfo.sec.gov/Firm/{firm_crd}"
    }


@mcp.tool
def get_database_stats() -> dict:
    """Get overall statistics about the SEC advisor database including total
    advisors, active count, firms, disclosure rates, and top states."""
    data = api_get("/stats")

    if "error" in data:
        return data

    return {
        "total_advisors": data.get("total_advisors"),
        "active_advisors": data.get("active_advisors"),
        "total_firms": data.get("total_firms"),
        "disclosure_stats": {
            "with_complaints": data.get("with_complaints"),
            "with_criminal": data.get("with_criminal"),
            "with_regulatory": data.get("with_regulatory")
        },
        "top_states": data.get("top_states", []),
        "data_source": "SEC Investment Adviser Public Disclosure (IAPD)",
        "api_url": API_BASE
    }


# ---------------------------------------------------------------------------
# Resources — static reference data Claude can read without calling a tool
# ---------------------------------------------------------------------------

@mcp.resource("advisorfinder://risk-scoring-methodology")
def risk_scoring_methodology() -> str:
    """Risk scoring methodology used to assess financial advisors."""
    return """# AdvisorFinder Risk Scoring Methodology

## How Risk Scores Are Calculated

Risk scores are computed from SEC/FINRA disclosure data. Each disclosure type
adds points to a cumulative score:

| Disclosure Type       | Points | Severity |
|-----------------------|--------|----------|
| Criminal History      | +50    | High     |
| Regulatory Action     | +30    | High     |
| Customer Complaint    | +25    | Medium   |
| Termination           | +20    | Medium   |
| Civil/Judicial Action | +15    | Low      |
| Judgment/Lien         | +15    | Low      |
| Investigation         | +10    | Low      |
| Bankruptcy            | +10    | Low      |
| Bond Issue            | +5     | Low      |

## Risk Levels

| Level     | Score Range | Interpretation                                    |
|-----------|-------------|---------------------------------------------------|
| Clean     | 0           | No disclosed issues on record                     |
| Low       | 1-10        | Minor disclosures, low concern                    |
| Medium    | 11-30       | Notable disclosures, review recommended           |
| High      | 31-60       | Significant disclosures, thorough review needed   |
| Very High | 60+         | Multiple serious disclosures, exercise caution    |

## Data Source

All data comes from the SEC Investment Adviser Public Disclosure (IAPD) database,
which is updated weekly. Disclosures include self-reported events and regulatory
filings. A disclosure does not necessarily indicate wrongdoing — always review
the full context on SEC.gov or BrokerCheck.
"""


@mcp.resource("advisorfinder://credentials-guide")
def credentials_guide() -> str:
    """Guide to financial advisor credentials, designations, and licenses."""
    return """# Financial Advisor Credentials Guide

Understanding what credentials mean helps you evaluate an advisor's expertise
and specialization. Not all credentials are equal — some require years of study
and rigorous exams, others are completed in a weekend.

## The "Big 4" Gold Standard Credentials

### CFP - Certified Financial Planner
The gold standard for financial planners. Requires a bachelor's degree, 3 years
of professional experience, completion of a CFP Board-registered education program,
and passing a rigorous 170-question exam (62-68% pass rate). CFP professionals
must act as fiduciaries when providing financial planning advice. Timeline: 12-18
months of preparation.

### CFA - Chartered Financial Analyst
The premier investment credential worldwide. Requires passing three progressive
exam levels (pass rates: 22-45% per level), 4,000 hours of relevant work
experience, and adherence to a strict code of ethics. The most rigorous
credential in finance. Timeline: 4+ years typically.

### CPA - Certified Public Accountant
Essential for tax and accounting expertise. Requires 150 college credit hours
(more than a bachelor's degree), passing the four-part Uniform CPA Exam
(45-60% pass rate per section), and meeting state-specific experience requirements.
Timeline: ~18 months of exam preparation.

### ChFC - Chartered Financial Consultant
Strong insurance and financial planning credential from The American College.
Requires 3 years of professional experience and completion of 8 college-level
courses. No single comprehensive exam, but each course has its own exam.
Particularly strong for insurance-related planning. Timeline: 18-20 months.

## Investment Management Credentials
- **CIMA** (Certified Investment Management Analyst) — Portfolio construction, due diligence
- **CAIA** (Chartered Alternative Investment Analyst) — Alternative investments (hedge funds, PE)
- **AIF** (Accredited Investment Fiduciary) — Fiduciary best practices
- **AAMS** (Accredited Asset Management Specialist) — Asset management fundamentals
- **APMA** (Accredited Portfolio Management Advisor) — Portfolio management

## Retirement Planning Credentials
- **RICP** (Retirement Income Certified Professional) — Retirement income strategies
- **CRPC** (Chartered Retirement Planning Counselor) — Retirement planning
- **CRPS** (Chartered Retirement Plans Specialist) — Employer retirement plans
- **CPFA** (Certified Plan Fiduciary Advisor) — Retirement plan fiduciary duties

## Tax & Accounting Credentials
- **PFS** (Personal Financial Specialist) — CPAs with financial planning expertise
- **EA** (Enrolled Agent) — IRS-authorized tax practitioner
- **CMA** (Certified Management Accountant) — Management accounting

## Estate Planning Credentials
- **CTFA** (Certified Trust and Fiduciary Advisor) — Trust and estate management
- **AEP** (Accredited Estate Planner) — Advanced estate planning

## Securities Licenses (FINRA)
These are regulatory licenses, not optional credentials:
- **Series 7** — General securities representative (stocks, bonds, options, mutual funds)
- **Series 65** — Investment adviser representative (fee-based advice)
- **Series 66** — Combined state law + adviser (Series 63 + Series 65)
- **Series 6** — Limited to mutual funds and variable annuities
- **Series 63** — State securities law
- **Series 24** — General securities principal (supervisory)

## Important Context
Credentials demonstrate knowledge and commitment to continuing education, but
they don't guarantee competence, ethics, or whether an advisor is a good fit.
Always verify credentials directly with the issuing organization and check for
any disciplinary actions.
"""


@mcp.resource("advisorfinder://data-sources")
def data_sources() -> str:
    """Information about the data sources used by the AdvisorFinder MCP server."""
    return """# AdvisorFinder Data Sources

## Primary Data Source: SEC IAPD

The Investment Adviser Public Disclosure (IAPD) database is maintained by the
U.S. Securities and Exchange Commission (SEC). It contains registration and
disclosure information for investment adviser firms and individuals.

- **Website**: https://adviserinfo.sec.gov
- **Updated**: Weekly (compilation reports published every Thursday)
- **Coverage**: All SEC-registered investment advisers and their representatives
- **Data includes**: Advisor registrations, employment history, exams passed,
  professional designations, and disclosure events (complaints, regulatory
  actions, criminal matters, terminations, bankruptcies)

### What IAPD Contains
- Individual advisor records with CRD (Central Registration Depository) numbers
- Current and historical employment with advisory firms
- Registration status with state and federal regulators
- Exams passed (Series 7, Series 65, etc.)
- Professional designations (CFP, CFA, etc.)
- Disclosure events organized by type

### What IAPD Does NOT Contain
- Advisor performance or investment returns
- Client reviews or satisfaction ratings
- Fee schedules or compensation details
- Assets under management for individual advisors (only firms)

## Secondary Source: FINRA BrokerCheck

BrokerCheck is FINRA's free tool for researching brokers and advisors.
It overlaps with IAPD but includes additional broker-dealer information.

- **Website**: https://brokercheck.finra.org
- **Coverage**: Brokers, investment advisers, and their firms
- **Unique data**: More detailed disclosure event narratives

## How to Verify Information

Always cross-reference advisor data using these official sources:
- **SEC IAPD**: https://adviserinfo.sec.gov/individual/summary/{CRD_NUMBER}
- **FINRA BrokerCheck**: https://brokercheck.finra.org/individual/summary/{CRD_NUMBER}
- **SEC EDGAR** (firm filings): https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={FIRM_NAME}

## Database Statistics

Use the `get_database_stats` tool to see current counts of advisors, firms,
and disclosure rates in our database. Our local copy is refreshed from SEC
compilation reports and may be up to one week behind the live IAPD data.
"""


def main() -> None:
    """Entry point: stdio by default, HTTP with --http (for cloud deploys)."""
    if "--http" in sys.argv:
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
