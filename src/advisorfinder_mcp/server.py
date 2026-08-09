"""AdvisorFinder MCP server: 6 consumer-facing tools over a local scrubbed
SQLite export (mcp_public.db). Every response funnels back to
advisorfinder.com via format.envelope(); disclosure status is always one of
the four honest states (never phrased as "clean"/"safe"); designations are
always labeled self-reported.

Tool functions are plain module-level functions decorated with @mcp.tool.
On fastmcp 3.4.6, `@mcp.tool` returns the original function object unchanged
(verified: `mcp.tool(fn) is fn`) — the FunctionTool wrapper is registered
internally and reachable via `await mcp.get_tool(name)`, but the name in this
module's namespace still refers to the plain function. Tests therefore import
and call these functions directly (e.g. `server.search_advisors(...)`) with
no `.fn` indirection needed.
"""
import json
import os

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import bootstrap, db, format, resources

mcp = FastMCP("advisorfinder")
resources.register(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _full_name(first, middle, last) -> str:
    return format.title_case_name(" ".join(p for p in (first, middle, last) if p))


def _individual_verify_links(crd: str) -> dict:
    return {
        "iapd": format.iapd_individual_url(crd),
        "brokercheck": format.brokercheck_individual_url(crd),
    }


def _parse_exam_json(raw) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _split_states(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


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


_SERVES_LABELS = {
    "serves_individuals": "individuals",
    "serves_hnw": "high-net-worth individuals",
    "serves_investment_companies": "investment companies",
    "serves_pooled_vehicles": "pooled investment vehicles",
    "serves_pension": "pension/profit-sharing plans",
    "serves_charitable": "charitable organizations",
    "serves_state_municipal": "state/municipal government entities",
    "serves_insurance": "insurance companies",
    "serves_sovereign": "sovereign wealth funds/foreign official institutions",
    "serves_corporations": "corporations/businesses",
    "serves_other": "other clients",
}

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


@mcp.tool
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
    IAPD / FINRA BrokerCheck registration feeds, not real-time.
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
    rows = db.search_advisors(name=name, firm=firm, city=city, state=state, limit=clamped_limit)

    results = []
    for r in rows:
        full_name = " ".join(
            p for p in (r["first_name"], r["middle_name"], r["last_name"]) if p
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

    payload = {"result_count": len(results), "results": results}
    if not results:
        payload["not_found_guidance"] = (
            "No advisors matched your search. Try a different spelling, just "
            "a last name, or search the official sources directly."
        )

    # Generic name-search fallback links always populate `verify`, whether or
    # not this specific call found anything — each result row also carries
    # its own iapd_link, but envelope()'s `verify` is meant to always be
    # populated with something actionable, not left as {} by default.
    return format.envelope(payload, verify=format.name_search_urls(name or firm or ""))


@mcp.tool
def get_advisor(crd: str) -> dict:
    """Get the full profile for an investment adviser representative (IAR) by
    CRD number — no need to search first if you already have it. Returns
    employment history (current firm first, then previous), exams passed,
    registered states, professional designations (always labeled
    self-reported — none of this data is independently verified), years in
    the industry, and four-state disclosure status (never phrased as
    "clean"/"safe": none_reported / disclosed_no_detail / disclosed_with_detail
    / unknown). Data is derived from SEC IAPD / FINRA BrokerCheck.
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

    employment = [
        {
            "firm_name": format.title_case_firm_name(e["firm_name"]),
            "is_current": bool(e["is_current"]),
            "start_date": e["start_date"],
            "end_date": e["end_date"],
            "branch_city": format.title_case_name(e["branch_city"]) if e["branch_city"] else None,
            "branch_state": e["branch_state"],
        }
        for e in bundle["employments"]
    ]

    exams = {
        "state": _parse_exam_json(iar["state_exams"]) if iar else [],
        "product": _parse_exam_json(iar["product_exams"]) if iar else [],
        "principal": _parse_exam_json(iar["principal_exams"]) if iar else [],
    }
    registered_states = _split_states(iar["registered_states"]) if iar else []

    designations = [
        {"code": d["code"], "name": d["name"], "issuing_body": d["issuing_body"], "status": "self-reported"}
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

    industry_start_date = iar["industry_start_date"] if iar else None
    years_in_industry = format.years_since(industry_start_date)

    disclosure = format.disclosure_status(rep["has_disclosure"], iar)

    payload = {
        "found": True,
        "crd": crd,
        "name": _full_name(rep["first_name"], rep["middle_name"], rep["last_name"]),
        "title": title,
        "bio": bio,
        "employment": employment,
        "exams": exams,
        "registered_states": registered_states,
        "designations": designations,
        "industry_start_date": industry_start_date,
        "years_in_industry": years_in_industry,
        "disclosure": disclosure,
    }

    # Marketplace enrichment (Task 4): a labeled listing block, present only
    # for advisors who are also AdvisorFinder marketplace members. The
    # envelope's own advisorfinder.link deep-links to that member's profile
    # instead of the generic homepage.
    marketplace_row, listing = _marketplace_listing_for(crd)
    if listing is not None:
        payload["advisorfinder_listing"] = listing

    return format.envelope(
        payload,
        caveats=caveats,
        verify=_individual_verify_links(crd),
        marketplace_row=marketplace_row,
    )


def _check_verdict(crd: str, bundle: dict) -> dict:
    rep = bundle["rep"]
    iar = bundle["iar"]
    registered_states = _split_states(iar["registered_states"]) if iar else []
    industry_start_date = iar["industry_start_date"] if iar else None
    disclosure = format.disclosure_status(rep["has_disclosure"], iar)
    payload = {
        "found": True,
        "crd": crd,
        "name": _full_name(rep["first_name"], rep["middle_name"], rep["last_name"]),
        "registration": {
            "active": rep["ia_scope"] == "Active",
            "registered_states": registered_states,
        },
        "experience": {
            "industry_start_date": industry_start_date,
            "years_in_industry": format.years_since(industry_start_date),
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


@mcp.tool
def check_advisor(name_or_crd: str, firm: str | None = None, state: str | None = None) -> dict:
    """Quick yes/no-style verification of an advisor: is their registration
    active, what states are they registered in, how long have they been in
    the industry, and their four-state disclosure status. Accepts EITHER a
    CRD number (all digits) OR a name — optionally narrowed by firm/state to
    disambiguate common names. If a name matches more than one advisor,
    returns candidates instead of guessing; re-run with the CRD from the list.
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
                "name": _full_name(r["first_name"], r["middle_name"], r["last_name"]),
                "firm": format.title_case_firm_name(firm_name) if firm_name else None,
            })
        return format.envelope(
            {
                "ambiguous": True,
                "candidates": candidates,
                "hint": "Multiple advisors matched — re-run check_advisor with the CRD from one of these candidates.",
            },
            verify=format.name_search_urls(name_or_crd),
        )

    crd = rows[0]["ind_source_id"]
    bundle = db.get_advisor(crd)
    return _check_verdict(crd, bundle)


def _regulatory_join(crd: str) -> tuple[dict, dict]:
    """(registration, disclosure) for a marketplace member's CRD, using the
    exact same db/format helpers check_advisor's _check_verdict does —
    registration.active + four-state disclosure.

    ADJUDICATED 2026-08-09 (Gate A2): the None branch below is a REAL,
    reachable path, not defensive dead code — 132/294 real marketplace members
    are state-registered/BD-side advisors legitimately absent from the SEC
    roster (ia_reps). sanitize_marketplace.py ships their row anyway
    (crd_mismatches is reported but no longer build-fatal), so db.get_advisor(crd)
    genuinely returns None for them. Returns a labeled fallback disclosure
    block (format.marketplace_unmatched_crd_disclosure) instead of crashing or
    silently reporting the generic 'unknown' four-state status."""
    bundle = db.get_advisor(crd)
    if bundle is None:
        return (
            {"active": None, "registered_states": []},
            format.marketplace_unmatched_crd_disclosure(crd),
        )
    rep = bundle["rep"]
    iar = bundle["iar"]
    registration = {
        "active": rep["ia_scope"] == "Active",
        "registered_states": _split_states(iar["registered_states"]) if iar else [],
    }
    disclosure = format.disclosure_status(rep["has_disclosure"], iar)
    return registration, disclosure


@mcp.tool
def find_bookable_advisors(
    specialty: str | None = None,
    city: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> dict:
    """Search advisors listed on AdvisorFinder's marketplace — professionals
    with public profiles you can view and contact directly. This searches
    only AdvisorFinder members (a few hundred advisors), not the full
    SEC roster; use search_advisors for the full roster.

    All filters are optional — browsing with none of them is fine, since this
    tool's scope is already narrow. `specialty` matches against the advisor's
    bio, client description, quick facts, and credentials (case-insensitive
    substring). Returns, per member: their self-provided profile info (bio,
    credentials, pricing, minimum account size, education, and more), a link
    to their full AdvisorFinder profile, AND the same regulatory facts
    check_advisor reports (registration status, four-state disclosure) —
    shown identically here as for any other advisor, never softened. AUM and
    client-count figures are self-reported by the advisor, not regulatory
    data, and are always labeled as such. Being listed here is a business
    relationship with AdvisorFinder, not an endorsement, and never affects
    how any advisor ranks in search_advisors/check_advisor.
    """
    if db.marketplace_stats() is None:
        return format.envelope({
            "available": False,
            "message": "Marketplace data not available in this deployment.",
            "result_count": 0,
            "results": [],
        })

    clamped_limit = max(1, min(limit, 50))
    rows = db.search_marketplace(specialty=specialty, city=city, state=state, limit=clamped_limit)

    results = []
    for row in rows:
        crd = row["crd"]
        # Regulatory join happens AFTER db.search_marketplace()'s own
        # ranking/limit — one db.get_advisor() per already-selected row,
        # never folded into that query, same discipline as the marketplace
        # enrichment in search_advisors/get_advisor/check_advisor above.
        registration, disclosure = _regulatory_join(crd)
        entry = {
            "crd": crd,
            "name": row["displayName"],
            "company": row["companyName"],
            "city": row["city"],
            "state": row["state"],
            "bio": row["bio"],
            "credentials": row["credentials"],
            "client_description": row["clientDescription"],
            "quick_facts": row["quickFacts"],
            "min_account_size": row["minAccountSize"],
            "years_of_experience": row["yearsOfExperience"],
            "virtual_meetings_offered": row["virtualMeetingsOffered"],
            "allowed_states": _split_states(row["allowedStates"]),
            "member_since": row["memberSince"],
            "website": row["advisorWebsiteURL"],
            "linkedin": row["linkedInURL"],
            "twitter": row["twitterURL"],
            "bio_video": row["bioVideoLink"],
            "education": row["education"],
            "self_reported": {
                "aum": row["aum"],
                "client_number": row["clientNumber"],
                "label": format.SELF_REPORTED_LABEL,
            },
            "in_their_own_words": _json_list(row["in_their_own_words"]),
            "registration": registration,
            "disclosure": disclosure,
            **format.marketplace_block(row),  # profile_url, job_title, pricing, note
        }
        results.append(entry)

    payload = {"result_count": len(results), "results": results}
    if not results:
        payload["not_found_guidance"] = (
            "No AdvisorFinder members matched your search. Try a different "
            "specialty, city, or state — or use search_advisors for the "
            "full SEC roster (not marketplace-only)."
        )
    return format.envelope(payload)


@mcp.tool
def search_firms(name: str | None = None, state: str | None = None, limit: int = 20) -> dict:
    """Search SEC- and state-registered investment adviser FIRMS by name
    and/or state. Matches on the firm's primary name, legal name, or any
    "also known as" prior name on file. Returns, per match: CRD, name, city/
    state, AUM band, advisor headcount, and a link to the firm's SEC IAPD
    record. State-registered-only firms (not SEC-registered) are included but
    flagged — our advisor roster coverage for those is much thinner.
    """
    unsanitizable = _unsanitizable_supplied_filter(name=name)
    if unsanitizable:
        return _unsanitizable_filter_error(*unsanitizable)

    clamped_limit = max(1, min(limit, 50))
    rows = db.search_firms(name=name, state=state, limit=clamped_limit)

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
        if r["state_only"]:
            entry["caveat"] = (
                "State-registered adviser — our advisor roster does not cover "
                "most state-registered firms."
            )
        results.append(entry)

    payload = {"result_count": len(results), "results": results}
    if not results:
        payload["not_found_guidance"] = "No firms matched your search."

    # Same rationale as search_advisors: `verify` always carries generic
    # name-search fallback links, not just on a miss.
    verify = {
        "iapd": format.iapd_firm_search_url(name or ""),
        "brokercheck": format.brokercheck_firm_search_url(name or ""),
    }
    return format.envelope(payload, verify=verify)


@mcp.tool
def get_firm(crd: str) -> dict:
    """Get the full profile for an investment adviser FIRM by CRD number:
    identity, address, website, AUM band, headcount, who they serve and how
    they charge (rendered as lists), any disclosure flag, state registrations,
    office locations (private-residence addresses are withheld per SEC
    privacy rules), prior/other names, fee details from their ADV Part 2A
    brochure (marked as estimated when it is), and how many individual
    advisor records we have on file for them (an empty roster does not
    necessarily mean an empty firm — see the caveat when that happens).
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
            "State-registered adviser — our advisor roster does not cover "
            "most state-registered firms."
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
            "states_approved": _split_states(s["states_approved"]),
        }
        return format.envelope(payload, caveats=caveats, verify=_firm_verify_links(crd))

    f = bundle["firm"]
    serves = [label for flag, label in _SERVES_LABELS.items() if f[flag] == "Y"]
    fees = [label for flag, label in _FEE_LABELS.items() if f[flag] == "Y"]

    locations = []
    for loc in bundle["locations"]:
        if loc["private_res"] == "Y" and not loc["street1"]:
            locations.append({
                "address": "Address withheld — private residence (SEC privacy protection)",
                "city": loc["city"],
                "state": loc["state"],
            })
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

    if bundle["roster_count"] == 0 and (f["investment_adviser_reps"] or 0) > 0:
        caveats.append(
            "We don't have individual advisor records for this firm; the firm "
            f"reports {f['investment_adviser_reps']} investment adviser reps."
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
        "website": f["website_clean"],
        "aum_band": f["aum_band"],
        "total_employees": f["total_employees"],
        "investment_adviser_reps": f["investment_adviser_reps"],
        "serves": serves,
        "fee_arrangements": fees,
        "any_disciplinary": f["any_disciplinary"],
        "state_registrations": _split_states(f["state_registrations"]),
        "locations": locations,
        "other_names": other_names,
        "fees": format.fee_block(bundle["part2a"]),
        "content": content_out,
        "advisor_roster_count": bundle["roster_count"],
    }
    return format.envelope(payload, caveats=caveats, verify=_firm_verify_links(crd))


@mcp.tool
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
    marketplace_stats = db.marketplace_stats()
    marketplace = {
        "member_count": marketplace_stats["count"] if marketplace_stats else 0,
        "snapshot_date": marketplace_stats["snapshot_date"] if marketplace_stats else None,
    }

    payload = {
        "firms_count": int(meta.get("firms_count", 0)),
        "state_firms_count": state_firms_count,
        "advisors_count": advisors_count,
        "vintages": {
            "advisors_as_of": meta.get("ia_reps_as_of"),
            "firms_as_of": meta.get("firms_as_of"),
            "individuals_bulk_as_of": meta.get("individuals_as_of"),
        },
        "disclosure_tally": tally,
        "marketplace": marketplace,
        "coverage": {
            "state_firms_with_advisor_rosters": f"{state_firms_with_reps}/{state_firms_count}",
            "note": (
                "Advisor rosters cover SEC-registered firms; most state-registered "
                "firms are not covered — an empty roster does not mean nobody works there."
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
    bootstrap.ensure_db()
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))


if __name__ == "__main__":
    main()
