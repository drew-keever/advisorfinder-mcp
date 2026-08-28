"""Formatting/wording helpers shared by server.py's tools.

This is the ONE place site links, disclosure phrasing, and fee phrasing come
from — future changes to advisorfinder.com deep links, or to how we phrase
disclosure/fee caveats, only ever touch this file.
"""
import json
import re
import unicodedata
import urllib.parse
from datetime import date, datetime
from typing import Any


def advisorfinder_url() -> str:
    return "https://advisorfinder.com"


def advisorfinder_profile_url(row_or_none: Any) -> str:
    """The deep link envelope() uses for its top-level advisorfinder.link:
    a marketplace member's own AdvisorFinder profile URL when `row_or_none`
    is a marketplace_advisors row (or any row-like object with a truthy
    `profile_url` column/key), else the generic advisorfinder.com homepage.
    Works with dict, sqlite3.Row, or None (via _row_value, defined below)."""
    url = _row_value(row_or_none, "profile_url")
    return url if url else advisorfinder_url()


# ── name title-casing ─────────────────────────────────────────────────────────
# Copied from firm-intelligence scripts/build_public_export.py (lines ~54-75)
# — keep in sync. Handles O'Hearn/McDonald-style prefixes, hyphenated names, and
# roman-numeral/Jr/Sr suffixes. Idempotent on already-cased input.

_SUFFIXES = {"ii", "iii", "iv", "jr", "sr"}
_LOWER = {"of", "and", "the", "for"}


def title_case_name(s: str | None) -> str | None:
    if not s:
        return None
    out = []
    for word in s.strip().lower().split():
        if word in _SUFFIXES:
            out.append(word.upper() if word in {"ii", "iii", "iv"} else word.capitalize())
            continue

        def cap_part(p):
            if not p:
                return p
            if p.startswith("mc") and len(p) > 2:
                return "Mc" + p[2:].capitalize()
            if "'" in p:
                a, _, b = p.partition("'")
                return a.capitalize() + "'" + b.capitalize()
            return p.capitalize()

        word = "-".join(cap_part(p) for p in word.split("-"))
        out.append(word)
    return " ".join(out)


# Legal-entity designators that must stay uppercase in firm names ("LLC", not
# "Llc"). Compared with dots/commas stripped so "L.P.," matches "LP".
_FIRM_ACRONYMS = {"LLC", "LLP", "LLLP", "LP", "PLLC", "PLC", "PC", "PA"}


def title_case_firm_name(s: str | None) -> str | None:
    cased = title_case_name(s)
    if not cased:
        return cased
    return " ".join(
        tok.upper() if tok.strip(",.").replace(".", "").upper() in _FIRM_ACRONYMS else tok
        for tok in cased.split(" ")
    )


# ── verification link builders ────────────────────────────────────────────────

def iapd_individual_url(crd) -> str:
    return f"https://adviserinfo.sec.gov/individual/summary/{crd}"


def iapd_firm_url(crd) -> str:
    return f"https://adviserinfo.sec.gov/firm/summary/{crd}"


def brokercheck_individual_url(crd) -> str:
    return f"https://brokercheck.finra.org/individual/summary/{crd}"


def brokercheck_firm_url(crd) -> str:
    return f"https://brokercheck.finra.org/firm/summary/{crd}"


def name_search_urls(name: str) -> dict:
    """v1-style fallback search links for not-found responses, keyed by
    source, so a caller can suggest "search by name instead" when a CRD lookup
    misses."""
    q = urllib.parse.quote(name or "")
    return {
        "iapd": f"https://adviserinfo.sec.gov/search/genericsearch/gridCurrent498?query={q}",
        "brokercheck": f"https://brokercheck.finra.org/search?query={q}",
    }


def iapd_firm_search_url(name: str) -> str:
    q = urllib.parse.quote(name or "")
    return f"https://adviserinfo.sec.gov/search/genericsearch/gridCurrent498?query={q}"


def brokercheck_firm_search_url(name: str) -> str:
    q = urllib.parse.quote(name or "")
    return f"https://brokercheck.finra.org/search?query={q}"


# ── disclosure status (the four-state contract) ──────────────────────────────

