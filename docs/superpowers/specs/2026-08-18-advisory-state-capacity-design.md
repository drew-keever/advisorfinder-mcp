# Advisory state capacity — design

*Approved in brainstorming 2026-08-18 with Drew. Fixes an overstatement defect found
in internal testing on 2026-08-13. Depends on the firm-intelligence re-fetch that
completed 2026-08-17.*

## Purpose

The MCP tells consumers which states an advisor is registered in. Today that list
mixes two different registrations and labels the whole list as investment advisory.

The SEC marks every state registration with a scope: `IA` (investment adviser
representative) or `BC` (broker-dealer agent). Advisers register where they have an
office — 2.0 states on average. Brokers register where their customers are — 20.4
states on average. `fetch_iar_details.py` kept the state name and dropped the scope,
so both merged into one list.

Across the population there were 6,944,903 state claims. Only 787,861 were truly
advisory. **90% of the state claims the product made were false.**

Worked example — CRD 6805515, reported by a colleague on 2026-08-13:

| | Today | After this change |
|---|---|---|
| States shown | 31 entries, 29 unique | 2 |
| The states | Arizona … Wyoming | Florida, Texas |
| Bio | "…advisory services in 29 U.S. states" | "…advisory services in Florida and Texas" |

A consumer in Ohio is told this advisor can advise them. He cannot. He is a broker
there. The duplicate entries the colleague reported (Florida twice, Texas twice) were
the visible symptom of this, not the defect.

## What changed upstream

firm-intelligence re-fetched all 416,491 advisors from the SEC individual endpoint.
The run took 87 hours, finished 2026-08-17, and had zero permanent errors. The SEC
endpoint was chosen over the faster FINRA BrokerCheck endpoint because BrokerCheck's
terms bar use for marketing, and this database also drives outbound sales.

New columns on `iar_details`, all additive. `registered_states` (the mixed column) is
untouched and **must never be exported again**.

| Column | Contents | Coverage |
|---|---|---|
| `registered_states_ia` | Advisory states, comma-separated, already distinct | 412,993 non-empty of 416,491 |
| `registered_states_ia_pending_ce` | Advisory states where an annual CE course is outstanding | 1,811 advisors |
| `registered_states_scoped` | Raw JSON `[{state, regScope, status, regDate}]` | all rows |
| `registered_sros`, `registration_count`, `other_names` | Raw JSON, not used by this change | all rows |
| `scope_fetched_at` | Fetch marker | non-null on all 416,491 |

**The definition of an advisory state** (reuse, do not re-derive):

```
regScope = 'IA' AND status IN ('APPROVED','APPROVED_RES','APP_PEND_IARCE','TEMPREG','REQUAL')
```

This matches the SEC's own `approvedIAStateRegistrationCount` on 100% of 40,000
advisors tested. Counting only `APPROVED` gives 88% and understates 93,567 advisors.
`registered_states_ia` already applies this rule, so the MCP consumes the column and
never re-implements the logic.

### Verified before this spec was written

| Check | Result |
|---|---|
| CRD 6805515 `registered_states_ia` | `Florida,Texas` — correct |
| Regenerated bio for that CRD | "…registered to provide investment advisory services in Florida and Texas." |
| `registered_states_ia_pending_ce` vs the advisory list | strict subset, 1,811 of 1,811 — an annotation, never an extra state |
| Advisors with zero advisory states | 3,498 in `firms.db`; **3,479 in the export**, of which 2,378 show a non-empty list today |
| Advisors with at least one broker state (export) | 329,689 of 412,580 (79.9%) |
| Pending-CE advisors (export) | 1,802 |
| Status values present in the scoped JSON | `IA/APPROVED`, `IA/APPROVED_RES`, `IA/APP_PEND_IARCE`, `IA/TEMPREG`, and BC equivalents |
| Bios | 416,488 rows, all regenerated 2026-08-18, all `needs_review = 1` |
| Does `needs_review` filter the export? | No. Bios flow through. The export gains ~200,000 advisors who had no bio. |
| Cost of exporting `registered_states_scoped` | +635 MB on a 645 MB artifact — rejected, see below |

## Decisions

Drew made three calls on 2026-08-18.

1. **Rename the output key to `advisory_states`.** `registered_states` never said
   registered as *what*. The consumer of this output is usually an AI assistant, so
   the key name is the label. The schema version bump already marks the break.
2. **Show that the person is also a broker, as a count, not a list.** This prevents a
   new error in the opposite direction: answering a flat "not registered in Ohio"
   when the advisor is in fact a registered broker there.
3. **Merge the duplicate job rows into one job with a capacity field.** The `ia_only`
   fact stays in the exported data; the consumer sees one job.

### Rejected: exporting the raw scoped JSON

`registered_states_scoped` averages 1,598 bytes per advisor — 635 MB across the
export, against a current artifact of 645 MB. The artifact is boot-downloaded from R2
by Render on every cold start, so doubling it costs startup time on every deploy for
data we would only reduce to one integer. The export therefore carries a derived
`registered_states_bc_count INTEGER` instead.

## The output contract

### Normal advisor

