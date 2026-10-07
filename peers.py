"""
Peer Intelligence — "what are other hedge funds like this one doing?"

Compares one client against an anonymous cohort of similar clients (same
sub-segment → segment → all institutional) on product adoption, token mix,
options strategies, flow direction, and recent trends. Output is phrased so a
salesperson can say it out loud: "8 of 9 macro funds we cover trade options".

PRIVACY (the reason this file exists rather than a few groupbys):
  • Peers are only ever anonymous peer_ids — no names in, no names out.
  • k-anonymity: any cell backed by fewer than K_MIN distinct peers is
    suppressed. A cohort smaller than K_MIN falls back to the next level up.
  • Dominance rule: an aggregate is suppressed if one peer contributes more
    than DOMINANCE of it (otherwise "macro funds bought $400M SOL" can be one
    fund's trade, and everyone knows who).
These are re-applied here even though the Databricks view should already
enforce them — belt and braces.

Pure pandas. No Streamlit, no Databricks.
"""
from __future__ import annotations
from datetime import timedelta

import pandas as pd

K_MIN = 5
DOMINANCE = 0.60
LEVELS = ["Sub-segment", "Segment", "All institutional"]
PRODUCTS = ["Spot", "Derivatives", "Options", "Credit", "FX"]


# ── Cohort ────────────────────────────────────────────────────────────────────
def cohort(peer_act: pd.DataFrame, profile: dict, level: str) -> tuple[pd.DataFrame, str, str]:
    """Filter peer activity to the requested cohort, widening if it is too small
    for k-anonymity. Returns (rows, label, level_used)."""
    order = LEVELS[LEVELS.index(level):]
    for lv in order:
        if lv == "Sub-segment":
            df = peer_act[peer_act["sub_segment"] == profile["sub_segment"]]
            label = _sub_label(profile["sub_segment"], profile["segment"])
        elif lv == "Segment":
            df = peer_act[peer_act["segment"] == profile["segment"]]
            label = _plural(profile["segment"])
        else:
            df, label = peer_act, "institutional clients"
        if df["peer_id"].nunique() >= K_MIN:
            return df, label, lv
    return peer_act.iloc[0:0], "insufficient peers", order[-1]


_SUB_LABEL = {"Digital Asset Manager": "digital asset managers",
              "Traditional AM": "traditional asset managers",
              "Crypto-Native": "crypto-native market makers",
              "TradFi Prop": "TradFi prop market makers",
              "Crypto Discretionary": "crypto discretionary funds",
              "Multi-Strategy": "multi-strategy funds", "Macro": "macro funds",
              "Quant": "quant funds", "HFT": "HFT firms", "Prop": "prop firms",
              "Treasury": "corporate treasuries", "Miner": "miners"}


def _sub_label(sub: str, seg: str) -> str:
    return _SUB_LABEL.get(sub, f"{sub} {_plural(seg)}")


def _change(c: float) -> str:
    """+0.45 → 'up ~45%'; +2.4 → 'up ~3.4×' (big % on a small base reads as hype)."""
    if c >= 1:
        return f"up ~{1 + c:.1f}×"
    return f"up ~{c * 100:.0f}%" if c >= 0 else f"down ~{-c * 100:.0f}%"


def _plural(seg: str) -> str:
    return {"Hedge Fund": "hedge funds", "Prop Trading": "prop firms",
            "Market Maker": "market makers", "Asset Manager": "asset managers",
            "Corporate": "corporates"}.get(seg, seg.lower() + "s")


def n_peers(df: pd.DataFrame) -> int:
    return int(df["peer_id"].nunique()) if not df.empty else 0


def safe(per_peer: pd.Series) -> bool:
    """Passes k-anonymity AND dominance for a per-peer series of values."""
    per_peer = per_peer[per_peer > 0]
    if per_peer.size < K_MIN:
        return False
    tot = per_peer.sum()
    return bool(tot > 0 and per_peer.max() / tot <= DOMINANCE)


