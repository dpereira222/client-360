"""
Deterministic mock dataset for the Client 360 prototype.

This module fabricates a realistic cross-product world (spot / derivatives /
options / credit / FX) for a handful of marquee institutional clients so the UI
can be demoed with ZERO Databricks access.

IT/engineering never needs to read this file. It exists only to feed the UI.
The real contract lives in `data.py` — this just gives those functions something
to return. Everything is seeded, so the demo looks identical on every machine.
"""
from __future__ import annotations
import random
from datetime import date, datetime, timedelta

import pandas as pd

TODAY = date(2026, 7, 16)

TOKENS = ["BTC", "ETH", "SOL", "XRP", "AVAX", "LINK", "ARB", "OP", "USDC", "USDT"]

# ── Client registry ───────────────────────────────────────────────────────────
# Each parent client has legal entities and a behavioral profile that drives the
# generated flow. Tiers scale notionals; products list gates which books appear.
CLIENTS = {
    "Brevan Howard": {
        "tier": "Platinum",
        "since": "2019-03-11",
        "coverage": "Sarah Kwan",
        "segment": "Hedge Fund", "sub_segment": "Macro",
        "contact": "James",
        "entities": [
            ("BH-001", "Brevan Howard Master Fund Ltd",   "Master Fund"),
            ("BH-002", "BH Digital LP",                    "Digital"),
            ("BH-003", "BH-DG Systematic Master Fund",     "Systematic"),
            ("BH-004", "Brevan Howard Asset Management LLP","Management Co"),
        ],
        "products": ["Spot", "Derivatives", "Options", "Credit", "FX"],
        "tokens": ["BTC", "ETH", "SOL", "USDC"],
        "size": 1.0,
    },
    "Jump Trading": {
        "tier": "Platinum",
        "since": "2018-07-02",
        "coverage": "Marcus Vell",
        "segment": "Prop Trading", "sub_segment": "HFT",
        "contact": "Ravi",
        "entities": [
            ("JMP-001", "Jump Trading LLC",        "Prop"),
            ("JMP-002", "Jump Crypto Holdings LLC", "Digital"),
        ],
        "products": ["Spot", "Derivatives", "Options", "FX"],
        "tokens": ["BTC", "ETH", "SOL", "ARB", "OP"],
        "size": 1.3,
    },
    "Wintermute": {
        "tier": "Gold",
        "since": "2020-11-19",
        "coverage": "Marcus Vell",
        "segment": "Market Maker", "sub_segment": "Crypto-Native",
        "contact": "Yuki",
        "entities": [
            ("WIN-001", "Wintermute Trading Ltd",   "Prop"),
            ("WIN-002", "Wintermute Asia Pte Ltd",  "Asia"),
        ],
        "products": ["Spot", "Derivatives", "FX"],
        "tokens": ["ETH", "SOL", "LINK", "AVAX", "USDT"],
        "size": 0.9,
    },
    "Galaxy Digital": {
        "tier": "Gold",
        "since": "2021-02-08",
        "coverage": "Sarah Kwan",
        "segment": "Asset Manager", "sub_segment": "Digital Asset Manager",
        "contact": "Elena",
        "entities": [
            ("GLX-001", "Galaxy Digital LP",          "Master"),
            ("GLX-002", "Galaxy Digital Trading LLC",  "Trading"),
        ],
        "products": ["Spot", "Credit", "Derivatives"],
        "tokens": ["BTC", "ETH", "SOL"],
        "size": 0.8,
    },
    "Cumberland": {
        "tier": "Gold",
        "since": "2019-09-30",
        "coverage": "Deepa Rao",
        "segment": "Market Maker", "sub_segment": "TradFi Prop",
        "contact": "Tom",
        "entities": [
            ("CMB-001", "Cumberland DRW LLC", "Prop"),
        ],
        "products": ["Spot", "Derivatives", "FX"],
        "tokens": ["BTC", "ETH", "XRP", "USDC"],
        "size": 0.7,
    },
    # ── Wider book (added for idea targeting: needs a universe to rank) ───────
    # opt_pref: options structures this client favours (weights the generator).
    "Multicoin Capital": {
        "tier": "Gold", "since": "2020-05-12", "coverage": "Deepa Rao",
        "segment": "Hedge Fund", "sub_segment": "Crypto Discretionary", "contact": "Kyle",
        "entities": [("MCC-001", "Multicoin Master Fund LP", "Master Fund"),
                     ("MCC-002", "Multicoin Venture Fund III", "Venture")],
        "products": ["Spot", "Derivatives", "Options"],
        "tokens": ["SOL", "ETH", "BTC", "AVAX"], "size": 0.75,
        "opt_pref": ["Long Straddle", "Short Strangle", "Calendar", "Iron Condor"],
    },
    "Millennium": {
        "tier": "Platinum", "since": "2022-03-01", "coverage": "Sarah Kwan",
        "segment": "Hedge Fund", "sub_segment": "Multi-Strategy", "contact": "Priya",
        "entities": [("MLN-001", "Millennium International Ltd", "Master Fund"),
                     ("MLN-002", "Millennium Digital Pod", "Pod")],
        "products": ["Spot", "Derivatives", "Options", "Credit"],
        "tokens": ["BTC", "ETH", "USDC"], "size": 1.1,
        "opt_pref": ["Calendar", "Risk Reversal", "Long Straddle"],
    },
    "Two Sigma": {
        "tier": "Gold", "since": "2023-01-17", "coverage": "Marcus Vell",
        "segment": "Hedge Fund", "sub_segment": "Quant", "contact": "Alex",
        "entities": [("TSG-001", "Two Sigma Investments LP", "Master Fund")],
        "products": ["Spot", "Derivatives", "FX"],
        "tokens": ["BTC", "ETH", "SOL", "LINK"], "size": 0.9,
    },
    "Pantera Capital": {
        "tier": "Silver", "since": "2021-08-23", "coverage": "Deepa Rao",
        "segment": "Hedge Fund", "sub_segment": "Crypto Discretionary", "contact": "Joey",
        "entities": [("PAN-001", "Pantera Liquid Token Fund", "Liquid")],
        "products": ["Spot", "Derivatives", "Options"],
        "tokens": ["SOL", "ETH", "ARB", "OP"], "size": 0.5,
        "opt_pref": ["Risk Reversal", "Bull Call Spread", "Long Call"],
    },
    "Fidelity Digital Assets": {
        "tier": "Gold", "since": "2022-10-04", "coverage": "Sarah Kwan",
        "segment": "Asset Manager", "sub_segment": "Traditional AM", "contact": "Meredith",
        "entities": [("FDA-001", "Fidelity Digital Assets Services LLC", "Custody/Trading")],
        "products": ["Spot", "Options"],
        "tokens": ["BTC", "ETH"], "size": 0.85,
        "opt_pref": ["Long Call", "Bull Call Spread"],
    },
    "Marathon Digital": {
        "tier": "Silver", "since": "2023-05-30", "coverage": "Marcus Vell",
        "segment": "Corporate", "sub_segment": "Miner", "contact": "Sam",
        "entities": [("MRA-001", "Marathon Digital Holdings Inc", "Corporate")],
        "products": ["Spot", "Options", "Credit"],
        "tokens": ["BTC", "USDC"], "size": 0.45,
        "opt_pref": ["Bear Put Spread", "Short Put", "Long Call"],
    },
    "QCP Capital": {
        "tier": "Gold", "since": "2020-02-14", "coverage": "Deepa Rao",
        "segment": "Market Maker", "sub_segment": "Crypto-Native", "contact": "Darius",
        "entities": [("QCP-001", "QCP Capital Pte Ltd", "Prop")],
        "products": ["Spot", "Derivatives", "Options", "FX"],
        "tokens": ["BTC", "ETH", "SOL", "USDT"], "size": 0.8,
        "opt_pref": ["Short Strangle", "Iron Condor", "Calendar", "Long Butterfly"],
    },
}


