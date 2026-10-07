"""
Idea targeting — "we have an ETH vol idea: who will bite?" — and learning
from how past pitches landed.

Inputs (all via data.py): the book blotter, every pitched idea + whether the
client executed it (pitches.match_executions), CRM notes (tags.py tags), chat
messages, profiles and current positions. Outputs:
  • rank          — every client scored for an idea, with the reasons in
                    plain English, the warnings, and how to pitch them
  • post_mortem   — a past desk idea: who we pitched, who took it, why the
                    others passed, whether the model would have picked the
                    takers, and who we should have pitched but didn't
  • evaluate      — time-split backtest: does the ranking beat "pitch the
                    clients who usually say yes"?
  • drivers       — what the model learned predicts a take, book-wide

The model is a small ridge logistic regression on ~12 features a salesperson
would recognise (trades the product? the token? done this structure? said so
in a note? asked about it in chat? takes our ideas?). It is fitted on the
book's own pitch history, and every training row is built POINT-IN-TIME —
only trades, notes and chat from before that pitch — so it can't learn from
the future. Every feature maps to a sentence, so each score explains itself.

No Streamlit, no Databricks. The optional LLM pitch drafter lives in app.py.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

import options
import tags

FLOW_DAYS = 180        # "trades it with us" lookback
NOTE_DAYS = 365        # notes stay relevant for a year
CHAT_DAYS = 90
PRIOR_N = 4            # pseudo-ideas shrinking each client's take-rate to the book's
RIDGE = 1.0
MIN_TRAIN = 30         # below this, fall back to a hand-weighted score

STRUCT_TAG = {"Iron Condor": "#IronCondor", "Iron Butterfly": "#Butterfly",
              "Condor": "#IronCondor", "Long Butterfly": "#Butterfly",
              "Short Butterfly": "#Butterfly", "Long Straddle": "#Straddle",
              "Short Straddle": "#Straddle", "Long Strangle": "#Strangle",
              "Short Strangle": "#Strangle", "Risk Reversal": "#RiskReversal",
              "Bull Call Spread": "#CallSpread", "Bear Call Spread": "#CallSpread",
              "Bull Put Spread": "#PutSpread", "Bear Put Spread": "#PutSpread",
              "Calendar Spread": "#Calendar", "Calendar": "#Calendar",
              "Covered Call": "#CoveredCall"}
VOL_KINDS = {"vol", "condor", "butterfly", "calendar"}
_PRODUCT_TAG = {"Spot": "#Spot", "Derivatives": "#Derivatives", "Options": "#Options"}

FEATURES = ["setup", "prod_active", "prod_share", "tok_share", "struct_exact",
            "struct_family", "notes_pos", "notes_neg", "chat_tok", "take_rate",
            "kind_rate", "quiet"]
LABELS = {"setup": "Set up for the product", "prod_active": "Trades the product with us",
          "prod_share": "Product share of their flow", "tok_share": "Token share of their flow",
          "struct_exact": "Has done this exact structure", "struct_family": "Trades this style of structure",
          "notes_pos": "Notes mention it", "notes_neg": "Notes say no to it",
          "chat_tok": "Asks about the token in chat", "take_rate": "Takes our ideas generally",
          "kind_rate": "Takes this kind of idea", "quiet": "Gone quiet on us"}
# Hand weights for the cold-start score (before there's pitch history to learn from).
_PRIOR_W = {"setup": 2.0, "prod_active": 1.0, "prod_share": 1.0, "tok_share": 1.5,
            "struct_exact": 0.8, "struct_family": 0.6, "notes_pos": 0.6, "notes_neg": -1.2,
            "chat_tok": 0.4, "take_rate": 1.5, "kind_rate": 0.8, "quiet": -0.6}

TIPS = {"Level / entry": "Level-sensitive. Pitch with a specific entry and offer to work an order.",
        "Better price elsewhere": "Price-shops. Lead with your tightest price and show size.",
        "Risk limits": "Often capped by risk limits. Ask about budget first; early in the month is better.",
        "Timing": "Times around macro events. Mention the catalyst and the date.",
        "Already positioned": "Often already has it on. Check positions before pitching.",
        "Disagree with view": "Pushes back on the thesis. Lead with data, not a view.",
        "Not set up for product": "Hits onboarding gaps. Confirm the entity can trade it.",
        "No response": "Often doesn't reply in chat. Call instead of messaging."}


# ── The idea ──────────────────────────────────────────────────────────────────
@dataclass
class Idea:
    token: str
    product: str = "Spot"                 # 'Spot' | 'Derivatives' | 'Options'
    direction: str = "Long"
    kind: str = "Directional"             # 'Directional' | 'Relative Value' | 'Options'
    structure: str | None = None
    token2: str | None = None
    thesis: str = ""
    theme: set = field(default_factory=set)   # extra tags, e.g. {'#Vol'}

    @property
    def tokens(self) -> list[str]:
        return [t for t in (self.token, self.token2) if t]

    @property
    def struct_key(self) -> str | None:
        return STRUCT_TAG.get(self.structure, self.structure) if self.structure else None

    @property
    def vol(self) -> bool:
        s = (self.structure or "")
        return "#Vol" in self.theme or any(w in s for w in ("Straddle", "Strangle", "Condor",
                                                             "Butterfly", "Calendar"))

    def tag_set(self) -> set[str]:
        """Note tags that count as 'they've talked about this'."""
        t = {f"#{x}" for x in self.tokens} | {_PRODUCT_TAG.get(self.product, "")} | set(self.theme)
        if self.struct_key and self.struct_key.startswith("#"):
            t.add(self.struct_key)
        if self.vol:
            t.add("#Vol")
        return t - {""}

    def label(self) -> str:
        if self.kind == "Relative Value":
            return f"{self.direction} {self.token}/{self.token2} RV"
        if self.structure:
            return f"{self.token} {self.structure}"
        return f"{self.direction} {self.token} {self.product.lower()}"


