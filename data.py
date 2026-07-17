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
