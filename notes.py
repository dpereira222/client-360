"""
CRM notes — turns logged call notes into client knowledge (the "Pros" pattern).

Inputs (all via data.py): get_notes (text + tags stored at write time by
tags.py) and the blotter. Outputs:
  • profile        — what we know about a client, tag by tag, with the latest
                     note that said it (every claim traces back to a note)
  • notes_vs_book  — what the notes say vs what the blotter shows: talked
                     about options but never traded them = cross-sell; said
                     "not set up for options" = onboarding task, not a pitch
  • follow_ups     — recent notes asking for a follow-up
  • who_mentioned  — book-wide: "who has talked about #Vol + #ETH?"

No Streamlit, no Databricks, no LLM.
"""
from __future__ import annotations
from datetime import timedelta

import pandas as pd

import tags

FOLLOW_UP_DAYS = 21      # a #FollowUp older than this is stale, not open
_PRODUCT_TAGS = {"#Spot": "Spot", "#Derivatives": "Derivatives", "#Options": "Options",
                 "#Credit": "Credit", "#FX": "FX"}


def explode(notes: pd.DataFrame) -> pd.DataFrame:
    """One row per (note, tag): note_id, parent, note_ts, author, tag, category, stance."""
    cols = ["note_id", "parent", "note_ts", "author", "tag", "category", "stance"]
    if notes is None or notes.empty:
        return pd.DataFrame(columns=cols)
    x = notes[["note_id", "parent", "note_ts", "author", "tags"]].explode("tags").dropna(
        subset=["tags"])
    if x.empty:
        return pd.DataFrame(columns=cols)
    x["stance"] = x["tags"].map(lambda t: -1 if t.startswith("-") else 1)
    x["tag"] = x["tags"].str.lstrip("-")
    x["category"] = x["tag"].map(tags.category)
    return x[cols].reset_index(drop=True)


def profile(notes: pd.DataFrame) -> pd.DataFrame:
    """Per tag: mentions, net stance, first/last seen, the latest note's text.
    stance = the most recent mention's stance (people change their minds:
    'not set up for options' in March, 'trading condors' in June → +1)."""
    x = explode(notes)
    if x.empty:
        return pd.DataFrame(columns=["tag", "category", "mentions", "stance", "first_seen",
                                     "last_seen", "last_note_id", "last_text"])
    x = x.sort_values("note_ts")
    text = notes.set_index("note_id")["text"]
    g = x.groupby("tag").agg(category=("category", "first"), mentions=("note_id", "nunique"),
                             stance=("stance", "last"), first_seen=("note_ts", "min"),
                             last_seen=("note_ts", "max"),
                             last_note_id=("note_id", "last")).reset_index()
    g["last_text"] = g["last_note_id"].map(text)
    order = {c: i for i, c in enumerate(tags.CATEGORIES)}
    g["_o"] = g["category"].map(lambda c: order.get(c, 99))
    return g.sort_values(["_o", "mentions"], ascending=[True, False]).drop(columns="_o") \
        .reset_index(drop=True)


