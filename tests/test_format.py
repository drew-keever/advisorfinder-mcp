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
    assert "coverage_caveats" in result
    assert isinstance(result["coverage_caveats"], list)


def test_envelope_data_as_of_mentions_advisor_and_firm_dates():
    result = fmt.envelope({})
    assert "advisor data as of" in result["data_as_of"]
    assert "firm data as of" in result["data_as_of"]


def test_envelope_omits_empty_verify():
    # No "verify": {} in any response: the key is present only with links.
    assert "verify" not in fmt.envelope({})
    assert "verify" not in fmt.envelope({}, verify={})
    assert "verify" not in fmt.envelope({}, verify=None)


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
    # Not filled in on the profile -> None ("not stated"), never [] ("none").
    assert fmt.marketplace_states(None) is None
    assert fmt.marketplace_states("") is None
    assert fmt.marketplace_states("[]") is None
    assert fmt.marketplace_states("[{bad") == ["[{bad"]  # plain text, split as CSV


def test_marketplace_states_production_values():
    # The only two allowedStates values in production (2026-08-28 export).
    assert fmt.marketplace_states('[{"S":"TX"},{"S":"OK"},{"S":"NM"},{"S":"LA"}]') == ["TX", "OK", "NM", "LA"]
    assert fmt.marketplace_states('[{"S":"NC"}]') == ["NC"]


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


# ── Excel export artifacts in marketplace prose ──────────────────────────────
# Production: a literal backslash-n (two characters) in 206/294 bios and 11
# client descriptions, and "_x000D_" in 8 bios + 4 client descriptions.

def test_clean_prose_removes_excel_artifacts():
    raw = "planning._x000D_\\n_x000D_\\nAs a citizen of the US.\\nJoe joined in 1975.  "
    assert fmt.clean_prose(raw) == "planning.\n\nAs a citizen of the US.\nJoe joined in 1975."


def test_clean_prose_collapses_three_or_more_newlines_to_two():
    assert fmt.clean_prose("a\\n\\n\\n\\nb") == "a\n\nb"
    assert fmt.clean_prose("a\n\\n\nb") == "a\n\nb"


def test_clean_prose_leaves_clean_text_unchanged_and_still_repairs_mojibake():
    assert fmt.clean_prose("It’s fine.\n\nNew paragraph. ") == "It’s fine.\n\nNew paragraph. "
    assert fmt.clean_prose("Iâ€™m a CFPÂ®\\nholder") == "I’m a CFP®\nholder"
    assert fmt.clean_prose(None) is None
    assert fmt.clean_prose("") == ""


def test_prose_lists_and_degrees_are_cleaned():
    assert fmt.marketplace_text_list(json.dumps(["One\\ntwo_x000D_", "  "])) == ["One\ntwo"]
    raw = json.dumps([{"M": {"institution": {"S": "NYU"}, "degreeObtained": {"S": "MBA_x000D_\\n"}}}])
    assert fmt.marketplace_education(raw) == [{"institution": "NYU", "degree": "MBA", "year": None}]


# ── pricing: unit only for bare numbers; allowlisted keys ────────────────────

def test_aum_unit_only_when_every_present_value_is_a_bare_number():
    raw = json.dumps([
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "1%"}, "maxRange": {"S": "1.25"}}},
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "1%"}, "maxRange": {"S": "varies"}}},
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "$500"}}},
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "1%%"}}},  # one "%" removed only
        {"M": {"typeName": {"S": "AUM"}, "minRange": {"S": "2.0"}, "maxRange": {"S": "1.1"}}},
    ])
    assert fmt.marketplace_pricing(raw) == [
        {"type": "aum", "min": "1", "max": "1.25", "unit": "percent of assets"},
        {"type": "aum", "min": "1", "max": "varies"},
        {"type": "aum", "min": "$500"},
        {"type": "aum", "min": "1%"},
        # min > max passes through as listed, never "corrected".
        {"type": "aum", "min": "2.0", "max": "1.1", "unit": "percent of assets"},
    ]


