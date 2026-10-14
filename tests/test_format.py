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


def test_title_case_name_jr_suffix_with_trailing_dot():
    # name_suffix values from the export (Task: post-sweep resume-round,
    # name_suffix column) look like "JR." WITH the trailing dot -- unlike
    # "III"/"II"/"IV", which have no dot and hit the explicit _SUFFIXES
    # branch. "jr." never matches that branch (it's "jr." != "jr"), but
    # falls through to the generic cap_part() path, where plain
    # str.capitalize() on "jr." happens to already yield "Jr." -- this is a
    # characterization test pinning that (already-correct) behavior, not a
    # bugfix.
    assert fmt.title_case_name("SMITH JOHN JR.") == "Smith John Jr."
    assert fmt.title_case_name("ROBERT JONES JR.") == "Robert Jones Jr."


def test_title_case_name_jr_suffix_idempotent():
    once = fmt.title_case_name("ROBERT JONES JR.")
    twice = fmt.title_case_name(once)
    assert once == twice == "Robert Jones Jr."


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


# ── advisory states / broker registration / employment merge ──────────────────

def test_advisory_states_lists_only_the_ia_column():
    row = {"registered_states_ia": "Florida,Texas", "registered_states_ia_pending_ce": None}
    assert fmt.advisory_states_block(row) == {"advisory_states": ["Florida", "Texas"]}


def test_advisory_states_empty_column_gets_the_none_on_record_note():
    row = {"registered_states_ia": "", "registered_states_ia_pending_ce": None}
    block = fmt.advisory_states_block(row)
    assert block["advisory_states"] == []
    assert block["advisory_states_note"] == fmt.ADVISORY_STATES_NONE_NOTE


def test_advisory_states_missing_detail_row_is_not_none_on_record():
    # No iar_details row is a THIRD meaning: we hold no detail record for this
    # person. It must not claim "none on record", which is a positive finding.
    block = fmt.advisory_states_block(None)
    assert block["advisory_states"] == []
    assert block["advisory_states_note"] == fmt.ADVISORY_STATES_UNAVAILABLE_NOTE
    assert block["advisory_states_note"] != fmt.ADVISORY_STATES_NONE_NOTE


def test_advisory_states_pending_ce_is_listed_in_both_arrays():
    row = {"registered_states_ia": "California,Texas", "registered_states_ia_pending_ce": "California"}
    block = fmt.advisory_states_block(row)
    assert block["advisory_states"] == ["California", "Texas"]
    assert block["advisory_states_pending_ce"] == ["California"]
    assert block["advisory_states_pending_ce_note"] == fmt.ADVISORY_STATES_PENDING_CE_NOTE


def test_advisory_states_pending_ce_never_adds_a_state():
    # Defensive: upstream says pending-CE is a strict subset (1,811 of 1,811).
    # If that ever changes, we must not silently grow the advisory list.
    row = {"registered_states_ia": "California", "registered_states_ia_pending_ce": "California,Nevada"}
    block = fmt.advisory_states_block(row)
    assert block["advisory_states"] == ["California"]
    assert block["advisory_states_pending_ce"] == ["California"]


def test_broker_block_present_with_the_count_in_the_note():
    block = fmt.broker_registration_block({"registered_states_bc_count": 29})
    assert block["state_count"] == 29
    assert "29 states" in block["note"]
    # No "Also": for a zero-advisory-state advisor this note sits beside
    # ADVISORY_STATES_NONE_NOTE, where "Also" would presuppose an advisory
    # registration the person does not have.
    assert not block["note"].startswith("Also")


def test_broker_block_uses_the_singular_for_one_state():
    # About 35,000 advisors have exactly one broker state. "in 1 states" is a bug.
    block = fmt.broker_registration_block({"registered_states_bc_count": 1})
    assert "1 state" in block["note"]
    assert "1 states" not in block["note"]


def test_notes_never_claim_a_position_in_the_payload():
    # The payload is JSON rendered in any order by the consuming assistant, so
    # no note may say "below".
    for note in (
        fmt.ADVISORY_STATES_NONE_NOTE,
        fmt.ADVISORY_STATES_UNAVAILABLE_NOTE,
        fmt.ADVISORY_STATES_PENDING_CE_NOTE,
        fmt.broker_registration_block({"registered_states_bc_count": 3})["note"],
    ):
        assert "below" not in note.lower()


