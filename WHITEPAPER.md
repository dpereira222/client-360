# White Paper — Client 360 Dashboard

**Author:** David Pereira (RevOps)
**For:** Data Engineering / Databricks platform team
**Date:** 2026-07-16
**Status:** Draft for review — rev. 2026-10-07 (adds the sales-CRM layer: peers,
chat/pitch tracking, notes, expiries, idea targeting, daily color)
**Companion:** working prototype in this folder (`app.py`), runnable today on mock data

---

## 1. Purpose

Give the IC and sales desk a **single client view across every product line** —
spot, derivatives, options, credit, and FX — for a client relationship and all of
its legal entities. Today this data is spread across separate product tables,
dashboards (Sigma, Midas), and the spot-only Client Brief tool. Before a call with,
say, Brevan Howard, a salesperson should open one screen and immediately see:

- every entity that rolls up to that client,
- what they've traded recently and in what products,
- current positions and risk across products,
- open credit facilities and their health,
- revenue and margin to FalconX,
- recent calls, next steps, and relevant news.

This document specifies the app and, critically, the **data contract** engineering
needs to fulfil. A runnable prototype accompanies it so requirements are concrete,
not abstract — it already renders the full UI on fabricated data.

## 2. Background & workflow (from the Jul-10 review)

The build model we agreed on:

1. RevOps describes the desired outcomes (this document + the prototype).
2. Engineering **prepares/normalizes the underlying Databricks tables** — raw
   product data is messy and needs filtering and a common shape.