def test_pricing_drops_unknown_fields():
    raw = json.dumps([
        {"M": {"typeName": {"S": "HOURLY"}, "rate": {"S": "250"}, "internalId": {"S": "x1"},
               "unit": {"S": "smuggled"}, "description": {"S": "promo copy"}}},
    ])
    assert fmt.marketplace_pricing(raw) == [{"type": "hourly", "rate": "250"}]


# ── NaN / Infinity never become "nan" strings or undefined ordering ──────────

def test_non_finite_numbers_are_dropped_and_sort_last():
    raw = (
        '[{"M": {"acronym": {"S": "Z1"}, "sortingPriority": {"N": NaN}}},'
        ' {"M": {"acronym": {"S": "Z2"}, "sortingPriority": {"N": "Infinity"}}},'
        ' {"M": {"acronym": {"S": "Z3"}, "sortingPriority": {"S": "abc"}}},'
        ' {"M": {"acronym": {"S": "A"}, "sortingPriority": {"N": "2"}}},'
        ' {"M": {"acronym": {"S": "B"}, "sortingPriority": {"N": "-Infinity"}}},'
        ' {"M": {"acronym": {"S": "C"}, "sortingPriority": {"N": "1"}}}]'
    )
    assert [c["acronym"] for c in fmt.marketplace_credentials(raw)] == ["C", "A", "Z1", "Z2", "Z3", "B"]
    assert fmt.unwrap_dynamodb('{"N": NaN}') is None
    assert fmt.unwrap_dynamodb('{"N": Infinity}') is None
    pricing = fmt.marketplace_pricing('[{"M": {"typeName": {"S": "HOURLY"}, "rate": NaN}}]')
    assert pricing == [{"type": "hourly"}]
    assert "nan" not in json.dumps(fmt.marketplace_credentials(raw)).lower()


# ── title_case_firm_name: dotted initials and initialisms ───────────────────

@pytest.mark.parametrize("raw, expected", [
    ("J.P. MORGAN SECURITIES LLC", "J.P. Morgan Securities LLC"),
    ("RBC GLOBAL ASSET MANAGEMENT (U.S.) INC.", "RBC Global Asset Management (U.S.) Inc."),
    ("JPMORGAN CHASE BANK, N.A.", "Jpmorgan Chase Bank, N.A."),
    ("B.O.S.S. RETIREMENT ADVISORS, LLC", "B.O.S.S. Retirement Advisors, LLC"),
    ("A.G.P / ALLIANCE GLOBAL PARTNERS", "A.G.P / Alliance Global Partners"),
    ("LPL FINANCIAL LLC", "LPL Financial LLC"),
    ("ICE DATA PRICING & REFERENCE DATA, LLC", "ICE Data Pricing & Reference Data, LLC"),
    ("UBS FINANCIAL SERVICES INC.", "UBS Financial Services Inc."),
    ("BNY MELLON ADVISORS, INC.", "BNY Mellon Advisors, Inc."),
    ("MANULIFE INVESTMENT MANAGEMENT (US) LLC", "Manulife Investment Management (US) LLC"),
    ("AXA INVESTMENT MANAGERS US INC", "AXA Investment Managers US Inc"),
    ("BB&T SECURITIES, LLC", "BB&T Securities, LLC"),
    ("H&H INTERNATIONAL INVESTMENT, LLC", "H&H International Investment, LLC"),
    ("MORGAN STANLEY AIP GP LP", "Morgan Stanley AIP GP LP"),
    ("KOSHINSKI ASSET MANAGEMENT, INC.-DBA IPI ASSET MANAGEMENT, INC.",
     "Koshinski Asset Management, Inc.-Dba IPI Asset Management, Inc."),
    ('"TATE ASSET MANAGEMENT" AND "DCO WEALTH MANAGEMENT"',
     '"Tate Asset Management" And "Dco Wealth Management"'),
    # Unchanged behavior: legal suffixes, Inc./Ltd/Corp., St., Mc, roman numerals.
    ("EDWARD D. JONES & CO., L.P.", "Edward D. Jones & Co., L.P."),
    ("OXFORD FINANCIAL GROUP, LTD", "Oxford Financial Group, Ltd"),
    ("ST. CLAIR FINANCIAL", "St. Clair Financial"),
    ("COOPER MCMANUS", "Cooper McManus"),
    ("III CAPITAL MANAGEMENT", "III Capital Management"),
])
def test_title_case_firm_name_initials(raw, expected):
    assert fmt.title_case_firm_name(raw) == expected
    assert fmt.title_case_firm_name(expected) == expected  # idempotent


