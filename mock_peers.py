"""
Mock peer universe for Peer Intelligence ("what are other hedge funds doing?").

Fabricates ~75 ANONYMOUS institutional clients across a segment taxonomy, plus
the five named clients from mock.py, as WEEKLY aggregates per peer × product ×
token. Segment "playbooks" bake in a few recent trends (macro funds ramping SOL
and risk reversals, market makers drawing more credit, …) so the demo's talking
points have something real to find.

Engineering never reads this file — the contract is in data.py.
"""
from __future__ import annotations
import hashlib
import random
from datetime import timedelta

import pandas as pd

import mock

# Segment → sub-segments. Mirrors the taxonomy we'd want on the Salesforce
# Account (see WHITEPAPER §12.2).
SEGMENTS = {
    "Hedge Fund":    ["Macro", "Multi-Strategy", "Quant", "Crypto Discretionary"],
    "Prop Trading":  ["HFT", "Prop"],
    "Market Maker":  ["Crypto-Native", "TradFi Prop"],
    "Asset Manager": ["Digital Asset Manager", "Traditional AM"],
    "Corporate":     ["Treasury", "Miner"],
}

# How many anonymous peers per sub-segment. TradFi Prop is deliberately small
# (4 + Cumberland) so the k-anonymity suppression path shows up in the demo.
_N_SYNTH = {
    "Macro": 9, "Multi-Strategy": 7, "Quant": 8, "Crypto Discretionary": 10,
    "HFT": 5, "Prop": 6, "Crypto-Native": 6, "TradFi Prop": 4,
    "Digital Asset Manager": 7, "Traditional AM": 5, "Treasury": 5, "Miner": 4,
}

# Per-segment behaviour: P(active in product), token weights, option strategy
# preferences, and RECENT trend multipliers (last 8 weeks).
_PLAYBOOK = {
    "Hedge Fund": {
        "products": {"Spot": 0.95, "Derivatives": 0.9, "Options": 0.75, "Credit": 0.45, "FX": 0.5},
        "tokens": {"BTC": 5, "ETH": 4, "SOL": 2, "USDC": 1, "XRP": 0.6, "AVAX": 0.5},
        "strategies": {"Risk Reversal": 3, "Bull Call Spread": 2.5, "Bear Put Spread": 2,
                       "Long Straddle": 1.5, "Iron Condor": 1, "Calendar Spread": 1.2,
                       "Long Call": 1.5},
        "trend": {"tok": {"SOL": 2.3, "ETH": 0.8}, "prod": {"Options": 1.6, "Credit": 1.3},
                  "strat": {"Risk Reversal": 2.4, "Calendar Spread": 1.8}},
        "buy_share": {"SOL": 0.70, "BTC": 0.58, "ETH": 0.42},
        "size": (0.4, 1.3),
    },
    "Prop Trading": {
        "products": {"Spot": 0.9, "Derivatives": 1.0, "Options": 0.5, "Credit": 0.15, "FX": 0.7},
        "tokens": {"BTC": 4, "ETH": 4, "SOL": 3, "ARB": 1.5, "OP": 1.5},
        "strategies": {"Short Strangle": 2.5, "Iron Condor": 2, "Long Straddle": 1.5,
                       "Calendar Spread": 1.5},
        "trend": {"tok": {"ARB": 1.8, "OP": 1.5}, "prod": {"Derivatives": 1.3},
                  "strat": {"Short Strangle": 1.6}},
        "buy_share": {"ARB": 0.35, "OP": 0.40},
        "size": (0.6, 1.6),
    },
    "Market Maker": {
        "products": {"Spot": 1.0, "Derivatives": 0.9, "Options": 0.3, "Credit": 0.6, "FX": 0.9},
        "tokens": {"ETH": 4, "BTC": 4, "SOL": 2, "USDT": 3, "USDC": 3, "LINK": 1, "XRP": 1},
        "strategies": {"Short Strangle": 2, "Iron Condor": 1.5},
        "trend": {"tok": {"USDT": 1.4}, "prod": {"Credit": 1.9, "FX": 1.3}, "strat": {}},
        "buy_share": {},
        "size": (0.5, 1.4),
    },
    "Asset Manager": {
        "products": {"Spot": 1.0, "Derivatives": 0.4, "Options": 0.55, "Credit": 0.35, "FX": 0.3},
        "tokens": {"BTC": 6, "ETH": 4, "SOL": 1.5},
        "strategies": {"Covered Call": 3, "Long Call": 1.5, "Bull Call Spread": 1.5},
        "trend": {"tok": {"BTC": 1.4}, "prod": {"Options": 1.5},
                  "strat": {"Covered Call": 2.0}},
        "buy_share": {"BTC": 0.72, "ETH": 0.60, "SOL": 0.65},
        "size": (0.3, 1.0),
    },
    "Corporate": {
        "products": {"Spot": 1.0, "Derivatives": 0.2, "Options": 0.3, "Credit": 0.6, "FX": 0.5},
        "tokens": {"BTC": 6, "USDC": 2, "ETH": 1},
        "strategies": {"Covered Call": 2, "Bear Put Spread": 1.5},
        "trend": {"tok": {}, "prod": {"Credit": 1.5}, "strat": {"Bear Put Spread": 1.5}},
        "buy_share": {"BTC": 0.40},
        "size": (0.15, 0.6),
    },
}