def idea_from_row(r) -> Idea:
    """A stored trade idea (data.get_trade_ideas row) → Idea."""
    th = {t for t in tags.tag_list(r.get("thesis") or "") if tags.category(t) == "Strategy"}
    return Idea(token=r["token"], product=r["product"], direction=r["direction"],
                kind=r["kind"], structure=r.get("structure") or None,
                token2=r.get("token2") or None, thesis=r.get("thesis") or "", theme=th)


_STRUCT_WORDS = sorted(STRUCT_TAG, key=len, reverse=True)


def idea_from_text(text: str, default_token: str = "BTC") -> Idea:
    """'ETH vol into the upgrade' → Idea(ETH, Options, theme={#Vol, #Event}).
    Deterministic: the same tagger as the notes, so idea and notes speak the same tags."""
    tl = [t for t in tags.tag_list(text) if not t.startswith("-")]
    toks = [t[1:] for t in tl if tags.category(t) == "Token" and t != "#Stables"]
    struct = next((s for s in _STRUCT_WORDS if re.search(rf"\b{re.escape(s)}s?\b", text, re.I)),
                  None)
    if not struct:
        st_tag = next((t for t in tl if tags.category(t) == "Structure"), None)
        struct = next((k for k, v in STRUCT_TAG.items() if v == st_tag), None)
    theme = {t for t in tl if tags.category(t) == "Strategy"}
    if struct or "#Options" in tl or "#Vol" in theme:
        product = "Options"
    elif "#Derivatives" in tl or theme & {"#Basis", "#Funding"}:
        product = "Derivatives"
    else:
        product = "Spot"
    rv = "#RelValue" in theme and len(toks) >= 2
    short = bool(re.search(r"\b(short|sell|bearish|fade)\b", text, re.I))
    return Idea(token=toks[0] if toks else default_token, token2=toks[1] if rv else None,
                product="Derivatives" if rv else product,
                direction="Short" if short and not struct else "Long",
                kind="Relative Value" if rv else "Options" if product == "Options" else "Directional",
                structure=struct, thesis=text, theme=theme)


