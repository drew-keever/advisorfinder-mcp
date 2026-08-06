import asyncio

from fastmcp import FastMCP

from advisorfinder_mcp import resources


def _registered_uris(mcp) -> set[str]:
    regs = asyncio.run(mcp.list_resources())
    return {str(r.uri) for r in regs}


def test_register_attaches_exactly_three_resources():
    mcp = FastMCP("test")
    resources.register(mcp)
    uris = _registered_uris(mcp)
    assert uris == {
        "advisorfinder://credentials-guide",
        "advisorfinder://data-sources",
        "advisorfinder://coverage-and-limitations",
    }


def test_risk_scoring_methodology_resource_removed():
    mcp = FastMCP("test")
    resources.register(mcp)
    uris = _registered_uris(mcp)
    assert "advisorfinder://risk-scoring-methodology" not in uris


def test_credentials_guide_content_carried_over_from_v1():
    text = resources.credentials_guide()
    assert "CFP" in text and "Certified Financial Planner" in text
    assert "CFA" in text and "Chartered Financial Analyst" in text
    assert "Series 65" in text


def test_data_sources_mentions_upstream_and_vintage_and_disclosure_note():
    text = resources.data_sources()
    assert "SEC IAPD" in text or "IAPD" in text
    assert "FINRA BrokerCheck" in text or "BrokerCheck" in text
    assert "2026-05-20" in text or "2026-05-01" in text  # from fixture export_meta
    assert "disclosure detail is deliberately not included" in text.lower() or \
        "disclosure detail" in text.lower()


def test_coverage_and_limitations_mentions_scope_and_self_reported():
    text = resources.coverage_and_limitations()
    lower = text.lower()
    assert "state-registered" in lower
    assert "self-reported" in lower
    assert "2026-05-20" in text or "2026-05-01" in text