def _row_value(row, key, default=None):
    """Works for dict, sqlite3.Row, or None. sqlite3.Row raises IndexError
    (not KeyError) for a missing column name."""
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def disclosure_status(has_disclosure: str | None, iar_row: Any) -> dict:
    """Four states, keyed on ia_reps.has_disclosure ONLY — iar_row presence
    only distinguishes the two Y sub-states, it never demotes a Y or N rep to
    'unknown'. (CORRECTED CONTRACT — see task-2-report.md's post-review-fix
    section: the original brief checked "no iar_details row" first, which
    contradicted the export script's own aggregate tally, where hd=='N'
    counts as none_reported unconditionally regardless of row existence. This
    version resolves that contradiction: has_disclosure is the authoritative
    roster flag; a 'Y' rep with no detail row must not soften to "unknown"
    since that understates a known disclosure.)
      has_disclosure == 'Y' AND row exists AND (disclosure_count or 0) > 0
          -> disclosed_with_detail
      has_disclosure == 'Y' otherwise (row with count 0/NULL, OR no row at all)
          -> disclosed_no_detail
      has_disclosure == 'N' (regardless of row existence)
          -> none_reported
      has_disclosure NULL/empty/anything else
          -> unknown (never phrased as "no disclosures")
    """
    if has_disclosure == "Y":
        count = _row_value(iar_row, "disclosure_count", 0) or 0
        if count > 0:
            return {
                "status": "disclosed_with_detail",
                "disclosure_count": count,
                "guidance": (
                    f"This advisor has {count} disclosure event(s) on record. "
                    "Review the full record on FINRA BrokerCheck before making decisions."
                ),
            }
        return {
            "status": "disclosed_no_detail",
            "guidance": (
                "This advisor has disclosure(s) on record. "
                "Review the full record on FINRA BrokerCheck."
            ),
        }
    if has_disclosure == "N":
        return {"status": "none_reported", "guidance": "No disclosures reported."}
    return {
        "status": "unknown",
        "guidance": (
            "Disclosure status not available in our data — verify on "
            "FINRA BrokerCheck / SEC IAPD."
        ),
    }


# ── fee_block ─────────────────────────────────────────────────────────────────

def fee_block(part2a_row: Any) -> dict | None:
    """None if the firm has no firm_part2a row. Never emits a fee number
    without a "basis" field alongside it."""
    if part2a_row is None:
        return None

    raw_tiers = _row_value(part2a_row, "fee_tiers_json")
    tiers = None
    if raw_tiers:
        try:
            tiers = json.loads(raw_tiers)
        except (json.JSONDecodeError, TypeError):
            tiers = None

    is_estimate = bool(_row_value(part2a_row, "fee_rate_is_estimate", 0))
    needs_review = bool(_row_value(part2a_row, "needs_review", 0))

    block = {
        "rate": _row_value(part2a_row, "fee_rate_blended"),
        "tiers": tiers,
        "notes": _row_value(part2a_row, "fee_notes"),
        "account_minimum": _row_value(part2a_row, "account_minimum"),
        "account_minimum_waivable": bool(_row_value(part2a_row, "account_minimum_waivable", 0)),
        "brochure_date": _row_value(part2a_row, "brochure_date"),
    }

    if is_estimate or needs_review:
        block["basis"] = "estimated from ADV Part 2A filing"
        block["disclaimer"] = (
            "Estimated from the firm's ADV brochure; confirm current fees with the firm."
        )
    else:
        block["basis"] = "as filed in ADV Part 2A"

    return block


# ── marketplace listing block (labeled enrichment, never an endorsement) ─────
# The single fixed disclosure note required verbatim by task-4-brief.md /
# global-constraints.md -- every marketplace-member result gets exactly this
# wording, never "endorsed"/"recommended by AdvisorFinder"/"vetted by
# AdvisorFinder" (test-enforced: tests/test_no_endorsement_wording.py).
_MARKETPLACE_NOTE = "This advisor is listed on AdvisorFinder — view their full profile and contact them."

# The fixed label for self-reported financials (aum/clientNumber) sourced from
# a marketplace_advisors row -- these are NOT regulatory AUM figures, they're
# whatever the advisor typed into their own AdvisorFinder profile.
SELF_REPORTED_LABEL = "as listed on their AdvisorFinder profile"


def marketplace_block(row: Any) -> dict:
    """The labeled marketplace-listing block attached to any tool result for
    a marketplace member: deep link, job title, pricing, and the fixed
    disclosure note above. `row` is a marketplace_advisors row (sqlite3.Row
    or dict) as returned by db.get_marketplace_by_crd()/db.search_marketplace().
    Never render this data without the note -- that's what keeps a business
    listing from reading as an endorsement."""
    return {
        "profile_url": _row_value(row, "profile_url"),
        "job_title": _row_value(row, "jobTitle"),
        "pricing": _row_value(row, "pricing"),
        "note": _MARKETPLACE_NOTE,
    }