# ── Context: everything indexed once ─────────────────────────────────────────
class Context:
    """Pre-indexed book data so per-(client, idea, date) features are cheap."""

    def __init__(self, book: pd.DataFrame, ideas: pd.DataFrame, executed: pd.DataFrame,
                 notes: pd.DataFrame, chat: pd.DataFrame, profiles: dict,
                 positions: dict | None = None):
        self.profiles = profiles
        self.parents = sorted(profiles)
        self.positions = positions or {}
        self.trades = {p: g.sort_values("trade_time") for p, g in
                       book.groupby("parent")} if not book.empty else {}
        self.structs = {}
        for p, g in self.trades.items():
            opt = g[g["product"] == "Options"]
            if opt.empty:
                continue
            s = options.summarize(opt.to_dict("records"))
            self.structs[p] = pd.DataFrame(
                [{"ts": pd.Timestamp(x["trade_time"]), "name": x["classification"]["name"],
                  "kind": x["classification"]["kind"]} for x in s])
        ex = ideas.merge(executed[["idea_id", "executed"]], on="idea_id", how="left") \
            if not ideas.empty else ideas.assign(executed=False)
        ex["executed"] = ex["executed"].fillna(False).astype(bool)
        self.ideas = ex.sort_values("ts").reset_index(drop=True)
        self.book_rate = float(self.ideas["executed"].mean()) if len(self.ideas) else 0.3
        self.by_parent_ideas = {p: g for p, g in self.ideas.groupby("parent")}
        self.notes = notes
        self.note_x = {}
        if notes is not None and not notes.empty:
            for p, g in notes.groupby("parent"):
                self.note_x[p] = g.sort_values("note_ts")
        cl = chat[chat["sender_role"] == "Client"] if chat is not None and not chat.empty \
            else pd.DataFrame(columns=["parent", "ts", "text"])
        self.chat = {p: g for p, g in cl.groupby("parent")}


# ── Features ──────────────────────────────────────────────────────────────────
def _window(df, col, t, days):
    if df is None or df.empty:
        return df
    t = pd.Timestamp(t)
    return df[(df[col] < t) & (df[col] >= t - timedelta(days=days))]