_BPS = {"Spot": 4.5, "Derivatives": 6.0, "Options": 22.0, "Credit": 45.0, "FX": 2.2}
_BIAS = {"Risk Reversal": "bullish", "Bull Call Spread": "bullish", "Long Call": "bullish",
         "Bear Put Spread": "bearish", "Long Straddle": "long vol",
         "Calendar Spread": "long vol", "Short Strangle": "short vol",
         "Iron Condor": "short vol", "Covered Call": "income / capped upside",
         "Short Put": "bullish", "Long Butterfly": "neutral"}
TREND_WEEKS = 8
HIST_WEEKS = 160


def peer_id(name: str) -> str:
    """Stable anonymous id. Prod: salted hash computed inside Databricks."""
    return "P-" + hashlib.sha1(f"c360-salt|{name}".encode()).hexdigest()[:6].upper()


def _weeks() -> list:
    last = mock.TODAY - timedelta(days=mock.TODAY.weekday())  # Monday of this week
    return [last - timedelta(weeks=i) for i in range(HIST_WEEKS)][::-1]


def roster() -> list[dict]:
    """Every peer (synthetic + named): id, segment, sub_segment, tier, size."""
    out = []
    for seg, subs in SEGMENTS.items():
        for sub in subs:
            for i in range(_N_SYNTH[sub]):
                r = random.Random(f"peer|{sub}|{i}")
                lo, hi = _PLAYBOOK[seg]["size"]
                size = r.uniform(lo, hi)
                out.append({"name": f"{sub}#{i}", "peer_id": peer_id(f"{sub}#{i}"),
                            "segment": seg, "sub_segment": sub,
                            "tier": "Platinum" if size > 1.1 else "Gold" if size > 0.6 else "Silver",
                            "size": size, "named": False})
    for name, c in mock.CLIENTS.items():
        out.append({"name": name, "peer_id": peer_id(name), "segment": c["segment"],
                    "sub_segment": c["sub_segment"], "tier": c["tier"],
                    "size": c["size"], "named": True})
    return out


_ACT_CACHE: dict = {}


