"""SQL access layer for advisorfinder_mcp.

Holds all query functions used by the server's tools, plus connection/schema
plumbing. server.py holds tool orchestration and consumer-facing wording;
this module holds SQL (plus the one Python-side filter in search_marketplace,
which matches against format.marketplace_search_text -- see its docstring).
"""
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import SCHEMA_VERSION
from .format import firm_rank_key, fts_query, marketplace_search_text, name_rank_tokens

# Set by bootstrap.ensure_db() (or directly by tests) before any query runs.
DB_PATH: Path | None = None

# get_meta() result, cached per DB_PATH. Cleared by set_db_path() so a
# re-pointed DB_PATH never hands back another database's stale meta.
_meta_cache: dict | None = None

# all_roster_counts() result: (DB_PATH it was computed for, {crd: count}).
# Keyed on the path as well as cleared by set_db_path(), so a DB_PATH that is
# re-pointed directly (tests monkeypatch it) is never served stale counts.
_roster_cache: tuple[Path, dict[str, int]] | None = None


def set_db_path(path) -> None:
    """Point the module at a DB file and invalidate the cached export_meta
    and roster counts."""
    global DB_PATH, _meta_cache, _roster_cache
    DB_PATH = Path(path)
    _meta_cache = None
    _roster_cache = None


# Case-insensitive (production stores ~70% of names mixed case, and BINARY
# collation sorts 'SMITH' before 'Smith'). Deterministic order inside a tier (and for the no-name path): a plain
# "John Smith" with no middle name sorts before "John Q Smith"; ind_source_id
# makes full ties stable.
_TIE_BREAK = (
    "lower(last_name), lower(first_name), "
    "(middle_name IS NOT NULL AND middle_name <> ''), lower(middle_name), ind_source_id"
)


@contextmanager
def get_conn():
    """Read-only, immutable connection to DB_PATH. Raises RuntimeError if no
    path has been set yet (bootstrap.ensure_db() hasn't run)."""
    if DB_PATH is None:
        raise RuntimeError("advisorfinder_mcp.db: no DB path set — call bootstrap.ensure_db() first")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size=-32000")
    conn.execute("PRAGMA mmap_size=268435456")
    try:
        yield conn
    finally:
        conn.close()


def get_meta() -> dict:
    """export_meta as a plain dict, cached for the lifetime of the current
    DB_PATH (see set_db_path)."""
    global _meta_cache
    if _meta_cache is None:
        with get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM export_meta").fetchall()
        _meta_cache = {r["key"]: r["value"] for r in rows}
    return _meta_cache


def assert_schema_version() -> None:
    """Raises RuntimeError if the DB's export_meta.schema_version (a string)
    doesn't match this package's SCHEMA_VERSION (an int)."""
    meta = get_meta()
    found = int(meta["schema_version"])
    if found != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema version mismatch: mcp_public.db has schema_version={found}, "
            f"this server expects {SCHEMA_VERSION} — redeploy a matching export"
        )


def disclosure_tally() -> dict:
    """Four-state disclosure tally over ALL ia_reps, computed directly here —
    deliberately NOT read from export_meta's disclosure_tally_* fields, even
    though the export script (firm-intelligence repo, build_mcp_public_db.py)
    now implements the exact same four-state contract as
    format.disclosure_status() and its precomputed tally matches this
    recompute value-for-value: has_disclosure='Y' with no iar_details row (or
    a row with count 0/NULL) is disclosed_no_detail, never softened to
    "unknown" for lack of detail.

    Recomputing here anyway is deliberate DECOUPLING / defense-in-depth, not
    a correction of a wrong export: this server owns format.disclosure_status()
    (the per-advisor bucketing) and shouldn't have to trust that a future
    change to the separately-owned export script keeps matching it exactly.
    The CASE expression below mirrors format.disclosure_status()'s bucketing
    over the same ia_reps/iar_details rows the per-advisor view reads, so the
    aggregate here and the per-advisor view can never silently disagree,
    regardless of what export_meta's precomputed fields say.
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN r.has_disclosure = 'N' THEN 1 ELSE 0 END)
                    AS none_reported,
                SUM(CASE WHEN r.has_disclosure = 'Y'
                              AND COALESCE(d.disclosure_count, 0) > 0
                         THEN 1 ELSE 0 END)
                    AS disclosed_with_detail,
                SUM(CASE WHEN r.has_disclosure = 'Y'
                              AND COALESCE(d.disclosure_count, 0) <= 0
                         THEN 1 ELSE 0 END)
                    AS disclosed_no_detail,
                SUM(CASE WHEN r.has_disclosure IS NULL
                              OR r.has_disclosure NOT IN ('Y', 'N')
                         THEN 1 ELSE 0 END)
                    AS unknown
            FROM ia_reps r
            LEFT JOIN iar_details d ON d.ind_source_id = r.ind_source_id
            """
        ).fetchone()
        return {
            "none_reported": row["none_reported"] or 0,
            "disclosed_no_detail": row["disclosed_no_detail"] or 0,
            "disclosed_with_detail": row["disclosed_with_detail"] or 0,
            "unknown": row["unknown"] or 0,
        }