def test_broker_block_absent_for_zero_and_for_null():
    assert fmt.broker_registration_block({"registered_states_bc_count": 0}) is None
    assert fmt.broker_registration_block({"registered_states_bc_count": None}) is None
    assert fmt.broker_registration_block({}) is None
    assert fmt.broker_registration_block(None) is None


def test_merge_employments_collapses_the_adviser_broker_pair():
    rows = [
        {"firm_crd": "79", "firm_name": "J.P. MORGAN SECURITIES LLC", "is_current": 1,
         "start_date": "2023-06-06", "end_date": None, "branch_city": "NAPLES",
         "branch_state": "FL", "ia_only": "Y"},
        {"firm_crd": "79", "firm_name": "J.P. MORGAN SECURITIES LLC", "is_current": 1,
         "start_date": "2023-06-06", "end_date": None, "branch_city": "Naples",
         "branch_state": "FL", "ia_only": "N"},
    ]
    merged = fmt.merge_employment_rows(rows)
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
    assert len(fmt.merge_employment_rows(rows)) == 2


def test_merge_employments_falls_back_to_firm_name_when_crd_is_null():
    rows = [
        {"firm_crd": None, "firm_name": "NO CRD LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": "Y"},
        {"firm_crd": None, "firm_name": "NO CRD LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": "N"},
    ]
    assert len(fmt.merge_employment_rows(rows)) == 1


def test_merge_employments_omits_capacity_when_the_flag_is_missing():
    rows = [
        {"firm_crd": "79", "firm_name": "BIG FIRM LLC", "is_current": 1,
         "start_date": "2020-01-01", "end_date": None, "branch_city": "NY",
         "branch_state": "NY", "ia_only": None},
    ]
    merged = fmt.merge_employment_rows(rows)
    assert len(merged) == 1
    assert "capacity" not in merged[0]


def test_merge_employments_single_flag_states_one_capacity():
    adviser = [{"firm_crd": "1", "firm_name": "A", "is_current": 1, "start_date": "2020-01-01",
                "end_date": None, "branch_city": "NY", "branch_state": "NY", "ia_only": "Y"}]
    broker = [{"firm_crd": "2", "firm_name": "B", "is_current": 1, "start_date": "2020-01-01",
               "end_date": None, "branch_city": "NY", "branch_state": "NY", "ia_only": "N"}]
    assert fmt.merge_employment_rows(adviser)[0]["capacity"] == "investment adviser"
    assert fmt.merge_employment_rows(broker)[0]["capacity"] == "broker-dealer"


# ── name_rank_tokens ─────────────────────────────────────────────────────────

def test_name_rank_tokens_basic_lowercases_and_splits():
    assert fmt.name_rank_tokens("John Smith") == ["john", "smith"]


def test_name_rank_tokens_folds_diacritics():
    assert fmt.name_rank_tokens("José García") == ["jose", "garcia"]


def test_name_rank_tokens_drops_suffix_tokens():
    assert fmt.name_rank_tokens("John Smith Jr.") == ["john", "smith"]
    assert fmt.name_rank_tokens("Smith III") == ["smith"]
    assert fmt.name_rank_tokens("Smith iv") == ["smith"]
    assert fmt.name_rank_tokens("Smith Sr") == ["smith"]
    assert fmt.name_rank_tokens("Smith II") == ["smith"]


def test_name_rank_tokens_suffix_only_is_empty():
    assert fmt.name_rank_tokens("Jr") == []


def test_name_rank_tokens_punctuation():
    assert fmt.name_rank_tokens("O'Hearn") == ["o'hearn"]
    assert fmt.name_rank_tokens("'John' -Smith-") == ["john", "smith"]
    assert fmt.name_rank_tokens("John. Smith,") == ["john", "smith"]
    assert fmt.name_rank_tokens("...") == []


def test_name_rank_tokens_none_and_empty():
    assert fmt.name_rank_tokens(None) == []
    assert fmt.name_rank_tokens("") == []
    assert fmt.name_rank_tokens("   ") == []


def test_name_rank_tokens_uses_raw_tokens_not_fts_form():
    # never the quoted/starred FTS form
    assert all('"' not in t and "*" not in t for t in fmt.name_rank_tokens("john smi*"))


# ── marketplace cleanup: DynamoDB JSON unwrap + mojibake repair ──────────────
# Shapes below are copied from the production marketplace export (schema 4).
import json  # noqa: E402

_CFP_DESCRIPTION = "A CFP professional is bound to be a fiduciary..."


