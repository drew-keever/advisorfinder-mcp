# Fixture DB regeneration

`mcp_public.db` + `manifest.json` in this directory are built by the REAL export
script from the firm-intelligence repo (Task 1), fed by `make_fixture_source.py`
(committed here), which builds a small `firms.db`-shaped source database that
exercises every tool path in `advisorfinder_mcp` — see the docstring at the top
of `make_fixture_source.py` for exactly which advisor/firm covers which case.

## Regenerate

```bash
# 1. Build the source DB (firms.db-shaped) into scratch space.
.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/fixture_source.db

# 2. Run the real export script (from the OTHER repo's worktree) against it.
#    --out expects a DIRECTORY.
.venv/bin/python \
  /Users/lv/projects/advisorfinder/firm-intelligence-worktrees/mcp-export/scripts/build_mcp_public_db.py \
  --source /tmp/fixture_source.db \
  --out /tmp/fixture_out

# 3. Copy the two committed artifacts over (NOT fillrate.csv — not part of this fixture).
cp /tmp/fixture_out/mcp_public.db tests/fixtures/mcp_public.db
cp /tmp/fixture_out/manifest.json tests/fixtures/manifest.json
```

## Not byte-for-byte reproducible

Re-running this will NOT reproduce byte-identical files: `export_meta.generated_at`
and `manifest.json`'s `generatedAt`/`sha256` all vary run-to-run because the build
script stamps the current UTC time into the database before hashing it. Row
counts, disclosure tally, and every other value should match exactly — only the
generation timestamp (and the hash computed over bytes that include it) differ.

## Schema version

`export_meta.schema_version` in the built fixture must equal
`advisorfinder_mcp.SCHEMA_VERSION`. `tests/conftest.py` asserts this on every
test run — if the export script's schema version bumps, regenerate the fixture
and bump `SCHEMA_VERSION` together.
