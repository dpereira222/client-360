"""
Opus 5.5 analyst — activity summaries and live chat over a client's Client 360.

Two entry points, both streaming generators (use with st.write_stream):
  • summarize(ctx)                -> streamed activity summary for the period
  • chat(ctx, history, question)  -> streamed answer, grounded in the same context

The context is built once from the data already on the page (build_context), so the
model never needs its own Databricks access — it reasons over what the UI computed.

Key handling: reads ANTHROPIC_API_KEY from the environment; for local runs it also
falls back to the repo .env. In the Databricks App, set it as an app secret/env var.
"""
from __future__ import annotations
import os

MODEL = "claude-opus-5-5"

# Opus 5.5: thinking is always on and counts against max_tokens, and effort
# defaults to "medium" — so both are set explicitly on every call.
# Fallbacks: if a safety classifier declines, the API re-runs the request on
# Anthropic's recommended model for that refusal category (server-side).
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def stream_text(system: str, messages: list[dict], max_tokens: int = 16000,
                effort: str = "medium"):
    """Stream reply text. Shared by every Opus call in the app."""
    with _client().beta.messages.stream(
        model=MODEL, max_tokens=max_tokens, system=system, messages=messages,
        output_config={"effort": effort},
        betas=[FALLBACK_BETA], fallbacks="default",
    ) as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n\n_The model declined this request._"
    elif final.stop_reason == "max_tokens":
        yield "\n\n_…cut off at the length limit._"


# ── API key ───────────────────────────────────────────────────────────────────
def _load_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key
    for path in (".env", "../.env", "/home/ubuntu/market-color/.env"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ANTHROPIC_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def available() -> bool:
    return _load_key() is not None


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=_load_key())


# ── Context builder ───────────────────────────────────────────────────────────
def _usd(x) -> str:
    x = float(x); a = abs(x)
    if a >= 1e9: return f"${x/1e9:.2f}B"
    if a >= 1e6: return f"${x/1e6:.2f}M"
    if a >= 1e3: return f"${x/1e3:.1f}K"
    return f"${x:,.0f}"


def build_context(parent, window_label, kpi, activity, token_flow, structures,
                  credit, positions, desk_cmp, gong, peer=None, pitch=None, notes_=None,
                  expiring=None, color=None) -> str:
    """Assemble a compact, information-dense brief for the model.

    The CRM inputs are optional and are the same dicts the tabs already draw from:
    peer = peers.build(), pitch = pitches.build(), notes_ = notes.build(),
    expiring = this client's rows of expiries.live(), color = daily_color.color_context()
    text built with the client's own flow excluded. Only aggregates and short
    excerpts go in — peers stay anonymous (cohort label + ratios only)."""
    L = [f"# FalconX Client 360 context: {parent}", f"Period: {window_label}", ""]

    L.append("## Cross-product summary")
    L.append(f"- Notional traded: {_usd(kpi['notional'])} over {kpi['trade_count']:,} trades")
    L.append(f"- Revenue to FalconX: {_usd(kpi['revenue'])} at {kpi['blended_bps']:.1f} bps blended")
    L.append(f"- Active products: {', '.join(kpi['products_active']) or 'none'}")
    L.append(f"- Open positions: {_usd(kpi['net_position'])} notional, "
             f"unrealized P&L {_usd(kpi['unrealized_pnl'])}")
    if kpi["credit_outstanding"]:
        L.append(f"- Credit drawn: {_usd(kpi['credit_outstanding'])}")

    if activity is not None and not activity.empty:
        L.append("\n## Revenue & volume by product")
        for _, r in activity.iterrows():
            L.append(f"- {r['product']}: {_usd(r['notional_usd'])} notional, "
                     f"{_usd(r['revenue_usd'])} revenue, {int(r['trades'])} trades, "
                     f"{r['avg_bps']:.1f} bps")

    if token_flow is not None and not token_flow.empty:
        L.append("\n## Net token flow (buy + / sell -)")
        for _, r in token_flow.head(8).iterrows():
            L.append(f"- {r['token']}: net {_usd(r['net_usd'])} on {_usd(r['total_usd'])} total")

    if structures:
        L.append(f"\n## Options structures traded ({len(structures)})")
        from collections import Counter
        by_name = Counter(s["classification"]["name"] for s in structures)
        for name, cnt in by_name.most_common():
            L.append(f"- {name}: {cnt}")
        L.append("Most recent structures:")
        for s in structures[:6]:
            c = s["classification"]
            L.append(f"  • {c['name']} on {s['token']} ({c['bias']}, net {c['net']}), "
                     f"{s['n_legs']} legs, {_usd(s['notional_usd'])}, "
                     f"{str(s['trade_time'])[:10]}")

    if positions is not None and not positions.empty:
        L.append("\n## Open positions")
        for _, r in positions.iterrows():
            extra = ""
            if r.get("delta") == r.get("delta") and r["product"] == "Options":
                extra = f", delta {r['delta']}"
            L.append(f"- {r['product']} {r['instrument']} {r['direction']} "
                     f"{_usd(r['notional_usd'])}, uPnL {_usd(r['unrealized_pnl'])}{extra}")

    if credit is not None and not credit.empty:
        L.append("\n## Credit facilities")
        for _, r in credit.iterrows():
            L.append(f"- {r['deal_id']} {r['facility_type']}: drawn {_usd(r['drawn_usd'])}, "
                     f"LTV {r['ltv_pct']}%, {r['rate_bps']/100:.2f}%, matures {r['maturity']} "
                     f"({r['days_to_maturity']}d), status {r['status']}")

    if desk_cmp is not None and not desk_cmp.empty:
        L.append("\n## Client vs desk positioning")
        for _, r in desk_cmp.iterrows():
            L.append(f"- {r['token']}: client {r['client_dir']} / desk {r['desk_dir']} "
                     f"→ {r['signal']}")

    if gong is not None and not gong.empty:
        L.append("\n## Recent calls")
        for _, r in gong.head(4).iterrows():
            L.append(f"- {r['call_date']} {r['title']}: {r['brief']} "
                     f"Next: {r['next_steps']}")
    _crm_sections(L, peer, pitch, notes_, expiring)
    if color:
        L.append("\n## Today's desk color (anonymized, this client's own flow excluded)")
        L.append(color)
    return "\n".join(L)


