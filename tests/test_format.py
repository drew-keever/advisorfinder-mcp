"""Tests for advisorfinder_mcp.format — pure helpers, no DB required (except
envelope(), which reads export_meta via db.get_meta() — the session-scoped
conftest fixture has already pointed db at the fixture DB by the time this
module runs)."""
import sqlite3

import pytest

from advisorfinder_mcp import format as fmt


# ── advisorfinder_url ────────────────────────────────────────────────────────

def test_advisorfinder_url():
    assert fmt.advisorfinder_url() == "https://advisorfinder.com"


# ── title_case_name ──────────────────────────────────────────────────────────

def test_title_case_name_basic():
    assert fmt.title_case_name("JOHN SMITH") == "John Smith"


def test_title_case_name_ohearn():
    assert fmt.title_case_name("O'HEARN") == "O'Hearn"


def test_title_case_name_mcdonald_suffix():
    assert fmt.title_case_name("MCDONALD III") == "McDonald III"


def test_title_case_name_idempotent():
    once = fmt.title_case_name("O'HEARN")
    twice = fmt.title_case_name(once)
    assert once == twice == "O'Hearn"

    once2 = fmt.title_case_name("MCDONALD III")
    twice2 = fmt.title_case_name(once2)
    assert once2 == twice2 == "McDonald III"


def test_title_case_name_none_and_empty():
    assert fmt.title_case_name(None) is None
    assert fmt.title_case_name("") is None


# ── URL builders ──────────────────────────────────────────────────────────────

def test_iapd_individual_url():
    assert fmt.iapd_individual_url("1000002") == "https://adviserinfo.sec.gov/individual/summary/1000002"


def test_iapd_firm_url():
    assert fmt.iapd_firm_url("100001") == "https://adviserinfo.sec.gov/firm/summary/100001"


def test_brokercheck_individual_url():
    assert fmt.brokercheck_individual_url("1000002") == "https://brokercheck.finra.org/individual/summary/1000002"


def test_brokercheck_firm_url():
    assert fmt.brokercheck_firm_url("100001") == "https://brokercheck.finra.org/firm/summary/100001"


def test_name_search_fallback_urls_present_and_quoted():
    urls = fmt.name_search_urls("Jane O'Hearn")
    assert "iapd" in urls and "brokercheck" in urls
    assert "Jane" not in urls["iapd"] or "%20" in urls["iapd"] or "+" in urls["iapd"]
    # quoting must not raise and must include the sanitized name somehow
    assert "adviserinfo.sec.gov" in urls["iapd"]
    assert "brokercheck.finra.org" in urls["brokercheck"]


# ── disclosure_status: four states ───────────────────────────────────────────
# CORRECTED CONTRACT (coordinator spec correction, post-implementation): keyed
# on ia_reps.has_disclosure ONLY. iar_details row presence only distinguishes
# the two Y sub-states — it never demotes a Y or N rep to 'unknown'. The
# original brief had 'no iar_details row' checked first (-> always unknown),
# which contradicted the export script's own tally (hd=='N' counts as
# none_reported unconditionally, row or no row) — that contradiction is what
# this correction resolves.
#   has_disclosure == 'Y' AND row exists AND (disclosure_count or 0) > 0
#       -> disclosed_with_detail
#   has_disclosure == 'Y' otherwise (row with count 0/NULL, OR no row at all)
#       -> disclosed_no_detail
#   has_disclosure == 'N' (regardless of row existence)
#       -> none_reported
#   has_disclosure NULL/empty/anything else
#       -> unknown

def test_disclosure_status_unknown_when_flag_is_null_regardless_of_row():
    no_row = fmt.disclosure_status(None, None)
    assert no_row["status"] == "unknown"
    assert "no disclosures" not in no_row["guidance"].lower()

    with_row = fmt.disclosure_status(None, {"disclosure_count": 5})
    assert with_row["status"] == "unknown"


def test_disclosure_status_unknown_for_other_non_yn_values():
    result = fmt.disclosure_status("", None)
    assert result["status"] == "unknown"


def test_disclosure_status_none_reported_regardless_of_row_existence():
    with_row = fmt.disclosure_status("N", {"disclosure_count": 0})
    assert with_row["status"] == "none_reported"
    assert with_row["guidance"] == "No disclosures reported."

    no_row = fmt.disclosure_status("N", None)
    assert no_row["status"] == "none_reported"
    assert no_row["guidance"] == "No disclosures reported."


def test_disclosure_status_disclosed_with_detail():
    result = fmt.disclosure_status("Y", {"disclosure_count": 3})
    assert result["status"] == "disclosed_with_detail"
    assert result["disclosure_count"] == 3
    assert "3 disclosure event(s)" in result["guidance"]
    assert "brokercheck" in result["guidance"].lower()


