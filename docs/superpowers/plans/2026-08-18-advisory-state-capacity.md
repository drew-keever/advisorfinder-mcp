# Advisory State Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the MCP reporting broker-dealer state registrations as investment-advisory ones, and stop it printing the same job twice.

**Architecture:** The upstream re-fetch already produced correct per-state capacity, so the MCP never re-derives it — it consumes `iar_details.registered_states_ia` and a new derived integer `registered_states_bc_count`. The export script swaps the mixed column out of its whitelist and derives the broker count in SQL; the server renames the output key to `advisory_states`, adds a labeled broker-registration block, and merges duplicate employment rows using the `ia_only` flag the export now carries. All new consumer wording lives in named constants in one module so a wording change is a text edit.

**Tech Stack:** Python 3, fastmcp 3.4.6, SQLite (JSON1 functions confirmed available, sqlite 3.53.4), pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-advisory-state-capacity-design.md`

## Global Constraints

- The advisory-state rule is `regScope = 'IA' AND status IN ('APPROVED','APPROVED_RES','APP_PEND_IARCE','TEMPREG','REQUAL')`. It is already applied by `registered_states_ia`. **The MCP consumes that column and never re-implements the rule.**
- `iar_details.registered_states` (the mixed column) **must never be exported again**.
- `registered_states_scoped` **must never be exported** — it adds 635 MB to a 645 MB artifact that Render boot-downloads from R2.
- `registered_states_bc_count` is `0` when an advisor has no broker states and `NULL` only when `registered_states_scoped` is absent (meaning *not fetched*). The server omits the broker block for both, and never compares `NULL` with `>`.
- Never guess a capacity. A missing `ia_only` flag is not an adviser flag — omit the field.
- `SCHEMA_VERSION` moves 3 → 4 in both repos, together with fixture regeneration. Ship order is R2 upload first, then deploy.
- The four-state disclosure contract is untouched: keyed on `ia_reps.has_disclosure` only, never softened, no numeric risk scores.
- No "endorsed/recommended/vetted by AdvisorFinder" wording anywhere (enforced by `tests/test_no_endorsement_wording.py`).
- Coverage copy is mirrored across `src/advisorfinder_mcp/resources.py` + `README.md` + `docs/mcp-help.md` — change all three together or none (enforced by `tests/test_coverage_wording.py`).
- Fixtures are built by the real firm-intelligence scripts and are **never hand-edited** (`tests/fixtures/README.md`).
- **WORDING GATE:** the four note strings in Task 2 are placeholders. Drew approves the final wording before Task 2 starts. Tests assert presence and placement, never exact prose.

---

## File Structure

**firm-intelligence repo** (`/Users/lv/Projects/advisorfinder/firm-intelligence`):

- `scripts/build_mcp_public_db.py` — modify. Whitelist swap, `ia_only` passthrough, broker-count derivation, schema version.

**advisorfinder-mcp repo** (this repo):

- `src/advisorfinder_mcp/__init__.py` — modify. `SCHEMA_VERSION = 4`.
- `src/advisorfinder_mcp/format.py` — modify. Four wording constants plus three pure helpers: `advisory_states_block()`, `broker_registration_block()`, `merge_employment_rows()`. This is where all new logic and all new copy live, because it is the module with no DB dependency and therefore the one that unit-tests cheaply.
- `src/advisorfinder_mcp/server.py` — modify. Four call sites plus the employment build.
- `src/advisorfinder_mcp/db.py` — **no change.** Every relevant query already uses `SELECT *`, so new columns arrive without edits. Verified before writing this plan.
- `src/advisorfinder_mcp/resources.py`, `README.md`, `docs/mcp-help.md` — modify. Mirrored copy.
- `tests/fixtures/make_fixture_source.py` — modify. New columns, three new advisors, one duplicate-job pair.
- `tests/test_format.py` — modify. Unit tests for the three helpers.
- `tests/test_get_advisor.py`, `tests/test_check_advisor.py`, `tests/test_find_bookable_advisors.py` — modify. Tool-level tests.

---

### Task 1: Export the capacity data (firm-intelligence repo)

Nothing in this repo can be tested until the export produces the new shape, so this task comes first. It is self-contained and verified by running the real script.

**Files:**
- Modify: `/Users/lv/Projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py`

**Interfaces:**
- Consumes: `src.iar_details.registered_states_ia`, `.registered_states_ia_pending_ce`, `.registered_states_scoped` (populated by the 2026-08-17 re-fetch); `iaOnly` inside `src.iar_details.raw_current_employments`.
- Produces: exported `iar_details.registered_states_ia TEXT`, `.registered_states_ia_pending_ce TEXT`, `.registered_states_bc_count INTEGER`; exported `advisor_employments.ia_only TEXT`; `export_meta.schema_version = '4'`.

- [ ] **Step 1: Confirm the working tree is clean before touching it**

```bash
cd /Users/lv/Projects/advisorfinder/firm-intelligence
git status --short && git log --oneline -1
```

Expected: no uncommitted changes to `scripts/build_mcp_public_db.py`. Regenerating fixtures against a dirty checkout silently bakes in someone else's edits.

- [ ] **Step 2: Swap the `iar_details` whitelist**

In `EXPORT_SCHEMA["iar_details"]` (around line 166), delete the `registered_states` line and add three:

```python
        "registered_states_ia": "TEXT",
        "registered_states_ia_pending_ce": "TEXT",
        "registered_states_bc_count": "INTEGER",
```

- [ ] **Step 3: Add `ia_only` to the employment whitelist**

In `EXPORT_SCHEMA["advisor_employments"]`, after `"branch_state": "TEXT",`, add:

```python
        "ia_only": "TEXT",
```

- [ ] **Step 4: Exclude the derived column from the copy SELECT**

`registered_states_bc_count` does not exist in `src.iar_details`, so a straight column copy would fail. Follow the existing `aum_band` idiom (see `_FIRMS_SELECT_COLS`, line 285). Add next to it:

```python
_IAR_DETAILS_SELECT_COLS = [
    c for c in EXPORT_SCHEMA["iar_details"] if c != "registered_states_bc_count"
]
```

Then in `export_iar_details()` replace `cols = list(EXPORT_SCHEMA["iar_details"])` with:

```python
    cols = list(_IAR_DETAILS_SELECT_COLS)
