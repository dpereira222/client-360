"""
Daily color — the 4:30pm ET note: what moved, what the desk saw, who did it.

Inputs (all via data.py): get_desk_flow (anonymous trade-level flow for the
whole desk), get_price_history (moves), get_profile / get_trades (only for the
per-client and per-rep views — never in the shared note).
Outputs:
  • window       — the color day: 4:30pm ET yesterday → 4:30pm ET today, in UTC
  • moves        — per token: day return and σ-move vs trailing 30d vol
  • token_flow   — per token: activity vs the 20-day average, buy share, peers
  • segment_skew — which client types leaned which way (k-anon + dominance)
  • options_mix  — structures traded today by strategy / bias vs average
  • headlines    — ranked one-liners: the facts worth saying out loud
  • note         — the shareable plain-text color (Slack / email / Telegram)
  • for_client   — the same note re-ordered for one client, own flow excluded
  • rep_sheet    — internal: which of a rep's clients to send which line to

PRIVACY — the note is client-facing, so it carries RATIOS AND PERCENTAGES ONLY
(activity multiples, buy share, move sizes). Never desk dollar volumes, never
names, and every segment/strategy line passes peers.safe (≥ K_MIN distinct
peers, no single peer over DOMINANCE of the notional). Compliance should still
review the template once (WHITEPAPER §16).

No Streamlit, no Databricks. The LLM polish (COLOR_SYSTEM / color_context) is
optional — the note is complete without it.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import peers

ET = ZoneInfo("America/New_York")
CUTOFF = time(16, 30)          # 4:30pm ET
BASE_DAYS = 20                 # "vs the 20-day average"
VOL_DAYS = 30                  # σ for move detection
BIG_MOVE_Z = 1.5               # a move worth a line
BUSY = 1.5                     # activity ≥ 1.5× average = "busy"
QUIET = 0.6                    # ≤ 0.6× = "quiet"
SKEW = 0.15                    # buy share ≥ 65% or ≤ 35% = a lean
DIRECTIONAL = ("Spot", "Derivatives")
STABLES = ("USDC", "USDT")


# ── The color day ─────────────────────────────────────────────────────────────
def window(asof: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) in naive UTC for the color day ending 4:30pm ET on `asof`.
    DST-aware: 20:30 UTC in summer, 21:30 UTC in winter."""
    end = datetime.combine(asof, CUTOFF, tzinfo=ET)
    start = datetime.combine(asof - timedelta(days=1), CUTOFF, tzinfo=ET)
    utc = ZoneInfo("UTC")
    return (pd.Timestamp(start.astimezone(utc)).tz_localize(None),
            pd.Timestamp(end.astimezone(utc)).tz_localize(None))


def color_day(ts: pd.Series) -> pd.Series:
    """Map naive-UTC trade times to the color day they belong to (a trade at
    4:31pm ET counts toward tomorrow's note)."""
    et = pd.to_datetime(ts).dt.tz_localize("UTC").dt.tz_convert(ET)
    return (et + pd.Timedelta(hours=7, minutes=30)).dt.date