def _rng(parent: str, salt: str = "") -> random.Random:
    """A stable RNG keyed on the client name so the demo is reproducible."""
    return random.Random(f"{parent}|{salt}")


def all_parents() -> list[str]:
    return sorted(CLIENTS)


def resolve(parent: str) -> dict | None:
    return CLIENTS.get(parent)


# ── Entities ──────────────────────────────────────────────────────────────────
def entities(parent: str) -> pd.DataFrame:
    c = CLIENTS[parent]
    rows = []
    for i, (eid, name, etype) in enumerate(c["entities"]):
        r = _rng(parent, f"ent{eid}")
        rows.append({
            "entity_id": eid,
            "entity_name": name,
            "parent_name": parent,
            "entity_type": etype,
            "status": "Active",
            "onboarded": c["since"] if i == 0 else
                         (date.fromisoformat(c["since"]) + timedelta(days=r.randint(120, 900))).isoformat(),
        })
    return pd.DataFrame(rows)


# ── Trade blotter (unified across products) ───────────────────────────────────
# Full multi-year history is generated once per client (deterministic), then the
# data layer filters it to the requested date range. This supports a "years"
# lookback and arbitrary custom ranges.
_HISTORY_CACHE: dict = {}
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _history_start(c: dict) -> date:
    onb = date.fromisoformat(c["since"])
    return max(onb, TODAY - timedelta(days=365 * 3))