@pytest.mark.parametrize("raw, expected", [
    ("RICE FINANCIAL", "Rice Financial"),       # ICE only as a whole token
    ("ICED CAPITAL", "Iced Capital"),
    ("TDX FUND", "Tdx Fund"),                  # TD only as a whole token
    ("MUSTARD SEED ADVISORS", "Mustard Seed Advisors"),
    ("TRUST US WEALTH", "Trust US Wealth"),    # documented: US is an initialism here
])
def test_title_case_firm_name_initialisms_are_whole_token_only(raw, expected):
    assert fmt.title_case_firm_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    # US is the pronoun after ABOUT/FOR/WITH/TO/OF; the country elsewhere.
    ("WHAT ABOUT US FINANCIAL LLC", "What About Us Financial LLC"),
    ("WEALTH FOR US", "Wealth For Us"),
    ("RECORD CURRENCY MANAGEMENT (US) INC", "Record Currency Management (US) Inc"),
    ("AXA INVESTMENT MANAGERS US INC", "AXA Investment Managers US Inc"),
    # ICE only as the name's first token.
    ("ICE DATA PRICING & REFERENCE DATA, LLC", "ICE Data Pricing & Reference Data, LLC"),
    ("POLERO ICE ADVISERS, L.L.C.", "Polero Ice Advisers, L.L.C."),
    # CPA, MBA, PFS; parts split by "/", "," and "-".
    ("ADI MAHAJAN CPA PC", "Adi Mahajan CPA PC"),
    ("MYRNA MITCHELL, MBA, CFP", "Myrna Mitchell, MBA, CFP"),
    ("JAMES C WANGERIN, CPA/PFS", "James C Wangerin, CPA/PFS"),
    ("GEORGE PAPADOPOULOS, CPA/PFS,CFP", "George Papadopoulos, CPA/PFS,CFP"),
    ("MBA ADVISORS LLC", "MBA Advisors LLC"),
    # One letter & Co is a company, not two initials.
    ("R&CO CM (NA)", "R&Co Cm (NA)"),
    ("A&M CAPITAL", "A&M Capital"),
    # 2-3 letter abbreviation that is the whole parenthesized text.
    ("PINNACLE ASSOCIATES, INC. (NY)", "Pinnacle Associates, Inc. (NY)"),
    ("KAINOS (TX) CAPITAL LP", "Kainos (TX) Capital LP"),
    ("MERCATOR GP (DE) LLC", "Mercator GP (DE) LLC"),
    ("LUBERT-ADLER PARTNERS (DBA)", "Lubert-Adler Partners (DBA)"),
    ("FINANCIAL PLANNING GROUP INC. (THE)", "Financial Planning Group Inc. (The)"),
    ("SMITH (RELYING ADVISER)", "Smith (Relying Adviser)"),
    # First letter after a leading &, @, #, + or curly quote.
    ("&PARTNERS", "&Partners"),
    ("&wealth partners, llc", "&Wealth Partners, LLC"),
    ("CAPONE &PARTNERS", "Capone &Partners"),
    ("DUNCKER, STREETT &CO., LLC", "Duncker, Streett &Co., LLC"),
    ("@FINANCIAL INVESTMENTS", "@Financial Investments"),
    ("+RINGENBERG FINANCIAL GROUP", "+Ringenberg Financial Group"),
    ("\u201cTHE FIRM\u201d LLC", "\u201cThe Firm\u201d LLC"),
    # Roman numerals, also inside a token.
    ("ABRY PARTNERS II, LLC", "Abry Partners II, LLC"),
    ("BATTERY PARTNERS VIII (AIV), LLC", "Battery Partners VIII (AIV), LLC"),
    ("ARES CLO MANAGEMENT XII, L.P.", "Ares Clo Management XII, L.P."),
    ("ESSEX WOODLANDS FUND-IX GP, LP", "Essex Woodlands Fund-IX GP, LP"),
    ("SEI-II MANAGEMENT", "SEI-II Management"),
    ("CROW HOLDINGS VII-B, L.P.", "Crow Holdings VII-B, L.P."),
    ("CORTEC INVESTMENT (III) L.L.C.", "Cortec Investment (III) L.L.C."),
    ("CLASS VI VENTURES, LLC", "Class VI Ventures, LLC"),
    ("VII CAPITAL MANAGEMENT LLC", "VII Capital Management LLC"),
    ("VI-BHAKTI VENTURES", "Vi-Bhakti Ventures"),  # a word at the name start
    ("DAVID X MARTIN, LLC", "David X Martin, LLC"),  # V and X are initials
])
def test_title_case_firm_name_context_rules(raw, expected):
    assert fmt.title_case_firm_name(raw) == expected
    assert fmt.title_case_firm_name(expected) == expected  # idempotent


