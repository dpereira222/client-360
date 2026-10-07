"""
============================================================================
 CLIENT 360 — DATA CONTRACT  (this file is the handoff to engineering)
============================================================================

Every function below is a SEAM. Today each one returns mock data. To go live,
an engineer replaces ONLY the body of each function with a Databricks query that
returns a DataFrame matching the documented OUTPUT SCHEMA. The UI (app.py) never
changes — it only ever sees these DataFrames.

Rules that came out of the Jul-10 architecture review:
  • All data access stays inside Databricks (SQL warehouse / Genie tables).
    No client data leaves the environment. See WHITEPAPER.md §Security.
  • Column names and dtypes in the OUTPUT SCHEMA are the API. Keep them exact.
  • Functions marked  [DERIVED]  need NO new query — they are pure pandas on top
    of the base functions. Engineers can ignore them.

Flip MOCK_MODE to False once the queries are wired.
============================================================================
"""
from __future__ import annotations
import os
from datetime import date, timedelta

import pandas as pd

import mock

MOCK_MODE = os.getenv("CLIENT360_MOCK", "1") == "1"

# When MOCK_MODE is False, engineers open connections here. Pattern already in
# use elsewhere in this repo (see revenue-app / client-brief):
#
#   from databricks.sdk.core import Config
#   from databricks import sql
#   def _run(q: str) -> pd.DataFrame:
#       cfg = Config()
#       wh = os.getenv("DATABRICKS_WAREHOUSE_ID", "9118f4f27caf0ffe")
#       conn = sql.connect(server_hostname=cfg.host,
#                          http_path=f"/sql/1.0/warehouses/{wh}",
#                          credentials_provider=lambda: cfg.authenticate)
#       try:
#           with conn.cursor() as c:
#               c.execute(q)
#               cols = [d[0] for d in c.description]
#               return pd.DataFrame(c.fetchall(), columns=cols)
#       finally:
#           conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. CLIENT / ENTITY RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────
def search_parents() -> list[str]:
    """
    All top-level client relationships the user can search.

    SOURCE  : client master / CRM dimension (Salesforce account or a curated
              entity-mapping table). Needs a parent<->legal-entity mapping —
              this is the piece that does NOT exist in trade_log today and is the
              #1 ask for engineering (§Entity Resolution in the white paper).
    OUTPUT  : list[str]  — distinct parent client names.
    """
    if MOCK_MODE:
        return mock.all_parents()
    raise NotImplementedError


def get_entities(parent: str) -> pd.DataFrame:
    """
    Every legal entity that rolls up to a parent client.

    SOURCE  : entity mapping table (parent_name -> entity). Ideally sourced from
              onboarding/KYC. Must reconcile the many counterparty spellings in
              the trade tables back to one entity_id.
    OUTPUT SCHEMA:
        entity_id     str
        entity_name   str
        parent_name   str
        entity_type   str    e.g. 'Master Fund', 'Digital', 'Management Co'
        status        str    'Active' | 'Dormant' | 'Closed'
        onboarded     str    ISO date
    """
    if MOCK_MODE:
        return mock.entities(parent)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 2. UNIFIED TRADE BLOTTER  (spot + derivs + options + credit + FX)
