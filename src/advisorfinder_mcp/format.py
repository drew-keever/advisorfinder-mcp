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
