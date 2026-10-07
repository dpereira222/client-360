"""
Client page — one relationship, every product. Sidebar: client + period.
Tabs: Overview · Risk · Peers · Relationship · Analyst (sub-views as nested tabs).
"""
from __future__ import annotations
import datetime as _dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import theme
import ui
from ui import usd, fmt


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
            cr.append(f":orange[:material/warning:] **{r['deal_id']}** at **{r['ltv_pct']}% LTV** — margin health "
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


# ── Sidebar + masthead ────────────────────────────────────────────────────────
parent = ui.client_picker()
start, end, window_label = ui.date_range()

ents = data.get_entities(parent)
trades_all = data.get_trades(parent, start, end)
positions = data.get_positions(parent)
credit = data.get_credit(parent)
gong = data.get_gong(parent, start, end)


# Entity focus (sidebar, under the client)
ent_names = ents["entity_name"].tolist()
picked = st.sidebar.multiselect("Focus entities", ent_names, default=ent_names,
                                help="Default: all legal entities. Pick one to drill in.")
picked_ids = ents[ents["entity_name"].isin(picked)]["entity_id"].tolist()
trades = trades_all[trades_all["entity_id"].isin(picked_ids)] if not trades_all.empty else trades_all
ui.sidebar_footer()

kpi = data.overview_kpis(trades, positions, credit)
tokens = trades["token"].value_counts().index.tolist()[:5] if not trades.empty else []
structures = data.option_structures(trades, positions)

# ── Masthead ──────────────────────────────────────────────────────────────────
prof = data.get_profile(parent)
tier = prof["tier"] if prof else "—"
since = prof["since"] if prof else "—"
cover = prof["coverage"] if prof else "—"
ui.masthead(parent, f"{tier} · client since {since} · covered by {cover} · "
                    f"{len(ents)} entit{'y' if len(ents) == 1 else 'ies'} · {window_label}")

covered = kpi["products_active"]
cells = "".join(
    f"<span class='fx-cov{' fx-cov-on' if p in covered else ''}' "
    f"style='--c:{theme.PRODUCT_COLORS[p]}'>{p}</span>" for p in theme.PRODUCT_ORDER)
chips = "".join(f"<span class='fx-chip'>{r.entity_name} · {r.entity_type}</span>"
                for r in ents.itertuples())
st.markdown(f"<div class='fx-idrow'><div><span class='fx-idlabel'>Products</span>{cells}</div>"
            f"<div><span class='fx-idlabel'>Entities</span>{chips}</div></div>",
            unsafe_allow_html=True)

# ── KPI strip ─────────────────────────────────────────────────────────────────
k = st.columns(6)
k[0].metric("Notional traded", usd(kpi["notional"]))
k[1].metric("Revenue to FX", usd(kpi["revenue"], 0 if kpi["revenue"] < 1e6 else 2))
k[2].metric("Blended bps", f"{kpi['blended_bps']:.1f}")
k[3].metric("Open positions", usd(kpi["net_position"]))
k[4].metric("Unrealized P&L", usd(kpi["unrealized_pnl"]))
k[5].metric("Credit drawn", usd(kpi["credit_outstanding"]) if kpi["credit_outstanding"] else "—")

tab_ov_, tab_risk, tab_peer, tab_relx, tab_ai = st.tabs(
    [":material/dashboard: Overview", ":material/shield: Risk", ":material/groups: Peers",
     ":material/handshake: Relationship", ":material/auto_awesome: Analyst"])
with tab_ov_:
    tab_ov, tab_trd = st.tabs(["Summary", "Trades"])
with tab_risk:
    tab_pos, tab_opt, tab_cr = st.tabs(["Positions", "Options", "Credit"])
with tab_relx:
    tab_rel, tab_notes, tab_pitch = st.tabs(["Brief", "Notes", "Pitches & Chat"])

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
# PEERS — what similar clients are doing (anonymous, k-anonymized)
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner="Building peer cohort…")
def _peer_view(parent, start, end, level):
    import peers
    import pitches
    ext = (pd.Timestamp(start) - pd.Timedelta(weeks=16)).date().isoformat()
    tr = data.get_trades(parent, start, end)   # parent level: like-for-like with peers
    ideas = data.get_trade_ideas(parent, start, end)
    if not ideas.empty:
        m = pitches.match_executions(ideas, data.get_trades(parent, start, end))
        ideas = ideas.merge(m[["idea_id", "executed"]], on="idea_id")
    return peers.build(parent, data.get_profile(parent), tr, data.option_structures(tr),
                       ideas, start, end, level,
                       data.get_peer_activity(parent, ext, end),
                       data.get_peer_strategies(parent, ext, end),
                       data.get_desk_idea_uptake(exclude_parent=parent))


_PT_PILL = {"Product gap": ("GAP", "serious"), "Strategy gap": ("GAP", "serious"),
            "Trending": ("TREND", "good"), "Contrarian": ("CONTRARIAN", "warning"),
            "Peers took it": ("PEERS TOOK IT", "good")}