# ── advisor search ────────────────────────────────────────────────────────────

def _advisor_filters(name=None, firm=None, city=None, state=None) -> tuple[list[str], list, str | None]:
    """(conditions over ia_rep_firms columns, their bound params, the name
    FTS query or None) for the name/firm/city/state filters. A filter that
    sanitizes to nothing adds no condition. Shared by _advisor_where and
    count_advisors, so the total and the returned page always use the same
    filters. See search_advisors for the filter semantics."""
    firm_city_state_conditions = []
    firm_city_state_params: list = []
    if firm:
        q = fts_query(firm)
        if q:
            firm_city_state_conditions.append(
                "crd_number IN (SELECT DISTINCT crd_number FROM firm_fts WHERE firm_fts MATCH ?)"
            )
            firm_city_state_params.append(q)
    if city:
        firm_city_state_conditions.append("branch_city = ? COLLATE NOCASE")
        firm_city_state_params.append(city)
    if state:
        firm_city_state_conditions.append("branch_state = ? COLLATE NOCASE")
        firm_city_state_params.append(state)
    name_q = fts_query(name) if name else None  # empty-after-sanitize -> absent
    return firm_city_state_conditions, firm_city_state_params, name_q or None


_NAME_MATCH = "IN (SELECT ind_source_id FROM advisor_fts WHERE advisor_fts MATCH ?)"


def _advisor_where(name=None, firm=None, city=None, state=None) -> tuple[list[str], list]:
    """(WHERE clauses over ia_reps, bound params) for the name/firm/city/state
    filters (_advisor_filters)."""
    where = ["1=1"]
    params: list = []
    conditions, condition_params, name_q = _advisor_filters(
        name=name, firm=firm, city=city, state=state
    )
    if conditions:
        sub_where = " AND ".join(conditions)
        where.append(f"ind_source_id IN (SELECT ind_source_id FROM ia_rep_firms WHERE {sub_where})")
        params.extend(condition_params)
    if name_q:
        where.append("ind_source_id " + _NAME_MATCH)
        params.append(name_q)
    return where, params


# Match counts (search_advisors' total_matches, search_firms' total) stop
# at this number. A broad search ("llc": 268,283 advisors) does not need an
# exact total. Read at call time, so tests can lower it.
COUNT_CAP = 10_000


def count_advisors(name=None, firm=None, city=None, state=None) -> int:
    """Number of advisors search_advisors() would match with no limit, but
    at most COUNT_CAP + 1 (a result above COUNT_CAP means "more than
    COUNT_CAP matched"). Same filters as the search (_advisor_filters);
    never a fetch of every row.

    With a firm/city/state filter, the count walks the matching
    ia_rep_firms rows (DISTINCT advisors that are in ia_reps) and stops at
    COUNT_CAP + 1. The ia_reps-side form (_advisor_where) first builds the
    full list of matching advisor ids, so a LIMIT on it saved little.
    Production (2026-08-28 export, warm): firm="llc" (268,283 matches)
    ~0.31 s uncapped, ~0.26 s capped with the ia_reps-side form, ~35 ms
    with this one; state="CA" 45 ms -> 9 ms. A name-only count keeps the
    ia_reps-side form (name="smith": ~5 ms)."""
    conditions, condition_params, name_q = _advisor_filters(
        name=name, firm=firm, city=city, state=state
    )
    if conditions:
        conditions = list(conditions)
        params = list(condition_params)
        if name_q:
            conditions.append("f.ind_source_id " + _NAME_MATCH)
            params.append(name_q)
        sql = (
            "SELECT COUNT(*) FROM (SELECT DISTINCT f.ind_source_id FROM ia_rep_firms f "
            "JOIN ia_reps r ON r.ind_source_id = f.ind_source_id WHERE "
            + " AND ".join(conditions) + " LIMIT ?)"
        )
    else:
        where, params = _advisor_where(name=name, firm=firm, city=city, state=state)
        sql = "SELECT COUNT(*) FROM (SELECT 1 FROM ia_reps WHERE " + " AND ".join(where) + " LIMIT ?)"
    with get_conn() as conn:
        return conn.execute(sql, [*params, COUNT_CAP + 1]).fetchone()[0]