# ── marketplace member with no regulatory-dataset match (Gate A2 Ruling 1) ───
# ADJUDICATED 2026-08-09 (Gate A2): 132/294 real marketplace members are
# state-registered/BD-side advisors whose CRDs legitimately never appear in
# ia_reps (the SEC roster) -- sanitize_marketplace.py ships their row anyway
# (crd_mismatches is reported but no longer build-fatal, see
# scripts/sanitize_marketplace.py in the firm-intelligence repo). This is
# therefore a REAL, reachable server path, not a defensive fallback for data
# that "should never happen" -- exactly the note below, verbatim, plus the
# same IAPD/BrokerCheck verify links check_advisor uses for any other CRD.
_MARKETPLACE_CRD_UNMATCHED_NOTE = (
    "Regulatory records for this advisor aren't in our SEC dataset (likely "
    "state-registered) — verify on FINRA BrokerCheck / SEC IAPD."
)


def marketplace_unmatched_crd_disclosure(crd) -> dict:
    """Labeled fallback disclosure block for a marketplace member whose crd
    has no row in ia_reps (db.get_advisor(crd) returns None). Auto-upgrades to
    an ordinary disclosure_status() join the moment the state-IAR ingest lands
    and the CRD starts resolving -- this function is only ever reached for the
    CRDs that don't resolve today."""
    return {
        "status": "not_in_regulatory_dataset",
        "note": _MARKETPLACE_CRD_UNMATCHED_NOTE,
        "verify": {
            "iapd": iapd_individual_url(crd),
            "brokercheck": brokercheck_individual_url(crd),
        },
    }


# ── FTS query sanitizer ───────────────────────────────────────────────────────
# Lives here (not db.py) because it's pure text processing with no DB
# dependency; db.py's search queries import and call it.
#
# Unicode-aware by design: the export's advisor_fts/firm_fts tables are built
# with tokenize='unicode61 remove_diacritics 2' (build_mcp_public_db.py), which
# (a) folds Latin diacritics to their plain-ASCII equivalent for matching
# purposes and (b) tokenizes non-Latin scripts (e.g. CJK) as ordinary letter
# tokens, not garbage to be stripped. A sanitizer that only kept
# [A-Za-z0-9\s'-] mangled both: "José García" survived as fragments
# ("Jos"/"Garc"/"a") that could never MATCH the indexed row, and "李明" had
# every character stripped, sanitizing to None — which callers (correctly, for
# the *absent-filter* case) treat as "no name filter", silently turning a
# supplied-but-mangled name into an unfiltered browse. See server.py's
# per-tool guards for the caller-side half of that fix: a name/firm the caller
# actually supplied must never silently sanitize away.
_DISALLOWED_RE = re.compile(r"[^\w\s'\-]", re.UNICODE)