def history_window(asof: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Flow needed for one note: today + the BASE_DAYS before it."""
    return window(asof - timedelta(days=BASE_DAYS))[0], window(asof)[1]


# ── Moves ─────────────────────────────────────────────────────────────────────
def moves(prices: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Per token: close, 1-day return, σ-move. Columns: token, close, ret, z.
    Mock prices are daily closes; prod should mark at 4:30pm ET to match the
    flow window."""
    p = prices.assign(date=pd.to_datetime(prices["date"]).dt.date)
    w = p[p["date"] <= asof].pivot(index="date", columns="token", values="close").sort_index()
    w = w.tail(VOL_DAYS + 2)
    lr = np.log(w).diff()
    sd = lr.iloc[:-1].tail(VOL_DAYS).std()
    out = pd.DataFrame({"close": w.iloc[-1], "ret": np.exp(lr.iloc[-1]) - 1,
                        "z": lr.iloc[-1] / sd.replace(0, np.nan)})
    out = out.drop([t for t in STABLES if t in out.index])
    return (out.rename_axis("token").reset_index()
               .sort_values("z", key=lambda s: -s.abs()).reset_index(drop=True))


# ── Flow ──────────────────────────────────────────────────────────────────────
def _tag(flow: pd.DataFrame) -> pd.DataFrame:
    return flow.assign(day=color_day(flow["trade_time"])) if not flow.empty else \
        flow.assign(day=pd.Series(dtype=object))


def _base_days(asof: date) -> list[date]:
    return [asof - timedelta(days=i) for i in range(1, BASE_DAYS + 1)]


def token_flow(flow: pd.DataFrame, asof: date, exclude_peer: str | None = None) -> pd.DataFrame:
    """Per token, today vs the 20-day average. Buy share uses Spot + Derivatives
    only (credit draws and option premia aren't a directional view).
    Columns: token, activity_x, buy_share, n_peers, safe, lean."""
    f = _tag(flow)
    if exclude_peer:
        f = f[f["peer_id"] != exclude_peer]
    base_days = _base_days(asof)
    today = f[f["day"] == asof]
    base = f[f["day"].isin(base_days)]
    daily = base.groupby(["token", "day"])["notional_usd"].sum().unstack(fill_value=0.0)
    daily = daily.reindex(columns=base_days, fill_value=0.0)
    rows = []
    for tok, g in today.groupby("token"):
        if tok in STABLES:
            continue
        d = g[g["product"].isin(DIRECTIONAL)]
        tot = d["notional_usd"].sum()
        bs = d.loc[d["side"] == "Buy", "notional_usd"].sum() / tot if tot else float("nan")
        per_peer = g.groupby("peer_id")["notional_usd"].sum()
        avg = daily.loc[tok].mean() if tok in daily.index else 0.0
        rows.append({"token": tok,
                     "activity_x": g["notional_usd"].sum() / avg if avg else float("nan"),
                     "buy_share": bs, "n_peers": int(per_peer.size),
                     "safe": peers.safe(per_peer), "lean": _lean(bs)})
    out = pd.DataFrame(rows, columns=["token", "activity_x", "buy_share", "n_peers",
                                      "safe", "lean"])
    return out.sort_values("activity_x", ascending=False).reset_index(drop=True)


def _lean(bs: float) -> str:
    if pd.isna(bs):
        return "—"
    return "buyers" if bs >= 0.5 + SKEW else "sellers" if bs <= 0.5 - SKEW else "two-way"


def desk_activity(flow: pd.DataFrame, asof: date) -> dict:
    """Whole-desk activity multiple + product-mix shift (shares, not dollars)."""
    f = _tag(flow)
    base_days = _base_days(asof)
    today = f[f["day"] == asof]
    base = f[f["day"].isin(base_days)]
    tot_t, tot_b = today["notional_usd"].sum(), base["notional_usd"].sum() / BASE_DAYS
    mix_t = today.groupby("product")["notional_usd"].sum() / (tot_t or 1)
    mix_b = base.groupby("product")["notional_usd"].sum() / (base["notional_usd"].sum() or 1)
    mix = pd.DataFrame({"today": mix_t, "base": mix_b}).fillna(0.0)
    mix["shift_pp"] = (mix["today"] - mix["base"]) * 100
    return {"activity_x": tot_t / tot_b if tot_b else float("nan"),
            "n_peers": int(today["peer_id"].nunique()),
            "mix": mix.rename_axis("product").reset_index()}


def segment_skew(flow: pd.DataFrame, asof: date, tokens: list[str] | None = None,
                 exclude_peer: str | None = None) -> pd.DataFrame:
    """Which client types leaned which way, per token. Only rows that pass
    k-anonymity + dominance are returned as safe=True — the note uses only those.
    Columns: segment, label, token, buy_share, n_peers, share_of_flow, safe, lean."""
    f = _tag(flow)
    f = f[(f["day"] == asof) & f["product"].isin(DIRECTIONAL) & ~f["token"].isin(STABLES)]
    if exclude_peer:
        f = f[f["peer_id"] != exclude_peer]
    if tokens:
        f = f[f["token"].isin(tokens)]
    tok_tot = f.groupby("token")["notional_usd"].sum()
    rows = []
    for (seg, tok), g in f.groupby(["segment", "token"]):
        tot = g["notional_usd"].sum()
        bs = g.loc[g["side"] == "Buy", "notional_usd"].sum() / tot if tot else float("nan")
        per_peer = g.groupby("peer_id")["notional_usd"].sum()
        rows.append({"segment": seg, "label": peers._plural(seg), "token": tok,
                     "buy_share": bs, "n_peers": int(per_peer.size),
                     "share_of_flow": tot / tok_tot[tok] if tok_tot[tok] else 0.0,
                     "safe": peers.safe(per_peer), "lean": _lean(bs)})
    cols = ["segment", "label", "token", "buy_share", "n_peers", "share_of_flow", "safe", "lean"]
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["token", "share_of_flow"], ascending=[True, False]).reset_index(drop=True)