def activity() -> pd.DataFrame:
    """All peers, all weeks. Columns: peer_id, segment, sub_segment, tier, week,
    product, token, notional_usd, buy_usd, sell_usd, trades, revenue_usd."""
    if "all" in _ACT_CACHE:
        return _ACT_CACHE["all"]
    weeks = _weeks()
    rows = []
    for p in roster():
        if p["named"]:
            rows.extend(_named_rows(p))
            continue
        pb = _PLAYBOOK[p["segment"]]
        r = random.Random(f"act|{p['name']}")
        prods = [k for k, pr in pb["products"].items() if r.random() < pr] or ["Spot"]
        toks = list(pb["tokens"])
        k = min(len(toks), r.randint(2, 4))
        mine = []
        while len(mine) < k:
            t = r.choices(toks, weights=[pb["tokens"][x] for x in toks])[0]
            if t not in mine:
                mine.append(t)
        # Trend tokens: most of the segment picks them up recently.
        late = [t for t in pb["trend"]["tok"] if pb["trend"]["tok"][t] > 1 and t not in mine
                and r.random() < 0.7]
        for wi, wk in enumerate(weeks):
            recent = wi >= HIST_WEEKS - TREND_WEEKS
            for prod in prods:
                pm = pb["trend"]["prod"].get(prod, 1.0) if recent else 1.0
                toks_now = mine + (late if recent else [])
                for tok in toks_now:
                    if prod == "Options" and tok in ("USDC", "USDT"):
                        continue
                    if r.random() < 0.35:          # not every week
                        continue
                    tm = pb["trend"]["tok"].get(tok, 1.0) if recent else 1.0
                    base = 9e6 * p["size"] * {"Spot": 1, "Derivatives": 1.2, "Options": 0.35,
                                              "Credit": 0.5, "FX": 0.6}[prod]
                    notional = base * r.lognormvariate(0, 0.6) * pm * tm
                    bs = pb["buy_share"].get(tok, 0.5) if recent else 0.5
                    buy_share = min(0.95, max(0.05, r.gauss(bs, 0.12)))
                    bps = max(0.4, r.gauss(_BPS[prod], _BPS[prod] * 0.3))
                    rows.append({
                        "peer_id": p["peer_id"], "segment": p["segment"],
                        "sub_segment": p["sub_segment"], "tier": p["tier"], "week": wk,
                        "product": prod, "token": tok, "notional_usd": round(notional, 0),
                        "buy_usd": round(notional * buy_share, 0),
                        "sell_usd": round(notional * (1 - buy_share), 0),
                        "trades": max(1, int(notional / 2.5e6)),
                        "revenue_usd": round(notional * bps / 1e4, 0),
                    })
    df = pd.DataFrame(rows)
    _ACT_CACHE["all"] = df
    return df


def _named_rows(p: dict) -> list[dict]:
    """Aggregate a named client's real (mock) blotter into the weekly shape."""
    t = mock.trades(p["name"]).copy()
    tt = pd.to_datetime(t["trade_time"])
    t["week"] = (tt - pd.to_timedelta(tt.dt.weekday, unit="D")).dt.date
    t["buy"] = t["notional_usd"].where(t["side"] == "Buy", 0.0)
    t["sell"] = t["notional_usd"].where(t["side"] == "Sell", 0.0)
    g = t.groupby(["week", "product", "token"]).agg(
        notional_usd=("notional_usd", "sum"), buy_usd=("buy", "sum"),
        sell_usd=("sell", "sum"), trades=("product", "size"),
        revenue_usd=("revenue_usd", "sum")).reset_index()
    out = []
    for r in g.to_dict("records"):
        out.append({"peer_id": p["peer_id"], "segment": p["segment"],
                    "sub_segment": p["sub_segment"], "tier": p["tier"], **r})
    return out


_STRAT_CACHE: dict = {}


