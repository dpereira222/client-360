"""
Shared page furniture for every Client 360 page: number formatting, the masthead,
the sidebar client picker (persists across pages), and the date-range control.

Pages live in views/; app.py is only the router. Keep data access in data.py —
nothing here touches a table.
"""
from __future__ import annotations
import datetime as _dt

import pandas as pd
import streamlit as st

import data
import theme


def usd(x: float, prec: int = 1) -> str:
    x = float(x)
    a, sign = abs(x), "-" if x < 0 else ""
    if a >= 1e9:
        return f"{sign}${a/1e9:.{prec}f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.{prec}f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.{prec}f}K"
    return f"{sign}${a:,.0f}"


def fmt(df: pd.DataFrame, formatters: dict) -> pd.DataFrame:
    """Return a copy with columns formatted to display strings (no Styler/jinja2)."""
    out = df.copy()
    for col, fn in formatters.items():
        if col in out.columns:
            out[col] = out[col].apply(lambda v: "" if pd.isna(v) else fn(v))
    return out


# ── Masthead ──────────────────────────────────────────────────────────────────
def masthead(title: str, subtitle: str = "") -> None:
    """Dark band at the top of every page: brand · page title · as-of · data badge."""
    badge = ("<span class='fx-mh-badge fx-mh-demo'>Demo data</span>" if data.MOCK_MODE
             else "<span class='fx-mh-badge'>Live · Databricks</span>")
    sub = f"<div class='fx-mh-sub'>{subtitle}</div>" if subtitle else ""
    st.markdown(
        f"<div class='fx-mh'><div><div class='fx-mh-brand'>FALCONX · CLIENT 360</div>"
        f"<div class='fx-mh-title'>{title}</div>{sub}</div>"
        f"<div class='fx-mh-right'>{badge}"
        f"<div class='fx-mh-asof'>As of {data.TODAY:%a %d %b %Y}</div></div></div>",
        unsafe_allow_html=True)


def section(label: str) -> None:
    """Small uppercase rule-label used between blocks on a page."""
    st.markdown(f"<div class='fx-section'>{label}</div>", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
def _sidebar_brand() -> None:
    st.sidebar.markdown("<div class='fx-sb-brand'>FalconX<span>Client 360</span></div>",
                        unsafe_allow_html=True)


def client_picker() -> str:
    """Sidebar client selector. The choice lives in st.session_state['client'] (a plain
    key, not the widget key) so it survives moving between pages."""
    _sidebar_brand()
    parents = data.search_parents()
    cur = st.session_state.get("client")
    if cur not in parents:
        cur = "Brevan Howard" if "Brevan Howard" in parents else parents[0]
    st.session_state["client"] = cur

    def _sync():
        st.session_state["client"] = st.session_state["_client_w"]

    st.sidebar.selectbox("Client relationship", parents, index=parents.index(cur),
                         key="_client_w", on_change=_sync)
    return st.session_state["client"]


def date_range() -> tuple[str, str, str]:
    """Sidebar lookback / custom range → (start, end, label). Client page only — the
    desk pages are book-wide and carry their own date logic."""
    st.sidebar.markdown("<div class='fx-sb-label'>Period</div>", unsafe_allow_html=True)
    mode = st.sidebar.radio("Date range", ["Quick lookback", "Custom range"],
                            horizontal=True, label_visibility="collapsed")
    if mode == "Quick lookback":
        c1, c2 = st.sidebar.columns([1, 1])
        unit = c2.selectbox("Unit", ["Days", "Weeks", "Months", "Years"], index=2)
        amount = c1.number_input(
            "Last", min_value=1,
            value={"Days": 30, "Weeks": 12, "Months": 6, "Years": 1}[unit], step=1)
        start, end = data.window(int(amount), unit)
        label = f"last {amount} {unit.lower()}"
    else:
        today = data.TODAY
        dr = st.sidebar.date_input(
            "Pick start & end",
            value=(today - _dt.timedelta(days=180), today),
            min_value=today - _dt.timedelta(days=365 * 3), max_value=today)
        if isinstance(dr, (tuple, list)) and len(dr) == 2:
            start, end = dr[0].isoformat(), dr[1].isoformat()
        else:
            start, end = (today - _dt.timedelta(days=180)).isoformat(), today.isoformat()
        label = f"{start} → {end}"
    st.sidebar.caption(f"{start} → {end}")
    return start, end, label


def sidebar_footer(note: str = "") -> None:
    if note:
        st.sidebar.caption(note)
    if data.MOCK_MODE:
        st.sidebar.markdown(
            "<div class='fx-sb-note'><b>Demo data.</b> Running on the mock layer — set "
            "<code>CLIENT360_MOCK=0</code> once Databricks is wired.</div>",
            unsafe_allow_html=True)


# ── Shared caches (used by more than one page) ────────────────────────────────
@st.cache_data(show_spinner="Loading the expiry book…")
def expiry_view() -> dict:
    """expiries.build() over the whole book — the Expiries page and the client
    Analyst both read it."""
    import expiries
    book = data.get_book_trades("2000-01-01", data.TODAY.isoformat())
    profiles = {p: data.get_profile(p) for p in data.search_parents()}
    return expiries.build(book, profiles, data.get_price_history(), data.TODAY)


@st.cache_data(show_spinner="Pulling the desk's flow…")
def color_flow(asof):
    import daily_color
    s, e = daily_color.history_window(asof)
    return data.get_desk_flow(s.isoformat(), e.isoformat())


@st.cache_data(show_spinner=False)
def color_view(asof, exclude_peer=None) -> dict:
    """daily_color.build() — Daily color page, and the client Analyst (with the
    client's own flow excluded)."""
    import daily_color
    return daily_color.build(color_flow(asof), data.get_price_history(), asof, exclude_peer)


def pill(text: str, level: str) -> str:
    return theme.pill(text, level)
