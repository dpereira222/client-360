"""
Mock Telegram chats between FalconX coverage and each client.

Generates, deterministically:
  • messages(parent)     — the raw chat, as the Telegram bot / archive would land it
  • trade_ideas(parent)  — what the chat-extraction job would pull out of it
                           (pitches + the client's verbal reaction)
  • rfqs(parent)         — client price requests, our quote, and the outcome
  • fill_trades(parent)  — blotter rows for what the client ACTUALLY executed.
                           Injected into mock.trades() so pitches.py can match
                           "what they said" against "what they did".

Execution ground truth (_EXEC) is deliberately NOT exposed through data.py —
the app has to infer it from the blotter, exactly as it will in production.
Engineering never reads this file.
"""
from __future__ import annotations
import hashlib
import json
import random
from datetime import date, datetime, timedelta

import pandas as pd

import mock

# FalconX people who appear in client chats → Telegram handle.
FX_STAFF = {
    "Sarah Kwan": "@sarahk_fx", "Marcus Vell": "@marcusv_fx", "Deepa Rao": "@deepar_fx",
    "Leo Brandt": "@leob_fxderivs",          # options desk — pitches vol ideas
}

# Desk ideas: one view, broadcast to several clients. (days_ago, …)
# kind: Directional | Options | Relative Value
DESK_IDEAS = [
    dict(days_ago=170, kind="Directional", token="SOL", direction="Long", product="Spot",
         target=0.15, stop=-0.08, horizon=45, thesis="ETF approval odds rising and Firedancer on mainnet"),
    dict(days_ago=152, kind="Options", token="SOL", direction="Long", structure="Risk Reversal",
         legs=[("C", 1.10, "Buy", 2), ("P", 0.90, "Sell", 2)], horizon=60,
         thesis="skew still bid for puts — sell downside to fund upside"),
    dict(days_ago=140, kind="Directional", token="BTC", direction="Long", product="Derivatives",
         target=0.08, stop=-0.05, horizon=30, thesis="quarter-end ETF inflows and funding still flat"),
    dict(days_ago=121, kind="Relative Value", token="BTC", token2="ETH", direction="Long",
         target=0.10, stop=-0.06, horizon=60, thesis="ETH keeps bleeding vs BTC on L2 fee compression"),
    dict(days_ago=108, kind="Directional", token="ARB", direction="Short", product="Derivatives",
         target=0.15, stop=-0.10, horizon=40, thesis="unlock overhang — 1.1B tokens hitting the market"),
    dict(days_ago=96, kind="Options", token="BTC", direction="Long", structure="Bull Call Spread",
         legs=[("C", 1.00, "Buy", 2), ("C", 1.12, "Sell", 2)], horizon=60,
         thesis="cheap way to play the breakout with vol in the 40s"),
    dict(days_ago=82, kind="Options", token="ETH", direction="Long", structure="Long Straddle",
         legs=[("C", 1.00, "Buy", 1), ("P", 1.00, "Buy", 1)], horizon=30,
         thesis="upgrade event in 3 weeks and implied looks too low"),
    dict(days_ago=71, kind="Directional", token="LINK", direction="Long", product="Spot",
         target=0.12, stop=-0.08, horizon=40, thesis="CCIP volumes inflecting and staking v2 lockups"),
    dict(days_ago=60, kind="Directional", token="AVAX", direction="Long", product="Spot",
         target=0.15, stop=-0.08, horizon=40, thesis="subnet launches pipeline looks strong"),
    dict(days_ago=46, kind="Options", token="BTC", direction="Short", structure="Short Strangle",
         legs=[("C", 1.10, "Sell", 1), ("P", 0.90, "Sell", 1)], horizon=30,
         thesis="realized vol well under implied — get paid to sit in the range"),
    dict(days_ago=34, kind="Relative Value", token="SOL", token2="ETH", direction="Long",
         target=0.12, stop=-0.07, horizon=45, thesis="SOL taking DEX share from ETH L1"),
    dict(days_ago=24, kind="Directional", token="OP", direction="Short", product="Derivatives",
         target=0.15, stop=-0.10, horizon=30, thesis="superchain revenue share disappointing"),
    dict(days_ago=12, kind="Directional", token="XRP", direction="Long", product="Spot",
         target=0.12, stop=-0.08, horizon=30, thesis="ETF filings stacking up"),
]