def strategies() -> pd.DataFrame:
    """Options structures per peer per week. Columns: peer_id, segment,
    sub_segment, tier, week, strategy, bias, token, structures, notional_usd."""
    if "all" in _STRAT_CACHE:
        return _STRAT_CACHE["all"]
    import options
    weeks = _weeks()
    act = activity()
    opt_peers = set(act[act["product"] == "Options"]["peer_id"])
    rows = []
    for p in roster():
        if p["peer_id"] not in opt_peers:
            continue
        if p["named"]:
            t = mock.trades(p["name"])
            for s in options.summarize(t[t["product"] == "Options"].to_dict("records")):
                ts = pd.Timestamp(s["trade_time"])
                rows.append({"peer_id": p["peer_id"], "segment": p["segment"],
                             "sub_segment": p["sub_segment"], "tier": p["tier"],
                             "week": (ts - timedelta(days=ts.weekday())).date(),
                             "strategy": s["classification"]["name"],
                             "bias": s["classification"]["bias"], "token": s["token"],
                             "structures": 1, "notional_usd": s["notional_usd"]})
            continue
        pb = _PLAYBOOK[p["segment"]]
        r = random.Random(f"strat|{p['name']}")
        names = list(pb["strategies"])
        favs = r.sample(names, k=min(len(names), r.randint(2, 3)))
        toks = [t for t in pb["tokens"] if t not in ("USDC", "USDT")]
        for wi, wk in enumerate(weeks):
            recent = wi >= HIST_WEEKS - TREND_WEEKS
            for sname in names:
                w = pb["strategies"][sname] * (1.0 if sname in favs else 0.25)
                if recent:
                    w *= pb["trend"]["strat"].get(sname, 1.0)
                if r.random() > min(0.9, 0.06 * w):
                    continue
                tok = r.choices(toks, weights=[pb["tokens"][x] for x in toks])[0]
                rows.append({"peer_id": p["peer_id"], "segment": p["segment"],
                             "sub_segment": p["sub_segment"], "tier": p["tier"],
                             "week": wk, "strategy": sname, "bias": _BIAS.get(sname, "—"),
                             "token": tok, "structures": 1,
                             "notional_usd": round(4e6 * p["size"] * r.lognormvariate(0, 0.5), 0)})
    df = pd.DataFrame(rows)
    _STRAT_CACHE["all"] = df
    return df


def profile(parent: str) -> dict:
    c = mock.CLIENTS[parent]
    return {"parent": parent, "segment": c["segment"], "sub_segment": c["sub_segment"],
            "tier": c["tier"], "coverage": c["coverage"], "since": c["since"],
            "contact": c.get("contact", ""), "peer_id": peer_id(parent)}


def _window(df: pd.DataFrame, col: str, start: str, end: str) -> pd.DataFrame:
    s, e = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    w = pd.to_datetime(df[col]).dt.date
    return df[(w >= s) & (w <= e)]


def peer_activity(parent: str, start: str, end: str) -> pd.DataFrame:
    df = activity()
    df = df[df["peer_id"] != peer_id(parent)]
    return _window(df, "week", start, end).reset_index(drop=True)


def peer_strategies(parent: str, start: str, end: str) -> pd.DataFrame:
    df = strategies()
    df = df[df["peer_id"] != peer_id(parent)]
    return _window(df, "week", start, end).reset_index(drop=True)


# ── Desk-idea uptake across the book ──────────────────────────────────────────
_UPTAKE_CACHE: dict = {}


def desk_idea_uptake(exclude: str | None = None) -> pd.DataFrame:
    """Per desk idea × segment / sub_segment: how many clients were pitched it
    and how many executed. Named clients come from their real (mock) chat +
    blotter via pitches.match_executions; anonymous peers are simulated with a
    take-rate driven by how well the idea fits their segment playbook.
    Columns: desk_idea_id, headline, segment, sub_segment, pitched, executed.
    `exclude`: a client's own rows are dropped — it's a peer view."""
    if "all" not in _UPTAKE_CACHE:
        _UPTAKE_CACHE["all"] = _uptake_rows()
    df = _UPTAKE_CACHE["all"]
    if exclude:
        df = df[df["peer_id"] != peer_id(exclude)]
    df = df.groupby(["desk_idea_id", "segment", "sub_segment"],
                    as_index=False)[["pitched", "executed"]].sum()
    heads = {f"DESK-{k:02d}": _desk_headline(d) for k, d in enumerate(__import__("mock_chat").DESK_IDEAS)}
    df["headline"] = df["desk_idea_id"].map(heads)
    return df


