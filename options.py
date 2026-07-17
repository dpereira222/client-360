"""
Options structure detection & classification.

Turns a set of individual option legs into named strategies. Two jobs:
  1. group()    — cluster raw option trades into structures (by structure_id if
                  the OMS supplies one, else by entity+underlying+time proximity).
  2. classify() — name the structure from its legs (vertical spread, straddle,
                  iron condor, …) with a bull/bear read and net debit/credit.

Instrument format expected: TOKEN-DDMMMYY-STRIKE-{C|P}  e.g. BTC-29AUG26-95000-C
Pure Python — no Streamlit, no Databricks, no pandas dependency in the core.
"""
from __future__ import annotations
from dataclasses import dataclass

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


@dataclass
class Leg:
    right: str        # 'C' or 'P'
    strike: float
    expiry: str       # raw token e.g. '29AUG26'
    side: str         # 'Buy' or 'Sell'
    qty: float
    notional: float
    premium: float    # per-contract mark (optional; 0 if unknown)
    instrument: str

    @property
    def signed_qty(self) -> float:
        return self.qty if self.side == "Buy" else -self.qty


def parse_instrument(instrument: str):
    """Return (token, expiry, strike, right) or None if not an option symbol."""
    parts = instrument.split("-")
    if len(parts) != 4:
        return None
    token, expiry, strike, right = parts
    right = right.upper()
    if right not in ("C", "P"):
        return None
    try:
        strike_f = float(strike)
    except ValueError:
        return None
    return token, expiry, strike_f, right


def _expiry_key(expiry: str) -> tuple:
    """Sortable key for an expiry token like '29AUG26'."""
    try:
        dd = int(expiry[:2]); mon = _MONTHS[expiry[2:5].upper()]; yy = int(expiry[5:7])
        return (2000 + yy, mon, dd)
    except Exception:
        return (9999, 99, 99)


def leg_from_trade(row: dict) -> Leg | None:
    """Build a Leg from a trade dict (keys: instrument, side, notional_usd, qty?, mark?)."""
    p = parse_instrument(row.get("instrument", ""))
    if not p:
        return None
    _, expiry, strike, right = p
    return Leg(
        right=right, strike=strike, expiry=expiry,
        side=row.get("side", "Buy"),
        qty=float(row.get("qty", 1) or 1),
        notional=float(row.get("notional_usd", 0) or 0),
        premium=float(row.get("price", 0) or 0),
        instrument=row["instrument"],
    )


# ── Grouping ──────────────────────────────────────────────────────────────────
def group(trades: list[dict]) -> list[dict]:
    """
    Cluster option trades into structures.

    Each trade dict needs: instrument, side, notional_usd, token, entity_name,
    trade_time, and optionally structure_id / qty / price.
    Returns a list of structure dicts: {key, token, entity_name, trade_time, legs}.
    """
    groups: dict = {}
    order: list = []
    for t in trades:
        if t.get("product") != "Options":
            continue
        leg = leg_from_trade(t)
        if leg is None:
            continue
        sid = t.get("structure_id")
        if sid:
            key = ("sid", sid)
        else:
            # fall back to entity + underlying + minute bucket
            tt = str(t.get("trade_time"))[:16]
            key = ("clust", t.get("entity_name"), t.get("token"), tt)
        if key not in groups:
            groups[key] = {"key": key, "token": t.get("token"),
                           "entity_name": t.get("entity_name"),
                           "trade_time": t.get("trade_time"), "legs": []}
            order.append(key)
        groups[key]["legs"].append(leg)
    return [groups[k] for k in order]


# ── Classification ────────────────────────────────────────────────────────────
def _net_premium(legs: list[Leg]) -> float:
    """Net cash: positive = net debit paid, negative = net credit received."""
    return sum((1 if l.side == "Buy" else -1) * l.premium * l.qty for l in legs)


