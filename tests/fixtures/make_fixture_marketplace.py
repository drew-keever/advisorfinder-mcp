#!/usr/bin/env python3
"""
make_fixture_marketplace.py — Build a small AdFi marketplace-advisor xlsx export
(the raw, UN-sanitized shape read by scripts/sanitize_marketplace.py in the
firm-intelligence repo) that feeds the REAL build_mcp_public_db.py --marketplace
flag to produce tests/fixtures/mcp_public.db's optional marketplace_advisors table.

Sibling to make_fixture_source.py rather than an extension of it: this file's
output is a completely separate input to the export pipeline (the marketplace
xlsx, not the firms.db-shaped regulatory source), so it gets its own generator
and its own CLI. See tests/fixtures/README.md for the full three-command
regeneration sequence (source db -> marketplace xlsx -> real build script).

Header mirrors the REAL AdFi export's raw column set (see
firm-intelligence-worktrees/marketplace/tests/conftest.py::MARKETPLACE_REAL_HEADER)
-- deliberately wider than sanitize_marketplace.MARKETPLACE_COLUMNS, including
several never-export columns (email, phoneNumber, cognitoUsername, calendlyUrl)
so the whitelist projection has something real to prove itself against.

Three rows, three cases:
  - "qv3Y1g3y" / crd 1000002 (JOHN Q SMITH — already has bio/content/designations
    in make_fixture_source.py's base fixture): RICH profile — prompts, pricing
    (via pricingV2 fallback), aum, education, all fields populated. Short-format
    professionalId.
  - "3fa85f64-5717-4562-b3fc-2c963f66afa6" / crd 1000003 (MARY JONES): MINIMAL
    profile — only displayName/companyName/city/state/memberSince populated,
    everything else NULL/empty. UUID-format professionalId.
  - "excludedAdv1" / crd 1000005 (PATRICK MCDONALD III): populated like a real
    row, but its professionalId does NOT appear in tests/fixtures/marketplace_sitemap.xml
    -- proves the sitemap-scoping gate (sanitize_marketplace.sanitize()) drops it
    from marketplace_advisors even though it's a well-formed, non-PII-violating,
    crd-valid row.

None of the three rows' whitelisted (published) field values contain anything
that would trip sanitize_marketplace's PII value-scan (email/phone/cognito/calendly
patterns, 10+ consecutive digits) -- the never-export-only columns (email,
phoneNumber, cognitoUsername, calendlyUrl) carry realistic-looking PII on purpose,
to prove those columns are dropped by the whitelist projection rather than by the
value-scan.

Usage:
    python3 tests/fixtures/make_fixture_marketplace.py /path/to/fixture_marketplace.xlsx
"""
import json
import sys
from pathlib import Path

import openpyxl

# Raw AdFi export header, copied verbatim from
# firm-intelligence-worktrees/marketplace/tests/conftest.py::MARKETPLACE_REAL_HEADER
# (56 columns) -- kept in lockstep by hand since this is a standalone fixture
# generator that does not import across repos.
REAL_HEADER = [
    "professionalId", "accountEnabled", "adtrax", "adtraxBioExpiredAt", "adtraxBioExpiresAt",
    "advisorELID", "advisorPrompts", "advisorWebsiteURL", "agreedToBetaAgreement", "allowedStates",
    "appStatus", "aum", "averageAccountSize", "bio", "bioVideoLink", "calendlyUrl", "calendlyUser",
    "city", "clientDescription", "clientNumber", "cognitoUsername", "companyName", "crd",
    "createdDate", "credentials", "disclosureText", "displayName", "education", "email",
    "entityType", "finraMatch", "finraUrl", "firstName", "hasInsuranceLicense", "jobHistory",
    "jobTitle", "lastName", "lastUpdatedDate", "linkedInURL", "memberSince", "minAccountSize",
    "numberOfLocations", "paidAdvisor", "phoneNumber", "pricing", "pricingV2",
    "profileCompletenessScore", "quickFacts", "rsnipDisclosure", "secUrl", "state",
    "supplementalZipCodes", "twitterURL", "virtualMeetingsOffered", "yearsOfExperience", "zipCode",
]
assert len(REAL_HEADER) == 56


def _prompts(*responses: str) -> str:
    """DynamoDB-JSON-shaped advisorPrompts blob, matching what
    sanitize_marketplace._parse_prompts() expects: a JSON list of
    {"M": {"response": {"S": "..."}}} entries."""
    return json.dumps([
        {"M": {"response": {"S": text}, "promptId": {"N": str(i)}}}
        for i, text in enumerate(responses)
    ])