# Client behaviour in chat: base take-rate, pass-reason weights, chattiness.
_BEHAVIOUR = {
    "Brevan Howard":  dict(take=0.36, ignore=0.15, size=(6, 25),
                           reasons={"Already positioned": 3, "Level / entry": 3, "Risk limits": 2,
                                    "Disagree with view": 1.5, "Timing": 1.5,
                                    "Better price elsewhere": 1}),
    "Jump Trading":   dict(take=0.30, ignore=0.20, size=(8, 30),
                           reasons={"Better price elsewhere": 4, "Already positioned": 2,
                                    "Level / entry": 1.5, "Disagree with view": 1}),
    "Wintermute":     dict(take=0.22, ignore=0.38, size=(5, 15),
                           reasons={"Risk limits": 2, "Better price elsewhere": 2,
                                    "Timing": 1.5, "Disagree with view": 1}),
    "Galaxy Digital": dict(take=0.42, ignore=0.12, size=(4, 15),
                           reasons={"Level / entry": 3, "Timing": 2, "Risk limits": 1.5,
                                    "Already positioned": 1}),
    "Cumberland":     dict(take=0.28, ignore=0.22, size=(5, 18),
                           reasons={"Better price elsewhere": 3, "Level / entry": 2,
                                    "Disagree with view": 1.5, "Timing": 1}),
    "Multicoin Capital": dict(take=0.40, ignore=0.15, size=(4, 14),
                              reasons={"Level / entry": 3, "Disagree with view": 2,
                                       "Already positioned": 2, "Timing": 1}),
    "Millennium":     dict(take=0.25, ignore=0.20, size=(8, 30),
                           reasons={"Risk limits": 3, "Better price elsewhere": 3,
                                    "Level / entry": 1.5, "Timing": 1}),
    "Two Sigma":      dict(take=0.15, ignore=0.45, size=(5, 20),
                           reasons={"Disagree with view": 2, "Better price elsewhere": 2,
                                    "Timing": 1}),
    "Pantera Capital": dict(take=0.38, ignore=0.18, size=(2, 8),
                            reasons={"Already positioned": 3, "Level / entry": 2,
                                     "Risk limits": 1}),
    "Fidelity Digital Assets": dict(take=0.20, ignore=0.30, size=(5, 20),
                                    reasons={"Disagree with view": 1, "Timing": 3,
                                             "Risk limits": 2, "Not set up for product": 1}),
    "Marathon Digital": dict(take=0.30, ignore=0.25, size=(2, 8),
                             reasons={"Timing": 2, "Level / entry": 2, "Risk limits": 1}),
    "QCP Capital":    dict(take=0.30, ignore=0.20, size=(4, 15),
                           reasons={"Better price elsewhere": 3, "Level / entry": 2,
                                    "Already positioned": 1}),
}

# Tokens clients talk about but (mostly) don't trade with us → "chat interests".
INTEREST_TOKENS = ["HYPE", "SUI", "TAO", "ENA", "SOL", "AVAX", "LINK"]

_DONE = ["done ${n}M", "let's do it, ${n}M pls", "ok go — {n}m", "yes print ${n}M",
         "we're in for ${n}M", "sounds good, ${n}M"]
_PASS = {
    "Already positioned": ["already have it on through another book", "we're already long, pass",
                           "have plenty of {tok} risk already"],
    "Level / entry": ["not at these levels — ping me at {lvl}", "would rather get it at {lvl}",
                      "too rich here, call me if we see {lvl}"],
    "Risk limits": ["out of risk budget till month end", "PM says no more {tok} exposure for now",
                    "can't add, we're at our gross limit"],
    "Better price elsewhere": ["got a tighter px elsewhere tbh", "street is showing better",
                               "already done away, sorry"],
    "Disagree with view": ["don't buy the thesis", "not convinced on {tok}",
                           "we see it the other way"],
    "Timing": ["want to wait for FOMC", "maybe next week", "after the CPI print"],
    "Not set up for product": ["not set up for {product} on that entity yet",
                               "can't do {product} from that fund"],
}
_LATER = ["let me think about it", "interesting, let me run it by the PM",
          "will come back to you"]