def classify(legs: list[Leg]) -> dict:
    """Name a structure from its legs. Returns {name, kind, bias, net, detail}."""
    n = len(legs)
    calls = [l for l in legs if l.right == "C"]
    puts = [l for l in legs if l.right == "P"]
    expiries = sorted({l.expiry for l in legs}, key=_expiry_key)
    strikes = sorted({l.strike for l in legs})
    net = _net_premium(legs)
    net_dir = "debit" if net > 0 else "credit"

    def bought(ls): return [l for l in ls if l.side == "Buy"]
    def sold(ls):   return [l for l in ls if l.side == "Sell"]

    # ── single leg ────────────────────────────────────────────────────────────
    if n == 1:
        l = legs[0]
        side = "Long" if l.side == "Buy" else "Short"
        kind = "Call" if l.right == "C" else "Put"
        bias = {("Long", "C"): "bullish", ("Short", "P"): "bullish",
                ("Long", "P"): "bearish", ("Short", "C"): "bearish"}[(side, l.right)]
        return _mk(f"{side} {kind}", "single", bias, net_dir,
                   f"{l.strike:g} {l.expiry}")

    # ── two legs, one expiry ────────────────────────────────────────────────────
    if n == 2 and len(expiries) == 1:
        if calls and not puts:                       # call vertical
            b, s = bought(calls), sold(calls)
            if b and s:
                bias = "bullish" if b[0].strike < s[0].strike else "bearish"
                name = "Bull Call Spread" if bias == "bullish" else "Bear Call Spread"
                return _mk(name, "vertical", bias, net_dir,
                           f"{min(strikes):g}/{max(strikes):g} {expiries[0]}")
        if puts and not calls:                       # put vertical
            b, s = bought(puts), sold(puts)
            if b and s:
                bias = "bearish" if b[0].strike > s[0].strike else "bullish"
                name = "Bear Put Spread" if bias == "bearish" else "Bull Put Spread"
                return _mk(name, "vertical", bias, net_dir,
                           f"{min(strikes):g}/{max(strikes):g} {expiries[0]}")
        if calls and puts:                           # straddle / strangle / RR
            c, p = calls[0], puts[0]
            same_strike = abs(c.strike - p.strike) < 1e-6
            if c.side == p.side:                     # same direction on both
                side = "Long" if c.side == "Buy" else "Short"
                if same_strike:
                    return _mk(f"{side} Straddle", "vol",
                               "long vol" if side == "Long" else "short vol",
                               net_dir, f"{c.strike:g} {expiries[0]}")
                return _mk(f"{side} Strangle", "vol",
                           "long vol" if side == "Long" else "short vol",
                           net_dir, f"{min(strikes):g}/{max(strikes):g} {expiries[0]}")
            # opposite sides, one call one put → risk reversal / collar
            long_call = c.side == "Buy"
            bias = "bullish" if long_call else "bearish"
            return _mk("Risk Reversal", "combo", bias, net_dir,
                       f"{p.strike:g}P/{c.strike:g}C {expiries[0]}")

    # ── two legs, two expiries, same strike & right → calendar ──────────────────
    if n == 2 and len(expiries) == 2 and len(strikes) == 1 and \
            len({l.right for l in legs}) == 1:
        return _mk("Calendar Spread", "calendar", "long vol", net_dir,
                   f"{strikes[0]:g} {expiries[0]}/{expiries[1]}")

    # ── three legs, one right → butterfly ───────────────────────────────────────
    if n == 3 and len({l.right for l in legs}) == 1 and len(strikes) == 3:
        k1, k2, k3 = strikes
        if abs((k2 - k1) - (k3 - k2)) < max(1.0, 0.05 * (k3 - k1)):
            mid = [l for l in legs if abs(l.strike - k2) < 1e-6]
            wing = [l for l in legs if l.strike in (k1, k3)]
            if mid and wing and mid[0].side != wing[0].side:
                long_fly = wing[0].side == "Buy"
                return _mk("Long Butterfly" if long_fly else "Short Butterfly",
                           "butterfly", "neutral", net_dir,
                           f"{k1:g}/{k2:g}/{k3:g}")

    # ── four legs ───────────────────────────────────────────────────────────────
    if n == 4:
        if len(calls) == 2 and len(puts) == 2:
            call_ks = sorted(l.strike for l in calls)
            put_ks = sorted(l.strike for l in puts)
            iron_fly = abs(call_ks[0] - put_ks[1]) < 1e-6
            name = "Iron Butterfly" if iron_fly else "Iron Condor"
            # short vol if the inner (sold) legs collect premium
            bias = "short vol" if net < 0 else "long vol"
            return _mk(name, "condor", bias, net_dir,
                       f"{min(strikes):g}/{max(strikes):g}")
        if (len(calls) == 4 or len(puts) == 4) and len(strikes) == 4:
            return _mk("Condor", "condor", "neutral", net_dir,
                       f"{min(strikes):g}–{max(strikes):g}")

    # ── fallback ────────────────────────────────────────────────────────────────
    rights = f"{len(calls)}C/{len(puts)}P"
    return _mk(f"{n}-Leg Structure", "custom", "—", net_dir,
               f"{rights} · {len(expiries)} expiries")


def _mk(name, kind, bias, net_dir, detail):
    return {"name": name, "kind": kind, "bias": bias, "net": net_dir, "detail": detail}


def detect_covered_calls(structures: list[dict], spot_positions: list[dict]) -> None:
    """Re-label a lone short call as 'Covered Call' if the entity is long the spot.
    Mutates structure dicts in place. spot_positions: list of {token, direction}."""
    held = {p["token"] for p in spot_positions
            if p.get("product") == "Spot" and p.get("direction") == "Long"}
    for s in structures:
        legs = s["legs"]
        if len(legs) == 1 and legs[0].right == "C" and legs[0].side == "Sell" \
                and s["token"] in held:
            s["classification"]["name"] = "Covered Call"
            s["classification"]["bias"] = "income / capped upside"


def summarize(trades: list[dict], spot_positions: list[dict] | None = None) -> list[dict]:
    """Full pipeline: group + classify. Returns structures enriched for display."""
    out = []
    for g in group(trades):
        legs = g["legs"]
        cls = classify(legs)
        notional = sum(l.notional for l in legs)
        out.append({**g, "classification": cls, "n_legs": len(legs),
                    "notional_usd": notional,
                    "legs_display": [
                        {"side": l.side, "right": "Call" if l.right == "C" else "Put",
                         "strike": l.strike, "expiry": l.expiry,
                         "qty": l.qty, "notional_usd": l.notional,
                         "instrument": l.instrument} for l in legs]})
    if spot_positions:
        detect_covered_calls(out, spot_positions)
    # newest first
    return sorted(out, key=lambda s: str(s["trade_time"]), reverse=True)
