"""
Option expiries — every live client structure across the book, by expiry.

Inputs (all via data.py): get_book_trades (the blotter for every client),
get_profile (coverage → who gets the alert) and get_price_history (spot marks).
Outputs:
  • live         — one row per live structure: client, coverage, structure name
                   (options.py), front expiry, days left, notional, premium,
                   where spot sits vs the strikes, P&L if it expired at spot now
  • apply_filters— client / expiry date / trade date / size / structure / token
  • ladder       — notional expiring per expiry date (the wall of expiries)
  • pin_risk     — expiring soon with spot near a strike
  • digest       — the Monday note per salesperson: what expires this week and
                   the one line to open the call with

"Live" = front expiry on/after today. The mock blotter never closes options
early; in prod, net against get_positions (or the OMS close flag) so a
structure unwound in June doesn't show up as expiring in July.

No Streamlit, no Databricks, no LLM.
"""
from __future__ import annotations
from datetime import date, timedelta

import pandas as pd

import options

PIN_PCT = 0.03       # spot within 3% of a strike …
PIN_DAYS = 14        # … with ≤ 14 days left = pin risk
DIGEST_DAYS = 7      # the Monday note covers the next 7 days

COLS = ["structure_id", "parent", "coverage", "entity_name", "token", "structure", "kind",
        "bias", "detail", "n_legs", "trade_date", "expiry", "last_expiry", "dte",
        "notional_usd", "premium_usd", "spot", "strikes", "nearest_strike",
        "nearest_pct", "payoff_usd", "pnl_usd", "pin", "status", "talking_point"]


def expiry_date(token: str) -> date | None:
    """'26JUL26' → date(2026, 7, 26)."""
    y, m, d = options._expiry_key(token)
    return None if y == 9999 else date(y, m, d)


def spot_marks(prices: pd.DataFrame, today: date) -> dict:
    """Latest close on/before today, per token."""
    p = prices[pd.to_datetime(prices["date"]).dt.date <= today]
    return p.sort_values("date").groupby("token")["close"].last().to_dict()


def _mult(leg) -> float:
    """Contract multiplier implied by the blotter (notional = qty × strike × mult)."""
    return leg.notional / (leg.qty * leg.strike) if leg.qty and leg.strike else 1.0


def _intrinsic(leg, spot: float) -> float:
    return max(0.0, spot - leg.strike) if leg.right == "C" else max(0.0, leg.strike - spot)


def live(book: pd.DataFrame, profiles: dict, prices: pd.DataFrame,
         today: date) -> pd.DataFrame:
    """One row per structure whose front expiry is today or later."""
    if book is None or book.empty:
        return pd.DataFrame(columns=COLS)
    opt = book[book["product"] == "Options"]
    if opt.empty:
        return pd.DataFrame(columns=COLS)
    marks = spot_marks(prices, today)
    parent_of = opt.drop_duplicates("entity_name").set_index("entity_name")["parent"]
    rows = []
    for g in options.group(opt.to_dict("records")):
        legs = g["legs"]
        exps = sorted(e for e in {expiry_date(l.expiry) for l in legs} - {None} if e >= today)
        if not exps:
            continue
        front = exps[0]
        legs = [l for l in legs if expiry_date(l.expiry) in exps]   # drop already-expired legs
        cls = options.classify(legs)
        parent = parent_of.get(g["entity_name"], "")
        spot = marks.get(g["token"])
        sid = g["key"][1] if g["key"][0] == "sid" else "-".join(map(str, g["key"][1:]))
        signed = lambda l: 1 if l.side == "Buy" else -1                     # noqa: E731
        premium = round(sum(signed(l) * l.premium * l.qty * _mult(l) for l in legs), 2)
        front_legs = [l for l in legs if expiry_date(l.expiry) == front]
        strikes = sorted({l.strike for l in legs})
        row = {"structure_id": sid, "parent": parent,
               "coverage": (profiles.get(parent) or {}).get("coverage", ""),
               "entity_name": g["entity_name"], "token": g["token"],
               "structure": cls["name"], "kind": cls["kind"], "bias": cls["bias"],
               "detail": cls["detail"], "n_legs": len(legs),
               "trade_date": pd.Timestamp(g["trade_time"]).date(), "expiry": front,
               "last_expiry": exps[-1], "dte": (front - today).days,
               "notional_usd": sum(l.notional for l in legs), "premium_usd": premium,
               "spot": spot, "strikes": strikes}
        if spot:
            near = min((l.strike for l in front_legs), key=lambda k: abs(k - spot))
            payoff = sum(signed(l) * _intrinsic(l, spot) * l.qty * _mult(l) for l in front_legs)
            row.update(nearest_strike=near, nearest_pct=abs(near - spot) / spot,
                       payoff_usd=payoff,
                       # P&L only means something when the whole structure expires together
                       pnl_usd=payoff - premium if len(exps) == 1 else float("nan"))
        else:
            row.update(nearest_strike=float("nan"), nearest_pct=float("nan"),
                       payoff_usd=float("nan"), pnl_usd=float("nan"))
        row["pin"] = bool(row["dte"] <= PIN_DAYS and row["nearest_pct"] <= PIN_PCT)
        row["status"] = _status(row)
        row["talking_point"] = talking_point(row)
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=COLS)
    return pd.DataFrame(rows)[COLS].sort_values(["expiry", "notional_usd"],
                                               ascending=[True, False]).reset_index(drop=True)