def trades(parent: str) -> pd.DataFrame:
    """All trades across the client's full history (multi-year). Filter downstream."""
    if parent in _HISTORY_CACHE:
        return _HISTORY_CACHE[parent]
    c = CLIENTS[parent]
    r = _rng(parent, "hist")
    ents = c["entities"]
    span = (TODAY - _history_start(c)).days
    rows = []

    # Non-option flow: one row per trade across the span.
    flow_products = [p for p in c["products"] if p != "Options"]
    n = int(2.4 * span * c["size"])
    for _ in range(n):
        prod = r.choices(flow_products, weights=_product_weights(flow_products))[0]
        ts = _rand_ts(r, span)
        eid, ename, _t = r.choice(ents)
        tok = r.choice(c["tokens"])
        side = r.choice(["Buy", "Sell"])
        notional = round(r.lognormvariate(14.6, 1.05) * c["size"], 0)
        px = _price_at(tok, ts, r)
        bps = _bps_for(prod, r)
        rows.append(_row(ts, eid, ename, prod, _instrument(prod, tok, r), tok, side,
                         notional, px, bps, qty=round(notional / px, 4)))

    # Options flow: emitted as multi-leg structures sharing a structure_id.
    if "Options" in c["products"]:
        n_struct = int(0.5 * span * c["size"] / 7)   # ~ a few per week
        for i in range(max(6, n_struct)):
            rows.extend(_option_structure(parent, r, ents, c, i))

    # Fills for pitches/RFQs the client executed in chat (see mock_chat.py), so
    # the pitch→trade matcher in pitches.py has real blotter rows to find.
    import mock_chat
    rows.extend(mock_chat.fill_trades(parent))

    df = pd.DataFrame(rows).sort_values("trade_time", ascending=False).reset_index(drop=True)
    _HISTORY_CACHE[parent] = df
    return df


def _rand_ts(r: random.Random, span: int) -> datetime:
    d = r.randint(0, span)
    return datetime.combine(TODAY, datetime.min.time()) - timedelta(
        days=d, hours=r.randint(0, 23), minutes=r.randint(0, 59))


def _row(ts, eid, ename, prod, instrument, tok, side, notional, px, bps,
         qty=1.0, structure_id=None):
    return {
        "trade_time": ts, "entity_id": eid, "entity_name": ename, "product": prod,
        "instrument": instrument, "token": tok, "side": side,
        "notional_usd": float(notional), "price": px, "bps": round(bps, 1),
        "revenue_usd": round(notional * bps / 10000, 0), "venue": _venue(prod),
        "qty": qty, "structure_id": structure_id,
    }


