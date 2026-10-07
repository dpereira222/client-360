"""
Auto-hashtags for call notes — the Bloomberg "Pros" pattern.

A salesperson writes free text after a meeting ("James is using our TWAP for
ETH, wants to look at a condor into Sept expiry, pricing on perps too wide").
extract() turns it into tags (#ETH #TWAP #IronCondor #Options #Vol
#PricingFeedback), deterministically, so every tag can be traced back to the
words that produced it. Over time the tags are the queryable layer: "who has
talked about vol", "who complained about pricing", "who uses the API".

Stance: a tag preceded by a negation ("not set up for options", "no interest
in SOL") is kept but marked stance=-1. "Not interested in options" is a
signal too, and idea targeting must not read it as interest.

TAXONOMY is plain data. Add a row to add a tag; nothing else changes. In prod
it can move to a Databricks table and be edited by sales ops.

No Streamlit, no Databricks, no LLM.
"""
from __future__ import annotations
import re

# tag → (category, [phrases]). Phrases are case-insensitive, matched on word
# boundaries; regex is allowed. Order inside a category doesn't matter.
TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # ── Tokens ────────────────────────────────────────────────────────────────
    "#BTC":  ("Token", ["btc", "bitcoin", "xbt"]),
    "#ETH":  ("Token", ["eth", "ether", "ethereum"]),
    "#SOL":  ("Token", ["sol", "solana"]),
    "#XRP":  ("Token", ["xrp", "ripple"]),
    "#AVAX": ("Token", ["avax", "avalanche"]),
    "#LINK": ("Token", ["link", "chainlink"]),
    "#ARB":  ("Token", ["arb", "arbitrum"]),
    "#OP":   ("Token", ["op", "optimism"]),
    "#HYPE": ("Token", ["hype", "hyperliquid"]),
    "#SUI":  ("Token", ["sui"]),
    "#TAO":  ("Token", ["tao", "bittensor"]),
    "#ENA":  ("Token", ["ena", "ethena"]),
    "#Stables": ("Token", ["usdc", "usdt", "stablecoins?", "tether"]),
    # ── Products ──────────────────────────────────────────────────────────────
    "#Spot":        ("Product", ["spot"]),
    "#Derivatives": ("Product", ["perps?", "perpetuals?", "futures", "derivs?", "derivatives",
                                 "swaps?", "nds?f?s?"]),
    # Bare "call" is ambiguous in sales notes ("set up a call"), so calls/puts
    # only count in a trading context: "buy calls", "25d puts", "call options".
    "#Options":     ("Product", ["options?", "(?:call|put) options?", "puts",
                                 r"(?:buy|sell|bought|sold|long|short|buying|selling|outright|otm|itm|atm|\d+d?)"
                                 r" (?:\w+ )?(?:calls?|puts?)",
                                 "vol surface", "skew"]),
    "#Credit":      ("Product", ["credit", "loans?", "lending", "borrow(?:ing)?", "financing",
                                 "repo", "margin loan", "term loan"]),
    "#FX":          ("Product", ["fx", "fiat", "usd/eur", "eur", "jpy", "sgd"]),
    # ── Options structures (names match options.py classifications) ───────────
    "#IronCondor":    ("Structure", ["iron condors?", "condors?"]),
    "#Butterfly":     ("Structure", ["butterfl(?:y|ies)", "flys?"]),
    "#Straddle":      ("Structure", ["straddles?"]),
    "#Strangle":      ("Structure", ["strangles?"]),
    "#RiskReversal":  ("Structure", ["risk reversals?", "rr"]),
    "#CallSpread":    ("Structure", ["call spreads?", "bull call", "bear call"]),
    "#PutSpread":     ("Structure", ["put spreads?", "bear put", "bull put"]),
    "#CoveredCall":   ("Structure", ["covered calls?", "overwrit(?:e|ing)", "call overwrite"]),
    "#Calendar":      ("Structure", ["calendars?", "calendar spreads?"]),
    "#Collar":        ("Structure", ["collars?"]),
    # ── Strategy / theme ──────────────────────────────────────────────────────
    "#Vol":       ("Strategy", ["vol", "volatility", "implied", "realized", "gamma", "vega"]),
    "#Basis":     ("Strategy", ["basis", "cash and carry", "cash-and-carry", "carry trade"]),
    "#Funding":   ("Strategy", ["funding", "funding rates?"]),
    "#Yield":     ("Strategy", ["yield", "staking", "income", "lst"]),
    "#Hedging":   ("Strategy", ["hedg(?:e|es|ing)", "protection", "downside"]),
    "#RelValue":  ("Strategy", ["rel(?:ative)? val(?:ue)?", "pairs?", "rv", "vs btc", "vs eth"]),
    "#Directional": ("Strategy", ["directional", "outright"]),
    "#Treasury":  ("Strategy", ["treasury", "balance sheet"]),
    "#Event":     ("Strategy", ["etf", "unlocks?", "upgrade", "fomc", "cpi", "halving"]),
    # ── FalconX platform / functions (the "which functions they use" bit) ─────
    "#RFQ":        ("Platform", ["rfq", "request for quote", "two-?way", "2-?way"]),
    "#Algo":       ("Platform", ["algos?", "twap", "vwap", "pov", "iceberg"]),
    "#API":        ("Platform", ["api", "fix", "websockets?"]),
    "#Electronic": ("Platform", ["electronic", "streaming prices?", "click to trade", "gui"]),
    "#PrimeBrokerage": ("Platform", ["prime", "prime brokerage", "pb"]),
    "#CrossMargin":    ("Platform", ["cross[- ]margin", "portfolio margin", "margin"]),
    "#Settlement":     ("Platform", ["settlement", "settle", "net settlement", "t\\+\\d"]),
    "#Custody":        ("Platform", ["custody", "custodian", "tri-?party", "off-?exchange"]),
    "#Reporting":      ("Platform", ["reporting", "statements?", "dashboards?", "tca"]),
    # ── Relationship signals ──────────────────────────────────────────────────
    "#PricingFeedback": ("Signal", ["too wide", "wide", "pricing", "tighter", "better px",
                                    "better price", "spreads? (?:are|were) wide"]),
    "#Competitor":   ("Signal", ["traded away", "done away", "elsewhere", "other dealers?",
                                 "the street", "competitor", "coinbase", "cumberland",
                                 "wintermute", "b2c2", "galaxy"]),
    "#RiskLimits":   ("Signal", ["risk limits?", "risk budget", "gross limit", "var limit",
                                 "drawdown", "de-?risk(?:ing)?"]),
    "#Onboarding":   ("Signal", ["onboard(?:ing)?", "kyc", "set up for", "new entity",
                                 "isda", "legal docs?", "cosa"]),
    "#PMChange":     ("Signal", ["new pm", "new cio", "hired", "joined", "left the fund",
                                 "leaving", "reorg"]),
    "#NewMandate":   ("Signal", ["new fund", "new mandate", "launching", "raised",
                                 "allocation", "aum"]),
    "#Interest":     ("Signal", ["interested in", "wants? to", "looking at", "asked about",
                                 "keen on", "would like", "exploring"]),
    "#FollowUp":     ("Signal", ["follow up", "follow-up", "send (?:over|them|him|her)",
                                 "circle back", "next week", "set up a call"]),
}