def _fold_diacritics(s: str) -> str:
    """NFKD-normalize and drop combining marks, e.g. 'José' -> 'Jose'. A
    no-op for scripts with no combining marks to strip (e.g. CJK), so it's
    safe to apply unconditionally before the disallowed-character sweep."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def fts_query(raw: str | None) -> str | None:
    """Sanitize free-text user input into a safe SQLite FTS5 MATCH query:
    fold diacritics to ASCII (to match the index's remove_diacritics=2
    tokenizer), strip everything except unicode word characters (letters in
    any script, digits — but not underscore)/whitespace/apostrophe/hyphen,
    split into tokens, drop any token with no alphanumeric character, quote
    each surviving token as a phrase, star the last token (prefix match), join
    with spaces (FTS5's implicit AND). Returns None if nothing survives —
    callers treat that as "no name filter", not an error (see server.py for
    why a *supplied* filter sanitizing to None must be rejected instead of
    silently reaching this "absent" path).
    """
    if not raw:
        return None
    folded = _fold_diacritics(raw)
    cleaned = _DISALLOWED_RE.sub(" ", folded).replace("_", " ")
    tokens = [t for t in cleaned.split() if any(c.isalnum() for c in t)]
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens]
    quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)


# ── flexible date parsing (SEC data mixes ISO and M/D/YYYY) ──────────────────

def parse_flexible_date(raw: str | None) -> date | None:
    """Tries ISO (YYYY-MM-DD) then M/D/YYYY; never raises — unparseable input
    (including garbage strings) returns None."""
    if not raw:
        return None
    for fmt_str in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt_str).date()
        except ValueError:
            continue
    return None


def years_since(raw: str | None) -> int | None:
    """Whole years between a flexibly-parsed date and today. None if the date
    can't be parsed at all — never guesses."""
    d = parse_flexible_date(raw)
    if d is None:
        return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


# ── envelope ──────────────────────────────────────────────────────────────────

def envelope(
    payload: dict,
    *,
    caveats: list[str] | None = None,
    verify: dict | None = None,
    marketplace_row: Any = None,
) -> dict:
    """Wraps every tool's return value with the shared honesty scaffolding:
    data vintage, the advisorfinder.com link-out, verification links, and any
    coverage caveats the caller collected while building the response.

    `marketplace_row` (Task 4, marketplace-layer): pass the caller's
    marketplace_advisors row when the tool's subject is a single advisor
    who's also a marketplace member (get_advisor/check_advisor) -- the
    top-level advisorfinder.link then deep-links to that member's own
    profile via advisorfinder_profile_url() instead of the generic
    homepage. Left as None (the default) for list-shaped/multi-advisor tools
    (search_advisors, find_bookable_advisors, get_database_stats, ...),
    where a single per-response link to one advisor wouldn't make sense."""
    from . import db  # local import: db.py never imports format.py, so this
    # is a one-directional dependency, not a cycle — deferred only to keep
    # format.py importable in isolation (e.g. before a DB path is set).

    meta = db.get_meta()
    data_as_of = (
        f"advisor data as of {meta.get('ia_reps_as_of')}; "
        f"firm data as of {meta.get('firms_as_of')}"
    )
    return {
        **payload,
        "data_as_of": data_as_of,
        "advisorfinder": {
            "link": advisorfinder_profile_url(marketplace_row),
            "note": "Compare financial advisors on AdvisorFinder.",
        },
        "verify": verify or {},
        "coverage_caveats": caveats or [],
    }


# ── advisory-state wording (APPROVED by Drew 2026-08-18) ─────────────────────
# All four strings live here and nowhere else, so a later wording change stays a
# text edit. Tests assert placement, not prose.
#
# Four wording decisions are deliberate — do not "improve" them back:
#   - No "Also" in the broker note. For the 3,479 zero-advisory-state advisors
#     the broker note appears next to ADVISORY_STATES_NONE_NOTE, and "Also"
#     would presuppose an advisory registration the person does not have.
#   - "linked in this response", never "below". The payload is JSON read by an
#     assistant that renders keys in any order; there is no "below".
#   - The pending-CE note is plural-safe. 214 of 1,811 advisors have more than
#     one pending-CE state, so "this state" would be wrong for them.
#   - The broker note takes a pre-built count phrase, so "1 state" never
#     renders as "1 states". About 35,000 advisors have exactly one.

ADVISORY_STATES_NONE_NOTE = (
    "No state investment-adviser registrations are on record for this person in "
    "the SEC data. This is not a disclosure or disciplinary finding. Check the "
    "IAPD record linked in this response."
)

ADVISORY_STATES_UNAVAILABLE_NOTE = (
    "State registration detail is not available for this person in our data. It "
    "does not mean there are none. Check the IAPD record linked in this response."
)

ADVISORY_STATES_PENDING_CE_NOTE = (
    "Registration is in place in these states. An annual continuing-education "
    "course is outstanding. This is an administrative status, not a restriction "
    "and not a disclosure event."
)

BROKER_REGISTRATION_NOTE = (
    "Registered as a broker-dealer agent in {count_phrase}. Selling securities is "
    "not the same as giving investment advice. See the BrokerCheck record linked "
    "in this response."
)

CAPACITY_BOTH = "investment adviser and broker-dealer"
CAPACITY_IA = "investment adviser"
CAPACITY_BC = "broker-dealer"


# ── advisory states / broker registration / employment merge ────────────────

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
    state_count in this one expression — never built from a second variable.
    Singular is handled here too: "1 state", never "1 states"."""
    if iar_row is None:
        return None
    count = _row_value(iar_row, "registered_states_bc_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return None
    count_phrase = "1 state" if count == 1 else f"{count} states"
    return {
        "state_count": count,
        "note": BROKER_REGISTRATION_NOTE.format(count_phrase=count_phrase),
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