def _crm_sections(L, peer, pitch, notes_, expiring) -> None:
    if peer and not peer.get("empty", True):
        L.append(f"\n## Peers (anonymous cohort: {peer['n']} {peer['label']})")
        for p in peer.get("points", [])[:6]:
            L.append(f"- [{p['kind']}] {p['say']}")

    if pitch:
        sc = pitch.get("score") or {}
        if sc.get("pitched"):
            L.append("\n## Pitch track record (ideas we sent them)")
            L.append(f"- {sc['pitched']} pitched, {sc['executed']} taken "
                     f"({sc['take_rate']*100:.0f}%); {sc['hit_rate']*100:.0f}% of closed ideas "
                     f"made money; client P&L on ideas taken {_usd(sc['client_pnl'])}, "
                     f"P&L left on ideas passed {_usd(sc['missed_pnl'])}; "
                     f"{sc['passed_then_traded']} 'no reply, then traded'")
            for p in pitch.get("points", [])[:4]:
                L.append(f"- [{p['kind']}] {p['say']}")
        ch = pitch.get("chat") or {}
        if ch.get("messages"):
            L.append(f"- Chat: {ch['messages']} messages (~{ch['per_week']:.0f}/wk), client "
                     f"writes {ch['client_share']*100:.0f}%, median reply "
                     f"{ch['median_reply_min']:.0f} min, last client msg "
                     f"{str(ch['last_client_msg'])[:10]}")
        it = pitch.get("interests")
        if it is not None and not it.empty:
            lat = it[~it["trades_with_us"]].head(4)
            if not lat.empty:
                L.append("- Asked about in chat but not trading with us: " + ", ".join(
                    f"{r.token} ({r.mentions}×)" for r in lat.itertuples()))

    if notes_ and notes_.get("notes") is not None and not notes_["notes"].empty:
        n = notes_["notes"]
        L.append(f"\n## Call notes ({len(n)} on file)")
        prof = notes_.get("profile")
        if prof is not None and not prof.empty:
            L.append("- What we know: " + " ".join(
                (t if st >= 0 else f"NOT {t}") for t, st in
                prof.head(12)[["tag", "stance"]].itertuples(index=False)))
        vb = notes_.get("vs_book")
        if vb is not None and not vb.empty:
            for r in vb[vb["verdict"] != "Consistent"].head(4).itertuples():
                L.append(f"- {r.verdict}: {r.tag} — notes say {r.notes_say}, "
                         f"{'trades' if r.trades_with_us else 'does not trade'} it with us")
        for r in n.sort_values("note_ts", ascending=False).head(3).itertuples():
            L.append(f"- {str(r.note_ts)[:10]} {r.author}: {r.text[:220]}")
        fu = notes_.get("follow_ups")
        if fu is not None and not fu.empty:
            L.append(f"- Open follow-ups: {len(fu)}")

    if expiring is not None and not expiring.empty:
        L.append(f"\n## Live option structures ({len(expiring)} total; nearest "
                 f"{min(6, len(expiring))} by expiry)")
        for r in expiring.sort_values("dte").head(6).itertuples():
            L.append(f"- {r.expiry} ({r.dte}d) {r.token} {r.structure} {_usd(r.notional_usd)}: "
                     f"{r.status}{' — PIN RISK' if r.pin else ''}; {r.talking_point}")


SYSTEM = (
    "You are a senior FalconX sales/coverage analyst. You are given a structured "
    "brief of one institutional client's cross-product activity (spot, derivatives, "
    "options, credit, FX) over a period. Be concrete, quantitative, and useful for a "
    "salesperson prepping a meeting. Cite the numbers from the brief. Flag risks "
    "(margin/LTV, concentration, contrarian positioning, pin risk into expiry) and "
    "cross-sell openings. Peer data is anonymous: never guess which firms are in a "
    "cohort. "
    "Never invent data not in the brief. Keep it tight."
)


# ── Streaming entry points ────────────────────────────────────────────────────
def summarize(ctx: str):
    """Yield a streamed activity summary for the period."""
    prompt = (
        "Write a briefing on this client's activity for the period. Structure it:\n"
        "**Headline** (2-3 sentences), **What they traded** (products, notable options "
        "structures and what they imply directionally), **Positioning & risk**, "
        "**Vs the desk & peers**, **Relationship** (pitch record, chat, what notes say), "
        "**Cross-sell / talking points** for the next call.\n\n"
        f"{ctx}"
    )
    yield from stream_text(SYSTEM, [{"role": "user", "content": prompt}], effort="high")


def chat(ctx: str, history: list[dict], question: str):
    """Yield a streamed answer to a live question, grounded in the context."""
    msgs = [{"role": "user", "content":
             f"Here is the client brief you will answer questions about:\n\n{ctx}\n\n"
             "Acknowledge you have it in one short line."},
            {"role": "assistant", "content":
             "Got it — I have the full cross-product brief. Ask away."}]
    for m in history:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": question})
    yield from stream_text(SYSTEM, msgs)