def _av_credential(acronym, value, priority=None, id_tag="S"):
    m = {
        "acronym": {"S": acronym},
        "value": {"S": value},
        "description": {"S": _CFP_DESCRIPTION},
        "requirements": {"S": "Exam plus 6,000 hours of experience."},
        "quickFact": {"S": "Over 100,000 CFP professionals."},
        "id": {id_tag: "7"},
    }
    if priority is not None:
        m["sortingPriority"] = {"N": str(priority)}
    return {"M": m}


def test_unwrap_dynamodb_all_types():
    raw = json.dumps({"M": {
        "s": {"S": "x"}, "n": {"N": "0.50"}, "b": {"BOOL": True}, "z": {"NULL": True},
        "l": {"L": [{"S": "a"}, {"N": "2"}]}, "ss": {"SS": ["p", "q"]}, "ns": {"NS": ["1", "2"]},
        "m": {"M": {"inner": {"S": "y"}}},
    }})
    assert fmt.unwrap_dynamodb(raw) == {
        "s": "x", "n": "0.50", "b": True, "z": None, "l": ["a", "2"],
        "ss": ["p", "q"], "ns": ["1", "2"], "m": {"inner": "y"},
    }


def test_unwrap_dynamodb_tolerates_plain_values():
    assert fmt.unwrap_dynamodb("CFP, CFA") == "CFP, CFA"
    assert fmt.unwrap_dynamodb('["a", "b"]') == ["a", "b"]
    assert fmt.unwrap_dynamodb('{"plain": 1, "nested": {"S": "x"}}') == {"plain": 1, "nested": "x"}
    assert fmt.unwrap_dynamodb([{"S": "TX"}]) == ["TX"]
    assert fmt.unwrap_dynamodb(None) is None
    assert fmt.unwrap_dynamodb("   ") is None


@pytest.mark.parametrize("bad", [
    "[{not json", '{"S": 5}', '{"M": "x"}', '{"L": 3}', '{"N": true}', '{"SS": "x"}', b"\xff\xfe[", 3.5,
])
def test_unwrap_dynamodb_never_raises_on_malformed_input(bad):
    fmt.unwrap_dynamodb(bad)  # must not raise


def test_unwrap_dynamodb_bad_entries_become_none():
    assert fmt.unwrap_dynamodb('{"S": 5}') is None
    assert fmt.unwrap_dynamodb('[{"S": 5}, {"S": "ok"}]') == [None, "ok"]


def test_marketplace_credentials_clean_sorted_and_labeled():
    raw = json.dumps([
        _av_credential("CFA", "Chartered Financial Analyst", 2),
        _av_credential("CFP®", "Certified Financial Planner™", 0, id_tag="N"),
        {"M": {"id": {"S": "99"}, "sortingPriority": {"N": "1"}}},  # bare catalog id
        _av_credential("ChFC", "Chartered Financial Consultant"),  # no sortingPriority
    ])
    assert fmt.marketplace_credentials(raw) == [
        {"acronym": "CFP®", "name": "Certified Financial Planner™", "status": "self-reported"},
        {"acronym": "CFA", "name": "Chartered Financial Analyst", "status": "self-reported"},
        {"acronym": "ChFC", "name": "Chartered Financial Consultant", "status": "self-reported"},
    ]
    # The generic credential marketing copy never reaches the output.
    assert "fiduciary" not in json.dumps(fmt.marketplace_credentials(raw))


def test_marketplace_credentials_plain_and_empty():
    assert fmt.marketplace_credentials("CFP, CFA") == "CFP, CFA"
    assert fmt.marketplace_credentials("[]") == []
    assert fmt.marketplace_credentials(None) is None
    assert fmt.marketplace_credentials("[{broken") == "[{broken"


def test_marketplace_education_extracts_year_never_invents():
    raw = json.dumps([
        {"M": {"institution": {"S": "Texas A&M"}, "degreeObtained": {"S": "BBA"},
               "completedDate": {"S": "Fri May 15 2009 04:00:00 GMT+0000 (Coordinated Universal Time)"},
               "educationId": {"S": "e1"}}},
        {"M": {"institution": {"S": "NYU"}, "degreeObtained": {"S": "MBA"},
               "completedDate": {"NULL": True}, "educationId": {"S": "e2"}}},
        {"M": {"institution": {"S": "Rice"}, "degreeObtained": {"S": "BA"},
               "completedDate": {"S": "not a date GMT+0000"}}},
        {"M": {"educationId": {"S": "e3"}}},
    ])
    assert fmt.marketplace_education(raw) == [
        {"institution": "Texas A&M", "degree": "BBA", "year": 2009},
        {"institution": "NYU", "degree": "MBA", "year": None},
        {"institution": "Rice", "degree": "BA", "year": None},
    ]
    assert fmt.marketplace_education("MBA, NYU Stern School of Business") == "MBA, NYU Stern School of Business"


