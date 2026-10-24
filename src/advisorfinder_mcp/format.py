"""Formatting/wording helpers shared by server.py's tools.

This is the ONE place site links, disclosure phrasing, and fee phrasing come
from — future changes to advisorfinder.com deep links, or to how we phrase
disclosure/fee caveats, only ever touch this file.
"""
import json
import math
import re
import unicodedata
import urllib.parse
from datetime import date, datetime
from typing import Any

import ftfy


def advisorfinder_url() -> str:
    return "https://advisorfinder.com"


def advisorfinder_profile_url(row_or_none: Any) -> str:
    """The deep link envelope() uses for its top-level advisorfinder.link:
    a marketplace member's own AdvisorFinder profile URL when `row_or_none`
    is a marketplace_advisors row (or any row-like object with a truthy
    `profile_url` column/key), else the generic advisorfinder.com homepage.
    Works with dict, sqlite3.Row, or None (via _row_value, defined below)."""
    url = normalize_url(_row_value(row_or_none, "profile_url"))
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

# Initialisms kept uppercase in firm names ("LPL Financial", not "Lpl
# Financial"). Matched against a WHOLE token (or one hyphen/slash/comma-
# separated part of a token) with surrounding punctuation removed, never as a
# substring, so "Rice" or "Tdx" are never touched. Only tokens that are not
# ordinary English words or common surnames belong here; "Inc."/"Ltd"/"Corp."
# keep their title case on purpose. Two entries have a context rule in
# _firm_token_case: ICE (the exchange operator, "ICE Data Pricing") only as
# the first token of the name; US not after a word that makes it the pronoun
# ("What About Us Financial").
_FIRM_INITIALISMS = frozenset({
    "AAM", "AE", "AIP", "ALM", "AQR", "ASB", "AST", "AXA", "BB&T", "BBR",
    "BBVA", "BD", "BDC", "BDT", "BFC", "BFG", "BMO", "BNP", "BNY", "BOK",
    "BTG", "CBIZ", "CFI", "CFP", "CG", "CIBC", "CIFC", "CPA", "CREF", "CVC",
    "CW", "CWA", "CWM", "CX", "DBX", "DLJ", "DWS", "ECP", "EFS", "EP", "ESI",
    "ETF", "FBL", "FIAM", "GC", "GCM", "GP", "GTCR", "GW&K", "GWN", "HB",
    "HBK", "HBKS", "HBW", "HK", "HPS", "HSBC", "ICE", "IEQ", "IFG", "IFM",
    "IFP", "IHT", "IP", "IPI", "JMG", "KFG", "KKR", "KSL", "LNW", "LPL",
    "LSV", "MBA", "MDT", "MDTA", "MFS", "MMA", "MML", "MWA", "NB", "NBC",
    "NEPC", "NFSG", "NISA", "NLG", "NPA", "NWAM", "NWF", "PFG", "PFS", "PGIM",
    "PKS", "PMC", "PNC", "PPM", "PTMA", "RBC", "RFG", "RMR", "RWA", "SCS",
    "SEI", "SEIA", "SLC", "SPC", "SSGA", "SVB", "TCW", "TD", "TIAA", "TLG",
    "TPG", "UBS", "UK", "US", "USA", "USAA", "WCG", "WCM",
})
# "Us" is the pronoun, not "US", after these words.
_US_PRONOUN_AFTER = frozenset({"ABOUT", "FOR", "WITH", "TO", "OF"})
# Fund-series roman numerals ("Partners VII", "Fund-IX"). title_case_name()
# already uppercases a whole-word II/III/IV; this also covers VI..XII and a
# numeral inside a token ("Sei-II", "(III)"). At the very start of the name
# only a 3-4 letter numeral (VII, VIII) counts: "Vi-Bhakti Ventures" is a
# word. V and X are left alone: as one letter they are also initials
# ("David X Martin").
_ROMAN_NUMERALS = frozenset({"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"})
# A 2-3 letter word that is the whole content of parentheses is an
# abbreviation ("(NY)", "(UK)", "(GP)", "(DBA)"), except these words.
_PAREN_WORDS = frozenset({"THE", "AND", "FOR", "OF", "AN", "LTD", "INC", "CO"})

# A token made only of single letters each followed by a dot ("J.P.",
# "U.S.", "N.A.", "B.o.s.s."), the final dot optional ("A.g.p").
_DOTTED_INITIALS_RE = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]?")
# One or two letters on each side of "&" ("H&H", "L&G", "S&P").
_AMPERSAND_INITIALS_RE = re.compile(r"[A-Za-z]{1,2}&[A-Za-z]{1,2}")
# One letter, "&", "Co" ("R&Co"): a company, not two initials.
_AMPERSAND_CO_RE = re.compile(r"([A-Za-z])&co", re.IGNORECASE)
# Leading / trailing punctuation around a token's core. The trailing class
# has no "." so the dotted-initials rule still sees "J.P.". The leading class
# includes & @ # + and curly quotes so "&PARTNERS" becomes "&Partners".
_TOKEN_SPLIT_RE = re.compile("^([(\\[\"'&@#+\u201c\u2018]*)(.*?)([)\\]\"',;:\u201d\u2019]*)$")
# Separators inside a token's core ("Cpa/Pfs,Cfp", "Sei-II").
_PART_SPLIT_RE = re.compile(r"([-/,])")


def _upper_initialism_part(
    part: str, allow_ice: bool, us_is_pronoun: bool, name_start: bool,
) -> str:
    core = part.rstrip(".")
    upper = core.upper()
    if _AMPERSAND_CO_RE.fullmatch(core):
        return core[0].upper() + "&Co" + part[len(core):]
    if upper == "ICE" and not allow_ice:
        return part
    if upper == "US" and us_is_pronoun:
        return part
    if (upper in _FIRM_INITIALISMS
            or (upper in _ROMAN_NUMERALS and (not name_start or len(upper) > 2))
            or _AMPERSAND_INITIALS_RE.fullmatch(core)):
        return upper + part[len(core):]
    return part