def search_advisors(name=None, firm=None, city=None, state=None, limit=20) -> list[dict]:
    """Returns up to `limit` advisor bundles matching all supplied filters
    (AND semantics across name/firm/city/state). Each bundle: ind_source_id,
    first/middle/last name, name_suffix (e.g. 'JR.'/'III', per the post-sweep
    resume-round schema addition), has_disclosure, `firms` (every ia_rep_firms row
    for that advisor — not just the ones that matched a firm/city/state
    filter), and `iar_row` (the iar_details row, or None -> 'unknown' four-
    state disclosure).

    All filtering (name/firm/city/state) AND the LIMIT are pushed into a
    single SQL statement via correlated subqueries against ia_reps — this is
    deliberate, not just tidiness: an earlier version built Python-side ID
    sets and passed them back into SQL as `IN (?, ?, ..., ?)` with one
    placeholder per candidate. On the real export (ia_rep_firms ~= 311k rows),
    a plain state/city browse can match tens of thousands of ids, well past
    SQLite's ~32766-variable ceiling (`sqlite3.OperationalError: too many SQL
    variables`) — invisible in this fixture's 6-advisor scale, but a
    guaranteed crash against production data. LIMIT now runs before the
    per-advisor firm/iar_details fetches below, so those touch at most
    `limit` rows regardless of how large the underlying match set is.

    Ordering: alphabetical (last_name, first_name) unless a `name` filter is
    given, in which case exact-name matches rank first (see the tier comment
    in the body) so a common name's real matches are not cut off by LIMIT.

    "Empty after sanitize = treat as absent" (per task-2-brief.md, stated for
    `name`) is applied uniformly to `firm` too, since both go through the same
    fts_query() sanitizer: a filter that sanitizes to nothing just drops that
    constraint rather than forcing zero results. This is the right behavior
    for a filter that was never supplied (None) in the first place.
    server.py's `search_advisors`/`check_advisor` tools now additionally
    reject a caller-SUPPLIED name/firm that sanitizes to empty before ever
    calling down here, rather than let it silently degrade into an
    unfiltered browse — but that guard lives at the tool layer, not
    universally: `search_firms` (below) has no such guard yet and still
    relies on this exact treat-as-absent path for a supplied-but-unsanitizable
    `name`. This function's "absent filter" contract is deliberately kept
    simple/reusable here; callers that need to distinguish "no filter" from
    "unsanitizable filter" must do so themselves, as search_advisors/
    check_advisor do.
    """
    with get_conn() as conn:
        where, params = _advisor_where(name=name, firm=firm, city=city, state=state)

        # Name ranking applies only when a usable name filter was given. Tiers
        # compare lower(first/last_name) to the RAW rank tokens (bound
        # parameters only): with >=2 tokens, 0 = exact first+last, 1 = exact
        # last, 2 = rest; with 1 token, 1 = exact last, 2 = rest. Without a
        # name (or with only suffix tokens) the order stays alphabetical.
        order_by = _TIE_BREAK
        order_params: list = []
        rank_tokens = name_rank_tokens(name)
        if len(rank_tokens) >= 2:
            order_by = (
                "CASE WHEN lower(first_name) = ? AND lower(last_name) = ? THEN 0 "
                "WHEN lower(last_name) = ? THEN 1 ELSE 2 END, " + _TIE_BREAK
            )
            order_params = [rank_tokens[0], rank_tokens[-1], rank_tokens[-1]]
        elif len(rank_tokens) == 1:
            order_by = (
                "CASE WHEN lower(last_name) = ? THEN 1 ELSE 2 END, " + _TIE_BREAK
            )
            order_params = [rank_tokens[0]]

        sql = (
            "SELECT ind_source_id, first_name, middle_name, last_name, name_suffix, "
            "has_disclosure FROM ia_reps WHERE " + " AND ".join(where) + " "
            "ORDER BY " + order_by + " LIMIT ?"
        )
        params.extend(order_params)
        params.append(limit)

        advisor_rows = conn.execute(sql, params).fetchall()

        results = []
        for r in advisor_rows:
            ind = r["ind_source_id"]
            firm_rows = conn.execute(
                "SELECT crd_number, firm_name, branch_city, branch_state "
                "FROM ia_rep_firms WHERE ind_source_id = ?",
                (ind,),
            ).fetchall()
            iar_row = conn.execute(
                "SELECT * FROM iar_details WHERE ind_source_id = ?", (ind,)
            ).fetchone()
            results.append({
                "ind_source_id": ind,
                "first_name": r["first_name"],
                "middle_name": r["middle_name"],
                "last_name": r["last_name"],
                "name_suffix": r["name_suffix"],
                "has_disclosure": r["has_disclosure"],
                "firms": [dict(fr) for fr in firm_rows],
                "iar_row": dict(iar_row) if iar_row else None,
            })
        return results


