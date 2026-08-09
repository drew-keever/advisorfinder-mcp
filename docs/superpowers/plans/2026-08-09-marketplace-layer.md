# Marketplace Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AdvisorFinder's ~294 public marketplace advisors to the MCP as a PII-scrubbed, sitemap-scoped discovery layer with per-advisor deep links, a `find_bookable_advisors` tool, and labeled (never ranked) enrichment of existing tools.

**Architecture:** A new sanitizer module in firm-intelligence parses Drew's AdFi xlsx export, joins canonical profile URLs from advisorfinder.com/sitemap.xml, enforces a three-layer PII audit, and hands clean rows to `build_mcp_public_db.py` (new `--marketplace` flag) which bakes a `marketplace_advisors` table into the same `mcp_public.db` artifact. The server bumps to SCHEMA_VERSION 3, gains one tool and CRD-keyed enrichment.

**Tech Stack:** Python stdlib + openpyxl (sanitizer only), sqlite3, fastmcp 3.x. Spec: `docs/superpowers/specs/2026-08-09-marketplace-layer-design.md` (in the advisorfinder-mcp repo).

## Global Constraints

- Repos: firm-intelligence work in `/Users/lv/projects/advisorfinder/firm-intelligence` (Tasks 1–2), server work in `/Users/lv/services/advisorfinder-mcp` (Tasks 3–5). Use worktrees per superpowers:using-git-worktrees at execution time.
- NEVER open the real `db/firms.db` or the real uploaded export in tests — fixtures only. The real export currently lives at `/Users/lv/.claude/uploads/9c59b851-f89a-482c-9cb9-191f36340cf0/ce056182-AdvisorFinder_advisors__8.8.2026.xlsx`; the documented production drop path is `imports/marketplace/latest.xlsx` (firm-intelligence repo, gitignored).
- Publish column whitelist (EXACT, from spec): professionalId, crd, displayName, companyName, jobTitle, city, state, bio, credentials, clientDescription, quickFacts, pricing (from pricingV2 falling back to pricing), minAccountSize, yearsOfExperience, virtualMeetingsOffered, allowedStates, memberSince, advisorWebsiteURL, linkedInURL, twitterURL, bioVideoLink, education, aum, clientNumber, in_their_own_words (JSON array of advisorPrompts response strings), profile_url.
- Never-export columns (audit-enforced): email, phoneNumber, cognitoUsername, advisorELID, adtrax*, calendlyUrl, calendlyUser, paidAdvisor, profileCompletenessScore, accountEnabled, appStatus, agreedToBetaAgreement, zipCode, supplementalZipCodes, disclosureText, rsnipDisclosure, finraUrl, secUrl, jobHistory, createdDate, lastUpdatedDate, entityType, hasInsuranceLicense, averageAccountSize.
- Scope rule: publish ONLY advisors whose professionalId appears in a sitemap advisor-profile URL. URLs are stored verbatim from the sitemap, never assembled (two ID formats exist: short `qv3Y1g3y` and UUID).
- No ranking changes in existing tools. Enrichment wording: "This advisor is listed on AdvisorFinder — view their full profile and contact them." Never "endorsed", "recommended", or "vetted by AdvisorFinder" anywhere (test-enforced).
- aum/clientNumber rendered with label "as listed on their AdvisorFinder profile" (self-reported).
- PII value-scan regexes (applied to every published text field): email `[\w.+-]+@[\w-]+\.[\w.]{2,}`, phone `(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}` and 10+ consecutive digits, plus `cognito|calendly` substrings. Violations are reported with row professionalId + field name; build exits non-zero.
- Deploy ordering (Gate section): upload new DB (schemaVersion 3) to R2 FIRST, then push server code — the running v2 server only reads its DB at boot, and the new deploy's health-gated swap picks up v3.
- fastmcp tools are sync functions; tests call the plain functions directly (existing pattern in tests/).
- TDD per task; full suite green before each commit. Test runner: the repo-local `.venv/bin/python -m pytest tests/` in each repo (create a venv with `openpyxl` + `pytest` in the firm-intelligence worktree: `python3 -m venv .venv && .venv/bin/pip install pytest openpyxl`).

---

### Task 1: Sanitizer module (firm-intelligence)

**Files:**
- Create: `scripts/sanitize_marketplace.py`
- Test: `tests/test_sanitize_marketplace.py`
- Modify: `.gitignore` (add `imports/`)