3. The app is a **folder of code** (Streamlit, like Aayushi's app) that is moved
   into the Databricks-accessible GitHub location and executed as a Databricks App.
4. Data access happens through a thin seam so the UI and the queries evolve
   independently.

This mirrors the arrangement where the platform team preps tables and the app owner
manages app logic.

## 3. Constraints (non-negotiable)

- **All data stays inside Databricks.** No client data — especially client names —
  leaves the environment. No external cloud compute, no copying to a laptop, no
  third-party SaaS. The app runs as a Databricks App against the SQL warehouse /
  Genie tables. *(The one exception is public market-news RSS, which contains no
  client data.)*
- **Access control** follows existing Databricks entitlements; the app inherits the
  viewer's permissions. Row/column controls on sensitive fields stay enforced at the
  warehouse, not in app code.
- **Security tags** on the underlying tables currently reset daily (known issue on
  the platform side). Whatever table-prep the app depends on must survive that reset
  or be re-applied by the pipeline.
- **The AI analyst is the one feature that needs a compliance decision** — see §10.
  In the prototype it calls the Anthropic API directly (fine: mock data, no real
  client names). In production it must run through an in-environment model (Databricks
  model serving / an approved Claude endpoint) so no client data leaves Databricks.

## 4. What the app shows

The app has two workspaces, switched from the top navigation bar. The selected
client stays selected when you move between pages.

**Client 360** covers one relationship across every product. The sidebar holds the
client picker, a per-entity focus filter and the date controls (quick lookback in
days/weeks/months/**years**, or a **custom start/end range**). The page opens with a
masthead (name, tier, coverage, segment, entity chips) and a cross-product KPI strip,
then five tabs. Some tabs have sub-tabs:

| Tab › sub-tab | Content | Backing data |
|---|---|---|
| **Overview › Summary** | Revenue by product, notional over time, product-activity table, net token flow, **client-vs-desk positioning** | `product_activity`, `volume_timeseries`, `token_flow`, `get_desk_benchmark` |
| **Overview › Trades** | Filterable unified blotter (product / side / token), size distribution, avg bps by product | `get_trades` |
| **Risk › Positions** | Gross/long/short exposure, exposure by product and token, position detail (funding, delta, vega) | `get_positions` |
| **Risk › Options** | **Multi-leg structure detection** (verticals, straddle/strangle, risk reversal, butterfly, iron condor, calendar, covered call), strategy mix, bias, leg detail | `get_trades` + `option_structures` |
| **Risk › Credit** | Facilities, drawn/collateral, LTV, maturity, margin-health status, alerts | `get_credit` |
| **Peers** | What similar clients (anonymous cohort, e.g. "Macro hedge funds") are doing: product adoption, token mix, trends, direction, popular structures, benchmarks, desk-idea uptake, talking points (§12) | `get_peer_activity`, `get_peer_strategies`, `get_desk_idea_uptake` |
| **Relationship › Brief** | Auto meeting brief, Gong call history, live token news | `get_gong`, `get_news` |
| **Relationship › Notes** | Pros-style call notes with auto-hashtags, "what we know", notes-vs-book verdicts, follow-ups, book-wide "who has talked about…" (§14) | `get_notes`, `save_note` |
| **Relationship › Pitches & Chat** | Pitch scorecard ("we pitched X, it's up N%, you did / didn't take it"), pass reasons, callbacks, chat engagement / gone quiet, RFQs, chat archive, live extractor audit (§13) | `get_trade_ideas`, `get_chat_messages`, `get_rfqs`, `get_price_history` |
| **Analyst** | **Opus 5.5**: one-click activity summary + live chat, grounded in everything above, including peers, pitches, notes, expiring structures and today's desk color (§10) | `llm.py` over the derived context |

**Desk pages** run across the whole book and ignore the sidebar dates:

| Page | Content | Backing data |
|---|---|---|
| **Expiries** | Every live option structure on the book: scope (client / rep's book / whole book), filters (expiry, trade date, size, structure), mark-to-spot P&L, **pin risk** (≤14d and ≤3% from strike), expiry ladder, Monday digest per salesperson | `get_book_trades`, `get_price_history` |
| **Idea targeting** | Type a trade idea ("ETH vol into Dec"). Clients are ranked by "who will bite", each with a reason and warnings, and an Opus draft of the pitch. A post-mortem on past ideas feeds the outcomes back into the ranking (§15) | `get_book_trades`, `get_notes`, `get_trade_ideas`, `get_profile` |
| **Daily color** | The 4:30pm ET note: σ-sized moves, flow vs. the 20-day average, segment skew, options mix, "split views". Comes in a desk version, a per-client version (that client's own flow excluded) and an internal rep sheet (§16) | `get_desk_flow`, `get_price_history`, `get_news` |

## 5. The data contract

The app talks to data **only** through the functions in `data.py`. Each is a seam:
today it returns mock data; going live means replacing the body with a Databricks
query that returns **the documented columns and dtypes**. The UI does not change.

Functions marked **[DERIVED]** are pure pandas on top of the others — **no query
needed** (that includes options structure detection and the desk comparison).
Engineering implements the base functions below: §5.1–5.8 for the client view and
§5.9–5.13 for the CRM layer. The full output schema for each one is in its `data.py`
docstring, which is the source of truth.

### 5.1 `search_parents() -> list[str]`
Distinct top-level client relationships for the picker.

### 5.2 `get_entities(parent) -> DataFrame`
Every legal entity under a parent.
Columns: `entity_id, entity_name, parent_name, entity_type, status, onboarded`.

### 5.3 `get_trades(parent, start, end) -> DataFrame`  ← core fact table
One row per executed trade across **all** products, all entities, with `trade_time`
between `start` and `end` (ISO dates). Date-range args support the years lookback
and custom range.
Columns: `trade_time, entity_id, entity_name, product, instrument, token, side,
notional_usd, price, bps, revenue_usd, venue, qty, structure_id`.
`product ∈ {Spot, Derivatives, Options, Credit, FX}`; `side ∈ {Buy, Sell}`.
- `price` — execution price; **option premium per contract** for option legs.
- `qty` — contracts / units.
- **`structure_id`** — parent order id that links the legs of one multi-leg options
  structure. This is what powers strategy detection (condor vs. put spread vs. …).
  Null for single-instrument trades. If the OMS has no such id, the detector falls
  back to clustering by entity + underlying + timestamp, but **supplying it is
  strongly preferred** for accuracy.
- Option `instrument` format: `TOKEN-DDMMMYY-STRIKE-{C|P}` (e.g. `BTC-29AUG26-95000-C`).

### 5.4 `get_positions(parent) -> DataFrame`
Current open positions across spot / derivatives / options.
Columns: `product, instrument, token, direction, qty, avg_price, mark_price,
notional_usd, unrealized_pnl, funding_8h_bps, delta, vega_usd`
(last three null except where the product supplies them).

### 5.5 `get_credit(parent) -> DataFrame`
Open credit facilities.
Columns: `deal_id, entity_name, facility_type, collateral_token, principal_usd,
drawn_usd, collateral_usd, ltv_pct, rate_bps, start, maturity, days_to_maturity,
status`. `status ∈ {good, warning, serious, critical}` (can be computed from LTV +
days-to-maturity, or supplied by the credit desk table).

### 5.6 `get_gong(parent, start, end) -> DataFrame`
Reuses the existing, working query in `client-brief/app.py` against
`main.salesforce.gong_gong_call_c`.

### 5.7 `get_desk_benchmark(tokens, start, end) -> DataFrame`
Desk-wide net flow per token across **all** clients in the window — the baseline
the Overview tab compares one client against. Aggregate over all counterparties;
**return only aggregates, never client names** (privacy-safe by construction).
Columns: `token, desk_buy_usd, desk_sell_usd, desk_net_usd, num_clients`.

### 5.8 `get_news(tokens) -> list[dict]`
Public crypto RSS, filtered to the client's tokens (with ticker→name synonyms),
deduped, newest-first, cached 15 min. Only call permitted to reach outside
Databricks — contains **no client data**.

### 5.9 `get_profile(parent) -> dict` · `get_book_trades(start, end) -> DataFrame`
Relationship metadata from the Salesforce Account (tier, coverage, **segment /
sub-segment** per §12.2, main contact, products set up). `get_book_trades` is the
§5.3 blotter **for every client** plus a `parent` column. It feeds Expiries, Idea
targeting and the daily color, and is internal only.

### 5.10 Peer seams: `get_peer_activity`, `get_peer_strategies`, `get_desk_idea_uptake`, `get_desk_flow`, `peer_id_for`
**Anonymized Databricks views** over the unified blotter. The counterparty is
replaced by a salted-hash `peer_id` and the requesting client is excluded, so
names never leave the view. Activity and strategies are weekly; desk flow is at
trade grain (the color day runs 4:30pm → 4:30pm ET). `peer_id_for(parent)` exists
only so the app can *exclude* a client's own rows. It must never be used to
re-identify anyone.

### 5.11 Chat seams: `get_chat_messages`, `get_trade_ideas`, `get_rfqs`
Read from the Telegram archive (§13). `get_trade_ideas` is the output of the nightly
`chat_intel.extract()` job. Execution is **not** stored. `pitches.py` infers it by
matching ideas against `get_trades`.

### 5.12 `get_price_history() -> DataFrame`
Daily close per token, about 3 years (`date, token, close`). This is market data,
not client data. It marks pitched ideas and drives move detection in the daily color.

### 5.13 `get_notes(parent|None, start, end)` · `save_note(...)` ← the one WRITE path
An append-only Delta table (e.g. `main.client360.crm_notes`). An edit is written as a
new row that references the old `note_id`, which keeps the history auditable. Tags are
computed inside `save_note` (`tags.py`), so every writer (the app, a Slack shortcut,
a Salesforce sync) tags notes the same way.

## 6. Critical dependency — entity resolution

**This is the single biggest ask and the main thing that does not exist today.**

The spot table (`main.otc_analytics.trade_log`) keys on `counterparty`, a free-text
name with many spellings and no notion of a parent client or its sub-entities. The
whole value of Client 360 is rolling **BH Digital LP**, **Brevan Howard Master Fund
Ltd**, etc. up to one **Brevan Howard** relationship.

We need a maintained mapping table, ideally sourced from onboarding/KYC:

```
parent_name  →  entity_id  →  { entity_name, entity_type, onboarded, status }
```

plus a reconciliation from each product table's counterparty identifier to
`entity_id`. Everything else composes cleanly once this exists.

## 7. Source tables to normalize

The app expects one **unified, cleaned** trade shape and one position shape. From the
review, the cleaned product data lives in the Genie spaces. Engineering maps each
source into the common schema (§5.3 / §5.4):

| Product | Source (confirm exact names) | Notes |
|---|---|---|
| Spot | `main.otc_analytics.trade_log` (`trade_type='order'`, `pnl_usd` not null) | Already used by Revenue Intel / Client Brief |
| Derivatives | Genie: derivatives trades + positions | Perps/futures; funding for positions |
| Options | Genie: options trades + risk | Greeks (delta, vega) from the risk table. Include a **parent-order / structure id** on option legs if the OMS has one (powers strategy detection — see §5.3). |
| Credit | Credit desk deal table + collateral marks | Facilities, LTV, maturities |
| FX | FX trades table | |

We do **not** need the app to compute risk — we need the desk's existing marks and
greeks surfaced. Where a product has no clean table yet, we phase it in (§9).

**Options structure detection** (condor / put spread / covered call / …) is done in
the app from the legs — no extra table needed. It only needs (a) the option
`instrument` in the standard `TOKEN-DDMMMYY-STRIKE-{C|P}` format and, ideally,
(b) `structure_id` to group legs precisely. Without `structure_id` it clusters legs
by entity + underlying + timestamp, which is good but not perfect for busy books.

## 8. Non-functional requirements

- **Freshness:** intraday is ideal for positions/credit; end-of-day is acceptable for
  the v1. State the actual latency so the UI can label it.
- **Performance:** queries are always scoped to one parent's `entity_id` set and a
  date window — should be cheap with the right partitioning on `pdate`/entity.
- **Warehouse:** reuse the existing SQL warehouse (`9118f4f27caf0ffe`) and the
  `Config()` auth pattern already in this repo.
- **No caching of client data outside Databricks.**

## 9. Phasing

- **Phase 0 — done:** prototype + this spec (mock data, full UI).
- **Phase 1 — Entity resolution:** the parent↔entity mapping (§6). Unblocks everything.
- **Phase 2 — Spot + Credit live:** spot already exists; credit is the highest-value
  net-new surface for the desk.
- **Phase 3 — Derivatives + Options:** positions, greeks, funding.
- **Phase 4 — FX** and intraday refresh.
- **Phase 5 — CRM layer:** notes table + segments (no dependencies, so it can start
  any time), then the anonymized peer views, then the Telegram archive. Daily color
  and Idea targeting light up once those seams exist.

Each phase = implement the relevant `data.py` function(s); the UI already handles
missing products gracefully (empty tabs show "no activity").

## 10. The AI analyst — compliance decision required

The **Analyst ✨** tab uses Claude Opus 5.5 to summarize a client's activity and
answer live questions. It reasons only over the context the page already computed
(no separate data access). **But sending that context to a model is a data-egress
decision**, because the context includes client names and positions.

- **Prototype (today):** calls the Anthropic API directly. This is fine *only*
  because the prototype runs on mock data — no real client information.
- **Production requirement:** the model call must **not** send client data outside
  Databricks. Options, in order of preference:
  1. **Databricks Model Serving** hosting Claude (or an FMAPI-served model) inside
     the environment — the context never leaves the tenant. *(Preferred.)*
  2. An **approved, in-VPC Claude endpoint** / gateway already cleared for client data.
  3. **Anonymize before sending** — replace entity names with tokens
     (`ENTITY_1`, …) and drop identifiers, re-mapping on display. Weaker; needs
     sign-off.

`llm.py` is isolated precisely so this swap is a one-file change: point it at the
approved endpoint and the rest of the app is unaffected. **This needs a data-
governance decision before the analyst goes live on real clients.**

### 10.1 Every model call in the app

The same egress rule covers all of them. Each one is isolated, so moving to the
approved endpoint is the same one-file swap:

| Feature | File | What is sent |
|---|---|---|
| Analyst summary + chat | `llm.py` | Page context for one client, including CRM sections (peers stay anonymous) |
| Chat → trade-idea extraction | `chat_intel.py` | Raw Telegram messages (**the most sensitive input**) |
| Idea-targeting pitch drafts | `targeting.py` | The idea plus the reasons for the top-ranked clients (client names) |
| Daily color polish | `daily_color.py` | The aggregated note only (no names, no $). It is complete without the model |

## 11. Phase-2 caveats engineering should know

- **Expiries netting:** the mock never closes options early, so `expiries.live()`
  treats every traded leg as still open until expiry. Production must **net option
  legs against `get_positions`** (or an OMS close flag) so that structures closed
  early don't show up as live pin risk.
- **Targeting model:** a 12-feature, sign-constrained ridge logistic regression
  (numpy, no sklearn). It starts from documented prior weights until there is enough
  pitch history. Mock evaluation on a time split gives AUC 0.64 vs. 0.51 for the
  take-rate baseline, and the top third converts at 50% vs. 35%. Re-check this
  on real data before anyone relies on the ranking.

## 12. Peer intelligence — privacy rules

Sales wants to say "other macro funds are doing X" without revealing who.

### 12.1 Rules (enforced in `peers.py`, reused by the daily color)
- **k-anonymity:** any cell backed by fewer than `K_MIN = 5` distinct peers is
  suppressed. A cohort smaller than 5 falls back to the next level up
  (sub-segment → segment → whole book).
- **Dominance:** no single peer may be more than `DOMINANCE = 60%` of a cell's
  notional. Without this, "macro funds bought $400M SOL" could be one fund.
- Peers are described with **ratios and directions** ("up ~3.4×", "62% buyers"),
  never names. The client being viewed is always excluded from their own cohort.

### 12.2 Segment taxonomy (needed on the Salesforce Account)
| Segment | Sub-segments |
|---|---|
| Hedge Fund | Macro, Multi-Strategy, Quant, Crypto Discretionary |
| Prop Trading | HFT, Prop |
| Market Maker | Crypto-Native, TradFi Prop |
| Asset Manager | Digital Asset Manager, Traditional AM |
| Corporate | Treasury, Miner |

## 13. Telegram chat — archive bot + pitch tracking

- A FalconX bot is added to each client group. It **only archives** messages and
  never posts. A coverage-maintained table maps `chat_id → parent`.
- A nightly job (`chat_intel.extract`, structured output validated with
  Pydantic) pulls out each idea we pitched (token, direction, levels, size,
  horizon, option legs) and the client's reaction (Done / Passed + reason / Later /
  No response). The results go to `get_trade_ideas`.
- `pitches.py` then matches ideas to actual trades (time window + token/direction/
  structure fit), marks them to market, and produces the scorecard and call talking
  points: "we pitched X, it's up N%, you didn't take it". A pass that would have lost
  money is shown as "dodged a loss".
- Compliance: chat archiving needs client consent and a retention policy. The
  extractor is an LLM call over raw messages (§10.1).

## 14. CRM notes (the Bloomberg "Pros" pattern)

After a call, a salesperson writes free text. `tags.py` turns it into hashtags
deterministically ("wants a condor into Sept on ETH" → `#ETH #IronCondor #Options
#Vol`), so every tag can be traced back to the words that produced it. A negation
is kept as a negative tag ("not set up for options" → `-#Options`), so targeting
doesn't read it as interest. The taxonomy is plain data and can move to a table
edited by sales ops. Over time the notes become the queryable client memory
("who has talked about vol?", "who complained about pricing?").

## 15. Idea targeting ("who will bite?")

Given an idea, `targeting.py` scores every client on point-in-time features: trades
the product / token / structure, notes and chat interest, past take-rate on similar
ideas, peer uptake, recency, and set-up status. Clients who aren't set up for the
product are listed as "Onboard first" rather than ranked. Each row explains
*why* and what to watch out for. After a pitch, "pitched to 7, 2 replied" goes into a
post-mortem that refits the weights, which closes the learning loop.

## 16. Daily color — 4:30pm ET

`daily_color.py` builds the note for the last color day (4:30pm → 4:30pm ET,
DST-aware): moves sized in σ vs. 30 days, token flow vs. the 20-day average,
segment buy/sell skew, options mix, and "split views" where segments disagree. The note is
**client-facing**, so it carries **ratios and percentages only**: no desk dollar
volumes and no names. Every segment and strategy line passes the §12.1 rules. The
per-client version excludes that client's own flow. The rep sheet (which clients to
send which line to) is internal. `daily_color.run()` is a CLI entry point meant to
run as a scheduled **Databricks Job** at 4:30pm ET. **Compliance should sign off on
the note template once before it goes to clients.**

## 17. Open questions for engineering

1. Does a parent↔entity mapping exist anywhere, or do we build it? Preferred source?
2. Exact table names / Genie space handles for derivatives, options, credit, FX.
3. Do option legs carry a parent-order / structure id we can expose as `structure_id`?
4. Are marks, greeks, and LTV pre-computed in those tables, or do we compute them?
5. Achievable refresh latency per product?
6. Any fields that access control forbids surfacing even to the desk?
7. **AI analyst:** is there an approved in-Databricks Claude/LLM endpoint we should
   target (see §10), or do we gate the analyst until one exists?
8. **Segments:** can the §12.2 segment / sub-segment fields be added to the
   Salesforce Account, and who maintains them?
9. **Telegram:** who owns the archive bot, the consent process and the
   `chat_id → parent` mapping (§13)?
10. **Notes table:** where should `crm_notes` live, and should notes sync two-way
    with Salesforce Activities (§5.13)?
11. **Options closes:** is there an OMS close / position-netting source for the
    expiries dashboard (§11)?
