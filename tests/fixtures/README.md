# Fixture DB regeneration

`mcp_public.db` + `manifest.json` in this directory are built by the REAL export
script from the firm-intelligence repo (Tasks 1-2), fed by two committed
generators here:

- `make_fixture_source.py` builds a small `firms.db`-shaped source database
  that exercises every tool path in `advisorfinder_mcp` — see the docstring at
  the top of that file for exactly which advisor/firm covers which case.
- `make_fixture_marketplace.py` builds a small AdFi marketplace-advisor xlsx
  (the raw, un-sanitized export shape) feeding `build_mcp_public_db.py`'s
  optional `--marketplace` flag — see its docstring for which of the fixture
  advisors becomes a rich vs. minimal marketplace member, and which one is
  present in the marketplace xlsx but deliberately excluded by
  `marketplace_sitemap.xml` (proves the sitemap-scoping gate).

`marketplace_sitemap.xml` (also committed here) is a small fixture sitemap:
three `advisor-profile` `<loc>` entries (one short-format professionalId
`qv3Y1g3y`, one UUID-format, and one more short-format professionalId
`qv3Y4u6v` added for Gate A2's crd-not-in-ia_reps case) plus a couple of
non-advisor URLs (homepage, blog post) that must never be mistaken for
advisor profiles.

`mcp_public_no_marketplace.db` is a SECOND committed fixture: the same
`make_fixture_source.py` source database run through the real
`build_mcp_public_db.py` WITHOUT `--marketplace` — i.e. a genuine
schema_version=3 export that simply never created `marketplace_advisors`.
It exists so `tests/test_bootstrap_and_db.py::test_marketplace_functions_are_graceful_when_table_absent`
can assert the graceful-absence contract (`get_marketplace_by_crd` → `None`,
`search_marketplace` → `[]`, `marketplace_stats` → `None`) unconditionally —
no dependency on the sibling firm-intelligence repo, no skip, works in CI
and on any machine. (A separate, clearly-skippable provenance test rebuilds
an equivalent DB live via the real script, to prove this fixture's shape
actually matches current script output — see that test's docstring.)

## The other repo's location

The REAL export script (`scripts/build_mcp_public_db.py`) lives in the
firm-intelligence repo's MAIN checkout:

```
/Users/lv/projects/advisorfinder/firm-intelligence
```

(As of the post-sweep resume-round, 2026-08: this repo's `main` branch has
merged both the marketplace-export workstream AND a data-sweep round —
`ia_reps.name_suffix` plus a roster-completeness fix, commit `5654333`. The
formerly-used sibling worktree, `firm-intelligence-worktrees/marketplace`,
predates that merge — it is missing `name_suffix` entirely and is being
retired; do not point regeneration at it anymore.)

That repo does not keep a persistent committed venv. Create a small
throwaway one there before regenerating (it only needs `openpyxl`, for the
`--marketplace` branch):

```bash
cd /Users/lv/projects/advisorfinder/firm-intelligence
python3 -m venv .venv-fixture
.venv-fixture/bin/pip install -q --upgrade pip
.venv-fixture/bin/pip install -q openpyxl
```

Before regenerating, confirm that repo's `main` is clean and at the commit
you expect (`git -C /Users/lv/projects/advisorfinder/firm-intelligence status`
/ `log --oneline -1`) — regenerating against a dirty or unexpected checkout
silently bakes in whatever uncommitted script changes happen to be sitting
there.

## Regenerate

```bash
# 1. Build the source DB (firms.db-shaped) into scratch space.
.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/fixture_source.db

# 2. Build the fixture marketplace xlsx into scratch space.
.venv/bin/python tests/fixtures/make_fixture_marketplace.py /tmp/fixture_marketplace.xlsx

# 3. Run the real export script (from the OTHER repo's MAIN checkout, using ITS
#    fixture venv — it needs openpyxl for the --marketplace branch) against
#    both. --out expects a DIRECTORY. --marketplace-sitemap accepts a local
#    file path (as here) or an https:// URL (fetched live) —
#    tests/production always pass a local path.
/Users/lv/projects/advisorfinder/firm-intelligence/.venv-fixture/bin/python \
  /Users/lv/projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py \
  --source /tmp/fixture_source.db \
  --out /tmp/fixture_out \
  --marketplace /tmp/fixture_marketplace.xlsx \
  --marketplace-sitemap "$(pwd)/tests/fixtures/marketplace_sitemap.xml"

# 4. Copy the two committed artifacts over (NOT fillrate.csv — not part of this fixture).
cp /tmp/fixture_out/mcp_public.db tests/fixtures/mcp_public.db
cp /tmp/fixture_out/manifest.json tests/fixtures/manifest.json
```

### Regenerate mcp_public_no_marketplace.db (the no-`--marketplace` fixture)

```bash
# 1. Build the source DB (same generator, same source shape as above).
.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/fixture_source_nm.db

# 2. Run the real export script WITHOUT --marketplace / --marketplace-sitemap.
/Users/lv/projects/advisorfinder/firm-intelligence/.venv-fixture/bin/python \
  /Users/lv/projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py \
  --source /tmp/fixture_source_nm.db \
  --out /tmp/fixture_out_nm

# 3. Copy just the db -- nothing here reads its manifest.json.
cp /tmp/fixture_out_nm/mcp_public.db tests/fixtures/mcp_public_no_marketplace.db
```

Omitting `--marketplace`/`--marketplace-sitemap` still produces a valid
schema_version=3 DB — `marketplace_advisors` is OPTIONAL-at-runtime (see
`db._marketplace_table_exists()`), so a v3 build without marketplace data is a
legitimate shape, just not the one committed as `mcp_public.db`. That's
exactly the shape `mcp_public_no_marketplace.db` commits, and what the
provenance test in `tests/test_bootstrap_and_db.py` rebuilds live in
`tmp_path` to double-check against.

## Not byte-for-byte reproducible

Re-running this will NOT reproduce byte-identical files: `export_meta.generated_at`
and `manifest.json`'s `generatedAt`/`sha256` all vary run-to-run because the build
script stamps the current UTC time into the database before hashing it.
`export_meta.marketplace_snapshot_date` also varies run-to-run — it's derived
from the marketplace xlsx's file mtime at build time (step 2 above), not a
fixed value — but since it's baked into the checked-in `mcp_public.db` at
generation time, it stays fixed for the life of *this* fixture; only
regenerating changes it. Row counts, disclosure tally, and every other value
should match exactly — only these generation-time-derived values differ.

## Schema version

`export_meta.schema_version` in the built fixture must equal
`advisorfinder_mcp.SCHEMA_VERSION`. `tests/conftest.py` asserts this on every
test run — if the export script's schema version bumps, regenerate the fixture
and bump `SCHEMA_VERSION` together.

As of Task 3 (marketplace-layer), `SCHEMA_VERSION = 3`: the export script now
writes `export_meta.marketplace_count` / `marketplace_snapshot_date`
unconditionally (`"0"`/`None` when `--marketplace` is omitted) and supports the
optional `marketplace_advisors` table. v3 is the SERVER contract, not a
data-presence flag — a v3 build without `--marketplace` is still schema
version 3, just without that one optional table.

### Post-sweep resume-round (2026-08): `ia_reps.name_suffix`

The firm-intelligence data-sweep round added an `ia_reps.name_suffix` column
(values like `'JR.'`/`'III'`/`NULL`) and made the SEC-side IAR roster complete
(previously ~52% silently truncated). Neither change bumped
`SCHEMA_VERSION` — it's still `3`: `name_suffix` is a new optional-in-practice
column the server now reads explicitly (see `db.search_advisors()`'s column
list and `format.title_case_name()`'s suffix handling), not a change to the
v3 contract's shape (table presence / `export_meta` keys). `make_fixture_source.py`
adds one suffixed advisor (`1000011`, `ROBERT JONES JR.`) so both committed
fixtures exercise this column; regenerate both (above) whenever
`make_fixture_source.py` changes.
