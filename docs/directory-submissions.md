# Directory Submission Worksheet — Claude & ChatGPT

Internal prep doc. Everything both reviews will ask for, pre-filled. Fields marked **[DREW]** need something only you have.

---

## Shared listing copy

- **Name:** AdvisorFinder
- **Short description (≤100 chars):** Search and vet SEC-registered financial advisors: profiles, disclosures, firm fees, credentials.
- **Long description:** AdvisorFinder gives your assistant direct access to official SEC/FINRA registration data on 213,000+ investment adviser representatives and 30,000+ advisory firms. Check whether an advisor is registered and has disclosures on record, review their work history, exams, and credentials, compare firms' size and ADV-filed fee structures, or browse advisors by city and state — all with verification links back to SEC IAPD and FINRA BrokerCheck. Built by AdvisorFinder, the platform for finding and comparing financial advisors.
- **Category:** Finance (or "Research/Data" where offered)
- **MCP endpoint:** `https://mcp.advisorfinder.com/mcp` (streamable HTTP)
- **Authentication:** None (public, read-only data)
- **Public docs/help page:** **[DREW]** publish `docs/mcp-help.md` at a stable URL (e.g. `advisorfinder.com/mcp`) and use that URL. Interim fallback: the GitHub README.
- **Privacy policy URL:** **[DREW]** advisorfinder.com's existing policy + the addendum below (both reviews require a stable URL).
- **Logo:** **[DREW]** AdvisorFinder logo — SVG or URL for Claude; **square PNG, no manually rounded corners** for ChatGPT (they reject rounded/wrong-shape). Also a favicon URL for Claude.
- **Support contact:** support@advisorfinder.com

## Reviewer test instructions (both reviews)

> No authentication or test account is needed — the server is public and read-only. Connect to `https://mcp.advisorfinder.com/mcp` (streamable HTTP). All six tools work anonymously. Suggested end-to-end flow: call `search_advisors` with name "Garcia" and state "FL"; take a returned CRD and call `get_advisor` then `check_advisor` with it; call `search_firms` with name "Edward Jones"; call `get_firm` with CRD 250; call `get_database_stats` with no arguments. Responses are JSON with `data_as_of`, verification links (SEC IAPD / FINRA BrokerCheck), and coverage caveats on every payload. The three resources (`advisorfinder://credentials-guide`, `data-sources`, `coverage-and-limitations`) are static reference text.

## Claude connectors directory — specifics

- Submitted from the claude.ai **submission portal in org admin settings; requires a Team or Enterprise organization**. **[DREW]** confirm the AdvisorFinder workspace plan.
- Ownership: connector touches only advisorfinder.com infrastructure (mcp.advisorfinder.com), our own R2 data artifact, and republishes public SEC/FINRA data we ingest ourselves — no third-party API wrapping.
- Example prompts (needs ≥3; use the 5 from the help page).
- Confirm-before-submit checklist: every tool run by us against production ✓ (all six exercised in deployment gates); no auth flow to document; privacy policy URL live **[DREW]**.

## ChatGPT apps/plugin directory — specifics

Submitted via OpenAI's plugin portal ("With MCP" path): production `/mcp` URL, domain verification **[DREW]** (DNS or file challenge on advisorfinder.com — same drill as the MCP registry TXT), tool scan, country availability (suggest: US only — the data is US-regulatory), policy attestations.

**Starter prompts:** use example prompts 1, 3, 4 from the help page.

**Positive test cases (5 — each tool covered):**
1. *"Is my financial advisor legitimate? His name is John Smith and he works at Edward Jones in Texas."* → calls `check_advisor` (name + firm + state); expected: ambiguous-match list of candidates with CRDs, or a single verdict with registration + disclosure status and BrokerCheck link.
2. *"Look up the advisor with CRD 2827240."* → calls `get_advisor`; expected: full profile (employment history, exams, registered states, disclosure status).
3. *"Tell me about Edward Jones as an advisory firm — size and fees."* → calls `search_firms` and/or `get_firm` (CRD 250); expected: AUM band `$25B+`, headcount, fee structure labeled "estimated from ADV Part 2A filing".
4. *"Find financial advisors in Austin, Texas I could talk to."* → calls `search_advisors` with city/state only (browse mode); expected: list of advisors with firms and disclosure status.
5. *"How fresh is your data and what does it cover?"* → calls `get_database_stats`; expected: counts, per-source vintages, coverage caveats.

**Negative test cases (3 — when NOT to trigger):**
1. *"What stocks should I buy this year?"* → must NOT call any tool: investment advice, out of scope; the model should answer (or decline) without invoking AdvisorFinder.
2. *"Find me a good dentist in Chicago."* → must NOT call any tool: not a financial-advisor query despite the "find me a professional near me" shape.
3. *"What were Apple's earnings last quarter?"* → must NOT call any tool: financial topic but not advisor/firm registration data.

**Tool annotations:** all six tools are `readOnlyHint: true`, no destructive/write operations, no user data stored. (Already accurate in the server's tool definitions.)

**Screenshots:** only required for Apps-SDK widget apps; this is a tool-only MCP plugin — skip unless the portal demands them.

## Privacy policy addendum (hand to whoever owns advisorfinder.com's policy)

> **AI Assistant Integrations (MCP).** AdvisorFinder provides a public, read-only API ("MCP server") that AI assistants such as Claude and ChatGPT use to query advisor and firm registration data on your behalf. We do not require accounts or collect personal information through this service. Queries you type stay with your AI assistant provider; our servers receive only the search terms the assistant sends (such as an advisor name or city) and standard technical logs (IP address, timestamps), retained briefly for security and operations. All advisor and firm data served is public regulatory information originating from the SEC and FINRA. Contact support@advisorfinder.com with questions or data concerns.

## Submission-day checklist

1. **[DREW]** Publish help page → stable URL
2. **[DREW]** Privacy policy addendum live → stable URL
3. **[DREW]** Logo assets (SVG + square PNG + favicon)
4. Claude: org admin portal → fill from this doc → submit
5. ChatGPT: developer portal → verify domain → fill from this doc → submit (review ≈ 1–2 weeks)
6. After approval: publish from each portal; then update README/help page with "available in the Claude/ChatGPT directory" links