def test_title_case_firm_name_keeps_replacement_char():
    assert fmt.title_case_firm_name("M�TIER CAPITAL, LLC") == "M�tier Capital, LLC"


def test_firm_initialisms_are_uppercase_and_short():
    for token in fmt._FIRM_INITIALISMS:
        assert token == token.upper() and 2 <= len(token) <= 5, token


def test_firm_initialism_rules_do_not_leak_into_person_names():
    # The initialism allowlist lives in title_case_firm_name only; person and
    # city names keep title_case_name's behavior. (Dotted initials in person
    # names are a separate follow-up, not covered here.)
    assert fmt.title_case_name("LPL JONES") == "Lpl Jones"
    assert fmt.title_case_name("TD JONES") == "Td Jones"


# ── firm_rank_key ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", fmt.FIRM_LEGAL_SUFFIXES)
def test_firm_rank_key_drops_each_trailing_suffix(suffix):
    assert fmt.firm_rank_key(f"Wealth Enhancement Group, {suffix}") == "wealth enhancement group"
    assert fmt.firm_rank_key(f"wealth enhancement group {suffix.lower()}") == "wealth enhancement group"


def test_firm_rank_key_suffix_list_is_explicit():
    assert set(fmt.FIRM_LEGAL_SUFFIXES) == {
        "LLC", "L.L.C.", "INC", "INCORPORATED", "LP", "L.P.", "LLP", "LLLP",
        "LTD", "LIMITED", "CO", "COMPANY", "CORP", "CORPORATION", "PLLC", "PLC",
        "PC", "PA", "P.A.", "N.A.",
    }


@pytest.mark.parametrize("raw, expected", [
    ("WEALTH ENHANCEMENT GROUP, LLC", "wealth enhancement group"),
    ("Wealth  Enhancement   Group", "wealth enhancement group"),
    ("GOLDMAN SACHS & CO. LLC", "goldman sachs"),          # suffixes removed repeatedly
    ("Edward D. Jones & Co., L.P.", "edward d jones"),
    ("J.P. Morgan", "jp morgan"),
    ("JPMorgan Chase Bank, N.A.", "jpmorgan chase bank"),
    ("Société Générale", "societe generale"),               # diacritics folded
    ("L.L.C.", "llc"),                                       # never stripped to empty
    ("Co Investors LLC", "co investors"),                    # only TRAILING suffixes
    ("Smith Financial Planning, P.A.", "smith financial planning"),
    ("SMITH FINANCIAL PLANNING PA", "smith financial planning"),
    ("Barings Asset Management PLC", "barings asset management"),
    ("Acme Partners, LLLP", "acme partners"),
    ("Schroder Investment Management Limited", "schroder investment management"),
    ("Capital Research and Management Company", "capital research and management"),
    ("Company Limited", "company"),                          # first token always kept
    ("P.A.", "pa"),
    ("Limited Partners Co", "limited partners"),
    ("M�TIER CAPITAL, LLC", "m tier capital"),
    ("", ""),
    (None, ""),
])
def test_firm_rank_key(raw, expected):
    assert fmt.firm_rank_key(raw) == expected


