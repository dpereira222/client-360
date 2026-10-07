"""
Desk page — Expiries. Every live client option structure, book-wide.
"""
from __future__ import annotations
import datetime as _dt

import plotly.graph_objects as go
import streamlit as st

import data
import theme
import ui
from ui import usd, fmt

# ── Sidebar + masthead ────────────────────────────────────────────────────────
parent = ui.client_picker()
prof = data.get_profile(parent)
ui.sidebar_footer("Book-wide — the client only drives the “This client” scope.")
ui.masthead("Expiries", "Every live client option structure, book-wide")

# ══════════════════════════════════════════════════════════════════════════════
# EXPIRIES — every live client option structure, book-wide
# ══════════════════════════════════════════════════════════════════════════════
_EXP_PILL = {"Pin risk": "critical", "Against them": "serious", "In the money": "good",
             "Front leg ITM": "good"}

with st.container():
    import expiries as _ex

    ev = ui.expiry_view()
    lv = ev["live"]
    cover = prof["coverage"] if prof else ""
    scopes = ["This client", f"{cover}'s book" if cover else "My book", "Whole book"]
    scope = st.radio("Scope", scopes, horizontal=True, key="exp_scope",
                     help="Expiries ignore the sidebar date range — a structure traded "
                          "a year ago that expires next week matters now.")
    base = (lv[lv["parent"] == parent] if scope == scopes[0]
            else lv[lv["coverage"] == cover] if scope == scopes[1] else lv)

    with st.expander("Filters", expanded=False):
        f1, f2, f3 = st.columns(3)
        f_cli = f1.multiselect("Client", sorted(base["parent"].unique()), key="exp_cli")
        f_str = f2.multiselect("Structure", sorted(base["structure"].unique()), key="exp_str")
        f_tok = f3.multiselect("Token", sorted(base["token"].unique()), key="exp_tok")
        f4, f5, f6 = st.columns(3)
        horizon = f4.date_input("Expiring between",
                                value=(data.TODAY, data.TODAY + _dt.timedelta(days=120)),
                                min_value=data.TODAY, key="exp_rng")
        traded = f5.date_input("Traded between",
                               value=(data.TODAY - _dt.timedelta(days=365 * 3), data.TODAY),
                               max_value=data.TODAY, key="exp_trd")
        min_n = f6.select_slider("Min notional", [0, 100_000, 1_000_000, 5_000_000,
                                                  10_000_000, 50_000_000],
                                 value=0, format_func=lambda v: usd(v, 0) if v else "Any",
                                 key="exp_min")
    rng = lambda d: tuple(d) if isinstance(d, (tuple, list)) and len(d) == 2 else (None, None)  # noqa: E731
    (e_from, e_to), (t_from, t_to) = rng(horizon), rng(traded)
    view = _ex.apply_filters(base, parents=f_cli, structures=f_str, tokens=f_tok,
                             expiry_from=e_from, expiry_to=e_to, traded_from=t_from,
                             traded_to=t_to, min_notional=min_n)

    if view.empty:
        st.info("No live option structures match. Widen the scope or the filters.")
    else:
        wk_s, wk_e = ev["week"]
        this_wk = view[(view["expiry"] >= data.TODAY) & (view["expiry"] <= wk_e)]
        k = st.columns(5)
        k[0].metric("Live structures", f"{len(view)}",
                    help=f"Across {view['parent'].nunique()} clients")
        k[1].metric("Notional", usd(view["notional_usd"].sum()))
        k[2].metric("Next expiry", view["expiry"].min().strftime("%d %b"),
                    f"{int(view['dte'].min())}d", delta_color="off")
        k[3].metric("Expiring by " + wk_e.strftime("%d %b"), f"{len(this_wk)}",
                    help=f"{usd(this_wk['notional_usd'].sum())} notional")
        k[4].metric("Pin risk", f"{int(view['pin'].sum())}",
                    help=f"≤{_ex.PIN_DAYS}d to expiry with spot within "
                         f"{_ex.PIN_PCT*100:.0f}% of a strike")

        cL, cR = st.columns([3, 2])
        with cL:
            st.subheader("Expiry ladder")
            lad = _ex.ladder(view)
            fig = go.Figure(go.Bar(
                x=[d.strftime("%d %b %y") for d in lad["expiry"]], y=lad["notional_usd"],
                marker_color=theme.PRODUCT_COLORS["Options"],
                text=[usd(v) for v in lad["notional_usd"]], textposition="outside",
                customdata=list(zip(lad["structures"], lad["clients"], lad["pins"])),
                hovertemplate="%{x}<br>%{text} notional<br>%{customdata[0]} structures · "
                              "%{customdata[1]} clients · %{customdata[2]} pin risk"
                              "<extra></extra>"))
            theme.bar_ends(fig)
            fig.update_layout(height=300, showlegend=False, xaxis=dict(type="category"),
                              yaxis=dict(showticklabels=False, title=None))
            st.plotly_chart(fig, width="stretch")
        with cR:
            st.subheader("Where they stand")
            stc = view.groupby("status").agg(n=("structure_id", "count"),
                                             notional=("notional_usd", "sum"))
            order = ["Pin risk", "Against them", "In the money", "Front leg ITM",
                     "Front leg OTM", "Expiring worthless", "No mark"]
            rows_html = "".join(
                f"<div style='display:flex;justify-content:space-between;padding:6px 0;"
                f"border-bottom:1px solid {theme.GRID}'>"
                f"<span>{theme.pill(s_, _EXP_PILL[s_]) if s_ in _EXP_PILL else s_}</span>"
                f"<span style='color:{theme.INK_SECONDARY}'><b>{int(stc.loc[s_, 'n'])}</b>"
                f" · {usd(stc.loc[s_, 'notional'])}</span></div>"
                for s_ in order if s_ in stc.index)
            st.markdown(rows_html, unsafe_allow_html=True)
            st.caption("Marked at today's spot as if it expired now. ‘Against them’ = "
                       "the structure owes at expiry (short legs ITM).")

        pins = _ex.pin_risk(view)
        if not pins.empty:
            st.subheader(":material/warning: Pin risk")
            for r in pins.itertuples():
                st.markdown(f"{theme.pill('PIN', 'critical')} **{r.parent}** — "
                            f"{r.talking_point}", unsafe_allow_html=True)

        st.subheader("All live structures")
        tbl = view.assign(
            Expiry=view["expiry"].map(lambda d: d.strftime("%d %b %y")),
            Traded=view["trade_date"].map(lambda d: d.strftime("%d %b %y")),
            Strikes=view["strikes"].map(lambda ks: " / ".join(f"{k:g}" for k in ks)))
        tbl = tbl[["Expiry", "dte", "parent", "coverage", "token", "structure", "Strikes",
                   "bias", "notional_usd", "premium_usd", "spot", "nearest_pct",
                   "pnl_usd", "status", "Traded", "entity_name"]]
        tbl.columns = ["Expiry", "Days", "Client", "Coverage", "Token", "Structure",
                       "Strikes", "Bias", "Notional", "Premium (+paid/−recv)", "Spot",
                       "Spot vs strike", "P&L at spot", "Status", "Traded", "Entity"]
        st.dataframe(fmt(tbl, {"Notional": lambda v: usd(v, 1),
                               "Premium (+paid/−recv)": lambda v: usd(v, 0),
                               "Spot": lambda v: f"{v:,.2f}" if v < 100 else f"{v:,.0f}",
                               "Spot vs strike": lambda v: f"{v*100:.1f}%",
                               "P&L at spot": lambda v: usd(v, 0)}),
                     width="stretch", hide_index=True)

    # ── Monday note ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(":material/mail: Monday expiry note")
    wk_s, wk_e = ev["week"]
    reps = ev["coverage"] or ["—"]
    c1, c2 = st.columns([1, 3])
    who = c1.selectbox("Salesperson", reps,
                       index=reps.index(cover) if cover in reps else 0, key="exp_rep")
    c1.caption(f"Covers {wk_s:%a %d %b} – {wk_e:%a %d %b}. In prod this posts to the "
               "rep's Slack every Monday 7am; here you can copy it.")
    c2.code(_ex.digest(lv, who, wk_s, wk_e), language=None)

