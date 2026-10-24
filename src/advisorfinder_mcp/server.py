"""AdvisorFinder MCP server: 7 consumer-facing tools over a local scrubbed
SQLite export (mcp_public.db). Every response funnels back to
advisorfinder.com via format.envelope(); disclosure status is always one of
the four honest states (never phrased as "clean"/"safe"); designations are
always labeled self-reported.

Tool functions are plain module-level functions decorated with
`@mcp.tool(title=..., annotations=...)`. On fastmcp 3.4.x, the decorator
(bare or parametrized) returns the original function object unchanged
(verified: `mcp.tool(fn) is fn` and `mcp.tool(title=...)(fn) is fn`) — the
FunctionTool wrapper is registered internally and reachable via
`await mcp.get_tool(name)`, but the name in this module's namespace still
refers to the plain function. Tests therefore import
and call these functions directly (e.g. `server.search_advisors(...)`) with
no `.fn` indirection needed.
"""
import time

# Taken before the heavy fastmcp/mcp imports so main()'s boot log can show
# how long module import itself took.
_IMPORT_T0 = time.monotonic()

import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

from fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from . import bootstrap, db, format, guard, resources  # noqa: E402

# Named explicitly: under `python -m`, __name__ is "__main__".
log = logging.getLogger("advisorfinder_mcp.server")

mcp = FastMCP("advisorfinder", instructions=format.FAIR_USE_NOTICE)
resources.register(mcp)
# Per-client-IP rate limits and one usage log line per tool call
# (tools/call only; see guard.py for the limits and their env overrides).
mcp.add_middleware(guard.from_env())


def _read_only(title: str) -> ToolAnnotations:
    """MCP tool annotations shared by every tool: all 7 are pure lookups
    against a local, read-only SQLite export — no writes, no side effects,
    same answer for the same input, and no calls out to external systems."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _full_name(first, middle, last, suffix=None) -> str:
    return format.title_case_name(" ".join(p for p in (first, middle, last, suffix) if p))


def _individual_verify_links(crd: str) -> dict:
    return {
        "iapd": format.iapd_individual_url(crd),
        "brokercheck": format.brokercheck_individual_url(crd),
    }


def _parse_exam_json(raw) -> list:
    """Exam entries ({name, date, scope}) with `date` as YYYY-MM-DD (or None
    when absent/unreadable). Production stores M/D/YYYY."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {**e, "date": format.iso_date(e.get("date"))} if isinstance(e, dict) and "date" in e else e
        for e in data
    ]


def _firm_verify_links(crd: str) -> dict:
    return {
        "iapd": format.iapd_firm_url(crd),
        "brokercheck": format.brokercheck_firm_url(crd),
    }


def _unsanitizable_filter_error(label: str, value: str) -> dict:
    """A caller-supplied name/firm filter that sanitizes to nothing (e.g.
    punctuation-only, or — before the fts_query() unicode fix — a script
    fts_query's [A-Za-z0-9] whitelist couldn't tokenize at all) must never be
    silently dropped and treated as "no filter": that would fall through to
    an unfiltered browse and present arbitrary rows as if they matched the
    caller's search. Surface a clear error instead."""
    return format.envelope({
        "error": f"The provided {label} could not be used for search — it has no searchable characters.",
        "hint": "Try a plain-text name/firm using letters, spaces, apostrophes, or hyphens.",
    }, verify=format.name_search_urls(value))


def _unsanitizable_supplied_filter(**filters: str | None) -> tuple[str, str] | None:
    """Returns (label, value) for the first supplied (truthy) filter whose
    fts_query() sanitizes to None/empty, or None if every supplied filter
    survives sanitizing intact. Only checks filters that go through
    fts_query() (name/firm) — city/state are plain equality matches, not FTS."""
    for label, value in filters.items():
        if value and format.fts_query(value) is None:
            return label, value
    return None


_FEE_LABELS = {
    "fee_pct_of_aum": "percentage of AUM",
    "fee_hourly": "hourly charges",
    "fee_subscription": "subscription/fixed fees",
    "fee_fixed": "fixed fees",
    "fee_commissions": "commissions",
    "fee_performance_based": "performance-based fees",
    "fee_other": "other fee arrangements",
}


def _marketplace_listing_for(crd: str):
    """(marketplace_row, listing_dict) for `crd` -- listing_dict is
    format.marketplace_block(marketplace_row), or None when `crd` isn't a
    marketplace member (or this deployment has no marketplace data at all;
    db.get_marketplace_by_crd already returns None gracefully in that case).
    Shared by get_advisor/check_advisor/search_advisors's per-result
    enrichment (Task 4, marketplace-layer) -- one lookup per advisor CRD,
    called only on already-selected rows (post-ranking/post-limit), never
    folded into a search query's WHERE clause."""
    marketplace_row = db.get_marketplace_by_crd(crd)
    if marketplace_row is None:
        return None, None
    return marketplace_row, format.marketplace_block(marketplace_row)


def _json_list(raw) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _total_matches(total: int) -> dict:
    """{"total_matches": n} for a match count, capped at db.COUNT_CAP: a
    larger count gives total_matches = db.COUNT_CAP plus
    "total_matches_capped": True (the key is absent otherwise)."""
    if total > db.COUNT_CAP:
        return {"total_matches": db.COUNT_CAP, "total_matches_capped": True}
    return {"total_matches": total}