# ── Client → same weekly shape as the peer feed ───────────────────────────────
def client_weekly(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["week", "product", "token", "notional_usd",
                                     "buy_usd", "sell_usd", "trades", "revenue_usd"])
    t = trades.copy()
    tt = pd.to_datetime(t["trade_time"])
    t["week"] = (tt - pd.to_timedelta(tt.dt.weekday, unit="D")).dt.date
    t["buy"] = t["notional_usd"].where(t["side"] == "Buy", 0.0)
    t["sell"] = t["notional_usd"].where(t["side"] == "Sell", 0.0)
    return t.groupby(["week", "product", "token"]).agg(
        notional_usd=("notional_usd", "sum"), buy_usd=("buy", "sum"),
        sell_usd=("sell", "sum"), trades=("product", "size"),
        revenue_usd=("revenue_usd", "sum")).reset_index()


def _in(df: pd.DataFrame, start, end) -> pd.DataFrame:
    if df.empty:
        return df
    w = pd.to_datetime(df["week"]).dt.date
    return df[(w >= pd.Timestamp(start).date()) & (w <= pd.Timestamp(end).date())]


# ── 1. Product adoption ───────────────────────────────────────────────────────
def product_adoption(coh: pd.DataFrame, client: pd.DataFrame) -> pd.DataFrame:
    """Share of cohort peers active in each product vs whether the client is."""
    n = n_peers(coh)
    rows = []
    for p in PRODUCTS:
        active = coh[coh["product"] == p]["peer_id"].nunique()
        rows.append({"product": p, "peers_active": int(active), "n_peers": n,
                     "pct": active / n if n else 0.0,
                     "client_active": bool((client["product"] == p).any()) if not client.empty else False,
                     "suppressed": False})
    return pd.DataFrame(rows)


# ── 2. Token mix ──────────────────────────────────────────────────────────────
def token_mix(coh: pd.DataFrame, client: pd.DataFrame, top: int = 8) -> pd.DataFrame:
    """Median per-peer share of notional by token (medians resist one big fund)
    vs the client's own share. Tokens traded by < K_MIN peers are suppressed."""
    if coh.empty:
        return pd.DataFrame()
    per = coh.groupby(["peer_id", "token"])["notional_usd"].sum().reset_index()
    per["share"] = per["notional_usd"] / per.groupby("peer_id")["notional_usd"].transform("sum")
    n = n_peers(coh)
    cl_tot = client["notional_usd"].sum() if not client.empty else 0
    cl_share = (client.groupby("token")["notional_usd"].sum() / cl_tot) if cl_tot else pd.Series(dtype=float)
    rows = []
    for tok, g in per.groupby("token"):
        if g["peer_id"].nunique() < K_MIN:
            continue
        # median over ALL cohort peers (non-traders count as 0 share)
        shares = list(g["share"]) + [0.0] * (n - len(g))
        rows.append({"token": tok, "peers_trading": int(g["peer_id"].nunique()), "n_peers": n,
                     "adoption_pct": g["peer_id"].nunique() / n,
                     "peer_median_share": float(pd.Series(shares).median()),
                     "peer_mean_share": float(pd.Series(shares).mean()),
                     "client_share": float(cl_share.get(tok, 0.0))})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("peer_mean_share", ascending=False).head(top).reset_index(drop=True)


