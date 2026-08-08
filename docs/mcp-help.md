# AdvisorFinder MCP — Help & Getting Started

*The content of this page is written to be published at a stable public URL (e.g. `advisorfinder.com/mcp` or the AdvisorFinder blog). Both the Claude and ChatGPT directory reviews require it.*

---

## What it is

The **AdvisorFinder MCP server** lets AI assistants like Claude and ChatGPT search and vet SEC-registered financial advisors using official regulatory data. Ask about an advisor by name — no CRD number needed — and get their registration status, disclosure history status, work history, exams and credentials, plus firm-level details like advisory fees (as filed in ADV brochures) and assets under management.

All data originates from public SEC IAPD and FINRA BrokerCheck records, refreshed from SEC bulk filings. Every answer includes links to verify directly with the SEC and FINRA.

## Connect

**Claude:** Settings → Connectors → Add custom connector → paste:

```
https://mcp.advisorfinder.com/mcp
```

**Claude Code:** `claude mcp add --transport http advisorfinder https://mcp.advisorfinder.com/mcp`

**Any MCP client (stdio):** `pip install advisorfinder-mcp`, then run `advisorfinder-mcp`.

No account, no API key, no authentication — it's free and read-only.

## Example prompts

1. **"Is my financial advisor legit? Her name is Jane Smith, she's at Edward Jones in Texas."** — checks registration status, years of experience, and disclosure status, with FINRA BrokerCheck links to review the full record.
2. **"Give me a full profile on the advisor with CRD 2827240."** — employment history, exams passed, registered states, designations, and disclosure status.
3. **"Tell me about Edward Jones as a firm — how big are they and what do they charge?"** — firm profile with AUM band, headcount, client types, and fee structure as filed in their ADV Part 2A brochure.
4. **"Find financial advisors in Austin, Texas."** — browse advisors by location, a starting point for choosing someone to interview.
5. **"How current is your advisor data?"** — data vintages, coverage counts, and known limitations.

## What's in the data

- **213,000+ active investment adviser representatives** at SEC-registered firms, with employment history, exams, registered states, and self-reported designations
- **15,000+ SEC-registered advisory firms** — size, client types, fee arrangements, ADV-filed fee structures, branch locations, prior names
- **16,000+ state-registered advisory firms** (reduced profiles)
- Four-state disclosure status per advisor, derived from the SEC roster flag

## Honest limitations

- **Disclosure detail is deliberately not republished.** We show whether an advisor has disclosures on record and how many; for the events themselves we link to FINRA BrokerCheck. Never treat "has disclosures" as automatically disqualifying — or "none reported" as a guarantee.
- **Advisor rosters cover SEC-registered firms.** Most state-registered firms (small RIAs) don't have individual advisor records here — an empty roster means "not covered," not "nobody works there."
- **Designations (CFP®, CFA, etc.) are self-reported** in regulatory filings and not independently verified.
- **Fee data is extracted from ADV brochures** and may be estimated — always confirm current fees with the firm.
- Data is refreshed from SEC bulk filings; each response carries its `data_as_of` vintage.

## Disclaimer

AdvisorFinder republishes public regulatory registration data for informational and research purposes. This is not financial, legal, or investment advice, and not a recommendation of any advisor or firm. Verify anything important directly at [adviserinfo.sec.gov](https://adviserinfo.sec.gov) and [brokercheck.finra.org](https://brokercheck.finra.org).

## Support

Questions or data issues: support@advisorfinder.com · [advisorfinder.com](https://advisorfinder.com)