@mcp.tool(title="Search advisors", annotations=_read_only("Search advisors"))
def search_advisors(
    name: str | None = None,
    firm: str | None = None,
    city: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> dict:
    """Search SEC-registered investment adviser representatives (IARs) by
    name, firm, city, and/or state. No CRD number needed. At least one of
    name/firm/city/state is required — e.g. browse by city+state alone, or
    search by name.

    Returns, per match: CRD, name, the firm(s) they're linked to (with branch
    city/state), a four-state disclosure status (none_reported /
    disclosed_no_detail / disclosed_with_detail / unknown — never "clean"),
    and a link to the advisor's full SEC IAPD record. Data is derived from SEC
    IAPD / FINRA BrokerCheck registration feeds, not real-time. Exact name
    matches rank first. `total_matches` is the number of advisors that match
    the filters; `more_results_available` appears when more advisors match
    than the limit returns. Counting stops at 10,000: `total_matches_capped:
    true` means more than 10,000 advisors matched.
    """
    if not any([name, firm, city, state]):
        return format.envelope({
            "error": "At least one search filter is required.",
            "hint": (
                "Provide name, firm, city, and/or state — e.g. "
                "name='Jane Smith', or city='Boston' with state='MA' to browse."
            ),
        })

    unsanitizable = _unsanitizable_supplied_filter(name=name, firm=firm)
    if unsanitizable:
        return _unsanitizable_filter_error(*unsanitizable)

    clamped_limit = max(1, min(limit, 50))
    # Fetch one extra row so truncation is detectable without a second query.
    rows = db.search_advisors(name=name, firm=firm, city=city, state=state, limit=clamped_limit + 1)
    more_results = len(rows) > clamped_limit
    rows = rows[:clamped_limit]
    # One COUNT(*) over the same WHERE as the search (db._advisor_where),
    # stopped at db.COUNT_CAP + 1; skipped when the page already holds every
    # match.
    total = (
        db.count_advisors(name=name, firm=firm, city=city, state=state)
        if more_results else len(rows)
    )

    results = []
    for r in rows:
        full_name = " ".join(
            p for p in (r["first_name"], r["middle_name"], r["last_name"], r["name_suffix"])
            if p
        )
        firms_out = [
            {
                "crd_number": f["crd_number"],
                "name": format.title_case_firm_name(f["firm_name"]),
                "branch_city": format.title_case_name(f["branch_city"]) if f["branch_city"] else None,
                "branch_state": f["branch_state"],
            }
            for f in r["firms"]
        ]
        disclosure = format.disclosure_status(r["has_disclosure"], r["iar_row"])
        entry = {
            "crd": r["ind_source_id"],
            "name": format.title_case_name(full_name),
            "firms": firms_out,
            "disclosure": disclosure["status"],
            "iapd_link": format.iapd_individual_url(r["ind_source_id"]),
        }
        # Marketplace enrichment (Task 4): one lookup per already-ranked,
        # already-limited row -- never inside db.search_advisors()'s query,
        # so it can't change which rows match or their order.
        _, listing = _marketplace_listing_for(r["ind_source_id"])
        if listing is not None:
            entry["advisorfinder_listing"] = listing
        results.append(entry)

    payload = {"result_count": len(results), **_total_matches(total), "results": results}
    if more_results:
        payload["more_results_available"] = True
        payload["more_results_hint"] = (
            "More advisors matched than are shown. Narrow the search by firm, city, or state."
        )
    if not results:
        payload["not_found_guidance"] = (
            "No advisors matched your search. Try a different spelling, just "
            "a last name, or search the official sources directly."
        )

    # Generic name-search fallback links populate `verify` whenever there is
    # a name (or firm) to search for, whether or not this call found
    # anything. A city/state-only browse has no name to search, so it gets no
    # verify links (each result row still carries its own iapd_link) --
    # except on a miss, which gets the official search pages so a record we
    # do not hold can still be looked up.
    search_text = name or firm
    if search_text:
        verify = format.name_search_urls(search_text)
    elif not results:
        verify = format.official_search_home_urls()
    else:
        verify = None
    return format.envelope(payload, verify=verify)


@mcp.tool(title="Get advisor profile", annotations=_read_only("Get advisor profile"))
def get_advisor(crd: str) -> dict:
    """Get the full profile for an investment adviser representative (IAR) by
    CRD number — no need to search first if you already have it. Returns
    employment history (current firm first, then previous), exams passed,
    the states where they hold an investment-adviser registration
    (`advisory_states`; broker-dealer state registrations are reported
    separately as a labeled count in `also_registered_as_broker`, because
    selling securities is not the same as giving investment advice),
    professional designations (always labeled
    self-reported — none of this data is independently verified), years in
    the industry, and four-state disclosure status (never phrased as
    "clean"/"safe": none_reported / disclosed_no_detail / disclosed_with_detail
    / unknown). Data is derived from SEC IAPD / FINRA BrokerCheck. All dates
    are YYYY-MM-DD. For an advisor who is also listed on AdvisorFinder,
    `advisorfinder_listing.self_reported` carries the credentials and years
    of experience from that profile, labeled as such. Each designation has a
    `source` (e.g. "Form ADV Part 2B brochure supplement"). When no
    designations are on file but the profile lists some,
    `designations_coverage_note` explains that the public data covers few
    advisors; when both lists are present and not the same,
    `credential_sources_note` says so. Neither note is a finding against the
    advisor.
    """
    bundle = db.get_advisor(crd)
    if bundle is None:
        return format.envelope(
            {
                "found": False,
                "crd": crd,
                "message": f"No advisor found with CRD {crd} in our data.",
            },
            verify=_individual_verify_links(crd),
        )

    rep = bundle["rep"]
    iar = bundle["iar"]
    content = bundle["content"]

    # merge_employment_rows() collapses the adviser/broker row pair IAPD
    # returns for one job, and labels the capacity when it is derivable.
    # Dates are normalized AFTER the merge: merge_employment_rows() groups on
    # the raw start/end strings.
    employment = [
        {
            "firm_name": format.title_case_firm_name(e["firm_name"]),
            "is_current": e["is_current"],
            "start_date": format.iso_date(e["start_date"]),
            "end_date": format.iso_date(e["end_date"]),
            "branch_city": format.title_case_name(e["branch_city"]) if e["branch_city"] else None,
            "branch_state": e["branch_state"],
            **({"capacity": e["capacity"]} if "capacity" in e else {}),
        }
        for e in format.merge_employment_rows(bundle["employments"])
    ]

    exams = {
        "state": _parse_exam_json(iar["state_exams"]) if iar else [],
        "product": _parse_exam_json(iar["product_exams"]) if iar else [],
        "principal": _parse_exam_json(iar["principal_exams"]) if iar else [],
    }

    designations = [
        {
            "code": d["code"], "name": d["name"], "issuing_body": d["issuing_body"],
            "status": "self-reported",
            "source": format.designation_source_label(d.get("source")),
        }
        for d in bundle["designations"]
    ]

    caveats = []
    bio = None
    title = None
    if content:
        bio = content["bio"]
        title = content["title_clean"]
        if content["match_method"] == "name_unique":
            caveats.append("Bio matched by name within firm — verify it refers to the right person.")

    raw_start = iar["industry_start_date"] if iar else None
    industry_start_date = format.iso_date(raw_start)
    years_in_industry = format.years_since(raw_start)

    disclosure = format.disclosure_status(rep["has_disclosure"], iar)

    payload = {
        "found": True,
        "crd": crd,
        "name": _full_name(rep["first_name"], rep["middle_name"], rep["last_name"], rep["name_suffix"]),
        "title": title,
        "bio": bio,
        "employment": employment,
        "exams": exams,
        **format.advisory_states_block(iar),
        "designations": designations,
        "industry_start_date": industry_start_date,
        "years_in_industry": years_in_industry,
        "disclosure": disclosure,
    }

    # Broker-dealer state registrations, labeled as such: present only when
    # there is a real count to report (see format.broker_registration_block).
    broker = format.broker_registration_block(iar)
    if broker is not None:
        payload["also_registered_as_broker"] = broker

    # Marketplace enrichment (Task 4): a labeled listing block, present only
    # for advisors who are also AdvisorFinder marketplace members. The
    # envelope's own advisorfinder.link deep-links to that member's profile
    # instead of the generic homepage.
    marketplace_row, listing = _marketplace_listing_for(crd)
    if listing is not None:
        # The profile's own credentials and experience, labeled as from the
        # AdvisorFinder profile. Only the designation lists are compared
        # (exam licenses and degrees left out); experience is never compared.
        # No designations on file is missing public data, not a difference.
        profile = format.marketplace_profile_facts(marketplace_row)
        listing["self_reported"] = profile
        payload["advisorfinder_listing"] = listing
        if format.designations_coverage_gap(bundle["designations"], profile["credentials"]):
            payload["designations_coverage_note"] = format.DESIGNATIONS_COVERAGE_NOTE
        elif format.credential_sources_differ(bundle["designations"], profile["credentials"]):
            payload["credential_sources_note"] = format.CREDENTIAL_SOURCES_NOTE

    return format.envelope(
        payload,
        caveats=caveats,
        verify=_individual_verify_links(crd),
        marketplace_row=marketplace_row,
    )


def _check_verdict(crd: str, bundle: dict) -> dict:
    rep = bundle["rep"]
    iar = bundle["iar"]
    raw_start = iar["industry_start_date"] if iar else None
    disclosure = format.disclosure_status(rep["has_disclosure"], iar)
    # "Can this person advise me in my state?" is the question this tool
    # answers, so the broker-dealer fact belongs here too — a bare "no" about
    # a registered broker would mislead.
    registration = {
        "active": rep["ia_scope"] == "Active",
        **format.advisory_states_block(iar),
    }
    broker = format.broker_registration_block(iar)
    if broker is not None:
        registration["also_registered_as_broker"] = broker
    payload = {
        "found": True,
        "crd": crd,
        "name": _full_name(rep["first_name"], rep["middle_name"], rep["last_name"], rep["name_suffix"]),
        "registration": registration,
        "experience": {
            "industry_start_date": format.iso_date(raw_start),
            "years_in_industry": format.years_since(raw_start),
        },
        "disclosure": disclosure,
    }

    # Marketplace enrichment (Task 4) -- same labeled listing block/deep-link
    # behavior as get_advisor, see _marketplace_listing_for()'s docstring.
    marketplace_row, listing = _marketplace_listing_for(crd)
    if listing is not None:
        payload["advisorfinder_listing"] = listing

    return format.envelope(
        payload,
        verify=_individual_verify_links(crd),
        marketplace_row=marketplace_row,
    )


@mcp.tool(title="Check an advisor", annotations=_read_only("Check an advisor"))
def check_advisor(name_or_crd: str, firm: str | None = None, state: str | None = None) -> dict:
    """Quick yes/no-style verification of an advisor: is their registration
    active, which states they hold an investment-adviser registration in
    (`advisory_states`, with any broker-dealer state registrations reported
    separately as a labeled count in `also_registered_as_broker`), how long
    they have been in the industry, and their four-state disclosure status.
    Accepts EITHER a CRD number (all digits) OR a name — optionally narrowed
    by firm/state to disambiguate common names. If a name matches more than
    one advisor, returns the candidates with their CRD numbers instead of
    guessing.
    This tool never returns a numeric risk score — only the underlying facts
    (registration status, disclosure status) so you can judge for yourself.
    """
    if name_or_crd.strip().isdigit():
        crd = name_or_crd.strip()
        bundle = db.get_advisor(crd)
        if bundle is None:
            return format.envelope(
                {
                    "found": False,
                    "crd": crd,
                    "message": f"No advisor found with CRD {crd} in our data.",
                },
                verify=_individual_verify_links(crd),
            )
        return _check_verdict(crd, bundle)

    unsanitizable = _unsanitizable_supplied_filter(name=name_or_crd, firm=firm)
    if unsanitizable:
        return _unsanitizable_filter_error(*unsanitizable)

    rows = db.search_advisors(name=name_or_crd, firm=firm, city=None, state=state, limit=6)

    if not rows:
        return format.envelope(
            {
                "found": False,
                "message": f"No advisor matched '{name_or_crd}'.",
            },
            verify=format.name_search_urls(name_or_crd),
        )

    if len(rows) > 1:
        candidates = []
        for r in rows[:5]:
            firm_name = r["firms"][0]["firm_name"] if r["firms"] else None
            candidates.append({
                "crd": r["ind_source_id"],
                "name": _full_name(r["first_name"], r["middle_name"], r["last_name"], r["name_suffix"]),
                "firm": format.title_case_firm_name(firm_name) if firm_name else None,
            })
        payload = {
            "ambiguous": True,
            "candidates": candidates,
            "hint": "Multiple advisors matched — re-run check_advisor with the CRD from one of these candidates.",
        }
        if len(rows) > 5:
            payload["more_candidates"] = True
            payload["hint"] = (
                "More advisors matched than are listed. Add a firm or state to narrow the "
                "search, or re-run check_advisor with the CRD from one of these candidates."
            )
        return format.envelope(payload, verify=format.name_search_urls(name_or_crd))

    crd = rows[0]["ind_source_id"]
    bundle = db.get_advisor(crd)
    return _check_verdict(crd, bundle)


def _regulatory_join(crd: str) -> tuple[dict, dict]:
    """(registration, disclosure) for a marketplace member's CRD, using the
    exact same db/format helpers check_advisor's _check_verdict does —
    registration.active + four-state disclosure.

    ADJUDICATED 2026-08-09 (Gate A2): the None branch below is a REAL,
    reachable path, not defensive dead code — some real marketplace members
    are state-registered/BD-side advisors legitimately absent from the SEC
    roster (ia_reps): 98 of 294 in the 2026-08-28 export. sanitize_marketplace.py ships their row anyway
    (crd_mismatches is reported but no longer build-fatal), so db.get_advisor(crd)
    genuinely returns None for them. Returns a labeled fallback disclosure
    block (format.marketplace_unmatched_crd_disclosure) instead of crashing or
    silently reporting the generic 'unknown' four-state status.

    That branch's empty advisory_states list means 'not matched to the SEC
    roster', which is a DIFFERENT fact from 'no advisory registrations on
    record'. It therefore never carries the zero-state note — the labeled
    disclosure block already states why the record is missing."""
    bundle = db.get_advisor(crd)
    if bundle is None:
        return (
            {"active": None, "advisory_states": []},
            format.marketplace_unmatched_crd_disclosure(crd),
        )
    rep = bundle["rep"]
    iar = bundle["iar"]
    registration = {
        "active": rep["ia_scope"] == "Active",
        **format.advisory_states_block(iar),
    }
    broker = format.broker_registration_block(iar)
    if broker is not None:
        registration["also_registered_as_broker"] = broker
    disclosure = format.disclosure_status(rep["has_disclosure"], iar)
    return registration, disclosure


# find_bookable_advisors' message while the marketplace is paused
# (db.marketplace_enabled() is False). No reason is given.
_MARKETPLACE_PAUSED_MESSAGE = (
    "AdvisorFinder marketplace listings are temporarily not available through "
    "this connector. You can browse advisors directly at https://advisorfinder.com."
)

_PROFILE_FIELDS_NOTE = (
    "allowed_states and virtual_meetings_offered are null when the member did "
    "not fill them in: null means not stated on the profile, not \"none\"."
)


@mcp.tool(title="Find bookable advisors", annotations=_read_only("Find bookable advisors"))
def find_bookable_advisors(
    specialty: str | None = None,
    city: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> dict:
    """Search advisors listed on AdvisorFinder's marketplace — professionals
    with public profiles you can view and contact directly. This searches
    only AdvisorFinder members (a few hundred advisors), not the full
    SEC roster.

    All filters are optional — browsing with none of them is fine, since this
    tool's scope is already narrow. `specialty` matches against the advisor's
    bio, client description, "in their own words" answers, and credential
    acronyms and names (case-insensitive substring; e.g. "CFP" finds CFP®
    holders). Returns, per member: their self-provided profile info (bio;
    `credentials` as [{acronym, name, status: "self-reported"}]; `education`
    as [{institution, degree, year}]; `pricing` as a list of {type, ...}
    entries such as {"type": "aum", "min": "0.50", "max": "1.00", "unit":
    "percent of assets"}, {"type": "flat_fee", "min", "max", "frequency"},
    {"type": "hourly", "rate"}, {"type": "one_time", "cost"} or
    {"type": "fees_information", "link"}, with values as listed on the
    profile and formatting normalized (type/frequency lowercased, a trailing
    "%" removed, unset markers dropped; the "percent of assets" unit only
    when the listed values are plain numbers); minimum account size; allowed
    states; `member_since` as YYYY-MM-DD; and more), a link to their full
    AdvisorFinder profile, AND regulatory facts for their CRD.
    `allowed_states` and `virtual_meetings_offered` are null when the member
    did not fill them in: null means not stated on the profile, not "none".
    Members whose CRD is in our SEC dataset get their registration status
    and four-state disclosure status (none_reported / disclosed_no_detail /
    disclosed_with_detail / unknown) from that dataset — never softened.
    Members whose CRD is NOT in our SEC dataset (typically state-registered
    or BD-side advisors) get the status `not_in_regulatory_dataset` with a
    labeled note saying so and FINRA BrokerCheck / SEC IAPD verify links —
    never presented as if clean. AUM and client-count figures are
    self-reported by the advisor, not regulatory data, and are always
    labeled as such. Results are in alphabetical order by name; membership
    or payment has no effect on the order. `total_matches` is the number of
    members that match the filters. Being listed here is a business
    relationship with AdvisorFinder, not an endorsement, and never affects
    how any advisor ranks in search results. The marketplace listings can be
    temporarily unavailable; in that case this tool returns
    `available: false` with no results.
    """
    # The pause switch is checked FIRST, so a paused deployment gets the
    # paused message, never the "no marketplace table" one below. The
    # message deliberately gives no reason for the pause.
    if not db.marketplace_enabled():
        return format.envelope({
            "available": False,
            "message": _MARKETPLACE_PAUSED_MESSAGE,
            "result_count": 0,
            "results": [],
        })
    if db.marketplace_stats() is None:
        return format.envelope({
            "available": False,
            "message": "Marketplace data not available in this deployment.",
            "result_count": 0,
            "results": [],
        })

    clamped_limit = max(1, min(limit, 50))
    rows, total = db.search_marketplace_with_total(
        specialty=specialty, city=city, state=state, limit=clamped_limit
    )

    results = []
    for row in rows:
        crd = row["crd"]
        # Regulatory join happens AFTER db.search_marketplace()'s own
        # ranking/limit — one db.get_advisor() per already-selected row,
        # never folded into that query, same discipline as the marketplace
        # enrichment in search_advisors/get_advisor/check_advisor above.
        registration, disclosure = _regulatory_join(crd)
        # Profile text goes through format.clean_text (mojibake repair; prose
        # fields through format.clean_prose, which also removes Excel export
        # artifacts) and the structured columns through the marketplace_*
        # builders (DynamoDB JSON -> plain values). quickFacts is not output:
        # in production it holds only UI field keys ("aum", "clientNumber"),
        # not content.
        clean = format.clean_text
        credentials = format.marketplace_credentials(row["credentials"])
        entry = {
            "crd": crd,
            # A trailing ", CFP®" is removed only when it is one of this
            # member's own listed credentials (format.marketplace_display_name).
            "name": format.marketplace_display_name(row["displayName"], credentials),
            "company": clean(row["companyName"]),
            "city": clean(row["city"]),
            "state": row["state"],
            "bio": format.clean_prose(row["bio"]),
            "credentials": credentials,
            "client_description": format.clean_prose(row["clientDescription"]),
            "min_account_size": row["minAccountSize"],
            "years_of_experience": row["yearsOfExperience"],
            # None when not filled in on the profile (see profile_fields_note).
            "virtual_meetings_offered": format.marketplace_flag(row["virtualMeetingsOffered"]),
            "allowed_states": format.marketplace_states(row["allowedStates"]),
            "member_since": format.iso_date(row["memberSince"]),
            # Links: "https://" added when the profile omits a scheme; any
            # non-http(s) scheme is dropped (format.normalize_url).
            "website": format.normalize_url(row["advisorWebsiteURL"]),
            "linkedin": format.normalize_url(row["linkedInURL"]),
            "twitter": format.normalize_url(row["twitterURL"]),
            "bio_video": format.normalize_url(row["bioVideoLink"]),
            "education": format.marketplace_education(row["education"]),
            "self_reported": {
                "aum": row["aum"],
                "client_number": row["clientNumber"],
                "label": format.SELF_REPORTED_LABEL,
            },
            "in_their_own_words": format.marketplace_text_list(row["in_their_own_words"]),
            "registration": registration,
            "disclosure": disclosure,
            **format.marketplace_block(row),  # profile_url, job_title, pricing, note
        }
        results.append(entry)

    payload = {"result_count": len(results), "total_matches": total, "results": results}
    if results:
        payload["profile_fields_note"] = _PROFILE_FIELDS_NOTE
    else:
        payload["not_found_guidance"] = (
            "No AdvisorFinder members matched your search. Try a different "
            "specialty, city, or state — or use search_advisors for the "
            "full SEC roster (not marketplace-only)."
        )
        # A miss still gets the official search pages.
        return format.envelope(payload, verify=format.official_search_home_urls())
    return format.envelope(payload)


# Short per-result form of get_firm's empty-roster caveat (same meaning).
_NO_ROSTER_NOTE = (
    "No individual advisor records on file for this firm; this does not "
    "necessarily mean it has no advisors. Check the firm's SEC IAPD record (link)."
)


@mcp.tool(title="Search firms", annotations=_read_only("Search firms"))
def search_firms(name: str | None = None, state: str | None = None, limit: int = 20) -> dict:
    """Search SEC- and state-registered investment adviser FIRMS by name
    and/or state. Matches on the firm's primary name, legal name, or any
    "also known as" prior name on file. Returns, per match: CRD, name, city/
    state, AUM band, `advisor_count` (the number of investment adviser
    representatives on file for the firm in our data — broker-dealer-only
    registrations are not counted; null for state-registered-only firms,
    which have no roster), and a link to the firm's SEC IAPD record.
    State-registered-only firms (not SEC-registered) are included but
    flagged — purely state-registered firms have no advisor rosters at all;
    state-listed firms showing rosters are dual-registered. Exact name matches
    rank first (the firm's own name, then an "also known as" name), then names
    that start with the search text (own names before "also known as" names),
    then other matches. Inside each group, firms with a larger SEC AUM band
    come first, then firms with more advisors on file (a state-registered-
    only firm ranks as an undisclosed band with no advisors). A result can
    carry `advisor_count_note` (no advisor records on file) or `unreadable_characters_note` (characters
    lost in the source data, shown as \ufffd). `total_matches` is the number
    of firms that match the filters. Counting stops at 10,000:
    `total_matches_capped: true` means more than 10,000 firms matched.
    """
    unsanitizable = _unsanitizable_supplied_filter(name=name)
    if unsanitizable:
        return _unsanitizable_filter_error(*unsanitizable)

    clamped_limit = max(1, min(limit, 50))
    rows, total = db.search_firms_with_total(name=name, state=state, limit=clamped_limit)

    results = []
    for r in rows:
        entry = {
            "crd": r["crd_number"],
            "name": format.title_case_firm_name(r["name"]),
            "city": format.title_case_name(r["city"]) if r["city"] else None,
            "state": r["state"],
            "aum_band": r["aum_band"],
            "advisor_count": r["advisor_count"],
            "link": format.iapd_firm_url(r["crd_number"]),
        }
        if r["kinds"] == {"other"} and r["other_name"]:
            entry["matched_as"] = f"also known as {format.title_case_firm_name(r['other_name'])}"
        if not r["state_only"] and r["advisor_count"] == 0:
            entry["advisor_count_note"] = _NO_ROSTER_NOTE
        if format.has_unreadable_chars(entry["name"], entry["city"], entry.get("matched_as")):
            entry["unreadable_characters_note"] = format.UNREADABLE_CHARS_NOTE
        if r["state_only"]:
            entry["caveat"] = (
                "State-registered adviser — advisor rosters cover SEC-registered "
                "firms only, so no roster is available here; verify on FINRA "
                "BrokerCheck / SEC IAPD."
            )
        results.append(entry)

    payload = {"result_count": len(results), **_total_matches(total), "results": results}
    if not results:
        payload["not_found_guidance"] = "No firms matched your search."

    # Same rationale as search_advisors: `verify` carries generic name-search
    # fallback links whenever there is a name to search, not just on a miss.
    # A state-only browse has no name, so it gets no verify links, except
    # on a miss, which gets the official search pages.
    verify = None
    if name:
        verify = {
            "iapd": format.iapd_firm_search_url(name),
            "brokercheck": format.brokercheck_firm_search_url(name),
        }
    elif not results:
        verify = format.official_search_home_urls()
    return format.envelope(payload, verify=verify)


_PRIVATE_RESIDENCE_NOTE = (
    "Address withheld — private residence (SEC privacy protection). These "
    "office locations are counted in private_residence_locations_withheld "
    "and are not listed in locations."
)


@mcp.tool(title="Get firm profile", annotations=_read_only("Get firm profile"))
def get_firm(crd: str) -> dict:
    """Get the full profile for an investment adviser FIRM by CRD number:
    identity, address, website, AUM band, total employees, how they charge
    (a list), any disclosure flag, state registrations (a list of
    {state, date}, date YYYY-MM-DD or null), office locations (private
    residences are not listed: their count is in
    `private_residence_locations_withheld`, addresses withheld per SEC
    privacy rules), prior/other names, fee details from their ADV Part 2A
    brochure (marked as estimated when it is, and basis "not extracted" when
    no fee details were extracted), and `advisor_count`: how many
    investment adviser representatives we have on file for them
    (broker-dealer-only registrations are not counted; an empty roster does
    not necessarily mean an empty firm — see the caveat when that happens).
    Client types are withheld (see client_types_note).
    State-registered-only firms get a reduced profile.
    """
    bundle = db.get_firm(crd)
    if bundle is None:
        return format.envelope(
            {
                "found": False,
                "crd": crd,
                "message": f"No firm found with CRD {crd} in our data.",
            },
            verify=_firm_verify_links(crd),
        )

    caveats = []

    if bundle["firm"] is None:
        s = bundle["state_firm"]
        caveats.append(
            "State-registered adviser — advisor rosters cover SEC-registered "
            "firms only, so no roster is available here; verify on FINRA "
            "BrokerCheck / SEC IAPD."
        )
        payload = {
            "found": True,
            "crd": crd,
            "reduced_profile": True,
            "name": format.title_case_firm_name(s["primary_name"]),
            "city": format.title_case_name(s["address_city"]) if s["address_city"] else None,
            "state": s["address_state"],
            "aum_band": s["aum_band"],
            "total_employees": s["total_employees"],
            # No roster for a purely state-registered firm (see caveat above).
            "advisor_count": None,
            "states_approved": format.state_registrations(s["states_approved"]),
        }
        if format.has_unreadable_chars(payload["name"], payload["city"]):
            caveats.append(format.UNREADABLE_CHARS_CAVEAT)
        return format.envelope(payload, caveats=caveats, verify=_firm_verify_links(crd))

    f = bundle["firm"]
    fees = [label for flag, label in _FEE_LABELS.items() if f[flag] == "Y"]

    # Private residences (no street on file) are counted, not listed: a list
    # of "address withheld" entries crowded out the real offices (Vanguard
    # Advisers: 22 of 25 locations).
    locations = []
    private_withheld = 0
    for loc in bundle["locations"]:
        if loc["private_res"] == "Y" and not loc["street1"]:
            private_withheld += 1
        else:
            locations.append({
                "street1": loc["street1"],
                "city": format.title_case_name(loc["city"]) if loc["city"] else None,
                "state": loc["state"],
                "zip": loc["zip"],
            })

    other_names = [format.title_case_firm_name(o["other_name"]) for o in bundle["other_names"]]

    content_out = None
    if bundle["content"]:
        c = bundle["content"]
        content_out = {
            "tagline": c["marketing_tagline"],
            "bio": c["firm_bio"],
            "year_founded": c["year_founded"],
            "services_description": c["services_description"],
            "custodians": _json_list(c["custodians"]),
        }
        if c["needs_review"]:
            content_out["note"] = "unverified — scraped from firm website"

    # The count is the firm's roster in our data (distinct investment adviser
    # representatives, ia_only = 'Y' -- see db.roster_counts). The
    # Form ADV `investment_adviser_reps` figure is deliberately not shown: it
    # is unreliable upstream (0 for firms with thousands of roster advisors)
    # and is dropped in schema 5.
    if bundle["roster_count"] == 0:
        caveats.append(
            "We don't have individual advisor records for this firm. An empty "
            "roster here does not necessarily mean the firm has no advisors — "
            "check the firm's SEC IAPD record (link in verify)."
        )

    payload = {
        "found": True,
        "crd": crd,
        "name": format.title_case_firm_name(f["primary_name"]),
        "legal_name": format.title_case_firm_name(f["legal_name"]) if f["legal_name"] else None,
        "address": {
            "street1": f["address_street1"],
            "city": format.title_case_name(f["address_city"]) if f["address_city"] else None,
            "state": f["address_state"],
            "zip": f["address_zip"],
        },
        "website": format.normalize_url(f["website_clean"]),
        "aum_band": f["aum_band"],
        "total_employees": f["total_employees"],
        "advisor_count": bundle["roster_count"],
        "client_types_note": (
            "Client types are not available in this data version. See Item 5.D "
            "of the firm's Form ADV on SEC IAPD (link in verify)."
        ),
        "fee_arrangements": fees,
        "any_disciplinary": f["any_disciplinary"],
        "state_registrations": format.state_registrations(f["state_registrations"]),
        "locations": locations,
        "other_names": other_names,
        "fees": format.fee_block(bundle["part2a"]),
        "content": content_out,
        # Same value as advisor_count; kept so existing clients of this
        # shipped key do not break.
        "advisor_roster_count": bundle["roster_count"],
    }
    if private_withheld:
        payload["private_residence_locations_withheld"] = private_withheld
        payload["private_residence_note"] = _PRIVATE_RESIDENCE_NOTE
    # Lost characters (U+FFFD) are shown as they are and labeled, never
    # guessed. Checks every returned name/address field.
    if format.has_unreadable_chars(
        payload["name"], payload["legal_name"],
        payload["address"]["street1"], payload["address"]["city"],
        *other_names,
        *(loc.get(k) for loc in locations for k in ("street1", "city")),
    ):
        caveats.append(format.UNREADABLE_CHARS_CAVEAT)
    return format.envelope(payload, caveats=caveats, verify=_firm_verify_links(crd))


@mcp.tool(title="Get database stats", annotations=_read_only("Get database stats"))
def get_database_stats() -> dict:
    """Overall stats about our data: how many firms and advisors we cover,
    when each data source was last refreshed (vintages), and a full
    four-state disclosure tally — INCLUDING the 'unknown' bucket (advisors
    where we simply don't have disclosure detail on file; that is not the
    same as a clean record). Also states coverage scope explicitly: we do
    not claim to cover the whole US advisor population, only SEC-registered
    firms and their active reps.
    """
    meta = db.get_meta()
    advisors_count = int(meta.get("advisors_count", 0))

    # Recomputed directly via db.disclosure_tally() — deliberately NOT read
    # from export_meta's disclosure_tally_* fields, even though the export
    # script (firm-intelligence repo, build_mcp_public_db.py) now implements
    # the exact same four-state contract as format.disclosure_status() and
    # its precomputed tally matches this recompute exactly. This is
    # decoupling/defense-in-depth, not a correction of a wrong export: the
    # server never has to trust that some future export-script change keeps
    # matching format.disclosure_status()'s bucketing — it derives the tally
    # from the same ia_reps/iar_details rows the per-advisor view reads, so
    # the aggregate and the per-advisor rendering can never silently diverge
    # again, regardless of what export_meta says.
    tally = db.disclosure_tally()

    state_firms_count = int(meta.get("state_firms_count", 0))
    state_firms_with_reps = int(meta.get("state_firms_with_reps", 0))

    # Marketplace count + snapshot date (Task 4, marketplace-layer): None
    # from db.marketplace_stats() means marketplace_advisors is absent
    # entirely (a v3 build without --marketplace) -- distinct from a real
    # build with zero sitemap-matched members, which reports member_count=0.
    # While the marketplace is paused (db.marketplace_enabled() is False) the
    # block shows only that status: no member count, no snapshot date.
    if db.marketplace_enabled():
        marketplace_stats = db.marketplace_stats()
        marketplace = {
            "member_count": marketplace_stats["count"] if marketplace_stats else 0,
            "snapshot_date": marketplace_stats["snapshot_date"] if marketplace_stats else None,
        }
    else:
        marketplace = {"status": "paused"}

    payload = {
        "firms_count": int(meta.get("firms_count", 0)),
        "state_firms_count": state_firms_count,
        "advisors_count": advisors_count,
        # export_meta's individuals_as_of is NOT a vintage of this data: it
        # dates the ADV Schedule A owners file (ia_individuals), a table the
        # export forbids. Nothing a tool returns comes from it.
        "vintages": {
            "advisors_as_of": meta.get("ia_reps_as_of"),
            "firms_as_of": meta.get("firms_as_of"),
        },
        "disclosure_tally": tally,
        "marketplace": marketplace,
        "coverage": {
            "state_firms_with_advisor_rosters": f"{state_firms_with_reps}/{state_firms_count}",
            "note": (
                "Advisor rosters cover SEC-registered advisers only, complete as "
                "of 2026-08 — purely state-registered firms have no rosters at "
                "all (a state-registered firm showing a roster is dual-registered "
                "with the SEC); an empty roster does not mean nobody works there."
            ),
        },
    }
    return format.envelope(payload)


def main() -> None:
    """Entry point: acquire the DB (bootstrap.ensure_db() — MCP_DB_PATH in
    dev/test, a verified R2 download in production), then serve over
    streamable HTTP. This is what the Dockerfile's CMD runs
    (`python -m advisorfinder_mcp.server`); the PyPI package's console
    script is a different entry point — see proxy.py."""
    # INFO only for this package's own loggers; the root stays at WARNING so
    # third-party INFO lines (httpx/botocore request logs, which can carry
    # URLs) are not turned on as a side effect.
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("advisorfinder_mcp").setLevel(logging.INFO)
    t0 = time.monotonic()
    log.info(
        "[boot +0.0s] start (module import took %.1fs)", t0 - _IMPORT_T0
    )
    db_path = bootstrap.ensure_db()
    port = int(os.environ.get("PORT", 8080))
    log.info(
        "[boot +%.1fs] DB ready (%s); server starting on 0.0.0.0:%d",
        time.monotonic() - t0, db_path, port,
    )
    # Stateless HTTP: every request is self-contained, so no server-side
    # MCP session can be lost (a restart, deploy or dropped session made
    # clients holding an old Mcp-Session-Id get 404 "Session not found",
    # which the directory reports as McpSessionTerminated). Safe because no
    # tool uses session state (no Context, progress, sampling or
    # subscriptions).
    mcp.run(transport="http", host="0.0.0.0", port=port, stateless_http=True)


if __name__ == "__main__":
    main()
