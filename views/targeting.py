"""
Desk page — Idea targeting. Who will bite? Ranked from the book’s own pitch history.
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import data
import theme
import ui
from ui import fmt

# ── Sidebar + masthead ────────────────────────────────────────────────────────
parent = ui.client_picker()
prof = data.get_profile(parent)
ui.sidebar_footer("Book-wide — the selected client is highlighted in the ranking.")
ui.masthead("Idea targeting", "Who will bite? Ranked from the book’s own pitch history")

# ══════════════════════════════════════════════════════════════════════════════
# TARGETING — "who will bite?" + learning from past pitches
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_resource(show_spinner="Learning from the book's pitch history…")
def _targeting():
    import targeting
    ctx = targeting.load(data, data.TODAY)
    return ctx, targeting.build(ctx, data.TODAY)


_TIER_PILL = {"High": "good", "Medium": "warning", "Low": "serious"}
_PRODUCTS_T = ["Spot", "Derivatives", "Options"]
_STRUCTS_T = ["—", "Long Call", "Long Put", "Bull Call Spread", "Bear Put Spread", "Risk Reversal",
              "Long Straddle", "Short Strangle", "Iron Condor", "Long Butterfly", "Calendar Spread"]

with st.container():
    import targeting as _tg

    tctx, tv = _targeting()
    st.caption("Who should we pitch, and why? Scores come from the book's own history: "
               "which clients took which ideas, based on what we knew *before* each pitch "
               "(blotter, notes, chat). Each score lists its reasons.")

    # ── 1. Rank the book for an idea ─────────────────────────────────────────
    st.subheader("Who will bite?")
    q = st.text_input("Describe the idea", value="ETH vol into the upgrade — long straddle",
                      key="tgt_q", help="Free text, parsed with the same tagger as the notes. "
                                        "Adjust anything it gets wrong below.")
    parsed = _tg.idea_from_text(q)
    toks = sorted(set(tctx.ideas["token"].dropna()) | {parsed.token})
    c1, c2, c3, c4 = st.columns(4)
    tok = c1.selectbox("Token", toks, index=toks.index(parsed.token), key=f"tgt_tok::{q}")
    prod = c2.selectbox("Product", _PRODUCTS_T, index=_PRODUCTS_T.index(parsed.product)
                        if parsed.product in _PRODUCTS_T else 0, key=f"tgt_prod::{q}")
    dirn = c3.selectbox("Direction", ["Long", "Short"],
                        index=0 if parsed.direction == "Long" else 1, key=f"tgt_dir::{q}")
    s_opts = _STRUCTS_T if parsed.structure in _STRUCTS_T or not parsed.structure \
        else _STRUCTS_T + [parsed.structure]
    struct = c4.selectbox("Structure", s_opts, index=s_opts.index(parsed.structure or "—"),
                          key=f"tgt_str::{q}", disabled=prod != "Options")
    idea = _tg.Idea(token=tok, product=prod, direction=dirn,
                    kind="Options" if prod == "Options" else parsed.kind,
                    structure=struct if prod == "Options" and struct != "—" else None,
                    token2=parsed.token2, thesis=q, theme=parsed.theme)
    ranked = _tg.rank(tctx, tv["model"], idea, data.TODAY)

    f1, f2 = st.columns([2, 1])
    reps_t = sorted(set(ranked["coverage"]) - {""})
    who_t = f1.multiselect("Coverage", reps_t, key="tgt_cov",
                           help="Only show clients covered by these salespeople")
    show_onb = f2.toggle("Show not-set-up clients", value=False, key="tgt_onb")
    rv = ranked[ranked["coverage"].isin(who_t)] if who_t else ranked
    if not show_onb:
        rv = rv[rv["status"] == "Prospect"]

    pros = ranked[ranked["status"] == "Prospect"]
    k = st.columns(4)
    k[0].metric("Idea", idea.label())
    k[1].metric("Prospects", f"{len(pros)}", help=f"{len(ranked) - len(pros)} not set up for "
                                                  f"{idea.product.lower()}")
    k[2].metric("High-fit", f"{int((pros['tier'] == 'High').sum())}")
    me = ranked[ranked["parent"] == parent]
    if not me.empty:
        k[3].metric(f"{parent} ranks", f"#{int(me['rank'].iloc[0])} of {len(ranked)}",
                    me["tier"].iloc[0], delta_color="off")

    for r in rv.head(10).itertuples():
        with st.container(border=True):
            h1, h2 = st.columns([4, 1])
            pill_ = theme.pill(r.tier.upper(), _TIER_PILL[r.tier]) if r.tier in _TIER_PILL \
                else theme.pill("ONBOARD FIRST", "serious")
            h1.markdown(f"**#{r.rank} {r.parent}** &nbsp; {pill_} &nbsp; "
                        f"<span style='color:{theme.INK_SECONDARY}'>{r.segment} · "
                        f"{r.coverage} · took {r.record} of our ideas</span>",
                        unsafe_allow_html=True)
            h2.markdown(f"<div style='text-align:right;font-size:1.3rem'><b>{r.score*100:.0f}%</b>"
                        f"<div style='color:{theme.INK_MUTED};font-size:.75rem'>chance they take it"
                        f"</div></div>", unsafe_allow_html=True)
            lines = ([f":green[:material/check_circle:] {x}" for x in r.reasons]
                     + [f":orange[:material/warning:] {x}" for x in r.warnings])
            if r.tip:
                lines.append(f":blue[:material/lightbulb:] *{r.tip}*")
            st.markdown("  \n".join(lines) if lines else "_No strong signal either way._")
    if len(rv) > 10:
        with st.expander(f"All {len(rv)} clients"):
            st.dataframe(fmt(rv[["rank", "parent", "coverage", "tier", "score", "record",
                                 "status"]].rename(columns={
                                     "rank": "#", "parent": "Client", "coverage": "Coverage",
                                     "tier": "Fit", "score": "P(take)", "record": "Took",
                                     "status": "Status"}),
                             {"P(take)": lambda v: f"{v*100:.0f}%"}),
                         width="stretch", hide_index=True)

    # Pitch drafts (Opus)
    import llm as _llm
    dkey = f"tgt_draft::{idea.label()}::{','.join(who_t)}"
    if _llm.available():
        if st.button("Draft pitch messages for the top 5", icon=":material/auto_awesome:", key="tgt_draft_btn"):
            with st.chat_message("assistant"):
                st.session_state[dkey] = st.write_stream(_llm.stream_text(
                    _tg.DRAFT_SYSTEM,
                    [{"role": "user", "content": _tg.draft_context(idea, rv)}],
                    max_tokens=4000, effort="medium"))
        elif dkey in st.session_state:
            st.markdown(st.session_state[dkey])
    else:
        st.caption("Set `ANTHROPIC_API_KEY` to have Opus draft a pitch for each client.")

    # ── 2. Post-mortem on past desk ideas ────────────────────────────────────
    st.markdown("---")
    st.subheader("How did past ideas land?")
    di = tv["desk_ideas"]
    if di.empty:
        st.info("No desk-wide ideas in the history yet.")
    else:
        lbl = {r.desk_idea_id: f"{r.ts:%d %b} · {r.label} — {r.taken}/{r.pitched} took it"
               for r in di.itertuples()}
        pick = st.selectbox("Desk idea", list(lbl), format_func=lbl.get, key="tgt_pm")
        pm = _tg.post_mortem(tctx, pick)
        st.caption(f"Thesis: *{di.set_index('desk_idea_id').loc[pick, 'thesis']}*. "
                   f"Model refitted on the {pm['n_train']} pitches before {pm['ts']:%d %b %Y}, "
                   "so this is what it would have said at the time.")
        for line in pm["lessons"]:
            st.markdown(f"- {line}")
        cA, cB = st.columns([3, 2])
        with cA:
            t = pm["table"]
            t = t[t["pitched"] | (t["status"] == "Prospect")].copy()
            t["Outcome"] = t.apply(lambda r: "Took it" if r["executed"] else
                                   (r["reason"] or r["reaction"]) if r["pitched"] else
                                   "Not pitched", axis=1)
            t = t.sort_values("score", ascending=False)
            fig = go.Figure()
            for lab, color in [("Took it", theme.BUY),
                               ("Passed / no reply", theme.SELL),
                               ("Not pitched", theme.AXIS)]:
                sub = t[t["Outcome"].eq("Took it")] if lab == "Took it" else \
                    t[t["pitched"] & ~t["executed"]] if lab.startswith("Passed") else \
                    t[~t["pitched"]]
                if sub.empty:
                    continue
                fig.add_bar(name=lab, x=sub["score"] * 100, y=sub["parent"], orientation="h",
                            marker_color=color, customdata=sub["Outcome"],
                            hovertemplate="%{y}: %{x:.0f}% · %{customdata}<extra></extra>")
            theme.bar_ends(fig)
            fig.update_layout(height=max(260, 24 * len(t)), barmode="overlay",
                              yaxis=dict(categoryorder="array",
                                         categoryarray=list(t["parent"])[::-1]),
                              xaxis=dict(title="Model score at the time (%)"),
                              legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, width="stretch")
        with cB:
            st.markdown("**Takers vs the rest**")
            if not pm["contrast"].empty:
                st.dataframe(pm["contrast"].rename(columns={
                    "signal": "Already…", "takers": "Took it", "passed": "Didn't"}),
                    width="stretch", hide_index=True)
            if len(pm["pass_reasons"]):
                st.markdown("**Why the rest didn't**")
                st.dataframe(pm["pass_reasons"].rename_axis("Reason").reset_index(name="Clients"),
                             width="stretch", hide_index=True)

    # ── 3. Model card ────────────────────────────────────────────────────────
    with st.expander("How the model scores (and whether it works)"):
        ev_ = tv["eval"]
        m_ = tv["model"]
        if ev_:
            e = st.columns(4)
            e[0].metric("Trained on", f"{m_.n_train} pitches")
            e[1].metric("Ranking quality (AUC)", f"{ev_['auc_model']:.2f}",
                        f"{ev_['auc_model'] - ev_['auc_baseline']:+.2f} vs take-rate only",
                        help="Chance a random taker scores above a random passer. 0.5 = coin flip. "
                             f"Tested on the {ev_['n_test']} pitches after "
                             f"{ev_['cutoff']:%d %b}, trained only on earlier ones.")
            e[2].metric("Top-third take rate", f"{ev_['top_third_model']*100:.0f}%",
                        f"{(ev_['top_third_model'] - ev_['base_rate'])*100:+.0f}pt vs all",
                        help=f"Average across held-out pitches: {ev_['base_rate']*100:.0f}%")
            e[3].metric("Baseline top third", f"{ev_['top_third_baseline']*100:.0f}%",
                        help="Ranking by each client's past take-rate alone")
        dr = tv["drivers"]
        dr = dr[dr["weight"] != 0].sort_values("weight")
        fig = go.Figure(go.Bar(
            x=dr["weight"], y=dr["label"], orientation="h",
            marker_color=[theme.BUY if w > 0 else theme.SELL for w in dr["weight"]],
            hovertemplate="%{y}: %{x:+.2f}<extra></extra>"))
        theme.bar_ends(fig)
        fig.update_layout(height=max(220, 26 * len(dr)), showlegend=False,
                          xaxis=dict(title="Effect on the odds of taking it (per s.d.)",
                                     zeroline=True))
        st.plotly_chart(fig, width="stretch")
        st.caption("Each weight is constrained to point the common-sense way. A feature "
                   "that comes out backwards is treated as noise and dropped. It's a small "
                   "model on a small sample, so use it to rank, and check the reasons.")

