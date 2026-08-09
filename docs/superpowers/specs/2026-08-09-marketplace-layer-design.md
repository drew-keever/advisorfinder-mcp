# Marketplace layer for the AdvisorFinder MCP — design

*Approved in brainstorming 2026-08-08/09 with Drew. Implementation not yet scheduled — this spec is ready to hand to a writing-plans session when the go signal comes.*

## Purpose

Add AdvisorFinder's own marketplace advisors (~294 public profiles, all CRD-verified) to the MCP as a clearly-labeled discovery layer with per-advisor deep links — completing the growth engine (MCP answers → bookable advisor profiles) without compromising the tool's neutral regulatory-vetting credibility.

## Non-negotiable principles (Drew-approved)

1. **No ranking changes.** Marketplace membership never affects order or inclusion in the regulatory search tools. Enrichment is a labeled affiliation, not a boost.
2. **No PII ships.** Emails, phone numbers, user IDs, internal account fields are excluded structurally (whitelist + audit + value-scan), same philosophy as the never-export audit in `build_mcp_public_db.py`.
3. **Publish exactly what's already public**: only advisors present in `advisorfinder.com/sitemap.xml` (294 of 358 export rows as of 2026-08-08). Falling out of the sitemap removes an advisor on the next refresh.
4. **Snapshot architecture unchanged**: no live database or API dependencies at serve time. Data enters via Drew's periodic AdFi db export (monthly-ish / 2× per quarter).

## Architecture (Option A — single artifact)

```
AdFi db export (.xlsx, Drew drops at known path on the mini)
        │
sanitize_marketplace.py  ── fetches sitemap.xml at build time (canonical URLs)
        │  whitelist + PII value-scan + CRD-validity audit
        ▼
marketplace_advisors table ──► baked into mcp_public.db by build_mcp_public_db.py
                                (same manifest / audit / schema_version / refresh command)
```

- `sanitize_marketplace.py` lives beside `build_mcp_public_db.py` in firm-intelligence's `scripts/` (sanctioned export path). `build_mcp_public_db.py` gains `--marketplace <xlsx>` (optional — export builds fine without it; manifest gate records `marketplaceIncluded`).
- Server `SCHEMA_VERSION` bumps to 3 when the table lands.

## Input facts (verified against the 2026-08-08 export)

- Sheet `Sheet2`: 358 rows × 56 columns; `professionalId` unique; `crd` 100% filled; `appStatus` all "verified".
- Sitemap: 294 advisor-profile URLs, **two ID formats** (short e.g. `qv3Y1g3y`, and UUID) — URLs must come from the sitemap, never assembled. All 294 sitemap IDs join `professionalId` exactly; 64 export rows have no public profile → excluded.
- `advisorPrompts`: DynamoDB-JSON `[{"M":{"response":{"S":"…"},"promptId":{"N":"0"}}}]`, filled 128/358. RESOLVED: promptId→question mapping is not recoverable — publish **answers only**, framed as `in_their_own_words: [..]` (list of response strings). The framing makes standalone answers read naturally; promptId is dropped.
- No specialties column in the export (marketplace-card specialties live elsewhere) — accepted; `find_bookable_advisors` specialty filtering searches bio/clientDescription/quickFacts text instead.

## Column whitelist (marketplace_advisors)

**Publish:** professionalId, crd, displayName, companyName, jobTitle, city, state, bio, credentials, clientDescription, quickFacts, pricingV2 (fallback pricing), minAccountSize, yearsOfExperience, virtualMeetingsOffered, allowedStates, memberSince, advisorWebsiteURL, linkedInURL, profile_url (from sitemap), in_their_own_words (parsed advisorPrompts responses), aum, clientNumber, education, twitterURL, bioVideoLink (all five RESOLVED 2026-08-09: confirmed displayed publicly on profile pages; aum/clientNumber labeled 'as listed on their AdvisorFinder profile' — self-reported, distinct from the regulatory banding rules).

**Never-export (audit-enforced, fail loudly):** email, phoneNumber, cognitoUsername, advisorELID, adtrax*, calendlyUrl, calendlyUser, paidAdvisor, profileCompletenessScore, accountEnabled, appStatus, agreedToBetaAgreement, zipCode, supplementalZipCodes, disclosureText, rsnipDisclosure, finraUrl, secUrl, jobHistory, createdDate, lastUpdatedDate, entityType*, hasInsuranceLicense*, averageAccountSize*
  (* = deferred: not confirmed as publicly displayed.)

**Booking:** the profile URL IS the booking link (booking flow lives on the profile page; raw calendly links bypass the funnel — excluded).

## Sanitizer audit (three layers, build fails loudly on any violation)

1. **Column whitelist**: output table's columns must EQUAL the publish list; forbidden name-patterns (`email`, `phone`, `cognito`, `calendly`, `paid`, `token`, `password`, `auth`, `stripe`, `zip`) can never appear.
2. **Value-level PII scan** over every published text field (bio, prompts, quickFacts, clientDescription, …): email-regex, phone-regex, and long-numeric-ID patterns → violations named with row + field; Drew adjudicates (strip or per-instance allow).
3. **Cross-dataset validity**: every published `crd` must exist in `ia_reps` (typo catch); every `profile_url` must have come from the sitemap fetch; row count ≤ sitemap profile count.

## Product surface (server changes)

1. **New tool `find_bookable_advisors(specialty?, city?, state?, limit=20)`** — searches marketplace_advisors ONLY (294 rows — simple SQL, no FTS needed). Docstring discloses scope: "advisors listed on AdvisorFinder's marketplace — professionals you can view and contact directly." Results: name, firm, title, city/state, pricing model, min account size, years experience, virtual-meetings flag, profile_url, plus CRD-joined registration + four-state disclosure status (pre-vetted results). Envelope as usual.
2. **Enrichment (no ranking change)**: `get_advisor`, `check_advisor`, `search_advisors` — when result CRD ∈ marketplace_advisors, add labeled block `advisorfinder_listing: {profile_url, job_title, pricing, note: "This advisor is listed on AdvisorFinder — view their full profile and contact them."}`.
3. **Deep links**: `format.advisorfinder_url()` grows a per-advisor variant returning profile_url for members, homepage otherwise (still the single URL chokepoint).
4. **Disclosure**: `coverage-and-limitations` resource + help page gain: marketplace listings are advisors who joined AdvisorFinder's platform; affiliation is labeled, never a ranking factor; regulatory data covers everyone equally.
5. Stats tool reports marketplace_advisors count + snapshot date.

## Testing (same standards as v2)

- Sanitizer: fixture xlsx with planted PII (email in bio, phone in prompt response, calendly column) → audit failures named; sitemap-absent row excluded; CRD-not-in-regulatory row flagged; DynamoDB-prompt parsing; URL never assembled (fixture sitemap with both ID formats).
- Server: find_bookable tool shapes + envelope; enrichment block present for member CRD, absent for non-member; ranking unchanged (fixture ordering assertion); disclosure wording tests (never "endorsed/recommended/vetted by AdvisorFinder").
- Fixture regeneration via the real scripts (cross-repo contract test pattern).

## Open items

- Refresh cadence: export drop is manual (monthly / 2× quarter); document the drop path + `refresh_mcp_db.sh` picks it up automatically if present.
- Wording review of "listed on AdvisorFinder" copy (avoid anything reading as endorsement — possible legal glance).