def test_marketplace_pricing_shapes_values_as_listed():
    raw = json.dumps([
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": ".5%"}, "maxRange": {"S": "2.00"}}},
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "65"}, "maxRange": {"S": ""}}},
        {"M": {"typeName": {"S": "AUM"}}},
        {"M": {"typeName": {"S": "FLAT_FEE"}, "minRange": {"S": "1000"}, "maxRange": {"S": "2500"},
               "frequency": {"S": "YEARLY"}}},
        {"M": {"typeName": {"S": "FLAT_FEE"}, "frequency": {"N": "-1"}}},
        {"M": {"typeName": {"S": "FLAT_FEE"}, "frequency": {"S": ""}, "minRange": {"S": ""}}},
        {"M": {"typeName": {"S": "HOURLY"}, "rate": {"S": "250"}}},
        {"M": {"typeName": {"S": "ONE_TIME"}, "cost": {"S": "1,500"}}},
        {"M": {"typeName": {"S": "FEES_INFORMATION"}, "link": {"S": "https://example.com/fees"}}},
        {"M": {"minRange": {"S": "1"}}},  # no type: skipped
    ])
    assert fmt.marketplace_pricing(raw) == [
        {"type": "aum", "min": ".5", "max": "2.00", "unit": "percent of assets"},
        # Outlier passed through as listed, never "corrected".
        {"type": "aum", "min": "65", "unit": "percent of assets"},
        {"type": "aum"},
        {"type": "flat_fee", "min": "1000", "max": "2500", "frequency": "yearly"},
        {"type": "flat_fee"},
        {"type": "flat_fee"},
        {"type": "hourly", "rate": "250"},
        {"type": "one_time", "cost": "1,500"},
        {"type": "fees_information", "link": "https://example.com/fees"},
    ]


def test_marketplace_pricing_plain_text_passes_through():
    text = "$3,000/yr flat planning fee, or $250/hr for one-off consultations"
    assert fmt.marketplace_pricing(text) == text
    assert fmt.marketplace_pricing(None) is None


def test_marketplace_states_av_json_and_csv():
    assert fmt.marketplace_states('[{"S":"TX"},{"S":"OK"}]') == ["TX", "OK"]
    assert fmt.marketplace_states("NY,NJ, CT") == ["NY", "NJ", "CT"]
    assert fmt.marketplace_states(None) == []
    assert fmt.marketplace_states("[{bad") == ["[{bad"]  # plain text, split as CSV


# Mojibake samples from production (UTF-8 decoded as cp1252 upstream).
@pytest.mark.parametrize("garbled, fixed", [
    ("ðŸ‘‹ I'm Allen", "👋 I'm Allen"),
    ("Nonnaâ€™s Garden", "Nonna’s Garden"),
    ("CERTIFIED FINANCIAL PLANNERÂ® professional", "CERTIFIED FINANCIAL PLANNER® professional"),
    ("a â€œfortressâ€\x9d around", "a “fortress” around"),
    ("transitionsâ€”retirement", "transitions—retirement"),
])
def test_clean_text_repairs_mojibake(garbled, fixed):
    assert fmt.clean_text(garbled) == fixed


@pytest.mark.parametrize("correct", [
    "CFP® professional", "Nonna’s “Garden”", "José García", "Hello! 👋", "fee-only — 1.00%",
    "Certified Financial Planner™", "plain ASCII text", "",
])
def test_clean_text_leaves_correct_text_unchanged(correct):
    assert fmt.clean_text(correct) == correct


def test_clean_text_non_strings_pass_through():
    assert fmt.clean_text(None) is None
    assert fmt.clean_text(5) == 5


def test_marketplace_search_text_excludes_credential_marketing_copy():
    row = {
        "bio": "Helps Nonnaâ€™s family.",
        "clientDescription": None,
        "in_their_own_words": '["I love sailing."]',
        "credentials": json.dumps([_av_credential("CFP®", "Certified Financial Planner™", 0)]),
    }
    text = fmt.marketplace_search_text(row)
    assert "nonna’s" in text  # repaired before matching
    assert "sailing" in text
    assert "cfp®" in text and "certified financial planner™" in text
    assert "fiduciary" not in text  # generic description copy is not searched
    assert '"s"' not in text and "acronym" not in text  # no JSON keys
