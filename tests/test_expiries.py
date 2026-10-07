"""Batch 5: Expiries — live book, payoff marks, filters, Monday digest, app render."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import expiries as ex  # noqa: E402
import mock  # noqa: E402

TODAY = date(2026, 7, 16)


def _leg(inst, side, qty, px, sid, ent="E1", ts="2026-07-01 10:00"):
    tok, _, k, _ = inst.split("-")
    return {"trade_time": pd.Timestamp(ts), "entity_id": ent, "entity_name": ent,
            "product": "Options", "instrument": inst, "token": tok, "side": side,
            "qty": qty, "price": px, "notional_usd": qty * float(k), "structure_id": sid,
            "parent": "P"}


def _prices(**spots):
    return pd.DataFrame([{"date": TODAY, "token": t, "close": c} for t, c in spots.items()])


def _live(rows, **spots):
    return ex.live(pd.DataFrame(rows), {"P": {"coverage": "Rep"}}, _prices(**spots), TODAY)


@pytest.fixture(scope="module")
def book_view():
    book = data.get_book_trades("2000-01-01", data.TODAY.isoformat())
    profiles = {p: data.get_profile(p) for p in mock.all_parents()}
    return book, ex.build(book, profiles, data.get_price_history(), data.TODAY)


def test_expiry_date_parse():
    assert ex.expiry_date("26JUL26") == date(2026, 7, 26)
    assert ex.expiry_date("garbage") is None


def test_only_live_structures(book_view):
    _, v = book_view
    lv = v["live"]
    assert not lv.empty and (lv["expiry"] >= data.TODAY).all() and (lv["dte"] >= 0).all()
    assert lv["structure_id"].is_unique
    assert (lv["coverage"] != "").all()                       # every row routes to a rep
    assert v["ladder"]["structures"].sum() == len(lv)


def test_expired_structure_dropped():
    rows = [_leg("BTC-26JUN26-90000-C", "Buy", 1, 1000, "old")]
    assert _live(rows, BTC=95000).empty


def test_short_put_payoff_and_status():
    # sold 1 put @ 2000 premium, strike 100k, spot 90k → owes 10k, pnl −8k
    r = _live([_leg("BTC-26JUL26-100000-P", "Sell", 1, 2000, "s")], BTC=90000).iloc[0]
    assert r["premium_usd"] == -2000 and r["payoff_usd"] == -10000
    assert r["pnl_usd"] == -8000 and r["status"] == "Against them" and r["dte"] == 10


def test_otm_credit_keeps_premium():
    rows = [_leg("ETH-26JUL26-4000-C", "Sell", 10, 50, "st"),
            _leg("ETH-26JUL26-2500-P", "Sell", 10, 40, "st")]
    r = _live(rows, ETH=3200).iloc[0]
    assert r["structure"] == "Short Strangle" and r["payoff_usd"] == 0
    assert r["pnl_usd"] == 900 and "keep the" in r["talking_point"]


def test_pin_risk_flag():
    r = _live([_leg("ETH-26JUL26-3250-C", "Buy", 1, 60, "pin")], ETH=3200).iloc[0]
    assert r["pin"] and r["status"] == "Pin risk" and "Pin risk" in r["talking_point"]
    far = [_leg("ETH-26OCT26-3250-C", "Buy", 1, 60, "far")]
    assert not _live(far, ETH=3200).iloc[0]["pin"]           # near strike but > PIN_DAYS out


def test_calendar_keeps_back_leg_after_front_expires():
    rows = [_leg("BTC-26JUN26-95000-C", "Sell", 1, 1000, "cal"),
            _leg("BTC-26SEP26-95000-C", "Buy", 1, 3000, "cal")]
    r = _live(rows, BTC=95000).iloc[0]
    assert r["expiry"] == date(2026, 9, 26) and r["n_legs"] == 1


def test_filters(book_view):
    _, v = book_view
    lv = v["live"]
    p = lv["parent"].iloc[0]
    assert set(ex.apply_filters(lv, parents=[p])["parent"]) == {p}
    big = ex.apply_filters(lv, min_notional=10_000_000)
    assert (big["notional_usd"] >= 10_000_000).all()
    soon = ex.apply_filters(lv, expiry_to=data.TODAY + timedelta(days=14))
    assert (soon["dte"] <= 14).all()
    ic = ex.apply_filters(lv, structures=["Iron Condor"])
    assert set(ic["structure"]) <= {"Iron Condor"}
    assert len(ex.apply_filters(lv)) == len(lv)


def test_week_window_is_next_monday():
    s, e = ex.week_window(date(2026, 7, 16))                 # a Thursday
    assert s == date(2026, 7, 20) and s.weekday() == 0 and (e - s).days == 6
    assert ex.week_window(date(2026, 7, 20))[0] == date(2026, 7, 20)


def test_digest_only_my_clients(book_view):
    _, v = book_view
    lv = v["live"]
    rep = v["coverage"][0]
    mine = set(lv.loc[lv["coverage"] == rep, "parent"])
    txt = ex.digest(lv, rep, data.TODAY, data.TODAY + timedelta(days=120))
    for p in set(lv["parent"]) - mine:
        assert f"• {p}" not in txt
    assert ex.digest(lv, "Nobody", data.TODAY, data.TODAY).endswith("expires this week.")


def test_app_renders_expiries_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                           default_timeout=180)
    at.run()
    assert not at.exception
    at.switch_page("views/expiries.py").run()
    assert not at.exception
    tab = at.main
    assert any(m.label == "Live structures" for m in tab.metric)
    assert len(tab.code) == 1                                   # Monday note
    at.radio(key="exp_scope").set_value("Whole book").run()
    assert not at.exception