def features(ctx: Context, parent: str, idea: Idea, t, exclude_idea: str | None = None) -> dict:
    """Raw feature values + the evidence behind each (for the reasons)."""
    prof = ctx.profiles.get(parent) or {}
    t = pd.Timestamp(t)
    tr = ctx.trades.get(parent)
    recent = _window(tr, "trade_time", t, FLOW_DAYS)
    tot = float(recent["notional_usd"].sum()) if recent is not None and len(recent) else 0.0
    prod_n = float(recent.loc[recent["product"] == idea.product, "notional_usd"].sum()) if tot else 0
    tok_n = float(recent.loc[recent["token"].isin(idea.tokens), "notional_usd"].sum()) if tot else 0
    before = tr[tr["trade_time"] < t] if tr is not None else None
    quiet = (t - before["trade_time"].max()).days if before is not None and len(before) else 365

    st = ctx.structs.get(parent)
    st = st[st["ts"] < t] if st is not None else None
    n_exact = n_family = 0
    if st is not None and len(st) and idea.product == "Options":
        if idea.structure:
            n_exact = int((st["name"].map(lambda n: STRUCT_TAG.get(n, n)) == idea.struct_key).sum())
        fam = st["kind"].isin(VOL_KINDS) if idea.vol else ~st["kind"].isin(VOL_KINDS)
        n_family = int(fam.sum())

    want = idea.tag_set()
    # A note supports the idea only if it's about the idea, not just the token:
    # "TWAP for ETH spot" says nothing about an ETH straddle. Needs the token plus
    # the product/structure/theme, or a structure/theme tag on its own.
    tok_tags = {f"#{x}" for x in idea.tokens}
    prod_tags = set(_PRODUCT_TAG.values())
    pos_hits, neg_hits = [], []
    nx = _window(ctx.note_x.get(parent), "note_ts", t, NOTE_DAYS)
    if nx is not None and len(nx):
        for r in nx.itertuples():
            p_, n_ = tags.split(r.tags)
            hit = p_ & want
            if (hit & tok_tags and hit - tok_tags) or (hit - tok_tags - prod_tags):
                pos_hits.append((r.note_ts, r.text, sorted(hit)))
            # a "no" counts against the product/theme; a token "no" only for spot ideas
            if n_ & (want - tok_tags if idea.product != "Spot" else want):
                neg_hits.append((r.note_ts, r.text, sorted(n_ & want)))
    pos_hits.sort(key=lambda h: (len(h[2]), h[0]))     # best evidence last

    ch = _window(ctx.chat.get(parent), "ts", t, CHAT_DAYS)
    n_chat = 0
    if ch is not None and len(ch):
        pat = "|".join(rf"\b{re.escape(x)}\b" for x in idea.tokens)
        n_chat = int(ch["text"].str.contains(pat, case=False, regex=True).sum())

    hist = ctx.by_parent_ideas.get(parent)
    if hist is not None:
        hist = hist[hist["ts"] < t]
        if exclude_idea is not None:
            hist = hist[hist["idea_id"] != exclude_idea]
    k = int(hist["executed"].sum()) if hist is not None else 0
    n = int(len(hist)) if hist is not None else 0
    kind_h = hist[hist["kind"] == idea.kind] if hist is not None else None
    kk = int(kind_h["executed"].sum()) if kind_h is not None else 0
    kn = int(len(kind_h)) if kind_h is not None else 0
    br = ctx.book_rate
    passes = hist[hist["reason"].notna()]["reason"].value_counts() if hist is not None and n \
        else pd.Series(dtype=int)

    setup = idea.product in prof.get("products", []) or (
        idea.kind == "Relative Value" and "Derivatives" in prof.get("products", []))
    x = {"setup": float(setup),
         "prod_active": float(prod_n > 0),
         "prod_share": prod_n / tot if tot else 0.0,
         "tok_share": tok_n / tot if tot else 0.0,
         "struct_exact": float(np.log1p(n_exact)),
         "struct_family": float(np.log1p(n_family)),
         "notes_pos": float(np.log1p(len(pos_hits))),
         "notes_neg": float(len(neg_hits) > 0),
         "chat_tok": float(np.log1p(n_chat)),
         "take_rate": (k + PRIOR_N * br) / (n + PRIOR_N),
         "kind_rate": (kk + PRIOR_N * br) / (kn + PRIOR_N),
         "quiet": min(quiet, 180) / 30.0}
    ev = {"prod_share": prod_n / tot if tot else 0.0, "tok_share": tok_n / tot if tot else 0.0,
          "n_exact": n_exact, "n_family": n_family, "pos_hits": pos_hits, "neg_hits": neg_hits,
          "n_chat": n_chat, "k": k, "n": n, "kk": kk, "kn": kn, "quiet_days": quiet,
          "passes": passes, "setup": setup}
    return {"x": x, "ev": ev}


# ── Model ─────────────────────────────────────────────────────────────────────
@dataclass
class Model:
    coef: np.ndarray
    intercept: float
    mu: np.ndarray
    sd: np.ndarray
    n_train: int
    learned: bool

    def z(self, x: dict) -> np.ndarray:
        return (np.array([x[f] for f in FEATURES]) - self.mu) / self.sd

    def contrib(self, x: dict) -> dict:
        return dict(zip(FEATURES, self.coef * self.z(x)))

    def prob(self, x: dict) -> float:
        return float(1 / (1 + np.exp(-(self.intercept + self.coef @ self.z(x)))))


def training_rows(ctx: Context, before=None, exclude_desk: str | None = None) -> pd.DataFrame:
    """One point-in-time feature row per pitched idea (optionally only those before a date)."""
    rows = []
    for r in ctx.ideas.to_dict("records"):
        if before is not None and pd.Timestamp(r["ts"]) >= pd.Timestamp(before):
            continue
        if exclude_desk and r.get("desk_idea_id") == exclude_desk:
            continue
        f = features(ctx, r["parent"], idea_from_row(r), r["ts"], exclude_idea=r["idea_id"])
        rows.append({**f["x"], "y": int(r["executed"]), "idea_id": r["idea_id"],
                     "parent": r["parent"], "ts": r["ts"]})
    return pd.DataFrame(rows)