# ── ROW 1: rich profile, short-format professionalId, crd == 1000002 ────────
ROW_RICH = {
    "professionalId": "qv3Y1g3y", "crd": "1000002",
    "displayName": "John Q. Smith", "companyName": "Alpha Wealth LLC",
    "jobTitle": "Senior Financial Advisor", "city": "New York", "state": "NY",
    "bio": "Helps growing families and small-business owners build durable retirement plans.",
    "credentials": "CFP, CFA",
    "clientDescription": "Works best with tech professionals, small-business owners, and pre-retirees.",
    "quickFacts": "Fee-only; flat annual planning fee available; virtual meetings welcome",
    "pricing": "", "pricingV2": "$3,000/yr flat planning fee, or $250/hr for one-off consultations",
    "minAccountSize": 250000, "yearsOfExperience": 15, "virtualMeetingsOffered": "Yes",
    "allowedStates": "NY,NJ,CT", "memberSince": "2022-03-01",
    "advisorWebsiteURL": "https://johnsmithadvisors.example.com",
    "linkedInURL": "https://linkedin.com/in/johnqsmith",
    "twitterURL": "https://twitter.com/johnqsmith",
    "bioVideoLink": "https://video.example.com/johnsmith-intro",
    "education": "MBA, NYU Stern School of Business",
    "aum": 45_000_000, "clientNumber": 120,
    "advisorPrompts": _prompts(
        "I love helping clients retire early and confidently.",
        "My biggest professional achievement is growing my practice threefold in five years.",
    ),
    # never-export-only columns -- must NOT survive the whitelist projection.
    "email": "john.smith@example.com", "phoneNumber": "212-555-0101",
    "cognitoUsername": "cognito|abc123def456", "calendlyUrl": "https://calendly.com/johnsmith",
    "firstName": "John", "lastName": "Smith", "accountEnabled": "Y", "paidAdvisor": "Y",
}

# ── ROW 2: minimal profile, UUID-format professionalId, crd == 1000003 ──────
ROW_MINIMAL = {
    "professionalId": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "crd": "1000003",
    "displayName": "Mary Jones", "companyName": "Beta Advisors Inc",
    "jobTitle": None, "city": "Boston", "state": "MA",
    "bio": None, "credentials": None, "clientDescription": None, "quickFacts": None,
    "pricing": None, "pricingV2": None, "minAccountSize": None, "yearsOfExperience": None,
    "virtualMeetingsOffered": None, "allowedStates": None, "memberSince": "2023-06-15",
    "advisorWebsiteURL": None, "linkedInURL": None, "twitterURL": None, "bioVideoLink": None,
    "education": None, "aum": None, "clientNumber": None, "advisorPrompts": None,
    "email": None, "phoneNumber": None, "cognitoUsername": None, "calendlyUrl": None,
    "firstName": "Mary", "lastName": "Jones",
}

# ── ROW 3: well-formed, crd-valid, non-PII-violating -- but NOT in the sitemap ──
ROW_NOT_IN_SITEMAP = {
    "professionalId": "excludedAdv1", "crd": "1000005",
    "displayName": "Patrick McDonald", "companyName": "Alpha Wealth LLC",
    "jobTitle": "Financial Advisor", "city": "New York", "state": "NY",
    "bio": "Should never appear in marketplace_advisors -- not a sitemap-listed advisor.",
    "credentials": "CFP", "clientDescription": "Individuals and families",
    "quickFacts": "Virtual meetings available", "pricing": "1% of AUM", "pricingV2": None,
    "minAccountSize": 100000, "yearsOfExperience": 8, "virtualMeetingsOffered": "Yes",
    "allowedStates": "NY,MA", "memberSince": "2021-01-01",
    "advisorWebsiteURL": "https://patrickmcdonald.example.com",
    "linkedInURL": "https://linkedin.com/in/patrickmcdonald", "twitterURL": None,
    "bioVideoLink": None, "education": "BA, Boston College", "aum": 8_000_000, "clientNumber": 30,
    "advisorPrompts": _prompts("I enjoy long-term financial planning."),
    "email": "patrick.mcdonald@example.com", "phoneNumber": "617-555-0199",
    "cognitoUsername": "cognito|xyz999", "calendlyUrl": "https://calendly.com/patrickmcdonald",
    "firstName": "Patrick", "lastName": "McDonald",
}

ROWS = [ROW_RICH, ROW_MINIMAL, ROW_NOT_IN_SITEMAP]


def build_marketplace_xlsx(path) -> Path:
    path = Path(path)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet2"  # sanitize_marketplace._read_export_rows() looks for "Sheet2" first
    ws.append(REAL_HEADER)
    for row in ROWS:
        ws.append([row.get(col) for col in REAL_HEADER])
    wb.save(path)
    return path


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: make_fixture_marketplace.py <output-xlsx-path>", file=sys.stderr)
        return 2
    build_marketplace_xlsx(Path(argv[0]))
    print(f"OK: fixture marketplace xlsx written to {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