def _uptake_rows() -> pd.DataFrame:
    import mock_chat
    import pitches
    rows = []
    start = (mock.TODAY - timedelta(days=200)).isoformat()
    end = mock.TODAY.isoformat()
    for p in roster():
        if p["named"]:
            ideas = mock_chat.trade_ideas(p["name"], start, end)
            ideas = ideas[ideas["desk_idea_id"].notna()]
            if ideas.empty:
                continue
            m = pitches.match_executions(ideas, mock.trades(p["name"]))
            ideas = ideas.merge(m[["idea_id", "executed"]], on="idea_id")
            for _, i in ideas.iterrows():
                rows.append({"desk_idea_id": i["desk_idea_id"], "peer_id": p["peer_id"],
                             "segment": p["segment"],
                             "sub_segment": p["sub_segment"], "pitched": 1,
                             "executed": int(bool(i["executed"]))})
            continue
        pb = _PLAYBOOK[p["segment"]]
        r = random.Random(f"uptake|{p['name']}")
        for k, d in enumerate(mock_chat.DESK_IDEAS):
            if r.random() > 0.55:          # not every idea goes to every client
                continue
            prod = "Options" if d["kind"] == "Options" else d.get("product", "Derivatives")
            fit = pb["products"].get(prod, 0.3) * (0.6 + 0.15 * pb["tokens"].get(d["token"], 0))
            if d["kind"] == "Options":
                fit *= 0.5 + 0.2 * pb["strategies"].get(d.get("structure"), 0)
            take = min(0.8, 0.12 + 0.35 * fit)
            rows.append({"desk_idea_id": f"DESK-{k:02d}", "peer_id": p["peer_id"],
                         "segment": p["segment"],
                         "sub_segment": p["sub_segment"], "pitched": 1,
                         "executed": int(r.random() < take)})
    return pd.DataFrame(rows)


def _desk_headline(d: dict) -> str:
    if d["kind"] == "Options":
        return f"{d['token']} {d['structure'].lower()}"
    if d["kind"] == "Relative Value":
        return f"Long {d['token']} / short {d['token2']}"
    return f"{d['direction']} {d['token']} {'spot' if d.get('product') == 'Spot' else 'perp'}"


# ── Daily, trade-level desk flow (the 4:30pm daily color) ─────────────────────
# Weekly aggregates are too coarse for "what happened today", so this is a
# separate feed: one row per anonymous trade (or per options structure).
# Flow REACTS to the day's price move so the color has a story to find:
# hedge funds chase momentum and buy vol after big days, asset managers buy
# dips in majors, corporates sell rallies, market makers draw more credit and
# prop firms simply trade more.
FLOW_DAYS = 120
_REACT = {  # segment → (buy-share tilt per unit z, volume per |z|, options per |z|)
    "Hedge Fund":    (+0.16, 0.45, 0.60),
    "Prop Trading":  (+0.03, 0.70, 0.30),
    "Market Maker":  (0.00, 0.40, 0.10),
    "Asset Manager": (-0.14, 0.20, 0.25),
    "Corporate":     (-0.16, 0.15, 0.10),
}
_FLOW_CACHE: dict = {}


def _day_moves() -> pd.DataFrame:
    """Per token × day: log return and z vs trailing 30d vol."""
    import numpy as np
    p = mock.price_history()
    w = p.pivot(index="date", columns="token", values="close").sort_index()
    r = np.log(w).diff()
    z = r / r.rolling(30, min_periods=10).std().shift(1)
    return z.fillna(0.0).clip(-4, 4)