# ── normalize_url ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("mgrwealth.com", "https://mgrwealth.com"),
    ("linkedin.com/in/jakehughesnm", "https://linkedin.com/in/jakehughesnm"),
    ("www.twitter.com/7saturdaysaweek", "https://www.twitter.com/7saturdaysaweek"),
    ("  https://Example.com/A  ", "https://Example.com/A"),
    ("http://example.com", "http://example.com"),
    ("HTTPS://EXAMPLE.COM/Path", "HTTPS://EXAMPLE.COM/Path"),
    ("linkedin.com/in/joe-cfp®", "https://linkedin.com/in/joe-cfp®"),
    ("//cdn.example.com/x", "https://cdn.example.com/x"),
    ("www.example.com:8080/a", "https://www.example.com:8080/a"),
    ("javascript:alert(1)", None),
    ("JavaScript:alert(1)", None),
    ("mailto:a@example.com", None),
    ("ftp://example.com", None),
    ("", None),
    ("   ", None),
    (None, None),
    (42, None),
    # Whitespace after "/" is removed; any other internal whitespace means
    # the value is not a working link.
    ("www.linkedin.com/in/ philip-mcdonald", "https://www.linkedin.com/in/philip-mcdonald"),
    ("https://example.com/ \tpage", "https://example.com/page"),
    ("https://tswinvest.com (primary firm website)", None),
    ("https://koonfinancial.com; https://www.linkedin.com/company/koon", None),
    ("https://kingfishercapital.com/ legacy url", None),
    ("https://saxony advisors- facebook", None),
])
def test_normalize_url(raw, expected):
    assert fmt.normalize_url(raw) == expected


def test_has_unreadable_chars():
    assert fmt.has_unreadable_chars(None, "S�O PAULO")
    assert not fmt.has_unreadable_chars(None, "São Paulo", 3)
    assert "�" in fmt.UNREADABLE_CHARS_CAVEAT


# ── iso_date: every date a tool returns is YYYY-MM-DD ───────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("2011-06-15", "2011-06-15"),                       # employment dates
    ("2024-08-14 20:25:09", "2024-08-14"),              # marketplace memberSince
    ("2022-12-06 00:00:00", "2022-12-06"),
    ("3/30/2026", "2026-03-30"),                        # brochure_date
    ("8/19/2003", "2003-08-19"),                        # industry_start_date, exam dates
    ("12/11/2015", "2015-12-11"),
    ("04/14/2009", "2009-04-14"),                       # state registration dates
    ("Mon May 05 2025 12:59:41 GMT+0000 (Coordinated Universal Time)", "2025-05-05"),
    ("Tue Dec 31 2024 16:38:19 GMT+0000 (Coordinated Universal Time)", "2024-12-31"),
    ("  2011-06-15  ", "2011-06-15"),
])
def test_iso_date_known_formats(raw, expected):
    assert fmt.iso_date(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "   ", "not-a-real-date", "2/30/2020", "2020-13-01", "13/1/2020",
    "Mon Foo 05 2025", "2025", 20250505, "05/2025",
])
def test_iso_date_empty_or_unparseable_is_none(raw):
    assert fmt.iso_date(raw) is None


def test_parse_flexible_date_accepts_every_iso_date_format():
    import datetime
    assert fmt.parse_flexible_date("2024-08-14 20:25:09") == datetime.date(2024, 8, 14)
    assert fmt.parse_flexible_date(
        "Mon May 05 2025 12:59:41 GMT+0000 (Coordinated Universal Time)"
    ) == datetime.date(2025, 5, 5)
    assert isinstance(fmt.years_since("8/19/2003"), int)


# ── fee_block: no fee value extracted ────────────────────────────────────────

_EMPTY_PART2A = {  # Vanguard Advisers (CRD 106715) in the 2026-08-28 export
    "brochure_name": "STABLE VALUE",
    "brochure_date": "3/30/2026",
    "fee_rate_blended": None,
    "fee_rate_is_estimate": 0,
    "fee_tiers_json": "[]",
    "fee_notes": None,
    "account_minimum": None,
    "account_minimum_waivable": 0,
    "needs_review": 1,
}


