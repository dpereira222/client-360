"""Batch 6: Idea targeting — parsing, point-in-time features, model, post-mortem, app."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import targeting as tg  # noqa: E402


@pytest.fixture(scope="module")
def tv():
    ctx = tg.load(data, data.TODAY)
    return ctx, tg.build(ctx, data.TODAY)


@pytest.mark.parametrize("text,tok,prod,struct", [
    ("ETH vol into the upgrade — long straddle", "ETH", "Options", "Long Straddle"),
    ("BTC iron condor into Sept", "BTC", "Options", "Iron Condor"),
    ("short ARB perps on the unlock", "ARB", "Derivatives", None),
    ("long SOL spot, ETF flows", "SOL", "Spot", None),
])
def test_idea_from_text(text, tok, prod, struct):
    i = tg.idea_from_text(text)
    assert (i.token, i.product, i.structure) == (tok, prod, struct)
    if struct:
        assert "#Vol" in i.tag_set() or struct == "Risk Reversal"


def test_short_parsed():
    assert tg.idea_from_text("short ARB perps").direction == "Short"


def test_features_are_point_in_time(tv):
    """A feature at time t must not change when we add data after t."""
    ctx, _ = tv
    r = ctx.ideas.iloc[len(ctx.ideas) // 2]
    idea = tg.idea_from_row(r)
    a = tg.features(ctx, r["parent"], idea, r["ts"], exclude_idea=r["idea_id"])["x"]
    # poison everything after t: a giant future fill, a future note, a future taken idea
    t = pd.Timestamp(r["ts"])
    p = r["parent"]
    ctx.trades[p] = pd.concat([ctx.trades[p], pd.DataFrame([{
        "trade_time": t + pd.Timedelta(days=1), "product": idea.product, "token": idea.token,
        "notional_usd": 1e12}])], ignore_index=True)
    b = tg.features(ctx, p, idea, t, exclude_idea=r["idea_id"])["x"]
    ctx.trades[p] = ctx.trades[p].iloc[:-1]
    assert a == b


def test_own_outcome_not_a_feature(tv):
    ctx, _ = tv
    r = ctx.ideas[ctx.ideas["executed"]].iloc[-1]
    f = tg.features(ctx, r["parent"], tg.idea_from_row(r), r["ts"], exclude_idea=r["idea_id"])
    later = ctx.ideas[(ctx.ideas["parent"] == r["parent"]) & (ctx.ideas["ts"] < r["ts"])]
    assert f["ev"]["n"] == len(later)


def test_model_learned_and_signs_sensible(tv):
    _, v = tv
    m = v["model"]
    assert m.learned and m.n_train >= tg.MIN_TRAIN
    for f, w in zip(tg.FEATURES, m.coef):
        assert w == 0 or np.sign(w) == np.sign(tg._PRIOR_W[f]), f


def test_model_beats_baseline_out_of_sample(tv):
    _, v = tv
    e = v["eval"]
    assert e["auc_model"] > 0.55 and e["auc_model"] > e["auc_baseline"]
    assert e["top_third_model"] >= e["base_rate"]


def test_cold_start_falls_back_to_prior():
    m = tg.fit(pd.DataFrame(columns=tg.FEATURES + ["y"]))
    assert not m.learned and m.coef[tg.FEATURES.index("setup")] > 0


def test_rank_orders_and_flags_onboarding(tv):
    ctx, v = tv
    r = tg.rank(ctx, v["model"], tg.idea_from_text("ETH long straddle"), data.TODAY)
    assert len(r) == len(ctx.parents) and list(r["rank"]) == list(range(1, len(r) + 1))
    pros = r[r["status"] == "Prospect"]
    assert pros["score"].is_monotonic_decreasing
    onb = r[r["status"] == "Onboard first"]
    for p in onb["parent"]:                                  # not set up → never a prospect
        assert "Options" not in ctx.profiles[p]["products"]
    assert pros.iloc[0]["reasons"]                           # top pick explains itself


def test_vol_history_raises_score_all_else_equal(tv):
    ctx, v = tv
    idea = tg.idea_from_text("BTC long straddle")
    p = max(ctx.parents, key=lambda p: tg.features(ctx, p, idea, data.TODAY)["ev"]["n_family"])
    x = tg.features(ctx, p, idea, data.TODAY)["x"]
    assert v["model"].prob(x) > v["model"].prob({**x, "struct_family": 0.0})
    assert v["model"].prob(x) > v["model"].prob({**x, "notes_neg": 1.0})


def test_tiers_relative_to_prospects(tv):
    ctx, v = tv
    r = tg.rank(ctx, v["model"], tg.idea_from_text("BTC long straddle"), data.TODAY)
    pros = r[r["status"] == "Prospect"]
    assert set(pros["tier"]) == {"High", "Medium", "Low"}
    assert (r.loc[r["status"] != "Prospect", "tier"] == "—").all()


def test_post_mortem(tv):
    ctx, v = tv
    did = v["desk_ideas"].iloc[0]["desk_idea_id"]
    pm = tg.post_mortem(ctx, did)
    d = ctx.ideas[ctx.ideas["desk_idea_id"] == did]
    assert pm["pitched"] == len(d) and pm["taken"] == int(d["executed"].sum())
    assert pm["n_train"] <= int((ctx.ideas["ts"] < pm["ts"]).sum())   # refit on the past only
    assert pm["lessons"][0].startswith(f"Pitched to {len(d)}")


def test_draft_context_names_only_prospects(tv):
    ctx, v = tv
    r = tg.rank(ctx, v["model"], tg.idea_from_text("ETH long straddle"), data.TODAY)
    txt = tg.draft_context(tg.idea_from_text("ETH long straddle"), r, n=3)
    assert txt.count("CLIENT:") == 3
    for p in r[r["status"] != "Prospect"]["parent"]:
        assert p not in txt


def test_app_renders_targeting_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                           default_timeout=240)
    at.run()
    assert not at.exception
    at.switch_page("views/targeting.py").run()
    assert not at.exception
    tab = at.main
    assert any(m.label == "Prospects" for m in tab.metric)
    at.text_input(key="tgt_q").set_value("short ARB perps on the unlock").run()
    assert not at.exception