def _product_weights(products: list[str]) -> list[float]:
    w = {"Spot": 5, "Derivatives": 3, "Credit": 0.6, "FX": 2}
    return [w[p] for p in products]


def _bps_for(prod: str, r: random.Random) -> float:
    base = {"Spot": 4.5, "Derivatives": 6.0, "Options": 22.0, "Credit": 45.0, "FX": 2.2}[prod]
    return max(0.4, r.gauss(base, base * 0.35))


def _instrument(prod: str, tok: str, r: random.Random) -> str:
    if prod == "Spot":
        return f"{tok}/USD"
    if prod == "Derivatives":
        return f"{tok}-PERP" if r.random() < 0.7 else f"{tok}-{_fmt_expiry(TODAY + timedelta(days=70))}"
    if prod == "Credit":
        return f"{tok} Financing"
    return f"{tok}/USD FX"


def _fmt_expiry(d: date) -> str:
    return f"{d.day:02d}{_MONTHS[d.month - 1]}{d.year % 100:02d}"


# ── Option structure templates ────────────────────────────────────────────────
# Each leg: (right, strike_moneyness, side, qty_mult, expiry_months).
# Premium is derived from moneyness so nets (debit/credit) come out realistic.
_STRUCTURES = [
    ("Long Call",        [("C", 1.05, "Buy", 1, 2)]),
    ("Short Put",        [("P", 0.92, "Sell", 1, 2)]),
    ("Bull Call Spread", [("C", 1.00, "Buy", 1, 2), ("C", 1.12, "Sell", 1, 2)]),
    ("Bear Put Spread",  [("P", 1.00, "Buy", 1, 2), ("P", 0.88, "Sell", 1, 2)]),
    ("Long Straddle",    [("C", 1.00, "Buy", 1, 1), ("P", 1.00, "Buy", 1, 1)]),
    ("Short Strangle",   [("C", 1.10, "Sell", 1, 1), ("P", 0.90, "Sell", 1, 1)]),
    ("Risk Reversal",    [("C", 1.10, "Buy", 1, 3), ("P", 0.90, "Sell", 1, 3)]),
    ("Calendar",         [("C", 1.00, "Sell", 1, 1), ("C", 1.00, "Buy", 1, 3)]),
    ("Long Butterfly",   [("C", 0.95, "Buy", 1, 2), ("C", 1.00, "Sell", 2, 2),
                          ("C", 1.05, "Buy", 1, 2)]),
    ("Iron Condor",      [("P", 0.85, "Buy", 1, 2), ("P", 0.93, "Sell", 1, 2),
                          ("C", 1.07, "Sell", 1, 2), ("C", 1.15, "Buy", 1, 2)]),
]


def _premium(px: float, right: str, moneyness: float, months: int) -> float:
    """Crude but monotonic option premium: intrinsic + time value."""
    strike = px * moneyness
    intrinsic = max(0.0, (px - strike) if right == "C" else (strike - px))
    tv = px * 0.05 * (months ** 0.5) * (1.0 - min(0.8, abs(1 - moneyness) * 2))
    return round(max(px * 0.004, intrinsic + tv), 2)


def _option_structure(parent, r, ents, c, i) -> list[dict]:
    pref = set(c.get("opt_pref", []))
    name, template = r.choices(_STRUCTURES,
                               weights=[4 if n in pref else 1 for n, _ in _STRUCTURES])[0]
    eid, ename, _t = r.choice(ents)
    tok = r.choice([t for t in c["tokens"] if t not in ("USDC", "USDT")] or ["BTC"])
    span = (TODAY - _history_start(c)).days
    ts = _rand_ts(r, span)
    px = _price_at(tok, ts, r)
    sid = f"{eid}-OPT-{i:04d}"
    contracts = round(r.uniform(50, 400) * c["size"], 0)
    tick = _tick(px)
    legs = []
    for right, mny, side, qmult, months in template:
        strike = round(px * mny / tick) * tick
        expiry = _fmt_expiry(_forward_expiry(ts, months))
        prem = _premium(px, right, mny, months)
        qty = contracts * qmult
        notional = round(qty * strike * (1 if px > 100 else 100), 0)
        legs.append(_row(ts, eid, ename, "Options",
                         f"{tok}-{expiry}-{strike:g}-{right}", tok, side,
                         notional, prem, _bps_for("Options", r),
                         qty=qty, structure_id=sid))
    return legs