def _flow_rows() -> pd.DataFrame:
    import options
    z = _day_moves()
    days = [mock.TODAY - timedelta(days=i) for i in range(FLOW_DAYS)][::-1]
    rows = []
    for p in roster():
        if p["named"]:
            continue
        pb = _PLAYBOOK[p["segment"]]
        tilt, vol_k, opt_k = _REACT[p["segment"]]
        r = random.Random(f"flow|{p['name']}")
        prods = [k for k, pr in pb["products"].items() if r.random() < pr] or ["Spot"]
        toks = [t for t in pb["tokens"]]
        strat_names = list(pb["strategies"])
        for d in days:
            for tok in toks:
                zt = float(z.at[d, tok]) if d in z.index and tok in z.columns else 0.0
                lam = 0.10 * pb["tokens"][tok] / 3 * (1 + vol_k * abs(zt))
                for prod in prods:
                    if prod == "Options" or (prod == "Credit" and tok not in ("USDC", "USDT", "BTC")):
                        continue
                    pm = {"Spot": 1.0, "Derivatives": 0.9, "Credit": 0.25, "FX": 0.35}[prod]
                    if p["segment"] == "Market Maker" and prod == "Credit":
                        pm *= 1 + 0.5 * abs(zt)
                    n = sum(r.random() < lam * pm for _ in range(3))
                    for _ in range(n):
                        bs = pb["buy_share"].get(tok, 0.5) + tilt * zt
                        side = "Buy" if r.random() < min(0.92, max(0.08, bs)) else "Sell"
                        if prod == "Credit":
                            side = "Buy"     # a draw
                        rows.append(_flow_row(p, d, r, prod, tok, side,
                                              3e6 * p["size"] * r.lognormvariate(0, 0.7)))
                # Options: whole structures, more of them (and more long vol) after big days
                if "Options" in prods and tok not in ("USDC", "USDT"):
                    if r.random() < 0.12 * pb["tokens"][tok] / 3 * (1 + opt_k * abs(zt)):
                        w = [pb["strategies"][s] * (1 + (abs(zt) if _BIAS.get(s) == "long vol"
                                                         else 0) * opt_k) for s in strat_names]
                        s = r.choices(strat_names, weights=w)[0]
                        row = _flow_row(p, d, r, "Options", tok, "Buy",
                                        4e6 * p["size"] * r.lognormvariate(0, 0.5))
                        row.update(strategy=s, bias=_BIAS.get(s, "—"))
                        rows.append(row)
    # Named clients: their actual (mock) blotter, anonymized the same way.
    for p in roster():
        if not p["named"]:
            continue
        t = mock.trades(p["name"])
        t = t[pd.to_datetime(t["trade_time"]).dt.date >= days[0]]
        opt = t[t["product"] == "Options"]
        for tr in t[t["product"] != "Options"].to_dict("records"):
            rows.append({"trade_time": pd.Timestamp(tr["trade_time"]), "peer_id": p["peer_id"],
                         "segment": p["segment"], "sub_segment": p["sub_segment"],
                         "tier": p["tier"], "product": tr["product"], "token": tr["token"],
                         "side": tr["side"], "notional_usd": float(tr["notional_usd"]),
                         "strategy": None, "bias": None})
        for s in options.summarize(opt.to_dict("records")):
            rows.append({"trade_time": pd.Timestamp(s["trade_time"]), "peer_id": p["peer_id"],
                         "segment": p["segment"], "sub_segment": p["sub_segment"],
                         "tier": p["tier"], "product": "Options", "token": s["token"],
                         "side": "Buy", "notional_usd": float(s["notional_usd"]),
                         "strategy": s["classification"]["name"],
                         "bias": s["classification"]["bias"]})
    return pd.DataFrame(rows).sort_values("trade_time").reset_index(drop=True)


def _flow_row(p, d, r, prod, tok, side, notional) -> dict:
    # Trading-day intraday shape in UTC: busiest in the London/NY overlap.
    hour = min(23, max(0, int(r.gauss(15, 4.5))))
    ts = pd.Timestamp(d) + pd.Timedelta(hours=hour, minutes=r.randint(0, 59))
    return {"trade_time": ts, "peer_id": p["peer_id"], "segment": p["segment"],
            "sub_segment": p["sub_segment"], "tier": p["tier"], "product": prod,
            "token": tok, "side": side, "notional_usd": round(notional, 0),
            "strategy": None, "bias": None}


def desk_flow(start, end) -> pd.DataFrame:
    """Trade-level anonymous flow with trade_time in [start, end] (Timestamps,
    UTC-naive). Columns: trade_time, peer_id, segment, sub_segment, tier,
    product, token, side, notional_usd, strategy, bias."""
    if "all" not in _FLOW_CACHE:
        _FLOW_CACHE["all"] = _flow_rows()
    df = _FLOW_CACHE["all"]
    tt = df["trade_time"]
    return df[(tt >= pd.Timestamp(start)) & (tt < pd.Timestamp(end))].reset_index(drop=True)
