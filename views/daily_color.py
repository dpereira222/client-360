"""
Desk page — Daily color. The 4:30pm ET note from anonymized desk flow.
"""
from __future__ import annotations
import datetime as _dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import data
import theme
import ui
from ui import fmt

# ── Sidebar + masthead ────────────────────────────────────────────────────────
parent = ui.client_picker()
prof = data.get_profile(parent)
ui.sidebar_footer("Book-wide — “For <client>” drops that client’s own flow.")
ui.masthead("Daily color", "The 4:30pm ET note from anonymized desk flow")

# ══════════════════════════════════════════════════════════════════════════════
# DAILY COLOR — the 4:30pm ET note (anonymized desk flow)
# ══════════════════════════════════════════════════════════════════════════════
with st.container():
    import daily_color as _dc

    c1, c2 = st.columns([1, 3])
    c_day = c1.date_input("Color day", value=data.TODAY, max_value=data.TODAY, key="dc_day",
                          help="Each note covers 4:30pm ET the day before → 4:30pm ET this day. "
                               "Ignores the sidebar date range.")
    ws, we = _dc.window(c_day)
    c2.caption(f"Window: {ws:%a %d %b %H:%M} → {we:%a %d %b %H:%M} UTC. Built from "
               "**anonymized** desk-wide flow. The note shows ratios and percentages only, "
               f"and each segment or strategy line needs ≥{_dc.peers.K_MIN} clients with "
               f"no single client above {_dc.peers.DOMINANCE*100:.0f}% of the flow.")
    cv = ui.color_view(c_day)

    k = st.columns(4)
    k[0].metric("Desk activity", f"{cv['desk']['activity_x']:.2f}×",
                help=f"Today's notional vs the {_dc.BASE_DAYS}-day average")
    k[1].metric("Clients active", f"{cv['desk']['n_peers']}")
    big = cv["moves"].iloc[0]
    k[2].metric("Biggest move", f"{big['token']} {big['ret']*100:+.1f}%",
                f"{abs(big['z']):.1f}σ", delta_color="off")
    k[3].metric("Headlines", f"{len(cv['headlines'])}")

    cL, cR = st.columns([3, 2])
    with cL:
        st.subheader("Who leaned which way")
        tf = cv["token_flow"]
        tf = tf[tf["safe"] & tf["buy_share"].notna()].sort_values("buy_share")
        if tf.empty:
            st.info("Not enough anonymous flow to show token skew today.")
        else:
            mvz = cv["moves"].set_index("token")
            dev = tf["buy_share"] - 0.5
            fig = go.Figure(go.Bar(
                y=tf["token"], x=dev * 100, orientation="h", base=0,
                marker_color=[theme.BUY if d >= 0 else theme.SELL for d in dev],
                text=[f"{b*100:.0f}% buy · {a:.1f}×" for b, a in
                      zip(tf["buy_share"], tf["activity_x"])],
                textposition="outside",
                customdata=[[mvz["ret"].get(t, float("nan")) * 100, n] for t, n in
                            zip(tf["token"], tf["n_peers"])],
                hovertemplate="%{y}: %{text} avg activity<br>price %{customdata[0]:+.1f}% · "
                              "%{customdata[1]} clients<extra></extra>"))
            theme.bar_ends(fig)
            lim = max(25, float(dev.abs().max()) * 100 + 30)
            fig.update_layout(height=max(220, 34 * len(tf)), showlegend=False,
                              xaxis=dict(range=[-lim, lim], zeroline=True,
                                         title="Buy share minus 50% (spot + perps)",
                                         ticksuffix="pp"))
            st.plotly_chart(fig, width="stretch")
            st.caption("Right = net buyers, left = net sellers. The label shows today's "
                       "activity as a multiple of the 20-day average.")
    with cR:
        st.subheader("Moves")
        mv = cv["moves"].head(8)
        st.dataframe(fmt(mv.rename(columns={"token": "Token", "close": "Close", "ret": "1d",
                                            "z": "σ-move"}),
                         {"Close": lambda v: f"{v:,.2f}" if v < 100 else f"{v:,.0f}",
                          "1d": lambda v: f"{v*100:+.1f}%", "σ-move": lambda v: f"{v:+.1f}σ"}),
                     width="stretch", hide_index=True)
        om = cv["options"]
        if not om.empty:
            st.subheader("Options flow")
            lv_, sv_ = _dc.vol_tilt(om[om["safe"]])
            shown = om[om["safe"]]
            st.markdown("  \n".join(
                f"**{r.strategy}** ({r.bias}): {r.n_today} today, {r.activity_x:.1f}× usual"
                for r in shown.itertuples()) or "_Too few clients per structure to show._")
            if not pd.isna(lv_):
                st.caption(f"{lv_*100:.0f}% long vol · {sv_*100:.0f}% short vol "
                           "(structures that pass the privacy check)")

    if cv["contrasts"]:
        st.subheader("Split views")
        for c_ in cv["contrasts"]:
            st.markdown(f":material/swap_horiz: {c_}")

    # ── The note ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader(":material/send: The 4:30pm note")
    n1, n2 = st.columns([1, 3])
    mode = n1.radio("Version", ["Desk-wide", f"For {parent}"], key="dc_mode",
                    help=f"‘For {parent}’ drops {parent}'s own trades from every number, "
                         "so we never send a client its own flow back as ‘the market’, and "
                         "puts its tokens first.")
    if mode == "Desk-wide":
        note_txt = _dc.note(cv)
    else:
        my_toks = _dc.client_tokens(data.get_trades(parent, (c_day - _dt.timedelta(days=90))
                                                    .isoformat(), c_day.isoformat()))
        cvx = ui.color_view(c_day, data.peer_id_for(parent))
        note_txt = _dc.note(cvx, focus=my_toks, title=f"FalconX Daily Color for {parent}")
    n1.caption("In prod a Databricks Job runs `daily_color.py` at 4:30pm ET and posts the "
               "note to the sales channel for a quick review before it goes out.")
    n2.code(note_txt, language=None)

    import llm as _llm
    pkey = f"dc_polish::{c_day}::{mode}"
    if _llm.available():
        if st.button("Polish with Opus", icon=":material/auto_awesome:", key="dc_polish_btn",
                     help="Rewrites the same facts into prose. It is given only the "
                          "aggregated, privacy-checked numbers, never client names."):
            with st.chat_message("assistant"):
                st.session_state[pkey] = st.write_stream(_llm.stream_text(
                    _dc.COLOR_SYSTEM,
                    [{"role": "user", "content": _dc.color_context(
                        cv if mode == "Desk-wide" else cvx)}],
                    max_tokens=4000, effort="medium"))
        elif pkey in st.session_state:
            st.markdown(st.session_state[pkey])

    # ── Internal: who to send which line to ──────────────────────────────────
    st.markdown("---")
    st.subheader(":material/target: Who to send it to (internal)")
    cover_c = prof["coverage"] if prof else ""
    reps_c = sorted({data.get_profile(p_)["coverage"] for p_ in data.search_parents()} - {""})
    rep = st.selectbox("Salesperson", reps_c,
                       index=reps_c.index(cover_c) if cover_c in reps_c else 0, key="dc_rep")
    book_c = {p_: data.get_profile(p_) for p_ in data.search_parents()}
    book_c = {p_: v_ for p_, v_ in book_c.items() if v_["coverage"] == rep}
    toks_c = {p_: _dc.client_tokens(data.get_trades(
        p_, (c_day - _dt.timedelta(days=90)).isoformat(), c_day.isoformat())) for p_ in book_c}
    sheet = _dc.rep_sheet(cv, book_c, toks_c)
    st.dataframe(sheet[["parent", "tokens", "hook", "n_matches"]].rename(columns={
        "parent": "Client", "tokens": "Trades most", "hook": "Open with",
        "n_matches": "Lines that match"}), width="stretch", hide_index=True)
    st.caption("The opener for each client is the top headline in a token they trade. "
               "Client names stay on this page and never go into the note.")

