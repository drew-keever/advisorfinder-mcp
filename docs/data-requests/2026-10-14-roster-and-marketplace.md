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