```json
"advisory_states": ["Florida", "Texas"],
"also_registered_as_broker": {
  "state_count": 29,
  "note": "Registered as a broker-dealer agent in 29 states. Selling securities is not the same as giving investment advice. See the BrokerCheck record linked in this response."
}
```

`also_registered_as_broker` is present only when `registered_states_bc_count` is a
positive integer — 329,689 of 412,580 exported advisors (79.9%). It is omitted
entirely otherwise, never rendered as zero. The server treats `0` and `NULL` the same
way: omit. It must not compare a `NULL` with `>`.

The number inside `note` is templated from `state_count` in the same expression that
sets the field. It is never built from a second variable. This is the defect class we
are already fixing once — a count embedded in a sentence that nothing enforces.

**This block appears in `check_advisor` as well as `get_advisor`.** `check_advisor` is
the tool a consumer reaches for when asking "can this person advise me in Ohio?", and
it is where the answer changes most: 2 states where it showed 29. Without the broker
block, that tool answers a bare "no" for a person who is in fact a registered broker
in Ohio — the exact failure in the opposite direction that decision 2 exists to
prevent.

### Advisor with a continuing-education course outstanding (1,802 exported)

```json
"advisory_states": ["California", "Texas"],
"advisory_states_pending_ce": ["California"],
"advisory_states_pending_ce_note": "Registration is in place in these states. An annual continuing-education course is outstanding. This is an administrative status, not a restriction and not a disclosure event."
```

The pending-CE states are a subset of `advisory_states` and are listed in both. They
are never added to the advisory count, and never presented as a problem with the
advisor.

### Advisor with no advisory states (3,479 exported)

```json
"advisory_states": [],
"advisory_states_note": "No state investment-adviser registrations are on record for this person in the SEC data. This is not a disclosure or disciplinary finding. Check the IAPD record linked in this response."
```

This case matters most. 2,378 of these advisors show a long state list today, so the
list visibly empties. The note must read as *none on record*, never as *unknown* and
never as a finding against the advisor. An empty array with no note is not acceptable
output.

**A hazard worth naming.** The NONE-vs-UNAVAILABLE choice is keyed on whether an
`iar_details` row exists, not on whether scope was actually fetched. All 416,491
rows today carry fetched scope, so the split is safe in practice. But if a future
partial or interrupted scope fetch ever inserts a detail row before its IA/BC scope
is populated, that row's `registered_states_ia` would be NULL, and this same logic
would confidently report NONE — a false negative about a real person's registration.
The durable fix at that point is to key UNAVAILABLE on the presence of scope data,
not row presence, or to have the export refuse to write a detail row with unfetched
scope.

**One empty array is not this case.** `_regulatory_join()` (`server.py:429`) returns
`registered_states: []` for a marketplace member whose CRD is not in the SEC roster —
132 of 294 members, adjudicated on 2026-08-09 as a real path. That empty list means
*not matched to the SEC roster*, which is a different fact from *no advisory
registrations on record*. It renames to `advisory_states` with the rest, but it must
**not** carry the zero-state note. It keeps the existing labeled fallback wording.

### Employment

```json
"employment": [{
  "firm_name": "J.P. Morgan Securities LLC",
  "is_current": true,
  "start_date": "2023-06-06",
  "end_date": null,
  "branch_city": "Naples",
  "branch_state": "FL",
  "capacity": "investment adviser and broker-dealer"
}]
```

**Merge rule.** Group the rows of one advisor by
`(firm_crd, is_current, start_date, end_date)`, falling back to `firm_name` when
`firm_crd` is null. Grouping must include the dates — keying on firm alone would
merge a genuine rehire into one row.

**Branch tie-break.** When merged rows disagree, take the branch city and state from
the `ia_only = 'Y'` row. This is an advisory product, so the advisory registration's
office is the right one to show. The pair also differs in letter case
(`NAPLES` / `Naples`); existing title-casing already handles that.

**Capacity values.** Derived from the `ia_only` flags present in the group:

| Flags in group | `capacity` |
|---|---|
| `Y` and `N` | `"investment adviser and broker-dealer"` |
| `Y` only | `"investment adviser"` |
| `N` only | `"broker-dealer"` |
| any flag null | field omitted |

`ia_only` is present on 415,032 of 416,491 rows (99.6%). For the remaining 0.4% the
field is omitted. **Never guess a capacity.** A missing flag is not an adviser flag.

## Changes by repository

### firm-intelligence — `scripts/build_mcp_public_db.py`

1. `EXPORT_SCHEMA["iar_details"]` (line ~166): remove `registered_states`; add
   `registered_states_ia`, `registered_states_ia_pending_ce`, and
   `registered_states_bc_count INTEGER`. The post-build audit enforces this
   whitelist and fails loudly on a mistake — that is the intended safety net.
2. Derive `registered_states_bc_count` at export time from
   `registered_states_scoped`: the count of distinct states where
   `regScope = 'BC'` and `status` is in the same five-value set used for `IA`.
   The raw JSON stays out of the export. Write **`0`, not `NULL`**, when an advisor
   has no broker states, so the column carries one meaning. Write `NULL` only when
   `registered_states_scoped` itself is absent, which means *not fetched*, not *zero*.
   The server omits the block for both values.
