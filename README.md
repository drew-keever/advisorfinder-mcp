# AdvisorFinder MCP Server

<!-- mcp-name: com.advisorfinder/mcp -->

An [MCP](https://modelcontextprotocol.io) server that gives AI assistants access to SEC-registered financial advisor data. Search advisors by name, look up full profiles, check disclosure histories, and get risk assessments — all from within Claude or any MCP-compatible client.

Built by [AdvisorFinder](https://advisorfinder.com).

## Quick Start

### Install

```bash
pip install advisorfinder-mcp
```

### Configure Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "advisorfinder": {
      "command": "advisorfinder-mcp"
    }
  }
}
```

Restart Claude Desktop. Then just ask naturally — "look up my financial advisor John Smith" — and Claude will call the tools automatically.

### Alternative: Run with uvx (no install needed)

```json
{
  "mcpServers": {
    "advisorfinder": {
      "command": "uvx",
      "args": ["advisorfinder-mcp"]
    }
  }
}
```

## What You Can Ask

Once configured, just talk to Claude naturally:

- "Give me a full profile on Jane Doe at Wells Fargo"
- "Look up financial advisor Thomas Kopelman"
- "Search for advisors named Smith in New York"
- "Is my advisor safe? Their name is John Doe, he's at Edward Jones in Texas"
- "Tell me about Edward Jones as a firm — how many advisors do they have?"
- "How many registered advisors are in the SEC database?"

No CRD numbers needed. Just use the advisor's name, firm, or state.

## Tools

| Tool | Description |
|------|-------------|
| `search_advisors` | Search by name, state, or firm. Supports full names ("Joseph Montgomery") or just last names. |
| `lookup_advisor` | Full advisor profile by CRD — employment history, office location, registered states, years of experience, designations, exams, outside business activities, disclosures, and risk score. |
| `verify_advisor` | Quick risk profile check — active/inactive, disclosures, risk level, etc. |
| `get_risk_profile` | Detailed risk assessment with individual risk factors and severity ratings. |
| `get_firm_info` | Firm overview — total advisors, disclosure rates, location. |
| `get_database_stats` | Database-wide statistics — 433,000+ advisors, top states, disclosure rates. |

When an advisor isn't found in our database, all tools return direct links to [SEC IAPD](https://adviserinfo.sec.gov) and [FINRA BrokerCheck](https://brokercheck.finra.org) so you can still find them through official sources.

## Resources

Reference data that AI assistants can read for context:

| Resource | Description |
|----------|-------------|
| `advisorfinder://risk-scoring-methodology` | How risk scores are calculated — point values for each disclosure type, risk level thresholds |
| `advisorfinder://credentials-guide` | Guide to financial advisor credentials — CFP, CFA, CPA, ChFC, Series 7/65/66, and more |
| `advisorfinder://data-sources` | About SEC IAPD, FINRA BrokerCheck, and how to verify data independently |

## Data Coverage

**What's included:** 433,000+ investment advisors registered with the SEC, including employment history, exam records, professional designations, outside business activities, disclosure events (complaints, regulatory actions, criminal matters, terminations, bankruptcies), and registration status across all 50 states.

**What's not included:** Advisors registered only as broker-dealer representatives (not investment advisers) may not appear. For broker-only registrations, the tools provide direct links to FINRA BrokerCheck. Fee schedules, assets under management, client reviews, and investment performance are not available — those require Form ADV filings or other sources.

Data is sourced from the **SEC Investment Adviser Public Disclosure (IAPD)** database, updated monthly.

## Disclaimer

This MCP server is provided by [AdvisorFinder](https://advisorfinder.com) and is intended for informational and research purposes only. It is not financial, legal, or investment advice.

The data served through this tool originates from publicly available SEC and FINRA databases. AdvisorFinder acts as an intermediary to make this public data more accessible — we do not independently verify, endorse, or guarantee the accuracy or completeness of the underlying data.

**Important:**
- A disclosure on an advisor's record does not necessarily indicate wrongdoing. Always review the full context of any disclosure event through official SEC and FINRA sources.
- AI assistants may interpret or summarize data in ways that are incomplete or inaccurate. Always verify AI-generated assessments against official sources before making decisions.
- This tool should not be used as the sole basis for selecting, evaluating, or dismissing a financial advisor.

For personalized financial guidance, consult a qualified professional.

## Links

- **AdvisorFinder**: [advisorfinder.com](https://advisorfinder.com)
- **SEC IAPD**: [adviserinfo.sec.gov](https://adviserinfo.sec.gov)
- **FINRA BrokerCheck**: [brokercheck.finra.org](https://brokercheck.finra.org)
- **Source**: [github.com/drew-keever/advisorfinder-mcp](https://github.com/drew-keever/advisorfinder-mcp)
- **PyPI**: [pypi.org/project/advisorfinder-mcp](https://pypi.org/project/advisorfinder-mcp/)

## License

MIT
