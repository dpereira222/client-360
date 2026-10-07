"""Batch 7: Daily color — window, moves, flow, privacy, note, per-client cut, app render."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import daily_color as dc  # noqa: E402
import data  # noqa: E402
import mock_peers  # noqa: E402
import peers  # noqa: E402

ASOF = date(2026, 7, 16)


@pytest.fixture(scope="module")
def flow():
    s, e = dc.history_window(ASOF)
    return data.get_desk_flow(s.isoformat(), e.isoformat())


@pytest.fixture(scope="module")
def view(flow):
    return dc.build(flow, data.get_price_history(), ASOF)


def _f(rows):
    base = {"segment": "Hedge Fund", "sub_segment": "Macro", "tier": "Gold",
            "product": "Spot", "token": "BTC", "strategy": None, "bias": None}
    return pd.DataFrame([{**base, **r} for r in rows])


# ── window ────────────────────────────────────────────────────────────────────
def test_window_is_dst_aware():
    s, e = dc.window(date(2026, 7, 16))             # EDT
    assert (s, e) == (pd.Timestamp("2026-07-15 20:30"), pd.Timestamp("2026-07-16 20:30"))
    s, e = dc.window(date(2026, 1, 15))             # EST
    assert e == pd.Timestamp("2026-01-15 21:30")


def test_color_day_cutoff():
    ts = pd.Series(pd.to_datetime(["2026-07-16 20:29", "2026-07-16 20:31"]))
    assert list(dc.color_day(ts)) == [date(2026, 7, 16), date(2026, 7, 17)]


def test_seam_respects_window(flow):
    s, e = dc.history_window(ASOF)
    assert flow["trade_time"].min() >= s and flow["trade_time"].max() < e
    cols = {"trade_time", "peer_id", "segment", "sub_segment", "tier", "product", "token",
            "side", "notional_usd", "strategy", "bias"}
    assert cols <= set(flow.columns)


def test_flow_has_no_names(flow):
    import mock
    assert not set(mock.all_parents()) & set(flow["peer_id"])
    assert flow["peer_id"].str.startswith("P-").all()


# ── moves ─────────────────────────────────────────────────────────────────────
def test_moves_sorted_by_sigma_and_no_stables():
    mv = dc.moves(data.get_price_history(), ASOF)
    assert not set(dc.STABLES) & set(mv["token"])
    assert (mv["z"].abs().diff().dropna() <= 1e-12).all()
    import mock
    eth = mv.set_index("token").loc["ETH"]
    assert eth["ret"] == pytest.approx(mock.close_on("ETH", ASOF) /
                                       mock.close_on("ETH", ASOF - timedelta(days=1)) - 1)


# ── flow math ─────────────────────────────────────────────────────────────────
def test_token_flow_ratio_and_buy_share():
    day = pd.Timestamp("2026-07-16 15:00")
    rows = []
    for i in range(1, dc.BASE_DAYS + 1):          # $1M/day baseline
        rows.append({"trade_time": day - pd.Timedelta(days=i), "peer_id": "P-0",
                     "side": "Buy", "notional_usd": 1e6})
    for k in range(6):                             # today: 6 peers, $3M, 2/3 buy
        rows.append({"trade_time": day, "peer_id": f"P-{k}",
                     "side": "Buy" if k < 4 else "Sell", "notional_usd": 0.5e6})
    rows.append({"trade_time": day, "peer_id": "P-9", "product": "Credit",
                 "side": "Buy", "notional_usd": 1e6})   # credit: activity yes, skew no
    tf = dc.token_flow(_f(rows), ASOF).set_index("token").loc["BTC"]
    assert tf["activity_x"] == pytest.approx(4.0)
    assert tf["buy_share"] == pytest.approx(4 / 6)
    assert tf["safe"] and tf["lean"] == "buyers"


def test_segment_skew_suppresses_small_and_dominated():
    day = pd.Timestamp("2026-07-16 15:00")
    few = [{"trade_time": day, "peer_id": f"P-{k}", "side": "Buy", "notional_usd": 1e6}
           for k in range(peers.K_MIN - 1)]
    dom = [{"trade_time": day, "peer_id": f"Q-{k}", "segment": "Corporate", "side": "Sell",
            "notional_usd": 1e8 if k == 0 else 1e6} for k in range(peers.K_MIN + 2)]
    sk = dc.segment_skew(_f(few + dom), ASOF).set_index("segment")
    assert not sk.loc["Hedge Fund", "safe"]
    assert not sk.loc["Corporate", "safe"]
    assert dc.contrasts(sk.reset_index()) == []


def test_exclude_peer_removes_their_flow(flow):
    pid = mock_peers.peer_id("Brevan Howard")
    assert pid in set(flow["peer_id"])
    tf_all = dc.token_flow(flow, ASOF)
    tf_ex = dc.token_flow(flow, ASOF, exclude_peer=pid)
    today = flow[dc.color_day(flow["trade_time"]) == ASOF]
    mine = set(today.loc[today["peer_id"] == pid, "token"])
    for tok in mine & set(tf_all["token"]) & set(tf_ex["token"]):
        assert (tf_all.set_index("token").loc[tok, "n_peers"]
                == tf_ex.set_index("token").loc[tok, "n_peers"] + 1)


# ── headlines + note ──────────────────────────────────────────────────────────
def test_headlines_reference_only_safe_facts(view):
    assert view["headlines"], "mock should produce at least one headline"
    unsafe = set(view["token_flow"].loc[~view["token_flow"]["safe"], "token"])
    for h in view["headlines"]:
        if h["kind"] == "flow":
            assert h["token"] not in unsafe
    scores = [h["score"] for h in view["headlines"]]
    assert scores == sorted(scores, reverse=True)


def test_big_move_gets_a_headline(view):
    big = view["moves"][view["moves"]["z"].abs() >= dc.BIG_MOVE_Z]["token"]
    moved = {h["token"] for h in view["headlines"] if h["kind"] == "move"}
    assert set(big) == moved


def test_note_has_no_dollars_or_names(view):
    import mock
    txt = dc.note(view)
    assert "$" not in txt
    assert not any(p in txt for p in mock.all_parents())
    assert dc.DISCLAIMER in txt and "4:30pm ET" in txt
    ctx = dc.color_context(view)
    assert "$" not in ctx and not any(p in ctx for p in mock.all_parents())


def test_focus_floats_client_tokens(view):
    toks = [h["token"] for h in view["headlines"] if h["token"]]
    last = toks[-1]
    txt = dc.note(view, focus=[last])
    first = txt.split("What we saw:\n")[1].splitlines()[0]
    assert last in first


def test_for_client_excludes_and_titles(flow):
    txt = dc.for_client(flow, data.get_price_history(), ASOF,
                        mock_peers.peer_id("Brevan Howard"), ["SOL"], "Brevan Howard")
    assert txt.startswith("FalconX Daily Color for Brevan Howard")


def test_quiet_day_note():
    v = {"asof": ASOF, "headlines": [], "contrasts": [],
         "moves": pd.DataFrame([{"token": "BTC", "ret": 0.001, "z": 0.1}]),
         "desk": {"activity_x": 0.5}}
    txt = dc.note(v)
    assert "quiet" in txt and "no standout flow" in txt


def test_rep_sheet_hooks(view):
    profiles = {p: data.get_profile(p) for p in data.search_parents()}
    toks = {p: [h["token"] for h in view["headlines"] if h["token"]][:1] for p in profiles}
    sh = dc.rep_sheet(view, profiles, toks)
    assert len(sh) == len(profiles)
    assert (sh["n_matches"] >= 1).all()


def test_cli_run():
    txt = dc.run(data, ASOF)
    assert txt.startswith("FalconX Daily Color · Thu 16 Jul 2026")


# ── app ───────────────────────────────────────────────────────────────────────
def test_app_renders_daily_color_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                           default_timeout=240)
    at.run()
    assert not at.exception
    at.switch_page("views/daily_color.py").run()
    assert not at.exception
    tab = at.main
    assert any(m.label == "Desk activity" for m in tab.metric)
    assert tab.code[0].value.startswith("FalconX Daily Color")
    at.radio(key="dc_mode").set_value(f"For {at.session_state['client']}").run()
    assert not at.exception
    tab = at.main
    assert " for " in tab.code[0].value.splitlines()[0]
    at.date_input(key="dc_day").set_value(ASOF - timedelta(days=3)).run()
    assert not at.exception