def test_fee_block_nothing_extracted_says_so():
    block = fmt.fee_block(_EMPTY_PART2A)
    assert block["basis"] == "not extracted"
    assert "disclaimer" not in block
    assert "account_minimum_waivable" not in block
    assert "estimated" not in str(block).lower()
    assert block["brochure_date"] == "2026-03-30"
    assert "no fee details were extracted" in block["note"].lower()
    assert "SEC IAPD" in block["note"]


def test_fee_block_nothing_extracted_without_brochure_date():
    block = fmt.fee_block({**_EMPTY_PART2A, "brochure_date": None, "fee_tiers_json": None, "fee_notes": "  "})
    assert block["basis"] == "not extracted"
    assert block["brochure_date"] is None


def test_fee_block_account_minimum_zero_is_an_extracted_value():
    block = fmt.fee_block({**_EMPTY_PART2A, "account_minimum": 0})
    assert block["basis"] != "not extracted"
    assert block["account_minimum"] == 0
    assert "account_minimum_waivable" in block


def test_fee_block_brochure_date_is_iso():
    block = fmt.fee_block({**_EMPTY_PART2A, "fee_rate_blended": 0.01, "needs_review": 0})
    assert block["brochure_date"] == "2026-03-30"
    assert block["basis"] == "as filed in ADV Part 2A"


# ── state registrations: [{state, date}] ────────────────────────────────────

def test_state_registrations_firms_format():
    # firms.state_registrations in production: "ST-MM/DD/YYYY, ..."
    assert fmt.state_registrations("AK-04/14/2009, CA-03/13/2025") == [
        {"state": "AK", "date": "2009-04-14"},
        {"state": "CA", "date": "2025-03-13"},
    ]


def test_state_registrations_firms_state_format():
    # firms_state.states_approved in production: "ST:YYYY-MM-DD"
    assert fmt.state_registrations("VA:2023-01-03") == [{"state": "VA", "date": "2023-01-03"}]


def test_state_registrations_without_dates_and_odd_values():
    assert fmt.state_registrations("NY,FL") == [
        {"state": "NY", "date": None}, {"state": "FL", "date": None},
    ]
    assert fmt.state_registrations("TX-99/99/2020") == [{"state": "TX", "date": None}]
    assert fmt.state_registrations(None) == []
    assert fmt.state_registrations("") == []


# ── marketplace: virtual meetings flag ──────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("1", True), ("Yes", True), ("true", True), (1, True),
    ("0", False), ("No", False), (0, False),
    (None, None), ("", None), ("  ", None),
])
def test_marketplace_flag(raw, expected):
    assert fmt.marketplace_flag(raw) is expected


# ── marketplace: credential suffixes in display names ───────────────────────

def _creds(*acronyms):
    return [{"acronym": a, "name": None, "status": "self-reported"} for a in acronyms]


@pytest.mark.parametrize("name, creds, expected", [
    ("David Calliott, CFP®", _creds("CFP®", "Series 7", "Series 66"), "David Calliott"),
    ("Steven Evensen, CFP ®", _creds("CFP®"), "Steven Evensen"),
    ("Justin Porter, CPA, CFP(r)", _creds("CFP®", "CPA"), "Justin Porter"),
    ("Jay Williams, CFP®,\xa0CIMA®", _creds("AIF®", "CIMA®", "CFP®"), "Jay Williams"),
    ("Erik Baskin, CFP®, MBA", _creds("MBA", "Series 65", "CFP®"), "Erik Baskin"),
    ("Stephen Santangelo, CFA, CAIA", _creds("CAIA®", "CFA®"), "Stephen Santangelo"),
    # Only the trailing run that matches the member's own credentials goes.
    ("D. Vance Barse, CPWA®, AIF®", _creds("AIF®"), "D. Vance Barse, CPWA®"),
    ("Fred Tan, CFP, MBA", _creds("CFP®", "Series 65"), "Fred Tan, CFP, MBA"),
    # No credentials on the profile: nothing is stripped.
    ("Dan Bennett, CFP", None, "Dan Bennett, CFP"),
    ("Dan Bennett, CFP", [], "Dan Bennett, CFP"),
    # No comma: never cut.
    ("EP Wealth Advisors", _creds("EP"), "EP Wealth Advisors"),
    ("Cfp Smith", _creds("CFP®"), "Cfp Smith"),
    # The name itself is never removed, even when it equals an acronym.
    ("CFA, CFP", _creds("CFA®", "CFP®"), "CFA"),
    # Plain-text credentials (the fixture shape).
    ("John Q. Smith, CFA", "CFP, CFA", "John Q. Smith"),
    (None, _creds("CFP®"), None),
])
def test_marketplace_display_name(name, creds, expected):
    assert fmt.marketplace_display_name(name, creds) == expected


