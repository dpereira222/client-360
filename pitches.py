"""
Pitch tracker — "here's the trade we pitched, it's up X%, and you did / didn't take it."

Inputs (all via data.py):
  • trade ideas extracted from the client's Telegram chat (what we SAID)
  • the unified blotter (what they DID)
  • daily market closes (what HAPPENED)

Three steps, all pure pandas:
  1. match_executions  — did the client actually put the idea on with us?
                         Inferred from the blotter, never from the chat alone:
                         "done 10M" in chat that never fills is a flag, and a
                         "pass" that quietly trades 3 days later is a win.
  2. performance       — honest track record. Each idea closes at the FIRST of
                         target / stop / horizon end; until then it is marked to
                         today. No hindsight: an idea that hit its stop and then
                         rallied is still a loss.
  3. talking_points    — say-it-out-loud lines for the next call.

No Streamlit, no Databricks, no LLM.
"""
from __future__ import annotations
import json
import math
from datetime import date, datetime, timedelta

import pandas as pd

MATCH_DAYS = 14          # how long after a pitch a fill still counts as "took it"
MATCH_MIN_SCORE = 0.5
# Implied vol used to mark pitched option structures (prod: the desk vol surface).
DEFAULT_VOL = {"BTC": 0.50, "ETH": 0.65, "SOL": 0.80, "XRP": 0.85, "AVAX": 0.90,
               "LINK": 0.85, "ARB": 1.00, "OP": 1.00}


def _d(x) -> date:
    return pd.Timestamp(x).date()


def _sgn(direction: str) -> int:
    return 1 if direction == "Long" else -1


# ── 1. Pitch → execution matching ─────────────────────────────────────────────
def _size_score(ratio: float) -> float:
    if 0.4 <= ratio <= 1.15:
        return 1.0
    if 0.2 <= ratio <= 1.5:
        return 0.35
    return 0.0


def _time_score(lag_days: float) -> float:
    if lag_days < 0:
        return 0.0
    return 1.0 if lag_days <= 1 else max(0.5, 1.0 - 0.5 * (lag_days - 1) / (MATCH_DAYS - 1))


def stated_size(text) -> float | None:
    """'done $6M' / 'ok go — 10m' / 'we're in for 2.5mm' → USD notional."""
    if not isinstance(text, str):
        return None
    import re
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(mm|m|k)\b", text, re.I)
    if not m:
        return None
    mult = 1e3 if m.group(2).lower() == "k" else 1e6
    return float(m.group(1)) * mult


def _usual_sizes(trades: pd.DataFrame, tok: str, prod: str, before) -> pd.Series:
    """Client's own ticket sizes in this token/product over the prior 90 days."""
    tt = pd.to_datetime(trades["trade_time"])
    b = pd.Timestamp(before)
    m = (trades["token"] == tok) & (trades["product"] == prod) & (tt < b) \
        & (tt >= b - pd.Timedelta(days=90))
    return trades.loc[m, "notional_usd"]


def _unusual(notional: float, usual: pd.Series) -> float:
    """A pitch-sized fill should stand out from the client's normal flow.
    Percentile of this ticket within their usual sizes, mapped to a weight:
    a top-5% ticket keeps full weight, a median-sized one is heavily discounted."""
    if usual.empty:
        return 1.0
    pct = float((usual < notional).mean())
    return 1.0 if pct >= 0.95 else 0.85 if pct >= 0.85 else 0.4


def _legs(idea) -> list[dict]:
    lj = idea.get("legs_json")
    return json.loads(lj) if isinstance(lj, str) and lj else []