CATEGORIES = ["Token", "Product", "Structure", "Strategy", "Platform", "Signal"]

# A tag implied by another (a condor is an options trade with a vol view).
IMPLIES = {
    "#IronCondor": ["#Options", "#Vol"], "#Butterfly": ["#Options", "#Vol"],
    "#Straddle": ["#Options", "#Vol"], "#Strangle": ["#Options", "#Vol"],
    "#Calendar": ["#Options", "#Vol"], "#RiskReversal": ["#Options"],
    "#CallSpread": ["#Options"], "#PutSpread": ["#Options"],
    "#CoveredCall": ["#Options", "#Yield"], "#Collar": ["#Options", "#Hedging"],
    "#Basis": ["#Derivatives"], "#Funding": ["#Derivatives"],
}

# Tags that only make sense as a positive signal get no stance flip
# ("not happy, pricing too wide" is still #PricingFeedback).
_NO_STANCE = {"Signal"}

_NEG = re.compile(r"\b(?:not|no|never|don'?t|doesn'?t|didn'?t|won'?t|can'?t|cannot|"
                  r"isn'?t|aren'?t|without|zero|stopped|pass(?:ed)? on|no longer)\b", re.I)
_NEG_WINDOW = 5   # words before a match that a negation can reach
# A clause break ends a negation: "no interest in SOL, but keen on ETH".
_CLAUSE = re.compile(r"[.;!?]|\bbut\b|\bhowever\b|\bwhereas\b", re.I)


def _compile():
    out = []
    for tag, (cat, phrases) in TAXONOMY.items():
        pat = r"(?<![\w#$])(?:" + "|".join(phrases) + r")(?![\w])"
        out.append((tag, cat, re.compile(pat, re.I)))
    return out


_PATTERNS = _compile()


def _negated(text: str, start: int) -> bool:
    before = text[:start]
    cut = max((m.end() for m in _CLAUSE.finditer(before)), default=0)
    words = before[cut:].split()[-_NEG_WINDOW:]
    return bool(_NEG.search(" ".join(words)))


def extract(text: str) -> list[dict]:
    """Tags found in a note, one row per tag:
        tag str, category str, stance int (+1 / -1), evidence str (the phrase),
        implied bool (added via IMPLIES, not written in the note).
    A tag mentioned both positively and negatively keeps the positive stance
    ("no BTC, but ETH and BTC options yes" → #BTC +1)."""
    if not isinstance(text, str) or not text.strip():
        return []
    found: dict[str, dict] = {}
    for tag, cat, pat in _PATTERNS:
        for m in pat.finditer(text):
            stance = -1 if cat not in _NO_STANCE and _negated(text, m.start()) else 1
            cur = found.get(tag)
            if cur is None or stance > cur["stance"]:
                found[tag] = {"tag": tag, "category": cat, "stance": stance,
                              "evidence": m.group(0), "implied": False}
    for tag, row in list(found.items()):
        if row["stance"] < 0:
            continue
        for imp in IMPLIES.get(tag, []):
            if imp not in found:
                found[imp] = {"tag": imp, "category": TAXONOMY[imp][0], "stance": 1,
                              "evidence": row["evidence"], "implied": True}
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(found.values(), key=lambda r: (order[r["category"]], r["tag"]))


def tag_list(text: str) -> list[str]:
    """Just the tag strings, negatives prefixed with '-' (e.g. '-#Options')."""
    return [("-" if r["stance"] < 0 else "") + r["tag"] for r in extract(text)]


def split(tags: list[str]) -> tuple[set[str], set[str]]:
    """Stored tag list → (positive tags, negative tags)."""
    pos = {t for t in tags if not t.startswith("-")}
    neg = {t[1:] for t in tags if t.startswith("-")}
    return pos, neg


def category(tag: str) -> str:
    return TAXONOMY.get(tag.lstrip("-"), ("Other", []))[0]