def _firm_token_case(tok: str, first: bool = False, prev: str = "") -> str:
    """One token of title_case_name() output. `first`: it is the name's first
    token. `prev`: the previous token (any case)."""
    if tok.strip(",.").replace(".", "").upper() in _FIRM_ACRONYMS:
        return tok.upper()
    lead, core, trail = _TOKEN_SPLIT_RE.match(tok).groups()
    if not core:
        return tok
    if lead and core[0].islower():
        # capitalize() in title_case_name() saw the quote/parenthesis/"&" as
        # the first character: '"tate' -> '"Tate', '&partners' -> '&Partners'.
        core = core[0].upper() + core[1:]
    if _DOTTED_INITIALS_RE.fullmatch(core.rstrip(".")):
        return lead + core.upper() + trail
    if (lead.endswith("(") and trail.startswith(")") and 2 <= len(core) <= 3
            and core.isalpha() and core.isascii() and core.upper() not in _PAREN_WORDS):
        return lead + core.upper() + trail
    prev_core = _TOKEN_SPLIT_RE.match(prev).group(2).rstrip(".").upper() if prev else ""
    us_is_pronoun = prev_core in _US_PRONOUN_AFTER
    parts = _PART_SPLIT_RE.split(core)
    core = "".join(
        p if i % 2 else _upper_initialism_part(
            p,
            allow_ice=first and len(parts) == 1,
            us_is_pronoun=us_is_pronoun,
            name_start=first and i == 0,
        )
        for i, p in enumerate(parts)
    )
    return lead + core + trail


def title_case_firm_name(s: str | None) -> str | None:
    """title_case_name() plus firm-name rules: legal-entity designators stay
    uppercase (LLC, L.P.), dotted initials stay uppercase (J.P., U.S., N.A.),
    listed initialisms (LPL, UBS, KKR), fund-series roman numerals (VII) and
    a 2-3 letter abbreviation in parentheses ("(NY)") are uppercased, "R&co"
    becomes "R&Co", and the first letter after a leading quote, parenthesis,
    "&", "@", "#" or "+" is capitalized. Idempotent."""
    cased = title_case_name(s)
    if not cased:
        return cased
    toks = cased.split(" ")
    return " ".join(
        _firm_token_case(tok, first=(i == 0), prev=toks[i - 1] if i else "")
        for i, tok in enumerate(toks)
    )


# ── unreadable characters (U+FFFD) in firm data ─────────────────────────────
# Upstream decoded some cp1252 SEC files as UTF-8 with errors='replace', so a
# few firm names/addresses carry U+FFFD where a letter was lost
# ("S\ufffdO PAULO"). The original letter is not recoverable here: the text is
# returned as is (never guessed, never passed through ftfy) and labeled.
REPLACEMENT_CHAR = "\ufffd"

UNREADABLE_CHARS_CAVEAT = (
    "Some characters in this firm's record could not be read from the source "
    "data (shown as \ufffd). See the firm's SEC IAPD record (link in verify) "
    "for the exact spelling."
)

UNREADABLE_CHARS_NOTE = (
    "Some characters could not be read from the source data (shown as \ufffd). "
    "See the SEC IAPD record for the exact spelling."
)


def has_unreadable_chars(*values) -> bool:
    """True when any string in `values` contains U+FFFD."""
    return any(isinstance(v, str) and REPLACEMENT_CHAR in v for v in values)


# ── outbound URLs ────────────────────────────────────────────────────────────

# "name:" at the start of the value. A digit after the colon is a port
# ("localhost:8080"), not a scheme.
_URL_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):(?!\d)")
_SLASH_SPACE_RE = re.compile(r"/\s+")
_ANY_SPACE_RE = re.compile(r"\s")


def normalize_url(value) -> str | None:
    """A link safe to hand to a client, or None.

    Strips surrounding whitespace; empty -> None. Whitespace directly after
    a "/" is removed ("linkedin.com/in/ jane" -> "linkedin.com/in/jane"); a
    value that still contains whitespace is not a working link -> None
    ("https://x.com (primary firm website)"). A value that already has an
    http:// or https:// scheme (any letter case) is returned unchanged. Any
    other scheme ("javascript:", "mailto:", "ftp:") -> None. A value with no
    scheme gets "https://" in front ("linkedin.com/in/x" ->
    "https://linkedin.com/in/x"; "//host/x" -> "https://host/x"). Nothing
    else is changed: no lowercasing, no percent-encoding ("®" stays)."""
    if not isinstance(value, str):
        return None
    url = _SLASH_SPACE_RE.sub("/", value.strip())
    if not url or _ANY_SPACE_RE.search(url):
        return None
    match = _URL_SCHEME_RE.match(url)
    if match:
        return url if match.group(1).lower() in ("http", "https") else None
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url


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