def test_disclosure_status_disclosed_no_detail_row_with_zero_count():
    result = fmt.disclosure_status("Y", {"disclosure_count": 0})
    assert result["status"] == "disclosed_no_detail"
    assert "brokercheck" in result["guidance"].lower()


def test_disclosure_status_disclosed_no_detail_null_count():
    result = fmt.disclosure_status("Y", {"disclosure_count": None})
    assert result["status"] == "disclosed_no_detail"


def test_disclosure_status_disclosed_no_detail_when_no_row_at_all():
    # THE corrected case: 'Y' with no iar_details row must NOT soften to
    # 'unknown' (that would understate a known disclosure) — it's
    # disclosed_no_detail, same as a row with count 0.
    result = fmt.disclosure_status("Y", None)
    assert result["status"] == "disclosed_no_detail"
    assert "brokercheck" in result["guidance"].lower()


def test_disclosure_status_works_with_sqlite_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT 3 AS disclosure_count").fetchone()
    result = fmt.disclosure_status("Y", row)
    assert result["status"] == "disclosed_with_detail"
    assert result["disclosure_count"] == 3
    conn.close()


@pytest.mark.parametrize("has_disclosure,iar_row", [
    ("Y", {"disclosure_count": 3}),
    ("Y", {"disclosure_count": 0}),
    ("Y", None),
])
def test_disclosure_status_never_phrased_clean_or_safe(has_disclosure, iar_row):
    result = fmt.disclosure_status(has_disclosure, iar_row)
    guidance_lower = result["guidance"].lower()
    assert "clean" not in guidance_lower
    assert "safe" not in guidance_lower
    assert "safe" not in guidance_lower


# ── fee_block ─────────────────────────────────────────────────────────────────

def test_fee_block_none_row_returns_none():
    assert fmt.fee_block(None) is None


def test_fee_block_estimate_row_has_estimated_basis_and_disclaimer():
    row = {
        "fee_rate_blended": 0.0125,
        "fee_rate_is_estimate": 1,
        "fee_tiers_json": '[{"tier_label":"first $1M","min_aum":0,"max_aum":1000000,"annual_rate_decimal":0.0125}]',
        "fee_notes": "Negotiable above $5M",
        "account_minimum": 250000,
        "account_minimum_waivable": 1,
        "needs_review": 0,
    }
    block = fmt.fee_block(row)
    assert "estimated" in block["basis"].lower()
    assert "disclaimer" in block
    assert block["tiers"][0]["tier_label"] == "first $1M"


def test_fee_block_needs_review_also_triggers_estimate_basis():
    row = {
        "fee_rate_blended": 0.01,
        "fee_rate_is_estimate": 0,
        "fee_tiers_json": None,
        "fee_notes": None,
        "account_minimum": None,
        "account_minimum_waivable": None,
        "needs_review": 1,
    }
    block = fmt.fee_block(row)
    assert "estimated" in block["basis"].lower()
    assert "disclaimer" in block


def test_fee_block_as_filed_no_disclaimer():
    row = {
        "fee_rate_blended": 0.01,
        "fee_rate_is_estimate": 0,
        "fee_tiers_json": None,
        "fee_notes": None,
        "account_minimum": None,
        "account_minimum_waivable": None,
        "needs_review": 0,
    }
    block = fmt.fee_block(row)
    assert block["basis"] == "as filed in ADV Part 2A"
    assert "disclaimer" not in block


def test_fee_block_never_bare_number_without_basis():
    row = {
        "fee_rate_blended": 0.02,
        "fee_rate_is_estimate": 0,
        "fee_tiers_json": None,
        "fee_notes": None,
        "account_minimum": None,
        "account_minimum_waivable": None,
        "needs_review": 0,
    }
    block = fmt.fee_block(row)
    assert "rate" in block
    assert "basis" in block


# ── fts_query sanitizer ──────────────────────────────────────────────────────

def test_fts_query_basic_tokenizes_and_quotes():
    assert fmt.fts_query("jane smith") == '"jane" "smith"*'


def test_fts_query_single_token_gets_star():
    assert fmt.fts_query("smith") == '"smith"*'


def test_fts_query_empty_is_none():
    assert fmt.fts_query("") is None
    assert fmt.fts_query(None) is None


def test_fts_query_punctuation_only_is_none():
    assert fmt.fts_query("-") is None
    assert fmt.fts_query("'") is None
    assert fmt.fts_query("*") is None


def test_fts_query_injection_string_sanitized_no_exception():
    result = fmt.fts_query('"foo" OR 1; DROP TABLE')
    assert result is not None
    # quotes/semicolons stripped; must not contain raw double-quote-then-unquoted content
    # that could break out of FTS phrase syntax
    assert result.count('"') % 2 == 0


def test_fts_query_apostrophe_and_hyphen_preserved():
    result = fmt.fts_query("o'hearn mary-jane")
    assert result == '"o\'hearn" "mary-jane"*'


