#!/usr/bin/env python3
"""
make_fixture_source.py — Build a small `firms.db`-shaped SQLite source file that
exercises every tool path in advisorfinder_mcp, for feeding into the REAL
export script (build_mcp_public_db.py from the firm-intelligence-worktrees/mcp-export
repo) to produce tests/fixtures/mcp_public.db.

Source table schema is copied VERBATIM (structure only, not data) from that repo's
tests/conftest.py::_SCHEMA_SQL, which itself mirrors schema-current.md. This script
does NOT import anything from that repo — it is a standalone fixture generator.

Usage:
    python3 tests/fixtures/make_fixture_source.py /path/to/fixture_source.db

Design notes (why the population looks the way it does):
- ind_source_id values are numeric strings (e.g. "1000002") because in production
  ind_source_id IS the individual's CRD number — it's what iapd_individual_url()
  interpolates and what check_advisor's "all digits -> CRD" branch keys on. Using
  non-numeric ids like "IND001" (as the export-script's own test fixture does)
  would make the CRD-lookup path in check_advisor untestable.
- Four disclosure states are covered by construction, per the CORRECTED
  contract (keyed on has_disclosure ONLY — iar_details row presence only
  distinguishes the two Y sub-states; see format.disclosure_status()):
    1000001 JANE SMITH        has_disclosure='N', row exists            -> none_reported
    1000002 JOHN SMITH        has_disclosure='Y', row, count=3          -> disclosed_with_detail
    1000003 MARY JONES        has_disclosure='Y', row, count=0          -> disclosed_no_detail
    1000004 SAM O'HEARN       has_disclosure='Y', NO iar_details row    -> disclosed_no_detail
    1000005 PATRICK MCDONALD III  has_disclosure='N', row exists        -> none_reported (2 firms)
    1000006 ALEX NG           has_disclosure='N', row exists            -> none_reported
    1000007 CASEY UNKNOWNFLAG has_disclosure=NULL, NO iar_details row   -> unknown
    1000008 MORGAN NOROW      has_disclosure='N', NO iar_details row    -> none_reported
    1000009 JOSÉ GARCÍA       has_disclosure='N', row exists            -> none_reported
  JANE SMITH and JOHN SMITH share last name SMITH -> ambiguous check_advisor("smith").
  1000009 JOSÉ GARCÍA carries accented Latin characters specifically to exercise
  fts_query()'s diacritic-folding: the advisor_fts index is built with
  tokenize='unicode61 remove_diacritics 2' (build_mcp_public_db.py), which folds
  "José García" to plain-ASCII "jose"/"garcia" tokens for matching purposes —
  search_advisors(name="jose garcia") (no accents, as a consumer would type it)
  must still find this row.
  1000004/1000007/1000008 all lack an iar_details row, pinning all three ways
  that absence interacts with has_disclosure: 'Y' -> disclosed_no_detail (NOT
  unknown — a known disclosure must never soften just because we lack detail);
  'N' -> none_reported (row existence is irrelevant once the flag says N);
  NULL/missing -> unknown (the only case row-absence combines with a genuinely
  unresolved flag). An earlier version of this fixture omitted 1000008 and
  used the OLD contract's ordering (row-absence checked before the flag),
  which contradicted the export script's own aggregate tally (hd=='N' counts
  as none_reported unconditionally); the coordinator's spec correction
  resolved that contradiction by keying strictly off has_disclosure.

Task 3 (marketplace-layer) note: the optional marketplace_advisors overlay
(built by build_mcp_public_db.py's --marketplace flag) is fed by a SEPARATE
sibling generator, tests/fixtures/make_fixture_marketplace.py, not by this
file -- see its docstring. Its three marketplace rows reference existing
ind_source_id/crd values from THIS file's ia_reps population (1000002,
1000003, 1000005), which is why no structural change was needed here: the
marketplace crd cross-check (sanitize_marketplace.sanitize()'s known_crds
set) reads src.ia_reps.ind_source_id from whatever source db this file
builds, and 1000002/1000003/1000005 already exist in it.
"""
import json
import sqlite3
import sys
from pathlib import Path