# ── marketplace: credentials vs designations ────────────────────────────────

def test_credential_sources_differ():
    designations = [{"code": "CFP", "name": "Certified Financial Planner"}]
    # No designations on file is missing data, not a difference.
    assert fmt.credential_sources_differ([], _creds("CFP®", "Series 7")) is False
    assert fmt.credential_sources_differ(designations, _creds("CFP®", "CFA®")) is True
    assert fmt.credential_sources_differ(designations, "CFP, CFA") is True
    # Same designations: exam licenses (Series N) are not compared -- SEC
    # records list them as exams, never as designations.
    assert fmt.credential_sources_differ(designations, _creds("CFP®", "Series 65")) is False
    assert fmt.credential_sources_differ(designations, "CFP") is False
    # Nothing on the profile: nothing to compare.
    assert fmt.credential_sources_differ(designations, None) is False
    assert fmt.credential_sources_differ([], []) is False


def _degree(acronym, name):
    return {"acronym": acronym, "name": name, "status": "self-reported"}


def test_credential_comparison_leaves_out_academic_degrees():
    designations = [{"code": "CFP", "name": "Certified Financial Planner"}]
    profile = [
        _degree("CFP®", "Certified Financial Planner™"),
        _degree("MBA", "Master of Business Administration"),
        _degree("JD", "Juris Doctor"),
        _degree("MSFP", "Master of Science in Financial Planning"),
        _degree("MS in Business Administration", "Master of Science in Business Administration"),
        _degree("Ph.D.", None),
        _degree("Series 65", "Series 65 - Uniform Investment Adviser Law Exam"),
    ]
    assert fmt.credential_sources_differ(designations, profile) is False
    assert fmt.credential_sources_differ(designations, "CFP, MBA, MS, BA, BS, BBA, PhD, J.D.") is False
    # BFA (Behavioral Financial Advisor) is a designation, not a degree.
    assert fmt.credential_sources_differ(designations, "CFP, BFA") is True
    # "Master Registered Financial Consultant" (MRFC) is a designation, not a degree.
    assert fmt.credential_sources_differ(
        designations, [*profile, _degree("MRFC®", "Master Registered Financial Consultant®")]
    ) is True


def test_designations_coverage_gap():
    designations = [{"code": "CFP", "name": "Certified Financial Planner"}]
    assert fmt.designations_coverage_gap([], _creds("CFP®", "Series 7")) is True
    assert fmt.designations_coverage_gap([], "CFP") is True
    assert fmt.designations_coverage_gap(designations, _creds("CFA®")) is False
    # Only exam licenses or degrees on the profile: no designation to explain.
    assert fmt.designations_coverage_gap([], _creds("Series 65")) is False
    assert fmt.designations_coverage_gap([], [_degree("MBA", "Master of Business Administration")]) is False
    assert fmt.designations_coverage_gap([], None) is False


def test_degree_suffix_that_is_an_own_credential_is_still_stripped_from_name():
    creds = [_degree("CFP®", "Certified Financial Planner™"), _degree("MBA", "Master of Business Administration")]
    assert fmt.marketplace_display_name("Erik Baskin, CFP®, MBA", creds) == "Erik Baskin"


def test_designation_source_labels():
    assert fmt.designation_source_label("part2b") == "Form ADV Part 2B brochure supplement"
    assert fmt.designation_source_label("website_title") == "firm website"
    assert fmt.designation_source_label("something_else") == "something_else"
    assert fmt.designation_source_label(None) is None


def test_official_search_home_urls():
    assert fmt.official_search_home_urls() == {
        "iapd": "https://adviserinfo.sec.gov/",
        "brokercheck": "https://brokercheck.finra.org/",
    }