def _tick(px: float) -> float:
    """A strike tick fine enough to keep template strikes distinct."""
    if px >= 5000:
        return 250.0
    if px >= 500:
        return 25.0
    if px >= 50:
        return 1.0
    if px >= 5:
        return 0.25
    return 0.01


def _forward_expiry(ts: datetime, months: int) -> date:
    m = ts.month - 1 + months
    y = ts.year + m // 12
    mo = m % 12 + 1
    return date(y, mo, 26)


# ── Desk-wide benchmark (rest of the book) ────────────────────────────────────
def desk_benchmark(tokens: list[str], start: str, end: str) -> pd.DataFrame:
    """Desk-wide net flow per token across ALL clients (for comparison)."""
    rows = []
    for tok in tokens:
        rr = _rng("DESK", tok + start)
        buy = rr.uniform(50, 900) * 1e6
        sell = rr.uniform(50, 900) * 1e6
        rows.append({
            "token": tok,
            "desk_buy_usd": round(buy, 0),
            "desk_sell_usd": round(sell, 0),
            "desk_net_usd": round(buy - sell, 0),
            "num_clients": rr.randint(8, 60),
        })
    return pd.DataFrame(rows)


def _venue(prod: str) -> str:
    return {"Spot": "OTC", "Derivatives": "OTC-Deriv", "Options": "OTC-Options",
            "Credit": "Lending Desk", "FX": "FX Desk"}[prod]


BASE_PX = {"BTC": 94000, "ETH": 3200, "SOL": 165, "XRP": 0.62, "AVAX": 28,
           "LINK": 15, "ARB": 0.85, "OP": 1.9, "USDC": 1.0, "USDT": 1.0}


def _price(tok: str, r: random.Random) -> float:
    px = BASE_PX[tok]
    return round(px * r.uniform(0.97, 1.03), 2 if px < 100 else 0)


def _price_at(tok: str, ts: datetime, r: random.Random) -> float:
    """Execution price near that day's close (historical, not today's level)."""
    px = close_on(tok, ts.date())
    return round(px * r.uniform(0.995, 1.005), 2 if px >= 100 else 4)


# ── Daily price history (market data, not client data) ────────────────────────
# Walked BACKWARDS from today's BASE_PX so the latest close matches the rest of
# the mock. A drift over the last ~6 months gives the pitch tracker a mix of
# ideas that worked and ideas that didn't.
VOLS = {"BTC": 0.50, "ETH": 0.65, "SOL": 0.80, "XRP": 0.85, "AVAX": 0.90,
        "LINK": 0.85, "ARB": 1.00, "OP": 1.00, "USDC": 0.0, "USDT": 0.0}
_DRIFT_180D = {"BTC": 0.16, "ETH": -0.08, "SOL": 0.30, "XRP": 0.05,
               "AVAX": -0.15, "LINK": 0.08, "ARB": -0.25, "OP": -0.20}
_PX_CACHE: dict = {}


