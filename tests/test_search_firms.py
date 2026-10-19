from advisorfinder_mcp import format, server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_search_by_primary_name():
    result = server.search_firms(name="alpha wealth")
    _envelope_keys_present(result)
    crds = {r["crd"] for r in result["results"]}
    assert "100001" in crds


def test_other_name_only_match_carries_matched_as():
    result = server.search_firms(name="gateway")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "100002"
    assert "matched_as" in row
    assert "Gateway Capital Partners" in row["matched_as"]


def test_primary_match_has_no_matched_as():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "matched_as" not in row


def test_state_firm_carries_caveat():
    result = server.search_firms(name="delta state")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "500001"
    assert "caveat" in row
    assert "state-registered" in row["caveat"].lower()


def test_sec_firm_has_no_state_caveat():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "caveat" not in row


def test_state_filter_by_address_state():
    result = server.search_firms(state="MA")
    crds = {r["crd"] for r in result["results"]}
    assert "100002" in crds  # BETA is in MA
    assert "100001" not in crds  # ALPHA is in NY


def test_no_results_has_guidance():
    result = server.search_firms(name="zzznomatchzzz")
    assert result["result_count"] == 0
    assert "not_found_guidance" in result
    assert "iapd" in result["verify"] and "brokercheck" in result["verify"]


def test_verify_populated_even_with_results():
    result = server.search_firms(name="alpha wealth")
    assert result["result_count"] > 0
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]


def test_aum_band_field_present_on_every_result():
    result = server.search_firms(state="NY")
    for row in result["results"]:
        assert "aum_band" in row


def test_search_firms_unsanitizable_name_errors_instead_of_browse():
    result = server.search_firms(name="!!!")
    assert "error" in result
    assert not result.get("results")


def test_search_firms_renders_llc_uppercase():
    result = server.search_firms(name="alpha wealth")
    names = {r["name"] for r in result["results"]}
    assert "Alpha Wealth LLC" in names


# ── advisor_count comes from the roster, never the Form ADV column ──────────
# firms.investment_adviser_reps is unreliable upstream (production: Fisher
# Investments CRD 107342 reports 0 but has 2,861 roster advisors). The fixture
# disagrees the same way: 100001 reports 5 but has a larger roster, 100003
# reports 5 but has no roster at all. Expectations are computed from the
# fixture's own ia_rep_firms, never hard-coded.

def _fixture_counts():
    from advisorfinder_mcp import db
    with db.get_conn() as conn:
        roster = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT crd_number, COUNT(DISTINCT ind_source_id) FROM ia_rep_firms "
                "WHERE ia_only = 'Y' GROUP BY crd_number"
            )
        }
        adv = {
            r[0]: r[1]
            for r in conn.execute("SELECT crd_number, investment_adviser_reps FROM firms")
        }
    return roster, adv


def test_advisor_count_is_distinct_roster_count_not_adv_figure():
    roster, adv = _fixture_counts()
    # Guard: the fixture really does disagree, so this test proves something.
    assert roster.get("100001", 0) != adv["100001"]
    assert roster.get("100003", 0) != adv["100003"]

    result = server.search_firms(limit=50)
    by_crd = {r["crd"]: r for r in result["results"]}
    for crd in ("100001", "100002", "100003"):
        assert by_crd[crd]["advisor_count"] == roster.get(crd, 0), crd


def test_advisor_count_is_none_for_state_only_firm():
    result = server.search_firms(limit=50)
    state_only = [r for r in result["results"] if "caveat" in r]
    assert state_only
    assert all(r["advisor_count"] is None for r in state_only)


def test_roster_counts_counts_distinct_advisers_only():
    # One advisor linked to the same firm twice (two branches, or the adviser
    # row plus the broker row of a dual registrant) is one person. A person
    # with only ia_only = 'N' (broker-dealer) or a NULL flag is not counted.
    import sqlite3

    from advisorfinder_mcp import db
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ia_rep_firms (ind_source_id TEXT, crd_number TEXT, ia_only TEXT)")
    conn.executemany(
        "INSERT INTO ia_rep_firms VALUES (?, ?, ?)",
        [
            ("1", "A", "Y"), ("1", "A", "Y"), ("1", "A", "N"),  # dual: one person
            ("2", "A", "Y"),
            ("4", "A", "N"),  # broker-only: not counted
            ("5", "A", None),  # unknown flag: not counted
            ("3", "B", "Y"),
            ("6", "C", "N"),  # broker-only firm: absent (callers default to 0)
        ],
    )
    assert db.roster_counts(conn, ["A", "B", "C"]) == {"A": 2, "B": 1}
    assert db.roster_counts(conn, []) == {}


