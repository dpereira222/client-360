"""
Chat → trade ideas, with Opus 5.5 as the extractor.

This is the "bot in the Telegram channel" brain. The bot itself only archives
messages (see WHITEPAPER §13); this module reads the archived chat and pulls out:
  • every trade idea FalconX pitched (token, direction, level, target, stop,
    size, horizon, structure legs), and
  • the client's reaction to it (done / passed + why / later / no response).

Output matches data.get_trade_ideas() so the pitch tracker can't tell whether a
row came from the nightly job or a live re-extract in the app.

Uses structured outputs (output_config.format with a Pydantic-derived schema),
then validates with Pydantic — no regex over model prose.

⚠️ Same compliance caveat as llm.py: the direct Anthropic API is for the mock /
demo only. Production runs this inside Databricks model serving.
"""
from __future__ import annotations
import json
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

import llm

CHUNK = 160          # messages per call; pitches + reactions rarely span further
OVERLAP = 40         # context either side, so a pitch near a boundary keeps its reaction


class Leg(BaseModel):
    right: Literal["C", "P"]
    strike: float
    side: Literal["Buy", "Sell"]
    qty_mult: int = Field(description="Relative contract ratio, e.g. 1 or 2")
    expiry: str = Field(description="ISO date YYYY-MM-DD")


class Idea(BaseModel):
    msg_id: int = Field(description="msg_id of the FalconX message that pitched the idea")
    pitched_by: str
    kind: Literal["Directional", "Relative Value", "Options"]
    token: str
    token2: Optional[str] = Field(None, description="Short leg token for relative value")
    direction: Literal["Long", "Short"]
    product: Literal["Spot", "Derivatives", "Options"]
    structure: Optional[str] = Field(None, description="Options only, e.g. 'Risk Reversal'")
    legs: list[Leg] = Field(default_factory=list)
    entry_px: float = Field(description="Level quoted in the pitch (ratio for RV, spot for options)")
    target_px: Optional[float] = None
    stop_px: Optional[float] = None
    size_usd: float = Field(description="Size offered, USD notional")
    horizon_days: int = Field(description="Stated horizon; 30 if none stated")
    thesis: str = Field(description="One-line reason given, client-facing words")
    reaction: Literal["Done", "Passed", "Later", "No response"]
    reason: Optional[Literal["Already positioned", "Level / entry", "Risk limits",
                             "Better price elsewhere", "Disagree with view", "Timing",
                             "Not set up for product"]] = None
    reaction_msg_id: Optional[int] = None
    callback_level: Optional[float] = Field(
        None, description="If the client named a level to be called back at")


class Extraction(BaseModel):
    ideas: list[Idea]


SYSTEM = (
    "You extract trade ideas from an institutional crypto sales chat between FalconX "
    "staff (sender_role=FalconX) and one client (sender_role=Client).\n"
    "A trade idea is a FalconX message proposing a specific trade the client could do "
    "with us — a direction on a token, a relative-value pair, or an options structure. "
    "Market color, flow commentary, scheduling, and two-way price quotes in response to "
    "a client request (RFQs like '140 / 141') are NOT trade ideas.\n"
    "For each idea, find the client's reaction: a reply (reply_to_msg_id) or a nearby "
    "client message clearly about it. 'Done' = they agreed to trade; 'Passed' = declined "
    "(map the reason to the closest allowed value); 'Later' = deferred without deciding; "
    "'No response' = nothing. Only extract what the chat says — never infer a fill.\n"
    "Desk slang: 'done away' / 'traded away' / 'street is showing better' = Better price "
    "elsewhere; 'mine' / 'yours' / 'print it' = Done; 'ping me at X' = Level / entry with "
    "callback_level X. Replies can arrive hours later with other chatter in between — "
    "use reply_to when present."
)


# Structured outputs (output_config.format) are enforced by the Anthropic API and
# Databricks model serving, but NOT by every proxy — the FalconX dev AI gateway
# passes the schema through unenforced and the model may wrap JSON in ``` fences.
# So the schema is also stated in the prompt, and parse() is tolerant + validating.
from anthropic import transform_schema as _ts  # noqa: E402

_SCHEMA = _ts(Extraction.model_json_schema())
_SCHEMA_HINT = ("\n\nRespond with ONLY a JSON object matching this JSON Schema "
                "(no prose, no code fences):\n" + json.dumps(Extraction.model_json_schema()))


def parse(text: str) -> Extraction:
    """Validate model output against the schema. Accepts bare JSON or JSON
    wrapped in prose / ``` fences. Raises ValueError if nothing validates."""
    from pydantic import ValidationError
    t = text.strip()
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in response")
    try:
        return Extraction.model_validate_json(t[a:b + 1])
    except ValidationError as e:
        raise ValueError(str(e).splitlines()[0]) from e


def _render(msgs: pd.DataFrame) -> str:
    lines = []
    for r in msgs.itertuples():
        rt = f" reply_to={int(r.reply_to_msg_id)}" if r.reply_to_msg_id == r.reply_to_msg_id \
            and r.reply_to_msg_id is not None else ""
        lines.append(f"[{r.msg_id}] {pd.Timestamp(r.ts):%Y-%m-%d %H:%M} "
                     f"{r.sender} ({r.sender_role}){rt}: {r.text}")
    return "\n".join(lines)


