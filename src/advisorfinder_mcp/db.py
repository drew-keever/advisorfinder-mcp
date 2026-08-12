"""SQL access layer for advisorfinder_mcp.

Holds all query functions used by the server's tools, plus connection/schema
plumbing. server.py holds tool orchestration and consumer-facing wording;
this module holds SQL and nothing else.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import SCHEMA_VERSION
from .format import fts_query

# Set by bootstrap.ensure_db() (or directly by tests) before any query runs.
DB_PATH: Path | None = None

# get_meta() result, cached per DB_PATH. Cleared by set_db_path() so a
# re-pointed DB_PATH never hands back another database's stale meta.
_meta_cache: dict | None = None


def set_db_path(path) -> None:
    """Point the module at a DB file and invalidate the cached export_meta."""
    global DB_PATH, _meta_cache
    DB_PATH = Path(path)
    _meta_cache = None


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
        where = ["1=1"]
        params: list = []

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

        if firm_city_state_conditions:
            sub_where = " AND ".join(firm_city_state_conditions)
            where.append(f"ind_source_id IN (SELECT ind_source_id FROM ia_rep_firms WHERE {sub_where})")
            params.extend(firm_city_state_params)

        if name:
            q = fts_query(name)
            if q:  # empty-after-sanitize -> treat as absent: no clause added
                where.append(
                    "ind_source_id IN (SELECT ind_source_id FROM advisor_fts WHERE advisor_fts MATCH ?)"
                )
                params.append(q)

        sql = (
            "SELECT ind_source_id, first_name, middle_name, last_name, name_suffix, "
            "has_disclosure FROM ia_reps WHERE " + " AND ".join(where) + " "
            "ORDER BY last_name, first_name LIMIT ?"
        )
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
    """Returns up to `limit` firm bundles. firm_fts kinds: primary/legal/other
    (from `firms`) and state (from firms_state, disjoint from `firms` by
    construction). `kinds` on each bundle is the set of kinds that matched (or,
    with no name filter, {'primary'}/{'state'} for a plain browse); the caller
    decides matched_as / state caveat from that.

    The state filter is pushed into SQL (COLLATE NOCASE equality — a single
    bound parameter, not a per-row Python-side scan) for both the `firms` and
    `firms_state` fetches, whether or not a name was given. When a name IS
    given, the crd_number IN (...) list is bounded by firm_fts match count
    (typically small — actual search hits), not by table size, so it doesn't
    carry the same "too many SQL variables" risk as search_advisors' id sets
    did before that was fixed.
    """
    with get_conn() as conn:
        hits: dict[str, dict] | None = None

        if name:
            q = fts_query(name)
            if not q:  # empty-after-sanitize -> treat as absent (browse mode)
                name = None
            else:
                hits = {}
                rows = conn.execute(
                    "SELECT crd_number, kind, name FROM firm_fts WHERE firm_fts MATCH ?", (q,)
                ).fetchall()
                for r in rows:
                    entry = hits.setdefault(r["crd_number"], {"kinds": set(), "other_name": None})
                    entry["kinds"].add(r["kind"])
                    if r["kind"] == "other":
                        entry["other_name"] = r["name"]
                if not hits:
                    return []

        fsql = "SELECT * FROM firms WHERE 1=1"
        fparams: list = []
        ssql = "SELECT * FROM firms_state WHERE 1=1"
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
            info = hits[crd] if hits is not None else {"kinds": {"primary"}, "other_name": None}
            results.append({
                "crd_number": crd,
                "name": frow["primary_name"],
                "city": frow["address_city"],
                "state": frow["address_state"],
                "aum_band": frow["aum_band"],
                "advisor_count": frow["investment_adviser_reps"],
                "kinds": info["kinds"],
                "other_name": info["other_name"],
                "state_only": False,
            })
        for srow in conn.execute(ssql, sparams).fetchall():
            crd = srow["crd_number"]
            info = hits[crd] if hits is not None else {"kinds": {"state"}, "other_name": None}
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
            })

        results.sort(key=lambda r: r["name"] or "")
        return results[:limit]


# ── marketplace (optional; present only when the export was built with
#    --marketplace) ────────────────────────────────────────────────────────────

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
    sanitize_marketplace.sanitize())."""
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
    """Returns up to `limit` marketplace_advisors rows matching all supplied
    filters (AND semantics). `specialty` is a case-insensitive substring LIKE
    across bio/clientDescription/quickFacts/credentials (294 rows in
    production — a plain LIKE scan is fine at this scale, no FTS needed).
    `city`/`state` are exact NOCASE equality. Empty list (not an error) when
    the table is absent."""
    with get_conn() as conn:
        if not _marketplace_table_exists(conn):
            return []

        where = ["1=1"]
        params: list = []
        if specialty:
            like = f"%{specialty}%"
            where.append(
                "(bio LIKE ? OR clientDescription LIKE ? OR quickFacts LIKE ? OR credentials LIKE ?)"
            )
            params.extend([like, like, like, like])
        if city:
            where.append("city = ? COLLATE NOCASE")
            params.append(city)
        if state:
            where.append("state = ? COLLATE NOCASE")
            params.append(state)

        # ORDER BY before LIMIT, same discipline as search_advisors/search_firms:
        # without it, which rows a plain browse or a narrowly-matching filter
        # returns is whatever order SQLite happens to walk the table in —
        # unspecified, and free to reshuffle on any rebuild/VACUUM. At fixture
        # scale (2 rows) that's invisible; at production scale (294 rows) it
        # would make a `limit`-truncated result set silently non-deterministic.
        sql = (
            "SELECT * FROM marketplace_advisors WHERE " + " AND ".join(where)
            + " ORDER BY displayName LIMIT ?"
        )
        params.append(limit)
        return conn.execute(sql, params).fetchall()


def marketplace_stats() -> dict | None:
    """{"count": int, "snapshot_date": str} sourced from export_meta's
    marketplace_count / marketplace_snapshot_date keys (written on every v3
    build, unconditionally — "0"/None when --marketplace was omitted). Returns
    None when marketplace_advisors itself is absent (the table-presence check,
    not the count, is authoritative for 'was this a marketplace build at
    all' — a build run WITH --marketplace against zero sitemap-matched
    advisors would still have the table, just empty, and should report
    count=0 rather than None)."""
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
        roster_count = conn.execute(
            "SELECT COUNT(DISTINCT ind_source_id) FROM ia_rep_firms WHERE crd_number = ?", (crd,)
        ).fetchone()[0]

        return {
            "firm": dict(frow) if frow else None,
            "state_firm": dict(srow) if srow else None,
            "locations": [dict(loc) for loc in locations],
            "other_names": [dict(o) for o in other_names],
            "part2a": dict(part2a) if part2a else None,
            "content": dict(content) if content else None,
            "roster_count": roster_count,
        }