def _status(r: dict) -> str:
    if pd.isna(r["payoff_usd"]):
        return "No mark"
    if r["pin"]:
        return "Pin risk"
    if r["kind"] == "calendar":
        return "Front leg ITM" if r["payoff_usd"] else "Front leg OTM"
    if r["payoff_usd"] > 0:
        return "In the money"
    if r["payoff_usd"] < 0:
        return "Against them"
    return "Expiring worthless"


def _k(x: float) -> str:
    return f"{x:,.0f}" if x >= 100 else f"{x:,.2f}"


def _usd(x: float) -> str:
    a = abs(x)
    s = f"${a/1e6:.1f}M" if a >= 1e6 else f"${a/1e3:.0f}K" if a >= 1e3 else f"${a:,.0f}"
    return ("-" if x < 0 else "") + s


def talking_point(r: dict) -> str:
    """The one line to open the call with. Facts only — no recommendation."""
    when = r["expiry"].strftime("%a %d %b")
    head = f"{r['token']} {r['structure']} ({r['detail']}) expires {when}"
    if pd.isna(r["payoff_usd"]):
        return f"{head}."
    spot = f"spot {_k(r['spot'])}"
    if r["pin"]:
        return (f"{head} — {spot} is {r['nearest_pct']*100:.1f}% from the "
                f"{_k(r['nearest_strike'])} strike. Pin risk: offer to close or roll.")
    if r["kind"] == "calendar":
        return f"{head} — front leg expires first; roll the short leg to the next month?"
    credit = r["premium_usd"] < 0
    if r["payoff_usd"] == 0:
        if abs(r["premium_usd"]) < 1:
            return f"{head} — {spot}, zero-cost and all strikes OTM. Re-strike or let it go?"
        if credit:
            return (f"{head} — {spot}, all strikes OTM: on track to keep the "
                    f"{_usd(-r['premium_usd'])} credit. Re-strike for the next expiry?")
        return (f"{head} — {spot}, set to expire worthless ({_usd(r['premium_usd'])} "
                f"premium paid). Roll, or reload cheaper further out?")
    pnl = "" if pd.isna(r["pnl_usd"]) else f", {_usd(r['pnl_usd'])} P&L vs premium"
    if r["payoff_usd"] > 0:
        return f"{head} — {spot}, worth {_usd(r['payoff_usd'])} at expiry{pnl}. Roll or monetise?"
    return f"{head} — {spot}, owes {_usd(-r['payoff_usd'])} at expiry{pnl}. Hedge or roll?"


def apply_filters(df: pd.DataFrame, parents=None, expiry_from: date | None = None,
                  expiry_to: date | None = None, traded_from: date | None = None,
                  traded_to: date | None = None, min_notional: float = 0,
                  structures=None, tokens=None, coverage=None) -> pd.DataFrame:
    """Every filter is optional; empty list / None = no filter."""
    m = pd.Series(True, index=df.index)
    if parents:
        m &= df["parent"].isin(parents)
    if coverage:
        m &= df["coverage"].isin(coverage)
    if structures:
        m &= df["structure"].isin(structures)
    if tokens:
        m &= df["token"].isin(tokens)
    if expiry_from:
        m &= df["expiry"] >= expiry_from
    if expiry_to:
        m &= df["expiry"] <= expiry_to
    if traded_from:
        m &= df["trade_date"] >= traded_from
    if traded_to:
        m &= df["trade_date"] <= traded_to
    if min_notional:
        m &= df["notional_usd"] >= min_notional
    return df[m]


def ladder(df: pd.DataFrame) -> pd.DataFrame:
    """Per front expiry: structures, clients, notional, premium at stake."""
    if df.empty:
        return pd.DataFrame(columns=["expiry", "structures", "clients", "notional_usd",
                                     "premium_usd", "pins"])
    return df.groupby("expiry").agg(
        structures=("structure_id", "count"), clients=("parent", "nunique"),
        notional_usd=("notional_usd", "sum"), premium_usd=("premium_usd", "sum"),
        pins=("pin", "sum")).reset_index().sort_values("expiry")


def pin_risk(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["pin"]].sort_values(["dte", "nearest_pct"])


def week_window(today: date) -> tuple[date, date]:
    """The Monday note's window: the coming Monday (today, if Monday) + 7 days."""
    start = today + timedelta(days=(7 - today.weekday()) % 7)
    return start, start + timedelta(days=DIGEST_DAYS - 1)


def digest(df: pd.DataFrame, salesperson: str, start: date, end: date) -> str:
    """Plain-text Monday note for one salesperson (Slack / email ready)."""
    mine = df[(df["coverage"] == salesperson) & (df["expiry"] >= start) & (df["expiry"] <= end)]
    head = f"Expiries {start:%a %d %b} – {end:%a %d %b} · {salesperson}"
    if mine.empty:
        return f"{head}\nNothing of yours expires this week."
    lines = [head, f"{len(mine)} structures · {mine['parent'].nunique()} clients · "
                   f"{_usd(mine['notional_usd'].sum())} notional"
                   + (f" · ⚠️ {int(mine['pin'].sum())} pin risk" if mine["pin"].any() else ""),
             ""]
    for parent, g in mine.sort_values(["pin", "notional_usd"], ascending=False).groupby(
            "parent", sort=False):
        lines.append(f"• {parent}")
        lines += [f"    – {r.talking_point}" for r in g.sort_values("expiry").itertuples()]
    return "\n".join(lines)


def build(book: pd.DataFrame, profiles: dict, prices: pd.DataFrame, today: date) -> dict:
    lv = live(book, profiles, prices, today)
    return {"live": lv, "ladder": ladder(lv), "pins": pin_risk(lv),
            "week": week_window(today),
            "coverage": sorted(set(lv["coverage"]) - {""})}