# ─────────────────────────────────────────────────────────────────────────────
def get_trades(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    One row per executed trade across ALL product lines, for every entity under
    the parent, with trade_time in [start, end]. Core fact table for the dashboard.

    SOURCE  : UNION of the per-product Genie tables identified in the review:
                spot        -> main.otc_analytics.trade_log (trade_type='order')
                derivatives -> <derivatives trades table>   (Genie: derivs)
                options     -> <options trades table>       (Genie: options)
                credit      -> <credit/financing tickets>
                fx          -> <fx trades table>
              Normalize each into the common schema below. Filter to the
              parent's entity_ids and pdate BETWEEN start AND end.
    OUTPUT SCHEMA:
        trade_time    datetime64
        entity_id     str
        entity_name   str
        product       str     one of Spot|Derivatives|Options|Credit|FX
        instrument    str     e.g. 'BTC/USD', 'ETH-PERP', 'BTC-29AUG26-95000-C'
        token         str     base token
        side          str     'Buy' | 'Sell'
        notional_usd  float    USD notional of the trade
        price         float    execution price (option premium for option legs)
        bps           float    spread/margin captured, in bps
        revenue_usd   float    revenue to FalconX (notional * bps / 1e4)
        venue         str
        qty           float    contracts / units
        structure_id  str|None parent order id linking legs of one multi-leg
                               options structure (enables strategy detection).
                               Null for single-instrument trades.
    """
    if MOCK_MODE:
        df = mock.trades(parent)
        s = pd.Timestamp(start)
        e = pd.Timestamp(end) + pd.Timedelta(days=1)
        tt = pd.to_datetime(df["trade_time"])
        return df[(tt >= s) & (tt < e)].reset_index(drop=True)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 3. CURRENT POSITIONS  (spot holdings + derivs + options greeks)
# ─────────────────────────────────────────────────────────────────────────────
def get_positions(parent: str) -> pd.DataFrame:
    """
    Live open positions to monitor exposure in one place — the headline ask from
    the review ("aggregate credit, derivatives and spot to monitor positions").

    SOURCE  : position/risk snapshot tables per product (latest as-of). Options
              greeks come from the options risk table.
    OUTPUT SCHEMA:
        product         str
        instrument      str
        token           str
        direction       str    'Long' | 'Short'
        qty             float
        avg_price       float
        mark_price      float
        notional_usd    float
        unrealized_pnl  float
        funding_8h_bps  float | None   (derivatives only)
        delta           float | None   (options only)
        vega_usd        float | None   (options only)
    """
    if MOCK_MODE:
        return mock.positions(parent)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 4. CREDIT BOOK
# ─────────────────────────────────────────────────────────────────────────────
def get_credit(parent: str) -> pd.DataFrame:
    """
    Open credit facilities / financing deals with collateral and margin health.

    SOURCE  : credit desk deal table + latest collateral marks. LTV and
              days_to_maturity can be computed in SQL or left to [DERIVED].
    OUTPUT SCHEMA:
        deal_id           str
        entity_name       str
        facility_type     str   'Term Loan'|'Revolving Facility'|'Repo'|...
        collateral_token  str
        principal_usd     float
        drawn_usd         float
        collateral_usd    float
        ltv_pct           float
        rate_bps          float
        start             str   ISO date
        maturity          str   ISO date
        days_to_maturity  int
        status            str   'good'|'warning'|'serious'|'critical'
    Empty DataFrame if the client has no credit relationship.
    """
    if MOCK_MODE:
        return mock.credit(parent)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 5. RELATIONSHIP INTEL
# ─────────────────────────────────────────────────────────────────────────────
def get_gong(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Sales calls with AI summaries, most recent first.

    SOURCE  : main.salesforce.gong_gong_call_c  (already available; see
              client-brief/app.py for the working query and name-matching).
    OUTPUT SCHEMA:
        title str, call_date str(ISO), participants str, brief str,
        key_points str, next_steps str, duration_sec int
    """
    if MOCK_MODE:
        days = max(30, (date.today() - pd.Timestamp(start).date()).days)
        return mock.gong(parent, days)
    raise NotImplementedError


# Token → search synonyms so news matching is not just the ticker.
_TOKEN_SYNONYMS = {
    "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"],
    "SOL": ["solana", "sol"], "XRP": ["ripple", "xrp"], "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"], "ARB": ["arbitrum", "arb"], "OP": ["optimism"],
    "USDC": ["usdc", "circle"], "USDT": ["tether", "usdt"],
}
_NEWS_CACHE: dict = {}
_NEWS_TTL = 900  # 15 min


