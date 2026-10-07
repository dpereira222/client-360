"""Batch 1: hashtags, data.py seams, notes write path, pitch matcher sanity."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CLIENT360_MOCK", "1")

import pytest  # noqa: E402

import data  # noqa: E402
import mock  # noqa: E402
import mock_chat  # noqa: E402
import mock_notes  # noqa: E402
import pitches  # noqa: E402
import tags  # noqa: E402

S, E = "2026-01-17", "2026-07-16"


@pytest.mark.parametrize("text,want,unwanted", [
    ("Using our TWAP for ETH, wants a condor into Sept", {"#ETH", "#Algo", "#IronCondor", "#Options", "#Vol"}, set()),
    ("Not set up for options; no interest in SOL, but keen on BTC basis",
     {"-#Options", "-#SOL", "#BTC", "#Basis", "#Derivatives"}, {"#Options", "#SOL"}),
    ("wants us to call at specific levels; set up a call next week", {"#FollowUp"}, {"#Options"}),
    ("buying ETH calls and selling 25d puts", {"#ETH", "#Options"}, set()),
    ("pricing on perps too wide vs Coinbase", {"#PricingFeedback", "#Competitor", "#Derivatives"}, set()),
])
def test_tags(text, want, unwanted):
    got = set(tags.tag_list(text))
    assert want <= got, got
    assert not (unwanted & got), got


def test_negated_tag_does_not_imply():
    got = tags.tag_list("no condors for us")
    assert "-#IronCondor" in got and "#Options" not in got and "#Vol" not in got


@pytest.mark.parametrize("parent", mock.all_parents())
def test_seams_return_data(parent):
    p = data.get_profile(parent)
    assert {"segment", "sub_segment", "coverage", "contact", "products"} <= set(p)
    assert not data.get_trade_ideas(parent, S, E).empty
    assert not data.get_chat_messages(parent, S, E).empty
    assert not data.get_rfqs(parent, S, E).empty
    pa = data.get_peer_activity(parent, S, E)
    assert not pa.empty


def test_book_trades_cover_every_client():
    bk = data.get_book_trades("2026-06-01", E)
    assert set(bk["parent"]) == set(mock.all_parents())


def test_note_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_notes, "LOCAL_FILE", str(tmp_path / "notes.jsonl"))
    before = len(data.get_notes("Millennium", "2025-01-01", E))
    row = data.save_note("Millennium", "Sarah Kwan", "Call",
                         "Priya wants BTC calendar spreads", note_ts="2026-07-16T10:00:00")
    assert "#Calendar" in row["tags"]
    after = data.get_notes("Millennium", "2025-01-01", E)
    assert len(after) == before + 1 and row["note_id"] in set(after["note_id"])


def test_matcher_recall_and_precision():
    tp = fp = fn = 0
    for parent in mock.all_parents():
        t = pitches.match_executions(data.get_trade_ideas(parent, S, E), data.get_trades(parent, S, E))
        gt = mock_chat.ground_truth(parent)
        for iid, ex in zip(t["idea_id"], t["executed"]):
            truth = bool(gt.get(iid))
            tp += ex and truth
            fp += ex and not truth
            fn += (not ex) and truth
    assert tp / (tp + fn) >= 0.95 and tp / (tp + fp) >= 0.85, (tp, fp, fn)


def test_no_reply_flag():
    for parent in mock.all_parents():
        ideas = data.get_trade_ideas(parent, S, E)
        m = pitches.match_executions(ideas, data.get_trades(parent, S, E)).merge(ideas, on="idea_id")
        bad = m[(m["reaction"] == "No response") & (m["exec_flag"] == "Passed, then traded")]
        assert bad.empty