_SCHEMA_SQL = """
CREATE TABLE firms (
    crd_number TEXT PRIMARY KEY, sec_number TEXT, cik_number TEXT, sec_region TEXT,
    firm_type TEXT, primary_name TEXT, legal_name TEXT, website TEXT, phone TEXT, fax TEXT,
    address_street1 TEXT, address_street2 TEXT, address_city TEXT, address_state TEXT,
    address_country TEXT, address_zip TEXT, address_private_res TEXT, num_other_offices INTEGER,
    entity_type TEXT, fiscal_year_end TEXT, state_of_incorporation TEXT, sec_status TEXT,
    sec_status_date TEXT, latest_adv_date TEXT, has_website TEXT, umbrella_registration TEXT,
    total_employees INTEGER, registered_reps INTEGER, investment_adviser_reps INTEGER,
    supervised_persons INTEGER, non_clerical_employees INTEGER, clerical_employees INTEGER,
    independent_contractors INTEGER, num_clients INTEGER, num_clients_flag TEXT,
    aum_discretionary REAL, aum_non_discretionary REAL, aum_total REAL,
    accounts_discretionary INTEGER, accounts_non_discretionary INTEGER, accounts_total INTEGER,
    foreign_aum REAL, clients_individuals_non_hnw INTEGER, aum_individuals_non_hnw REAL,
    clients_individuals_hnw INTEGER, aum_individuals_hnw REAL, clients_investment_companies INTEGER,
    aum_investment_companies REAL, clients_bdc INTEGER, aum_bdc REAL,
    clients_pooled_vehicles INTEGER, aum_pooled_vehicles REAL, clients_pension INTEGER,
    aum_pension REAL, clients_charitable INTEGER, aum_charitable REAL,
    clients_state_municipal INTEGER, aum_state_municipal REAL, clients_other_advisers INTEGER,
    aum_other_advisers REAL, clients_insurance INTEGER, aum_insurance REAL,
    clients_sovereign INTEGER, aum_sovereign REAL, clients_corporations INTEGER,
    aum_corporations REAL, clients_other INTEGER, aum_other REAL,
    fee_pct_of_aum TEXT, fee_hourly TEXT, fee_subscription TEXT, fee_fixed TEXT,
    fee_commissions TEXT, fee_performance_based TEXT, fee_other TEXT, fee_other_desc TEXT,
    serves_individuals TEXT, serves_hnw TEXT, serves_investment_companies TEXT,
    serves_pooled_vehicles TEXT, serves_pension TEXT, serves_charitable TEXT,
    serves_state_municipal TEXT, serves_insurance TEXT, serves_sovereign TEXT,
    serves_corporations TEXT, serves_other TEXT, count_ia_affiliates INTEGER,
    count_bd_affiliates INTEGER, has_private_funds TEXT, any_hedge_funds TEXT,
    any_pe_funds TEXT, any_vc_funds TEXT, any_real_estate_funds TEXT,
    total_private_fund_assets REAL, any_disciplinary TEXT, state_registrations TEXT,
    pct_hnw_aum REAL, pct_hnw_clients REAL, website_clean TEXT, canonical_crd TEXT,
    is_us_based INTEGER, avg_account_size REAL, aum_per_rep REAL, clients_per_rep REAL,
    is_live INTEGER, http_status INTEGER
);

CREATE TABLE firms_state (
    crd_number TEXT PRIMARY KEY, primary_name TEXT, legal_name TEXT, address_street1 TEXT,
    address_city TEXT, address_state TEXT, address_country TEXT, address_zip TEXT, phone TEXT,
    states_approved TEXT, num_states_approved INTEGER, latest_filing_date TEXT,
    total_employees INTEGER, aum_total REAL, feed_date TEXT
);

CREATE TABLE firm_locations (
    id INTEGER PRIMARY KEY, crd_number TEXT, filing_id TEXT, filing_date TEXT,
    location_type TEXT, branch_number TEXT, street1 TEXT, street2 TEXT, city TEXT, state TEXT,
    country TEXT, zip TEXT, phone TEXT, fax TEXT, employees TEXT, private_res TEXT
);

CREATE TABLE firm_other_names (
    crd_number TEXT NOT NULL, other_name TEXT NOT NULL, states TEXT, filing_id INTEGER,
    filing_date TEXT, source TEXT, PRIMARY KEY (crd_number, other_name)
);

CREATE TABLE ia_reps (
    ind_source_id TEXT PRIMARY KEY, first_name TEXT, middle_name TEXT, last_name TEXT,
    ia_scope TEXT, has_disclosure TEXT, crd_numbers TEXT, firm_names TEXT,
    branch_states TEXT, fetched_date TEXT
);

CREATE TABLE ia_rep_firms (
    ind_source_id TEXT NOT NULL, crd_number TEXT NOT NULL, firm_name TEXT,
    branch_city TEXT, branch_state TEXT, ia_only TEXT,
    PRIMARY KEY (ind_source_id, crd_number)
);

CREATE TABLE iar_details (
    ind_source_id TEXT PRIMARY KEY, first_name TEXT, middle_name TEXT, last_name TEXT,
    full_name TEXT, ia_scope TEXT, bc_scope TEXT, industry_start_date TEXT,
    has_disclosure TEXT, ia_has_disclosure TEXT, current_firm_id TEXT, current_firm_name TEXT,
    current_firm_since TEXT, current_branch_city TEXT, current_branch_state TEXT,
    prev_employment_count INTEGER, state_exams TEXT, product_exams TEXT, principal_exams TEXT,
    registered_states TEXT, disclosure_count INTEGER, ia_disclosure_count INTEGER,
    raw_current_employments TEXT, raw_previous_employments TEXT, raw_disclosures TEXT,
    fetched_at TEXT
);

CREATE TABLE advisor_content (
    ind_source_id TEXT PRIMARY KEY, bio TEXT, headshot_url TEXT, specializations TEXT,
    title_clean TEXT, source TEXT, source_url TEXT, scraped_at TEXT, needs_review INTEGER,
    updated_at TEXT, match_method TEXT
);

CREATE TABLE advisor_designations (
    id INTEGER PRIMARY KEY, ind_source_id TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
    issuing_body TEXT, verified INTEGER, source TEXT, as_of TEXT, created_at TEXT
);

CREATE TABLE firm_content (
    crd_number TEXT PRIMARY KEY, marketing_tagline TEXT, firm_bio TEXT, year_founded INTEGER,
    clientele_description TEXT, services_description TEXT, compensation_narrative TEXT,
    expertise_tags TEXT, custodians TEXT, finra_member INTEGER, source TEXT, source_url TEXT,
    scraped_at TEXT, needs_review INTEGER, updated_at TEXT, conflicts_narrative TEXT
);

CREATE TABLE firm_part2a (
    crd_number TEXT PRIMARY KEY, brochure_version_id INTEGER, brochure_name TEXT,
    brochure_date TEXT, fee_rate_blended REAL, fee_rate_is_estimate INTEGER,
    fee_tiers_json TEXT, fee_notes TEXT, account_minimum INTEGER,
    account_minimum_waivable INTEGER, designations_json TEXT, extraction_model TEXT,
    extraction_date TEXT, needs_review INTEGER, review_reason TEXT, raw_item5_text TEXT,
    raw_item7_text TEXT, raw_item10_text TEXT, raw_item11_text TEXT, raw_item14_text TEXT,
    conflicts_captured INTEGER
);

CREATE TABLE ingest_meta (key TEXT PRIMARY KEY, value TEXT);
"""