def _candidates(idea, trades: pd.DataFrame) -> list[dict]:
    """Score every plausible blotter fill for one idea."""
    ts = pd.Timestamp(idea["ts"])
    tt = pd.to_datetime(trades["trade_time"])
    win = trades[(tt >= ts) & (tt <= ts + pd.Timedelta(days=MATCH_DAYS))]
    if win.empty:
        return []
    side = "Buy" if idea["direction"] == "Long" else "Sell"
    size = float(idea["size_usd"]) or 1.0
    out = []

    if idea["kind"] == "Directional":
        c = win[(win["token"] == idea["token"]) & (win["side"] == side)
                & (win["product"] == idea["product"])]
        usual = _usual_sizes(trades, idea["token"], idea["product"], ts)
        stated = stated_size(idea.get("reaction_text"))
        for i, r in c.iterrows():
            lag = (pd.Timestamp(r["trade_time"]) - ts).total_seconds() / 86400
            n = r["notional_usd"]
            if stated and lag <= 1 and abs(n / stated - 1) <= 0.15:
                s, tier = 1.0, "Confirmed"       # said "done $6M", $6M printed same day
            else:
                s = 0.8 * _size_score(n / size) * _time_score(lag) * _unusual(n, usual)
                tier = "Likely"
            if s:
                out.append({"score": s, "tier": tier, "rows": [i], "ts": r["trade_time"],
                            "notional": n, "px": r["price"]})

    elif idea["kind"] == "Relative Value":
        other = "Sell" if side == "Buy" else "Buy"
        a = win[(win["token"] == idea["token"]) & (win["side"] == side)
                & (win["product"] == "Derivatives")]
        b = win[(win["token"] == idea["token2"]) & (win["side"] == other)
                & (win["product"] == "Derivatives")]
        for i, r in a.iterrows():
            t0 = pd.Timestamp(r["trade_time"])
            pair = b[(pd.to_datetime(b["trade_time"]) - t0).abs() <= pd.Timedelta(hours=1)]
            if pair.empty:
                continue
            j = pair.index[0]
            lag = (t0 - ts).total_seconds() / 86400
            s = _size_score(r["notional_usd"] / size) * _time_score(lag)
            if s:
                ratio = r["price"] / trades.loc[j, "price"]
                out.append({"score": s, "tier": "Confirmed" if s >= 0.9 else "Likely",
                            "rows": [i, j], "ts": r["trade_time"],
                            "notional": r["notional_usd"], "px": ratio})

    else:  # Options: the strikes / expiry we quoted are the fingerprint
        want = {(l["right"], float(l["strike"]), l["expiry"]) for l in _legs(idea)}
        opt = win[(win["token"] == idea["token"]) & (win["product"] == "Options")]
        if opt.empty or not want:
            return out
        import options as _o
        key = opt["structure_id"].fillna(opt.index.to_series().astype(str))
        for sid, g in opt.groupby(key):
            got = set()
            for r in g.itertuples():
                p = _o.parse_instrument(r.instrument)
                if p:
                    tok, exp, strike, right = p
                    got.add((right, float(strike), _iso_expiry(exp)))
            overlap = len(want & got) / len(want)
            if overlap < 0.99:
                continue
            t0 = pd.Timestamp(g["trade_time"].min())
            lag = (t0 - ts).total_seconds() / 86400
            # exact strikes + expiry we quoted: a fingerprint, not a coincidence
            out.append({"score": _time_score(lag), "tier": "Confirmed",
                        "rows": list(g.index), "ts": t0,
                        "notional": float(g["qty"].max() * _spot_from_legs(idea)),
                        "px": None})
    return out


def _iso_expiry(exp: str) -> str:
    """'26AUG26' → '2026-08-26' (matches the legs_json expiry format)."""
    try:
        return datetime.strptime(exp, "%d%b%y").date().isoformat()
    except ValueError:
        return exp


def _spot_from_legs(idea) -> float:
    return float(idea["entry_px"])


