# AdvisorFinder MCP Server

<!-- mcp-name: com.advisorfinder/mcp -->

An [MCP](https://modelcontextprotocol.io) server that gives AI assistants access to SEC-registered financial advisor data — search advisors by name, look up full profiles, check disclosure status, and review firm fees, all from within Claude or any MCP-compatible client.

Built by [AdvisorFinder](https://advisorfinder.com).

## Quick Start

### Remote server (recommended)

The fastest way to use this — no install, always up to date. Point your MCP client at:

```
https://mcp.advisorfinder.com/mcp
```

**Claude (custom connector):** Settings → Connectors → Add custom connector → paste the URL above.

**Claude Code:**

```bash
claude mcp add --transport http advisorfinder https://mcp.advisorfinder.com/mcp
```

### pip / uvx (stdio)

Prefer a local stdio process — e.g. for MCP clients that don't yet support remote/HTTP servers directly? Install the package; it runs a thin stdio proxy in front of the same remote server above.

```bash
pip install advisorfinder-mcp
```

```json
{
  "mcpServers": {
    "advisorfinder": {
      "command": "advisorfinder-mcp"
    }
  }
}
```

Or with `uvx` (no install needed):

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

By default the proxy talks to `https://mcp.advisorfinder.com/mcp`. To point it at a different deployment (e.g. staging), set `ADVISORFINDER_MCP_URL`:

```json
{
  "mcpServers": {
    "advisorfinder": {
      "command": "advisorfinder-mcp",
      "env": { "ADVISORFINDER_MCP_URL": "https://staging.example.com/mcp" }
    }
  }
}
```

## What You Can Ask

Once configured, just talk to Claude naturally:

- "Look up financial advisor Thomas Kopelman"
- "Search for advisors named Smith in New York"
- "Give me the full profile on Jane Doe at Wells Fargo"
- "Is my advisor's registration active? Their name is John Doe, he's at Edward Jones in Texas"
- "Does John Doe have any disclosures on file?"
- "Tell me about Edward Jones as a firm — how many advisors do they have, and what do they charge?"
- "How many registered advisors are in the SEC database, and how fresh is the data?"
- "Find a fee-only advisor near Austin I can actually book a call with"

No CRD numbers needed — search by name, firm, city, or state.

## Tools

| Tool | Description |
|------|-------------|
| `search_advisors` | Search SEC-registered investment adviser representatives by name, firm, city, and/or state. Returns CRD, name, linked firm(s), a four-state disclosure status, and an SEC IAPD link per match. |
| `get_advisor` | Full profile by CRD: employment history, exams, registered states, self-reported professional designations, years in the industry, and four-state disclosure status. |
| `check_advisor` | Quick verification by CRD or name (optionally narrowed by firm/state): is the registration active, what states, how long in the industry, and disclosure status. Never returns a numeric risk score — only the underlying facts. |
| `find_bookable_advisors` | Search advisors listed on AdvisorFinder's marketplace — a few hundred members with public profiles you can view and contact directly (optionally filtered by specialty, city, and/or state). Returns each member's self-provided profile info, a link to their full AdvisorFinder profile, and regulatory facts for their CRD: the same registration + four-state disclosure status as any other advisor when their CRD is in our SEC dataset, or a labeled "not in our SEC dataset" note with verify links when it isn't (typically state-registered or BD-side advisors) — never presented as if clean. |
| `search_firms` | Search SEC- and state-registered investment adviser firms by name and/or state. Returns CRD, name, city/state, AUM band, advisor headcount, and an SEC IAPD link. State-registered-only firms are included but flagged as thinner-coverage. |
| `get_firm` | Full firm profile by CRD: locations, other/prior names, fee schedule (from Form ADV Part 2A where available), disciplinary flags, and advisor roster count. |
| `get_database_stats` | Database-wide stats: firm and advisor counts, data vintages (as-of dates for each upstream source), and a full four-state disclosure tally. |

**Disclosure status is always one of four honest states** — `none_reported`, `disclosed_no_detail`, `disclosed_with_detail`, or `unknown` — never phrased as "clean" or "safe." When we don't have a record for someone, every tool returns direct links to [SEC IAPD](https://adviserinfo.sec.gov) and [FINRA BrokerCheck](https://brokercheck.finra.org) so you can still look them up through official sources.

## Resources

| Resource | Description |
|----------|-------------|
| `advisorfinder://credentials-guide` | Guide to financial advisor credentials — CFP, CFA, CPA, ChFC, Series 7/65/66, and more (all self-reported; not independently verified here). |
| `advisorfinder://data-sources` | About SEC IAPD, FINRA BrokerCheck, what we store vs. don't, and how to verify data independently. |
| `advisorfinder://coverage-and-limitations` | What's covered, what isn't (state-only firms, disclosure detail, verified designations, the AdvisorFinder marketplace), and what an empty result actually means. |

## Data & Limitations

**Coverage:** SEC-registered investment adviser firms and the active investment adviser representatives (IARs) linked to them. Advisor rosters cover IARs at SEC-registered advisers only — complete as of 2026-08 (416,000+ active IARs; validated within 1% of FINRA's CRD statistics, and per-firm against Form ADV Item 5 self-reports). Purely state-registered advisers (~16k firms, ~47k IARs per NASAA) have no rosters; state-listed firms showing rosters are dual-registered. An empty roster for a state-registered firm means not covered, not empty. Use `get_database_stats` for current firm/advisor counts and per-source data vintages (as-of dates), which move independently of this package's version.

**Disclosure detail is deliberately not republished here.** We store and surface a disclosure *status* (one of the four states above), never the underlying event narratives, allegations, or settlement amounts. Always check [FINRA BrokerCheck](https://brokercheck.finra.org) or [SEC IAPD](https://adviserinfo.sec.gov) directly for the full disclosure record before making any decision.

**Designations are self-reported.** Every professional designation (CFP, CFA, etc.) shown here comes from the advisor's own filing — none of it is independently verified against the issuing body. Check directly with the issuing body if a credential matters to your decision.

**No numeric risk score.** This server intentionally does not compress disclosure/registration facts into a single risk number — it surfaces the underlying facts so you can judge for yourself.

**AdvisorFinder marketplace.** Some advisors are listed on AdvisorFinder's marketplace (`find_bookable_advisors`, and enrichment on other tools' results). Their listings add self-provided profile information and a link to contact them. Being listed is a business relationship with AdvisorFinder — it is labeled on every result, never affects search ranking, and is not an endorsement. Regulatory data is shown identically for all advisors. AUM and client-count figures in marketplace listings are self-reported, always labeled "as listed on their AdvisorFinder profile." Some marketplace members are state-registered or broker-dealer-side advisors whose records may not appear in our SEC-roster data — those results are clearly labeled, with FINRA BrokerCheck / SEC IAPD verify links included.

## Disclaimer

This MCP server is provided by [AdvisorFinder](https://advisorfinder.com) and is intended for informational and research purposes only. It is not financial, legal, or investment advice.

The data served through this tool originates from publicly available SEC and FINRA databases. AdvisorFinder acts as an intermediary to make this public data more accessible — we do not independently verify, endorse, or guarantee the accuracy or completeness of the underlying data.

**Important:**
- A disclosure on an advisor's record does not necessarily indicate wrongdoing. Always review the full context of any disclosure event through official SEC and FINRA sources.
- AI assistants may interpret or summarize data in ways that are incomplete or inaccurate. Always verify AI-generated assessments against official sources before making decisions.
- This tool should not be used as the sole basis for selecting, evaluating, or dismissing a financial advisor.

## Changelog

### 2.1.0 — Marketplace layer

Non-breaking addition on top of 2.0.0:

- **New tool:** `find_bookable_advisors` — search AdvisorFinder marketplace members (a few hundred advisors) by specialty, city, and/or state; returns their self-provided profile info plus regulatory facts for their CRD (the same registration + four-state disclosure status as any other advisor when the CRD is in our SEC dataset, or a labeled "not in our SEC dataset" note with verify links when it isn't).
- **Marketplace enrichment.** `search_advisors`, `get_advisor`, and `check_advisor` now include a labeled AdvisorFinder marketplace listing block on results for advisors who are marketplace members — never an endorsement, never affecting ranking.
- **`get_database_stats`** now reports a marketplace member count and snapshot date.
- **New resource copy:** `advisorfinder://coverage-and-limitations` documents the marketplace layer.

### 2.0.0 — Breaking changes

This is a full rebuild, not an incremental update:

- **New toolset.** `verify_advisor` → `check_advisor`; `lookup_advisor` → `get_advisor`; `get_risk_profile` is **removed**; `search_firms` and `get_firm` are **new**.
- **Numeric risk score removed entirely.** v1's `get_risk_profile` tool and the `advisorfinder://risk-scoring-methodology` resource are gone. Disclosure information is now surfaced only as the four honest states described above — never compressed into a score.
- **Backend replaced.** The server now reads from a local, periodically-refreshed SQLite export instead of querying upstream sources live per-request.
- **v1.x clients should upgrade.** The old v1 API surface (`verify_advisor`, `lookup_advisor`, `get_risk_profile`, `advisorfinder://risk-scoring-methodology`) is being retired and will stop working. Update to the new tool names and drop any reliance on risk scores.
