"""Regression pin for the post-sweep resume-round coverage-caveat rewording
(task review finding): the validated copy replaced resources.py/README.md/
docs/mcp-help.md's understated "most state-registered firms are not
covered" wording (see tests/test_resources.py, which only loosely asserts
"state-registered" is present in that resource -- it never pinned the old
literal). That first pass missed three LIVE TOOL-OUTPUT strings -- the
surface an MCP client actually reads, more consumer-facing than the static
resource text -- which still said the old understated thing:
search_firms' per-result state_only caveat, get_firm's reduced-profile
caveat, and get_database_stats' coverage note. All three are now updated to
the accurate, concise framing (purely state-registered firms have NO
rosters at all, not "most... not covered"; a state-registered firm showing a
roster is dual-registered).

Since nothing pinned the old literal before, this test exists specifically
so it can't silently regress back: scans a representative spread of tool
outputs (including a state-only firm via both search_firms and get_firm, and
get_database_stats) for the retired phrase.
"""
import re

from advisorfinder_mcp import server

# The retired, understated phrasing ("our advisor roster does not cover most
# state-registered firms" / "most state-registered firms are not covered").
# Matched loosely (case-insensitive, "not"/"does not"/"doesn't" variants)
# rather than one exact literal, since the point is that NO tool output
# should still understate coverage as "most... not covered" rather than the
# accurate "purely state-registered firms have zero rosters".
_RETIRED_RE = re.compile(r"most state-registered firms", re.IGNORECASE)


def _assert_retired_phrase_absent(obj, path="root"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_retired_phrase_absent(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_retired_phrase_absent(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        assert not _RETIRED_RE.search(obj), (
            f"retired 'most state-registered firms' coverage wording found at "
            f"{path}: {obj!r}"
        )


def test_retired_coverage_phrasing_absent_from_all_tool_outputs():
    outputs = [
        # search_firms: 500001 (DELTA STATE ADVISERS) is the fixture's
        # state-only firm -- carries the per-result state_only caveat.
        server.search_firms(state="NY"),
        server.search_firms(name="delta state advisers"),
        # get_firm: same CRD, via the reduced-profile branch.
        server.get_firm(crd="500001"),
        server.get_firm(crd="100001"),  # SEC-registered, no state caveat -- still scanned
        server.get_database_stats(),
    ]
    for output in outputs:
        _assert_retired_phrase_absent(output)


def test_search_firms_state_only_caveat_says_no_roster_not_most_not_covered():
    result = server.search_firms(name="delta state advisers")
    row = next(r for r in result["results"] if r["crd"] == "500001")
    caveat = row["caveat"].lower()
    assert "state-registered" in caveat
    assert "no roster is available here" in caveat
    assert "most state-registered firms" not in caveat


def test_get_firm_state_only_caveat_says_no_roster_not_most_not_covered():
    result = server.get_firm(crd="500001")
    caveats = [c.lower() for c in result["coverage_caveats"]]
    assert any("no roster is available here" in c for c in caveats)
    assert not any("most state-registered firms" in c for c in caveats)


def test_database_stats_note_reflects_complete_sec_side_and_dual_registration():
    result = server.get_database_stats()
    note = result["coverage"]["note"].lower()
    # Still true, still test-enforced by test_get_database_stats.py:
    assert "empty roster does not mean" in note
    # New, accurate framing from the validated post-sweep copy:
    assert "purely state-registered firms have no rosters" in note
    assert "dual-registered" in note
    assert "most state-registered firms" not in note