def _chunks(msgs: pd.DataFrame):
    i = 0
    while i < len(msgs):
        yield (msgs.iloc[max(0, i - OVERLAP): i + CHUNK + OVERLAP],
               set(msgs.iloc[i: i + CHUNK]["msg_id"]))
        i += CHUNK


def extract(msgs: pd.DataFrame, parent: str, on_progress=None) -> tuple[pd.DataFrame, dict]:
    """Run Opus over the chat. Returns (ideas DataFrame in get_trade_ideas schema,
    usage dict). Each call sees CHUNK messages plus OVERLAP of context on both
    sides; an idea is kept only from the chunk that owns its pitch msg_id, so
    nothing is double-counted."""
    msgs = msgs.sort_values("ts").reset_index(drop=True)
    client = llm._client()
    found: dict[int, Idea] = {}
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    chunks = list(_chunks(msgs))
    for n, (chunk, owned) in enumerate(chunks):
        if on_progress:
            on_progress(n, len(chunks))
        content = f"Chat: FalconX <> {parent}\n\n{_render(chunk)}"
        parsed = None
        for attempt in range(2):
            # Streamed: thinking + a chunk's worth of JSON can run long.
            with client.beta.messages.stream(
                model=llm.MODEL,
                max_tokens=32000,
                system=SYSTEM + _SCHEMA_HINT,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "high",
                               "format": {"type": "json_schema", "schema": _SCHEMA}},
                betas=[llm.FALLBACK_BETA], fallbacks="default",
            ) as stream:
                resp = stream.get_final_message()
            usage["calls"] += 1
            usage["input_tokens"] += resp.usage.input_tokens
            usage["output_tokens"] += resp.usage.output_tokens
            if resp.stop_reason == "refusal":
                break
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                parsed = parse(text)
                break
            except ValueError as e:
                content = (f"{content}\n\nYour previous answer did not validate ({e}). "
                           "Reply with ONLY the JSON object.")
        if parsed is None:
            usage["failed_chunks"] = usage.get("failed_chunks", 0) + 1
            continue
        for idea in parsed.ideas:
            if idea.msg_id in owned:
                found[idea.msg_id] = idea
    if on_progress:
        on_progress(len(chunks), len(chunks))
    return _to_frame(found.values(), msgs, parent), usage


def _to_frame(ideas, msgs: pd.DataFrame, parent: str) -> pd.DataFrame:
    by_id = msgs.set_index("msg_id")
    rows = []
    for k, i in enumerate(sorted(ideas, key=lambda x: x.msg_id)):
        m = by_id.loc[i.msg_id]
        rm = by_id.loc[i.reaction_msg_id] if i.reaction_msg_id in by_id.index else None
        rows.append({
            "idea_id": f"AI-{parent.split()[0][:3].upper()}-{k:03d}", "parent": parent,
            "chat_id": m["chat_id"], "ts": m["ts"], "pitched_by": i.pitched_by,
            "kind": i.kind, "token": i.token.upper(),
            "token2": i.token2.upper() if i.token2 else None,
            "direction": i.direction,
            "product": "Derivatives" if i.kind == "Relative Value" else i.product,
            "structure": i.structure,
            "legs_json": json.dumps([l.model_dump() for l in i.legs]) if i.legs else None,
            "entry_px": i.entry_px, "target_px": i.target_px, "stop_px": i.stop_px,
            "size_usd": i.size_usd, "horizon_days": i.horizon_days, "thesis": i.thesis,
            "desk_idea_id": None, "msg_id": i.msg_id,
            "reaction": i.reaction, "reason": i.reason,
            "reaction_ts": rm["ts"] if rm is not None else None,
            "reaction_text": rm["text"] if rm is not None else None,
            "callback_level": i.callback_level,
        })
    return pd.DataFrame(rows)


def compare(extracted: pd.DataFrame, stored: pd.DataFrame) -> dict:
    """How well a live extraction agrees with the stored ideas (keyed on the
    pitching msg_id). Used for the app's 'audit the extractor' panel and tests."""
    if stored is None or stored.empty:
        return {}
    e = extracted.set_index("msg_id") if not extracted.empty else pd.DataFrame()
    s = stored.set_index("msg_id")
    both = s.index.intersection(e.index) if not e.empty else []
    out = {"stored": len(s), "extracted": len(e), "matched": len(both),
           "missed": len(s) - len(both), "extra": len(e) - len(both)}
    if len(both):
        j = s.loc[both].join(e.loc[both], rsuffix="_ai")
        out["field_accuracy"] = {
            f: float((j[f] == j[f + "_ai"]).mean())
            for f in ("kind", "token", "direction", "reaction")}
        out["reason_accuracy"] = float(
            (j["reason"].fillna("—") == j["reason_ai"].fillna("—")).mean())
        out["entry_within_1pct"] = float(
            ((j["entry_px_ai"] / j["entry_px"] - 1).abs() < 0.01).mean())
    return out