def price_history() -> pd.DataFrame:
    """Daily close per token, ~3 years to TODAY. Columns: date, token, close.

    Log price = anchor (the 180-day drift, flat before that) + a mean-reverting
    (OU) wiggle, bridged to zero at TODAY so the last close is exactly BASE_PX.
    Mean reversion keeps 3-year paths plausible (no $400K BTC in 2023) while
    still moving enough over 30–60 days for pitches to win or lose."""
    if "all" in _PX_CACHE:
        return _PX_CACHE["all"]
    import math
    start = TODAY - timedelta(days=365 * 3 + 30)
    n = (TODAY - start).days
    kappa = 1 / 45
    rows = []
    for tok, base in BASE_PX.items():
        r = random.Random(f"PX|{tok}")
        sig = VOLS[tok] / math.sqrt(365)
        drift = _DRIFT_180D.get(tok, 0.0)
        dev, devs = 0.0, []
        for _ in range(n + 1):
            devs.append(dev)
            dev += -kappa * dev + (r.gauss(0, sig) if sig else 0.0)
        for i in range(n + 1):
            left = n - i                                   # days before TODAY
            anchor = -drift * min(1.0, left / 180)
            x = anchor + devs[i] - devs[n] * i / n         # bridge → 0 at TODAY
            rows.append({"date": start + timedelta(days=i), "token": tok,
                         "close": round(base * math.exp(x), 2 if base >= 100 else 4)})
    df = pd.DataFrame(rows)
    _PX_CACHE["all"] = df
    _PX_CACHE["lookup"] = {(t, d): c for t, d, c in df[["token", "date", "close"]].itertuples(index=False)}
    return df


def close_on(tok: str, d: date) -> float:
    """Close on date d (or the latest date before it, clamped to the history)."""
    price_history()
    lk = _PX_CACHE["lookup"]
    d = min(d, TODAY)
    for _ in range(10):
        if (tok, d) in lk:
            return float(lk[(tok, d)])
        d -= timedelta(days=1)
    return float(BASE_PX[tok])


# ── Current positions ─────────────────────────────────────────────────────────
def positions(parent: str) -> pd.DataFrame:
    """Consolidated open positions across spot, derivatives, options."""
    c = CLIENTS[parent]
    r = _rng(parent, "pos")
    rows = []
    for tok in c["tokens"]:
        if tok in ("USDC", "USDT"):
            continue
        if "Spot" in c["products"] and r.random() < 0.8:
            qty = r.uniform(50, 800) * c["size"] * (1 if tok == "BTC" else 1000 / _price(tok, r))
            mark = _price(tok, r)
            cost = mark * r.uniform(0.85, 1.15)
            rows.append(_pos_row("Spot", f"{tok} Spot", tok,
                                  "Long", qty, cost, mark, r))
        if "Derivatives" in c["products"] and r.random() < 0.7:
            direction = r.choice(["Long", "Short"])
            qty = r.uniform(100, 1500) * c["size"] * (1 if tok == "BTC" else 1000 / _price(tok, r))
            mark = _price(tok, r)
            cost = mark * r.uniform(0.9, 1.1)
            row = _pos_row("Derivatives", f"{tok}-PERP", tok, direction, qty, cost, mark, r)
            row["funding_8h_bps"] = round(r.gauss(1.2, 3.5), 1)
            rows.append(row)
        if "Options" in c["products"] and r.random() < 0.55:
            direction = r.choice(["Long", "Short"])
            qty = r.uniform(50, 600) * c["size"]
            mark = _price(tok, r) * r.uniform(0.02, 0.08)
            cost = mark * r.uniform(0.7, 1.3)
            spot = _price(tok, r)
            right = r.choice(["C", "P"])
            strike = round(spot * r.choice([0.9, 1.0, 1.1]) / _tick(spot)) * _tick(spot)
            inst = f"{tok}-{_fmt_expiry(_forward_expiry(datetime.combine(TODAY, datetime.min.time()), 2))}-{strike:g}-{right}"
            row = _pos_row("Options", inst, tok, direction, qty, cost, mark, r)
            # Position delta: sign from right × side (long call/short put = +).
            sgn = (1 if right == "C" else -1) * (1 if direction == "Long" else -1)
            row["delta"] = round(sgn * r.uniform(0.15, 0.6), 2)
            row["vega_usd"] = round(qty * r.uniform(5, 30), 0) * (1 if direction == "Long" else -1)
            rows.append(row)
    return pd.DataFrame(rows)


