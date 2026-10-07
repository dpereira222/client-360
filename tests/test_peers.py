"""Batch 2: Peers — privacy rules, cohort widening, tab build, talking points."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import mock  # noqa: E402
import peers  # noqa: E402

S, E = data.window(6, "Months")
EXT = (pd.Timestamp(S) - pd.Timedelta(weeks=16)).date().isoformat()


def _build(parent, level="Sub-segment"):
    tr = data.get_trades(parent, S, E)
    return peers.build(parent, data.get_profile(parent), tr, data.option_structures(tr), None,
                       S, E, level, data.get_peer_activity(parent, EXT, E),
                       data.get_peer_strategies(parent, EXT, E),
                       data.get_desk_idea_uptake(exclude_parent=parent))


def test_safe_k_anonymity_and_dominance():
    assert not peers.safe(pd.Series([1, 1, 1, 1]))                 # 4 < K_MIN
    assert peers.safe(pd.Series([1, 1, 1, 1, 1]))
    assert not peers.safe(pd.Series([100, 1, 1, 1, 1]))            # one peer > 60%
    assert not peers.safe(pd.Series([1, 1, 1, 1, 0, 0]))           # zeros don't count


def test_peer_feed_never_contains_client():
    import mock_peers
    for p in mock.all_parents():
        assert mock_peers.peer_id(p) not in set(data.get_peer_activity(p, S, E)["peer_id"])


def test_uptake_excludes_client():
    a = data.get_desk_idea_uptake()
    b = data.get_desk_idea_uptake(exclude_parent="Brevan Howard")
    assert b["pitched"].sum() < a["pitched"].sum()


def test_cohort_widens_when_too_small():
    prof = {"segment": "Hedge Fund", "sub_segment": "Macro"}
    act = data.get_peer_activity("Brevan Howard", S, E)
    tiny = act[(act["sub_segment"] != "Macro") |
               act["peer_id"].isin(sorted(set(act[act["sub_segment"] == "Macro"]["peer_id"]))[:3])]
    _, _, used = peers.cohort(tiny, prof, "Sub-segment")
    assert used == "Segment"


@pytest.mark.parametrize("parent", mock.all_parents())
@pytest.mark.parametrize("level", peers.LEVELS)
def test_build_every_client_and_level(parent, level):
    b = _build(parent, level)
    assert b["n"] >= peers.K_MIN and not b["empty"]
    assert peers.LEVELS.index(b["level_used"]) >= peers.LEVELS.index(level)
    for k in ("adoption", "mix", "trends", "direction", "benchmarks"):
        assert k in b
    assert (b["mix"]["peers_trading"] >= peers.K_MIN).all() if not b["mix"].empty else True


def test_talking_points_never_name_peers_or_dollars():
    names = [p for p in mock.all_parents()]
    for parent in names:
        for pt in _build(parent)["points"]:
            others = [n for n in names if n != parent]
            assert not any(n in pt["say"] for n in others), pt
            assert "$" not in pt["say"], pt
