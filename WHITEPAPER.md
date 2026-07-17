# White Paper — Client 360 Dashboard

**Author:** David Pereira (RevOps)
**For:** Data Engineering / Databricks platform team
**Date:** 2026-07-16
**Status:** Draft for review
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
- **The AI analyst is the one feature that needs a compliance decision** — see §11.
  In the prototype it calls the Anthropic API directly (fine: mock data, no real
  client names). In production it must run through an in-environment model (Databricks
  model serving / an approved Claude endpoint) so no client data leaves Databricks.

## 4. What the app shows

The prototype implements all of the below. Layout: a client selector + a
per-entity focus filter, flexible date controls (quick lookback in days/weeks/
months/**years**, or a **custom start/end range**), a cross-product KPI bar, then
seven tabs.

| Section | Content | Backing data |
|---|---|---|
| **Header** | Parent name, tier, coverage, client-since, entity chips, product-coverage matrix | `get_entities`, derived |
| **KPI bar** | Notional traded, revenue to FX, blended bps, open-position value, unrealized P&L, credit drawn | `overview_kpis` (derived) |
| **Overview** | Revenue by product, notional over time (stacked), product-activity table, net token flow, **client-vs-desk positioning** | `product_activity`, `volume_timeseries`, `token_flow`, `get_desk_benchmark` |
| **Positions & Risk** | Gross/long/short exposure, exposure by product, net exposure by token, full position detail (incl. funding, delta, vega) | `get_positions` |
| **Options** | **Multi-leg structure detection** — legs grouped by order and classified (bull/bear vertical spreads, straddle, strangle, risk reversal, butterfly, iron condor, calendar, covered call); strategy mix, directional-bias breakdown, per-structure leg detail, entity + strategy filters | `get_trades` + `option_structures` (derived) |
| **Trades** | Filterable unified blotter (product / side / token), size distribution, avg bps by product | `get_trades` |
| **Credit** | Facilities, principal/drawn/collateral, LTV, rate, maturity, margin-health status, near-term alerts | `get_credit` |
| **Relationship** | Auto meeting brief, Gong call history, **live token news** | `get_gong`, `get_news`, derived |
| **Analyst ✨** | **Opus 4.8**: one-click activity summary for the period + live chat, grounded in everything on the page | `llm.py` over the derived context |

## 5. The data contract

The app talks to data **only** through the functions in `data.py`. Each is a seam:
today it returns mock data; going live means replacing the body with a Databricks
query that returns **the documented columns and dtypes**. The UI does not change.

Functions marked **[DERIVED]** are pure pandas on top of the others — **no query
needed** (that includes options structure detection and the desk comparison).
Engineering implements the seven base functions below.

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

Each phase = implement the relevant `data.py` function(s); the UI already handles
missing products gracefully (empty tabs show "no activity").

## 10. The AI analyst — compliance decision required

The **Analyst ✨** tab uses Claude Opus 4.8 to summarize a client's activity and
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

## 11. Open questions for engineering

1. Does a parent↔entity mapping exist anywhere, or do we build it? Preferred source?
2. Exact table names / Genie space handles for derivatives, options, credit, FX.
3. Do option legs carry a parent-order / structure id we can expose as `structure_id`?
4. Are marks, greeks, and LTV pre-computed in those tables, or do we compute them?
5. Achievable refresh latency per product?
6. Any fields that access control forbids surfacing even to the desk?
7. **AI analyst:** is there an approved in-Databricks Claude/LLM endpoint we should
   target (see §10), or do we gate the analyst until one exists?