def notes_vs_book(notes: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Products and tokens: what the notes say vs whether they trade it with us.
    verdict: 'Cross-sell' (positive in notes, not traded), 'Onboarding'
    (negative / not set up, not traded), 'Stale note' (negative in notes but
    they trade it now), 'Consistent'."""
    p = profile(notes)
    p = p[p["category"].isin(["Product", "Token"])]
    if p.empty:
        return pd.DataFrame()
    traded_p = set(trades["product"]) if trades is not None and not trades.empty else set()
    traded_t = set(trades["token"]) if trades is not None and not trades.empty else set()
    rows = []
    for _, r in p.iterrows():
        if r["category"] == "Product":
            name = _PRODUCT_TAGS.get(r["tag"])
            trades_it = name in traded_p
        else:
            name = r["tag"].lstrip("#")
            trades_it = name in traded_t or (name == "Stables" and bool(
                traded_t & {"USDC", "USDT"}))
        if r["stance"] > 0 and not trades_it:
            verdict = "Cross-sell"
        elif r["stance"] < 0 and not trades_it:
            verdict = "Onboarding" if r["category"] == "Product" else "Not interested"
        elif r["stance"] < 0 and trades_it:
            verdict = "Stale note"
        else:
            verdict = "Consistent"
        rows.append({"tag": r["tag"], "category": r["category"], "notes_say":
                     "interested" if r["stance"] > 0 else "not interested / not set up",
                     "mentions": r["mentions"], "trades_with_us": trades_it,
                     "verdict": verdict, "last_seen": r["last_seen"], "evidence": r["last_text"]})
    out = pd.DataFrame(rows)
    rank = {"Cross-sell": 0, "Onboarding": 1, "Stale note": 2, "Not interested": 3,
            "Consistent": 4}
    return out.sort_values(["verdict", "mentions"], key=lambda s: s.map(rank)
                           if s.name == "verdict" else -s).reset_index(drop=True)


def follow_ups(notes: pd.DataFrame, today) -> pd.DataFrame:
    """Notes tagged #FollowUp in the last FOLLOW_UP_DAYS, newest first."""
    if notes is None or notes.empty:
        return pd.DataFrame()
    cut = pd.Timestamp(today) - timedelta(days=FOLLOW_UP_DAYS)
    m = notes["tags"].map(lambda ts: "#FollowUp" in ts) & (pd.to_datetime(notes["note_ts"]) >= cut)
    f = notes[m].copy()
    f["age_days"] = (pd.Timestamp(today) - pd.to_datetime(f["note_ts"])).dt.days
    return f.sort_values("note_ts", ascending=False).reset_index(drop=True)


def filter_by_tags(notes: pd.DataFrame, want: list[str], mode: str = "all") -> pd.DataFrame:
    """Notes containing the wanted tags positively. mode 'all' = every tag, 'any' = one."""
    if notes is None or notes.empty or not want:
        return notes
    want = set(want)

    def hit(ts):
        pos, _ = tags.split(ts)
        return want <= pos if mode == "all" else bool(want & pos)
    return notes[notes["tags"].map(hit)]


def who_mentioned(book_notes: pd.DataFrame, want: list[str], mode: str = "all") -> pd.DataFrame:
    """Book-wide: clients whose notes carry the tags (positively), ranked by
    mentions then recency. Clients who said the opposite are listed separately
    via the 'against' column so nobody pitches vol to a client who said no vol."""
    if book_notes is None or book_notes.empty or not want:
        return pd.DataFrame()
    hits = filter_by_tags(book_notes, want, mode)
    neg = book_notes[book_notes["tags"].map(lambda ts: bool(set(want) & tags.split(ts)[1]))]
    rows = []
    for parent in sorted(set(hits["parent"]) | set(neg["parent"])):
        h = hits[hits["parent"] == parent]
        n = neg[neg["parent"] == parent]
        last = h.sort_values("note_ts").iloc[-1] if not h.empty else None
        rows.append({"parent": parent, "mentions": len(h), "against": len(n),
                     "last_seen": last["note_ts"] if last is not None else None,
                     "last_author": last["author"] if last is not None else None,
                     "last_text": last["text"] if last is not None else n.iloc[0]["text"]})
    out = pd.DataFrame(rows)
    out["_t"] = pd.to_datetime(out["last_seen"])
    return out.sort_values(["mentions", "_t"], ascending=[False, False]) \
        .drop(columns="_t").reset_index(drop=True)


def build(parent: str, client_notes: pd.DataFrame, trades: pd.DataFrame, today) -> dict:
    """Everything the Notes tab draws for one client."""
    return {"notes": client_notes, "profile": profile(client_notes),
            "vs_book": notes_vs_book(client_notes, trades),
            "follow_ups": follow_ups(client_notes, today),
            "authors": sorted(client_notes["author"].unique()) if not client_notes.empty else []}