def get_advisor(crd: str) -> dict | None:
    """Full advisor bundle for get_advisor/check_advisor. None if `crd` isn't
    in ia_reps (i.e. not an exported, Active, roster-linked advisor)."""
    with get_conn() as conn:
        rep = conn.execute("SELECT * FROM ia_reps WHERE ind_source_id = ?", (crd,)).fetchone()
        if rep is None:
            return None
        iar = conn.execute("SELECT * FROM iar_details WHERE ind_source_id = ?", (crd,)).fetchone()
        employments = conn.execute(
            "SELECT * FROM advisor_employments WHERE ind_source_id = ? "
            "ORDER BY is_current DESC, start_date DESC",
            (crd,),
        ).fetchall()
        content = conn.execute(
            "SELECT * FROM advisor_content WHERE ind_source_id = ?", (crd,)
        ).fetchone()
        designations = conn.execute(
            "SELECT * FROM advisor_designations WHERE ind_source_id = ?", (crd,)
        ).fetchall()
        firms = conn.execute(
            "SELECT * FROM ia_rep_firms WHERE ind_source_id = ?", (crd,)
        ).fetchall()
        return {
            "rep": dict(rep),
            "iar": dict(iar) if iar else None,
            "employments": [dict(e) for e in employments],
            "content": dict(content) if content else None,
            "designations": [dict(d) for d in designations],
            "firms": [dict(f) for f in firms],
        }


# ── firm search ───────────────────────────────────────────────────────────────

def search_firms(name=None, state=None, limit=20) -> list[dict]:
    """Up to `limit` firm bundles: search_firms_with_total() without the
    total. See that function for the matching and ordering rules."""
    return search_firms_with_total(name=name, state=state, limit=limit)[0]


