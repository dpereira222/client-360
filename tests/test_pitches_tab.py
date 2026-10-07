"""Batch 3: Pitches & Chat — tab assembly, track-record invariants, app render."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import mock  # noqa: E402
import pitches  # noqa: E402

S, E = data.window(6, "Months")


def _build(parent):
    return pitches.build(parent, data.get_profile(parent), data.get_trade_ideas(parent, S, E),
                         data.get_trades(parent, S, E), data.get_price_history(),
                         data.get_chat_messages(parent, S, E), data.get_rfqs(parent, S, E),
                         data.TODAY)


@pytest.mark.parametrize("parent", mock.all_parents())
def test_build_every_client(parent):
    v = _build(parent)
    t, sc = v["tracker"], v["score"]
    assert not t.empty and sc["pitched"] == len(t)
    assert 0 <= sc["take_rate"] <= 1
    assert (t.loc[~t["executed"], "client_pnl_usd"] == 0).all()     # no P&L on untaken ideas
    assert (t.loc[t["executed"], "missed_pnl_usd"] == 0).all()      # nothing "missed" if taken
    assert (t["missed_pnl_usd"] >= 0).all()
    assert set(t["status"]) <= {"Target hit", "Stopped out", "Expired", "Open"}
    assert v["contact"] == data.get_profile(parent)["contact"]


def test_talking_points_use_contact_and_cite_dates():
    v = _build("Brevan Howard")
    assert v["points"]
    assert any("James" in p["say"] for p in v["points"])
    ws = [p["weight"] for p in v["points"]]
    assert ws == sorted(ws, reverse=True)


def test_interests_flag_latent_demand():
    it = _build("Brevan Howard")["interests"]
    gap = set(it.loc[~it["trades_with_us"], "token"])
    assert gap & {"HYPE", "SUI", "TAO", "ENA"}                       # mock interest tokens
    traded = set(data.get_trades("Brevan Howard", S, E)["token"])
    assert not (gap & traded)


def test_empty_inputs_dont_crash():
    empty = pd.DataFrame()
    v = pitches.build("X", None, empty, empty, data.get_price_history(), empty, empty, data.TODAY)
    assert v["tracker"].empty and v["score"] == {} and v["points"] == []
    assert v["chat"] == {} and v["rfq"] == {}


def test_app_renders_pitches_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                           default_timeout=120)
    at.run()
    assert not at.exception
    tab = [t for t in at.tabs if t.label == "Pitches & Chat"][0]
    assert len(tab.get("plotly_chart")) >= 3
    assert any(m.label == "Ideas pitched" for m in tab.metric)
