# Data request to firm-intelligence: roster counts and marketplace text (2026-10-14)

**From:** advisorfinder-mcp session · **For:** the firm-intelligence agent (owner of
`scripts/build_mcp_public_db.py` and `scripts/sanitize_marketplace.py`)

The MCP server now works around three defects in the schema-4 artifact
(`mcp_public.db`, built 2026-08-28). The workarounds are in the server's
presentation layer only. Please fix the source so the next export is clean.
Nothing here is urgent for the MCP; it matters for every other consumer of the
same data (public_export, directory CSVs, website).

## 1. `firms.investment_adviser_reps` is not a usable advisor count

| Firm | CRD | `investment_adviser_reps` | Distinct IARs in `ia_rep_firms` |
|---|---|---|---|
| Fisher Investments | 107342 | 0 | 2,861 |
| Edelman Financial Engines | 104510 | 0 | 723 |
| J.P. Morgan Securities | 79 | 8,513 | 13,866 (35,860 incl. `ia_only='N'`) |

7,687 of 15,648 SEC firms have 0 in the column but a non-empty roster. Schema 5
already drops the column; confirm no other export (public_export, directory CSV
`advisor_details`/firm files, bios) uses it as a headcount. If a headcount is
needed, derive it from the roster **with `ia_only = 'Y'`**: `ia_rep_firms` also
holds 150,427 broker-dealer (`N`) rows, and counting them inflates 91 firms by
2x or more (Goldman Sachs & Co.: 8,255 all rows, 0 IA rows). Same class of
error as the regScope fix.

## 2. Marketplace text is mojibake (UTF-8 read as cp1252)

32 of 294 `marketplace_advisors` rows. Examples: `ðŸ‘‹ I'm Allen` (was 👋),
`â€™` (was ’), `CERTIFIED FINANCIAL PLANNERÂ®` (was ®). The damage happens
before or inside `sanitize_marketplace.py` (likely reading the xlsx/CSV or the
DynamoDB export with the wrong encoding). Fix at read time; do not ship
`ftfy`-style repairs in the export if the read can be fixed.

## 3. Marketplace fields still carry DynamoDB attribute-value JSON

| Column | Rows | Shape |
|---|---|---|
| `credentials` | 235 | `[{"M":{"acronym":{"S":..},"value":{"S":..},"description":..}}]` |
| `education` | 239 | `[{"M":{"institution":{"S":..},"degreeObtained":{"S":..},"completedDate":{"S":"Fri May 15 2009 ..."}}}]` |
| `pricing` | 270 | `[{"M":{"typeName":{"S":"AUM"},"minRange":{"S":"0.50"},..}}]` |
| `allowedStates` | 5 | `[{"S":"TX"},{"S":"OK"}]` |
| `quickFacts` | 11 | `[{"S":"aum"},{"S":"averageAccountSize"}]` (UI keys, not content) |

Unmarshal DynamoDB JSON (e.g. `boto3.dynamodb.types.TypeDeserializer`) in the
sanitize step. Data-quality notes seen while doing this:

- AUM pricing ranges are percentages, but the unit is not stored; one row has
  `minRange "65"`, `maxRange "1.1"` (almost certainly a typo for 0.65). One row
  stores `".5%"`.
- Credential `description` text is generic AdvisorFinder copy (contains a typo,
  "boung to be a fiduciary"); the MCP no longer shows it.
- `education.completedDate` is a JavaScript `Date.toString()` string; store an
  ISO date or a year.

## 4. Lost characters (U+FFFD) in firm data — added 2026-10-18

The artifact stores the replacement character `�` where the SEC files have
accented letters. Example, Goldman Sachs CRD 361 branch:
`R. LEOPOLDO COUTO DE MAGALH�ES J�NIOR`, `S�O PAULO`.

| Table.column | Rows with `�` |
|---|---|
| `firm_locations.street1` / `street2` / `city` / `phone` | 58 / 16 / 20 / 4 (65 firms) |
| `firms.primary_name` / `legal_name` / `address_street1` / `address_street2` / `phone` | 3 / 2 / 2 / 2 / 1 |
| `firm_other_names.other_name` | 11 |