# ── 3. Trends (what's changing in the cohort right now) ───────────────────────
def trends(coh_full: pd.DataFrame, end, recent_weeks: int = 4, base_weeks: int = 12,
           peer_strat: pd.DataFrame | None = None) -> pd.DataFrame:
    """Avg weekly notional (or structure count) in the last `recent_weeks` vs the
    `base_weeks` before that, by token, product, and options strategy.
    Requires coh_full to cover at least recent+base weeks before `end`."""
    end = pd.Timestamp(end).date()
    r0 = end - timedelta(weeks=recent_weeks)
    b0 = r0 - timedelta(weeks=base_weeks)
    out = []

    def _one(df, dim, val_col, how):
        if df.empty:
            return
        w = pd.to_datetime(df["week"]).dt.date
        rec = df[(w > r0) & (w <= end)]
        base = df[(w > b0) & (w <= r0)]
        for item in sorted(set(rec[dim]) | set(base[dim])):
            rr, bb = rec[rec[dim] == item], base[base[dim] == item]
            if not safe(rr.groupby("peer_id")[val_col].sum()):
                continue
            rv = rr[val_col].sum() / recent_weeks
            bv = bb[val_col].sum() / base_weeks
            out.append({"dimension": how, "item": item,
                        "recent_weekly": rv, "base_weekly": bv,
                        "change_pct": (rv / bv - 1) if bv else None,
                        "peers_recent": int(rr["peer_id"].nunique()),
                        "peers_base": int(bb["peer_id"].nunique()),
                        "new_peers": int(len(set(rr["peer_id"]) - set(bb["peer_id"])))})

    _one(coh_full, "token", "notional_usd", "Token")
    _one(coh_full, "product", "notional_usd", "Product")
    if peer_strat is not None and not peer_strat.empty:
        _one(peer_strat, "strategy", "structures", "Options strategy")
    df = pd.DataFrame(out)
    if df.empty:
        return df
    return df.sort_values("change_pct", ascending=False, na_position="last").reset_index(drop=True)


# ── 4. Direction (are peers buyers or sellers?) ───────────────────────────────
def direction(coh: pd.DataFrame, client: pd.DataFrame) -> pd.DataFrame:
    """Per token: share of cohort peers that are net buyers, aggregate buy share,
    and the client's own net direction."""
    if coh.empty:
        return pd.DataFrame()
    pp = coh.groupby(["token", "peer_id"])[["buy_usd", "sell_usd"]].sum().reset_index()
    cl = client.groupby("token")[["buy_usd", "sell_usd"]].sum() if not client.empty else None
    rows = []
    for tok, g in pp.groupby("token"):
        gross = g["buy_usd"] + g["sell_usd"]
        if not safe(gross.set_axis(g["peer_id"])):
            continue
        buyers = int((g["buy_usd"] > g["sell_usd"]).sum())
        c_dir = None
        if cl is not None and tok in cl.index:
            b, s = cl.loc[tok, "buy_usd"], cl.loc[tok, "sell_usd"]
            c_dir = "Buyer" if b > s else "Seller"
        rows.append({"token": tok, "peers": int(len(g)), "net_buyers": buyers,
                     "pct_buyers": buyers / len(g),
                     "buy_share": g["buy_usd"].sum() / gross.sum() if gross.sum() else 0.5,
                     "client_dir": c_dir})
    return pd.DataFrame(rows).sort_values("peers", ascending=False).reset_index(drop=True)


# ── 5. Options strategies ─────────────────────────────────────────────────────
def strategy_popularity(peer_strat: pd.DataFrame, coh: pd.DataFrame,
                        client_structures: list[dict]) -> pd.DataFrame:
    """Of cohort peers active in options, % that used each strategy, vs the
    client's own count."""
    if peer_strat is None or peer_strat.empty or coh.empty:
        return pd.DataFrame()
    ids = set(coh["peer_id"])
    ps = peer_strat[peer_strat["peer_id"].isin(ids)]
    n_opt = ps["peer_id"].nunique()
    if n_opt < K_MIN:
        return pd.DataFrame()
    from collections import Counter
    mine = Counter(s["classification"]["name"] for s in client_structures)
    rows = []
    for name, g in ps.groupby("strategy"):
        users = g["peer_id"].nunique()
        if users < K_MIN:
            continue
        rows.append({"strategy": name, "bias": g["bias"].iloc[0], "peers_using": int(users),
                     "n_options_peers": int(n_opt), "pct": users / n_opt,
                     "structures": int(g["structures"].sum()),
                     "client_count": int(mine.get(name, 0))})
    df = pd.DataFrame(rows)
    return df.sort_values("pct", ascending=False).reset_index(drop=True) if not df.empty else df