_FILLER_FX = ["gm — desk was net buyers into the close, ETF inflows strong",
              "sending over the weekly vol deck now", "quick one — call Thursday 10am?",
              "flows were two-way today, nothing major", "funding flipped negative on {tok} perps"]
_FILLER_CL = ["gm", "thx", "any color on {tok} flows today?", "what are you seeing on {itok}?",
              "are you guys doing anything in {itok}?", "seeing any size in {itok}?",
              "works for me", "👍"]


def _rng(parent: str, salt: str) -> random.Random:
    return random.Random(f"chat|{parent}|{salt}")


def _fmt_px(p: float) -> str:
    return f"{p:,.0f}" if p >= 100 else f"{p:,.2f}" if p >= 1 else f"{p:.4f}"


def _ts(days_ago: float, r: random.Random) -> datetime:
    base = datetime.combine(mock.TODAY, datetime.min.time()) - timedelta(days=days_ago)
    return base.replace(hour=r.randint(8, 20), minute=r.randint(0, 59))


def _pitcher(parent: str, kind: str) -> str:
    return "Leo Brandt" if kind == "Options" else mock.CLIENTS[parent]["coverage"]


# ── Core generator ────────────────────────────────────────────────────────────
_CACHE: dict = {}


def _build(parent: str) -> dict:
    if parent in _CACHE:
        return _CACHE[parent]
    c = mock.CLIENTS[parent]
    beh = _BEHAVIOUR[parent]
    r = _rng(parent, "build")
    contact = c.get("contact", "there")
    chat_id = -1001000000000 - int(hashlib.sha1(parent.encode()).hexdigest()[:8], 16) % 10**9
    chat_title = f"FalconX <> {parent}"
    msgs, ideas, rfqs, execs = [], [], [], {}

    def say(ts, sender, role, text, reply_to=None):
        mid = len(msgs) + 1
        msgs.append({"chat_id": chat_id, "chat_title": chat_title, "msg_id": mid,
                     "ts": ts, "sender": sender, "sender_role": role,
                     "text": text, "reply_to_msg_id": reply_to})
        return mid

    # 1) Pitches: desk ideas (broadcast) + a few bespoke ones.
    plan = []
    for i, d in enumerate(DESK_IDEAS):
        rr = _rng(parent, f"desk{i}")
        tradable = d["token"] in mock.BASE_PX
        if tradable and rr.random() < 0.72:
            plan.append((d["days_ago"] + rr.uniform(-1.5, 1.5), dict(d), f"DESK-{i:02d}"))
    for j in range(r.randint(5, 8)):
        tok = r.choice([t for t in c["tokens"] if t not in ("USDC", "USDT")])
        dirn = r.choice(["Long", "Long", "Short"])
        plan.append((r.uniform(5, 175), dict(
            kind="Directional", token=tok, direction=dirn,
            product=r.choice(["Spot", "Derivatives"]) if dirn == "Long" else "Derivatives",
            target=round(r.uniform(0.08, 0.18), 2), stop=-round(r.uniform(0.05, 0.10), 2),
            horizon=r.choice([21, 30, 45]),
            thesis=r.choice(["flows have turned on our desk", "positioning looks washed out",
                             "funding reset and OI rebuilding", "technical breakout on the weekly"])),
            None))
    plan.sort(key=lambda x: -x[0])

    for n, (days_ago, d, desk_id) in enumerate(plan):
        rr = _rng(parent, f"idea{n}")
        ts = _ts(days_ago, rr)
        tok = d["token"]
        px = mock.close_on(tok, ts.date())
        size = round(rr.uniform(*beh["size"]) * (0.6 if d["kind"] == "Options" else 1.0), 0)
        who = _pitcher(parent, d["kind"])
        idea_id = f"{parent.split()[0][:3].upper()}-{n:03d}"
        row = {"idea_id": idea_id, "parent": parent, "chat_id": chat_id, "ts": ts,
               "pitched_by": who, "kind": d["kind"], "token": tok,
               "token2": d.get("token2"), "direction": d["direction"],
               "product": d.get("product", "Options" if d["kind"] == "Options" else "Spot"),
               "structure": d.get("structure"), "legs_json": None,
               "entry_px": px, "target_px": None, "stop_px": None,
               "size_usd": size * 1e6, "horizon_days": d["horizon"],
               "thesis": d["thesis"], "desk_idea_id": desk_id}
        sgn = 1 if d["direction"] == "Long" else -1
        if d["kind"] == "Directional":
            row["target_px"] = round(px * (1 + sgn * d["target"]), 4)
            row["stop_px"] = round(px * (1 + sgn * d["stop"]), 4)
            verb = "buying" if sgn > 0 else "selling"
            inst = f"{tok} spot" if row["product"] == "Spot" else f"{tok}-PERP"
            text = rr.choice([
                f"Hi {contact} — desk idea: we like {verb} {inst} here ~{_fmt_px(px)}. "
                f"{d['thesis'].capitalize()}. Target {_fmt_px(row['target_px'])}, "
                f"stop {_fmt_px(row['stop_px'])}. Can work ${size:.0f}M if you want it.",
                f"{contact}, idea for you: {'long' if sgn > 0 else 'short'} {inst} at "
                f"{_fmt_px(px)} — {d['thesis']}. Looking for {_fmt_px(row['target_px'])} "
                f"over ~{d['horizon']}d, out at {_fmt_px(row['stop_px'])}. Happy to show ${size:.0f}M.",
            ])
        elif d["kind"] == "Relative Value":
            t2 = d["token2"]
            px2 = mock.close_on(t2, ts.date())
            ratio = px / px2
            row["entry_px"] = round(ratio, 6)
            row["target_px"] = round(ratio * (1 + d["target"]), 6)
            row["stop_px"] = round(ratio * (1 + d["stop"]), 6)
            row["product"] = "Derivatives"
            text = (f"RV idea {contact}: long {tok} / short {t2} at {ratio:.4f}. "
                    f"{d['thesis'].capitalize()}. Target {row['target_px']:.4f}, "
                    f"stop {row['stop_px']:.4f}. ${size:.0f}M a side, done in perps.")
        else:
            exp = (ts.date() + timedelta(days=d["horizon"]))
            legs = []
            for right, mny, side, qm in d["legs"]:
                tick = mock._tick(px)
                legs.append({"right": right, "strike": round(px * mny / tick) * tick,
                             "side": side, "qty_mult": qm, "expiry": exp.isoformat()})
            row["legs_json"] = json.dumps(legs)
            legtxt = " / ".join(f"{'buy' if l['side'] == 'Buy' else 'sell'} the "
                                f"{l['strike']:g}{l['right']}" for l in legs)
            text = (f"{tok} {d['structure'].lower()} for you {contact}: {legtxt} "
                    f"{mock._fmt_expiry(exp)} (spot {_fmt_px(px)}). {d['thesis'].capitalize()}. "
                    f"Can show up to ${size:.0f}M notional.")
        mid = say(ts, who, "FalconX", text)
        row["msg_id"] = mid

        # 2) Client's reaction.
        take = beh["take"]
        set_up = row["product"] in c["products"] or (d["kind"] == "Relative Value"
                                                     and "Derivatives" in c["products"])
        if not set_up:
            take = 0.04
        # Fit matters: ideas in tokens they don't trade land less often; their
        # favourite options structures land more (what targeting.py should learn).
        if set_up and tok not in c["tokens"]:
            take *= 0.5
        if set_up and d.get("structure") and d["structure"] in c.get("opt_pref", []):
            take = min(0.85, take + 0.25)
        if set_up and desk_id is not None and d["days_ago"] in (170, 152, 34):   # SOL ideas resonated
            take = min(0.85, take + 0.25)
        u = rr.random()
        lag_min = rr.choice([2, 5, 9, 15, 30, 55, 120, 240])
        rts = ts + timedelta(minutes=lag_min)
        if u < take:
            frac = rr.choice([1.0, 1.0, 0.5, 0.75])
            n_m = max(1, round(size * frac))
            rtxt = rr.choice(_DONE).format(n=n_m)
            reaction, reason = "Done", None
            fell = rr.random() < 0.10
            # fill what they SAID ("done $6M"), so chat and blotter agree
            execs[idea_id] = None if fell else ("filled", rts + timedelta(minutes=rr.randint(3, 90)),
                                                n_m / size)
        elif u < take + beh["ignore"]:
            reaction, reason, rtxt = "No response", None, None
            execs[idea_id] = None
        else:
            if not set_up:
                reason = "Not set up for product"
            elif rr.random() < 0.22:
                reason = "Later"
            else:
                rs = beh["reasons"]
                reason = rr.choices(list(rs), weights=list(rs.values()))[0]
            if reason == "Later":
                reaction, rtxt = "Later", rr.choice(_LATER)
                reason = None
            else:
                reaction = "Passed"
                lvl = row["entry_px"] * (1 - sgn * rr.uniform(0.04, 0.09))
                row["_level"] = lvl if reason == "Level / entry" and d["kind"] == "Directional" else None
                rtxt = rr.choice(_PASS[reason]).format(
                    tok=tok, product=row["product"].lower(), lvl=_fmt_px(lvl))
            # Some passes / "laters" come back days later and trade anyway.
            back = 0.35 if reaction == "Later" else 0.10
            execs[idea_id] = ("filled", rts + timedelta(days=rr.uniform(2, 9)),
                              rr.choice([0.5, 1.0])) if rr.random() < back and set_up else None
        row["reaction"] = reaction
        row["reason"] = reason
        row["reaction_ts"] = rts if rtxt else None
        row["reaction_text"] = rtxt
        row["callback_level"] = row.pop("_level", None)
        if rtxt:
            say(rts, contact, "Client", rtxt, reply_to=mid)
        ideas.append(row)

    # 3) RFQs — client asks, we quote.
    toks = [t for t in c["tokens"] if t in mock.BASE_PX]
    for k in range(r.randint(18, 30)):
        rr = _rng(parent, f"rfq{k}")
        ts = _ts(rr.uniform(1, 178), rr)
        tok = rr.choice(toks)
        px = mock.close_on(tok, ts.date())
        notional = rr.uniform(*beh["size"]) * 1e6 * rr.uniform(0.3, 1.0)
        qty = round(notional / px, -1 if px < 1000 else 0) or 1
        side = rr.choice(["Buy", "Sell"])
        half = px * rr.uniform(1.5, 6) / 1e4
        bid, ask = px - half, px + half
        m1 = say(ts, contact, "Client",
                 rr.choice([f"px on {qty:,.0f} {tok}?", f"can you show me a 2-way in {qty:,.0f} {tok}",
                            f"{qty:,.0f} {tok} — where are you?"]))
        qts = ts + timedelta(minutes=rr.randint(1, 4))
        who = c["coverage"]
        m2 = say(qts, who, "FalconX", f"{_fmt_px(bid)} / {_fmt_px(ask)}", reply_to=m1)
        u = rr.random()
        if u < 0.55:
            resp, why, txt = "Done", None, "mine" if side == "Buy" else "yours"
        elif u < 0.70:
            resp, why, txt = "Passed", "Too wide", rr.choice(["too wide", "pass, wide"])
        elif u < 0.85:
            resp, why, txt = "Passed", "Traded away", rr.choice(["traded away", "done elsewhere, thx"])
        else:
            resp, why, txt = "No response", None, None
        rts = qts + timedelta(seconds=rr.randint(10, 90))
        if txt:
            say(rts, contact, "Client", txt, reply_to=m2)
        rfqs.append({"rfq_id": f"RFQ-{parent.split()[0][:3].upper()}-{k:03d}", "parent": parent,
                     "chat_id": chat_id, "ts": ts, "token": tok, "side": side, "qty": qty,
                     "notional_usd": round(qty * px, 0), "product": "Spot",
                     "quote_bid": round(bid, 4), "quote_ask": round(ask, 4),
                     "quoted_by": who, "response": resp, "reason": why,
                     "response_ts": rts if txt else None})
        execs[f"rfq{k}"] = ("filled", rts, 1.0, ask if side == "Buy" else bid) if resp == "Done" else None

    # 4) Filler — market color, scheduling, and interest mentions.
    itoks = [t for t in INTEREST_TOKENS if t not in c["tokens"]]
    for k in range(r.randint(60, 90)):
        rr = _rng(parent, f"fill{k}")
        ts = _ts(rr.uniform(0.5, 179), rr)
        tok = rr.choice(toks)
        itok = rr.choice(itoks or toks) if rr.random() < 0.7 else tok
        if rr.random() < 0.5:
            say(ts, c["coverage"], "FalconX", rr.choice(_FILLER_FX).format(tok=tok))
            if rr.random() < 0.7:
                say(ts + timedelta(minutes=rr.choice([3, 10, 25, 60, 180])), contact, "Client",
                    rr.choice(_FILLER_CL).format(tok=tok, itok=itok))
        else:
            say(ts, contact, "Client", rr.choice(_FILLER_CL).format(tok=tok, itok=itok))

    msgs.sort(key=lambda m: m["ts"])
    out = {"messages": msgs, "ideas": ideas, "rfqs": rfqs, "execs": execs, "plan": plan}
    _CACHE[parent] = out
    return out