def options_mix(flow: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Structures traded today by strategy vs the daily average.
    Columns: strategy, bias, n_today, avg_daily, activity_x, top_token, n_peers, safe."""
    f = _tag(flow)
    f = f[(f["product"] == "Options") & f["strategy"].notna()]
    base = f[f["day"].isin(_base_days(asof))]
    today = f[f["day"] == asof]
    avg = base.groupby("strategy").size() / BASE_DAYS
    rows = []
    for s, g in today.groupby("strategy"):
        a = float(avg.get(s, 0.0))
        rows.append({"strategy": s, "bias": g["bias"].iloc[0], "n_today": len(g),
                     "avg_daily": a, "activity_x": len(g) / a if a else float("nan"),
                     "top_token": g["token"].value_counts().index[0],
                     "n_peers": int(g["peer_id"].nunique()),
                     "safe": peers.safe(g.groupby("peer_id")["notional_usd"].sum())})
    cols = ["strategy", "bias", "n_today", "avg_daily", "activity_x", "top_token",
            "n_peers", "safe"]
    return pd.DataFrame(rows, columns=cols).sort_values(
        "n_today", ascending=False).reset_index(drop=True)


def vol_tilt(om: pd.DataFrame) -> tuple[float, float]:
    """Share of today's structures that were long vol vs short vol."""
    n = om["n_today"].sum()
    if not n:
        return float("nan"), float("nan")
    by = om.groupby("bias")["n_today"].sum()
    return by.get("long vol", 0) / n, by.get("short vol", 0) / n


# ── Headlines ─────────────────────────────────────────────────────────────────
def _pct(x: float) -> str:
    return f"{x*100:+.1f}%"


def _x(x: float) -> str:
    return f"{x:.1f}×"


def headlines(mv: pd.DataFrame, tf: pd.DataFrame, sk: pd.DataFrame, om: pd.DataFrame,
              desk: dict) -> list[dict]:
    """Ranked facts. Each: {kind, token, text, score}. Only k-safe facts."""
    out = []
    tf_i = tf.set_index("token") if not tf.empty else tf
    for r in mv.itertuples():
        if pd.isna(r.z) or abs(r.z) < BIG_MOVE_Z:
            continue
        txt = f"{r.token} {_pct(r.ret)} ({abs(r.z):.1f}σ)"
        if r.token in tf_i.index and tf_i.loc[r.token, "safe"]:
            t = tf_i.loc[r.token]
            if not pd.isna(t["activity_x"]):
                txt += f" — desk {r.token} activity {_x(t['activity_x'])} its 20-day average"
                if t["lean"] != "—":
                    txt += f", {t['buy_share']*100:.0f}% buy-side"
            lead = _leader(sk, r.token)
            if lead:
                txt += f"; {lead}"
        out.append({"kind": "move", "token": r.token, "text": txt + ".",
                    "score": abs(r.z) + 1})
    moved = {h["token"] for h in out}
    for r in tf[tf["safe"]].itertuples():
        if r.token in moved or pd.isna(r.activity_x):
            continue
        if r.activity_x >= BUSY or (r.lean in ("buyers", "sellers") and r.n_peers >= 2 * peers.K_MIN):
            txt = f"{r.token}: activity {_x(r.activity_x)} average"
            if r.lean != "—":
                txt += f", {r.buy_share*100:.0f}% buy-side"
            lead = _leader(sk, r.token)
            if lead:
                txt += f" — {lead}"
            out.append({"kind": "flow", "token": r.token, "text": txt + ".",
                        "score": np.log(max(r.activity_x, 1)) * 2 + abs(r.buy_share - 0.5) * 4})
    safe_om = om[om["safe"]]
    for r in safe_om.itertuples():
        if not pd.isna(r.activity_x) and r.activity_x >= BUSY and r.n_today >= 3:
            out.append({"kind": "options", "token": r.top_token,
                        "text": f"Options: {r.strategy.lower()}s ({r.bias}) {_x(r.activity_x)} "
                                f"their usual pace, mostly {r.top_token}.",
                        "score": np.log(r.activity_x) * 1.5 + 0.5})
    lv, sv = vol_tilt(safe_om)
    if not pd.isna(lv) and abs(lv - sv) >= 0.25:
        side = "long" if lv > sv else "short"
        out.append({"kind": "options", "token": None,
                    "text": f"Vol: {max(lv, sv)*100:.0f}% of structures traded were {side} vol.",
                    "score": abs(lv - sv) * 2})
    for r in desk["mix"].itertuples():
        if abs(r.shift_pp) >= 5 and r.product != "Spot":
            out.append({"kind": "mix", "token": None,
                        "text": f"{r.product} {'up' if r.shift_pp > 0 else 'down'} to "
                                f"{r.today*100:.0f}% of desk flow (vs {r.base*100:.0f}% average).",
                        "score": abs(r.shift_pp) / 10})
    return sorted(out, key=lambda h: -h["score"])


def _leader(sk: pd.DataFrame, token: str) -> str:
    """'hedge funds led the buying' — the biggest safe segment with a lean."""
    g = sk[(sk["token"] == token) & sk["safe"] & sk["lean"].isin(["buyers", "sellers"])]
    if g.empty:
        return ""
    r = g.sort_values("share_of_flow", ascending=False).iloc[0]
    if r["share_of_flow"] < 0.25:
        return ""
    verb = "buying" if r["lean"] == "buyers" else "selling"
    return f"{r['label']} led the {verb} ({r['buy_share']*100:.0f}% buy)"


def contrasts(sk: pd.DataFrame) -> list[str]:
    """Segments on opposite sides of the same token — the best line in the note."""
    out = []
    s = sk[sk["safe"] & sk["lean"].isin(["buyers", "sellers"]) & (sk["share_of_flow"] >= 0.1)]
    for tok, g in s.groupby("token"):
        b, se = g[g["lean"] == "buyers"], g[g["lean"] == "sellers"]
        if not b.empty and not se.empty:
            bl = b.sort_values("share_of_flow").iloc[-1]
            sl = se.sort_values("share_of_flow").iloc[-1]
            out.append(f"{tok}: {bl['label']} buying ({bl['buy_share']*100:.0f}% buy) while "
                       f"{sl['label']} sold ({sl['buy_share']*100:.0f}% buy).")
    return out


# ── The note ──────────────────────────────────────────────────────────────────
DISCLAIMER = ("Aggregated, anonymized FalconX flow; percentages and multiples only. "
              "For information — not investment advice.")


def _activity_word(x: float) -> str:
    if pd.isna(x):
        return "normal"
    return "busy" if x >= BUSY - 0.3 else "quiet" if x <= QUIET + 0.2 else "about average"


def note(v: dict, max_lines: int = 6, focus: list[str] | None = None,
         title: str = "FalconX Daily Color") -> str:
    """Plain-text, client-shareable. `focus` floats those tokens to the top."""
    asof = v["asof"]
    hl = v["headlines"]
    if focus:
        hl = sorted(hl, key=lambda h: (h["token"] not in focus, -h["score"]))
    lines = [f"{title} · {asof:%a %d %b %Y} · 4:30pm ET", ""]
    mv = v["moves"].head(5)
    lines.append("Markets: " + " · ".join(f"{r.token} {_pct(r.ret)}" for r in mv.itertuples()))
    d = v["desk"]
    lines.append(f"Desk: {_activity_word(d['activity_x'])} day — activity "
                 f"{_x(d['activity_x'])} the 20-day average.")
    lines.append("")
    if hl:
        lines.append("What we saw:")
        lines += [f"• {h['text']}" for h in hl[:max_lines]]
    else:
        lines.append("What we saw: a quiet, two-way session — no standout flow.")
    cx = v["contrasts"][:2]
    if cx:
        lines += ["", "Split views:"] + [f"• {c}" for c in cx]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def for_client(flow: pd.DataFrame, prices: pd.DataFrame, asof: date, peer_id: str,
               client_tokens: list[str], name: str = "") -> str:
    """The note re-cut for one client: their own flow EXCLUDED from every
    aggregate (so we never echo their trades back as 'the market'), their
    tokens first."""
    v = build(flow, prices, asof, exclude_peer=peer_id)
    return note(v, focus=client_tokens,
                title=f"FalconX Daily Color{' for ' + name if name else ''}")


def rep_sheet(v: dict, profiles: dict, client_tokens: dict) -> pd.DataFrame:
    """INTERNAL: for each client, the headline that matches what they trade.
    Columns: coverage, parent, tokens, hook, n_matches."""
    rows = []
    for parent, prof in profiles.items():
        toks = client_tokens.get(parent, [])
        hits = [h for h in v["headlines"] if h["token"] in toks]
        gen = [h for h in v["headlines"] if h["token"] is None]
        hook = (hits or gen or [{"text": "Quiet day in their names — send the general note."}])[0]
        rows.append({"coverage": prof.get("coverage", ""), "parent": parent,
                     "tokens": ", ".join(toks[:5]), "hook": hook["text"],
                     "n_matches": len(hits)})
    return pd.DataFrame(rows).sort_values(["coverage", "n_matches"],
                                          ascending=[True, False]).reset_index(drop=True)


def client_tokens(trades: pd.DataFrame, top: int = 5) -> list[str]:
    """A client's most-traded tokens (by notional), stables excluded."""
    if trades.empty:
        return []
    t = trades[~trades["token"].isin(STABLES)]
    return t.groupby("token")["notional_usd"].sum().sort_values(ascending=False).head(top).index.tolist()


# ── LLM polish (optional) ─────────────────────────────────────────────────────
COLOR_SYSTEM = (
    "You write FalconX's 4:30pm ET daily color for institutional crypto clients. "
    "Rewrite the facts you are given into a tight, readable note (≤ 150 words): "
    "a one-line takeaway, then 3-5 bullets, then the disclaimer verbatim. "
    "Rules: use ONLY the numbers given — never invent, round up, or extrapolate; "
    "never name or hint at a client; no dollar amounts of desk volume; no "
    "recommendations or price predictions; plain text, no markdown headers.")


def color_context(v: dict) -> str:
    """Everything the model may use — already aggregated and k-safe."""
    parts = [f"Date: {v['asof']:%A %d %B %Y}, cut-off 4:30pm ET.",
             "Price moves (1d, σ vs 30d vol): " + "; ".join(
                 f"{r.token} {_pct(r.ret)} ({r.z:+.1f}σ)" for r in v["moves"].head(8).itertuples()),
             f"Desk activity: {_x(v['desk']['activity_x'])} the 20-day average.",
             "Facts (ranked):"] + [f"- {h['text']}" for h in v["headlines"][:8]]
    if v["contrasts"]:
        parts += ["Split views:"] + [f"- {c}" for c in v["contrasts"]]
    parts.append(f"Disclaimer: {DISCLAIMER}")
    return "\n".join(parts)


# ── Assemble ──────────────────────────────────────────────────────────────────
def build(flow: pd.DataFrame, prices: pd.DataFrame, asof: date,
          exclude_peer: str | None = None) -> dict:
    """Everything the tab and the note need. `flow` must cover history_window(asof)."""
    mv = moves(prices, asof)
    tf = token_flow(flow, asof, exclude_peer)
    sk = segment_skew(flow, asof, exclude_peer=exclude_peer)
    om = options_mix(flow if not exclude_peer else flow[flow["peer_id"] != exclude_peer], asof)
    desk = desk_activity(flow if not exclude_peer else flow[flow["peer_id"] != exclude_peer], asof)
    v = {"asof": asof, "window": window(asof), "moves": mv, "token_flow": tf,
         "segment_skew": sk, "options": om, "desk": desk, "contrasts": contrasts(sk)}
    v["headlines"] = headlines(mv, tf, sk, om, desk)
    return v


def run(data, asof: date) -> str:
    """Entry point for the scheduled job: pull via the seams, return the note."""
    s, e = history_window(asof)
    flow = data.get_desk_flow(s.isoformat(), e.isoformat())
    return note(build(flow, data.get_price_history(), asof))


if __name__ == "__main__":
    # Databricks Job: schedule "30 16 * * *" in America/New_York, then post the
    # output to the sales Slack channel for review before it goes to clients.
    import argparse
    import data as _data
    ap = argparse.ArgumentParser(description="Print the 4:30pm daily color.")
    ap.add_argument("--date", default=_data.TODAY.isoformat())
    print(run(_data, date.fromisoformat(ap.parse_args().date)))
