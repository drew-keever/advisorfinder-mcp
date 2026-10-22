# Marketplace refresh runbook

Last checked: 2026-10-22. Live marketplace snapshot: 2026-08-12 (294 members).

## Why not `scripts/refresh_mcp_db.sh`

Do **not** run firm-intelligence `scripts/refresh_mcp_db.sh` for a marketplace
refresh. As of 2026-10-22 it would take production down or fail half-way:

1. **Schema mismatch.** firm-intelligence `main` builds `schema_version` **5**
   (`scripts/build_mcp_public_db.py:800`). This server is `SCHEMA_VERSION = 4`
   (`src/advisorfinder_mcp/__init__.py`) and refuses to boot on a mismatch.
   The script uploads straight to the production R2 keys, so the next restart
   (deploy, OOM, Render maintenance) would fail to boot.
2. **No gate, no rollback.** It overwrites `mcp_public.db` and `manifest.json`
   in R2 with no real-data check and no copy of the previous version.
3. **Deploy hook is a placeholder.** `RENDER_DEPLOY_HOOK` in
   `~/.config/adfi/mcp_refresh.env` is not a Render hook URL, so the final
   `curl` fails after the upload has already happened.
4. **It also rebuilds all regulatory data** from today's `db/firms.db`, which
   has unreviewed changes (schema-5 Item 5 / audience work). A marketplace
   refresh should not ship those.

## Recommended path: marketplace-only refresh

Replace only the `marketplace_advisors` table in the current production
database. Regulatory data stays byte-for-byte the same. The privacy boundary
stays firm-intelligence's `scripts/sanitize_marketplace.py` (unchanged since
the schema-4 builder `dd63707`), so the whitelist, PII scan, sitemap filter
and CRD check all still apply.

### Drew

1. Export the marketplace advisors from the AdvisorFinder app in the **same
   format as the August export** (same columns, `.xlsx`).
2. Save it on the Mac mini as
   `~/Projects/advisorfinder/firm-intelligence/imports/marketplace/latest.xlsx`.
   Rename the old file to `2026-08-12.xlsx` first, so it is kept.
3. Make sure every new advisor's public profile is live on
   advisorfinder.com (the sitemap decides who is published).
4. Tell Claude: "the new marketplace export is in place".

### Claude (controller)

1. **Copy, don't touch.** Copy `latest.xlsx` into the session scratchpad.
   Nothing is written inside firm-intelligence (another agent's uncommitted
   work lives there).
2. **Sanitize.** Run `sanitize_marketplace.py` (from firm-intelligence, read-only)
   against the copy, the live sitemap and `db/firms.db` (opened read-only).
   Stop on any PII violation and bring each one to Drew. Report CRD
   mismatches (expected: state-registered advisors).
3. **Compare with August.** List members added, removed (fell out of the
   sitemap) and changed. Check the column set is the same as August; a new or
   renamed column stops the run until reviewed.
4. **Build.** Download the current production DB from R2 and verify its
   sha256 against the manifest. In a copy: replace `marketplace_advisors` rows,
   update `export_meta` (`marketplace_count`, `marketplace_snapshot_date`,
   `marketplace_crd_unmatched`), keep `schema_version` = 4. Write a new
   manifest (sha256, sizeBytes, schemaVersion 4).
5. **Gate on real data.** Run the full test suite, then run the server
   locally on the new DB: all 7 tools, `find_bookable_advisors` with no filter
   (count = new member count), a sample of new members through
   `get_advisor` / `check_advisor`, ISO dates, no mojibake, no DynamoDB JSON,
   no email/phone outside `bio`.
6. **Back up.** Copy the current R2 `mcp_public.db` and `manifest.json` to
   `r2-adfi:advisorfinder-mcp/rollback-<date>/`.
7. **Upload** the new DB first, then the manifest (rclone remote `r2-adfi`,
   bucket `advisorfinder-mcp`).
8. **Restart** the Render service (the server downloads and verifies the DB at
   boot; no code push needed). Watch `[bootstrap +Ns]` lines in `render logs`.
9. **Live check.** `get_database_stats` shows the new member count and
   snapshot date; repeat the step-5 sample against
   `https://mcp.advisorfinder.com/mcp`.
10. **Rollback** if anything fails: copy the `rollback-<date>/` files back and
    restart.

Time: about 30-45 minutes once the export is in place.

## Later

- When the server moves to schema 5, rebuild through firm-intelligence's
  builder again and retire this splice path.
- Replace the placeholder `RENDER_DEPLOY_HOOK` before anyone uses
  `refresh_mcp_db.sh`, and add a gate + rollback step to that script
  (firm-intelligence change; hand to that agent).