def test_fts_query_cjk_name_survives():
    # A [A-Za-z0-9]-only whitelist strips every character of a CJK name,
    # sanitizing it to None -- which callers treat as "no name filter" and
    # silently fall through to an unfiltered browse. Unicode word characters
    # (any script) must survive instead.
    result = fmt.fts_query("李明")
    assert result is not None
    assert result == '"李明"*'


def test_fts_query_cjk_name_matches_fts5_unicode61_index():
    # Empirically verified (not assumed): sqlite's unicode61 tokenizer with
    # remove_diacritics=2 (the export's actual tokenizer config) treats a CJK
    # name as a single letter token that a prefix-star MATCH finds.
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(name, tokenize='unicode61 remove_diacritics 2')")
    conn.execute("INSERT INTO t VALUES ('李明')")
    q = fmt.fts_query("李明")
    assert conn.execute("SELECT name FROM t WHERE t MATCH ?", (q,)).fetchall() == [("李明",)]


def test_fts_query_accented_name_folds_to_ascii_tokens():
    # "José García" must tokenize to ASCII-foldable tokens, not phrase
    # fragments like "Jos"/"Garc"/"a" that can never MATCH the index (the
    # export's advisor_fts is built with remove_diacritics=2, which folds
    # accented Latin letters for matching purposes).
    result = fmt.fts_query("José García")
    assert result == '"Jose" "Garcia"*'


def test_fts_query_accented_name_matches_fts5_unicode61_index():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(name, tokenize='unicode61 remove_diacritics 2')")
    conn.execute("INSERT INTO t VALUES ('José García')")
    q = fmt.fts_query("José García")  # a caller typing the accented form...
    assert conn.execute("SELECT name FROM t WHERE t MATCH ?", (q,)).fetchall() == [("José García",)]
    q_ascii = fmt.fts_query("Jose Garcia")  # ...or the plain-ASCII form...
    assert conn.execute("SELECT name FROM t WHERE t MATCH ?", (q_ascii,)).fetchall() == [("José García",)]
    # ...both must match the same indexed (accented) row.


def test_fts_query_underscore_not_treated_as_name_character():
    # \w in Python's re module matches underscore too, but it isn't a real
    # name character -- must not survive as a "safe" token.
    result = fmt.fts_query("foo_bar")
    assert "_" not in (result or "")


# ── envelope ──────────────────────────────────────────────────────────────────

def test_envelope_keys_present():
    result = fmt.envelope({"foo": "bar"})
    assert result["foo"] == "bar"
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "note" in result["advisorfinder"]
    assert "verify" in result
    assert "coverage_caveats" in result
    assert isinstance(result["coverage_caveats"], list)


def test_envelope_data_as_of_mentions_advisor_and_firm_dates():
    result = fmt.envelope({})
    assert "advisor data as of" in result["data_as_of"]
    assert "firm data as of" in result["data_as_of"]


def test_envelope_caveats_and_verify_passed_through():
    result = fmt.envelope({}, caveats=["watch out"], verify={"iapd": "x"})
    assert result["coverage_caveats"] == ["watch out"]
    assert result["verify"] == {"iapd": "x"}


# ── flexible date parsing + years_since ──────────────────────────────────────

def test_parse_flexible_date_iso():
    import datetime
    assert fmt.parse_flexible_date("2011-06-15") == datetime.date(2011, 6, 15)


def test_parse_flexible_date_mdy():
    import datetime
    assert fmt.parse_flexible_date("6/15/2011") == datetime.date(2011, 6, 15)


def test_parse_flexible_date_garbage_returns_none():
    assert fmt.parse_flexible_date("not-a-real-date") is None
    assert fmt.parse_flexible_date(None) is None
    assert fmt.parse_flexible_date("") is None


def test_years_since_computes_a_plausible_range():
    # Don't hardcode an expected integer (the suite would rot in future years) —
    # assert it's in a sane range for a start date of 2011-06-15.
    years = fmt.years_since("2011-06-15")
    assert 10 <= years <= 40


def test_years_since_none_for_unparseable():
    assert fmt.years_since("garbage") is None
    assert fmt.years_since(None) is None


def test_title_case_firm_name_uppercases_entity_acronyms():
    assert fmt.title_case_firm_name("ALPHA WEALTH LLC") == "Alpha Wealth LLC"
    assert fmt.title_case_firm_name("SMITH ADVISORS PLLC") == "Smith Advisors PLLC"


def test_title_case_firm_name_dotted_acronyms_keep_dots():
    assert (
        fmt.title_case_firm_name("EDWARD D. JONES & CO., L.P.")
        == "Edward D. Jones & Co., L.P."
    )


def test_title_case_firm_name_idempotent_and_none():
    assert fmt.title_case_firm_name("Alpha Wealth LLC") == "Alpha Wealth LLC"
    assert fmt.title_case_firm_name(None) is None