def fit(rows: pd.DataFrame) -> Model:
    """Sign-constrained ridge logistic regression. A feature whose learned weight
    contradicts common sense (e.g. 'has done this structure' counting AGAINST a
    client) is small-sample noise, so it's dropped and the model refitted —
    which keeps every explanation sensible."""
    if rows is None or len(rows) < MIN_TRAIN or rows["y"].nunique() < 2:
        mu, sd = np.zeros(len(FEATURES)), np.ones(len(FEATURES))
        return Model(np.array([_PRIOR_W[f] for f in FEATURES]), -2.5, mu, sd,
                     0 if rows is None else len(rows), False)
    sign = np.sign([_PRIOR_W[f] for f in FEATURES])
    keep = np.ones(len(FEATURES), bool)
    for _ in range(len(FEATURES)):
        m = _fit_logit(rows, keep)
        bad = keep & (np.sign(m.coef) != sign)
        if not bad.any():
            break
        keep &= ~bad
    return m


def _fit_logit(rows: pd.DataFrame, keep: np.ndarray) -> Model:
    """Ridge logistic regression by Newton's method (12 features — exact, instant)."""
    X = rows[FEATURES].to_numpy(float)
    y = rows["y"].to_numpy(float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd < 1e-9] = 1.0
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd * keep])
    w = np.zeros(Z.shape[1])
    pen = np.eye(Z.shape[1]) * RIDGE
    pen[0, 0] = 0
    for _ in range(50):
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (p - y) + pen @ w
        H = (Z.T * (p * (1 - p))) @ Z + pen
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    coef = np.where(keep, w[1:], 0.0)
    return Model(coef, float(w[0]), mu, sd, len(rows), True)