def official_search_home_urls() -> dict:
    """The SEC IAPD and FINRA BrokerCheck search pages, for a search miss
    that has no name to put in a search link."""
    return {
        "iapd": "https://adviserinfo.sec.gov/",
        "brokercheck": "https://brokercheck.finra.org/",
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

FEES_NOT_EXTRACTED_NOTE = (
    "No fee details were extracted from this firm's ADV Part 2A brochure. "
    "The brochure is on the firm's SEC IAPD record (link in verify)."
)


def fee_block(part2a_row: Any) -> dict | None:
    """None if the firm has no firm_part2a row. Never emits a fee number
    without a "basis" field alongside it.

    When no fee value was extracted (no rate, no tiers, no notes, no account
    minimum -- 0 IS a value), basis is "not extracted" with a note instead
    of the "estimated" disclaimer: there is no estimate to qualify. 3,747 of
    14,243 production rows are like this (3,001 of them also have no
    brochure date)."""
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

    rate = _row_value(part2a_row, "fee_rate_blended")
    notes = _row_value(part2a_row, "fee_notes")
    account_minimum = _row_value(part2a_row, "account_minimum")
    if (rate is None and not tiers and not (isinstance(notes, str) and notes.strip())
            and account_minimum is None):
        return {
            "rate": None,
            "tiers": None,
            "notes": None,
            "account_minimum": None,
            "brochure_date": iso_date(_row_value(part2a_row, "brochure_date")),
            "basis": "not extracted",
            "note": FEES_NOT_EXTRACTED_NOTE,
        }

    block = {
        "rate": _row_value(part2a_row, "fee_rate_blended"),
        "tiers": tiers,
        "notes": _row_value(part2a_row, "fee_notes"),
        "account_minimum": _row_value(part2a_row, "account_minimum"),
        "account_minimum_waivable": bool(_row_value(part2a_row, "account_minimum_waivable", 0)),
        "brochure_date": iso_date(_row_value(part2a_row, "brochure_date")),
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
        "profile_url": normalize_url(_row_value(row, "profile_url")),
        "job_title": clean_text(_row_value(row, "jobTitle")),
        "pricing": marketplace_pricing(_row_value(row, "pricing")),
        "note": _MARKETPLACE_NOTE,
    }


# ── marketplace field cleanup (presentation layer only) ──────────────────────
# The marketplace export carries two upstream defects that the server repairs
# on output (no data refresh is possible at schema 4):
#   1. Mojibake: UTF-8 text that was decoded as cp1252 upstream ("â€™" for
#      "’", "CFPÂ®" for "CFP®", "ðŸ‘‹" for "👋"). clean_text() repairs it.
#   2. Excel export artifacts in the prose fields (bio, clientDescription,
#      in-their-own-words answers, education degrees): a literal backslash-n
#      ("\\n", two characters) where a line break was, and "_x000D_" (the
#      escaped carriage return). clean_prose() repairs them.
#   3. DynamoDB attribute-value JSON in credentials/education/pricing/
#      allowedStates/quickFacts ([{"M": {"acronym": {"S": "CFP®"}}}]).
#      unwrap_dynamodb() plus the marketplace_* builders below turn it into
#      plain values.
# Every builder also accepts plain text (the test fixture, and any future
# export that ships clean values) and passes it through unchanged except for
# clean_text().


def clean_text(value):
    """Repairs mojibake in a string; returns anything else unchanged.

    Uses ftfy.fix_encoding ONLY -- not ftfy.fix_text, which also uncurls
    quotes, NFC-normalizes and rewrites control characters, and so would
    change text that is already correct. fix_encoding changes a string only
    when it detects a mis-decoding, so correct ®, ’, é and emoji pass through
    byte-identical (verified on all 294 production marketplace rows)."""
    if not isinstance(value, str) or not value:
        return value
    try:
        return ftfy.fix_encoding(value)
    except Exception:  # never let a text repair break a tool response
        return value


_X000D = "_x000D_"
_LITERAL_NEWLINE = "\\n"  # backslash + "n": two characters, not a newline
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


def clean_prose(value):
    """clean_text() plus removal of the Excel export artifacts in marketplace
    PROSE fields only (bio, clientDescription, in-their-own-words answers,
    education degrees): "_x000D_" is removed, a literal backslash-n becomes a
    real newline, runs of 3+ newlines collapse to 2 (one blank line), and the
    result is stripped. Not used for names, URLs or pricing values, where a
    backslash could be content. Returns anything that is not a string
    unchanged."""
    value = clean_text(value)
    if not isinstance(value, str) or not value:
        return value
    if _X000D not in value and _LITERAL_NEWLINE not in value:
        return value  # no artifact: unchanged (not even stripped)
    value = value.replace(_X000D, "").replace(_LITERAL_NEWLINE, "\n")
    return _EXCESS_NEWLINES_RE.sub("\n\n", value).strip()


_AV_TYPES = frozenset({"S", "N", "BOOL", "NULL", "M", "L", "SS", "NS"})


def _unwrap_av(value):
    if isinstance(value, list):
        return [_unwrap_av(v) for v in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        (tag, inner), = value.items()
        if tag in _AV_TYPES:
            if tag == "S":
                return inner if isinstance(inner, str) else None
            if tag == "N":
                # Kept as the listed string ("0.50" stays "0.50"): no numeric
                # normalization of advisor-entered values.
                if isinstance(inner, bool) or not isinstance(inner, (str, int, float)):
                    return None
                if isinstance(inner, float) and not math.isfinite(inner):
                    return None  # never a "nan"/"inf" string
                return str(inner)
            if tag == "BOOL":
                return inner if isinstance(inner, bool) else None
            if tag == "NULL":
                return None
            if tag == "M":
                return {k: _unwrap_av(v) for k, v in inner.items()} if isinstance(inner, dict) else None
            if tag == "L":
                return [_unwrap_av(v) for v in inner] if isinstance(inner, list) else None
            # SS / NS: sets of plain strings / number-strings.
            if not isinstance(inner, list):
                return None
            return [str(v) for v in inner if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
    return {k: _unwrap_av(v) for k, v in value.items()}


def unwrap_dynamodb(value):
    """Converts DynamoDB attribute-value JSON to plain Python values.

    Accepts a JSON string or an already-parsed value. Handles S, N (kept as a
    string), BOOL, NULL, M, L, SS and NS at any depth; a dict that is not a
    single-key attribute value is treated as plain JSON and its values are
    unwrapped. A string that is not JSON is returned as-is (plain text).
    Never raises: malformed input returns None."""
    try:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped[0] in "[{":
                try:
                    value = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    return value  # looks like JSON but is not: plain text
            else:
                return value
        return _unwrap_av(value)
    except Exception:
        return None


def _text_or_none(value, clean=None) -> str | None:
    """A non-empty, repaired, stripped string, or None. `clean` is the repair
    function (clean_text by default; clean_prose for prose fields). A
    non-finite float (NaN/Infinity from lenient JSON) is None, never "nan"."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = (clean or clean_text)(value).strip()
    return value or None


def _sort_priority(value) -> float:
    """sortingPriority as a finite float; missing, non-numeric, NaN and
    +/-Infinity all sort last (inf), so the order is always defined."""
    if isinstance(value, bool):
        return math.inf
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.inf
    return number if math.isfinite(number) else math.inf


def marketplace_credentials(raw):
    """[{"acronym", "name", "status": "self-reported"}, ...] sorted by the
    profile's sortingPriority, from the marketplace credentials column.

    The generic description/requirements/quickFact copy that rides along with
    each credential is dropped on purpose: it is AdvisorFinder marketing text
    about the credential in general, not a fact about this advisor. Entries
    with neither an acronym nor a name (bare catalog ids) carry nothing a
    consumer can use and are skipped. Plain text is returned as (repaired)
    text; None when absent or unreadable."""
    data = unwrap_dynamodb(raw)
    if isinstance(data, str):
        return _text_or_none(data)
    if not isinstance(data, list):
        return None
    keyed = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            text = _text_or_none(item)
            if text:
                keyed.append((math.inf, index, {"acronym": None, "name": text, "status": "self-reported"}))
            continue
        acronym = _text_or_none(item.get("acronym"))
        name = _text_or_none(item.get("value"))
        if not acronym and not name:
            continue
        priority = _sort_priority(item.get("sortingPriority"))
        keyed.append((priority, index, {"acronym": acronym, "name": name, "status": "self-reported"}))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [entry for _, _, entry in keyed]


_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def marketplace_education(raw):
    """[{"institution", "degree", "year"}, ...] from the marketplace
    education column. `year` is the 4-digit year found in completedDate
    ("Fri May 15 2009 04:00:00 GMT+0000 ..." -> 2009), or None when there is
    no date -- never guessed. Plain text is returned as (repaired) text."""
    data = unwrap_dynamodb(raw)
    if isinstance(data, str):
        return _text_or_none(data)
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        institution = _text_or_none(item.get("institution"))
        degree = _text_or_none(item.get("degreeObtained"), clean_prose)
        if not institution and not degree:
            continue
        completed = item.get("completedDate")
        match = _YEAR_RE.search(completed) if isinstance(completed, str) else None
        out.append({
            "institution": institution,
            "degree": degree,
            "year": int(match.group(0)) if match else None,
        })
    return out


# Marketplace pricing field name -> output key. This is an ALLOWLIST: a field
# not named here is dropped, never passed through. Values are passed through
# as listed (strings); nothing is computed or corrected.
_PRICING_FIELDS = {
    "minRange": "min",
    "maxRange": "max",
    "frequency": "frequency",
    "rate": "rate",
    "cost": "cost",
    "link": "link",
}


_BARE_NUMBER_RE = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")


def marketplace_pricing(raw):
    """[{"type": ..., ...}, ...] from the marketplace pricing column, e.g.
    {"type": "aum", "min": "0.50", "max": "2.00", "unit": "percent of
    assets"}, {"type": "flat_fee", "min": "1000", "max": "2500",
    "frequency": "yearly"}, {"type": "hourly", "rate": "250"}.

    Output keys are an allowlist (type, min, max, unit, frequency, rate,
    cost, link); any other source field is dropped. Values are as listed on
    the advisor's profile: strings, never computed or corrected (min > max
    passes through as listed). Formatting only: type and frequency are
    lowercased, one trailing "%" is stripped from AUM min/max, and empty
    strings plus the profile's "-1" unset-frequency marker are dropped. AUM
    entries get "unit": "percent of assets" ONLY when every present min/max
    is a bare number; otherwise no unit is claimed. Plain text is returned as
    (repaired) text."""
    data = unwrap_dynamodb(raw)
    if isinstance(data, str):
        return _text_or_none(data)
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        type_name = _text_or_none(item.get("typeName"))
        if not type_name:
            continue
        entry = {"type": type_name.lower()}
        is_aum = entry["type"] == "aum"
        for field, value in item.items():
            key = _PRICING_FIELDS.get(field)
            if key is None:
                continue  # allowlist: typeName handled above, unknowns dropped
            text = _text_or_none(value)
            if text is None:
                continue
            if key == "frequency":
                if text == "-1":
                    continue
                text = text.lower()
            if is_aum and key in ("min", "max"):
                if text.endswith("%"):
                    text = text[:-1].strip()  # one trailing "%" only
                if not text:
                    continue
            entry[key] = text
        amounts = [entry[k] for k in ("min", "max") if k in entry]
        if is_aum and amounts and all(_BARE_NUMBER_RE.match(a) for a in amounts):
            entry["unit"] = "percent of assets"
        out.append(entry)
    return out


def marketplace_states(raw) -> list[str] | None:
    """Allowed-state list from the marketplace allowedStates column: a
    DynamoDB/JSON list (["TX", "OK"]) or comma-separated text ("NY,NJ").
    None when the member did not fill in the field (292 of 294 production
    rows): an empty list would read as "serves no states"."""
    data = unwrap_dynamodb(raw)
    if isinstance(data, str):
        states = _split_state_csv(data)
    elif isinstance(data, list):
        states = [t for t in (_text_or_none(v) for v in data) if t]
    else:
        states = []
    return states or None


_FLAG_TRUE = frozenset({"1", "y", "yes", "true"})
_FLAG_FALSE = frozenset({"0", "n", "no", "false"})


def marketplace_flag(raw) -> bool | None:
    """A yes/no profile field (virtualMeetingsOffered: "1" in production,
    "Yes"/"No" in the fixture) as True/False. None when the member did not
    fill it in or the value is not a recognized yes/no -- never guessed."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    value = raw.strip().casefold()
    if value in _FLAG_TRUE:
        return True
    if value in _FLAG_FALSE:
        return False
    return None


# Removed before comparing a credential suffix in a display name with the
# member's own acronyms: "CFP ®", "CFP(r)" and "CFP®" all compare as "CFP".
_CREDENTIAL_MARKS_RE = re.compile(r"[®™©]|\((?:r|tm|c)\)|\s", re.IGNORECASE)


def _credential_key(value) -> str:
    if not isinstance(value, str):
        return ""
    return _CREDENTIAL_MARKS_RE.sub("", value).upper()


def _credential_entries(credentials) -> list[tuple[str, str]]:
    """(normalized acronym, name) pairs from marketplace_credentials()
    output: a list of {"acronym", "name", ...} entries, or plain
    comma-separated text ("CFP, CFA"), which has no names."""
    if isinstance(credentials, str):
        return [(k, "") for k in (_credential_key(p) for p in credentials.split(",")) if k]
    if not isinstance(credentials, list):
        return []
    out = []
    for c in credentials:
        if not isinstance(c, dict):
            continue
        key = _credential_key(c.get("acronym"))
        if key:
            name = c.get("name")
            out.append((key, name if isinstance(name, str) else ""))
    return out


def _own_acronym_keys(credentials) -> set[str]:
    """Normalized acronyms from marketplace_credentials() output (every
    entry, degrees and exam licenses included)."""
    return {key for key, _ in _credential_entries(credentials)}


# Academic degrees are not professional designations, so the credential
# comparison leaves them out (production profiles list MBA, JD, MSFP and
# "MS in Business Administration"). Keys are _credential_key() values with
# dots removed ("Ph.D." -> "PHD"). BFA is left out on purpose: in
# advisor_designations it is the Behavioral Financial Advisor designation.
_DEGREE_KEYS = frozenset({
    "AA", "AS", "BA", "BS", "BBA", "BSBA", "MA", "MS", "MSC", "MBA",
    "MFA", "MPA", "MPH", "MACC", "MST", "MSF", "MSFP", "MSFS", "MED", "JD",
    "LLM", "PHD", "EDD", "DBA",
})
_DEGREE_KEY_PREFIXES = ("MASTER", "BACHELOR", "DOCTOR")
# A degree-letters key followed by "IN..." ("MSINBUSINESSADMINISTRATION").
_DEGREE_IN_RE = re.compile(r"(?:MS|MA|BS|BA|MBA)IN[A-Z]+")
# A degree by its full name; "Master Registered Financial Consultant" (MRFC)
# is a designation, so "Master" alone is not enough.
_DEGREE_NAME_RE = re.compile(r"\s*(?:(?:master|bachelor|doctor)s?\s+of\b|juris\s+doctor\b)", re.IGNORECASE)


def _is_degree(key: str, name: str = "") -> bool:
    bare = key.replace(".", "")
    return (
        bare in _DEGREE_KEYS
        or bare.startswith(_DEGREE_KEY_PREFIXES)
        or bool(_DEGREE_IN_RE.fullmatch(bare))
        or bool(_DEGREE_NAME_RE.match(name or ""))
    )


def _designation_keys(credentials) -> set[str]:
    """The profile acronyms that are professional designations: exam
    licenses ("Series 65" -- regulatory records list them as exams) and
    academic degrees are left out."""
    return {
        key for key, name in _credential_entries(credentials)
        if not key.startswith("SERIES") and not _is_degree(key, name)
    }


def marketplace_display_name(name, credentials):
    """The marketplace displayName without trailing ", <credential>" parts
    ("David Calliott, CFP®" -> "David Calliott"). A part is removed only
    when it matches one of this member's own credential acronyms
    (`credentials` is marketplace_credentials() output), working from the
    right and stopping at the first part that does not match, so a real name
    is never cut: "D. Vance Barse, CPWA®, AIF®" with only AIF® listed keeps
    "CPWA®". The first part is never removed."""
    name = clean_text(name)
    if not isinstance(name, str) or "," not in name:
        return name
    own = _own_acronym_keys(credentials)
    if not own:
        return name
    parts = name.split(",")
    while len(parts) > 1 and _credential_key(parts[-1]) in own:
        parts.pop()
    return ",".join(parts).strip()


# Both lists present and not the same.
CREDENTIAL_SOURCES_NOTE = (
    "The designations on file from Form ADV Part 2B (designations) and the "
    "credentials on the advisor's AdvisorFinder profile "
    "(advisorfinder_listing.self_reported.credentials) are not the same list. "
    "Both are self-reported and not independently verified; a difference is "
    "not by itself a problem. Confirm any credential with the issuing body."
)

# No designations on file, credentials on the profile. Missing public data
# is not a difference between the lists: advisor_designations covers few
# advisors (342 in the 2026-08-28 export, nearly all from Part 2B).
DESIGNATIONS_COVERAGE_NOTE = (
    "No professional designations are on file for this advisor in our public "
    "data. That data comes from Form ADV Part 2B brochure supplements and "
    "covers only a small share of advisors, so an empty list does not mean "
    "this advisor holds no designations. The credentials in "
    "advisorfinder_listing.self_reported come from the advisor's own "
    "AdvisorFinder profile and are not independently verified. Confirm any "
    "credential with the issuing body."
)


def _public_designation_keys(designations) -> set[str]:
    keys = {_credential_key(_row_value(d, "code")) for d in designations or []}
    keys.discard("")
    return {k for k in keys if not _is_degree(k)}


def credential_sources_differ(designations, profile_credentials) -> bool:
    """True when BOTH the public designations and the profile's
    designations (see _designation_keys: no exam licenses, no degrees) are
    present and are not the same set. An empty public list is missing data,
    not a difference (see designations_coverage_gap)."""
    profile = _designation_keys(profile_credentials)
    public = _public_designation_keys(designations)
    return bool(profile) and bool(public) and profile != public


def designations_coverage_gap(designations, profile_credentials) -> bool:
    """True when no designations are on file in the public data but the
    profile lists at least one designation (exam licenses and degrees do not
    count)."""
    return not _public_designation_keys(designations) and bool(_designation_keys(profile_credentials))


# advisor_designations.source -> label. Production holds only these two
# values; any other value is passed through as is.
_DESIGNATION_SOURCE_LABELS = {
    "part2b": "Form ADV Part 2B brochure supplement",
    "website_title": "firm website",
}


def designation_source_label(source):
    """Readable label for an advisor_designations.source value."""
    if not isinstance(source, str) or not source.strip():
        return None
    return _DESIGNATION_SOURCE_LABELS.get(source.strip(), source.strip())


def marketplace_profile_facts(row) -> dict:
    """The member's own credentials and years of experience, labeled as
    self-reported on their AdvisorFinder profile -- for get_advisor's
    advisorfinder_listing, next to the designations from public data."""
    return {
        "credentials": marketplace_credentials(_row_value(row, "credentials")),
        "years_of_experience": _row_value(row, "yearsOfExperience"),
        "label": SELF_REPORTED_LABEL,
    }


def marketplace_text_list(raw) -> list[str]:
    """A JSON list of strings (in_their_own_words), each repaired with
    clean_prose. Non-string and empty items are skipped; [] when absent or
    unreadable."""
    data = unwrap_dynamodb(raw)
    if not isinstance(data, list):
        return []
    return [t for t in (_text_or_none(v, clean_prose) for v in data) if t]


def marketplace_search_text(row) -> str:
    """The casefolded text find_bookable_advisors' `specialty` filter matches
    against: bio, client description, in-their-own-words answers, and
    credential acronyms and names -- all after cleanup, so generic credential
    marketing copy and DynamoDB JSON keys can never produce a match."""
    parts = [
        clean_prose(_row_value(row, "bio")),
        clean_prose(_row_value(row, "clientDescription")),
        *marketplace_text_list(_row_value(row, "in_their_own_words")),
    ]
    creds = marketplace_credentials(_row_value(row, "credentials"))
    if isinstance(creds, str):
        parts.append(creds)
    elif creds:
        for c in creds:
            parts.extend([c["acronym"], c["name"]])
    return "\n".join(p for p in parts if isinstance(p, str) and p).casefold()


# ── marketplace member with no regulatory-dataset match (Gate A2 Ruling 1) ───
# ADJUDICATED 2026-08-09 (Gate A2): some real marketplace members are
# state-registered/BD-side advisors whose CRDs legitimately never appear in
# ia_reps (the exported SEC roster; 98 of 294 members in the 2026-08-28
# export -- export_meta.marketplace_crd_unmatched counts against the
# unscoped source roster and so reports fewer) -- sanitize_marketplace.py ships their row anyway
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


_NAME_SUFFIX_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv"})


def name_rank_tokens(raw: str | None) -> list[str]:
    """Raw (never FTS-quoted/starred) lowercase tokens used to rank advisor
    name matches. Cleans `raw` the way fts_query() does (fold diacritics,
    strip disallowed characters), splits on whitespace, strips leading and
    trailing dots/apostrophes/hyphens from each token, drops empties, then
    drops name-suffix tokens (jr/sr/ii/iii/iv) so "John Smith Jr." ranks as
    "john smith". Pure; returns [] when nothing usable remains."""
    if not raw:
        return []
    cleaned = _DISALLOWED_RE.sub(" ", _fold_diacritics(raw)).replace("_", " ").lower()
    tokens = [t.strip(".'-") for t in cleaned.split()]
    return [t for t in tokens if t and t not in _NAME_SUFFIX_TOKENS]


# ── firm-name ranking key (search_firms exact-name-first ordering) ──────────

# Trailing legal-entity suffixes ignored when comparing a firm name with the
# search text, so "Wealth Enhancement Group" equals "WEALTH ENHANCEMENT GROUP,
# LLC". Written as they appear in filings; firm_rank_key() normalizes them the
# same way it normalizes names (dots removed: "L.L.C." -> "llc").
FIRM_LEGAL_SUFFIXES = (
    "LLC", "L.L.C.", "INC", "INCORPORATED", "LP", "L.P.", "LLP", "LLLP",
    "LTD", "LIMITED", "CO", "COMPANY", "CORP", "CORPORATION", "PLLC", "PLC",
    "PC", "PA", "P.A.", "N.A.",
)
_RANK_DROP = str.maketrans("", "", ".'\u2019")  # removed: "J.P." -> "jp"
_RANK_SPACE_RE = re.compile(r"[^\w\s]|_", re.UNICODE)  # other punctuation -> space


def _rank_tokens(raw: str) -> list[str]:
    # ASCII names (nearly all) skip the NFKD fold: it is a no-op for them,
    # and this runs once per matched name on broad searches.
    folded = raw if raw.isascii() else _fold_diacritics(raw)
    return _RANK_SPACE_RE.sub(" ", folded.lower().translate(_RANK_DROP)).split()


_FIRM_SUFFIX_TOKENS = frozenset(t for sfx in FIRM_LEGAL_SUFFIXES for t in _rank_tokens(sfx))


def firm_rank_key(raw: str | None) -> str:
    """Normalized firm name for exact / starts-with comparison in
    search_firms: diacritics folded, lowercase, dots and apostrophes removed,
    other punctuation (including U+FFFD) turned into spaces, whitespace
    collapsed, and trailing FIRM_LEGAL_SUFFIXES removed repeatedly ("Goldman
    Sachs & Co. LLC" -> "goldman sachs"). A name made only of suffixes keeps
    them. Pure; "" for empty input."""
    if not raw:
        return ""
    tokens = _rank_tokens(raw)
    end = len(tokens)
    while end > 1 and tokens[end - 1] in _FIRM_SUFFIX_TOKENS:
        end -= 1
    return " ".join(tokens[:end])


# ── flexible date parsing (the data mixes several date formats) ─────────────
# Formats in the 2026-08-28 production export:
#   YYYY-MM-DD            advisor_employments start/end dates
#   YYYY-MM-DD HH:MM:SS   marketplace memberSince (211 rows)
#   M/D/YYYY              industry_start_date, exam dates, brochure_date,
#                         firm state-registration dates ("AK-04/14/2009")
#   JavaScript Date text  marketplace memberSince (82 rows):
#                         "Mon May 05 2025 12:59:41 GMT+0000 (Coordinated
#                         Universal Time)" -- all GMT+0000; the date is taken
#                         as written.

_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_MDY_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_JS_DATE_RE = re.compile(r"[A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{2}) (\d{4})(?: .*)?")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def parse_flexible_date(raw: str | None) -> date | None:
    """A date from any format listed above; never raises -- empty,
    unparseable or impossible input (2/30/2020) returns None."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        m = _ISO_DATE_RE.fullmatch(text)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _MDY_DATE_RE.fullmatch(text)
        if m:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        m = _JS_DATE_RE.fullmatch(text)
        if m and m.group(1) in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None
    return None


def iso_date(raw) -> str | None:
    """"YYYY-MM-DD" for a date in any format parse_flexible_date() reads;
    None for empty or unparseable input (never guessed). Every date a tool
    returns goes through this."""
    d = parse_flexible_date(raw)
    return d.isoformat() if d else None


def years_since(raw: str | None) -> int | None:
    """Whole years between a flexibly-parsed date and today. None if the date
    can't be parsed at all — never guesses."""
    d = parse_flexible_date(raw)
    if d is None:
        return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


# ── envelope ──────────────────────────────────────────────────────────────────

# Fair-use notice carried by every tool response, the server instructions
# and the rate-limit message, so every caller (including a script) receives
# it with each call. Deliberately not called "terms" until counsel approves
# connector terms; then this text and the link change together.
FAIR_USE_URL = "https://advisorfinder.com/mcp"
FAIR_USE_NOTICE = (
    "This connector is for interactive research. Automated bulk extraction "
    f"is not permitted. See {FAIR_USE_URL}."
)


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
    where a single per-response link to one advisor wouldn't make sense.

    `verify` is left out of the result when it is empty or None."""
    from . import db  # local import: db.py never imports format.py, so this
    # is a one-directional dependency, not a cycle — deferred only to keep
    # format.py importable in isolation (e.g. before a DB path is set).

    meta = db.get_meta()
    data_as_of = (
        f"advisor data as of {meta.get('ia_reps_as_of')}; "
        f"firm data as of {meta.get('firms_as_of')}"
    )
    out = {
        **payload,
        "data_as_of": data_as_of,
        "advisorfinder": {
            "link": advisorfinder_profile_url(marketplace_row),
            "note": "Compare financial advisors on AdvisorFinder.",
            "fair_use": FAIR_USE_NOTICE,
        },
    }
    # `verify` only when there are links: never an empty {}.
    if verify:
        out["verify"] = verify
    out["coverage_caveats"] = caveats or []
    return out


# ── advisory-state wording (APPROVED by Drew 2026-08-18) ─────────────────────
# All four strings live here and nowhere else, so a later wording change stays a
# text edit. Tests assert placement, not prose.
#
# Four wording decisions are deliberate — do not "improve" them back:
#   - No "Also" in the broker note. For the 3,479 zero-advisory-state advisors
#     the broker note appears next to ADVISORY_STATES_NONE_NOTE, and "Also"
#     would presuppose an advisory registration the person does not have.
#   - "linked in this response", never "below". The payload is JSON read by an
#     assistant that renders keys in any order; there is no "below".
#   - The pending-CE note is plural-safe. 214 of 1,811 advisors have more than
#     one pending-CE state, so "this state" would be wrong for them.
#   - The broker note takes a pre-built count phrase, so "1 state" never
#     renders as "1 states". About 35,000 advisors have exactly one.

ADVISORY_STATES_NONE_NOTE = (
    "No state investment-adviser registrations are on record for this person in "
    "the SEC data. This is not a disclosure or disciplinary finding. Check the "
    "IAPD record linked in this response."
)

ADVISORY_STATES_UNAVAILABLE_NOTE = (
    "State registration detail is not available for this person in our data. It "
    "does not mean there are none. Check the IAPD record linked in this response."
)

ADVISORY_STATES_PENDING_CE_NOTE = (
    "Registration is in place in these states. An annual continuing-education "
    "course is outstanding. This is an administrative status, not a restriction "
    "and not a disclosure event."
)

BROKER_REGISTRATION_NOTE = (
    "Registered as a broker-dealer agent in {count_phrase}. Selling securities is "
    "not the same as giving investment advice. See the BrokerCheck record linked "
    "in this response."
)

CAPACITY_BOTH = "investment adviser and broker-dealer"
CAPACITY_IA = "investment adviser"
CAPACITY_BC = "broker-dealer"


# ── advisory states / broker registration / employment merge ────────────────

def _split_state_csv(raw) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in str(raw).split(",") if s.strip()]


# "AK-04/14/2009" (firms.state_registrations) or "VA:2023-01-03"
# (firms_state.states_approved). The date itself can hold hyphens, so the
# state is the leading letters before the first "-" or ":".
_STATE_DATE_RE = re.compile(r"([A-Za-z]{2,})\s*[-:]\s*(.*)")


def state_registrations(raw) -> list[dict]:
    """[{"state": "AK", "date": "2009-04-14"}, ...] from a comma-separated
    state registration column. date is None when the entry has no date or
    it cannot be read; a token that is not "ST-date"/"ST:date" is the state
    as given."""
    out = []
    for token in _split_state_csv(raw):
        m = _STATE_DATE_RE.fullmatch(token)
        if m:
            out.append({"state": m.group(1).upper(), "date": iso_date(m.group(2))})
        else:
            out.append({"state": token, "date": None})
    return out


def advisory_states_block(iar_row) -> dict:
    """The advisory-state keys for a tool payload.

    Three distinct outcomes, which must never be conflated:
      - no iar_details row      -> we hold no detail record (UNAVAILABLE note)
      - row, empty IA column    -> none on record (NONE note)
      - row, populated column   -> the advisory states, already distinct upstream

    NONE vs. UNAVAILABLE is keyed on row presence, not on whether scope was
    ever fetched. Today that is safe: every iar_details row that exists also
    carries fetched IA/BC scope (verified 0 NULLs across all 416,491 rows). But
    if a future upstream run ever inserts a detail row before scope has been
    fetched, `registered_states_ia` would be NULL/empty on that row, and this
    function would take the NONE branch — telling a consumer, confidently,
    that an advisor holds no advisory registrations when we simply never
    fetched them. That is the same confident-false-negative failure this
    whole feature exists to eliminate, just moved one layer down. If this ever
    becomes reachable, the durable fix is to key UNAVAILABLE on the presence
    of scope data itself (not row presence), or to have the export refuse to
    write a detail row with unfetched scope.
    """
    if iar_row is None:
        return {"advisory_states": [], "advisory_states_note": ADVISORY_STATES_UNAVAILABLE_NOTE}

    states = _split_state_csv(_row_value(iar_row, "registered_states_ia"))
    if not states:
        return {"advisory_states": [], "advisory_states_note": ADVISORY_STATES_NONE_NOTE}

    block = {"advisory_states": states}
    # Pending-CE is an annotation on states already in the list, never an
    # addition: intersect so a future upstream change cannot grow the list.
    pending = [s for s in states if s in set(_split_state_csv(
        _row_value(iar_row, "registered_states_ia_pending_ce")
    ))]
    if pending:
        block["advisory_states_pending_ce"] = pending
        block["advisory_states_pending_ce_note"] = ADVISORY_STATES_PENDING_CE_NOTE
    return block


def broker_registration_block(iar_row) -> dict | None:
    """Labeled broker-dealer registration count, or None when there is nothing
    to say. 0 (no broker states) and NULL (scope never fetched) both yield None,
    so no caller compares NULL with '>'. The note's number is templated from
    state_count in this one expression — never built from a second variable.
    Singular is handled here too: "1 state", never "1 states"."""
    if iar_row is None:
        return None
    count = _row_value(iar_row, "registered_states_bc_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return None
    count_phrase = "1 state" if count == 1 else f"{count} states"
    return {
        "state_count": count,
        "note": BROKER_REGISTRATION_NOTE.format(count_phrase=count_phrase),
    }


def _ia_only_flag(row):
    """'Y'/'N' normalized, or None when the flag is absent or unrecognized.
    An unrecognized flag is treated as absent — never as an adviser flag."""
    raw = _row_value(row, "ia_only")
    if raw is None:
        return None
    flag = str(raw).strip().upper()
    return flag if flag in ("Y", "N") else None


def merge_employment_rows(rows) -> list[dict]:
    """Collapse the adviser/broker row pair the SEC returns for one job.

    IAPD returns the same job twice for a dual-registered person: one object
    with iaOnly='Y' and one with iaOnly='N', same firm and same registration
    dates, differing only in their branch-office list (and in letter case).

    The group key includes the dates on purpose — keying on firm alone would
    merge a genuine rehire at the same firm into one row. firm_name is the
    fallback key when firm_crd is null, so two different no-CRD firms never
    merge with each other.
    """
    groups = {}
    order = []
    for row in rows:
        crd = _row_value(row, "firm_crd")
        firm_key = ("crd", str(crd)) if crd else ("name", str(_row_value(row, "firm_name") or ""))
        key = (
            firm_key,
            bool(_row_value(row, "is_current")),
            _row_value(row, "start_date"),
            _row_value(row, "end_date"),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    merged = []
    for key in order:
        group = groups[key]
        flags = [_ia_only_flag(r) for r in group]
        # Branch tie-break: prefer the adviser row's office. This is an
        # advisory product, so that is the office worth showing.
        preferred = next((r for r, f in zip(group, flags) if f == "Y"), group[0])
        out = {
            "firm_crd": _row_value(preferred, "firm_crd"),
            "firm_name": _row_value(preferred, "firm_name"),
            "is_current": bool(_row_value(preferred, "is_current")),
            "start_date": _row_value(preferred, "start_date"),
            "end_date": _row_value(preferred, "end_date"),
            "branch_city": _row_value(preferred, "branch_city"),
            "branch_state": _row_value(preferred, "branch_state"),
        }
        present = {f for f in flags if f is not None}
        if None in flags or not present:
            pass  # a missing flag is not an adviser flag — say nothing
        elif present == {"Y", "N"}:
            out["capacity"] = CAPACITY_BOTH
        elif present == {"Y"}:
            out["capacity"] = CAPACITY_IA
        elif present == {"N"}:
            out["capacity"] = CAPACITY_BC
        merged.append(out)
    return merged