# ── realistic raw employment JSON for the "current + previous" advisor (1000002) ──

CURRENT_EMPLOYMENT_JSON = json.dumps([
    {
        "firmId": 100001, "firmName": "ALPHA WEALTH LLC", "iaOnly": "Y",
        "registrationBeginDate": "3/1/2018", "firmBCScope": "NOTINSCOPE", "firmIAScope": "ACTIVE",
        "branchOfficeLocations": [
            {
                "displayOrder": 1, "locatedAtFlag": "Y", "supervisedFromFlag": "N",
                "privateResidenceFlag": "N", "street1": "1 ALPHA WAY",
                "city": "NEW YORK", "state": "NY", "country": "United States",
            }
        ],
    }
])

PREVIOUS_EMPLOYMENT_JSON = json.dumps([
    {
        "firmId": 700099, "firmName": "OLD LEGACY BROKERAGE LLC",
        "city": "JERSEY CITY", "state": "NJ", "country": "United States",
        "registrationBeginDate": "6/1/2012", "registrationEndDate": "2/15/2018",
        "firmBCScope": "ACTIVE", "firmIAScope": "ACTIVE",
    }
])

STATE_EXAMS = json.dumps([{"name": "Series 63", "date": "2011-05-01", "scope": "State"}])
PRODUCT_EXAMS = json.dumps([{"name": "Series 65", "date": "2011-06-15", "scope": "Product"}])
PRINCIPAL_EXAMS = json.dumps([])


