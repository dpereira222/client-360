"""
FalconX Client 360 — theme & plotly template.

Colors are taken from the validated data-viz palette (CVD-safe, checked with the
palette validator). Product hues use the fixed categorical order — assigned by
product identity, NEVER cycled by rank. Buy/sell uses the diverging blue<->red
pair so "buy" is not implied to be "good".

Nothing in this file talks to Databricks. It is pure presentation.
"""
from __future__ import annotations
import plotly.graph_objects as go
import plotly.io as pio

# ── Categorical palette (validated, fixed order) ──────────────────────────────
# Slot order is the CVD-safety mechanism — do not reorder casually.
SLOT = {
    "blue":    "#2a78d6",
    "green":   "#008300",
    "magenta": "#e87ba4",
    "yellow":  "#eda100",
    "aqua":    "#1baf7a",
    "orange":  "#eb6834",
    "violet":  "#4a3aa7",
    "red":     "#e34948",
}

# Product identity → color. Assigned once, by product, everywhere.
PRODUCT_COLORS = {
    "Spot":        SLOT["blue"],
    "Derivatives": SLOT["green"],
    "Options":     SLOT["violet"],
    "Credit":      SLOT["orange"],
    "FX":          SLOT["aqua"],
}
PRODUCT_ORDER = ["Spot", "Derivatives", "Options", "Credit", "FX"]

# Direction (diverging pair, not status)
BUY  = SLOT["blue"]
SELL = SLOT["red"]

# Status palette (reserved — never reused as a series color). Ships with a label.
STATUS = {
    "good":     "#0ca30c",
    "warning":  "#fab219",
    "serious":  "#ec835a",
    "critical": "#d03b3b",
}

# Chart chrome / ink (light surface)
INK_PRIMARY   = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED     = "#898781"
GRID          = "#e1e0d9"
AXIS          = "#c3c2b7"
SURFACE       = "#ffffff"
PLANE         = "#f9f9f7"

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def install_template() -> None:
    """Register 'falconx' as the default plotly template."""
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family=FONT, size=13, color=INK_SECONDARY),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        colorway=[PRODUCT_COLORS[p] for p in PRODUCT_ORDER],
        margin=dict(t=28, b=28, l=8, r=8),
        hoverlabel=dict(font_family=FONT, font_size=12, bgcolor="#ffffff",
                        bordercolor=AXIS),
        # automargin: the 8px margins above are a floor — plotly grows them to
        # fit category labels and axis titles instead of clipping them.
        xaxis=dict(showgrid=False, zeroline=False, linecolor=AXIS,
                   ticks="outside", tickcolor=AXIS, tickfont=dict(color=INK_MUTED),
                   automargin=True, title=dict(standoff=10)),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                   linecolor=AXIS, tickfont=dict(color=INK_MUTED),
                   automargin=True, title=dict(standoff=10)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(color=INK_SECONDARY), bgcolor="rgba(0,0,0,0)"),
        title=dict(font=dict(color=INK_PRIMARY, size=15)),
    )
    pio.templates["falconx"] = t
    pio.templates.default = "falconx"


def bar_ends(fig: go.Figure, radius: int = 4) -> go.Figure:
    """Round the data-end of bars (4px) per the mark spec."""
    # cliponaxis=False: outside value labels ("$737K") draw past the plot edge
    # instead of being cut off; the right margin below gives them room.
    fig.update_traces(marker=dict(cornerradius=radius), cliponaxis=False,
                      selector=dict(type="bar"))
    if any(getattr(t, "textposition", None) == "outside" for t in fig.data):
        fig.update_layout(margin=dict(r=64))
    return fig