def _pos_row(prod, instrument, tok, direction, qty, cost, mark, r):
    sign = 1 if direction == "Long" else -1
    notional = abs(qty * mark)
    upnl = sign * qty * (mark - cost)
    return {
        "product": prod, "instrument": instrument, "token": tok,
        "direction": direction, "qty": round(qty, 3),
        "avg_price": round(cost, 2), "mark_price": round(mark, 2),
        "notional_usd": round(notional, 0), "unrealized_pnl": round(upnl, 0),
        "funding_8h_bps": None, "delta": None, "vega_usd": None,
    }


# ── Credit book ───────────────────────────────────────────────────────────────
def credit(parent: str) -> pd.DataFrame:
    c = CLIENTS[parent]
    if "Credit" not in c["products"]:
        return pd.DataFrame()
    r = _rng(parent, "credit")
    rows = []
    kinds = ["Term Loan", "Revolving Facility", "Repo", "Structured Financing"]
    for i in range(r.randint(2, 4)):
        eid, ename, _ = r.choice(c["entities"])
        principal = round(r.uniform(15, 120) * 1e6 * c["size"], -5)
        drawn = round(principal * r.uniform(0.4, 1.0), -5)
        coll = round(drawn * r.uniform(1.15, 1.9), -5)
        ltv = round(drawn / coll * 100, 1)
        term = r.choice([90, 180, 270, 365, 730])
        # Open facilities mature in the future; some soon (renewal signal).
        days_to_mat = r.choice([18, 35, 62, 120, 210, 340])
        maturity = TODAY + timedelta(days=days_to_mat)
        start = maturity - timedelta(days=term)
        if ltv > 80:
            status = "critical"
        elif ltv > 70:
            status = "serious"
        elif days_to_mat < 30:
            status = "warning"
        else:
            status = "good"
        rows.append({
            "deal_id": f"{eid}-CR{i+1}",
            "entity_name": ename,
            "facility_type": r.choice(kinds),
            "collateral_token": r.choice(["BTC", "ETH"]),
            "principal_usd": principal,
            "drawn_usd": drawn,
            "collateral_usd": coll,
            "ltv_pct": ltv,
            "rate_bps": round(r.uniform(650, 1400), 0),
            "start": start.isoformat(),
            "maturity": maturity.isoformat(),
            "days_to_maturity": days_to_mat,
            "status": status,
        })
    return pd.DataFrame(rows)


# ── Gong calls (mock) ─────────────────────────────────────────────────────────
def gong(parent: str, days: int) -> pd.DataFrame:
    r = _rng(parent, "gong")
    topics = [
        ("Quarterly review + derivatives expansion",
         "Discussed growing perp flow; client wants tighter funding on ETH.",
         "Client happy with spot fills. Interested in structured options.",
         "Send options RFQ workflow; intro derivs desk lead."),
        ("Credit facility renewal",
         "Reviewed term loan maturing next quarter; client wants to upsize.",
         "Collateral mix shifting toward ETH. LTV comfortable.",
         "Draft renewal term sheet at +50bps; confirm collateral haircut."),
        ("Onboarding new entity",
         "New systematic entity coming online; needs API + FIX.",
         "Volumes expected to ramp over 60 days.",
         "Coordinate onboarding docs; assign coverage."),
        ("Market color + positioning",
         "Shared desk view on BTC; client net long into quarter-end.",
         "Client asked about options hedging overlay.",
         "Follow up with vol surface deck."),
    ]
    rows = []
    n = r.randint(2, 4)
    for i in range(n):
        t = topics[i % len(topics)]
        d = TODAY - timedelta(days=r.randint(1, max(2, days)))
        rows.append({
            "title": f"{parent} — {t[0]}",
            "call_date": d.isoformat(),
            "participants": "coverage@falconx.io; pm@" + parent.split()[0].lower() + ".com",
            "brief": t[1], "key_points": t[2], "next_steps": t[3],
            "duration_sec": r.randint(1500, 3600),
        })
    return pd.DataFrame(rows).sort_values("call_date", ascending=False).reset_index(drop=True)