**Interfaces:**
- Produces: `sanitize(xlsx_path: str, sitemap_xml: str, known_crds: set[str]) -> SanitizeResult` where `SanitizeResult` is a dataclass with `rows: list[dict]` (keys = exactly the publish whitelist), `excluded_not_public: int`, `violations: list[str]` (empty on success), `crd_mismatches: list[str]`.
- Produces: `MARKETPLACE_COLUMNS: dict[str, str]` (column → sqlite type; `in_their_own_words` TEXT holds a JSON array; numerics INTEGER/REAL where the export is numeric, else TEXT).
- Produces: `fetch_sitemap(url: str) -> str` (thin urllib wrapper; tests never call it).
- Produces: `parse_profile_urls(sitemap_xml: str) -> dict[str, str]` mapping professionalId → full URL, via regex `r'<loc>(https://advisorfinder\.com/app/advisor-profile/([^/<]+)/[^<]*)</loc>'`.

- [ ] **Step 1: Write failing tests** — fixture builder in the test module creates a small xlsx via openpyxl with the REAL 56-column header (copy verbatim from spec/Input-facts; hardcode the list in the test file) and 5 rows: (a) normal public advisor, (b) advisor NOT in fixture sitemap, (c) advisor with email embedded in bio ("reach me at jane@x.com"), (d) advisor with phone in an advisorPrompts response, (e) advisor whose crd is not in known_crds. Fixture sitemap XML string contains 4 `<loc>` entries covering both ID formats. Tests:

```python
def test_scope_only_sitemap_advisors(tmp_path):
    res = run_fixture(tmp_path)  # helper: build xlsx, call sanitize with fixture sitemap + known_crds
    assert res.excluded_not_public == 1
    assert all(r["profile_url"].startswith("https://advisorfinder.com/app/advisor-profile/") for r in res.rows)

def test_urls_verbatim_from_sitemap_both_formats(tmp_path): ...  # short-ID and UUID rows carry the exact sitemap string

def test_columns_equal_whitelist(tmp_path):
    assert set(res.rows[0].keys()) == set(MARKETPLACE_COLUMNS)  # and no forbidden name survives

def test_email_in_bio_is_violation(tmp_path):   # violations contain professionalId + "bio"
def test_phone_in_prompt_is_violation(tmp_path):
def test_crd_not_in_regulatory_flagged(tmp_path):
def test_prompts_parsed_from_dynamodb_json(tmp_path):
    # '[{"M":{"response":{"S":"Sailing"},"promptId":{"N":"0"}}}]' -> in_their_own_words == '["Sailing"]'
def test_pricing_v2_fallback(tmp_path):  # pricingV2 empty -> pricing value used
```

- [ ] **Step 2: Run to verify FAIL** — `.venv/bin/python -m pytest tests/test_sanitize_marketplace.py -v` → import error / missing module.
- [ ] **Step 3: Implement `scripts/sanitize_marketplace.py`** — openpyxl read (header→index map), row filter by sitemap membership, whitelist projection, DynamoDB-prompt parse (`[e["M"]["response"]["S"] for e in json.loads(raw)]`, malformed → skip field + count), PII scan over every str-valued published field with the Global-Constraints regexes, CRD cross-check, `SanitizeResult`. Module docstring states the trust contract. No prints on success; `main()` CLI (`--xlsx`, `--sitemap-file|--sitemap-url`, `--known-crds-db`) for standalone runs, exits 1 with named violations.
- [ ] **Step 4: Run to verify PASS**, full firm-intelligence suite green (existing 28 + new).
- [ ] **Step 5: Commit** — `feat: marketplace sanitizer with PII audit (whitelist + value-scan + CRD check)`

### Task 2: Build integration (firm-intelligence)

**Files:**
- Modify: `scripts/build_mcp_public_db.py` (EXPORT_SCHEMA, CLI, manifest)
- Modify: `scripts/refresh_mcp_db.sh`
- Test: extend `tests/test_build_mcp_public_db.py`

**Interfaces:**
- Consumes: `sanitize_marketplace.sanitize`, `MARKETPLACE_COLUMNS`, `parse_profile_urls`, `fetch_sitemap`.
- Produces: `mcp_public.db` optionally containing table `marketplace_advisors` (columns = MARKETPLACE_COLUMNS + PRIMARY KEY professionalId, index on crd); `export_meta` keys `marketplace_count`, `marketplace_snapshot_date` (xlsx file mtime date); manifest gains `gates.marketplaceIncluded: bool`, `counts.marketplace_advisors`.
- CLI: `--marketplace <xlsx>` and `--marketplace-sitemap <path-or-URL>` (default `https://advisorfinder.com/sitemap.xml`; tests pass a fixture file path). Build without `--marketplace` = identical to today (no table, gate false); schema_version stays `2` when absent, `3` when present? **No** — schema_version is the server contract, not data presence: it becomes `3` unconditionally in this task, and the server (Task 3) treats `marketplace_advisors` as optional-at-runtime.