# ── Explanations ──────────────────────────────────────────────────────────────
def _snip(text: str, n: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def _sentence(f: str, idea: Idea, ev: dict, positive: bool) -> str | None:
    tok = "/".join(idea.tokens)
    if f in ("prod_active", "prod_share"):
        return (f"Trades {idea.product.lower()} with us ({ev['prod_share']*100:.0f}% of 6m flow)"
                if positive else f"No {idea.product.lower()} with us in 6 months")
    if f == "tok_share":
        return (f"{tok} is {ev['tok_share']*100:.0f}% of their flow" if positive and ev["tok_share"]
                else f"Doesn't trade {tok} with us" if not positive else None)
    if f == "struct_exact" and positive and ev["n_exact"]:
        return f"Has done {ev['n_exact']} {idea.structure}{'s' if ev['n_exact'] > 1 else ''} with us"
    if f == "struct_family":
        style = "vol structures (straddles, strangles, condors)" if idea.vol else \
            "directional options (calls, spreads, risk reversals)"
        return (f"Trades {style}: {ev['n_family']} with us" if positive and ev["n_family"]
                else f"Never traded {style} with us" if not positive and idea.product == "Options"
                else None)
    if f == "notes_pos" and positive and ev["pos_hits"]:
        ts, text, hit = ev["pos_hits"][-1]
        return f"Note {pd.Timestamp(ts):%d %b}: “{_snip(text)}” ({' '.join(hit)})"
    if f == "notes_neg" and ev["neg_hits"]:
        ts, text, hit = ev["neg_hits"][-1]
        return f"Note {pd.Timestamp(ts):%d %b} says no: “{_snip(text)}”"
    if f == "chat_tok" and positive and ev["n_chat"]:
        return f"Asked about {tok} in chat {ev['n_chat']}× (last 90d)"
    if f == "take_rate" and ev["n"]:
        return f"Took {ev['k']} of {ev['n']} ideas we pitched ({ev['k']/ev['n']*100:.0f}%)"
    if f == "kind_rate" and ev["kn"]:
        return f"Took {ev['kk']} of {ev['kn']} {idea.kind.lower()} ideas"
    if f == "quiet" and not positive and ev["quiet_days"] >= 30:
        return f"No trades with us in {ev['quiet_days']} days"
    return None


def explain(model: Model, idea: Idea, f: dict, top: int = 3) -> tuple[list, list]:
    c = model.contrib(f["x"])
    reasons, warns = [], []
    for k, v in sorted(c.items(), key=lambda kv: -kv[1]):
        if v > 0.05 and len(reasons) < top:
            s = _sentence(k, idea, f["ev"], True)
            if s and s not in reasons:
                reasons.append(s)
    for k, v in sorted(c.items(), key=lambda kv: kv[1]):
        if v < -0.15 and len(warns) < 2:
            s = _sentence(k, idea, f["ev"], False)
            if s and s not in warns:
                warns.append(s)
    return reasons, warns


def _positioned(positions: pd.DataFrame | None, idea: Idea) -> str | None:
    if positions is None or positions.empty or idea.kind == "Relative Value":
        return None
    p = positions[(positions["token"] == idea.token) & (positions["product"] != "Options")]
    same = p[p["direction"] == idea.direction]
    if idea.product == "Options" or same.empty:
        return None
    big = same.loc[same["notional_usd"].idxmax()]
    return f"Already {idea.direction.lower()} {big['instrument']} (${big['notional_usd']/1e6:.1f}M) with us"


# ── Ranking ───────────────────────────────────────────────────────────────────
def rank(ctx: Context, model: Model, idea: Idea, today) -> pd.DataFrame:
    """Every client for one idea, best first. Not-set-up clients are listed but
    flagged 'Onboard first' rather than ranked as prospects."""
    t = pd.Timestamp(today) + timedelta(days=1)          # include everything up to today
    rows = []
    for p in ctx.parents:
        f = features(ctx, p, idea, t)
        prof = ctx.profiles.get(p) or {}
        reasons, warns = explain(model, idea, f)
        pos = _positioned(ctx.positions.get(p), idea)
        if pos:
            warns.insert(0, pos)
        passes = f["ev"]["passes"]
        if f["ev"]["setup"]:                       # an onboarding gap elsewhere isn't a pitch tip
            passes = passes.drop("Not set up for product", errors="ignore")
        tip = TIPS.get(passes.index[0]) if len(passes) else None
        rows.append({"parent": p, "coverage": prof.get("coverage", ""),
                     "contact": prof.get("contact", ""),
                     "segment": prof.get("sub_segment") or prof.get("segment", ""),
                     "score": model.prob(f["x"]),
                     "status": "Prospect" if f["ev"]["setup"] else "Onboard first",
                     "reasons": reasons, "warnings": warns, "tip": tip,
                     "record": f"{f['ev']['k']}/{f['ev']['n']}" if f["ev"]["n"] else "—",
                     "top_pass": passes.index[0] if len(passes) else None,
                     **{f"x_{k}": v for k, v in f["x"].items()}})
    df = pd.DataFrame(rows)
    df["_o"] = (df["status"] != "Prospect").astype(int)
    df = df.sort_values(["_o", "score"], ascending=[True, False]).drop(columns="_o")
    df["rank"] = range(1, len(df) + 1)
    pro = df["status"] == "Prospect"                     # tiers are relative to real prospects
    df["tier"] = "—"
    df.loc[pro, "tier"] = pd.cut(df.loc[pro, "score"].rank(pct=True), [0, 0.5, 0.8, 1.0],
                                 labels=["Low", "Medium", "High"]).astype(str)
    return df.reset_index(drop=True)


# ── Outcome learning ──────────────────────────────────────────────────────────
def desk_ideas(ctx: Context) -> pd.DataFrame:
    """Past desk-wide ideas with pitched / taken counts, newest first."""
    d = ctx.ideas[ctx.ideas["desk_idea_id"].notna()]
    if d.empty:
        return pd.DataFrame(columns=["desk_idea_id", "label", "ts", "pitched", "taken"])
    g = d.groupby("desk_idea_id").agg(ts=("ts", "min"), pitched=("idea_id", "count"),
                                     taken=("executed", "sum")).reset_index()
    first = d.sort_values("ts").groupby("desk_idea_id").first()
    g["label"] = g["desk_idea_id"].map(lambda i: idea_from_row(first.loc[i]).label())
    g["thesis"] = g["desk_idea_id"].map(first["thesis"])
    return g.sort_values("ts", ascending=False).reset_index(drop=True)


def post_mortem(ctx: Context, desk_idea_id: str) -> dict:
    """'We pitched 7, 2 took it — why?' Model refitted on what was known BEFORE
    the pitch, so 'would the model have picked them' is an honest question."""
    d = ctx.ideas[ctx.ideas["desk_idea_id"] == desk_idea_id].sort_values("ts")
    if d.empty:
        return {}
    first = d.iloc[0]
    idea = idea_from_row(first)
    t0 = pd.Timestamp(first["ts"])
    model = fit(training_rows(ctx, before=t0, exclude_desk=desk_idea_id))
    pitched = set(d["parent"])
    sc = rank(ctx, model, idea, t0 - timedelta(days=1))
    sc = sc.merge(d[["parent", "reaction", "reason", "executed", "pitched_by"]],
                  on="parent", how="left")
    sc["pitched"] = sc["parent"].isin(pitched)
    sc["executed"] = sc["executed"].astype("boolean").fillna(False).astype(bool)
    pt = sc[sc["pitched"]]
    takers, passers = pt[pt["executed"]], pt[~pt["executed"]]

    def share(df, col, cond):
        return f"{int(cond(df[col]).sum())}/{len(df)}" if len(df) else "—"
    contrast = []
    for col, lab, cond in [("x_tok_share", f"trade {'/'.join(idea.tokens)} with us", lambda s: s > 0),
                           ("x_prod_active", f"trade {idea.product.lower()} with us", lambda s: s > 0),
                           ("x_notes_pos", "had a note about it", lambda s: s > 0),
                           ("x_struct_family", "trade this style of structure", lambda s: s > 0)]:
        if col == "x_struct_family" and idea.product != "Options":
            continue
        contrast.append({"signal": lab, "takers": share(takers, col, cond),
                         "passed": share(passers, col, cond)})
    reasons = passers["reason"].fillna(passers["reaction"]).value_counts()
    # where the model would have put the takers among those pitched
    pt_rank = pt.sort_values("score", ascending=False).reset_index(drop=True)
    half = max(1, len(pt_rank) // 2)
    caught = int(pt_rank.head(half)["executed"].sum())
    missed_aud = sc[~sc["pitched"] & (sc["status"] == "Prospect")].head(3)
    return {"idea": idea, "ts": t0, "table": sc, "pitched": len(pt), "taken": len(takers),
            "contrast": pd.DataFrame(contrast), "pass_reasons": reasons,
            "top_half_caught": caught, "top_half_n": half, "model_learned": model.learned,
            "n_train": model.n_train, "missed_audience": missed_aud,
            "lessons": lessons(idea, takers, passers, reasons, caught, half, missed_aud)}


def lessons(idea, takers, passers, reasons, caught, half, missed) -> list[str]:
    out = []
    n = len(takers) + len(passers)
    out.append(f"Pitched to {n}, {len(takers)} took it ({len(takers)/n*100:.0f}%)." if n else
               "Not pitched to anyone.")
    if len(takers):
        out.append(f"The model's top {half} would have caught {caught} of {len(takers)} takers.")
    if len(reasons):
        top, cnt = reasons.index[0], int(reasons.iloc[0])
        out.append(f"Most common reason for not doing it: **{top}** ({cnt} of {len(passers)}). "
                   + (TIPS.get(top, "")))
    tk = takers["x_tok_share"].gt(0).mean() if len(takers) else None
    ps = passers["x_tok_share"].gt(0).mean() if len(passers) else None
    if tk is not None and ps is not None and tk - ps >= 0.3:
        out.append(f"Takers already traded {'/'.join(idea.tokens)} with us "
                   f"({tk*100:.0f}% vs {ps*100:.0f}% of the rest). Pitch it where they already trade the token.")
    if len(missed):
        out.append("Not pitched but scored high: " + ", ".join(missed["parent"]) + ".")
    return out


def evaluate(ctx: Context, split: float = 0.7) -> dict:
    """Time split: fit on the first 70% of pitches, score the rest. Compare to
    the obvious baseline (rank by the client's own take-rate)."""
    rows = training_rows(ctx)
    if len(rows) < MIN_TRAIN + 10:
        return {}
    rows = rows.sort_values("ts").reset_index(drop=True)
    cut = int(len(rows) * split)
    tr, te = rows.iloc[:cut], rows.iloc[cut:]
    m = fit(tr)
    te = te.assign(p=[m.prob(r) for r in te[FEATURES].to_dict("records")])
    k = max(1, len(te) // 3)
    return {"n_train": len(tr), "n_test": len(te), "base_rate": float(te["y"].mean()),
            "auc_model": _auc(te["y"], te["p"]), "auc_baseline": _auc(te["y"], te["take_rate"]),
            "top_third_model": float(te.nlargest(k, "p")["y"].mean()),
            "top_third_baseline": float(te.nlargest(k, "take_rate")["y"].mean()),
            "cutoff": te["ts"].min()}


def _auc(y, s) -> float:
    y, s = np.asarray(y), np.asarray(s, float)
    pos, neg = s[y == 1], s[y == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


def drivers(model: Model) -> pd.DataFrame:
    """Learned weight per feature (per 1 s.d.), in plain English, biggest first."""
    d = pd.DataFrame({"feature": FEATURES, "label": [LABELS[f] for f in FEATURES],
                      "weight": model.coef})
    return d.reindex(d["weight"].abs().sort_values(ascending=False).index).reset_index(drop=True)


def build(ctx: Context, today) -> dict:
    model = fit(training_rows(ctx))
    return {"model": model, "drivers": drivers(model), "eval": evaluate(ctx),
            "desk_ideas": desk_ideas(ctx)}


# ── LLM pitch drafts (context only — the call happens in app.py via llm.py) ───
DRAFT_SYSTEM = (
    "You are a FalconX institutional sales trader. Write short, specific Telegram "
    "pitch messages for a trade idea, one per client, using ONLY the facts given "
    "(their history with us, notes, how they like to be pitched). Two or three "
    "sentences each, no hype, no promises of returns, no made-up numbers or "
    "levels. Never mention other clients. Format: '**Client** — message'.")


def draft_context(idea: Idea, ranked: pd.DataFrame, n: int = 5) -> str:
    lines = [f"IDEA: {idea.label()} — {idea.thesis or 'no thesis given'}", ""]
    for r in ranked[ranked["status"] == "Prospect"].head(n).itertuples():
        lines.append(f"CLIENT: {r.parent} — address the message to {r.contact or 'the client'}"
                     f" (sent by {r.coverage}, their FalconX coverage)")
        lines += [f"  + {x}" for x in r.reasons] + [f"  - {x}" for x in r.warnings]
        if r.tip:
            lines.append(f"  how to pitch: {r.tip}")
    return "\n".join(lines)


def load(data, today) -> Context:
    """Assemble the Context from data.py seams only (one call per client; in prod
    these become single book-wide queries)."""
    import pitches
    s, e = "2000-01-01", pd.Timestamp(today).date().isoformat()
    parents = data.search_parents()
    profiles = {p: data.get_profile(p) for p in parents}
    book = data.get_book_trades(s, e)
    ideas, execd, chat = [], [], []
    for p in parents:
        i = data.get_trade_ideas(p, s, e)
        if not i.empty:
            ideas.append(i)
            execd.append(pitches.match_executions(i, book[book["parent"] == p]))
        m = data.get_chat_messages(p, s, e)
        if not m.empty:
            chat.append(m.assign(parent=p))
    cat = lambda fr: pd.concat([f.dropna(axis=1, how="all") for f in fr],  # noqa: E731
                               ignore_index=True) if fr else pd.DataFrame()
    return Context(book, cat(ideas), cat(execd), data.get_notes(None, s, e), cat(chat),
                   profiles, {p: data.get_positions(p) for p in parents})