# ── Streamlit page CSS ────────────────────────────────────────────────────────
PAGE_CSS = """
<style>
:root { --fx-ink:#0f1729; --fx-sub:#4a5468; --fx-muted:#7b8496;
        --fx-line:#e3e6ec; --fx-plane:#f4f5f7; --fx-navy:#0f1729; --fx-accent:#2a78d6; }

.block-container { padding-top: 4.2rem; padding-bottom: 3rem; max-width: 1560px; }
h1, h2, h3 { color: var(--fx-ink); letter-spacing: -0.015em; font-weight: 650; }
h3 { font-size: 1.08rem; margin-top: .4rem; }
hr { margin: .8rem 0 1.1rem; border-color: var(--fx-line); }

/* Masthead */
.fx-mh { display:flex; justify-content:space-between; align-items:flex-end;
    background: linear-gradient(100deg, #0f1729 0%, #18254a 70%, #1d3566 100%);
    color:#fff; border-radius:10px; padding:16px 22px 14px; margin:0 0 14px; }
.fx-mh-brand { font-size:.68rem; letter-spacing:.16em; color:#8fa3c8; font-weight:600; }
.fx-mh-title { font-size:1.55rem; font-weight:650; letter-spacing:-.02em; line-height:1.25; }
.fx-mh-sub { font-size:.84rem; color:#b9c4da; margin-top:2px; }
.fx-mh-right { text-align:right; }
.fx-mh-asof { font-size:.78rem; color:#b9c4da; margin-top:6px; }
.fx-mh-badge { display:inline-block; font-size:.68rem; font-weight:650; letter-spacing:.08em;
    text-transform:uppercase; padding:3px 9px; border-radius:4px;
    background:rgba(110,168,255,.18); color:#bcd6ff; border:1px solid rgba(110,168,255,.35); }
.fx-mh-demo { background:rgba(245,180,60,.15); color:#f5c674; border-color:rgba(245,180,60,.4); }

/* Identity row under the masthead */
.fx-idrow { display:flex; flex-wrap:wrap; gap:8px 28px; align-items:center; margin:0 0 12px; }
.fx-idlabel { font-size:.68rem; color:var(--fx-muted); text-transform:uppercase;
    letter-spacing:.08em; font-weight:600; margin-right:8px; }
.fx-section { font-size:.7rem; color:var(--fx-muted); text-transform:uppercase;
    letter-spacing:.1em; font-weight:650; border-bottom:1px solid var(--fx-line);
    padding-bottom:4px; margin:14px 0 8px; }

/* KPI tiles — flat strip, accent rule on top */
div[data-testid="stMetric"] {
    background:#fff; border:1px solid var(--fx-line); border-top:3px solid var(--fx-navy);
    border-radius:6px; padding:10px 14px 8px; }
div[data-testid="stMetricLabel"] p { font-size:.7rem; color:var(--fx-muted);
    text-transform:uppercase; letter-spacing:.07em; font-weight:600; }
div[data-testid="stMetricValue"] { font-size:1.4rem; font-weight:650; color:var(--fx-ink);
    font-variant-numeric: tabular-nums; }

/* Tabs: top level = underlined, bold; nested = quieter pill row */
div[data-testid="stTabs"] button[data-baseweb="tab"] p { font-size:.93rem; font-weight:600; }
div[data-testid="stTabs"] div[data-testid="stTabs"] button[data-baseweb="tab"] p {
    font-size:.84rem; font-weight:550; color:var(--fx-sub); }
div[data-testid="stTabs"] div[data-testid="stTabs"] button[aria-selected="true"] p {
    color:var(--fx-accent); }

/* Top nav */
header[data-testid="stHeader"] { background:#fff; border-bottom:1px solid var(--fx-line); }

/* Sidebar */
.fx-sb-brand { font-size:1.05rem; font-weight:700; color:#fff; letter-spacing:-.01em;
    padding:2px 0 12px; border-bottom:1px solid #2a3554; margin-bottom:12px; }
.fx-sb-brand span { display:block; font-size:.7rem; font-weight:600; color:#8fa3c8;
    letter-spacing:.14em; text-transform:uppercase; margin-top:2px; }
.fx-sb-label { font-size:.68rem; color:#8fa3c8; text-transform:uppercase;
    letter-spacing:.1em; font-weight:650; margin:14px 0 2px; }
.fx-sb-note { font-size:.75rem; color:#b9c4da; background:#1b2540; border:1px solid #2a3554;
    border-left:3px solid #f5b43c; border-radius:4px; padding:8px 10px; margin-top:14px; }
.fx-sb-note code { color:#f5c674; background:transparent; }

/* Plotly toolbar only on hover */
.js-plotly-plot .modebar-container { opacity:0; transition:opacity .15s; }
.js-plotly-plot:hover .modebar-container { opacity:1; }

/* Dataframes: tabular numerals */
div[data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }

/* Entity chips */
.fx-chip { display:inline-block; padding:2px 9px; margin:2px 4px 2px 0;
    border:1px solid var(--fx-line); border-radius:4px; font-size:.78rem;
    color:var(--fx-sub); background:#fff; }
.fx-chip-on { background:#2a78d6; color:#fff; border-color:#2a78d6; }

/* Pill badges for status */
.fx-pill { display:inline-block; padding:1px 8px; border-radius:4px;
    font-size:.72rem; font-weight:650; letter-spacing:.02em; }
.fx-good     { background:#e5f5e5; color:#0a7d0a; }
.fx-warning  { background:#fdf1d8; color:#8a5a00; }
.fx-serious  { background:#fbe6da; color:#a24a24; }
.fx-critical { background:#f7dede; color:#a52121; }

/* Product coverage cells (compact) */
.fx-cov { display:inline-block; border-radius:4px; padding:2px 10px; margin-right:4px;
    font-weight:600; font-size:.78rem; border:1px solid var(--fx-line); color:#b3b8c4;
    background:#fff; }
.fx-cov-on { background:var(--c); border-color:var(--c); color:#fff; }
</style>
"""

PILL = {
    "good": "fx-good", "warning": "fx-warning",
    "serious": "fx-serious", "critical": "fx-critical",
}


def pill(text: str, level: str) -> str:
    return f'<span class="fx-pill {PILL.get(level, "fx-good")}">{text}</span>'