```

- [ ] **Step 5: Carry the `iaOnly` flag through `project_employments()`**

In the current-employments loop, the append becomes eight values plus the flag. Replace the `to_insert.append((...))` in the `for e in cur_list:` branch with:

```python
            to_insert.append((
                ind, str(fid) if fid is not None else None, e.get("firmName"), 1,
                _iso_date(e.get("registrationBeginDate")), _iso_date(e.get("registrationEndDate")),
                city, state, e.get("iaOnly"),
            ))
```

and in the `for e in prev_list:` branch:

```python
            to_insert.append((
                ind, str(fid) if fid is not None else None, e.get("firmName"), 0,
                _iso_date(e.get("registrationBeginDate")), _iso_date(e.get("registrationEndDate")),
                e.get("city"), e.get("state"), e.get("iaOnly"),
            ))
```

Then update the INSERT in the same function:

```python
    conn.executemany(
        """
        INSERT INTO advisor_employments
            (ind_source_id, firm_crd, firm_name, is_current, start_date, end_date, branch_city, branch_state, ia_only)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        to_insert,
    )
```

- [ ] **Step 6: Derive the broker-state count in SQL**

Add this function immediately after `export_iar_details()`. The SQL was run against the real 416,491-row database before this plan was written: it returns 29 for CRD 6805515 and 0 (not NULL) for an adviser-only CRD.

```python
# Broker-dealer state registrations, counted at export time so the 635 MB
# registered_states_scoped JSON never enters the artifact. Same five-value
# status set the IA rule uses. json_valid() guards a malformed blob; the outer
# WHERE leaves the column NULL for advisors whose scope was never fetched, so
# NULL means "not fetched" and 0 means "no broker states".
_BC_STATUSES = "('APPROVED','APPROVED_RES','APP_PEND_IARCE','TEMPREG','REQUAL')"


def derive_bc_state_counts(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        f"""
        UPDATE iar_details SET registered_states_bc_count = (
            SELECT COUNT(DISTINCT json_extract(je.value, '$.state'))
            FROM src.iar_details s, json_each(s.registered_states_scoped) je
            WHERE s.ind_source_id = iar_details.ind_source_id
              AND json_extract(je.value, '$.regScope') = 'BC'
              AND json_extract(je.value, '$.status') IN {_BC_STATUSES}
        )
        WHERE ind_source_id IN (
            SELECT ind_source_id FROM src.iar_details
            WHERE registered_states_scoped IS NOT NULL
              AND json_valid(registered_states_scoped)
        )
        """
    )
    return cur.rowcount
```

- [ ] **Step 7: Call the derivation right after the iar_details export**

Find the call to `export_iar_details(conn)` in the build sequence and add the derivation immediately after it, recording the count the same way the neighbouring steps do:

```python
    counts["iar_details_bc_counts"] = derive_bc_state_counts(conn)
```

- [ ] **Step 8: Bump the schema version**

At line ~728 change `("schema_version", "3"),` to:

```python
        ("schema_version", "4"),
```

- [ ] **Step 9: Run the real export against the committed fixture source and verify the shape**

```bash
cd /Users/lv/services/advisorfinder-mcp
.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/t1_source.db
/Users/lv/Projects/advisorfinder/firm-intelligence/.venv-fixture/bin/python \
  /Users/lv/Projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py \
  --source /tmp/t1_source.db --out /tmp/t1_out
```

Expected: the build completes and the post-build audit reports no violations. The audit enforces the whitelist, so a mistake in Steps 2-4 fails here loudly. This run uses the *current* fixture generator, which does not yet have the new source columns — so expect the build to fail with "no such column: registered_states_ia". **That failure is the correct result of this step**: it proves the whitelist is live. Task 3 adds the source columns and this command then succeeds.

- [ ] **Step 10: Verify the derivation against production data**

```bash
cd /tmp && python3 - <<'EOF'
import sqlite3
c = sqlite3.connect(":memory:")
c.execute("ATTACH 'file:/Users/lv/Projects/advisorfinder/firm-intelligence/db/firms.db?mode=ro&immutable=1' AS src")
sql = """
SELECT COUNT(DISTINCT json_extract(je.value,'$.state'))
FROM src.iar_details s, json_each(s.registered_states_scoped) je
WHERE s.ind_source_id = ?
  AND json_extract(je.value,'$.regScope')='BC'
  AND json_extract(je.value,'$.status') IN ('APPROVED','APPROVED_RES','APP_PEND_IARCE','TEMPREG','REQUAL')
"""
assert c.execute(sql, ("6805515",)).fetchone()[0] == 29, "broker count for the reported advisor must be 29"
print("OK: CRD 6805515 broker states = 29")
EOF
```

Expected: `OK: CRD 6805515 broker states = 29`

- [ ] **Step 11: Commit in the firm-intelligence repo**

```bash
cd /Users/lv/Projects/advisorfinder/firm-intelligence
git add scripts/build_mcp_public_db.py
git commit -m "feat: export advisory-state capacity, drop the mixed column (schema 4)

registered_states mixed IA and BC registrations. Export registered_states_ia
and registered_states_ia_pending_ce instead, plus a derived
registered_states_bc_count (SQL, so the 635MB scoped JSON stays out of the
artifact). advisor_employments gains ia_only so the server can merge the
duplicate adviser/broker row pair. schema_version 3 -> 4."
```

---

### Task 2: Wording constants and the three pure helpers

**GATE: do not start this task until Drew has approved the four note strings.** The strings below are placeholders. Everything else in the task is final.

**Files:**
- Modify: `src/advisorfinder_mcp/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Consumes: nothing from other tasks. Pure functions over mappings, so they test without a database.
- Produces:
  - `advisory_states_block(iar_row) -> dict` — returns `{"advisory_states": [...]}` plus, conditionally, `"advisory_states_note"`, `"advisory_states_pending_ce"`, `"advisory_states_pending_ce_note"`.
  - `broker_registration_block(iar_row) -> dict | None` — returns `{"state_count": int, "note": str}` or `None`.
  - `merge_employment_rows(rows) -> list[dict]` — returns merged rows with keys `firm_crd`, `firm_name`, `is_current`, `start_date`, `end_date`, `branch_city`, `branch_state`, and `capacity` when derivable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_format.py`:

```python
# ── advisory states / broker registration / employment merge ──────────────────

def test_advisory_states_lists_only_the_ia_column():
    row = {"registered_states_ia": "Florida,Texas", "registered_states_ia_pending_ce": None}
    assert format.advisory_states_block(row) == {"advisory_states": ["Florida", "Texas"]}


def test_advisory_states_empty_column_gets_the_none_on_record_note():
    row = {"registered_states_ia": "", "registered_states_ia_pending_ce": None}
    block = format.advisory_states_block(row)
    assert block["advisory_states"] == []
    assert block["advisory_states_note"] == format.ADVISORY_STATES_NONE_NOTE


def test_advisory_states_missing_detail_row_is_not_none_on_record():
    # No iar_details row is a THIRD meaning: we hold no detail record for this
    # person. It must not claim "none on record", which is a positive finding.
    block = format.advisory_states_block(None)
    assert block["advisory_states"] == []
    assert block["advisory_states_note"] == format.ADVISORY_STATES_UNAVAILABLE_NOTE
    assert block["advisory_states_note"] != format.ADVISORY_STATES_NONE_NOTE


def test_advisory_states_pending_ce_is_listed_in_both_arrays():
    row = {"registered_states_ia": "California,Texas", "registered_states_ia_pending_ce": "California"}
    block = format.advisory_states_block(row)
    assert block["advisory_states"] == ["California", "Texas"]
    assert block["advisory_states_pending_ce"] == ["California"]
    assert block["advisory_states_pending_ce_note"] == format.ADVISORY_STATES_PENDING_CE_NOTE


def test_advisory_states_pending_ce_never_adds_a_state():
    # Defensive: upstream says pending-CE is a strict subset (1,811 of 1,811).
    # If that ever changes, we must not silently grow the advisory list.
    row = {"registered_states_ia": "California", "registered_states_ia_pending_ce": "California,Nevada"}
    block = format.advisory_states_block(row)
    assert block["advisory_states"] == ["California"]
    assert block["advisory_states_pending_ce"] == ["California"]


def test_broker_block_present_with_the_count_in_the_note():
    block = format.broker_registration_block({"registered_states_bc_count": 29})
    assert block["state_count"] == 29
    assert "29" in block["note"]


def test_broker_block_absent_for_zero_and_for_null():
    assert format.broker_registration_block({"registered_states_bc_count": 0}) is None
    assert format.broker_registration_block({"registered_states_bc_count": None}) is None
    assert format.broker_registration_block({}) is None
    assert format.broker_registration_block(None) is None


def test_merge_employments_collapses_the_adviser_broker_pair():
    rows = [
        {"firm_crd": "79", "firm_name": "J.P. MORGAN SECURITIES LLC", "is_current": 1,
         "start_date": "2023-06-06", "end_date": None, "branch_city": "NAPLES",
         "branch_state": "FL", "ia_only": "Y"},
        {"firm_crd": "79", "firm_name": "J.P. MORGAN SECURITIES LLC", "is_current": 1,
         "start_date": "2023-06-06", "end_date": None, "branch_city": "Naples",
         "branch_state": "FL", "ia_only": "N"},
    ]
    merged = format.merge_employment_rows(rows)
    assert len(merged) == 1
    assert merged[0]["capacity"] == "investment adviser and broker-dealer"
    # Branch comes from the ia_only='Y' row: this is an advisory product.
    assert merged[0]["branch_city"] == "NAPLES"


def test_merge_employments_keeps_a_genuine_rehire_separate():
    rows = [
        {"firm_crd": "79", "firm_name": "BIG FIRM LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": "Y"},
        {"firm_crd": "79", "firm_name": "BIG FIRM LLC", "is_current": 0,
         "start_date": "2012-01-01", "end_date": "2015-01-01", "branch_city": "NY",
         "branch_state": "NY", "ia_only": "Y"},
    ]
    assert len(format.merge_employment_rows(rows)) == 2


def test_merge_employments_falls_back_to_firm_name_when_crd_is_null():
    rows = [
        {"firm_crd": None, "firm_name": "NO CRD LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": "Y"},
        {"firm_crd": None, "firm_name": "NO CRD LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": "N"},
    ]
    assert len(format.merge_employment_rows(rows)) == 1


def test_merge_employments_omits_capacity_when_the_flag_is_missing():
    rows = [
        {"firm_crd": "79", "firm_name": "BIG FIRM LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": None},
    ]
    merged = format.merge_employment_rows(rows)
    assert len(merged) == 1
    assert "capacity" not in merged[0]


def test_merge_employments_single_flag_states_one_capacity():
    adviser = [{"firm_crd": "1", "firm_name": "A", "is_current": 1, "start_date": "2020-01-01",
                "end_date": None, "branch_city": "NY", "branch_state": "NY", "ia_only": "Y"}]
    broker = [{"firm_crd": "2", "firm_name": "B", "is_current": 1, "start_date": "2020-01-01",
               "end_date": None, "branch_city": "NY", "branch_state": "NY", "ia_only": "N"}]
    assert format.merge_employment_rows(adviser)[0]["capacity"] == "investment adviser"
    assert format.merge_employment_rows(broker)[0]["capacity"] == "broker-dealer"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_format.py -k "advisory or broker or merge_employ" -v
```

Expected: FAIL — `AttributeError: module 'advisorfinder_mcp.format' has no attribute 'advisory_states_block'`.

- [ ] **Step 3: Add the wording constants**

Add to `src/advisorfinder_mcp/format.py`, above the new helpers. Keeping every string here — and nowhere else — is what makes a wording change a text edit.

```python
# ── advisory-state wording (PROVISIONAL — Drew approves before ship) ──────────
# All four strings live here and nowhere else, so approved wording lands as a
# text edit with no code or test changes. Tests assert placement, not prose.

ADVISORY_STATES_NONE_NOTE = (
    "No state investment-adviser registrations are on record for this person in "
    "the SEC data. This is not a disclosure or disciplinary finding. Check the "
    "IAPD record linked below."
)

ADVISORY_STATES_UNAVAILABLE_NOTE = (
    "State registration detail is not available for this person in our data. "
    "This is not a statement that no registrations exist. Check the IAPD record "
    "linked below."
)

ADVISORY_STATES_PENDING_CE_NOTE = (
    "Registered in this state, with an annual continuing-education course "
    "outstanding. This is an administrative status, not a restriction and not a "
    "disclosure event."
)

BROKER_ALSO_REGISTERED_NOTE = (
    "Also registered as a broker-dealer agent in {state_count} states. Selling "
    "securities is not the same as giving investment advice. See the BrokerCheck "
    "link below."
)

CAPACITY_BOTH = "investment adviser and broker-dealer"
CAPACITY_IA = "investment adviser"
CAPACITY_BC = "broker-dealer"
```

- [ ] **Step 4: Implement the three helpers**

Add to `src/advisorfinder_mcp/format.py`. `_row_value()` already exists in this module and tolerates both `sqlite3.Row` (which raises `IndexError` on a missing key) and plain dicts — use it everywhere.

```python
def _split_state_csv(raw) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def advisory_states_block(iar_row) -> dict:
    """The advisory-state keys for a tool payload.

    Three distinct outcomes, which must never be conflated:
      - no iar_details row      -> we hold no detail record (UNAVAILABLE note)
      - row, empty IA column    -> none on record (NONE note)
      - row, populated column   -> the advisory states, already distinct upstream
    """
    if iar_row is None:
        return {"advisory_states": [], "advisory_states_note": ADVISORY_STATES_UNAVAILABLE_NOTE}

    states = _split_state_csv(_row_value(iar_row, "registered_states_ia"))
    if not states:
        return {"advisory_states": [], "advisory_states_note": ADVISORY_STATES_NONE_NOTE}

    block = {"advisory_states": states}
    # Pending-CE is an annotation on states already in the list, never an
    # addition: intersect so a future upstream change cannot grow the list.
    pending = [s for s in states if s in set(_split_state_csv(
        _row_value(iar_row, "registered_states_ia_pending_ce")
    ))]
    if pending:
        block["advisory_states_pending_ce"] = pending
        block["advisory_states_pending_ce_note"] = ADVISORY_STATES_PENDING_CE_NOTE
    return block


def broker_registration_block(iar_row) -> dict | None:
    """Labeled broker-dealer registration count, or None when there is nothing
    to say. 0 (no broker states) and NULL (scope never fetched) both yield None,
    so no caller compares NULL with '>'. The note's number is templated from
    state_count in this one expression — never built from a second variable."""
    if iar_row is None:
        return None
    count = _row_value(iar_row, "registered_states_bc_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return None
    return {
        "state_count": count,
        "note": BROKER_ALSO_REGISTERED_NOTE.format(state_count=count),
    }


def _ia_only_flag(row):
    """'Y'/'N' normalized, or None when the flag is absent or unrecognized.
    An unrecognized flag is treated as absent — never as an adviser flag."""
    raw = _row_value(row, "ia_only")
    if raw is None:
        return None
    flag = str(raw).strip().upper()
    return flag if flag in ("Y", "N") else None


def merge_employment_rows(rows) -> list[dict]:
    """Collapse the adviser/broker row pair the SEC returns for one job.

    IAPD returns the same job twice for a dual-registered person: one object
    with iaOnly='Y' and one with iaOnly='N', same firm and same registration
    dates, differing only in their branch-office list (and in letter case).

    The group key includes the dates on purpose — keying on firm alone would
    merge a genuine rehire at the same firm into one row. firm_name is the
    fallback key when firm_crd is null, so two different no-CRD firms never
    merge with each other.
    """
    groups = {}
    order = []
    for row in rows:
        crd = _row_value(row, "firm_crd")
        firm_key = ("crd", str(crd)) if crd else ("name", str(_row_value(row, "firm_name") or ""))
        key = (
            firm_key,
            bool(_row_value(row, "is_current")),
            _row_value(row, "start_date"),
            _row_value(row, "end_date"),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    merged = []
    for key in order:
        group = groups[key]
        flags = [_ia_only_flag(r) for r in group]
        # Branch tie-break: prefer the adviser row's office. This is an
        # advisory product, so that is the office worth showing.
        preferred = next((r for r, f in zip(group, flags) if f == "Y"), group[0])
        out = {
            "firm_crd": _row_value(preferred, "firm_crd"),
            "firm_name": _row_value(preferred, "firm_name"),
            "is_current": bool(_row_value(preferred, "is_current")),
            "start_date": _row_value(preferred, "start_date"),
            "end_date": _row_value(preferred, "end_date"),
            "branch_city": _row_value(preferred, "branch_city"),
            "branch_state": _row_value(preferred, "branch_state"),
        }
        present = {f for f in flags if f is not None}
        if None in flags or not present:
            pass  # a missing flag is not an adviser flag — say nothing
        elif present == {"Y", "N"}:
            out["capacity"] = CAPACITY_BOTH
        elif present == {"Y"}:
            out["capacity"] = CAPACITY_IA
        elif present == {"N"}:
            out["capacity"] = CAPACITY_BC
        merged.append(out)
    return merged
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_format.py -v
```

Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add src/advisorfinder_mcp/format.py tests/test_format.py
git commit -m "feat: advisory-state, broker-count and employment-merge helpers

Pure helpers over mappings, so they unit-test with no database. All four new
consumer strings are named constants in this module and nowhere else, so
approved wording lands as a text edit. Three outcomes stay distinct: no detail
row (unavailable), empty column (none on record), populated column."
```

---

### Task 3: Fixture population, schema bump, and server wiring

This task is atomic on purpose. The moment the fixture stops carrying `registered_states`, the server must already read `registered_states_ia`, or the suite is red. Splitting it would leave a broken checkpoint.

**Files:**
- Modify: `tests/fixtures/make_fixture_source.py`
- Modify: `src/advisorfinder_mcp/__init__.py:16`
- Modify: `src/advisorfinder_mcp/server.py` (lines 247-257, 264, 293, 319, 328, 429, 436)
- Modify: `tests/test_get_advisor.py:59-61`, `tests/test_check_advisor.py:34`, `tests/test_find_bookable_advisors.py:123`
- Regenerate: `tests/fixtures/mcp_public.db`, `tests/fixtures/manifest.json`, `tests/fixtures/mcp_public_no_marketplace.db`

**Interfaces:**
- Consumes: Task 1's export shape; Task 2's `format.advisory_states_block()`, `format.broker_registration_block()`, `format.merge_employment_rows()`.
- Produces: `advisory_states` (and its optional sibling keys) plus `also_registered_as_broker` in `get_advisor` and `check_advisor`; `employment[].capacity`; a schema-4 fixture pair.

- [ ] **Step 1: Add the new source columns to the fixture generator**

In `tests/fixtures/make_fixture_source.py`, the `CREATE TABLE iar_details` block (line ~147-155) mirrors the real source schema. After the `registered_states TEXT,` entry on line 153, add:

```python
    registered_states_ia TEXT, registered_states_ia_pending_ce TEXT,
    registered_states_scoped TEXT,
```

Keep `registered_states` — the real source still has it, and the point of this change is that the export no longer *reads* it.

- [ ] **Step 2: Populate the new columns on the existing advisors**

Every existing `_insert(conn, "iar_details", ...)` call needs the new keyword arguments. Use these values, which keep the current tests' expectations intact (`1000002` stays NY+FL):

```python
# 1000001: registered_states="NY"   -> add:
            registered_states_ia="NY",
            registered_states_scoped='[{"state":"NY","regScope":"IA","status":"APPROVED"}]',
# 1000002: registered_states="NY,FL" -> add:
            registered_states_ia="NY,FL",
            registered_states_scoped='[{"state":"NY","regScope":"IA","status":"APPROVED"},'
                                     '{"state":"FL","regScope":"IA","status":"APPROVED"},'
                                     '{"state":"NY","regScope":"BC","status":"APPROVED"},'
                                     '{"state":"NJ","regScope":"BC","status":"APPROVED"},'
                                     '{"state":"CT","regScope":"BC","status":"APPROVED"}]',
# 1000003: registered_states="MA"   -> add:
            registered_states_ia="MA",
            registered_states_scoped='[{"state":"MA","regScope":"IA","status":"APPROVED"}]',
# 1000005: registered_states="NY,MA" -> add:
            registered_states_ia="NY,MA",
            registered_states_scoped='[{"state":"NY","regScope":"IA","status":"APPROVED"},'
                                     '{"state":"MA","regScope":"IA","status":"APPROVED"}]',
# 1000006: registered_states="CA"   -> add:
            registered_states_ia="CA",
            registered_states_scoped='[{"state":"CA","regScope":"IA","status":"APPROVED"}]',
# 1000009: registered_states="NY"   -> add:
            registered_states_ia="NY",
            registered_states_scoped='[{"state":"NY","regScope":"IA","status":"APPROVED"}]',
```

`1000002` now has three broker-only states (NY, NJ, CT — NY is shared, so the distinct broker count is 3) and becomes the dual-registered case the tests pin.

- [ ] **Step 3: Add the three new advisors the new cases need**

The fixture must exercise pending-CE, zero-advisory-states, and the duplicate job. Add to `ia_reps` (following the existing `_insert(conn, "ia_reps", ...)` pattern used for `1000001`-`1000011`, with `has_disclosure='N'`), then to `iar_details`:

```python
    # 1000012 DANA PENDINGCE: advisory states with a CE course outstanding.
    #   Pins that a pending-CE state appears in BOTH arrays and is never
    #   presented as a restriction.
    _insert(conn, "iar_details", ind_source_id="1000012", ia_scope="Active",
            bc_scope="NotInScope", industry_start_date="2015-04-01",
            has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2019-01-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=0,
            state_exams="[]", product_exams="[]", principal_exams="[]",
            registered_states="CA,TX", registered_states_ia="CA,TX",
            registered_states_ia_pending_ce="CA",
            registered_states_scoped='[{"state":"CA","regScope":"IA","status":"APP_PEND_IARCE"},'
                                     '{"state":"TX","regScope":"IA","status":"APPROVED"}]',
            disclosure_count=0, ia_disclosure_count=0,
            raw_current_employments=None, raw_previous_employments=None,
            raw_disclosures="[]", fetched_at="2026-08-17")

    # 1000013 RILEY BROKERONLY: zero advisory states, many broker states.
    #   This is the 3,479-advisor production case, and the one that visibly
    #   loses a long list. Pins the "none on record" note AND the broker block.
    _insert(conn, "iar_details", ind_source_id="1000013", ia_scope="Active",
            bc_scope="Active", industry_start_date="2018-02-01",
            has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2020-01-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=0,
            state_exams="[]", product_exams="[]", principal_exams="[]",
            registered_states="NY,NJ,CT", registered_states_ia=None,
            registered_states_ia_pending_ce=None,
            registered_states_scoped='[{"state":"NY","regScope":"BC","status":"APPROVED"},'
                                     '{"state":"NJ","regScope":"BC","status":"APPROVED"},'
                                     '{"state":"CT","regScope":"BC","status":"APPROVED"}]',
            disclosure_count=0, ia_disclosure_count=0,
            raw_current_employments=None, raw_previous_employments=None,
            raw_disclosures="[]", fetched_at="2026-08-17")

    # 1000014 QUINN DUALJOB: one job returned twice by IAPD (iaOnly Y and N),
    #   same firm and dates, different branch-office lists and letter case.
    #   Pins the merge and the capacity label.
    _insert(conn, "iar_details", ind_source_id="1000014", ia_scope="Active",
            bc_scope="Active", industry_start_date="2019-12-27",
            has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2023-06-06", current_branch_city="NAPLES",
            current_branch_state="FL", prev_employment_count=0,
            state_exams="[]", product_exams="[]", principal_exams="[]",
            registered_states="FL,FL,TX,TX", registered_states_ia="FL,TX",
            registered_states_ia_pending_ce=None,
            registered_states_scoped='[{"state":"FL","regScope":"IA","status":"APPROVED"},'
                                     '{"state":"FL","regScope":"BC","status":"APPROVED"},'
                                     '{"state":"TX","regScope":"IA","status":"APPROVED"},'
                                     '{"state":"TX","regScope":"BC","status":"APPROVED"}]',
            disclosure_count=0, ia_disclosure_count=0,
            raw_current_employments=DUAL_EMPLOYMENT_JSON,
            raw_previous_employments=None,
            raw_disclosures="[]", fetched_at="2026-08-17")
```

- [ ] **Step 4: Add the duplicate-job raw JSON**

Next to the existing `CURRENT_EMPLOYMENT_JSON` constant (around line 193), add. This is the real shape from CRD 6805515: two objects, same `firmId` and `registrationBeginDate`, different `iaOnly` and different office lists.

```python
DUAL_EMPLOYMENT_JSON = json.dumps([
    {
        "firmId": 100001, "firmName": "ALPHA WEALTH LLC", "iaOnly": "Y",
        "registrationBeginDate": "6/6/2023",
        "branchOfficeLocations": [
            {"displayOrder": 1, "locatedAtFlag": "Y", "city": "NAPLES", "state": "FL"},
        ],
    },
    {
        "firmId": 100001, "firmName": "ALPHA WEALTH LLC", "iaOnly": "N",
        "registrationBeginDate": "6/6/2023",
        "branchOfficeLocations": [
            {"displayOrder": 1, "locatedAtFlag": "Y", "city": "Naples", "state": "FL"},
        ],
    },
])
```

Also add `1000014` to `ia_rep_firms` so it appears on the roster, following the pattern used for `1000002`.

- [ ] **Step 5: Update the fixture generator's docstring**

The docstring is the map of which advisor covers which case. Add to the advisor list:

```
    1000012 DANA PENDINGCE   IA states with a CE course outstanding
    1000013 RILEY BROKERONLY zero advisory states, 3 broker states
    1000014 QUINN DUALJOB    one job returned twice (iaOnly Y + N)
```

- [ ] **Step 6: Bump SCHEMA_VERSION**

In `src/advisorfinder_mcp/__init__.py` line 16:

```python
SCHEMA_VERSION = 4
```

- [ ] **Step 7: Regenerate both fixtures with the real export script**

```bash
cd /Users/lv/services/advisorfinder-mcp
.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/fixture_source.db
.venv/bin/python tests/fixtures/make_fixture_marketplace.py /tmp/fixture_marketplace.xlsx
/Users/lv/Projects/advisorfinder/firm-intelligence/.venv-fixture/bin/python \
  /Users/lv/Projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py \
  --source /tmp/fixture_source.db --out /tmp/fixture_out \
  --marketplace /tmp/fixture_marketplace.xlsx \
  --marketplace-sitemap "$(pwd)/tests/fixtures/marketplace_sitemap.xml"
cp /tmp/fixture_out/mcp_public.db tests/fixtures/mcp_public.db
cp /tmp/fixture_out/manifest.json tests/fixtures/manifest.json

.venv/bin/python tests/fixtures/make_fixture_source.py /tmp/fixture_source_nm.db
/Users/lv/Projects/advisorfinder/firm-intelligence/.venv-fixture/bin/python \
  /Users/lv/Projects/advisorfinder/firm-intelligence/scripts/build_mcp_public_db.py \
  --source /tmp/fixture_source_nm.db --out /tmp/fixture_out_nm
cp /tmp/fixture_out_nm/mcp_public.db tests/fixtures/mcp_public_no_marketplace.db
```

Expected: both builds complete with no audit violations. This is also the step that proves Task 1 Step 9's deliberate failure is now resolved.

- [ ] **Step 8: Verify the new fixture shape directly**

```bash
sqlite3 tests/fixtures/mcp_public.db "SELECT value FROM export_meta WHERE key='schema_version';"
sqlite3 tests/fixtures/mcp_public.db "PRAGMA table_info(iar_details);" | grep -c registered_states_ia
sqlite3 tests/fixtures/mcp_public.db "PRAGMA table_info(iar_details);" | grep -w registered_states || echo "OK: mixed column absent"
sqlite3 tests/fixtures/mcp_public.db "SELECT ind_source_id, registered_states_ia, registered_states_bc_count FROM iar_details ORDER BY ind_source_id;"
sqlite3 tests/fixtures/mcp_public.db "SELECT ind_source_id, firm_name, ia_only FROM advisor_employments WHERE ind_source_id='1000014';"
```

Expected: `4`; `2` (the `_ia` and `_ia_pending_ce` columns); `OK: mixed column absent`; `1000013` with a NULL/empty IA list and a broker count of 3; `1000002` with a broker count of 3; two `1000014` rows flagged `Y` and `N`.

- [ ] **Step 9: Write the failing tool-level tests**

Add to `tests/test_get_advisor.py`:

```python
def test_advisory_states_are_ia_only_and_broker_count_is_labeled():
    # 1000002 is dual-registered: IA in NY+FL, broker in NY/NJ/CT (3 distinct).
    result = server.get_advisor(crd="1000002")
    assert set(result["advisory_states"]) == {"NY", "FL"}
    assert "registered_states" not in result
    assert result["also_registered_as_broker"]["state_count"] == 3
    assert "3" in result["also_registered_as_broker"]["note"]


def test_pending_ce_state_appears_in_both_arrays_with_a_note():
    result = server.get_advisor(crd="1000012")
    assert set(result["advisory_states"]) == {"CA", "TX"}
    assert result["advisory_states_pending_ce"] == ["CA"]
    assert "advisory_states_pending_ce_note" in result


def test_zero_advisory_states_says_none_on_record_not_unknown():
    result = server.get_advisor(crd="1000013")
    assert result["advisory_states"] == []
    assert result["advisory_states_note"] == format.ADVISORY_STATES_NONE_NOTE
    # The broker registration is still surfaced, so a consumer is not told a
    # flat "not registered" about someone who is a registered broker.
    assert result["also_registered_as_broker"]["state_count"] == 3


def test_adviser_only_advisor_has_no_broker_block():
    result = server.get_advisor(crd="1000003")
    assert "also_registered_as_broker" not in result


def test_duplicate_job_is_merged_with_a_capacity_label():
    result = server.get_advisor(crd="1000014")
    current = [e for e in result["employment"] if e["is_current"]]
    assert len(current) == 1
    assert current[0]["capacity"] == "investment adviser and broker-dealer"
    assert current[0]["branch_city"] == "Naples"
```

Add the import at the top of the file if absent:

```python
from advisorfinder_mcp import format, server
```

Add to `tests/test_check_advisor.py`:

```python
def test_check_advisor_reports_advisory_states_and_the_broker_count():
    result = server.check_advisor(name_or_crd="1000002")
    assert set(result["registration"]["advisory_states"]) == {"NY", "FL"}
    assert "registered_states" not in result["registration"]
    # check_advisor is the "can this person advise me in Ohio?" tool, so the
    # broker fact must be here too, not only in get_advisor.
    assert result["registration"]["also_registered_as_broker"]["state_count"] == 3
```

- [ ] **Step 10: Update the three existing tests that name the old key**

`tests/test_get_advisor.py` line 59-61 becomes:

```python
def test_advisory_states_split_from_csv():
    result = server.get_advisor(crd="1000002")
    assert set(result["advisory_states"]) == {"NY", "FL"}
```

`tests/test_check_advisor.py` line 34 becomes:

```python
    assert set(result["registration"]["advisory_states"]) == {"NY", "FL"}
```

`tests/test_find_bookable_advisors.py` line 123 becomes — and note the added assertion, which pins that an unmatched marketplace CRD is a *different* fact from "none on record":

```python
    assert unmatched["registration"]["advisory_states"] == []
    # Unmatched-CRD means "not in the SEC roster", NOT "no advisory
    # registrations on record" — it must not borrow the zero-state note.
    assert "advisory_states_note" not in unmatched["registration"]
```

- [ ] **Step 11: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/ -x -q
```

Expected: FAIL. `advisory_states` is not yet in any payload, so the new and updated tests fail on `KeyError`.

- [ ] **Step 12: Wire `get_advisor`**

In `src/advisorfinder_mcp/server.py`, replace the `employment` list comprehension (lines 247-257) with a merge-then-format pass:

```python
    employment = [
        {
            "firm_name": format.title_case_firm_name(e["firm_name"]),
            "is_current": e["is_current"],
            "start_date": e["start_date"],
            "end_date": e["end_date"],
            "branch_city": format.title_case_name(e["branch_city"]) if e["branch_city"] else None,
            "branch_state": e["branch_state"],
            **({"capacity": e["capacity"]} if "capacity" in e else {}),
        }
        for e in format.merge_employment_rows(bundle["employments"])
    ]
```

Delete line 264 (`registered_states = _split_states(...)`).

In the `payload` dict, replace `"registered_states": registered_states,` with the two blocks:

```python
        **format.advisory_states_block(iar),
```

and immediately after the `payload` assignment, add:

```python
    broker = format.broker_registration_block(iar)
    if broker is not None:
        payload["also_registered_as_broker"] = broker
```

- [ ] **Step 13: Wire `check_advisor`**

In `_check_verdict()`, delete line 319 and build `registration` from the helpers:

```python
    registration = {
        "active": rep["ia_scope"] == "Active",
        **format.advisory_states_block(iar),
    }
    broker = format.broker_registration_block(iar)
    if broker is not None:
        registration["also_registered_as_broker"] = broker
```

Then use `"registration": registration,` in the payload instead of the inline dict.

- [ ] **Step 14: Wire `find_bookable_advisors`**

In `_regulatory_join()`, the unmatched branch (line 429) renames the key only — it keeps its own meaning and must not gain a note:

```python
        return (
            {"active": None, "advisory_states": []},
            format.marketplace_unmatched_crd_disclosure(crd),
        )
```

And the matched branch (lines 434-437):

```python
    registration = {
        "active": rep["ia_scope"] == "Active",
        **format.advisory_states_block(iar),
    }
    broker = format.broker_registration_block(iar)
    if broker is not None:
        registration["also_registered_as_broker"] = broker
```

- [ ] **Step 15: Remove the now-unused helper if nothing calls it**

```bash
grep -n "_split_states" src/advisorfinder_mcp/server.py
```

If the only remaining hit is the definition at line 54, delete the function. If any call site remains, leave it alone.

- [ ] **Step 16: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS, all tests. The count rises from 205 by the new tests added in Tasks 2 and 3.

- [ ] **Step 17: Commit**

```bash
git add src/advisorfinder_mcp/__init__.py src/advisorfinder_mcp/server.py \
        tests/fixtures/make_fixture_source.py tests/fixtures/mcp_public.db \
        tests/fixtures/manifest.json tests/fixtures/mcp_public_no_marketplace.db \
        tests/test_get_advisor.py tests/test_check_advisor.py \
        tests/test_find_bookable_advisors.py
git commit -m "feat!: advisory_states replaces the mixed registered_states (schema 4)

Tools now report investment-adviser state registrations only, plus a labeled
broker-dealer count so a consumer is never told a flat 'not registered' about
a registered broker. Duplicate adviser/broker job rows merge into one job with
a capacity label. Three fixture advisors added for pending-CE, zero-advisory-
state and duplicate-job cases. Unmatched marketplace CRDs keep their own
meaning and do not borrow the none-on-record note."
```

---

### Task 4: Mirrored copy sweep

**Files:**
- Modify: `src/advisorfinder_mcp/resources.py:77`
- Modify: `README.md:91`, `README.md:110` if it names registered states
- Modify: `docs/mcp-help.md:30`, `docs/mcp-help.md:38`
- Modify: `src/advisorfinder_mcp/server.py` docstrings for `get_advisor` (line 226), `check_advisor` (line 353), `search_advisors` (line 149) if it names registered states
- Test: `tests/test_coverage_wording.py`, `tests/test_no_endorsement_wording.py`

**Interfaces:**
- Consumes: the output contract from Task 3.
- Produces: no code interface. Consumer-visible copy only.

- [ ] **Step 1: Find every place the old promise is made**

```bash
cd /Users/lv/services/advisorfinder-mcp
grep -rn "registered states\|registered_states\|states are they registered" \
  src/advisorfinder_mcp/ README.md docs/mcp-help.md
```

Expected hits: `resources.py:77`, `README.md:91`, `docs/mcp-help.md:30`, `docs/mcp-help.md:38`, and the `get_advisor` / `check_advisor` docstrings. Treat this list as the authority — the mirrored-copy rule means all of them change in this one commit.

- [ ] **Step 2: Rewrite each hit to name the capacity**

Use "state investment-adviser registrations" in place of the bare "registered states". The three replacements:

```
resources.py:77   "- Registration facts: employment history, exams, state investment-adviser
                     registrations (broker-dealer registrations are counted separately and
                     labeled, never merged),"
README.md:91      "| `get_advisor` | Full profile by CRD: employment history, exams, state
                     investment-adviser registrations, self-reported professional
                     designations, years in the industry, and four-state disclosure status. |"
docs/mcp-help.md  "employment history, exams, state investment-adviser registrations,
                     designations, and disclosure status."
```

- [ ] **Step 3: Rewrite the two tool docstrings**

`get_advisor` (line 226) — replace `registered states,` with:

```
    state investment-adviser registrations (broker-dealer state registrations
    are reported separately as a labeled count, never merged into this list),
```

`check_advisor` (line 353) — replace `what states are they registered in` with:

```
    which states they may give investment advice in
```

- [ ] **Step 4: Run the wording tests**

```bash
.venv/bin/python -m pytest tests/test_coverage_wording.py tests/test_no_endorsement_wording.py -v
```

Expected: PASS. If `test_coverage_wording.py` pins an exact mirrored sentence that this edit changed in only two of three files, fix the third — that failure is the test doing its job.

- [ ] **Step 5: Run the full suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/advisorfinder_mcp/resources.py src/advisorfinder_mcp/server.py \
        README.md docs/mcp-help.md
git commit -m "docs: say state investment-adviser registrations, not registered states

Mirrored across resources.py + README + mcp-help.md and the two tool
docstrings, which are live tool output. The old phrasing did not say
registered as what, which is the defect this release fixes."
```

---

### Task 5: Real-data gate and ship

Ops, run by the controller, not by an implementation subagent. Do not start until Tasks 1-4 are committed and the suite is green.

**Files:** none modified. This task produces evidence.

- [ ] **Step 1: Rebuild the production export from the re-fetched source**

```bash
cd /Users/lv/Projects/advisorfinder/firm-intelligence
# use the repo's documented refresh path; --out expects a directory
```

- [ ] **Step 2: Gate on the reported advisor, end to end**

```bash
cd /Users/lv/services/advisorfinder-mcp
MCP_DB_PATH=/Users/lv/Projects/advisorfinder/firm-intelligence/exports/mcp_public/mcp_public.db \
  .venv/bin/python -c "
from advisorfinder_mcp import bootstrap, server
bootstrap.ensure_db()
r = server.get_advisor(crd='6805515')
print('advisory_states     :', r['advisory_states'])
print('broker block        :', r.get('also_registered_as_broker', {}).get('state_count'))
print('current jobs        :', [ (e['firm_name'], e.get('capacity')) for e in r['employment'] if e['is_current'] ])
print('bio                 :', r['bio'])
assert r['advisory_states'] == ['Florida', 'Texas'], r['advisory_states']
assert len([e for e in r['employment'] if e['is_current']]) == 1
assert 'registered_states' not in r
print('GATE PASS')
"
```

Expected: `advisory_states` is `['Florida', 'Texas']`, one current job, and `GATE PASS`.

- [ ] **Step 3: Spot-check the three edge cases on real data**

```bash
sqlite3 /Users/lv/Projects/advisorfinder/firm-intelligence/exports/mcp_public/mcp_public.db "
SELECT 'zero-advisory', COUNT(*) FROM iar_details WHERE registered_states_ia IS NULL OR registered_states_ia=''
UNION ALL SELECT 'pending-ce', COUNT(*) FROM iar_details WHERE registered_states_ia_pending_ce IS NOT NULL AND registered_states_ia_pending_ce<>''
UNION ALL SELECT 'broker-count-null', COUNT(*) FROM iar_details WHERE registered_states_bc_count IS NULL
UNION ALL SELECT 'mixed-column-present', COUNT(*) FROM pragma_table_info('iar_details') WHERE name='registered_states';"
```

Expected: about 3,479 zero-advisory, about 1,802 pending-CE, a small `broker-count-null` figure, and **0** for `mixed-column-present`. A non-zero last row blocks the ship.

- [ ] **Step 4: Upload to R2 BEFORE deploying**

The server refuses to boot on a schema mismatch, so this order is what prevents an outage.

```bash
rclone copy /Users/lv/Projects/advisorfinder/firm-intelligence/exports/mcp_public/mcp_public.db r2-adfi:advisorfinder-mcp/
rclone copy /Users/lv/Projects/advisorfinder/firm-intelligence/exports/mcp_public/manifest.json r2-adfi:advisorfinder-mcp/
```

- [ ] **Step 5: Push and let Render deploy**

```bash
git push origin main
```

- [ ] **Step 6: Verify the live server**

```bash
curl -s https://mcp.advisorfinder.com/health
```

Then call `get_advisor` for CRD 6805515 against the live endpoint and confirm Florida and Texas. Report the result to Drew with the before-and-after numbers.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: the `advisory_states` rename (Tasks 2, 3), the broker count including the rejection of raw-JSON export (Tasks 1, 2, 3), pending-CE (Tasks 2, 3), the zero-advisory-state case (Tasks 2, 3), the `_regulatory_join` unmatched-CRD distinction (Task 3 Steps 10, 14), the employment merge with its date-inclusive key and branch tie-break and never-guess rule (Tasks 1, 2, 3), the export whitelist and `schema_version` (Task 1), the copy sweep (Task 4), fixtures via the real script (Task 3), and the ship order (Task 5).

**One spec gap found and closed here.** The spec named three note strings. Planning surfaced a fourth case: an advisor with **no `iar_details` row at all** (three exist in the fixture; production currently has none). That is neither "none on record" nor a normal list — it is "we hold no detail record". `ADVISORY_STATES_UNAVAILABLE_NOTE` covers it, and `test_advisory_states_missing_detail_row_is_not_none_on_record` pins that the two notes never collapse into one. **This fourth string needs Drew's approval alongside the other three.**

**Type consistency.** `advisory_states_block()`, `broker_registration_block()`, `merge_employment_rows()`, `_ia_only_flag()`, `_split_state_csv()` and the constants `ADVISORY_STATES_NONE_NOTE`, `ADVISORY_STATES_UNAVAILABLE_NOTE`, `ADVISORY_STATES_PENDING_CE_NOTE`, `BROKER_ALSO_REGISTERED_NOTE`, `CAPACITY_BOTH`, `CAPACITY_IA`, `CAPACITY_BC` are spelled identically in Task 2's implementation, Task 2's tests, and Tasks 3-4's call sites. `derive_bc_state_counts()` and `_IAR_DETAILS_SELECT_COLS` appear only in Task 1.

**Deliberate red checkpoint.** Task 1 Step 9 fails on purpose — the whitelist references source columns the old fixture generator lacks. Task 3 Step 7 resolves it. Any executor running tasks out of order needs to know that, which is why it is stated in both places.
