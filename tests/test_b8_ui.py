"""Batch 8: multipage layout, client persistence, Analyst CRM context, mock fixes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import llm  # noqa: E402
import ui  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")
DESK = ["views/expiries.py", "views/targeting.py", "views/daily_color.py"]


def _app():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    assert not at.exception
    return at


def _title(at) -> str:
    mh = [m.value for m in at.markdown if m.value.startswith("<div class='fx-mh'")]
    return mh[0].split("fx-mh-title'>")[1].split("<")[0]


# ── layout ────────────────────────────────────────────────────────────────────
def test_client_page_has_five_top_tabs_and_nested_views():
    at = _app()
    labels = [t.label for t in at.tabs]
    top = [lab for lab in labels if lab.startswith(":material/")]
    assert len(top) == 5
    for sub in ["Summary", "Trades", "Positions", "Options", "Credit",
                "Brief", "Notes", "Pitches & Chat"]:
        assert sub in labels
    # desk tools moved off the client page
    assert not {"Expiries", "Targeting 🎯", "Daily Color 🕟"} & set(labels)


@pytest.mark.parametrize("page,title", list(zip(DESK, ["Expiries", "Idea targeting", "Daily color"])))
def test_desk_pages_render(page, title):
    at = _app()
    at.switch_page(page).run()
    assert not at.exception
    assert _title(at) == title


def test_selected_client_follows_you_across_pages():
    at = _app()
    other = [p for p in data.search_parents() if p != at.session_state["client"]][0]
    at.selectbox(key="_client_w").set_value(other).run()
    assert at.session_state["client"] == other
    at.switch_page("views/daily_color.py").run()
    assert not at.exception
    assert at.selectbox(key="_client_w").value == other
    assert any(o == f"For {other}" for o in at.radio(key="dc_mode").options)
    at.switch_page("views/client.py").run()
    assert _title(at) == other


# ── formatting ────────────────────────────────────────────────────────────────
def test_usd_puts_sign_before_dollar():
    assert ui.usd(-670_000) == "-$670.0K"
    assert ui.usd(2.1e9) == "$2.1B"
    assert ui.usd(-12) == "-$12"


# ── analyst context ───────────────────────────────────────────────────────────
def test_analyst_context_carries_crm_sections():
    at = _app()
    ctx = [c.value for c in at.code if c.value.startswith("# FalconX Client 360 context")]
    assert len(ctx) == 1
    ctx = ctx[0]
    for h in ["## Peers (anonymous cohort", "## Pitch track record", "## Call notes",
              "## Live option structures", "## Today's desk color"]:
        assert h in ctx, h
    # peers stay anonymous: no other named client leaks into the peer/color sections
    tail = ctx[ctx.index("## Peers"):]
    tail = tail[:tail.index("## Pitch track record")] + tail[tail.index("## Today's desk color"):]
    me = at.session_state["client"]
    assert not [p for p in data.search_parents() if p != me and p in tail]


def test_build_context_without_crm_inputs_is_unchanged_shape():
    kpi = {"notional": 1e6, "trade_count": 3, "revenue": 1e3, "blended_bps": 10.0,
           "products_active": ["Spot"], "net_position": 0, "unrealized_pnl": 0,
           "credit_outstanding": 0}
    ctx = llm.build_context("X", "last 30 days", kpi, None, None, [], None, None, None, None)
    assert "## Cross-product summary" in ctx
    assert "## Peers" not in ctx and "## Pitch" not in ctx and "desk color" not in ctx


def test_expiry_header_states_total_and_shown():
    lv = pd.DataFrame([{"expiry": "2026-07-26", "dte": i, "token": "BTC", "structure": "Long Call",
                        "notional_usd": 1e6, "status": "In the money", "pin": False,
                        "talking_point": "tp"} for i in range(8)])
    ctx = llm.build_context("X", "w", {"notional": 0, "trade_count": 0, "revenue": 0,
                                       "blended_bps": 0, "products_active": [],
                                       "net_position": 0, "unrealized_pnl": 0,
                                       "credit_outstanding": 0},
                            None, None, [], None, None, None, None, expiring=lv)
    assert "(8 total; nearest 6 by expiry)" in ctx
    assert ctx.count("Long Call") == 6


# ── seams / mock ──────────────────────────────────────────────────────────────
def test_peer_id_seam_is_stable_and_views_use_it():
    assert data.peer_id_for("Brevan Howard") == data.peer_id_for("Brevan Howard")
    assert data.peer_id_for("Brevan Howard") != data.peer_id_for("Millennium")
    root = os.path.dirname(APP)
    for f in ["app.py", "ui.py"] + [os.path.join("views", v) for v in os.listdir(
            os.path.join(root, "views")) if v.endswith(".py")]:
        src = open(os.path.join(root, f)).read()
        assert "import mock" not in src, f"{f} bypasses the data seam"


def test_option_position_delta_sign_matches_right_and_side():
    for c in data.search_parents():
        p = data.get_positions(c)
        for r in p[p["product"] == "Options"].itertuples():
            want = (1 if r.instrument.endswith("-C") else -1) * (1 if r.direction == "Long" else -1)
            assert r.delta * want > 0, (c, r.instrument, r.direction, r.delta)
