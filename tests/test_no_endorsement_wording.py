"""Task 4 (marketplace-layer) global constraint, test-enforced: being listed
on AdvisorFinder's marketplace is a business relationship, never phrased as
an endorsement. Scans every tool's output (across a representative spread of
fixture inputs, including marketplace members) for the forbidden phrases.
"""
import re

from advisorfinder_mcp import server

_FORBIDDEN_RE = re.compile(
    r"endorsed|recommended by advisorfinder|vetted by advisorfinder",
    re.IGNORECASE,
)


def _assert_no_forbidden_wording(obj, path="root"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_forbidden_wording(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_no_forbidden_wording(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        assert not _FORBIDDEN_RE.search(obj), f"forbidden endorsement wording at {path}: {obj!r}"


def test_no_endorsement_wording_anywhere():
    outputs = [
        server.get_advisor(crd="1000002"),  # marketplace member
        server.get_advisor(crd="1000003"),  # marketplace member (minimal)
        server.get_advisor(crd="1000001"),  # non-member
        server.get_advisor(crd="9999999"),  # not found
        server.check_advisor(name_or_crd="1000002"),
        server.check_advisor(name_or_crd="smith"),  # ambiguous
        server.check_advisor(name_or_crd="zzznomatchzzz"),
        server.search_advisors(name="smith"),
        server.search_advisors(city="New York", state="NY"),
        server.find_bookable_advisors(),
        server.find_bookable_advisors(specialty="retirement"),
        server.find_bookable_advisors(city="Boston", state="MA"),
        server.search_firms(name="alpha"),
        server.get_firm(crd="100001"),
        server.get_database_stats(),
    ]
    for output in outputs:
        _assert_no_forbidden_wording(output)