def search_firms_with_total(name=None, state=None, limit=20) -> tuple[list[dict], int]:
    """(page, total): up to `limit` firm bundles, and the number of firms
    that matched before the limit (after the firms/firms_state dedupe).

    firm_fts kinds: primary/legal/other (from `firms`) and state (from firms_state). The two tables are NOT
    disjoint (139 CRDs are in both in production): a firms_state row whose CRD
    is already returned from `firms` is skipped, so each CRD appears once and
    the full `firms` row (with its roster count) wins. `kinds` on each bundle is the set of kinds that matched (or,
    with no name filter, {'primary'}/{'state'} for a plain browse); the caller
    decides matched_as / state caveat from that.

    The state filter is pushed into SQL (COLLATE NOCASE equality — a single
    bound parameter, not a per-row Python-side scan) for both the `firms` and
    `firms_state` fetches, whether or not a name was given. When a name IS
    given, the crd_number IN (...) list is bounded by firm_fts match count
    (typically small — actual search hits), not by table size, so it doesn't
    carry the same "too many SQL variables" risk as search_advisors' id sets
    did before that was fixed.

    Ordering (applied to the FULL match set, before the limit): with a name,
    each CRD takes the best tier over all of its matched names (see
    _firm_name_tier): exact own (primary/legal/state) name, exact other
    ("also known as") name, own name that starts with the search text, other
    name that starts with it, any other own-name match, other-name-only
    match. Inside a tier the firm with the larger SEC AUM band comes first
    (_AUM_BAND_RANK: "$25B+" highest; "Not disclosed", a missing or unknown
    band, and every state-only firm lowest), then the larger adviser roster
    (all_roster_counts(): public SEC roster data only; state-only firms count
    0), then the name (case-insensitive), then crd_number. Marketplace
    membership is never a factor. Browse mode (no name) is ordered by
    name, then crd_number. `other_name` is the matched other name with the
    best tier, so matched_as shows the name that matched best.
    """
    with get_conn() as conn:
        hits: dict[str, dict] | None = None

        if name:
            q = fts_query(name)
            if not q:  # empty-after-sanitize -> treat as absent (browse mode)
                name = None
            else:
                hits = {}
                query_key = firm_rank_key(name)
                # Plain tuples (no Row factory): this loop sees every match of
                # a broad search ("llc" -> ~41k rows).
                cur = conn.execute(
                    "SELECT crd_number, kind, name FROM firm_fts WHERE firm_fts MATCH ?", (q,)
                )
                cur.row_factory = None
                key_cache: dict[str, str] = {}
                for crd, kind, hit_name in cur.fetchall():
                    entry = hits.get(crd)
                    if entry is None:
                        entry = hits[crd] = {
                            "kinds": set(), "other_name": None, "other_rank": None,
                            "tier": _TIER_REST_OTHER,
                        }
                    entry["kinds"].add(kind)
                    hit_name = hit_name or ""
                    is_other = kind == "other"
                    if _could_start_with(hit_name, query_key):
                        name_key = key_cache.get(hit_name)
                        if name_key is None:
                            name_key = key_cache[hit_name] = firm_rank_key(hit_name)
                        tier = _firm_name_tier(name_key, query_key, is_other)
                    else:
                        tier = _TIER_REST_OTHER if is_other else _TIER_REST_OWN
                    if tier < entry["tier"]:
                        entry["tier"] = tier
                    if is_other:
                        rank = (tier, hit_name.casefold(), hit_name)
                        if entry["other_rank"] is None or rank < entry["other_rank"]:
                            entry["other_rank"] = rank
                            entry["other_name"] = hit_name
                if not hits:
                    return [], 0

        # Only the columns used below: a broad name matches ~20k firms.
        cols = "crd_number, primary_name, address_city, address_state, aum_band"
        fsql = f"SELECT {cols} FROM firms WHERE 1=1"
        fparams: list = []
        ssql = f"SELECT {cols} FROM firms_state WHERE 1=1"
        sparams: list = []
        if hits is not None:
            crds = list(hits)
            placeholders = ",".join("?" * len(crds))
            fsql += f" AND crd_number IN ({placeholders})"
            fparams.extend(crds)
            ssql += f" AND crd_number IN ({placeholders})"
            sparams.extend(crds)
        if state:
            fsql += " AND address_state = ? COLLATE NOCASE"
            fparams.append(state)
            ssql += " AND address_state = ? COLLATE NOCASE"
            sparams.append(state)

        results = []
        for frow in conn.execute(fsql, fparams).fetchall():
            crd = frow["crd_number"]
            info = hits[crd] if hits is not None else _BROWSE_PRIMARY
            results.append({
                "crd_number": crd,
                "name": frow["primary_name"],
                "city": frow["address_city"],
                "state": frow["address_state"],
                "aum_band": frow["aum_band"],
                # Filled in from the roster below, for the returned page only.
                # firms.investment_adviser_reps (the Form ADV figure) is NOT
                # used: it is unreliable upstream (0 for Fisher Investments,
                # which has 2,861 roster advisors) and is dropped in schema 5.
                "advisor_count": None,
                "kinds": info["kinds"],
                "other_name": info["other_name"],
                "state_only": False,
                "tier": info["tier"],
            })
        sec_returned = {r["crd_number"] for r in results}
        for srow in conn.execute(ssql, sparams).fetchall():
            crd = srow["crd_number"]
            if crd in sec_returned:
                continue  # dual-listed: the `firms` row above already covers it
            info = hits[crd] if hits is not None else _BROWSE_STATE
            results.append({
                "crd_number": crd,
                "name": srow["primary_name"],
                "city": srow["address_city"],
                "state": srow["address_state"],
                "aum_band": srow["aum_band"],
                "advisor_count": None,
                "kinds": info["kinds"],
                "other_name": info["other_name"],
                "state_only": True,
                "tier": info["tier"],
            })

        # Roster-based advisor counts (see roster_counts), computed once per
        # process for every firm. State-only firms have no roster: they keep
        # advisor_count None and rank as 0.
        counts = all_roster_counts(conn)
        if hits is not None:
            # Tier first (exact names before the rest -- see the docstring),
            # so a broad name's exact match is never cut off by the limit;
            # then the larger SEC AUM band; then the larger roster; then a
            # case-insensitive, deterministic order. State-only firms rank
            # with the lowest band and a roster of 0.
            def sort_key(r):
                if r["state_only"]:
                    band = roster = 0
                else:
                    band = _AUM_BAND_RANK.get(r["aum_band"], 0)
                    roster = counts.get(r["crd_number"], 0)
                name_ = r["name"] or ""
                return (r["tier"], -band, -roster, name_.casefold(), name_, r["crd_number"])
        else:
            def sort_key(r):
                name_ = r["name"] or ""
                return (name_.casefold(), name_, r["crd_number"])
        results.sort(key=sort_key)
        page = results[:limit]
        for r in page:
            if not r["state_only"]:
                r["advisor_count"] = counts.get(r["crd_number"], 0)
        return page, len(results)