# ── 6. Benchmarks / percentile ────────────────────────────────────────────────
def benchmarks(coh: pd.DataFrame, client: pd.DataFrame) -> pd.DataFrame:
    """Where the client ranks in the cohort on size, revenue, margin, breadth."""
    if coh.empty:
        return pd.DataFrame()
    g = coh.groupby("peer_id").agg(notional=("notional_usd", "sum"),
                                    revenue=("revenue_usd", "sum"),
                                    products=("product", "nunique"),
                                    tokens=("token", "nunique"))
    g["bps"] = g["revenue"] / g["notional"].where(g["notional"] > 0) * 1e4
    c = {"notional": client["notional_usd"].sum(), "revenue": client["revenue_usd"].sum(),
         "products": client["product"].nunique(), "tokens": client["token"].nunique()} \
        if not client.empty else {"notional": 0, "revenue": 0, "products": 0, "tokens": 0}
    c["bps"] = c["revenue"] / c["notional"] * 1e4 if c["notional"] else 0
    labels = {"notional": "Notional traded", "revenue": "Revenue to FX",
              "bps": "Blended bps", "products": "Products used", "tokens": "Tokens traded"}
    rows = []
    for k, lab in labels.items():
        s = g[k].dropna()
        rows.append({"metric": lab, "key": k, "client": float(c[k]),
                     "p25": float(s.quantile(.25)), "median": float(s.median()),
                     "p75": float(s.quantile(.75)),
                     "percentile": float((s < c[k]).mean() + 0.5 * (s == c[k]).mean())})
    return pd.DataFrame(rows)


# ── 7. Desk ideas: how the cohort reacted ─────────────────────────────────────
def idea_uptake(uptake: pd.DataFrame, profile: dict, level_used: str,
                client_ideas: pd.DataFrame) -> pd.DataFrame:
    """For desk-wide ideas: cohort take-rate vs what this client did."""
    if uptake is None or uptake.empty:
        return pd.DataFrame()
    if level_used == "Sub-segment":
        u = uptake[uptake["sub_segment"] == profile["sub_segment"]]
    elif level_used == "Segment":
        u = uptake[uptake["segment"] == profile["segment"]]
    else:
        u = uptake
    g = u.groupby(["desk_idea_id", "headline"]).agg(
        pitched=("pitched", "sum"), executed=("executed", "sum")).reset_index()
    g = g[g["pitched"] >= K_MIN]
    g["take_rate"] = g["executed"] / g["pitched"]
    mine = {}
    if client_ideas is not None and not client_ideas.empty:
        for _, r in client_ideas.dropna(subset=["desk_idea_id"]).iterrows():
            mine[r["desk_idea_id"]] = "Took it" if r.get("executed") else r.get("reaction", "—")
    g["client"] = g["desk_idea_id"].map(lambda x: mine.get(x, "Not pitched"))
    return g.sort_values("take_rate", ascending=False).reset_index(drop=True)