with tab_peer:
    import peers as _peers
    pc1, pc2 = st.columns([2, 3])
    with pc1:
        level = st.radio("Compare against", _peers.LEVELS, horizontal=True,
                         help="Widens automatically if the cohort is too small to stay anonymous.")
    pv = _peer_view(parent, start, end, level)
    with pc2:
        st.markdown(
            f"<div style='padding-top:1.7rem;color:#52514e'>Cohort: <b>{pv['n']} "
            f"{pv['label']}</b> · {start} → {end}"
            + (" · <i>widened from " + pv["level_asked"].lower() + " to keep peers anonymous</i>"
               if pv["widened"] else "") + "</div>", unsafe_allow_html=True)

    if pv["empty"]:
        st.info(f"Fewer than {_peers.K_MIN} comparable clients — peer view suppressed "
                "to protect anonymity.")
    else:
        st.subheader("Say it on the call")
        if pv["points"]:
            for pt in pv["points"]:
                lab, lvl = _PT_PILL.get(pt["kind"], (pt["kind"].upper(), "good"))
                st.markdown(f"{theme.pill(lab, lvl)} &nbsp; {pt['say']}  \n"
                            f"<span style='color:#898781;font-size:.85rem'>{pt['why']}</span>",
                            unsafe_allow_html=True)
        else:
            st.caption("Nothing stands out vs this cohort in the period.")
        st.caption("Peers are never named. Counts only; any figure backed by fewer than "
                   f"{_peers.K_MIN} clients, or dominated by one, is suppressed.")

        st.markdown("")
        g1, g2 = st.columns(2)
        # ── Product adoption: % of cohort active, client marked ───────────────
        with g1:
            st.subheader("Product adoption")
            ad = pv["adoption"]
            fig = go.Figure(go.Bar(
                x=ad["pct"] * 100, y=ad["product"], orientation="h",
                marker_color=[theme.PRODUCT_COLORS[p] for p in ad["product"]],
                text=[("✓ client active" if a else "✗ client not active") for a in ad["client_active"]],
                textposition="outside", textfont=dict(color=theme.INK_SECONDARY),
                customdata=ad[["peers_active", "n_peers"]],
                hovertemplate="%{y}: %{customdata[0]} of %{customdata[1]} peers "
                              "(%{x:.0f}%)<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=260, showlegend=False,
                              yaxis=dict(autorange="reversed"),
                              xaxis=dict(title="% of cohort active", range=[0, 135],
                                         tickvals=[0, 25, 50, 75, 100]))
            st.plotly_chart(fig, width="stretch")

        # ── Token mix: dumbbell, client vs cohort median ──────────────────────
        with g2:
            st.subheader("Token mix vs cohort")
            mx = pv["mix"]
            if mx.empty:
                st.caption("Not enough peers per token to show a mix.")
            else:
                mx = mx.iloc[::-1]
                fig = go.Figure()
                for _, r in mx.iterrows():
                    fig.add_scatter(x=[r["peer_mean_share"] * 100, r["client_share"] * 100],
                                    y=[r["token"]] * 2, mode="lines",
                                    line=dict(color=theme.GRID, width=2),
                                    hoverinfo="skip", showlegend=False)
                fig.add_scatter(name="Cohort avg", x=mx["peer_mean_share"] * 100, y=mx["token"],
                                mode="markers", marker=dict(size=10, color=theme.INK_MUTED,
                                                            line=dict(color="#fff", width=2)),
                                customdata=mx[["peers_trading", "n_peers"]],
                                hovertemplate="%{y} · cohort avg %{x:.0f}% of volume "
                                              "(%{customdata[0]} of %{customdata[1]} trade it)"
                                              "<extra></extra>")
                fig.add_scatter(name=parent, x=mx["client_share"] * 100, y=mx["token"],
                                mode="markers", marker=dict(size=10, color=theme.SLOT["blue"],
                                                            line=dict(color="#fff", width=2)),
                                hovertemplate="%{y} · " + parent + " %{x:.0f}% of volume"
                                              "<extra></extra>")
                fig.update_layout(height=260, xaxis=dict(title="% of notional", ticksuffix="%"))
                st.plotly_chart(fig, width="stretch")
                st.caption(f"Tokens traded by fewer than {_peers.K_MIN} peers are hidden.")

        g3, g4 = st.columns(2)
        # ── Trends: diverging bars, last 4w vs prior 12w ──────────────────────
        with g3:
            st.subheader("What's changing")
            st.caption("Cohort avg weekly activity, last 4 weeks vs the 12 before.")
            tr_ = pv["trends"]
            if tr_.empty:
                st.caption("No trend clears the anonymity bar.")
            else:
                tr_ = tr_.dropna(subset=["change_pct"])
                tr_ = pd.concat([tr_.head(5), tr_.tail(3)]).drop_duplicates(["dimension", "item"])
                tr_ = tr_.sort_values("change_pct")
                lab = [f"{i} · {d.replace('Options strategy', 'strategy').lower()}"
                       for i, d in zip(tr_["item"], tr_["dimension"])]
                fig = go.Figure(go.Bar(
                    x=tr_["change_pct"] * 100, y=lab, orientation="h",
                    marker_color=[theme.BUY if v >= 0 else theme.SELL for v in tr_["change_pct"]],
                    customdata=tr_[["peers_recent", "new_peers"]],
                    hovertemplate="%{y}: %{x:+.0f}% · %{customdata[0]} active, "
                                  "%{customdata[1]} new<extra></extra>"))
                theme.bar_ends(fig)
                fig.update_layout(height=max(220, 30 * len(tr_)), showlegend=False,
                                  xaxis=dict(title="% change", ticksuffix="%",
                                             zeroline=True, zerolinecolor=theme.AXIS))
                st.plotly_chart(fig, width="stretch")
                st.caption("Blue = rising · Red = falling")

        # ── Direction: % of peers net buyers ─────────────────────────────────
        with g4:
            st.subheader("Buyers or sellers?")
            st.caption("Share of cohort peers net buying each token, vs the client.")
            dr = pv["direction"]
            if dr.empty:
                st.caption("Not enough peers per token.")
            else:
                dr = dr.head(8).iloc[::-1]
                ylab = [f"{t}  ({'client ' + c.lower() if c else 'client n/a'})"
                        for t, c in zip(dr["token"], dr["client_dir"])]
                fig = go.Figure()
                fig.add_bar(name="Net buyers", x=dr["pct_buyers"] * 100, y=ylab,
                            orientation="h", marker_color=theme.BUY,
                            customdata=dr[["net_buyers", "peers"]],
                            hovertemplate="%{customdata[0]} of %{customdata[1]} net buyers"
                                          "<extra></extra>")
                fig.add_bar(name="Net sellers", x=(1 - dr["pct_buyers"]) * 100, y=ylab,
                            orientation="h", marker_color=theme.SELL,
                            hovertemplate="%{x:.0f}% net sellers<extra></extra>")
                fig.add_vline(x=50, line=dict(color=theme.INK_MUTED, width=1, dash="dot"))
                fig.update_layout(barmode="stack", height=max(220, 32 * len(dr)), bargap=0.35,
                                  legend=dict(traceorder="normal"),
                                  xaxis=dict(range=[0, 100.5], ticksuffix="%",
                                             tickvals=[0, 25, 50, 75, 100]))
                st.plotly_chart(fig, width="stretch")

        g5, g6 = st.columns(2)
        # ── Options strategies ───────────────────────────────────────────────
        with g5:
            st.subheader("Options strategies")
            sp = pv["strategies"]
            if sp.empty:
                st.caption("Too few options traders in this cohort to show strategies.")
            else:
                sp = sp.head(8).iloc[::-1]
                fig = go.Figure(go.Bar(
                    x=sp["pct"] * 100, y=sp["strategy"], orientation="h",
                    marker_color=theme.SLOT["violet"],
                    text=[f"client: {c}" if c else "client: none" for c in sp["client_count"]],
                    textposition="outside", textfont=dict(color=theme.INK_SECONDARY),
                    customdata=sp[["peers_using", "n_options_peers", "bias"]],
                    hovertemplate="%{y} (%{customdata[2]}): %{customdata[0]} of "
                                  "%{customdata[1]} options peers<extra></extra>"))
                theme.bar_ends(fig)
                fig.update_layout(height=max(220, 30 * len(sp)), showlegend=False,
                                  xaxis=dict(title="% of options-trading peers using it",
                                             range=[0, 130], tickvals=[0, 25, 50, 75, 100]))
                st.plotly_chart(fig, width="stretch")

        # ── Benchmarks: percentile ───────────────────────────────────────────
        with g6:
            st.subheader("Where they rank")
            bm = pv["benchmarks"]
            bm = bm.iloc[::-1]
            fig = go.Figure(go.Bar(
                x=bm["percentile"] * 100, y=bm["metric"], orientation="h",
                marker_color=theme.SLOT["blue"],
                text=[f"{p*100:.0f}th pct" for p in bm["percentile"]], textposition="outside",
                textfont=dict(color=theme.INK_SECONDARY),
                hovertemplate="%{y}: %{x:.0f}th percentile of cohort<extra></extra>"))
            fig.add_vline(x=50, line=dict(color=theme.INK_MUTED, width=1, dash="dot"),
                          annotation_text="median", annotation_position="top",
                          annotation_font_color=theme.INK_MUTED)
            theme.bar_ends(fig)
            fig.update_layout(height=260, showlegend=False,
                              xaxis=dict(range=[0, 120], tickvals=[0, 25, 50, 75, 100]))
            st.plotly_chart(fig, width="stretch")
            st.caption("Blended bps: a low percentile means this client pays less than "
                       "peers — margin headroom, not a weakness in the relationship.")

        # ── Desk ideas: cohort take-rate ─────────────────────────────────────
        up = pv["uptake"]
        if up is not None and not up.empty:
            st.subheader("How the cohort took our desk ideas")
            d = up[["headline", "pitched", "executed", "take_rate", "client"]].copy()
            d.columns = ["Idea", "Peers pitched", "Peers executed", "Take rate", parent]
            d = fmt(d, {"Take rate": lambda v: f"{v*100:.0f}%"})
            st.dataframe(d, width="stretch", hide_index=True)

        with st.expander("Data tables"):
            t1 = pv["adoption"][["product", "peers_active", "n_peers", "pct", "client_active"]].copy()
            t1.columns = ["Product", "Peers active", "Cohort", "% active", "Client active"]
            st.dataframe(fmt(t1, {"% active": lambda v: f"{v*100:.0f}%"}),
                         width="stretch", hide_index=True)
            if not pv["mix"].empty:
                t2 = pv["mix"][["token", "peers_trading", "peer_median_share",
                                "peer_mean_share", "client_share"]].copy()
                t2.columns = ["Token", "Peers trading", "Cohort median share",
                              "Cohort avg share", "Client share"]
                st.dataframe(fmt(t2, {c: (lambda v: f"{v*100:.1f}%") for c in t2.columns[2:]}),
                             width="stretch", hide_index=True)
            b2 = pv["benchmarks"][["metric", "client", "p25", "median", "p75", "percentile"]].copy()
            b2.columns = ["Metric", "Client", "P25", "Median", "P75", "Percentile"]
            num = lambda v: usd(v) if abs(v) >= 1e4 else f"{v:,.1f}"  # noqa: E731
            st.dataframe(fmt(b2, {"Client": num, "P25": num, "Median": num, "P75": num,
                                  "Percentile": lambda v: f"{v*100:.0f}th"}),
                         width="stretch", hide_index=True)


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
                    st.caption(f":material/group: {r['participants']}")
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
# NOTES — CRM call notes with auto-hashtags
# ══════════════════════════════════════════════════════════════════════════════
_VERDICT_PILL = {"Cross-sell": "good", "Onboarding": "warning", "Stale note": "serious",
                 "Not interested": "critical", "Consistent": "good"}


def _tag_chips(tag_list) -> str:
    out = []
    for t_ in tag_list:
        if t_.startswith("-"):
            out.append(f"<span class='fx-chip' style='color:#a52121;border-color:#f0c8c8;"
                       f"text-decoration:line-through'>{t_[1:]}</span>")
        else:
            out.append(f"<span class='fx-chip'>{t_}</span>")
    return "".join(out)


def _save_note_cb(parent_, key_):
    txt = st.session_state.get(f"{key_}::text", "").strip()
    if not txt:
        st.session_state[f"{key_}::msg"] = ("warning", "Write something first.")
        return
    when = st.session_state.get(f"{key_}::date", data.TODAY)
    row = data.save_note(parent_, st.session_state[f"{key_}::author"],
                         st.session_state[f"{key_}::type"], txt,
                         note_ts=_dt.datetime.combine(when, _dt.datetime.now().time())
                         .isoformat(timespec="seconds"))
    st.session_state[f"{key_}::text"] = ""
    st.session_state[f"{key_}::msg"] = (
        "success", f"Saved {row['note_id']} · {len(row['tags'])} tags")


with tab_notes:
    import notes as _notes
    import tags as _tags

    nkey = f"note::{parent}"
    all_hist = st.toggle("All history (ignore the date range)", value=True, key="notes_all",
                         help="Notes are long-lived knowledge — a March 'not set up for "
                              "options' still matters in October.")
    n_start, n_end = ("2000-01-01", data.TODAY.isoformat()) if all_hist else (start, end)
    cnotes = data.get_notes(parent, n_start, n_end)
    nv = _notes.build(parent, cnotes, trades_all, data.TODAY)

    # ── Log a note ───────────────────────────────────────────────────────────
    with st.expander("Log a call note", icon=":material/edit_note:", expanded=cnotes.empty):
        cover = prof["coverage"] if prof else ""
        authors = sorted({cover, *nv["authors"]} - {""}) or ["Sales"]
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Author", authors, index=authors.index(cover) if cover in authors else 0,
                     key=f"{nkey}::author")
        c2.selectbox("Type", ["Call", "Meeting", "Chat", "Conference"], key=f"{nkey}::type")
        c3.date_input("Date", value=data.TODAY, max_value=data.TODAY, key=f"{nkey}::date")
        draft = st.text_area("Note", key=f"{nkey}::text", height=110,
                             placeholder="e.g. James is using our TWAP for ETH, wants to look "
                                         "at a condor into Sept expiry, perp pricing too wide.")
        if draft.strip():
            st.markdown("Tags: " + (_tag_chips(_tags.tag_list(draft)) or "<i>none found</i>"),
                        unsafe_allow_html=True)
            st.caption("Struck-through = said *no* to it (kept: it stops us pitching it). "
                       "Tags refresh when you click out of the box.")
        st.button("Save note", type="primary", on_click=_save_note_cb, args=(parent, nkey))
        msg = st.session_state.pop(f"{nkey}::msg", None)
        if msg:
            getattr(st, msg[0])(msg[1])

    if cnotes.empty:
        st.info("No notes logged for this client yet.")
    else:
        prof_t, vb, fu = nv["profile"], nv["vs_book"], nv["follow_ups"]
        last_ts = pd.Timestamp(cnotes["note_ts"].max())
        gaps = int((vb["verdict"] == "Cross-sell").sum()) if not vb.empty else 0
        k = st.columns(5)
        k[0].metric("Notes logged", len(cnotes))
        k[1].metric("Last note", f"{(pd.Timestamp(data.TODAY) - last_ts.normalize()).days}d ago",
                    help=f"{last_ts:%Y-%m-%d} by {cnotes.iloc[0]['author']}")
        k[2].metric("Distinct tags", len(prof_t))
        k[3].metric("Open follow-ups", len(fu),
                    help=f"#FollowUp in the last {_notes.FOLLOW_UP_DAYS} days")
        k[4].metric("Cross-sell gaps", gaps,
                    help="Talked about it in notes, never traded it with us")

        # ── What we know ─────────────────────────────────────────────────────
        st.subheader("What we know")
        st.caption("Auto-tagged from the notes; filter the feed below by any tag to see its source. "
                   "Stance is the latest mention — people change their minds.")
        for cat in _tags.CATEGORIES:
            p_ = prof_t[prof_t["category"] == cat]
            if p_.empty:
                continue
            chips = _tag_chips([("-" if r.stance < 0 else "") + r.tag for r in p_.itertuples()])
            counts = " ".join(f"{r.tag}×{r.mentions}" for r in p_.itertuples())
            st.markdown(f"<div style='margin:4px 0'><span style='color:#898781;font-size:.78rem;"
                        f"text-transform:uppercase;letter-spacing:.04em;display:inline-block;"
                        f"width:90px'>{cat}</span>{chips}"
                        f"<span style='color:#898781;font-size:.75rem'> &nbsp;{counts}</span></div>",
                        unsafe_allow_html=True)

        st.markdown("")
        c1, c2 = st.columns([3, 2])
        # ── Notes vs book ────────────────────────────────────────────────────
        with c1:
            st.subheader("Notes vs the book")
            if vb.empty:
                st.caption("No product or token tags in the notes yet.")
            else:
                for r in vb[vb["verdict"] != "Consistent"].itertuples():
                    st.markdown(f"{theme.pill(r.verdict.upper(), _VERDICT_PILL[r.verdict])} "
                                f"&nbsp; **{r.tag}** — notes say *{r.notes_say}*, "
                                f"{'trades it' if r.trades_with_us else 'no trades'} with us "
                                f"· last {pd.Timestamp(r.last_seen):%b %d}  \n"
                                f"<span style='color:#898781;font-size:.85rem'>“{r.evidence}”</span>",
                                unsafe_allow_html=True)
                ok = vb[vb["verdict"] == "Consistent"]["tag"].tolist()
                if ok:
                    st.caption("Consistent with the blotter: " + " ".join(ok))
        # ── Follow-ups ───────────────────────────────────────────────────────
        with c2:
            st.subheader("Follow-ups")
            if fu.empty:
                st.caption(f"No #FollowUp in the last {_notes.FOLLOW_UP_DAYS} days.")
            else:
                for r in fu.itertuples():
                    st.markdown(f"{theme.pill(f'{r.age_days}d', 'warning' if r.age_days > 7 else 'good')}"
                                f" &nbsp; **{r.author}** · {pd.Timestamp(r.note_ts):%b %d}  \n{r.text}",
                                unsafe_allow_html=True)

        # ── Note feed ────────────────────────────────────────────────────────
        st.subheader("Notes")
        present = prof_t["tag"].tolist()
        f1, f2, f3 = st.columns([3, 1, 2])
        want = f1.multiselect("Filter by tag", present, key="notes_filter")
        fmode = f2.radio("Match", ["all", "any"], horizontal=True, key="notes_fmode")
        who = f3.multiselect("Author", nv["authors"], key="notes_author")
        feed = _notes.filter_by_tags(cnotes, want, fmode)
        if who:
            feed = feed[feed["author"].isin(who)]
        st.caption(f"{len(feed)} of {len(cnotes)} notes")
        for r in feed.head(50).itertuples():
            st.markdown(f"<div style='border-left:3px solid #e1e0d9;padding:4px 0 6px 12px;"
                        f"margin-bottom:8px'><span style='color:#52514e;font-size:.82rem'>"
                        f"<b>{pd.Timestamp(r.note_ts):%Y-%m-%d}</b> · {r.meeting_type} · "
                        f"{r.author}{' · logged in app' if r.source == 'app' else ''}</span><br>"
                        f"{r.text}<br>{_tag_chips(r.tags)}</div>", unsafe_allow_html=True)

    # ── Book-wide search ─────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Who has talked about…")
    st.caption("Across every client's notes. 'Said no' counts notes where the client "
               "explicitly passed — don't pitch them.")
    b1, b2 = st.columns([4, 1])
    bwant = b1.multiselect("Tags", list(_tags.TAXONOMY), default=["#Vol", "#ETH"],
                           key="notes_book_tags")
    bmode = b2.radio("Match", ["all", "any"], horizontal=True, key="notes_book_mode")
    wm = _notes.who_mentioned(data.get_notes(None, "2000-01-01", data.TODAY.isoformat()),
                              bwant, bmode)
    if wm.empty:
        st.caption("Nobody yet.")
    else:
        wt = wm.rename(columns={"parent": "Client", "mentions": "Mentions", "against": "Said no",
                                "last_seen": "Last", "last_author": "By", "last_text": "Latest note"})
        st.dataframe(fmt(wt, {"Last": lambda v: f"{pd.Timestamp(v):%Y-%m-%d}" if pd.notna(v) else "—",
                              "By": lambda v: v or "—"}),
                     width="stretch", hide_index=True,
                     column_config={"Latest note": st.column_config.TextColumn(width="large")})