# search_firms name tiers (lower ranks first). "Own" names are the firm's
# primary, legal and state-registration names (firm_fts kinds primary/legal/
# state); "other" names are prior or "also known as" names.
_TIER_EXACT_OWN = 0     # an own name equals the search text
_TIER_EXACT_OTHER = 1   # an other name equals it
_TIER_PREFIX_OWN = 2    # an own name starts with it
_TIER_PREFIX_OTHER = 3  # an other name starts with it
_TIER_REST_OWN = 4      # an own name matched (full-text) some other way
_TIER_REST_OTHER = 5    # only other names matched

# search_firms tie-break inside a tier: the firm's SEC AUM band (public Form
# ADV data), larger first. The keys are the exact export strings (en dash).
# "Not disclosed", NULL, any unknown string, and state-only firms rank 0.
_AUM_BAND_RANK = {
    "$25B+": 6,
    "$5B\u2013$25B": 5,
    "$1B\u2013$5B": 4,
    "$500M\u2013$1B": 3,
    "$100M\u2013$500M": 2,
    "Under $100M": 1,
    "Not disclosed": 0,
}

# Browse mode (no name filter): no tier; the order is by name.
_BROWSE_PRIMARY = {"kinds": frozenset({"primary"}), "other_name": None, "tier": 0}
_BROWSE_STATE = {"kinds": frozenset({"state"}), "other_name": None, "tier": 0}

# First letter or digit of a name.
_FIRST_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


def _could_start_with(hit_name: str, query_key: str) -> bool:
    """False only when firm_rank_key(hit_name) certainly does not start with
    query_key, so _firm_name_tier() cannot return an exact or prefix tier.
    A cheap pre-check that skips most normalizations on broad searches. For
    an ASCII name, the first character of its rank key is its first letter
    or digit, lowercased (firm_rank_key drops punctuation and keeps the first
    token). Non-ASCII names always take the full check."""
    if not query_key:
        return False
    if not hit_name.isascii():
        return True
    m = _FIRST_ALNUM_RE.search(hit_name)
    return m is not None and m.group().lower() == query_key[0]


def _firm_name_tier(name_key: str, query_key: str, is_other: bool) -> int:
    """Tier of one matched firm_fts name. Both keys are
    format.firm_rank_key() values (case, diacritics, punctuation and trailing
    legal suffixes already normalized away). `is_other` is True for an
    "other" (prior / also known as) name."""
    if query_key and name_key:
        if name_key == query_key:
            return _TIER_EXACT_OTHER if is_other else _TIER_EXACT_OWN
        if name_key.startswith(query_key):
            return _TIER_PREFIX_OTHER if is_other else _TIER_PREFIX_OWN
    return _TIER_REST_OTHER if is_other else _TIER_REST_OWN