# ── 8. Talking points ─────────────────────────────────────────────────────────
def talking_points(label: str, n: int, adoption: pd.DataFrame, mix: pd.DataFrame,
                   trend: pd.DataFrame, dirn: pd.DataFrame, strat: pd.DataFrame,
                   uptake: pd.DataFrame, parent: str) -> list[dict]:
    """Say-it-out-loud lines, each with the gap/opportunity it points at.
    Only ever quotes counts and shares — never a peer's dollar amount."""
    pts = []
    if not adoption.empty:
        for _, r in adoption.sort_values("pct", ascending=False).iterrows():
            if not r["client_active"] and r["pct"] >= 0.5 and r["peers_active"] >= K_MIN:
                pts.append({"kind": "Product gap",
                            "say": f"{r['peers_active']} of the {n} {label} we cover trade "
                                   f"{r['product'].lower()} with us.",
                            "why": f"{parent} doesn't — whitespace."})
    if trend is not None and not trend.empty:
        hot = trend[(trend["change_pct"].fillna(0) > 0.4) & (trend["peers_recent"] >= K_MIN)]
        for _, r in hot.head(3).iterrows():
            if r["dimension"] == "Options strategy":
                what = f"{r['item'].lower()} usage"
            elif r["dimension"] == "Product":
                what = f"{r['item'].lower()} volume"
            else:
                what = f"{r['item']} volume"
            extra = f", {r['new_peers']} of them new to it" if r["new_peers"] >= 2 else ""
            pts.append({"kind": "Trending",
                        "say": f"{label[0].upper() + label[1:]}: {what} is {_change(r['change_pct'])} "
                               f"over the last 4 weeks vs the prior 12 "
                               f"({r['peers_recent']} active{extra}).",
                        "why": f"{r['dimension']} trend in cohort."})
    if dirn is not None and not dirn.empty:
        for _, r in dirn.iterrows():
            if r["client_dir"] and r["peers"] >= K_MIN:
                peer_side = "Buyer" if r["pct_buyers"] >= 0.65 else "Seller" if r["pct_buyers"] <= 0.35 else None
                if peer_side and peer_side != r["client_dir"]:
                    k = r["net_buyers"] if peer_side == "Buyer" else r["peers"] - r["net_buyers"]
                    pts.append({"kind": "Contrarian",
                                "say": f"{k} of {r['peers']} {label} active in {r['token']} "
                                       f"have been net {peer_side.lower()}s — you've been the other way.",
                                "why": "Client positioned against cohort — ask why / pitch hedge."})
    if strat is not None and not strat.empty:
        for _, r in strat.iterrows():
            if r["client_count"] == 0 and r["pct"] >= 0.4:
                pts.append({"kind": "Strategy gap",
                            "say": f"{r['pct']*100:.0f}% of {label} trading options with us "
                                   f"have used {r['strategy'].lower()}s this period.",
                            "why": f"{parent} hasn't — pitch a {r['strategy'].lower()}."})
                break
    if uptake is not None and not uptake.empty:
        for _, r in uptake.iterrows():
            if r["take_rate"] >= 0.4 and r["client"] not in ("Took it",):
                pts.append({"kind": "Peers took it",
                            "say": f"{int(r['executed'])} of {int(r['pitched'])} {label} we showed "
                                   f"'{r['headline']}' put it on.",
                            "why": f"{parent}: {r['client']}."})
                break
    return pts


# ── One call for the tab ──────────────────────────────────────────────────────
def build(parent: str, profile: dict, client_trades: pd.DataFrame, structures: list[dict],
          client_ideas: pd.DataFrame | None, start, end, level: str,
          peer_act: pd.DataFrame, peer_strat: pd.DataFrame,
          uptake: pd.DataFrame | None) -> dict:
    """Everything the Peers tab shows. peer_act / peer_strat must reach back at
    least 16 weeks before `end` (trends compare last 4w vs the 12w before)."""
    coh_full, label, used = cohort(peer_act, profile, level)
    ids = set(coh_full["peer_id"])
    strat_full = peer_strat[peer_strat["peer_id"].isin(ids)] if peer_strat is not None \
        and not peer_strat.empty else pd.DataFrame()
    coh = _in(coh_full, start, end)
    strat = _in(strat_full, start, end) if not strat_full.empty else strat_full
    client = client_weekly(client_trades)
    n = n_peers(coh)
    out = {"label": label, "level_used": used, "level_asked": level, "n": n,
           "widened": used != level}
    if n < K_MIN:
        out["empty"] = True
        return out
    out["adoption"] = product_adoption(coh, client)
    out["mix"] = token_mix(coh, client)
    out["trends"] = trends(coh_full, end, peer_strat=strat_full)
    out["direction"] = direction(coh, client)
    out["strategies"] = strategy_popularity(strat, coh, structures)
    out["benchmarks"] = benchmarks(coh, client)
    out["uptake"] = idea_uptake(uptake, profile, used, client_ideas)
    out["points"] = talking_points(label, n, out["adoption"], out["mix"], out["trends"],
                                   out["direction"], out["strategies"], out["uptake"], parent)
    out["empty"] = False
    return out