# ── a CRD in both `firms` and `firms_state` is returned once ────────────────
# Production has 139 such CRDs (e.g. 306770 came back twice, once with a
# count and once with null). The fixture has none, so this test builds a copy
# with one dual-listed firm.

def test_crd_in_both_firms_and_firms_state_is_returned_once(tmp_path, monkeypatch):
    import shutil
    import sqlite3

    from advisorfinder_mcp import db
    copy = tmp_path / "dup.db"
    shutil.copy(db.DB_PATH, copy)
    conn = sqlite3.connect(copy)
    conn.execute(
        "INSERT INTO firms_state (crd_number, primary_name, address_city, address_state, aum_band) "
        "SELECT crd_number, primary_name, address_city, address_state, aum_band "
        "FROM firms WHERE crd_number = '100001'"
    )
    conn.execute(
        "INSERT INTO firm_fts (name, crd_number, kind) "
        "SELECT primary_name, crd_number, 'state' FROM firms WHERE crd_number = '100001'"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", copy)

    for kwargs in ({"name": "alpha wealth"}, {"state": "NY", "limit": 50}):
        result = server.search_firms(**kwargs)
        rows = [r for r in result["results"] if r["crd"] == "100001"]
        assert len(rows) == 1, kwargs
        assert rows[0]["advisor_count"] is not None  # the `firms` row wins
        assert "caveat" not in rows[0]


# ── exact-name-first ranking ────────────────────────────────────────────────
# Production: "Wealth Enhancement Group" ranked CRD 133088 (other name "Total
# Wealth Enhancement Group LLC") above CRD 116407, whose other name is exactly
# "WEALTH ENHANCEMENT GROUP, LLC". These tests build a copy of the fixture with
# that shape (the committed .db files are never edited).

def _copy_with(
    tmp_path, monkeypatch, firms=(), state_firms=(), other_names=(), locations=(), rosters=(),
):
    import shutil
    import sqlite3

    from advisorfinder_mcp import db
    copy = tmp_path / "ranked.db"
    shutil.copy(db.DB_PATH, copy)
    conn = sqlite3.connect(copy)
    for crd, name, city, state in firms:
        conn.execute(
            "INSERT INTO firms (crd_number, primary_name, address_city, address_state, aum_band) "
            "VALUES (?, ?, ?, ?, 'Not disclosed')", (crd, name, city, state),
        )
        conn.execute("INSERT INTO firm_fts (name, crd_number, kind) VALUES (?, ?, 'primary')", (name, crd))
    for crd, name, city, state in state_firms:
        conn.execute(
            "INSERT INTO firms_state (crd_number, primary_name, address_city, address_state, aum_band) "
            "VALUES (?, ?, ?, ?, 'Under $100M')", (crd, name, city, state),
        )
        conn.execute("INSERT INTO firm_fts (name, crd_number, kind) VALUES (?, ?, 'state')", (name, crd))
    for crd, name in other_names:
        conn.execute("INSERT INTO firm_other_names (crd_number, other_name) VALUES (?, ?)", (crd, name))
        conn.execute("INSERT INTO firm_fts (name, crd_number, kind) VALUES (?, ?, 'other')", (name, crd))
    for crd, street1, city in locations:
        conn.execute(
            "INSERT INTO firm_locations (crd_number, location_type, street1, city, state, private_res) "
            "VALUES (?, 'branch', ?, ?, 'XX', 'N')", (crd, street1, city),
        )
    for crd, n in rosters:  # n distinct advisers (ia_only 'Y') plus one broker-only row
        conn.executemany(
            "INSERT INTO ia_rep_firms (ind_source_id, crd_number, ia_only) VALUES (?, ?, 'Y')",
            [(f"r{crd}-{i}", crd) for i in range(n)],
        )
        conn.execute(
            "INSERT INTO ia_rep_firms (ind_source_id, crd_number, ia_only) VALUES (?, ?, 'N')",
            (f"b{crd}", crd),
        )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", copy)
    return copy


_WEG_FIRMS = [
    ("900001", "GARDEN STATE INVESTMENT ADVISORY SERVICES, LLC", "NEWARK", "NJ"),
    ("900002", "WEALTH ENHANCEMENT ADVISORY SERVICES, LLC", "PLYMOUTH", "MN"),
    ("900003", "WEALTH ENHANCEMENT GROUP PARTNERS", "AUSTIN", "TX"),
    ("900005", "Wealth Enhancement Group Inc", "DENVER", "CO"),
]
_WEG_STATE = [("900004", "AAA WEALTH ENHANCEMENT GROUP ADVISERS", "BOISE", "ID")]
_WEG_OTHER = [
    ("900001", "TOTAL WEALTH ENHANCEMENT GROUP LLC"),
    ("900002", "WEALTH ENHANCEMENT GROUP HOLDINGS"),  # prefix match
    ("900002", "WEALTH ENHANCEMENT GROUP, LLC"),      # exact match
]


def test_exact_names_rank_before_partial_matches(tmp_path, monkeypatch):
    _copy_with(tmp_path, monkeypatch, _WEG_FIRMS, _WEG_STATE, _WEG_OTHER)
    result = server.search_firms(name="Wealth Enhancement Group")
    assert [r["crd"] for r in result["results"]] == [
        "900005",  # exact primary name (suffix "Inc" ignored)
        "900002",  # exact other name ("WEALTH ENHANCEMENT GROUP, LLC")
        "900003",  # primary name starts with the search text
        "900004",  # rest, alphabetical (state-only row ranked the same way)
        "900001",
    ]
    by_crd = {r["crd"]: r for r in result["results"]}
    # matched_as shows the best-matching other name, not the last one seen.
    assert by_crd["900002"]["matched_as"] == "also known as Wealth Enhancement Group, LLC"


def test_exact_match_survives_limit(tmp_path, monkeypatch):
    _copy_with(tmp_path, monkeypatch, _WEG_FIRMS, _WEG_STATE, _WEG_OTHER)
    result = server.search_firms(name="WEALTH ENHANCEMENT GROUP", limit=2)
    assert [r["crd"] for r in result["results"]] == ["900005", "900002"]


def test_exact_state_only_name_ranks_first(tmp_path, monkeypatch):
    _copy_with(tmp_path, monkeypatch, firms=[("900020", "AAA DELTA STATE ADVISERS LLC", "X", "NY")])
    result = server.search_firms(name="Delta State Advisers")
    assert [r["crd"] for r in result["results"]] == ["500001", "900020"]
    assert "caveat" in result["results"][0]


def test_browse_order_is_case_insensitive_then_crd(tmp_path, monkeypatch):
    _copy_with(tmp_path, monkeypatch, firms=[
        ("900031", "zeta advisors", "X", "WY"),
        ("900030", "ZETA ADVISORS", "X", "WY"),
        ("900032", "Yankee Advisors", "X", "WY"),
    ])
    result = server.search_firms(state="WY")
    assert [r["crd"] for r in result["results"]] == ["900032", "900030", "900031"]


# ── per-result notes: empty roster, lost characters ─────────────────────────

def test_zero_roster_sec_firm_gets_advisor_count_note():
    result = server.search_firms(limit=50)
    by_crd = {r["crd"]: r for r in result["results"]}
    assert by_crd["100003"]["advisor_count"] == 0
    note = by_crd["100003"]["advisor_count_note"]
    assert "does not necessarily mean" in note and "IAPD" in note
    assert by_crd["100001"]["advisor_count"] > 0
    assert "advisor_count_note" not in by_crd["100001"]
    # State-only firms keep their state caveat and a null count, no note.
    assert by_crd["500001"]["advisor_count"] is None
    assert "advisor_count_note" not in by_crd["500001"]
    assert "caveat" in by_crd["500001"]


def test_unreadable_characters_note_only_when_shown_text_has_fffd(tmp_path, monkeypatch):
    _copy_with(
        tmp_path, monkeypatch,
        firms=[("900040", "M�TIER CAPITAL, LLC", "NEW YORK", "NY"),
               ("900041", "PLAIN CAPITAL, LLC", "S�O PAULO", "NY"),
               ("900042", "OTHER NAME HOLDINGS", "NEW YORK", "NY")],
        other_names=[("900042", "HARVEST�WEALTH CAPITAL")],
    )
    result = server.search_firms(name="capital", limit=50)
    by_crd = {r["crd"]: r for r in result["results"]}
    assert by_crd["900040"]["name"] == "M�tier Capital, LLC"  # left as is
    assert by_crd["900041"]["city"] == "S�o Paulo"
    for crd in ("900040", "900041"):
        assert by_crd[crd]["unreadable_characters_note"] == format.UNREADABLE_CHARS_NOTE
    # matched_as is displayed text too.
    assert "�" in by_crd["900042"]["matched_as"]
    assert "unreadable_characters_note" in by_crd["900042"]
    assert "unreadable_characters_note" not in by_crd["100003"]


# ── own names before other names; larger roster first inside a tier ─────────
# Production: "Merrill" ranked BlackRock (other name "Merrill Lynch Global
# Securities Financing") above Merrill Lynch, Pierce, Fenner & Smith (CRD
# 7691, 25,660 advisers), and "Schwab" ranked Charles Schwab & Co. below
# smaller firms in the same tier.

def test_own_name_tiers_rank_above_other_name_tiers(tmp_path, monkeypatch):
    _copy_with(
        tmp_path, monkeypatch,
        firms=[
            ("900051", "BIGROCK INVESTMENT MANAGEMENT, LLC", "X", "NY"),
            ("900052", "MERRILL LYNCH, PIERCE, FENNER & SMITH INCORPORATED", "X", "NY"),
            ("900053", "THE MERRILL GROUP", "X", "NY"),
            ("900054", "OTHERWISE ADVISORS", "X", "NY"),
        ],
        other_names=[
            ("900051", "MERRILL LYNCH GLOBAL SECURITIES FINANCING"),  # other-name prefix
            ("900054", "SMITH MERRILL PARTNERS"),                      # other-name rest
        ],
        # The other-name hits have the larger rosters: tier still wins.
        rosters=[("900051", 50), ("900052", 5), ("900053", 1), ("900054", 60)],
    )
    result = server.search_firms(name="Merrill")
    assert [r["crd"] for r in result["results"]] == [
        "900052",  # own name starts with the search text
        "900051",  # other name starts with it
        "900053",  # own name contains it
        "900054",  # other name contains it
    ]


def test_larger_roster_ranks_first_inside_a_tier(tmp_path, monkeypatch):
    _copy_with(
        tmp_path, monkeypatch,
        firms=[
            ("900061", "SCHWAB ALPHA ADVISORS", "X", "NY"),
            ("900062", "SCHWAB BETA ADVISORS", "X", "NY"),
            ("900063", "SCHWAB GAMMA ADVISORS", "X", "NY"),
            ("900065", "SCHWAB ZETA ADVISORS", "X", "NY"),
        ],
        state_firms=[("900064", "SCHWAB DELTA ADVISORS", "X", "NY")],
        rosters=[("900062", 30), ("900063", 7), ("900065", 7)],
    )
    result = server.search_firms(name="schwab")
    rows = result["results"]
    assert [r["crd"] for r in rows] == [
        "900062",  # 30 advisers
        "900063",  # 7, then by name
        "900065",  # 7
        "900061",  # 0 (SEC, empty roster), by name before the state-only firm
        "900064",  # state-only counts as 0
    ]
    assert [r["advisor_count"] for r in rows] == [30, 7, 7, 0, None]


# ── SEC AUM band before roster inside a tier ────────────────────────────────
# Production: "Capital Group" matched two exact other names. Integrity
# Alliance (CRD 139627, "$5B–$25B", 359 advisers) ranked above Capital
# Research and Management Company (CRD 110885, "$25B+", roster 0), the real
# Capital Group.

def _set_bands(path, bands, table="firms"):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executemany(
        f"UPDATE {table} SET aum_band = ? WHERE crd_number = ?",
        [(band, crd) for crd, band in bands],
    )
    conn.commit()
    conn.close()


def test_larger_aum_band_ranks_before_larger_roster_inside_a_tier(tmp_path, monkeypatch):
    copy = _copy_with(
        tmp_path, monkeypatch,
        firms=[
            ("900081", "INTEGRITY ALLIANCE, LLC", "X", "NY"),
            ("900082", "CAPITAL RESEARCH AND MANAGEMENT COMPANY", "X", "CA"),
        ],
        other_names=[("900081", "CAPITAL GROUP"), ("900082", "CAPITAL GROUP")],
        rosters=[("900081", 359)],
    )
    _set_bands(copy, [("900081", "$5B\u2013$25B"), ("900082", "$25B+")])
    result = server.search_firms(name="Capital Group")
    assert [r["crd"] for r in result["results"]] == ["900082", "900081"]


def test_aum_band_order_then_roster_inside_a_tier(tmp_path, monkeypatch):
    copy = _copy_with(
        tmp_path, monkeypatch,
        firms=[
            ("900091", "BANDTEST ALPHA", "X", "NY"),    # Not disclosed (default)
            ("900092", "BANDTEST BRAVO", "X", "NY"),    # Under $100M
            ("900093", "BANDTEST CHARLIE", "X", "NY"),  # $100M–$500M
            ("900094", "BANDTEST DELTA", "X", "NY"),    # $500M–$1B
            ("900095", "BANDTEST ECHO", "X", "NY"),     # $1B–$5B, roster 2
            ("900096", "BANDTEST FOXTROT", "X", "NY"),  # $1B–$5B, roster 9
            ("900097", "BANDTEST GOLF", "X", "NY"),     # $5B–$25B
            ("900098", "BANDTEST HOTEL", "X", "NY"),    # $25B+
            ("900099", "BANDTEST INDIA", "X", "NY"),    # unknown band string
            ("900100", "BANDTEST JULIET", "X", "NY"),   # NULL band, roster 1
        ],
        state_firms=[("900101", "BANDTEST KILO", "X", "NY")],  # state-only
        rosters=[("900095", 2), ("900096", 9), ("900100", 1)],
    )
    _set_bands(copy, [
        ("900092", "Under $100M"),
        ("900093", "$100M\u2013$500M"),
        ("900094", "$500M\u2013$1B"),
        ("900095", "$1B\u2013$5B"),
        ("900096", "$1B\u2013$5B"),
        ("900097", "$5B\u2013$25B"),
        ("900098", "$25B+"),
        ("900099", "$1B-$5B"),  # hyphen, not the export's en dash: unknown
        ("900100", None),
    ])
    # A state-only firm's band is not used: it ranks lowest.
    _set_bands(copy, [("900101", "$1B\u2013$5B")], table="firms_state")
    result = server.search_firms(name="bandtest")
    assert [r["crd"] for r in result["results"]] == [
        "900098",  # $25B+
        "900097",  # $5B–$25B
        "900096",  # $1B–$5B, 9 advisers
        "900095",  # $1B–$5B, 2 advisers
        "900094",  # $500M–$1B
        "900093",  # $100M–$500M
        "900092",  # Under $100M
        "900100",  # lowest band (NULL), 1 adviser
        "900091",  # lowest band (Not disclosed), 0, then by name
        "900099",  # lowest band (unknown string)
        "900101",  # lowest band (state-only)
    ]


def test_aum_band_does_not_override_name_tier(tmp_path, monkeypatch):
    copy = _copy_with(
        tmp_path, monkeypatch,
        firms=[
            ("900111", "TIERBAND", "X", "NY"),
            ("900112", "TIERBAND GLOBAL PARTNERS", "X", "NY"),
            ("900113", "THE TIERBAND GROUP", "X", "NY"),
        ],
    )
    _set_bands(copy, [("900112", "$25B+"), ("900113", "$25B+")])
    result = server.search_firms(name="tierband")
    assert [r["crd"] for r in result["results"]] == ["900111", "900112", "900113"]


def test_roster_counts_are_cached_once_per_db(tmp_path, monkeypatch):
    from advisorfinder_mcp import db
    calls = []
    real = db.all_roster_counts

    def counting(conn):
        before = db._roster_cache
        result = real(conn)
        if db._roster_cache is not before:
            calls.append(db.DB_PATH)
        return result

    monkeypatch.setattr(db, "all_roster_counts", counting)
    monkeypatch.setattr(db, "_roster_cache", None)
    server.search_firms(name="alpha wealth")
    server.search_firms(name="beta")
    assert calls == [db.DB_PATH]  # computed once, then reused
    # A different DB_PATH (tests re-point it directly) is never served the
    # old counts.
    copy = _copy_with(tmp_path, monkeypatch, rosters=[("100003", 4)])
    result = server.search_firms(limit=50)
    assert {r["crd"]: r for r in result["results"]}["100003"]["advisor_count"] == 4
    assert calls[-1] == copy


def test_browse_order_ignores_roster(tmp_path, monkeypatch):
    _copy_with(
        tmp_path, monkeypatch,
        firms=[("900071", "ALPHA WY ADVISORS", "X", "WY"), ("900072", "BRAVO WY ADVISORS", "X", "WY")],
        rosters=[("900072", 99)],
    )
    result = server.search_firms(state="WY")
    assert [r["crd"] for r in result["results"]] == ["900071", "900072"]


def test_marketplace_membership_is_not_a_ranking_factor():
    # CLAUDE.md: marketplace affiliation is a label, never a ranking factor.
    # The firm ranking reads only firm_fts, firms, firms_state and the roster.
    import ast
    import inspect
    import textwrap

    from advisorfinder_mcp import db
    for fn in (db.search_firms, db._firm_name_tier, db.all_roster_counts):
        node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:]  # the docstring may say "never a factor"
        assert "marketplace" not in ast.unparse(node).lower(), fn.__name__