- [ ] **Step 1: Failing tests** — with `--marketplace` + fixture sitemap file: table exists with exact columns; audit passes (add `marketplace_advisors` to EXPORT_SCHEMA); rows joinable `marketplace_advisors.crd -> ia_reps` for fixture advisor; export_meta/manifest keys present; sanitizer violation (email fixture row) → build exits non-zero, no manifest written; without flag → no table, `marketplaceIncluded: false`, schema_version still 3, server-side... (assert export_meta schema_version == '3' in both modes).
- [ ] **Step 2: Verify FAIL.**
- [ ] **Step 3: Implement** — import sanitize_marketplace lazily inside the `--marketplace` branch (keeps stdlib-only default path); `export_marketplace(conn, result)` inserts rows (json.dumps for in_their_own_words already done by sanitizer); EXPORT_SCHEMA entry; bump written schema_version to '3'; audit layer-2 forbidden-regex check: confirm bare `aum` column does NOT trip `^aum_(?!band$)` (it doesn't — pattern requires underscore; add a comment + a test pinning this). Update `refresh_mcp_db.sh`: `MPX="imports/marketplace/latest.xlsx"; if [ -f "$MPX" ]; then MARKET_ARGS="--marketplace $MPX"; else MARKET_ARGS=""; fi` and pass `$MARKET_ARGS`.
- [ ] **Step 4: Verify PASS** (full suite).
- [ ] **Step 5: Commit** — `feat: bake marketplace_advisors into mcp_public.db (schema v3, optional --marketplace)`

### Task 3: Server data layer + schema bump + fixture regen (advisorfinder-mcp)

**Files:**
- Modify: `src/advisorfinder_mcp/__init__.py` (`SCHEMA_VERSION = 3`), `src/advisorfinder_mcp/db.py`
- Modify: `tests/fixtures/make_fixture_source.py` (+ fixture marketplace xlsx + fixture sitemap file), regenerate `tests/fixtures/mcp_public.db` + `manifest.json`, update `tests/fixtures/README.md`
- Test: extend `tests/test_bootstrap_and_db.py`

**Interfaces:**
- Produces: `db.get_marketplace_by_crd(crd: str) -> sqlite3.Row | None`; `db.search_marketplace(specialty: str | None, city: str | None, state: str | None, limit: int) -> list[sqlite3.Row]` (specialty = case-insensitive LIKE over bio, clientDescription, quickFacts, credentials — 294 rows, LIKE is fine); `db.marketplace_stats() -> dict | None` (count + snapshot date from export_meta; None when table absent).
- Fixture: 2 of the existing fixture advisors become marketplace members (one with prompts, pricing, aum; one minimal), 1 non-member stays; fixture regenerated with the REAL Task-1/2 scripts (document the three commands in fixtures/README.md).

- [ ] **Step 1: Failing tests** — get_marketplace_by_crd returns row with profile_url for member, None for non-member; search_marketplace by state/specialty-substring; marketplace_stats count == 2; conftest schema_version assert now expects 3; graceful `None`/empty when table absent (build a no-marketplace fixture variant in tmp_path via the real script and point db at it in one test).
- [ ] **Step 2: Verify FAIL** (schema assert fails first — regen fixture as part of this task's step 3).
- [ ] **Step 3: Implement + regenerate fixtures** with the real firm-intelligence scripts.
- [ ] **Step 4: Verify PASS** (expect some pinned counts in stats tests to need updating — legitimate only if caused by the fixture regen).
- [ ] **Step 5: Commit** — `feat: marketplace data layer, SCHEMA_VERSION 3, fixture regen`

### Task 4: Tool + enrichment + disclosure (advisorfinder-mcp)

**Files:**
- Modify: `src/advisorfinder_mcp/server.py`, `src/advisorfinder_mcp/format.py`, `src/advisorfinder_mcp/resources.py`
- Test: create `tests/test_find_bookable_advisors.py`, extend `tests/test_get_advisor.py`, `tests/test_check_advisor.py`, `tests/test_search_advisors.py`, `tests/test_get_database_stats.py`, `tests/test_resources.py`

**Interfaces:**
- Consumes: Task 3's db functions.
- Produces: tool `find_bookable_advisors(specialty: str | None = None, city: str | None = None, state: str | None = None, limit: int = 20)`; `format.marketplace_block(row) -> dict` returning `{"profile_url", "job_title", "pricing", "note": "This advisor is listed on AdvisorFinder — view their full profile and contact them."}`; `format.advisorfinder_profile_url(row_or_none) -> str` (profile_url if member row given, else `advisorfinder_url()`).

- [ ] **Step 1: Failing tests**

```python
def test_find_bookable_returns_members_with_deep_links(): ...   # profile_url present, regulatory disclosure status joined
def test_find_bookable_specialty_filter(): ...
def test_find_bookable_no_filters_returns_results(): ...        # browse OK here: scope is 294 members, disclosed in docstring
def test_find_bookable_aum_labeled_self_reported(): ...         # "as listed on their AdvisorFinder profile"
def test_get_advisor_member_has_listing_block(): ...            # advisorfinder_listing present w/ exact note copy
def test_get_advisor_nonmember_has_no_listing_block(): ...
def test_search_advisors_ranking_unchanged_by_membership(): ... # fixture: member and non-member matching same query -> order identical to pre-existing relevance order (pin exact order)
def test_no_endorsement_wording_anywhere():                     # scan all tool outputs for "endorsed|recommended by advisorfinder|vetted by advisorfinder" -> absent
def test_coverage_resource_discloses_marketplace(): ...
def test_stats_reports_marketplace_count(): ...
```

- [ ] **Step 2: Verify FAIL.**
- [ ] **Step 3: Implement** — new `@mcp.tool` (docstring: "Search advisors listed on AdvisorFinder's marketplace — professionals with public profiles you can view and contact directly. This searches only AdvisorFinder members (a few hundred advisors), not the full SEC roster; use search_advisors for the full roster."); enrichment lookup in get_advisor/check_advisor/search_advisors result assembly via `db.get_marketplace_by_crd` (search_advisors: one lookup per returned row AFTER ranking/limit — never in the query); envelope's per-advisor link via `advisorfinder_profile_url`; resources.py coverage-and-limitations paragraph: "Some advisors are listed on AdvisorFinder's marketplace. Their listings add self-provided profile information and a link to contact them. Being listed is a business relationship with AdvisorFinder — it is labeled on every result, never affects search ranking, and is not an endorsement. Regulatory data is shown identically for all advisors."; stats tool adds marketplace count + snapshot date.
- [ ] **Step 4: Verify PASS** (full suite).
- [ ] **Step 5: Commit** — `feat: find_bookable_advisors tool + labeled marketplace enrichment (no ranking changes)`

### Task 5: Docs (advisorfinder-mcp)

**Files:**
- Modify: `README.md` (tools table + one Quick-Start example), `docs/mcp-help.md` (new tool, example prompt "Find a fee-only advisor near Austin I can actually book a call with", marketplace disclosure paragraph mirroring the resource copy), `server.json` (version 2.1.0), `pyproject.toml` (version 2.1.0)
- Test: none (docs); validate `mcp-publisher validate` passes.

- [ ] Steps: update docs → `mcp-publisher validate` → full suite still green → commit `docs: marketplace layer docs + 2.1.0 version bump`.

### Gates (controller-run, after all tasks reviewed)

1. **Gate A2 (real build):** copy the uploaded xlsx to `imports/marketplace/latest.xlsx` in the firm-intelligence repo; run the real build with `--marketplace` + live sitemap; expect ~294 marketplace rows, audit pass, and adjudicate any PII value-scan hits with Drew (real bios may trip the phone regex).
2. **Gate B2 (local e2e):** serve real DB locally, exercise find_bookable_advisors + enrichment via MCP client.
3. **Gate C2/D2 (ship):** upload DB+manifest to R2 FIRST → push server → health-gated swap → verify live: member profile deep-link resolves (curl the URL → 200), stats shows marketplace count.
4. PyPI 2.1.0 + registry publish (same flow as 2.0.0; key + .pypirc already on the mini). Update advisorfinder.com/mcp page example prompts (Drew).

## Self-review notes

- Spec coverage: principles 1–4 → Tasks 4/1/1/2; whitelist → Tasks 1–2; audits → Task 1; product surface 1–5 → Task 4 (+3); testing section → per-task tests; open items (drop path, wording) → Task 2 refresh wiring + Task 4 copy. schema_version semantics decision documented in Task 2.
- Type consistency: `SanitizeResult.rows` dicts keyed by MARKETPLACE_COLUMNS feed `export_marketplace`; db functions return sqlite3.Row per existing pattern; `marketplace_block`/`advisorfinder_profile_url` names used consistently in Tasks 3–4.
