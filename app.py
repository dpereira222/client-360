"""
FalconX Client 360 — cross-product client intelligence for the IC / sales desk.

Prototype: presentation only. All data comes through data.py (mock today,
Databricks after engineering wires the seam). Charts follow the FalconX data-viz
system in theme.py.
"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import theme

st.set_page_config(page_title="FalconX Client 360", page_icon="🦅", layout="wide")
theme.install_template()
st.markdown(theme.PAGE_CSS, unsafe_allow_html=True)


def usd(x: float, prec: int = 1) -> str:
    x = float(x)
    a = abs(x)
    if a >= 1e9:
        return f"${x/1e9:.{prec}f}B"
    if a >= 1e6:
        return f"${x/1e6:.{prec}f}M"
    if a >= 1e3:
        return f"${x/1e3:.{prec}f}K"
    return f"${x:,.0f}"


def fmt(df: pd.DataFrame, formatters: dict) -> pd.DataFrame:
    """Return a copy with columns formatted to display strings (no Styler/jinja2)."""
    out = df.copy()
    for col, fn in formatters.items():
        if col in out.columns:
            out[col] = out[col].apply(lambda v: "" if pd.isna(v) else fn(v))
    return out


def build_brief(parent, kpi, trades, positions, credit, gong, days):  # days = window label
    """Pure-logic talking points for a meeting. No LLM required."""
    sections = []

    who = [
        f"**{usd(kpi['notional'])}** traded across "
        f"**{len(kpi['products_active'])}** product lines "
        f"({', '.join(kpi['products_active'])}) over {days}.",
        f"**{usd(kpi['revenue'], 0)}** revenue to FalconX at a blended "
        f"**{kpi['blended_bps']:.1f} bps** over {kpi['trade_count']:,} trades.",
    ]
    if kpi["credit_outstanding"]:
        who.append(f"Carrying **{usd(kpi['credit_outstanding'])}** drawn credit.")
    sections.append(("Relationship at a glance", who))

    if not trades.empty:
        act = data.product_activity(trades)
        top = act.iloc[0]
        conc = top["revenue_usd"] / act["revenue_usd"].sum() if act["revenue_usd"].sum() else 0
        rev = [f"Revenue is led by **{top['product']}** "
               f"({usd(top['revenue_usd'],0)}, {conc*100:.0f}% of the total)."]
        missing = [p for p in theme.PRODUCT_ORDER if p not in kpi["products_active"]]
        if missing:
            rev.append(f"No activity yet in **{', '.join(missing)}** — cross-sell whitespace.")
        sections.append(("Where the money is", rev))

    if not positions.empty:
        net = positions.apply(
            lambda r: r["notional_usd"] if r["direction"] == "Long" else -r["notional_usd"],
            axis=1).sum()
        bias = "net long" if net > 0 else "net short"
        pos = [f"Currently **{bias}** {usd(abs(net))} across open positions; "
               f"unrealized P&L **{usd(positions['unrealized_pnl'].sum(),0)}**."]
        big = positions.reindex(positions["notional_usd"].abs().sort_values(ascending=False).index).iloc[0]
        pos.append(f"Largest position: **{big['instrument']}** "
                   f"({big['direction']}, {usd(big['notional_usd'])}).")
        sections.append(("Positioning", pos))

    if not credit.empty:
        risky = credit[credit["status"].isin(["serious", "critical"])]
        soon = credit[credit["days_to_maturity"] < 45]
        cr = []
        if not risky.empty:
            r = risky.iloc[0]
            cr.append(f"⚠️ **{r['deal_id']}** at **{r['ltv_pct']}% LTV** — margin health "
                      f"is *{r['status']}*.")
        if not soon.empty:
            r = soon.iloc[0]
            cr.append(f"**{r['deal_id']}** matures {r['maturity']} "
                      f"({r['days_to_maturity']}d) — renewal conversation due.")
        if not cr:
            cr.append("Credit book healthy — no near-term maturities or margin stress.")
        sections.append(("Credit watch", cr))

    if not gong.empty:
        last = gong.iloc[0]
        sections.append(("Last conversation", [
            f"**{last['title']}** on {last['call_date']}.",
            f"Next steps: {last['next_steps']}"]))
    return sections


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("🦅 Client 360")
st.sidebar.caption("Cross-product client intelligence")
st.sidebar.markdown("---")

import datetime as _dt

mode = st.sidebar.radio("Date range", ["Quick lookback", "Custom range"], horizontal=True)
if mode == "Quick lookback":
    unit = st.sidebar.selectbox("Lookback unit", ["Days", "Weeks", "Months", "Years"], index=2)
    amount = st.sidebar.number_input(
        "Amount", min_value=1,
        value={"Days": 30, "Weeks": 12, "Months": 6, "Years": 1}[unit], step=1)
    start, end = data.window(int(amount), unit)
    window_label = f"last {amount} {unit.lower()}"
else:
    _today = data.TODAY
    dr = st.sidebar.date_input(
        "Pick start & end",
        value=(_today - _dt.timedelta(days=180), _today),
        min_value=_today - _dt.timedelta(days=365 * 3), max_value=_today)
    if isinstance(dr, (tuple, list)) and len(dr) == 2:
        start, end = dr[0].isoformat(), dr[1].isoformat()
    else:
        start, end = (_today - _dt.timedelta(days=180)).isoformat(), _today.isoformat()
    window_label = f"{start} → {end}"

st.sidebar.caption(f"{start} → {end}")
st.sidebar.markdown("---")

if data.MOCK_MODE:
    st.sidebar.warning("**DEMO DATA** — running on the mock layer. "
                       "Flip `CLIENT360_MOCK=0` once Databricks is wired.")

# ── Search ────────────────────────────────────────────────────────────────────
st.title("Client 360")
parents = data.search_parents()
parent = st.selectbox("Client relationship", parents,
                      index=parents.index("Brevan Howard") if "Brevan Howard" in parents else 0)

# ── Load ──────────────────────────────────────────────────────────────────────
ents = data.get_entities(parent)
trades_all = data.get_trades(parent, start, end)
positions = data.get_positions(parent)
credit = data.get_credit(parent)
gong = data.get_gong(parent, start, end)

# Entity filter
ent_names = ents["entity_name"].tolist()
picked = st.multiselect("Focus entities (default: all — pick one to drill in)",
                        ent_names, default=ent_names)
picked_ids = ents[ents["entity_name"].isin(picked)]["entity_id"].tolist()
trades = trades_all[trades_all["entity_id"].isin(picked_ids)] if not trades_all.empty else trades_all

kpi = data.overview_kpis(trades, positions, credit)
tokens = trades["token"].value_counts().index.tolist()[:5] if not trades.empty else []
structures = data.option_structures(trades, positions)

# ── Header block ──────────────────────────────────────────────────────────────
prof = None
try:
    prof = __import__("mock").resolve(parent)  # cosmetic metadata only
except Exception:
    pass

hcol, mcol = st.columns([3, 2])
with hcol:
    st.markdown(f"## {parent}")
    tier = prof["tier"] if prof else "—"
    since = prof["since"] if prof else "—"
    cover = prof["coverage"] if prof else "—"
    st.markdown(
        f"{theme.pill(tier, 'good')} &nbsp; "
        f"<span style='color:#52514e'>Client since <b>{since}</b> · "
        f"Coverage <b>{cover}</b> · <b>{len(ents)}</b> entities</span>",
        unsafe_allow_html=True)
    chips = "".join(
        f"<span class='fx-chip'>{r.entity_name} · {r.entity_type}</span>"
        for r in ents.itertuples())
    st.markdown(f"<div style='margin-top:8px'>{chips}</div>", unsafe_allow_html=True)
with mcol:
    covered = kpi["products_active"]
    cells = []
    for p in theme.PRODUCT_ORDER:
        on = p in covered
        color = theme.PRODUCT_COLORS[p]
        bg = color if on else "#ffffff"
        fg = "#ffffff" if on else "#c3c2b7"
        bd = color if on else "#e1e0d9"
        cells.append(
            f"<div class='fx-cov' style='background:{bg};color:{fg};border-color:{bd}'>{p}</div>")
    st.markdown("<div style='color:#898781;font-size:.78rem;text-transform:uppercase;"
                "letter-spacing:.04em;margin-bottom:6px'>Product coverage</div>",
                unsafe_allow_html=True)
    st.markdown(
        f"<div style='display:grid;grid-template-columns:repeat(5,1fr);gap:6px'>{''.join(cells)}</div>",
        unsafe_allow_html=True)

st.markdown("---")

# ── KPI bar ───────────────────────────────────────────────────────────────────
k = st.columns(6)
k[0].metric("Notional Traded", usd(kpi["notional"]))
k[1].metric("Revenue to FX", usd(kpi["revenue"], 0 if kpi["revenue"] < 1e6 else 2))
k[2].metric("Blended Bps", f"{kpi['blended_bps']:.1f}")
k[3].metric("Open Positions", usd(kpi["net_position"]))
k[4].metric("Unrealized P&L", usd(kpi["unrealized_pnl"]))
k[5].metric("Credit Drawn", usd(kpi["credit_outstanding"]) if kpi["credit_outstanding"] else "—")

st.markdown("")

tab_ov, tab_pos, tab_opt, tab_trd, tab_cr, tab_rel, tab_ai = st.tabs(
    ["Overview", "Positions & Risk", "Options", "Trades", "Credit",
     "Relationship", "Analyst ✨"])

# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with tab_ov:
    act = data.product_activity(trades)
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Revenue by product")
        if not act.empty:
            fig = go.Figure(go.Bar(
                x=act["revenue_usd"], y=act["product"], orientation="h",
                marker_color=[theme.PRODUCT_COLORS[p] for p in act["product"]],
                text=[usd(v, 0) for v in act["revenue_usd"]], textposition="outside",
                hovertemplate="%{y}: %{text}<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=280, yaxis=dict(autorange="reversed"),
                              xaxis=dict(showticklabels=False), showlegend=False)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No activity in window.")

    with c2:
        st.subheader("Notional over time")
        ts = data.volume_timeseries(trades, "W")
        if not ts.empty:
            fig = go.Figure()
            for p in theme.PRODUCT_ORDER:
                sub = ts[ts["product"] == p]
                if sub.empty:
                    continue
                fig.add_bar(name=p, x=sub["bucket"], y=sub["notional_usd"],
                            marker_color=theme.PRODUCT_COLORS[p])
            fig.update_layout(barmode="stack", height=280,
                              bargap=0.25, yaxis=dict(title=None))
            theme.bar_ends(fig, 2)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No activity in window.")

    st.markdown("")
    c3, c4 = st.columns([3, 2])
    with c3:
        st.subheader("Product activity")
        if not act.empty:
            disp = act.copy()
            disp.columns = ["Product", "Notional", "Revenue", "Trades", "Avg Bps"]
            disp = fmt(disp, {"Notional": lambda v: usd(v, 1),
                              "Revenue": lambda v: usd(v, 0),
                              "Avg Bps": lambda v: f"{v:.1f}"})
            st.dataframe(disp, width="stretch", hide_index=True)
    with c4:
        st.subheader("Token flow (net)")
        tf = data.token_flow(trades)
        if not tf.empty:
            tf = tf.head(6)
            fig = go.Figure(go.Bar(
                x=tf["net_usd"], y=tf["token"], orientation="h",
                marker_color=[theme.BUY if v >= 0 else theme.SELL for v in tf["net_usd"]],
                hovertemplate="%{y}: net %{x:$,.0f}<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=240, yaxis=dict(autorange="reversed"),
                              xaxis=dict(title="Net buy (+) / sell (−)"), showlegend=False)
            st.plotly_chart(fig, width="stretch")
            st.caption("Blue = net buyer · Red = net seller")

    # ── Client vs desk ────────────────────────────────────────────────────────
    st.markdown("")
    st.subheader("Client vs the desk")
    st.caption("Is this client positioned with or against the rest of the book?")
    tf_all = data.token_flow(trades)
    if not tf_all.empty:
        desk = data.get_desk_benchmark(tf_all["token"].tolist(), start, end)
        cmp = data.desk_comparison(tf_all, desk)
        if not cmp.empty:
            cc1, cc2 = st.columns([2, 3])
            with cc1:
                contr = cmp[cmp["signal"] == "Contrarian"]
                if not contr.empty:
                    for _, r in contr.iterrows():
                        st.markdown(
                            theme.pill("CONTRARIAN", "warning") +
                            f" **{r['token']}** — client {r['client_dir'].lower()}, "
                            f"desk {r['desk_dir'].lower()}", unsafe_allow_html=True)
                else:
                    st.markdown(theme.pill("ALIGNED", "good") +
                                " Client is aligned with the desk on all tokens.",
                                unsafe_allow_html=True)
            with cc2:
                fig = go.Figure()
                fig.add_bar(name="Client", x=cmp["token"], y=cmp["client_net_usd"],
                            marker_color=theme.SLOT["blue"])
                fig.add_bar(name="Desk", x=cmp["token"], y=cmp["desk_net_usd"],
                            marker_color=theme.SLOT["yellow"])
                theme.bar_ends(fig)
                fig.update_layout(barmode="group", height=240,
                                  yaxis=dict(title="Net USD"))
                st.plotly_chart(fig, width="stretch")
            disp = cmp[["token", "client_dir", "desk_dir", "num_clients", "signal"]].copy()
            disp.columns = ["Token", "Client", "Desk", "# Desk Clients", "Signal"]
            st.dataframe(disp, width="stretch", hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# POSITIONS & RISK
# ══════════════════════════════════════════════════════════════════════════════
with tab_pos:
    if positions.empty:
        st.info("No open positions.")
    else:
        long_n = positions[positions["direction"] == "Long"]["notional_usd"].sum()
        short_n = positions[positions["direction"] == "Short"]["notional_usd"].sum()
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Gross Exposure", usd(positions["notional_usd"].sum()))
        p2.metric("Long", usd(long_n))
        p3.metric("Short", usd(short_n))
        p4.metric("Unrealized P&L", usd(positions["unrealized_pnl"].sum()))

        st.markdown("")
        cc1, cc2 = st.columns(2)
        with cc1:
            st.subheader("Exposure by product")
            byprod = positions.groupby("product")["notional_usd"].sum().reset_index()
            fig = go.Figure(go.Bar(
                x=byprod["notional_usd"], y=byprod["product"], orientation="h",
                marker_color=[theme.PRODUCT_COLORS[p] for p in byprod["product"]],
                hovertemplate="%{y}: %{x:$,.0f}<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=240, yaxis=dict(autorange="reversed"), showlegend=False)
            st.plotly_chart(fig, width="stretch")
        with cc2:
            st.subheader("Net exposure by token")
            pt = positions.copy()
            pt["signed"] = pt.apply(
                lambda r: r["notional_usd"] if r["direction"] == "Long" else -r["notional_usd"],
                axis=1)
            bytok = pt.groupby("token")["signed"].sum().reset_index().sort_values("signed")
            fig = go.Figure(go.Bar(
                x=bytok["signed"], y=bytok["token"], orientation="h",
                marker_color=[theme.BUY if v >= 0 else theme.SELL for v in bytok["signed"]],
                hovertemplate="%{y}: %{x:$,.0f}<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=240, showlegend=False,
                              xaxis=dict(title="Long (+) / Short (−)"))
            st.plotly_chart(fig, width="stretch")

        st.subheader("Position detail")
        disp = positions[["product", "instrument", "token", "direction", "notional_usd",
                          "avg_price", "mark_price", "unrealized_pnl", "funding_8h_bps",
                          "delta", "vega_usd"]].copy()
        disp.columns = ["Product", "Instrument", "Token", "Dir", "Notional",
                        "Avg", "Mark", "uPnL", "Fund 8h bps", "Delta", "Vega $"]
        disp = fmt(disp, {"Notional": lambda v: usd(v, 1),
                          "uPnL": lambda v: usd(v, 0),
                          "Avg": lambda v: f"{v:,.2f}",
                          "Mark": lambda v: f"{v:,.2f}",
                          "Fund 8h bps": lambda v: f"{v:.1f}",
                          "Delta": lambda v: f"{v:.2f}",
                          "Vega $": lambda v: usd(v, 0)})
        st.dataframe(disp, width="stretch", hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# OPTIONS — structure detection
# ══════════════════════════════════════════════════════════════════════════════
_BIAS_PILL = {"bullish": "good", "bearish": "critical", "long vol": "serious",
              "short vol": "warning", "neutral": "good",
              "income / capped upside": "good"}

with tab_opt:
    if not structures:
        st.info("No options activity for this selection. "
                "(Tip: pick a single entity above to drill into just their book, "
                "or widen the date range.)")
    else:
        n_legs = sum(s["n_legs"] for s in structures)
        o1, o2, o3, o4 = st.columns(4)
        o1.metric("Structures", f"{len(structures)}")
        o2.metric("Total legs", f"{n_legs}")
        o3.metric("Options notional", usd(sum(s["notional_usd"] for s in structures)))
        from collections import Counter as _C
        kinds = _C(s["classification"]["name"] for s in structures)
        o4.metric("Most common", kinds.most_common(1)[0][0])

        st.markdown("")
        cL, cR = st.columns([3, 2])
        with cL:
            st.subheader("Strategy mix")
            mix = pd.DataFrame(kinds.most_common(), columns=["Strategy", "Count"])
            fig = go.Figure(go.Bar(
                x=mix["Count"], y=mix["Strategy"], orientation="h",
                marker_color=theme.SLOT["violet"],
                hovertemplate="%{y}: %{x}<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=max(240, 26 * len(mix)),
                              yaxis=dict(autorange="reversed"), showlegend=False,
                              xaxis=dict(title="Structures"))
            st.plotly_chart(fig, width="stretch")
        with cR:
            st.subheader("Directional bias")
            bias = _C(s["classification"]["bias"] for s in structures)
            bdf = pd.DataFrame(bias.most_common(), columns=["Bias", "Count"])
            st.dataframe(bdf, width="stretch", hide_index=True, height=max(200, 26*len(bdf)))

        st.subheader("Structures traded")
        st.caption("Legs grouped by parent order and classified. Newest first.")
        entity_opts = sorted({s["entity_name"] for s in structures})
        efil = st.multiselect("Entity", entity_opts, default=[])
        kfil = st.multiselect("Strategy", [k for k, _ in kinds.most_common()], default=[])
        shown = [s for s in structures
                 if (not efil or s["entity_name"] in efil)
                 and (not kfil or s["classification"]["name"] in kfil)]
        st.caption(f"{len(shown)} of {len(structures)} structures")

        for s in shown[:60]:
            c = s["classification"]
            head = (f"**{c['name']}** · {s['token']} · {s['entity_name']} · "
                    f"{usd(s['notional_usd'])} · {str(s['trade_time'])[:10]}")
            with st.expander(head):
                st.markdown(
                    theme.pill(c["bias"].upper(), _BIAS_PILL.get(c["bias"], "good")) +
                    f" &nbsp; net **{c['net']}** &nbsp; · &nbsp; {c['detail']}",
                    unsafe_allow_html=True)
                legdf = pd.DataFrame(s["legs_display"])
                legdf = legdf[["side", "right", "strike", "expiry", "qty",
                               "notional_usd", "instrument"]]
                legdf.columns = ["Side", "Type", "Strike", "Expiry", "Contracts",
                                 "Notional", "Instrument"]
                legdf = fmt(legdf, {"Notional": lambda v: usd(v, 0),
                                    "Strike": lambda v: f"{v:g}",
                                    "Contracts": lambda v: f"{v:g}"})
                st.dataframe(legdf, width="stretch", hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TRADES
# ══════════════════════════════════════════════════════════════════════════════
with tab_trd:
    if trades.empty:
        st.info("No trades in window.")
    else:
        f1, f2, f3 = st.columns(3)
        prod_f = f1.multiselect("Product", theme.PRODUCT_ORDER,
                                default=kpi["products_active"])
        side_f = f2.multiselect("Side", ["Buy", "Sell"], default=["Buy", "Sell"])
        tok_f = f3.multiselect("Token", sorted(trades["token"].unique()), default=[])

        blot = trades.copy()
        if prod_f:
            blot = blot[blot["product"].isin(prod_f)]
        if side_f:
            blot = blot[blot["side"].isin(side_f)]
        if tok_f:
            blot = blot[blot["token"].isin(tok_f)]

        st.caption(f"{len(blot):,} trades · {usd(blot['notional_usd'].sum())} notional · "
                   f"{usd(blot['revenue_usd'].sum(),0)} revenue")

        d1, d2 = st.columns(2)
        with d1:
            st.subheader("Trade size distribution")
            fig = go.Figure(go.Histogram(
                x=blot["notional_usd"], nbinsx=30, marker_color=theme.SLOT["blue"]))
            fig.update_layout(height=240, yaxis=dict(title="Trades"),
                              xaxis=dict(title="Notional ($)"), bargap=0.05)
            st.plotly_chart(fig, width="stretch")
        with d2:
            st.subheader("Avg bps by product")
            bp = blot.groupby("product")["bps"].mean().reset_index()
            fig = go.Figure(go.Bar(
                x=bp["product"], y=bp["bps"],
                marker_color=[theme.PRODUCT_COLORS[p] for p in bp["product"]],
                text=[f"{v:.1f}" for v in bp["bps"]], textposition="outside",
                hovertemplate="%{x}: %{y:.1f} bps<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=240, showlegend=False, yaxis=dict(title="bps"))
            st.plotly_chart(fig, width="stretch")

        st.subheader("Blotter")
        show = blot.head(300).copy()
        show["trade_time"] = pd.to_datetime(show["trade_time"]).dt.strftime("%Y-%m-%d %H:%M")
        show = show[["trade_time", "entity_name", "product", "instrument", "token",
                     "side", "notional_usd", "price", "bps", "revenue_usd", "venue"]]
        show.columns = ["Time", "Entity", "Product", "Instrument", "Token", "Side",
                        "Notional", "Price", "Bps", "Revenue", "Venue"]
        show = fmt(show, {"Notional": lambda v: usd(v, 1),
                          "Revenue": lambda v: usd(v, 0),
                          "Bps": lambda v: f"{v:.1f}",
                          "Price": lambda v: f"{v:,.2f}"})
        st.dataframe(show, width="stretch", hide_index=True, height=420)
        if len(blot) > 300:
            st.caption(f"Showing latest 300 of {len(blot):,}.")

# ══════════════════════════════════════════════════════════════════════════════
# CREDIT
# ══════════════════════════════════════════════════════════════════════════════
with tab_cr:
    if credit.empty:
        st.info(f"{parent} has no active credit facilities.")
    else:
        cr1, cr2, cr3, cr4 = st.columns(4)
        cr1.metric("Facilities", f"{len(credit)}")
        cr2.metric("Principal", usd(credit["principal_usd"].sum()))
        cr3.metric("Drawn", usd(credit["drawn_usd"].sum()))
        cr4.metric("Collateral", usd(credit["collateral_usd"].sum()))

        near = credit[credit["status"].isin(["serious", "critical"])]
        if not near.empty:
            for _, r in near.iterrows():
                st.markdown(
                    theme.pill(f"{r['status'].upper()}: {r['deal_id']} LTV {r['ltv_pct']}%",
                               r["status"]) + f" &nbsp; {r['facility_type']} · "
                    f"{r['entity_name']} · matures {r['maturity']}",
                    unsafe_allow_html=True)

        st.subheader("Facilities")
        for _, r in credit.iterrows():
            with st.expander(
                    f"{r['deal_id']} · {r['facility_type']} · {r['entity_name']} · "
                    f"LTV {r['ltv_pct']}%"):
                a, b, c = st.columns(3)
                a.metric("Drawn / Principal",
                         f"{usd(r['drawn_usd'])} / {usd(r['principal_usd'])}")
                b.metric("Collateral", f"{usd(r['collateral_usd'])} {r['collateral_token']}")
                c.metric("Rate", f"{r['rate_bps']/100:.2f}%")
                a2, b2, c2 = st.columns(3)
                a2.metric("LTV", f"{r['ltv_pct']}%")
                b2.metric("Maturity", r["maturity"])
                c2.metric("Days to maturity", f"{r['days_to_maturity']}")
                st.markdown(theme.pill(r["status"].upper(), r["status"]),
                            unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# RELATIONSHIP
# ══════════════════════════════════════════════════════════════════════════════
with tab_rel:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Meeting brief")
        st.caption("Auto-generated from trading, positions, and credit")
        for head, bullets in build_brief(parent, kpi, trades, positions, credit, gong,
                                          window_label):
            st.markdown(f"**{head}**")
            for bt in bullets:
                st.markdown(f"- {bt}")
            st.markdown("")

        st.subheader("Call history")
        if gong.empty:
            st.info("No calls on record in window.")
        else:
            for _, r in gong.iterrows():
                with st.expander(f"**{r['title']}** — {r['call_date']} · "
                                 f"{int(r['duration_sec'])//60}m"):
                    st.caption(f"👥 {r['participants']}")
                    t = st.tabs(["Summary", "Key Points", "Next Steps"])
                    t[0].write(r["brief"])
                    t[1].write(r["key_points"])
                    t[2].write(r["next_steps"])
    with right:
        st.subheader("News")
        st.caption(f"Live headlines · {', '.join(tokens[:4]) if tokens else 'crypto'}")
        news = data.get_news(tokens)
        if not news:
            st.info("No headlines right now.")
        for item in news:
            st.markdown(f"**[{item['title']}]({item['url']})**  \n"
                        f"<small>{item['source']} · {item['published']}</small>",
                        unsafe_allow_html=True)
            st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# ANALYST — Opus 4.8
# ══════════════════════════════════════════════════════════════════════════════
with tab_ai:
    import llm

    st.subheader("Opus 4.8 analyst")
    st.caption("Grounded in everything on this page for the selected client, "
               "entities, and period.")

    if not llm.available():
        st.warning("No `ANTHROPIC_API_KEY` found. Set it in the environment "
                   "(or the repo `.env`) to enable the analyst.")
    elif trades.empty:
        st.info("No activity in the current selection to analyze.")
    else:
        # Build the context once from what the page already computed.
        tf_ai = data.token_flow(trades)
        desk_ai = data.get_desk_benchmark(tf_ai["token"].tolist(), start, end) \
            if not tf_ai.empty else pd.DataFrame()
        cmp_ai = data.desk_comparison(tf_ai, desk_ai) if not desk_ai.empty else pd.DataFrame()
        ctx = llm.build_context(parent, window_label, kpi,
                                data.product_activity(trades), tf_ai, structures,
                                credit, positions, cmp_ai, gong)

        c1, c2 = st.columns([1, 3])
        with c1:
            go_summary = st.button("✨ Generate activity summary", type="primary")
        with c2:
            st.caption(f"Context: {parent} · {window_label} · "
                       f"{'all entities' if len(picked) == len(ent_names) else ', '.join(picked)}")

        if go_summary:
            with st.chat_message("assistant"):
                st.write_stream(llm.summarize(ctx))

        with st.expander("Show the context the model sees"):
            st.code(ctx, language="markdown")

        st.markdown("---")
        st.subheader("Ask about this client")
        skey = f"chat::{parent}"
        if skey not in st.session_state:
            st.session_state[skey] = []

        for m in st.session_state[skey]:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

        q = st.chat_input(f"Ask Opus about {parent}'s activity, positions, risk…")
        if q:
            st.session_state[skey].append({"role": "user", "content": q})
            with st.chat_message("user"):
                st.markdown(q)
            with st.chat_message("assistant"):
                answer = st.write_stream(llm.chat(ctx, st.session_state[skey][:-1], q))
            st.session_state[skey].append({"role": "assistant", "content": answer})
