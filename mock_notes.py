"""
Mock CRM call notes — what sales would have logged after meetings (the
Bloomberg "Pros" pattern), plus a local file so notes typed in the demo app
survive a restart.

Seeded notes are generated from each client's real (mock) profile — products,
tokens, favoured option structures, how they react to pitches — so the
hashtags and the idea-targeting engine find signal that is consistent with
the blotter, not random noise.

Prod: a Delta table (see data.get_notes / data.save_note). Engineering never
reads this file.
"""
from __future__ import annotations
import json
import os
import random
import uuid
from datetime import datetime, timedelta

import pandas as pd

import mock

LOCAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes_local.jsonl")

_STRUCT_WORDS = {
    "Long Straddle": "long straddles into events", "Short Strangle": "selling strangles",
    "Calendar": "calendar spreads on the term structure", "Iron Condor": "iron condors",
    "Risk Reversal": "risk reversals", "Bull Call Spread": "call spreads",
    "Long Call": "outright calls", "Bear Put Spread": "put spreads for downside protection",
    "Short Put": "selling puts to get long", "Long Butterfly": "butterflies around expiry",
}
_PRODUCT_LINES = {
    "Spot": ["Most spot flow still goes through RFQ in chat; {contact} likes the two-way pricing.",
             "Using our TWAP algo for {tok} spot, happy with execution so far."],
    "Derivatives": ["Running {tok} basis via perps, watching funding closely.",
                    "Perps desk: {contact} said our {tok} perp pricing was too wide vs the street last week."],
    "Credit": ["Credit facility is fine; may upsize the term loan next quarter.",
               "Asked about financing against {tok} collateral, wants indicative terms."],
    "FX": ["FX desk handling their USD/EUR fiat conversion, no issues."],
}
_NO_OPTIONS = ["Not set up for options on our side yet — onboarding docs pending.",
               "No interest in options right now, purely delta one."]
_GENERIC = [
    "Quarterly check-in. Follow up next week with the desk color deck.",
    "New PM joined the digital assets team; set up a call to intro the derivs desk.",
    "Asked about HYPE and SUI perps — interested in whether we will list them.",
    "Discussed cross-margin across spot and perps; keen on portfolio margin.",
    "They want better reporting / TCA on their algo fills.",
    "Raised a new fund, AUM up; expect volumes to ramp.",
    "Risk limits tight into month end, de-risking across the book.",
]
_REASON_LINES = {
    "Better price elsewhere": "Lost a couple of trades to other dealers on price — {contact} said we were wide.",
    "Level / entry": "{contact} likes our ideas but is level-sensitive; wants us to call at specific levels.",
    "Risk limits": "PM said risk budget is the constraint, not the ideas.",
    "Disagree with view": "Pushed back on our {tok} view, they see it the other way.",
    "Already positioned": "Already have most of our themes on through another book.",
    "Timing": "Wants to wait for FOMC / CPI before adding risk.",
    "Not set up for product": "Not set up for some products on the main entity yet.",
}
_AUTHORS_EXTRA = ["Leo Brandt"]   # options desk joins some calls


def _note_text(c: dict, r: random.Random, beh: dict) -> str:
    contact = c.get("contact", "the client")
    toks = [t for t in c["tokens"] if t not in ("USDC", "USDT")] or ["BTC"]
    tok = r.choice(toks)
    parts = []
    pref = c.get("opt_pref", [])
    if "Options" in c["products"] and pref and r.random() < 0.65:
        s = r.choice(pref)
        parts.append(r.choice([
            f"{contact} is active in {tok} vol — been {_STRUCT_WORDS[s]}.",
            f"Talked {tok} options: they like {_STRUCT_WORDS[s]}, asked for our vol surface.",
            f"Wants to look at {_STRUCT_WORDS[s]} on {tok} into the next expiry.",
        ]))
    elif "Options" not in c["products"] and r.random() < 0.35:
        parts.append(r.choice(_NO_OPTIONS))
    prod = r.choice([p for p in c["products"] if p in _PRODUCT_LINES])
    parts.append(r.choice(_PRODUCT_LINES[prod]).format(contact=contact, tok=tok))
    if r.random() < 0.45 and beh.get("reasons"):
        reason = r.choices(list(beh["reasons"]), weights=list(beh["reasons"].values()))[0]
        parts.append(_REASON_LINES[reason].format(contact=contact, tok=tok))
    if r.random() < 0.4:
        parts.append(r.choice(_GENERIC))
    return " ".join(parts)


_CACHE: dict = {}


def seeded() -> pd.DataFrame:
    """~1 year of logged notes across the whole book."""
    if "all" in _CACHE:
        return _CACHE["all"]
    import mock_chat
    import tags
    rows = []
    for parent, c in mock.CLIENTS.items():
        r = random.Random(f"notes|{parent}")
        beh = mock_chat._BEHAVIOUR.get(parent, {})
        for k in range(r.randint(8, 14)):
            d = datetime.combine(mock.TODAY, datetime.min.time()) - timedelta(
                days=r.randint(1, 360), hours=-r.randint(9, 18))
            text = _note_text(c, r, beh)
            author = c["coverage"] if r.random() > 0.2 else r.choice(_AUTHORS_EXTRA)
            rows.append({"note_id": f"N-{parent.split()[0][:3].upper()}-{k:03d}",
                         "parent": parent, "note_ts": d, "author": author,
                         "meeting_type": r.choice(["Call", "Call", "Meeting", "Chat", "Conference"]),
                         "text": text, "tags": tags.tag_list(text), "source": "seed"})
    df = pd.DataFrame(rows)
    _CACHE["all"] = df
    return df


def local() -> pd.DataFrame:
    """Notes typed into the demo app (persisted to notes_local.jsonl)."""
    if not os.path.exists(LOCAL_FILE):
        return pd.DataFrame(columns=seeded().columns)
    with open(LOCAL_FILE) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["note_ts"] = pd.to_datetime(df["note_ts"])
    return df


def notes(parent: str | None, start: str, end: str) -> pd.DataFrame:
    loc = local()
    df = pd.concat([seeded(), loc], ignore_index=True) if not loc.empty else seeded()
    if parent:
        df = df[df["parent"] == parent]
    t = pd.to_datetime(df["note_ts"])
    df = df[(t >= pd.Timestamp(start)) & (t < pd.Timestamp(end) + pd.Timedelta(days=1))]
    return df.sort_values("note_ts", ascending=False).reset_index(drop=True)


def save(row: dict) -> dict:
    row = {**row, "note_id": f"N-LOCAL-{uuid.uuid4().hex[:8]}", "source": "app"}
    with open(LOCAL_FILE, "a") as f:
        f.write(json.dumps({**row, "note_ts": pd.Timestamp(row["note_ts"]).isoformat()}) + "\n")
    return row