def all_roster_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """{crd: roster count} for EVERY firm with at least one adviser (same
    definition as roster_counts: distinct ind_source_id, ia_only = 'Y'),
    from one grouped query, cached for the life of the process per DB_PATH.
    Production: ~10k entries, under 1 MB, ~0.4 s once. search_firms uses it
    to rank inside a tier and for the returned advisor_count."""
    global _roster_cache
    cached = _roster_cache
    if cached is not None and cached[0] == DB_PATH:
        return cached[1]
    cur = conn.execute(
        "SELECT crd_number, COUNT(DISTINCT ind_source_id) FROM ia_rep_firms "
        "WHERE ia_only = 'Y' GROUP BY crd_number"
    )
    cur.row_factory = None
    counts = dict(cur.fetchall())
    _roster_cache = (DB_PATH, counts)
    return counts


def roster_counts(conn: sqlite3.Connection, crds: list[str]) -> dict[str, int]:
    """{crd: number of DISTINCT investment adviser representatives
    (ind_source_id) on that firm's roster} for `crds`, in one grouped query
    (uses idx_iaf_crd). A crd with no adviser rows is absent from the dict;
    callers default it to 0. This is the only advisor count the server
    reports for a firm -- see get_firm.

    Only ia_only = 'Y' rows count. IAPD returns a dual-registered person's job
    twice (iaOnly 'Y' = the adviser association, 'N' = the broker-dealer
    association), and a broker-only person has only 'N' rows. Counting 'N'
    rows reported broker-dealer registrants as advisers (production: J.P.
    Morgan Securities 35,860 vs 13,866 advisers; Goldman Sachs & Co. 8,255 vs
    0). Production holds only 'Y'/'N'; a NULL or other flag is not counted."""
    if not crds:
        return {}
    placeholders = ",".join("?" * len(crds))
    rows = conn.execute(
        "SELECT crd_number, COUNT(DISTINCT ind_source_id) FROM ia_rep_firms "
        f"WHERE crd_number IN ({placeholders}) AND ia_only = 'Y' "
        "GROUP BY crd_number",
        list(crds),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── marketplace (optional; present only when the export was built with
#    --marketplace) ────────────────────────────────────────────────────────────

MARKETPLACE_ENABLED_ENV = "MCP_MARKETPLACE_ENABLED"


def marketplace_enabled() -> bool:
    """The marketplace pause switch -- the ONE place it is read. Marketplace
    data is served only when MCP_MARKETPLACE_ENABLED is exactly "true"
    (case-insensitive, whitespace-trimmed); absent or any other value means
    paused, so the pause is the default with no deploy configuration. Read on
    every call (never cached at import), so flipping the variable and
    restarting is the whole switch. When paused, the three public readers
    below return their "no marketplace table" values without opening the DB,
    so no marketplace row is ever read and every tool degrades exactly as on
    an export built without --marketplace."""
    return os.environ.get(MARKETPLACE_ENABLED_ENV, "").strip().lower() == "true"


def _marketplace_table_exists(conn: sqlite3.Connection) -> bool:
    """marketplace_advisors is OPTIONAL-at-runtime (build_mcp_public_db.py only
    creates it when invoked with --marketplace) — checked via sqlite_master
    rather than try/except around the query itself, so a genuine SQL bug in
    this module's own queries still surfaces as a real error instead of being
    silently swallowed as 'table absent'."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'marketplace_advisors'"
    ).fetchone() is not None


def get_marketplace_by_crd(crd: str) -> sqlite3.Row | None:
    """The marketplace_advisors row (self-reported AdvisorFinder profile data —
    aum/clientNumber etc. are as listed on the advisor's own profile, not
    regulatory data) for `crd`, or None if either the table is absent (no
    --marketplace at build time) or no marketplace member has this crd (never
    joined the marketplace, or filtered out by the sitemap-scoping gate in
    sanitize_marketplace.sanitize()). Also None when the marketplace is paused
    (marketplace_enabled())."""
    if not marketplace_enabled():
        return None
    with get_conn() as conn:
        if not _marketplace_table_exists(conn):
            return None
        return conn.execute(
            "SELECT * FROM marketplace_advisors WHERE crd = ?", (crd,)
        ).fetchone()


def search_marketplace(
    specialty: str | None = None,
    city: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Up to `limit` rows: search_marketplace_with_total() without the
    total. See that function for the matching and ordering rules."""
    return search_marketplace_with_total(specialty=specialty, city=city, state=state, limit=limit)[0]


def search_marketplace_with_total(
    specialty: str | None = None,
    city: str | None = None,
    state: str | None = None,
    limit: int = 20,
) -> tuple[list[sqlite3.Row], int]:
    """(page, total): up to `limit` marketplace_advisors rows matching all
    supplied filters (AND semantics), and the number that matched before the
    limit. Ordered alphabetically by displayName (case-insensitive), then
    crd -- nothing else; membership details never change the order. `city`/`state` are exact
    NOCASE equality in SQL. `specialty` is a case-insensitive substring match
    against format.marketplace_search_text(row): the CLEANED bio, client
    description, in-their-own-words answers, and credential acronyms/names.

    `specialty` is matched in Python, not SQL LIKE: the raw credentials column
    is DynamoDB JSON that carries generic credential marketing copy (every
    CFP entry says "fiduciary"), so a LIKE over it matched advisors on text
    that says nothing about them, and on JSON keys. 294 rows in production,
    so loading the city/state-filtered rows and filtering here is cheap. The
    limit is applied AFTER the specialty filter. ([], 0) (not an error)
    when the table is absent or the marketplace is paused
    (marketplace_enabled())."""
    if not marketplace_enabled():
        return [], 0
    with get_conn() as conn:
        if not _marketplace_table_exists(conn):
            return [], 0

        where = ["1=1"]
        params: list = []
        if city:
            where.append("city = ? COLLATE NOCASE")
            params.append(city)
        if state:
            where.append("state = ? COLLATE NOCASE")
            params.append(state)

        # ORDER BY before the limit, same discipline as search_advisors/
        # search_firms: without it, which rows a `limit`-truncated result
        # returns would be whatever order SQLite walks the table in --
        # unspecified, and free to reshuffle on any rebuild/VACUUM.
        sql = (
            "SELECT * FROM marketplace_advisors WHERE " + " AND ".join(where)
            + " ORDER BY displayName COLLATE NOCASE, crd"
        )
        rows = conn.execute(sql, params).fetchall()

    needle = specialty.strip().casefold() if specialty else ""
    if needle:
        rows = [r for r in rows if needle in marketplace_search_text(r)]
    return rows[:limit], len(rows)


def marketplace_stats() -> dict | None:
    """{"count": int, "snapshot_date": str} sourced from export_meta's
    marketplace_count / marketplace_snapshot_date keys (written on every v3
    build, unconditionally — "0"/None when --marketplace was omitted). Returns
    None when marketplace_advisors itself is absent (the table-presence check,
    not the count, is authoritative for 'was this a marketplace build at
    all' — a build run WITH --marketplace against zero sitemap-matched
    advisors would still have the table, just empty, and should report
    count=0 rather than None). Also None when the marketplace is paused
    (marketplace_enabled()) -- callers that must tell "paused" from "absent"
    check marketplace_enabled() themselves."""
    if not marketplace_enabled():
        return None
    with get_conn() as conn:
        exists = _marketplace_table_exists(conn)
    if not exists:
        return None
    meta = get_meta()
    return {
        "count": int(meta.get("marketplace_count") or 0),
        "snapshot_date": meta.get("marketplace_snapshot_date"),
    }


def get_firm(crd: str) -> dict | None:
    """Full firm bundle for get_firm. None if `crd` is in neither `firms` nor
    `firms_state`. `firm` is None (with `state_firm` populated) for a
    state-only reduced profile."""
    with get_conn() as conn:
        frow = conn.execute("SELECT * FROM firms WHERE crd_number = ?", (crd,)).fetchone()
        srow = conn.execute("SELECT * FROM firms_state WHERE crd_number = ?", (crd,)).fetchone()
        if frow is None and srow is None:
            return None

        locations = conn.execute(
            "SELECT * FROM firm_locations WHERE crd_number = ?", (crd,)
        ).fetchall()
        other_names = conn.execute(
            "SELECT * FROM firm_other_names WHERE crd_number = ?", (crd,)
        ).fetchall()
        part2a = conn.execute(
            "SELECT * FROM firm_part2a WHERE crd_number = ?", (crd,)
        ).fetchone()
        content = conn.execute(
            "SELECT * FROM firm_content WHERE crd_number = ?", (crd,)
        ).fetchone()
        roster_count = roster_counts(conn, [crd]).get(crd, 0)

        return {
            "firm": dict(frow) if frow else None,
            "state_firm": dict(srow) if srow else None,
            "locations": [dict(loc) for loc in locations],
            "other_names": [dict(o) for o in other_names],
            "part2a": dict(part2a) if part2a else None,
            "content": dict(content) if content else None,
            "roster_count": roster_count,
        }