def _insert(conn, table, **kwargs):
    cols = list(kwargs.keys())
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        list(kwargs.values()),
    )


def build_source_db(db_path: Path) -> Path:
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA_SQL)

    # ── firms (3 SEC-registered, publishable) ──────────────────────────────
    _insert(conn, "firms", crd_number="100001", sec_number="801-100001",
            primary_name="ALPHA WEALTH LLC", legal_name=None,
            website="http://alphawealth.com", website_clean="alphawealth.com",
            phone="2125551000", address_street1="1 ALPHA WAY", address_city="NEW YORK",
            address_state="NY", address_country="United States", address_zip="10001",
            num_other_offices=1, entity_type="LLC", state_of_incorporation="DE",
            sec_status="Approved", sec_status_date="2020-01-01", latest_adv_date="2026-01-01",
            total_employees=12, registered_reps=5, investment_adviser_reps=5,
            supervised_persons=12, num_clients=150, num_clients_flag="Y", accounts_total=170,
            serves_individuals="Y", serves_hnw="Y", serves_investment_companies="N",
            serves_pooled_vehicles="N", serves_pension="N", serves_charitable="N",
            serves_state_municipal="N", serves_insurance="N", serves_sovereign="N",
            serves_corporations="N", serves_other="N",
            fee_pct_of_aum="Y", fee_hourly="N", fee_subscription="N", fee_fixed="N",
            fee_commissions="N", fee_performance_based="N", fee_other="N",
            count_ia_affiliates=0, count_bd_affiliates=0,
            has_private_funds="N", any_hedge_funds="N", any_pe_funds="N", any_vc_funds="N",
            any_real_estate_funds="N", any_disciplinary="N", state_registrations="NY,FL",
            canonical_crd=None, is_us_based=1, aum_total=None, is_live=1, http_status=200)

    _insert(conn, "firms", crd_number="100002", sec_number="801-100002",
            primary_name="BETA ADVISORS INC", legal_name="BETA ADVISORS INCORPORATED",
            website_clean="betaadvisors.com", phone="6175552000",
            address_street1="2 BETA BLVD", address_city="BOSTON", address_state="MA",
            address_country="United States", address_zip="02108",
            num_other_offices=0, entity_type="Corporation", state_of_incorporation="MA",
            sec_status="Approved", sec_status_date="2019-01-01", latest_adv_date="2026-01-01",
            total_employees=4, registered_reps=2, investment_adviser_reps=2,
            supervised_persons=4, num_clients=40, num_clients_flag="Y", accounts_total=45,
            serves_individuals="Y", serves_hnw="N", serves_investment_companies="N",
            serves_pooled_vehicles="N", serves_pension="N", serves_charitable="N",
            serves_state_municipal="N", serves_insurance="N", serves_sovereign="N",
            serves_corporations="N", serves_other="N",
            fee_pct_of_aum="Y", fee_hourly="Y", fee_subscription="N", fee_fixed="N",
            fee_commissions="N", fee_performance_based="N", fee_other="N",
            count_ia_affiliates=0, count_bd_affiliates=0,
            has_private_funds="N", any_hedge_funds="N", any_pe_funds="N", any_vc_funds="N",
            any_real_estate_funds="N", any_disciplinary="N", state_registrations="MA",
            canonical_crd=None, is_us_based=1, aum_total=50_000_000, is_live=1, http_status=200)

    _insert(conn, "firms", crd_number="100003", sec_number="801-100003",
            primary_name="GAMMA CAPITAL MANAGEMENT", legal_name=None,
            website_clean="gammacapital.com", phone="3105553000",
            address_street1="3 GAMMA AVE", address_city="LOS ANGELES", address_state="CA",
            address_country="United States", address_zip="90001",
            num_other_offices=0, entity_type="LLC", state_of_incorporation="CA",
            sec_status="Approved", sec_status_date="2018-01-01", latest_adv_date="2026-01-01",
            total_employees=8, registered_reps=5, investment_adviser_reps=5,
            supervised_persons=8, num_clients=60, num_clients_flag="Y", accounts_total=70,
            serves_individuals="Y", serves_hnw="Y", serves_investment_companies="N",
            serves_pooled_vehicles="N", serves_pension="N", serves_charitable="N",
            serves_state_municipal="N", serves_insurance="N", serves_sovereign="N",
            serves_corporations="N", serves_other="N",
            fee_pct_of_aum="Y", fee_hourly="N", fee_subscription="N", fee_fixed="N",
            fee_commissions="N", fee_performance_based="N", fee_other="N",
            count_ia_affiliates=0, count_bd_affiliates=0,
            has_private_funds="N", any_hedge_funds="N", any_pe_funds="N", any_vc_funds="N",
            any_real_estate_funds="N", any_disciplinary="N", state_registrations="CA",
            canonical_crd=None, is_us_based=1, aum_total=200_000_000, is_live=1, http_status=200)

    # ── firms_state: 1 state-only firm (never in `firms`) ──────────────────
    _insert(conn, "firms_state", crd_number="500001", primary_name="DELTA STATE ADVISERS",
            address_street1="9 STATE ST", address_city="ALBANY", address_state="NY",
            address_country="United States", address_zip="12207", phone="5185551234",
            states_approved="NY,NJ", num_states_approved=2, latest_filing_date="2026-01-01",
            total_employees=3, aum_total=5_000_000_000, feed_date="2026-05-01")

    # ── firm_locations: normal branch (100001) + private residence, null street (100002) ──
    _insert(conn, "firm_locations", crd_number="100001", location_type="Branch",
            branch_number="001", street1="2 BRANCH RD", city="BROOKLYN", state="NY",
            country="United States", zip="11201", phone="7185551111", private_res="N")
    _insert(conn, "firm_locations", crd_number="100002", location_type="Private Residence",
            branch_number="001", street1=None, city=None, state="MA",
            country="United States", zip=None, phone=None, private_res="Y")

    # ── firm_other_names ─────────────────────────────────────────────────────
    _insert(conn, "firm_other_names", crd_number="100001",
            other_name="ALPHA WEALTH MANAGEMENT LLC", states="NY",
            filing_date="2019-01-01", source="SEC")
    # firm 100002's ONLY other-name match target for the exclusive "matched_as" test —
    # searching "gateway" must hit ONLY this row (kind='other'), not primary_name.
    _insert(conn, "firm_other_names", crd_number="100002",
            other_name="GATEWAY CAPITAL PARTNERS", states="MA",
            filing_date="2018-01-01", source="SEC")

    # ── firm_content: needs_review on 100001 -> "unverified" caveat ─────────
    _insert(conn, "firm_content", crd_number="100001",
            marketing_tagline="Wealth, simplified.",
            firm_bio="Alpha Wealth is a boutique RIA serving HNW families.",
            year_founded=2010, clientele_description="HNW individuals",
            services_description="Financial planning, portfolio management",
            compensation_narrative="Fee-only", expertise_tags='["retirement","tax"]',
            custodians='["Schwab"]', finra_member=0, source="website",
            source_url="http://alphawealth.com/about", scraped_at="2026-01-01",
            needs_review=1, updated_at="2026-01-02",
            conflicts_narrative="Internal note — never exported.")

    # ── firm_part2a: 100001 has an estimated fee rate + tiers ────────────────
    _insert(conn, "firm_part2a", crd_number="100001", brochure_version_id=1,
            brochure_name="ADV Part 2A Brochure", brochure_date="2025-03-01",
            fee_rate_blended=0.0125, fee_rate_is_estimate=1,
            fee_tiers_json='[{"tier_label":"first $1M","min_aum":0,"max_aum":1000000,'
                            '"annual_rate_decimal":0.0125},'
                            '{"tier_label":"next $4M","min_aum":1000000,"max_aum":5000000,'
                            '"annual_rate_decimal":0.01}]',
            fee_notes="Estimated from brochure Item 5 narrative fee description.",
            account_minimum=250000, account_minimum_waivable=1,
            designations_json="[]", extraction_model="gpt", extraction_date="2025-03-02",
            needs_review=0, review_reason=None,
            raw_item5_text="Raw Item 5 fee text — legal-gated, never publishable.",
            conflicts_captured=1)

    # ── ia_reps: 6 advisors, four disclosure states, ALL-CAPS names ─────────
    _insert(conn, "ia_reps", ind_source_id="1000001", first_name="JANE", last_name="SMITH",
            ia_scope="Active", has_disclosure="N", branch_states='["NY"]',
            fetched_date="2026-05-20")
    _insert(conn, "ia_reps", ind_source_id="1000002", first_name="JOHN", middle_name="Q",
            last_name="SMITH", ia_scope="Active", has_disclosure="Y", branch_states='["NY"]',
            fetched_date="2026-05-20")
    _insert(conn, "ia_reps", ind_source_id="1000003", first_name="MARY", last_name="JONES",
            ia_scope="Active", has_disclosure="Y", branch_states='["MA"]',
            fetched_date="2026-05-20")
    _insert(conn, "ia_reps", ind_source_id="1000004", first_name="SAM", last_name="O'HEARN",
            ia_scope="Active", has_disclosure="Y", branch_states='["NY"]',
            fetched_date="2026-05-20")
    _insert(conn, "ia_reps", ind_source_id="1000005", first_name="PATRICK",
            last_name="MCDONALD III", ia_scope="Active", has_disclosure="N",
            branch_states='["NY","MA"]', fetched_date="2026-05-20")
    _insert(conn, "ia_reps", ind_source_id="1000006", first_name="ALEX", last_name="NG",
            ia_scope="Active", has_disclosure="N", branch_states='["CA"]',
            fetched_date="2026-05-20")
    # 1000007: has_disclosure NULL/missing, no iar_details row -> pins 'unknown'
    # (the only state where row-absence combines with a genuinely unresolved flag).
    _insert(conn, "ia_reps", ind_source_id="1000007", first_name="CASEY",
            last_name="UNKNOWNFLAG", ia_scope="Active", has_disclosure=None,
            branch_states='["NY"]', fetched_date="2026-05-20")
    # 1000008: has_disclosure='N', no iar_details row -> pins none_reported even
    # without a detail row (row existence is irrelevant once the flag says N).
    _insert(conn, "ia_reps", ind_source_id="1000008", first_name="MORGAN",
            last_name="NOROW", ia_scope="Active", has_disclosure="N",
            branch_states='["NY"]', fetched_date="2026-05-20")
    # 1000009: accented-name advisor (José García) -> exercises fts_query()'s
    # diacritic-folding against the export's remove_diacritics=2 FTS index.
    # has_disclosure='N', row exists -> none_reported (an ordinary state, so
    # this advisor tests ONLY the name-search/unicode path, nothing else).
    _insert(conn, "ia_reps", ind_source_id="1000009", first_name="JOSÉ",
            last_name="GARCÍA", ia_scope="Active", has_disclosure="N",
            branch_states='["NY"]', fetched_date="2026-05-20")

    # ── ia_rep_firms: firm 100001 roster + firm 100002 roster; 1000005 at both;
    #    firm 100003 has NO rows at all -> empty-roster caveat vs its declared
    #    investment_adviser_reps=5 above ──
    _insert(conn, "ia_rep_firms", ind_source_id="1000001", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000002", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000004", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000005", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000006", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000007", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000008", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000009", crd_number="100001",
            firm_name="ALPHA WEALTH LLC", branch_city="NEW YORK", branch_state="NY",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000003", crd_number="100002",
            firm_name="BETA ADVISORS INC", branch_city="BOSTON", branch_state="MA",
            ia_only="Y")
    _insert(conn, "ia_rep_firms", ind_source_id="1000005", crd_number="100002",
            firm_name="BETA ADVISORS INC", branch_city="BOSTON", branch_state="MA",
            ia_only="Y")

    # ── iar_details: covers with_detail / no_detail / none_reported states.
    #    1000004/1000007/1000008 deliberately have NO row at all (see below) —
    #    they pin, respectively, disclosed_no_detail / unknown / none_reported. ──
    _insert(conn, "iar_details", ind_source_id="1000001", ia_scope="Active",
            industry_start_date="not-a-real-date",  # exercises defensive-parse -> None
            has_disclosure="N", ia_has_disclosure="N", current_firm_id="100001",
            current_firm_name="ALPHA WEALTH LLC", current_firm_since="2015-01-01",
            current_branch_city="NEW YORK", current_branch_state="NY",
            prev_employment_count=0, state_exams="[]", product_exams="[]",
            principal_exams="[]", registered_states="NY", disclosure_count=0,
            ia_disclosure_count=0, raw_current_employments=None,
            raw_previous_employments=None, raw_disclosures=None, fetched_at="2026-05-20")
    _insert(conn, "iar_details", ind_source_id="1000002", ia_scope="Active",
            industry_start_date="2011-06-15", has_disclosure="Y", ia_has_disclosure="Y",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2018-03-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=1,
            state_exams=STATE_EXAMS, product_exams=PRODUCT_EXAMS,
            principal_exams=PRINCIPAL_EXAMS, registered_states="NY,FL",
            disclosure_count=3, ia_disclosure_count=3,
            raw_current_employments=CURRENT_EMPLOYMENT_JSON,
            raw_previous_employments=PREVIOUS_EMPLOYMENT_JSON,
            raw_disclosures='[{"eventDate":"2020-01-01"}]', fetched_at="2026-05-20")
    _insert(conn, "iar_details", ind_source_id="1000003", ia_scope="Active",
            industry_start_date="1/15/2012",  # M/D/YYYY -> exercises the second parse tier
            has_disclosure="Y", ia_has_disclosure="Y", current_firm_id="100002",
            current_firm_name="BETA ADVISORS INC", current_firm_since="2012-01-15",
            current_branch_city="BOSTON", current_branch_state="MA",
            prev_employment_count=0, state_exams="[]", product_exams="[]",
            principal_exams="[]", registered_states="MA", disclosure_count=0,
            ia_disclosure_count=0, raw_current_employments=None,
            raw_previous_employments=None, raw_disclosures=None, fetched_at="2026-05-20")
    _insert(conn, "iar_details", ind_source_id="1000005", ia_scope="Active",
            industry_start_date="2016-04-01", has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2016-04-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=0, state_exams="[]",
            product_exams="[]", principal_exams="[]", registered_states="NY,MA",
            disclosure_count=0, ia_disclosure_count=0, raw_current_employments=None,
            raw_previous_employments=None, raw_disclosures=None, fetched_at="2026-05-20")
    _insert(conn, "iar_details", ind_source_id="1000006", ia_scope="Active",
            industry_start_date="2019-09-01", has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2019-09-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=0, state_exams="[]",
            product_exams="[]", principal_exams="[]", registered_states="CA",
            disclosure_count=0, ia_disclosure_count=0, raw_current_employments=None,
            raw_previous_employments=None, raw_disclosures=None, fetched_at="2026-05-20")
    _insert(conn, "iar_details", ind_source_id="1000009", ia_scope="Active",
            industry_start_date="2017-01-01", has_disclosure="N", ia_has_disclosure="N",
            current_firm_id="100001", current_firm_name="ALPHA WEALTH LLC",
            current_firm_since="2017-01-01", current_branch_city="NEW YORK",
            current_branch_state="NY", prev_employment_count=0, state_exams="[]",
            product_exams="[]", principal_exams="[]", registered_states="NY",
            disclosure_count=0, ia_disclosure_count=0, raw_current_employments=None,
            raw_previous_employments=None, raw_disclosures=None, fetched_at="2026-05-20")
    # 1000004 (O'HEARN), 1000007 (UNKNOWNFLAG), 1000008 (NOROW): NO iar_details
    # row inserted for any of them, deliberately.

    # ── advisor_content: 'provided' (1000002) vs 'name_unique' (1000003) ────
    _insert(conn, "advisor_content", ind_source_id="1000002",
            bio="Experienced wealth advisor specializing in retirement planning.",
            headshot_url=None, specializations='["Retirement Planning"]',
            title_clean="Senior Financial Advisor", source="website",
            source_url="http://alphawealth.com/team/john", scraped_at="2026-01-01",
            needs_review=0, updated_at="2026-01-02", match_method="provided")
    _insert(conn, "advisor_content", ind_source_id="1000003",
            bio="Financial advisor bio matched by name within the firm roster.",
            headshot_url=None, specializations='["Tax Planning"]',
            title_clean="Advisor", source="website",
            source_url="http://betaadvisors.com/team/mary", scraped_at="2026-01-01",
            needs_review=0, updated_at="2026-01-02", match_method="name_unique")

    # ── advisor_designations: verified=0 (self-reported, per brief) ──────────
    _insert(conn, "advisor_designations", id=1, ind_source_id="1000002", code="CFP",
            name="Certified Financial Planner", issuing_body="CFP Board", verified=0,
            source="cfp_board", as_of="2025-01-01", created_at="2025-01-02")

    # ── ingest_meta: all four *_as_of keys required by export_meta ───────────
    for k, v in {
        "source_file": "/fixtures/fixture_source.csv", "total_firms": "3", "skipped": "0",
        "ingest_date": "2026-05-18 09:15:19", "individuals_as_of": "2024-12-31",
        "firms_as_of": "2026-05-01", "website_check_as_of": "2026-05-19",
        "ia_reps_as_of": "2026-05-20", "ia_reps_count": "9",
    }.items():
        _insert(conn, "ingest_meta", key=k, value=v)

    conn.commit()
    conn.close()
    return db_path


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: make_fixture_source.py <output-db-path>", file=sys.stderr)
        return 2
    build_source_db(Path(argv[0]))
    print(f"OK: fixture source DB written to {argv[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
