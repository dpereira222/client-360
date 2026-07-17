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
        xaxis=dict(showgrid=False, zeroline=False, linecolor=AXIS,
                   ticks="outside", tickcolor=AXIS, tickfont=dict(color=INK_MUTED)),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=False,
                   linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(color=INK_SECONDARY), bgcolor="rgba(0,0,0,0)"),
        title=dict(font=dict(color=INK_PRIMARY, size=15)),
    )
    pio.templates["falconx"] = t
    pio.templates.default = "falconx"


def bar_ends(fig: go.Figure, radius: int = 4) -> go.Figure:
    """Round the data-end of bars (4px) per the mark spec."""
    fig.update_traces(marker=dict(cornerradius=radius),
                      selector=dict(type="bar"))
    return fig


# ── Streamlit page CSS ────────────────────────────────────────────────────────
PAGE_CSS = """
<style>
:root { --fx-ink:#0b0b0b; --fx-sub:#52514e; --fx-muted:#898781;
        --fx-line:#e1e0d9; --fx-plane:#f9f9f7; }

.block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }

/* KPI tiles */
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid var(--fx-line);
    border-radius: 12px;
    padding: 14px 16px 12px;
    box-shadow: 0 1px 2px rgba(11,11,11,0.04);
}
div[data-testid="stMetricLabel"] p { font-size: .78rem; color: var(--fx-muted);
    text-transform: uppercase; letter-spacing: .04em; }
div[data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 650;
    color: var(--fx-ink); }

/* Section headers */
h2, h3 { color: var(--fx-ink); letter-spacing: -0.01em; }
hr { margin: .8rem 0 1.1rem; border-color: var(--fx-line); }

/* Tabs */
button[data-baseweb="tab"] { font-size: .95rem; font-weight: 550; }

/* Entity chips */
.fx-chip { display:inline-block; padding:3px 10px; margin:2px 4px 2px 0;
    border:1px solid var(--fx-line); border-radius:999px; font-size:.8rem;
    color:var(--fx-sub); background:#fff; }
.fx-chip-on { background:#2a78d6; color:#fff; border-color:#2a78d6; }

/* Pill badges for status */
.fx-pill { display:inline-block; padding:2px 9px; border-radius:6px;
    font-size:.76rem; font-weight:600; }
.fx-good     { background:#e5f5e5; color:#0a7d0a; }
.fx-warning  { background:#fdf1d8; color:#8a5a00; }
.fx-serious  { background:#fbe6da; color:#a24a24; }
.fx-critical { background:#f7dede; color:#a52121; }

/* Coverage matrix cells */
.fx-cov { border-radius:8px; padding:10px 12px; text-align:center;
    font-weight:600; font-size:.85rem; border:1px solid var(--fx-line); }
</style>
"""

PILL = {
    "good": "fx-good", "warning": "fx-warning",
    "serious": "fx-serious", "critical": "fx-critical",
}


def pill(text: str, level: str) -> str:
    return f'<span class="fx-pill {PILL.get(level, "fx-good")}">{text}</span>'