**Root cause (verified read-only):** `scripts/build_history.py`
`open_csv_in_zip()` does `data.decode('utf-8', errors='replace')`. The SEC ADV
bulk CSVs are cp1252/Latin-1, so every accented byte becomes U+FFFD and the
letter is lost. The raw zip still has the correct bytes: decoding
`IA_Schedule_D_1F_20111105_20241231.csv` from
`data/historical/adv-part1-2011-2024.zip` as cp1252 gives
`RUA LEOPOLDO COUTO DE MAGALHÃES`. The IAPD XML feeds declare
`encoding="ISO-8859-1"` and are not affected.

**Fix:** decode those CSVs as cp1252 (fall back to latin-1), rebuild
`firm_locations`, `firms`, `firm_other_names`, then re-export. Check other
readers of the same zips for the same pattern. The MCP server does not guess
the letters; it shows the text as stored with a note pointing to SEC IAPD.

## 5. Firm website can be an employee's personal site — added 2026-10-22

`firms.website_clean` for Fisher Investments (CRD 107342) is
`https://jaycarlsonfisherinvestments.wordpress.com`, a personal blog, not the
firm's site. CRD 125800 has `https://michaeldayoub.wordpress.com`. A directory
tester saw the Fisher value through `get_firm`.

**Cause (read-only check):** `scripts/cleanup.py` `clean_websites()` only
normalizes `firms.website`; it does not choose between the websites a firm
lists on Form ADV (Item 1.I / Schedule D Section 1.I lists every site,
including employee and social pages). The stored value is one of those
entries, not necessarily the firm's main domain.

**Request:** when a firm lists several websites, choose the one whose domain
best matches the firm's name or legal name, and skip hosted personal pages
(`*.wordpress.com`, `*.blogspot.*`, `*.wixsite.com`, and similar) unless
they are the only entry. The MCP server shows the value as exported; it does
not pick between websites itself.

## 6. Firm consolidation drops 3,577 advisors — added 2026-10-22

`firms.canonical_crd` (set by `scripts/cleanup.py` "entity consolidation")
points 19 firms at another CRD. `build_mcp_public_db.py:356` exports only
`canonical_crd IS NULL` firms, and their reps are not re-pointed, so every
advisor whose only adviser firm is a consolidated one disappears from the
export.

- 4,417 advisors (`ia_only='Y'`) are linked to consolidated firms; **3,577 of
  them are in no exported firm at all** (checked read-only against the
  2026-08-28 artifact).
- Largest: CRD 11025 WELLS FARGO ADVISORS FINANCIAL NETWORK, LLC → 19616
  (WELLS FARGO CLEARING SERVICES, LLC): 3,995 advisors. These are two separate
  SEC registrants with the same brand name, not duplicates. Next: 294197
  ROCKEFELLER & CO. LLC → 291361 (338), 105734 RUSSELL INVESTMENT MANAGEMENT
  → 329 (74).
- Found through a real lead: marketplace member Zach Farley (CRD 4609160, Wells
  Fargo Advisors Financial Network) is active and registered in NM and TX on
  IAPD, but `check_advisor` says he is not in our data.

**Request:** review the 19 consolidations; do not merge distinct SEC
registrants that only share a brand name. Where a merge is right, export the
reps under the canonical CRD instead of dropping them.

## 7. `scripts/refresh_mcp_db.sh` is unsafe to run today — added 2026-10-22

- `main` builds `schema_version` 5; the live server is schema 4 and refuses to
  boot on a mismatch. The script uploads straight to the production R2 keys,
  so the next restart would fail.
- No real-data gate and no rollback copy before the upload.
- `RENDER_DEPLOY_HOOK` in `~/.config/adfi/mcp_refresh.env` is still a
  placeholder, so the final `curl` fails after the upload.

**Request:** until the server moves to schema 5, do not run it. When it is
fixed: upload to a staging key, gate, keep a rollback copy, then promote. The
MCP side has a marketplace-only refresh runbook in the meantime
(`advisorfinder-mcp/docs/runbooks/marketplace-refresh.md`).