def match_executions(ideas: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """One row per idea: executed?, when, how much, at what price, how sure.

    A blotter row is claimed by at most one idea (greedy, best score first), so
    two similar pitches can't both take credit for the same fill."""
    cols = ["idea_id", "executed", "exec_ts", "exec_notional", "exec_px",
            "fill_ratio", "match_score", "match_tier", "lag_hours", "exec_flag"]
    if ideas is None or ideas.empty:
        return pd.DataFrame(columns=cols)
    cands = []
    if trades is not None and not trades.empty:
        for _, idea in ideas.iterrows():
            for c in _candidates(idea, trades):
                cands.append((c["score"], idea["idea_id"], c))
    cands.sort(key=lambda x: -x[0])
    used, best = set(), {}
    for score, iid, c in cands:
        if iid in best or score < MATCH_MIN_SCORE or used & set(c["rows"]):
            continue
        best[iid] = c
        used |= set(c["rows"])

    rows = []
    for _, idea in ideas.iterrows():
        c = best.get(idea["idea_id"])
        said_yes = idea.get("reaction") == "Done"
        if c:
            lag_h = (pd.Timestamp(c["ts"]) - pd.Timestamp(idea["ts"])).total_seconds() / 3600
            flag = "Took it" if said_yes else \
                "No reply, then traded" if idea.get("reaction") == "No response" else \
                "Passed, then traded"
            rows.append({"idea_id": idea["idea_id"], "executed": True, "exec_ts": c["ts"],
                         "exec_notional": float(c["notional"]),
                         "exec_px": c["px"],
                         "fill_ratio": float(c["notional"]) / float(idea["size_usd"]),
                         "match_score": round(c["score"], 2), "match_tier": c["tier"],
                         "lag_hours": round(lag_h, 1),
                         "exec_flag": flag})
        else:
            rows.append({"idea_id": idea["idea_id"], "executed": False, "exec_ts": None,
                         "exec_notional": 0.0, "exec_px": None, "fill_ratio": 0.0,
                         "match_score": 0.0, "match_tier": None, "lag_hours": None,
                         "exec_flag": "Said done — no fill" if said_yes else "Didn't take it"})
    return pd.DataFrame(rows, columns=cols)


# ── 2. Performance ────────────────────────────────────────────────────────────
def _closes(px: pd.DataFrame) -> dict:
    """{token: Series(close, index=date)}"""
    return {t: g.set_index("date")["close"].sort_index() for t, g in px.groupby("token")}


def _path(closes: dict, idea) -> pd.Series:
    """Daily series of the pitched instrument (price, or ratio for RV)."""
    s = closes[idea["token"]]
    if idea["kind"] == "Relative Value":
        s = (s / closes[idea["token2"]]).dropna()
    return s


def _bs(spot: float, strike: float, t_years: float, vol: float, right: str) -> float:
    if t_years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    sd = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    if right == "C":
        return spot * N(d1) - strike * N(d2)
    return strike * N(-d2) - spot * N(-d1)


def _structure_value(legs: list[dict], spot: float, on: date, vol: float) -> float:
    """Value of one 'unit' of the structure (qty_mult contracts per leg)."""
    v = 0.0
    for l in legs:
        t = (date.fromisoformat(l["expiry"]) - on).days / 365
        sign = 1 if l["side"] == "Buy" else -1
        v += sign * l["qty_mult"] * _bs(spot, float(l["strike"]), t, vol, l["right"])
    return v


def performance(ideas: pd.DataFrame, px: pd.DataFrame, today) -> pd.DataFrame:
    """Track record per idea.

    status  : 'Target hit' | 'Stopped out' | 'Expired' | 'Open'
    ret_pct : signed return at close (or marked to today if Open), on the
              pitched notional. Options: structure P&L / pitched notional.
    pnl_usd : ret_pct × pitched size — what the idea made on the size we showed.
    ret_since_pct : signed return from pitch to today, regardless of close.
    """
    cols = ["idea_id", "status", "close_date", "close_px", "ret_pct", "pnl_usd",
            "days_held", "ret_since_pct", "last_px", "best_pct", "worst_pct"]
    if ideas is None or ideas.empty:
        return pd.DataFrame(columns=cols)
    today = _d(today)
    closes = _closes(px)
    rows = []
    for _, i in ideas.iterrows():
        t0 = _d(i["ts"])
        horizon_end = t0 + timedelta(days=int(i["horizon_days"]))
        sgn = _sgn(i["direction"])
        size = float(i["size_usd"])

        if i["kind"] == "Options":
            legs = _legs(i)
            spot_s = closes[i["token"]]
            vol = DEFAULT_VOL.get(i["token"], 0.7)
            entry_spot = float(i["entry_px"])
            units = size / entry_spot
            v0 = _structure_value(legs, entry_spot, t0, vol)
            last_exp = max(date.fromisoformat(l["expiry"]) for l in legs)
            end = min(today, last_exp)
            seg = spot_s[(spot_s.index > t0) & (spot_s.index <= end)]
            if seg.empty:
                seg = spot_s[spot_s.index <= t0].tail(1)
            vals = pd.Series([_structure_value(legs, s, d, vol) for d, s in seg.items()],
                             index=seg.index)
            pnl_path = (vals - v0) * units
            status = "Expired" if last_exp <= today else "Open"
            close_d, close_px = seg.index[-1], float(seg.iloc[-1])
            pnl = float(pnl_path.iloc[-1])
            rows.append({"idea_id": i["idea_id"], "status": status, "close_date": close_d,
                         "close_px": close_px, "ret_pct": pnl / size, "pnl_usd": pnl,
                         "days_held": (close_d - t0).days, "ret_since_pct": pnl / size,
                         "last_px": float(spot_s[spot_s.index <= today].iloc[-1]),
                         "best_pct": float(pnl_path.max()) / size,
                         "worst_pct": float(pnl_path.min()) / size})
            continue

        s = _path(closes, i)
        entry = float(i["entry_px"])
        seg = s[(s.index > t0) & (s.index <= min(today, horizon_end))]
        r = sgn * (seg / entry - 1)
        status, close_d = "Open", None
        tgt = i.get("target_px")
        stp = i.get("stop_px")
        for d, p in seg.items():
            if tgt == tgt and tgt is not None and sgn * (p - tgt) >= 0:
                status, close_d = "Target hit", d
                break
            if stp == stp and stp is not None and sgn * (p - stp) <= 0:
                status, close_d = "Stopped out", d
                break
        if close_d is None and horizon_end <= today and not seg.empty:
            status, close_d = "Expired", seg.index[-1]
        last = float(s[s.index <= today].iloc[-1])
        if close_d is None:
            close_d = seg.index[-1] if not seg.empty else t0
            close_px = last
        else:
            close_px = float(s.loc[close_d])
        ret = sgn * (close_px / entry - 1)
        held = r[r.index <= close_d]
        rows.append({"idea_id": i["idea_id"], "status": status, "close_date": close_d,
                     "close_px": close_px, "ret_pct": ret, "pnl_usd": ret * size,
                     "days_held": (close_d - t0).days,
                     "ret_since_pct": sgn * (last / entry - 1), "last_px": last,
                     "best_pct": float(held.max()) if not held.empty else 0.0,
                     "worst_pct": float(held.min()) if not held.empty else 0.0})
    return pd.DataFrame(rows, columns=cols)


def tracker(ideas: pd.DataFrame, trades: pd.DataFrame, px: pd.DataFrame, today) -> pd.DataFrame:
    """ideas ⨝ execution ⨝ performance, plus what the client made / missed."""
    if ideas is None or ideas.empty:
        return pd.DataFrame()
    m = match_executions(ideas, trades)
    p = performance(ideas, px, today)
    t = ideas.merge(m, on="idea_id", how="left").merge(p, on="idea_id", how="left")
    # Client's own P&L on what they executed, from their fill price where we
    # have one (directional / RV), else pro-rata of the idea's P&L.
    cap = []
    for _, r in t.iterrows():
        if not r["executed"]:
            cap.append(0.0)
            continue
        if r["exec_px"] is not None and r["exec_px"] == r["exec_px"] and r["kind"] != "Options":
            ret = _sgn(r["direction"]) * (r["close_px"] / r["exec_px"] - 1)
            cap.append(ret * r["exec_notional"])
        else:
            cap.append(r["pnl_usd"] * min(1.0, r["fill_ratio"]))
    t["client_pnl_usd"] = cap
    t["missed_pnl_usd"] = t.apply(
        lambda r: r["pnl_usd"] if (not r["executed"] and r["pnl_usd"] > 0) else 0.0, axis=1)
    t["worked"] = t["ret_pct"] > 0
    t["headline"] = t.apply(headline, axis=1)
    return t.sort_values("ts", ascending=False).reset_index(drop=True)


# ── Display helpers ───────────────────────────────────────────────────────────
def fmt_px(p) -> str:
    p = float(p)
    return f"{p:,.0f}" if p >= 100 else f"{p:,.2f}" if p >= 1 else f"{p:.4f}"


def _usd(x: float) -> str:
    a = abs(x)
    s = f"${a/1e6:.1f}M" if a >= 1e6 else f"${a/1e3:.0f}K" if a >= 1e3 else f"${a:,.0f}"
    return ("−" if x < 0 else "") + s


def headline(r) -> str:
    """'Long SOL spot @ 143.36' / 'Long SOL / short ETH' / 'BTC bull call spread'."""
    if r["kind"] == "Options":
        return f"{r['token']} {str(r['structure']).lower()}"
    if r["kind"] == "Relative Value":
        return f"Long {r['token']} / short {r['token2']}" if r["direction"] == "Long" \
            else f"Short {r['token']} / long {r['token2']}"
    inst = "spot" if r["product"] == "Spot" else "perp"
    return f"{r['direction']} {r['token']} {inst}"


def _h(r) -> str:
    """Headline for use mid-sentence: 'long SOL spot', 'BTC bull call spread'."""
    h = r["headline"]
    return h[0].lower() + h[1:] if h.split()[0] in ("Long", "Short") else h


# ── 3. Scorecard + talking points ─────────────────────────────────────────────
def scorecard(t: pd.DataFrame) -> dict:
    if t is None or t.empty:
        return {}
    closed = t[t["status"] != "Open"]
    return {
        "pitched": int(len(t)),
        "executed": int(t["executed"].sum()),
        "take_rate": float(t["executed"].mean()),
        "hit_rate": float((closed["ret_pct"] > 0).mean()) if not closed.empty else None,
        "n_closed": int(len(closed)),
        "avg_ret": float(t["ret_pct"].mean()),
        "pitched_pnl": float(t["pnl_usd"].sum()),
        "client_pnl": float(t["client_pnl_usd"].sum()),
        "missed_pnl": float(t["missed_pnl_usd"].sum()),
        "said_done_no_fill": int((t["exec_flag"] == "Said done — no fill").sum()),
        "passed_then_traded": int(t["exec_flag"].isin(["Passed, then traded",
                                                        "No reply, then traded"]).sum()),
    }


def by_pitcher(t: pd.DataFrame) -> pd.DataFrame:
    if t is None or t.empty:
        return pd.DataFrame()
    g = t.groupby("pitched_by").agg(
        pitched=("idea_id", "size"), taken=("executed", "sum"),
        hit_rate=("worked", "mean"), avg_ret=("ret_pct", "mean"),
        pnl_usd=("pnl_usd", "sum")).reset_index()
    return g.sort_values("pitched", ascending=False)


def pass_reasons(t: pd.DataFrame) -> pd.DataFrame:
    if t is None or t.empty:
        return pd.DataFrame()
    x = t[~t["executed"]].copy()
    x["why"] = x["reason"].fillna(x["reaction"])
    x.loc[x["reaction"] == "Done", "why"] = "Said done — no fill"
    return x.groupby("why").agg(n=("idea_id", "size"),
                                would_have_made=("pnl_usd", "sum")).reset_index() \
        .sort_values("n", ascending=False)


def callbacks(t: pd.DataFrame, px: pd.DataFrame, today) -> pd.DataFrame:
    """Client said 'call me at X' — did the market get there afterwards?"""
    if t is None or t.empty or "callback_level" not in t:
        return pd.DataFrame()
    closes = _closes(px)
    rows = []
    for _, r in t.dropna(subset=["callback_level"]).iterrows():
        s = closes[r["token"]]
        after = s[(s.index > _d(r["reaction_ts"] or r["ts"])) & (s.index <= _d(today))]
        lvl = float(r["callback_level"])
        hit = after[(after - lvl) * _sgn(r["direction"]) <= 0]
        rows.append({"idea_id": r["idea_id"], "headline": r["headline"], "token": r["token"],
                     "asked_level": lvl, "asked_on": _d(r["reaction_ts"] or r["ts"]),
                     "reached": not hit.empty,
                     "reached_on": hit.index[0] if not hit.empty else None,
                     "last_px": float(s[s.index <= _d(today)].iloc[-1]),
                     "executed": bool(r["executed"])})
    return pd.DataFrame(rows)


def talking_points(t: pd.DataFrame, cb: pd.DataFrame, contact: str, limit: int = 6) -> list[dict]:
    """Ranked by $ relevance. Every line cites the date, level and size we pitched."""
    if t is None or t.empty:
        return []
    pts = []
    for _, r in t.iterrows():
        d = pd.Timestamp(r["ts"]).strftime("%b %d")
        lvl = f"{r['entry_px']:.4f}" if r["kind"] == "Relative Value" else \
            f"spot {fmt_px(r['entry_px'])}" if r["kind"] == "Options" else fmt_px(r["entry_px"])
        ret = f"{r['ret_pct']*100:+.0f}%"
        state = {"Target hit": f"hit our target in {r['days_held']}d",
                 "Stopped out": f"stopped out after {r['days_held']}d",
                 "Expired": "ran to the end of the horizon",
                 "Open": "still running"}[r["status"]]
        if r["executed"] and r["ret_pct"] > 0:
            pts.append({"kind": "Win together", "weight": abs(r["client_pnl_usd"]),
                        "idea_id": r["idea_id"],
                        "say": f"Good trade, {contact} — the {_h(r)} you put on with us "
                               f"after our {d} pitch ({lvl}) {state}, {ret} "
                               f"(≈{_usd(r['client_pnl_usd'])} on your {_usd(r['exec_notional'])})."})
        elif not r["executed"] and r["ret_pct"] > 0.03:
            pts.append({"kind": "Missed", "weight": r["missed_pnl_usd"],
                        "idea_id": r["idea_id"],
                        "say": f"The {_h(r)} we showed you on {d} at {lvl} {state}: "
                               f"{ret}, ≈{_usd(r['pnl_usd'])} on the {_usd(r['size_usd'])} we offered."
                               + (f" You passed — '{r['reason'].lower()}'." if r.get("reason") else "")})
        elif r["executed"] and r["ret_pct"] < -0.03:
            pts.append({"kind": "Heads-up", "weight": abs(r["client_pnl_usd"]) * 0.8,
                        "idea_id": r["idea_id"],
                        "say": f"Be ready to own it: the {_h(r)} from {d} {state} "
                               f"({ret}, ≈{_usd(r['client_pnl_usd'])} for them)."})
        if r["exec_flag"] == "Said done — no fill":
            pts.append({"kind": "Check booking", "weight": float(r["size_usd"]) * 0.05,
                        "idea_id": r["idea_id"],
                        "say": f"{contact} said '{r['reaction_text']}' on the {d} {_h(r)} "
                               f"but there's no matching fill on the blotter — booked elsewhere or fell through?"})
    if cb is not None and not cb.empty:
        for _, c in cb[cb["reached"] & ~cb["executed"]].iterrows():
            pts.append({"kind": "Level reached", "weight": 5e5, "idea_id": c["idea_id"],
                        "say": f"You asked us to call at {fmt_px(c['asked_level'])} on {c['token']} "
                               f"({c['asked_on']:%b %d}) — it traded there {c['reached_on']:%b %d}. "
                               f"Now {fmt_px(c['last_px'])}."})
    pts.sort(key=lambda p: -p["weight"])
    return pts[:limit]


# ── Chat analytics (no LLM) ───────────────────────────────────────────────────
def chat_stats(msgs: pd.DataFrame) -> dict:
    """Engagement: volume, who talks, how fast the client answers us."""
    if msgs is None or msgs.empty:
        return {}
    m = msgs.sort_values("ts")
    lag = []
    by_id = m.set_index("msg_id")
    for _, r in m[(m["sender_role"] == "Client") & m["reply_to_msg_id"].notna()].iterrows():
        q = by_id.loc[int(r["reply_to_msg_id"])]
        if q["sender_role"] == "FalconX":
            lag.append((pd.Timestamp(r["ts"]) - pd.Timestamp(q["ts"])).total_seconds() / 60)
    weeks = max(1.0, (pd.Timestamp(m["ts"].max()) - pd.Timestamp(m["ts"].min())).days / 7)
    return {"messages": int(len(m)), "per_week": len(m) / weeks,
            "client_share": float((m["sender_role"] == "Client").mean()),
            "median_reply_min": float(pd.Series(lag).median()) if lag else None,
            "last_client_msg": m[m["sender_role"] == "Client"]["ts"].max(),
            "last_fx_msg": m[m["sender_role"] == "FalconX"]["ts"].max()}


def weekly_volume(msgs: pd.DataFrame) -> pd.DataFrame:
    if msgs is None or msgs.empty:
        return pd.DataFrame()
    m = msgs.copy()
    m["week"] = pd.to_datetime(m["ts"]).dt.to_period("W").dt.start_time
    return m.groupby(["week", "sender_role"]).size().reset_index(name="messages")


def interests(msgs: pd.DataFrame, traded_tokens: list[str], universe: list[str]) -> pd.DataFrame:
    """Tokens the CLIENT asks about in chat, and whether they trade them with us.
    Asked-about-but-not-traded = latent demand → pitch / onboard the product."""
    if msgs is None or msgs.empty:
        return pd.DataFrame()
    import re
    cl = msgs[msgs["sender_role"] == "Client"]
    rows = []
    for tok in dict.fromkeys(universe):
        pat = re.compile(rf"\b{re.escape(tok)}\b", re.I)
        hits = cl[cl["text"].str.contains(pat)]
        if hits.empty:
            continue
        rows.append({"token": tok, "mentions": int(len(hits)),
                     "last_mentioned": hits["ts"].max(),
                     "trades_with_us": tok in traded_tokens})
    df = pd.DataFrame(rows)
    return df.sort_values(["trades_with_us", "mentions"], ascending=[True, False]) \
        .reset_index(drop=True) if not df.empty else df


def rfq_stats(rfqs: pd.DataFrame) -> dict:
    if rfqs is None or rfqs.empty:
        return {}
    n = len(rfqs)
    return {"rfqs": n, "hit_rate": float((rfqs["response"] == "Done").mean()),
            "too_wide": int((rfqs["reason"] == "Too wide").sum()),
            "traded_away": int((rfqs["reason"] == "Traded away").sum()),
            "no_response": int((rfqs["response"] == "No response").sum()),
            "notional_quoted": float(rfqs["notional_usd"].sum()),
            "notional_done": float(rfqs.loc[rfqs["response"] == "Done", "notional_usd"].sum())}


# ── Tab assembly ──────────────────────────────────────────────────────────────
# Tokens we listen for in client chat. Prod: the desk's listed + pipeline names.
WATCHLIST = ["BTC", "ETH", "SOL", "XRP", "AVAX", "LINK", "ARB", "OP",
             "HYPE", "SUI", "TAO", "ENA", "DOGE", "TON", "SEI", "PEPE"]


def build(parent: str, profile: dict | None, ideas: pd.DataFrame, trades: pd.DataFrame,
          px: pd.DataFrame, msgs: pd.DataFrame, rfqs: pd.DataFrame, today) -> dict:
    """Everything the Pitches & Chat tab draws, in one dict (app.py stays dumb)."""
    t = tracker(ideas, trades, px, today)
    cb = callbacks(t, px, today)
    contact = (profile or {}).get("contact") or parent
    traded = trades["token"].unique().tolist() if trades is not None and not trades.empty else []
    return {"tracker": t, "score": scorecard(t), "by_pitcher": by_pitcher(t),
            "pass_reasons": pass_reasons(t), "callbacks": cb,
            "points": talking_points(t, cb, contact), "contact": contact,
            "chat": chat_stats(msgs), "weekly": weekly_volume(msgs),
            "interests": interests(msgs, traded, WATCHLIST),
            "rfq": rfq_stats(rfqs), "rfqs": rfqs if rfqs is not None else pd.DataFrame(),
            "msgs": msgs if msgs is not None else pd.DataFrame()}