# ══════════════════════════════════════════════════════════════════════════════
# PITCHES & CHAT
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner="Scoring pitched ideas…")
def _pitch_view(parent, start, end):
    import pitches
    return pitches.build(parent, data.get_profile(parent),
                         data.get_trade_ideas(parent, start, end),
                         data.get_trades(parent, start, end), data.get_price_history(),
                         data.get_chat_messages(parent, start, end),
                         data.get_rfqs(parent, start, end), data.TODAY)


_PITCH_PILL = {"Win together": ("WIN", "good"), "Missed": ("MISSED", "warning"),
               "Heads-up": ("HEADS-UP", "serious"), "Check booking": ("CHECK BOOKING", "critical"),
               "Level reached": ("LEVEL HIT", "good")}

with tab_pitch:
    pt = _pitch_view(parent, start, end)
    t = pt["tracker"]
    sc = pt["score"]
    if t.empty:
        st.info("No trade ideas pitched to this client in the period.")
    else:
        st.caption("Ideas are pulled from the client's Telegram chat; whether they took it is "
                   "inferred from the blotter, never from what they said. Each idea closes at "
                   "the first of target / stop / horizon — no hindsight.")
        k = st.columns(6)
        k[0].metric("Ideas pitched", sc["pitched"])
        k[1].metric("Taken", f"{sc['executed']}  ({sc['take_rate']*100:.0f}%)")
        k[2].metric("Ideas that worked",
                    f"{sc['hit_rate']*100:.0f}%" if sc["hit_rate"] is not None else "—",
                    help=f"Of {sc['n_closed']} closed ideas")
        k[3].metric("Client P&L on our ideas", usd(sc["client_pnl"]))
        k[4].metric("Left on the table", usd(sc["missed_pnl"]),
                    help="P&L on winning ideas they didn't take, on the size we offered")
        k[5].metric("Passed, then traded", sc["passed_then_traded"],
                    help="Said no (or didn't reply) but the fill shows up on our blotter")

        st.subheader("Say it on the call")
        if pt["points"]:
            for p_ in pt["points"]:
                lab, lvl = _PITCH_PILL.get(p_["kind"], (p_["kind"].upper(), "good"))
                st.markdown(f"{theme.pill(lab, lvl)} &nbsp; {p_['say']}", unsafe_allow_html=True)
        else:
            st.caption("Nothing worth raising from recent pitches.")

        st.markdown("")
        # ── Track record: one dot per idea ───────────────────────────────────
        st.subheader("Track record")
        fig = go.Figure()
        for took, name, color in [(True, "Took it", theme.SLOT["blue"]),
                                  (False, "Didn't take it", theme.INK_MUTED)]:
            d = t[t["executed"] == took]
            if d.empty:
                continue
            fig.add_scatter(
                name=name, x=d["ts"], y=d["ret_pct"] * 100, mode="markers",
                marker=dict(size=11, color=color, line=dict(color="#fff", width=2)),
                customdata=d[["headline", "status", "exec_flag", "pitched_by"]],
                hovertemplate="<b>%{customdata[0]}</b> · %{x|%b %d}<br>%{y:+.1f}% · "
                              "%{customdata[1]}<br>%{customdata[2]} · pitched by "
                              "%{customdata[3]}<extra></extra>")
        fig.add_hline(y=0, line=dict(color=theme.AXIS, width=1))
        fig.update_layout(height=300, yaxis=dict(title="Idea return", ticksuffix="%"),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, width="stretch")

        tt = t[["ts", "headline", "pitched_by", "size_usd", "reaction", "exec_flag",
                "status", "ret_pct", "pnl_usd", "client_pnl_usd"]].copy()
        tt.columns = ["Pitched", "Idea", "By", "Size offered", "Said", "Did", "Status",
                      "Return", "Idea P&L", "Client P&L"]
        st.dataframe(fmt(tt, {"Pitched": lambda v: f"{pd.Timestamp(v):%Y-%m-%d}",
                              "Size offered": usd, "Idea P&L": usd,
                              "Client P&L": lambda v: usd(v) if v else "—",
                              "Return": lambda v: f"{v*100:+.1f}%"}),
                     width="stretch", hide_index=True)

        c1, c2 = st.columns(2)
        # ── Why they pass ────────────────────────────────────────────────────
        with c1:
            st.subheader("Why they pass")
            pr = pt["pass_reasons"]
            if pr.empty:
                st.caption("They took everything we pitched.")
            else:
                pr = pr.iloc[::-1]
                fig = go.Figure(go.Bar(
                    x=pr["n"], y=pr["why"], orientation="h", marker_color=theme.SLOT["blue"],
                    text=[f"would have made {usd(v)}" if v >= 0 else f"dodged a {usd(-v)} loss"
                          for v in pr["would_have_made"]],
                    textposition="outside", textfont=dict(color=theme.INK_SECONDARY),
                    hovertemplate="%{y}: %{x} ideas<extra></extra>"))
                theme.bar_ends(fig)
                fig.update_layout(height=max(200, 40 * len(pr)), showlegend=False,
                                  xaxis=dict(title="Ideas passed", dtick=1,
                                             range=[0, pr["n"].max() * 1.9]))
                st.plotly_chart(fig, width="stretch")
                st.caption("'Risk limits' and 'better price elsewhere' are fixable — "
                           "size down, or sharpen the quote.")
        # ── Who pitches ──────────────────────────────────────────────────────
        with c2:
            st.subheader("By salesperson")
            bp = pt["by_pitcher"].copy()
            bp.columns = ["Pitched by", "Ideas", "Taken", "Worked", "Avg return", "Idea P&L"]
            st.dataframe(fmt(bp, {"Worked": lambda v: f"{v*100:.0f}%",
                                  "Avg return": lambda v: f"{v*100:+.1f}%", "Idea P&L": usd}),
                         width="stretch", hide_index=True)
            cb = pt["callbacks"]
            if not cb.empty:
                st.markdown("**'Call me at…' levels**")
                cbd = cb[["headline", "asked_level", "asked_on", "reached", "reached_on",
                          "last_px", "executed"]].copy()
                cbd.columns = ["Idea", "Level", "Asked", "Reached", "On", "Now", "Traded"]
                import pitches as _pitches
                st.dataframe(fmt(cbd, {"Level": _pitches.fmt_px, "Now": _pitches.fmt_px,
                                       "Reached": lambda v: "✓" if v else "—",
                                       "Traded": lambda v: "✓" if v else "—"}),
                             width="stretch", hide_index=True)

    # ── Chat engagement ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Chat engagement")
    ch = pt["chat"]
    if not ch:
        st.info("No archived chat for this client in the period.")
    else:
        today_ts = pd.Timestamp(data.TODAY)
        quiet = (today_ts - pd.Timestamp(ch["last_client_msg"])).days
        k = st.columns(4)
        k[0].metric("Messages / week", f"{ch['per_week']:.1f}")
        k[1].metric("From the client", f"{ch['client_share']*100:.0f}%")
        k[2].metric("Median reply to us",
                    f"{ch['median_reply_min']:.0f} min" if ch["median_reply_min"] else "—")
        k[3].metric("Last heard from client", f"{quiet}d ago")
        if quiet >= 7:
            st.markdown(f"{theme.pill('GONE QUIET', 'warning')} &nbsp; No client message in "
                        f"{quiet} days — worth a check-in.", unsafe_allow_html=True)

        c1, c2 = st.columns([3, 2])
        with c1:
            wk = pt["weekly"]
            fig = go.Figure()
            for role, color in [("Client", theme.SLOT["blue"]), ("FalconX", theme.SLOT["green"])]:
                d = wk[wk["sender_role"] == role]
                fig.add_bar(name=role, x=d["week"], y=d["messages"], marker_color=color,
                            marker_line=dict(color=theme.SURFACE, width=2),
                            hovertemplate=role + " · week of %{x|%b %d}: %{y} messages"
                                                 "<extra></extra>")
            fig.update_layout(barmode="stack", height=260, bargap=0.25,
                              yaxis=dict(title="Messages / week"),
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, width="stretch")
        with c2:
            st.markdown("**What they ask about**")
            it = pt["interests"]
            if it.empty:
                st.caption("No token mentions from the client.")
            else:
                gap = it[~it["trades_with_us"]]
                if not gap.empty:
                    st.markdown(theme.pill("LATENT DEMAND", "good") + " &nbsp; Asked about, "
                                "not trading with us: <b>" + ", ".join(gap["token"]) + "</b>",
                                unsafe_allow_html=True)
                itd = it.copy()
                itd.columns = ["Token", "Mentions", "Last asked", "Trades with us"]
                st.dataframe(fmt(itd, {"Last asked": lambda v: f"{pd.Timestamp(v):%Y-%m-%d}",
                                       "Trades with us": lambda v: "✓" if v else "—"}),
                             width="stretch", hide_index=True, height=240)

    # ── RFQs ─────────────────────────────────────────────────────────────────
    rq = pt["rfq"]
    if rq:
        st.subheader("Price requests in chat")
        k = st.columns(4)
        k[0].metric("RFQs", rq["rfqs"])
        k[1].metric("Hit rate", f"{rq['hit_rate']*100:.0f}%",
                    help=f"{usd(rq['notional_done'])} done of {usd(rq['notional_quoted'])} quoted")
        k[2].metric("Traded away", rq["traded_away"])
        k[3].metric("Too wide", rq["too_wide"])
        with st.expander("All RFQs"):
            r = pt["rfqs"][["ts", "token", "side", "product", "notional_usd", "quote_bid",
                            "quote_ask", "quoted_by", "response", "reason"]].copy()
            r.columns = ["When", "Token", "Side", "Product", "Notional", "Bid", "Ask",
                         "Quoted by", "Response", "Reason"]
            st.dataframe(fmt(r, {"When": lambda v: f"{pd.Timestamp(v):%Y-%m-%d %H:%M}",
                                 "Notional": usd}), width="stretch", hide_index=True)

    # ── Raw chat + extractor audit ───────────────────────────────────────────
    if not pt["msgs"].empty:
        with st.expander("Chat archive"):
            m = pt["msgs"].sort_values("ts", ascending=False)[["ts", "sender", "sender_role",
                                                               "text"]].copy()
            m.columns = ["When", "Sender", "Side", "Message"]
            st.dataframe(fmt(m, {"When": lambda v: f"{pd.Timestamp(v):%Y-%m-%d %H:%M}"}),
                         width="stretch", hide_index=True, height=320)

        with st.expander("Audit the extractor (re-read the chat with Opus 5.5)"):
            import llm
            st.caption("In prod a nightly job extracts ideas from chat. This re-runs it live "
                       "on the current window and scores it against the stored ideas. "
                       "Demo only — sends chat text to the Anthropic API.")
            if not llm.available():
                st.warning("No `ANTHROPIC_API_KEY` — extractor audit disabled.")
            elif st.button("Run extraction", key=f"extract::{parent}"):
                import chat_intel
                bar = st.progress(0.0, text="Reading chat…")
                ext, usage = chat_intel.extract(
                    pt["msgs"], parent,
                    on_progress=lambda i, n: bar.progress(i / max(n, 1),
                                                          text=f"Chunk {i}/{n}"))
                bar.empty()
                cmp_ = chat_intel.compare(ext, t)
                if cmp_:
                    k = st.columns(4)
                    k[0].metric("Stored ideas", cmp_["stored"])
                    k[1].metric("Found", cmp_["matched"])
                    k[2].metric("Missed", cmp_["missed"])
                    k[3].metric("Extra", cmp_["extra"])
                    if "field_accuracy" in cmp_:
                        acc = {**cmp_["field_accuracy"], "reason": cmp_["reason_accuracy"],
                               "entry ±1%": cmp_["entry_within_1pct"]}
                        st.markdown(" · ".join(f"**{f}** {v*100:.0f}%" for f, v in acc.items()))
                st.caption(f"{usage['calls']} calls · {usage['input_tokens']:,} in / "
                           f"{usage['output_tokens']:,} out tokens"
                           + (f" · :orange[:material/warning:] {usage['failed_chunks']} chunk(s) failed"
                              if usage.get("failed_chunks") else ""))


# ══════════════════════════════════════════════════════════════════════════════
# ANALYST — Opus 5.5
# ══════════════════════════════════════════════════════════════════════════════
with tab_ai:
    import llm

    st.subheader("Opus 5.5 analyst")
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
        import notes as _notes_ai
        import peers as _peers_ai
        import daily_color as _dc_ai
        lv_ai = ui.expiry_view()["live"]
        ctx = llm.build_context(
            parent, window_label, kpi, data.product_activity(trades), tf_ai, structures,
            credit, positions, cmp_ai, gong,
            peer=_peer_view(parent, start, end, _peers_ai.LEVELS[0]),
            pitch=_pitch_view(parent, start, end),
            notes_=_notes_ai.build(parent, data.get_notes(parent, "2000-01-01",
                                                          data.TODAY.isoformat()),
                                   trades_all, data.TODAY),
            expiring=lv_ai[lv_ai["parent"] == parent],
            color=_dc_ai.color_context(ui.color_view(data.TODAY, data.peer_id_for(parent))))

        c1, c2 = st.columns([1, 3])
        with c1:
            go_summary = st.button("Generate activity summary", icon=":material/auto_awesome:", type="primary")
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