# ── Public mock API (called via data.py) ──────────────────────────────────────
def _win(df: pd.DataFrame, col: str, start: str, end: str) -> pd.DataFrame:
    if df.empty:
        return df
    s = pd.Timestamp(start)
    e = pd.Timestamp(end) + pd.Timedelta(days=1)
    t = pd.to_datetime(df[col])
    return df[(t >= s) & (t < e)].reset_index(drop=True)


def messages(parent: str, start: str, end: str) -> pd.DataFrame:
    return _win(pd.DataFrame(_build(parent)["messages"]), "ts", start, end)


def trade_ideas(parent: str, start: str, end: str) -> pd.DataFrame:
    return _win(pd.DataFrame(_build(parent)["ideas"]), "ts", start, end)


def rfqs(parent: str, start: str, end: str) -> pd.DataFrame:
    return _win(pd.DataFrame(_build(parent)["rfqs"]), "ts", start, end)


def ground_truth(parent: str) -> dict:
    """Hidden execution truth — tests only (measures matcher accuracy)."""
    return _build(parent)["execs"]


def fill_trades(parent: str) -> list[dict]:
    """Blotter rows for executed pitches / RFQs. Called from mock.trades()."""
    b = _build(parent)
    c = mock.CLIENTS[parent]
    eid, ename, _ = c["entities"][0]
    rows = []
    by_id = {i["idea_id"]: i for i in b["ideas"]}
    for key, ex in b["execs"].items():
        if not ex:
            continue
        if key.startswith("rfq"):
            q = b["rfqs"][int(key[3:])]
            _, ts, _f, px = ex
            notional = q["qty"] * px
            rows.append(mock._row(ts, eid, ename, "Spot", f"{q['token']}/USD", q["token"],
                                  q["side"], round(notional, 0), round(px, 4),
                                  abs(px - (q["quote_bid"] + q["quote_ask"]) / 2) / px * 1e4,
                                  qty=q["qty"]))
            continue
        _, ts, frac = ex
        i = by_id[key]
        r = _rng(parent, f"exec{key}")
        size = i["size_usd"] * frac
        side = "Buy" if i["direction"] == "Long" else "Sell"
        if i["kind"] == "Directional":
            px = mock.close_on(i["token"], ts.date())
            inst = f"{i['token']}/USD" if i["product"] == "Spot" else f"{i['token']}-PERP"
            rows.append(mock._row(ts, eid, ename, i["product"], inst, i["token"], side,
                                  round(size, 0), px, mock._bps_for(i["product"], r),
                                  qty=round(size / px, 4)))
        elif i["kind"] == "Relative Value":
            for tok, sd in ((i["token"], side), (i["token2"], "Sell" if side == "Buy" else "Buy")):
                px = mock.close_on(tok, ts.date())
                rows.append(mock._row(ts, eid, ename, "Derivatives", f"{tok}-PERP", tok, sd,
                                      round(size, 0), px, mock._bps_for("Derivatives", r),
                                      qty=round(size / px, 4)))
        else:
            spot = mock.close_on(i["token"], ts.date())
            contracts = round(size / spot, 2)
            sid = f"{eid}-CHAT-{key}"
            for leg in json.loads(i["legs_json"]):
                exp = date.fromisoformat(leg["expiry"])
                months = max(0.25, (exp - ts.date()).days / 30)
                prem = mock._premium(spot, leg["right"], leg["strike"] / spot, months)
                q = contracts * leg["qty_mult"]
                rows.append(mock._row(
                    ts, eid, ename, "Options",
                    f"{i['token']}-{mock._fmt_expiry(exp)}-{leg['strike']:g}-{leg['right']}",
                    i["token"], leg["side"], round(q * leg["strike"], 0), prem,
                    mock._bps_for("Options", r), qty=q, structure_id=sid))
    return rows