def get_news(tokens: list[str], limit: int = 8) -> list[dict]:
    """
    Live token-relevant headlines from public crypto RSS. NOT client data, so this
    is the one call allowed to reach outside Databricks. Filters to the client's
    tokens, dedupes, sorts newest-first. Cached 15 min.
    OUTPUT: list of {title, url, published, source, ts}.
    """
    import time
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    import requests

    key = ",".join(sorted(tokens))
    hit = _NEWS_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _NEWS_TTL:
        return hit[1]

    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://cryptoslate.com/feed/",
    ]
    wanted = set()
    for t in tokens:
        wanted.update(_TOKEN_SYNONYMS.get(t.upper(), [t.lower()]))

    seen, items = set(), []
    for feed in feeds:
        try:
            r = requests.get(feed, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = (it.findtext("pubDate") or "").strip()
                if not title or title.lower() in seen:
                    continue
                try:
                    dt = parsedate_to_datetime(pub)
                    ts = dt.timestamp()
                    pub_disp = dt.strftime("%b %d, %Y")
                except Exception:
                    ts, pub_disp = 0, pub[:16]
                relevant = not wanted or any(w in title.lower() for w in wanted)
                if relevant:
                    seen.add(title.lower())
                    items.append({"title": title, "url": link, "published": pub_disp,
                                  "source": feed.split("/")[2].replace("www.", ""),
                                  "ts": ts})
        except Exception:
            continue

    items.sort(key=lambda x: x["ts"], reverse=True)
    out = items[:limit]
    # Fallback: if token filter matched nothing, return latest general headlines.
    if not out and tokens:
        out = get_news([], limit)
    _NEWS_CACHE[key] = (time.time(), out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 6. DESK-WIDE BENCHMARK  (how the rest of the book is positioned)
# ─────────────────────────────────────────────────────────────────────────────
def get_desk_benchmark(tokens: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Desk-wide net flow per token across ALL clients in the window — the baseline
    to compare one client against ("is this client with or against the desk?").

    SOURCE  : same unified trade tables as get_trades, but NOT filtered to one
              client — aggregate over all counterparties, grouped by token.
              Client names are never returned, only aggregates (privacy-safe).
    OUTPUT SCHEMA:
        token str, desk_buy_usd float, desk_sell_usd float,
        desk_net_usd float, num_clients int
    """
    if MOCK_MODE:
        return mock.desk_benchmark(tokens, start, end)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 7. CLIENT PROFILE + BOOK UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────
def get_profile(parent: str) -> dict:
    """
    Relationship metadata for the header, peer cohorts, and targeting.

    SOURCE  : Salesforce Account (tier, coverage, segment taxonomy — see
              WHITEPAPER §12.2) joined to the entity mapping.
    OUTPUT  : dict with keys
        parent str, tier str, since str(ISO), coverage str (salesperson),
        segment str, sub_segment str, contact str (main client contact),
        products list[str] (products the client is SET UP for, from onboarding)
    """
    if MOCK_MODE:
        c = mock.CLIENTS[parent]
        return {"parent": parent, "tier": c["tier"], "since": c["since"],
                "coverage": c["coverage"], "segment": c["segment"],
                "sub_segment": c["sub_segment"], "contact": c.get("contact", ""),
                "products": list(c["products"])}
    raise NotImplementedError


def get_book_trades(start: str, end: str) -> pd.DataFrame:
    """
    The unified blotter for EVERY client in [start, end] — the input to the
    expiries dashboard, the 4:30pm daily color, and idea targeting.

    SOURCE  : same UNION as get_trades, without the parent filter, plus the
              parent_name from the entity mapping.
    OUTPUT SCHEMA: get_trades schema + `parent` str.
    PRIVACY : internal only. Anything shared with clients (daily color) is
              aggregated and anonymized downstream — never these rows.
    """
    if MOCK_MODE:
        frames = [get_trades(p, start, end).assign(parent=p) for p in mock.all_parents()]
        frames = [f for f in frames if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 8. PEER INTELLIGENCE  (anonymous — see peers.py for the privacy rules)
# ─────────────────────────────────────────────────────────────────────────────
def get_peer_activity(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Weekly activity for every OTHER client, anonymized — the cohort feed.

    SOURCE  : a Databricks VIEW over the unified blotter, grouped weekly,
              with counterparty replaced by a salted-hash peer_id and the
              requesting client excluded. Names never leave the view.
    OUTPUT SCHEMA:
        peer_id str, segment str, sub_segment str, tier str,
        week date (Monday), product str, token str,
        notional_usd float, buy_usd float, sell_usd float,
        trades int, revenue_usd float
    """
    if MOCK_MODE:
        import mock_peers
        return mock_peers.peer_activity(parent, start, end)
    raise NotImplementedError


def get_peer_strategies(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Weekly options-structure counts per anonymous peer (options.py run over
    each peer's option legs inside Databricks).
    OUTPUT SCHEMA:
        peer_id str, segment str, sub_segment str, tier str, week date,
        strategy str, bias str, token str, structures int, notional_usd float
    """
    if MOCK_MODE:
        import mock_peers
        return mock_peers.peer_strategies(parent, start, end)
    raise NotImplementedError


def get_desk_idea_uptake(exclude_parent: str | None = None) -> pd.DataFrame:
    """
    For each desk-wide trade idea: how many clients per segment were pitched
    it and how many executed (pitches.match_executions run book-wide, nightly).
    exclude_parent drops that client's own rows (peer view of "who took it").
    OUTPUT SCHEMA:
        desk_idea_id str, headline str, segment str, sub_segment str,
        pitched int, executed int
    """
    if MOCK_MODE:
        import mock_peers
        return mock_peers.desk_idea_uptake(exclude_parent)
    raise NotImplementedError


def get_desk_flow(start: str, end: str) -> pd.DataFrame:
    """
    Trade-level flow for the WHOLE desk in [start, end), anonymized — the input
    to the 4:30pm daily color. Timestamps, not dates: the color day runs
    4:30pm ET → 4:30pm ET, so the caller passes exact UTC cut-offs.

    SOURCE  : a Databricks VIEW over the unified blotter (same one as
              get_peer_activity) at trade grain; option legs collapsed to one
              row per structure via options.py inside Databricks. Counterparty
              replaced by the salted-hash peer_id. Names never leave the view.
    OUTPUT SCHEMA:
        trade_time    datetime64 (UTC, naive)
        peer_id str, segment str, sub_segment str, tier str,
        product str, token str, side str ('Buy'|'Sell'),
        notional_usd float,
        strategy str|None, bias str|None   (options structures only)
    PRIVACY : still internal. daily_color.py applies k-anonymity + dominance
              before anything reaches a client-facing note.
    """
    if MOCK_MODE:
        import mock_peers
        return mock_peers.desk_flow(start, end)
    raise NotImplementedError


def peer_id_for(parent: str) -> str:
    """
    The salted-hash peer_id for one of OUR named clients — used only to EXCLUDE
    that client's own rows from desk/peer aggregates (e.g. the per-client daily
    color), never to re-identify anyone.

    SOURCE  : same salted hash the anonymized views use, exposed as a scalar UDF
              or a tiny parent→peer_id lookup (sales-entitled users only).
    OUTPUT  : str, e.g. 'P-1A2B3C'
    """
    if MOCK_MODE:
        import mock_peers
        return mock_peers.peer_id(parent)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLIENT CHAT  (Telegram archive — WHITEPAPER §13)
# ─────────────────────────────────────────────────────────────────────────────
def get_chat_messages(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Archived messages from the client's FalconX Telegram group(s).

    SOURCE  : the Telegram bot's archive table (bot only archives; it never
              posts). chat_id → parent via a coverage-maintained mapping.
    OUTPUT SCHEMA:
        chat_id int, chat_title str, msg_id int, ts datetime64,
        sender str, sender_role str ('FalconX' | 'Client'),
        text str, reply_to_msg_id float|NaN
    """
    if MOCK_MODE:
        import mock_chat
        return mock_chat.messages(parent, start, end)
    raise NotImplementedError


def get_trade_ideas(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Trade ideas pitched in chat + the client's verbal reaction. Produced by
    the nightly chat_intel.extract() job (same schema it returns).
    OUTPUT SCHEMA:
        idea_id str, parent str, chat_id int, ts datetime64, pitched_by str,
        kind str ('Directional'|'Relative Value'|'Options'), token str,
        token2 str|None, direction str ('Long'|'Short'),
        product str ('Spot'|'Derivatives'|'Options'), structure str|None,
        legs_json str|None  (JSON list of {right, strike, side, qty_mult, expiry}),
        entry_px float, target_px float|None, stop_px float|None,
        size_usd float, horizon_days int, thesis str,
        desk_idea_id str|None  (set when the idea was a desk-wide broadcast),
        msg_id int, reaction str ('Done'|'Passed'|'Later'|'No response'),
        reason str|None, reaction_ts datetime64|None, reaction_text str|None,
        callback_level float|None
    Execution is NOT in this table — pitches.py infers it from get_trades.
    """
    if MOCK_MODE:
        import mock_chat
        return mock_chat.trade_ideas(parent, start, end)
    raise NotImplementedError


def get_rfqs(parent: str, start: str, end: str) -> pd.DataFrame:
    """
    Client price requests in chat, our quote, and the outcome.
    OUTPUT SCHEMA:
        rfq_id str, parent str, chat_id int, ts datetime64, token str,
        side str, qty float, notional_usd float, product str,
        quote_bid float, quote_ask float, quoted_by str,
        response str ('Done'|'Passed'|'No response'),
        reason str|None ('Too wide'|'Traded away'), response_ts datetime64|None
    """
    if MOCK_MODE:
        import mock_chat
        return mock_chat.rfqs(parent, start, end)
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 10. MARKET DATA  (not client data)
# ─────────────────────────────────────────────────────────────────────────────
def get_price_history() -> pd.DataFrame:
    """
    Daily close per token, ~3 years — marks pitched ideas and powers the
    daily color "big move" detection.
    SOURCE  : the desk's market-data table (or index prices).
    OUTPUT SCHEMA: date date, token str, close float
    """
    if MOCK_MODE:
        return mock.price_history()
    raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# 11. CRM NOTES  (the Pros pattern — the one WRITE path in the app)
# ─────────────────────────────────────────────────────────────────────────────
def get_notes(parent: str | None, start: str, end: str) -> pd.DataFrame:
    """
    Call / meeting notes logged by sales, newest first. parent=None returns
    the whole book (used by idea targeting and tag analytics).

    SOURCE  : a Delta table, e.g. main.client360.crm_notes, written by
              save_note(). Optionally backfilled from Salesforce Activities.
    OUTPUT SCHEMA:
        note_id str, parent str, note_ts datetime64, author str,
        meeting_type str ('Call'|'Meeting'|'Chat'|'Conference'),
        text str, tags list[str]  (tags.tag_list(text); '-#X' = negative),
        source str ('app'|'seed'|'salesforce')
    """
    if MOCK_MODE:
        import mock_notes
        return mock_notes.notes(parent, start, end)
    raise NotImplementedError


def save_note(parent: str, author: str, meeting_type: str, text: str,
              note_ts: str | None = None) -> dict:
    """
    Log a note. Tags are computed here (tags.py) so every writer — the app,
    a Slack shortcut, a Salesforce sync — gets identical tagging.

    SINK    : INSERT INTO the notes Delta table (append-only; edits are new
              rows referencing the old note_id, so history is auditable).
    RETURNS : the stored row (schema as get_notes).
    """
    import tags
    from datetime import datetime
    row = {"parent": parent, "author": author, "meeting_type": meeting_type,
           "text": text.strip(), "tags": tags.tag_list(text),
           "note_ts": note_ts or datetime.now().isoformat(timespec="seconds")}
    if MOCK_MODE:
        import mock_notes
        return mock_notes.save(row)
    raise NotImplementedError


# ═════════════════════════════════════════════════════════════════════════════
#  [DERIVED]  — pure pandas on the functions above. No new queries needed.
# ═════════════════════════════════════════════════════════════════════════════
def overview_kpis(trades: pd.DataFrame, positions: pd.DataFrame,
                  credit: pd.DataFrame) -> dict:
    """Cross-product headline numbers for the KPI bar. [DERIVED]"""
    notional = float(trades["notional_usd"].sum()) if not trades.empty else 0.0
    revenue = float(trades["revenue_usd"].sum()) if not trades.empty else 0.0
    blended_bps = (revenue / notional * 10000) if notional else 0.0
    net_pos = float(positions["notional_usd"].sum()) if not positions.empty else 0.0
    upnl = float(positions["unrealized_pnl"].sum()) if not positions.empty else 0.0
    credit_out = float(credit["drawn_usd"].sum()) if not credit.empty else 0.0
    products = sorted(trades["product"].unique().tolist()) if not trades.empty else []
    return {
        "notional": notional, "revenue": revenue, "blended_bps": blended_bps,
        "net_position": net_pos, "unrealized_pnl": upnl,
        "credit_outstanding": credit_out, "products_active": products,
        "trade_count": int(len(trades)),
    }


def product_activity(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-product notional / revenue / trades / avg bps. [DERIVED]"""
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby("product").agg(
        notional_usd=("notional_usd", "sum"),
        revenue_usd=("revenue_usd", "sum"),
        trades=("product", "size"),
        avg_bps=("bps", "mean"),
    ).reset_index()
    return g.sort_values("revenue_usd", ascending=False)


def volume_timeseries(trades: pd.DataFrame, grain: str = "W") -> pd.DataFrame:
    """Notional over time, split by product (long form). [DERIVED]"""
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["bucket"] = pd.to_datetime(df["trade_time"]).dt.to_period(grain).dt.start_time
    g = df.groupby(["bucket", "product"], as_index=False)["notional_usd"].sum()
    return g


def token_flow(trades: pd.DataFrame) -> pd.DataFrame:
    """Net buy/sell by token (for directional skew). [DERIVED]"""
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["buy"] = df["notional_usd"].where(df["side"] == "Buy", 0.0)
    df["sell"] = df["notional_usd"].where(df["side"] == "Sell", 0.0)
    g = df.groupby("token").agg(
        buy_usd=("buy", "sum"),
        sell_usd=("sell", "sum"),
        total_usd=("notional_usd", "sum"),
    ).reset_index()
    g["net_usd"] = g["buy_usd"] - g["sell_usd"]
    return g.sort_values("total_usd", ascending=False)


def option_structures(trades: pd.DataFrame,
                      positions: pd.DataFrame | None = None) -> list[dict]:
    """Group option legs into named strategies (condor, put spread, …). [DERIVED]
    Uses options.py. Returns display-ready structure dicts, newest first."""
    import options
    if trades.empty:
        return []
    opt = trades[trades["product"] == "Options"]
    if opt.empty:
        return []
    spot = positions.to_dict("records") if positions is not None and not positions.empty else None
    return options.summarize(opt.to_dict("records"), spot)


def desk_comparison(client_flow: pd.DataFrame, desk: pd.DataFrame) -> pd.DataFrame:
    """Join a client's per-token net flow against the desk baseline. [DERIVED]
    client_flow: output of token_flow(). desk: get_desk_benchmark()."""
    if client_flow.empty or desk.empty:
        return pd.DataFrame()
    m = client_flow.merge(desk, on="token", how="left")
    rows = []
    for _, r in m.iterrows():
        cl_net, desk_net = float(r["net_usd"]), float(r.get("desk_net_usd") or 0)
        cl_dir = "Buying" if cl_net > 0 else "Selling"
        desk_dir = "Buying" if desk_net > 0 else "Selling"
        aligned = (cl_net > 0) == (desk_net > 0)
        rows.append({
            "token": r["token"], "client_net_usd": cl_net, "client_dir": cl_dir,
            "desk_net_usd": desk_net, "desk_dir": desk_dir,
            "num_clients": int(r.get("num_clients") or 0),
            "signal": "Aligned" if aligned else "Contrarian",
        })
    return pd.DataFrame(rows)


# ── Date window helpers ───────────────────────────────────────────────────────
TODAY = date(2026, 7, 16)  # demo "today"; live impl uses date.today()


def window(amount: int, unit: str) -> tuple[str, str]:
    """Quick lookback → (start, end) ISO. unit ∈ {Days, Weeks, Months, Years}."""
    mult = {"Days": 1, "Weeks": 7, "Months": 30, "Years": 365}[unit]
    start = (TODAY - timedelta(days=amount * mult)).isoformat()
    return start, TODAY.isoformat()
