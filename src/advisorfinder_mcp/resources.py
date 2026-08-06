"""Static/semi-static reference resources for advisorfinder_mcp.

Three resources: credentials-guide (carried over verbatim from v1),
data-sources (updated for the new local-DB backend), and
coverage-and-limitations (new — replaces v1's removed risk-scoring-methodology
resource, which scored disclosures numerically; this server deliberately does
not do that anywhere).

register(mcp) attaches these to a FastMCP instance. The functions are plain
module-level functions (mcp.resource(uri)(fn) returns fn unchanged on
fastmcp 3.4.6, same as @mcp.tool) so tests can call them directly.
"""
from . import db


def credentials_guide() -> str:
    """Guide to financial advisor credentials, designations, and licenses."""
    return _CREDENTIALS_GUIDE


def data_sources() -> str:
    """Information about the data sources behind advisorfinder_mcp."""
    meta = db.get_meta()
    return f"""# AdvisorFinder Data Sources

## Upstream sources

This server republishes structured registration facts derived from two
public regulatory sources:

- **SEC IAPD** (Investment Adviser Public Disclosure) — https://adviserinfo.sec.gov
  Registration and disclosure information for SEC-registered investment
  adviser firms and their representatives.
- **FINRA BrokerCheck** — https://brokercheck.finra.org
  Overlapping coverage plus more detailed disclosure event narratives.

## What we store vs. what we don't

We store: registrations, employment history, exams passed, self-reported
professional designations, and a four-state disclosure STATUS (none
reported / disclosed-no-detail / disclosed-with-detail / unknown). We do
NOT store disclosure event detail (allegations, settlement amounts,
narratives) — disclosure detail is deliberately not included here. Always
check FINRA BrokerCheck / SEC IAPD directly for the full disclosure record
before making any decision.

## Data vintage

Our current copy was refreshed as of:
- Advisor (IAR) data: {meta.get('ia_reps_as_of')}
- Firm data: {meta.get('firms_as_of')}
- Individuals bulk file: {meta.get('individuals_as_of')}

Use the `get_database_stats` tool for live counts and vintages.

## How to verify

- SEC IAPD (individual): https://adviserinfo.sec.gov/individual/summary/{{CRD}}
- SEC IAPD (firm): https://adviserinfo.sec.gov/firm/summary/{{CRD}}
- FINRA BrokerCheck (individual): https://brokercheck.finra.org/individual/summary/{{CRD}}
- FINRA BrokerCheck (firm): https://brokercheck.finra.org/firm/summary/{{CRD}}
"""


def coverage_and_limitations() -> str:
    """What advisorfinder_mcp covers, what it doesn't, and what an empty
    result means (scope vs. absence)."""
    meta = db.get_meta()
    return f"""# Coverage and Limitations

## What's covered

- SEC-registered investment adviser firms (not state-registered-only firms,
  with a thin exception — see below).
- Active investment adviser representatives (IARs) linked to at least one
  covered firm.
- Registration facts: employment history, exams, registered states,
  self-reported professional designations, and four-state disclosure STATUS.

## What's NOT covered

- **State-registered-only firms**: most are not in our roster at all. A
  handful appear (linked via an active IAR who is also tied to a covered
  firm), but treat any state-registered firm's advisor roster as incomplete
  by default.
- **Disclosure detail**: we store disclosure STATUS, never the underlying
  event narratives, allegations, or settlement amounts. Always check FINRA
  BrokerCheck / SEC IAPD for the full record.
- **Verified designations**: every professional designation we show is
  self-reported. None of it is independently verified against the issuing
  body (CFP Board, CFA Institute, etc.) — check directly with them if a
  credential matters to your decision.
- **A numeric risk score**: intentionally absent. We surface the underlying
  facts (registration status, disclosure status) so you can judge for
  yourself; we do not compress them into a single number.

## Data vintage

- Advisor (IAR) data: {meta.get('ia_reps_as_of')}
- Firm data: {meta.get('firms_as_of')}
- Individuals bulk file: {meta.get('individuals_as_of')}

## What an empty result means

An empty search result, or a firm's advisor roster showing zero reps, is
usually about our COVERAGE SCOPE, not proof that nobody works there or that
an advisor doesn't exist. State-registered firms and non-Active
registrations are the most common reasons a real advisor or firm won't
show up here — always confirm via SEC IAPD / FINRA BrokerCheck directly.
"""


def register(mcp) -> None:
    mcp.resource("advisorfinder://credentials-guide")(credentials_guide)
    mcp.resource("advisorfinder://data-sources")(data_sources)
    mcp.resource("advisorfinder://coverage-and-limitations")(coverage_and_limitations)


# Carried over verbatim from v1 advisorfinder_mcp.py's credentials_guide()
# resource (see git history / task-1 predecessor file before its deletion).
_CREDENTIALS_GUIDE = """# Financial Advisor Credentials Guide

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