3. `EXPORT_SCHEMA["advisor_employments"]`: add `ia_only TEXT`.
4. `project_employments()`: carry `e.get("iaOnly")` into the new column. Do not
   collapse rows here — the merge is the server's job, and the export stays a
   faithful record.
5. `schema_version` 3 → 4 (line ~728).

### advisorfinder-mcp

1. `SCHEMA_VERSION` 3 → 4. The server refuses to boot on mismatch, which is what
   makes the ship order below safe.
2. `db.py`: select the new columns.
3. `server.py`: replace `registered_states` with the new `advisory_states` contract at
   all four sites — `get_advisor` (line 264), `_check_verdict` for `check_advisor`
   (line 319), and both branches of `_regulatory_join` for `find_bookable_advisors`
   (lines 429 and 436). Add the employment merge. `_split_states()` no longer needs to
   de-duplicate, because the source column is already distinct — but keep it
   defensive.
4. Copy sweep. Per the project rule, `resources.py` + `README.md` +
   `docs/mcp-help.md` change together or not at all. Also update the `get_advisor`,
   `check_advisor`, and `search_advisors` docstrings, which are live tool output and
   currently promise "registered states".

## Testing

New tests, all against the regenerated fixture:

1. An advisor with both registrations returns advisory states only, and the count
   matches `registered_states_ia`.
2. `also_registered_as_broker` is present when the count is positive and absent when
   the count is `0` **and** when it is `NULL`. It appears in both `get_advisor` and
   `check_advisor`, and its note repeats the same number as `state_count`.
3. A pending-CE advisor lists the state in both arrays and carries the note.
4. A zero-advisory-state advisor returns an empty array **and** the note.
5. Duplicate job rows merge to one row with the correct capacity.
6. A job with a null `ia_only` omits `capacity` rather than guessing.
7. A genuine rehire at the same firm does **not** merge.
8. A marketplace member whose CRD is not in the SEC roster returns an empty
   `advisory_states` with the existing unmatched-CRD wording, and **not** the
   zero-state note.
9. No tool output anywhere contains the mixed `registered_states` key.
10. The existing no-endorsement and coverage-wording tests still pass over the new
   copy.

Fixtures are regenerated with the real firm-intelligence scripts per
`tests/fixtures/README.md`. They are never hand-edited. The fixture set must include
one advisor of each of the three state cases and one duplicate-job advisor.

## Ship order

`schema_version` moves, so the order is fixed and not optional:

1. Rebuild the export from the re-fetched `firms.db`.
2. Run the real-data gate, including CRD 6805515 end to end.
3. Upload the new database and manifest to R2 (`rclone` remote `r2-adfi`).
4. Push `main`. Render auto-deploys, health-gated.
5. Verify the live tool output for CRD 6805515 returns Florida and Texas.

## Out of scope

Named here so the work does not grow:

- **The public website carries the same defect.** `dedupe_states()` in
  `build_public_export.py` collapsed the repeat, which removed the visible symptom and
  kept broker states inside a list the site labels advisory. The UAT directory upload
  of 2026-08-12 carries it. This is a separate fix in a separate repo and Will's team
  should be told. It is likely a larger exposure than the MCP.
- **A FINRA data agreement** for the Composite Individual and Individual Delta
  datasets. It solves this permanently and also solves refresh. Business action, not
  engineering.
- **`ia_reps.branch_states` duplicates** (67.5%). No consumer reads that column.
- **Marketplace `allowedStates`** is self-reported by the member and is not touched by
  this change.
- **The stale Datasette copy** `db/serve/firms_serve.db` — 213,697 rows against
  416,491.

## The note strings are APPROVED

**Approved by Drew on 2026-08-18.** There are FOUR, not three: planning surfaced an
advisor with no `iar_details` row at all, which is neither "none on record" nor a
normal list. The approved set, and the four defects the review round caught, are
recorded in the plan at Task 2 Step 3 — that block is the source of truth for the
exact prose.

The broker note, the pending-CE note, and the zero-state note are new consumer-facing
regulatory copy. Existing coverage copy in this repo is validated and cited, so new
copy of this kind does not go straight to code. The strings shown above are drafts.
They state only what the data says and deliberately avoid explaining registration law.

Four defects were fixed during that review, and each is now a tested property:

- **"in 1 states"** — about 35,000 advisors have exactly one broker state, so the
  broker note takes a pre-built count phrase and renders "1 state".
- **A presupposing "Also"** — for the 3,479 zero-advisory-state advisors the broker
  note sits beside the none-on-record note, where "Also registered as a broker" would
  imply an advisory registration the person does not have. The word is gone.
- **Positional "below"** — the payload is JSON rendered in any order by the consuming
  assistant, so no note may claim a position. All say "linked in this response".
- **"this state" on an array** — 214 of 1,811 pending-CE advisors have more than one
  such state, so that note is now plural-safe.

Standing rules, unchanged: each string lives in exactly one place so a later wording
change is a text edit; tests assert presence, placement and the four properties above,
never full prose.
