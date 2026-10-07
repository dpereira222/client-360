"""Batch 4: Notes / CRM — tag profile, notes vs book, save path, app render."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

import data  # noqa: E402
import mock  # noqa: E402
import mock_notes  # noqa: E402
import notes  # noqa: E402

ALL = ("2000-01-01", data.TODAY.isoformat())


def _note(parent, text, ts, nid):
    import tags
    return {"note_id": nid, "parent": parent, "note_ts": pd.Timestamp(ts), "author": "A",
            "meeting_type": "Call", "text": text, "tags": tags.tag_list(text), "source": "t"}


@pytest.mark.parametrize("parent", mock.all_parents())
def test_build_every_client(parent):
    n = data.get_notes(parent, *ALL)
    v = notes.build(parent, n, data.get_trades(parent, *ALL), data.TODAY)
    assert not n.empty
    assert set(v["profile"]["tag"]) == set(notes.explode(n)["tag"])
    assert set(v["profile"]["last_note_id"]) <= set(n["note_id"])     # every tag traces to a note
    if not v["vs_book"].empty:
        assert set(v["vs_book"]["category"]) <= {"Product", "Token"}


def test_stance_follows_latest_mention():
    n = pd.DataFrame([_note("X", "Not set up for options yet.", "2026-03-01", "a"),
                      _note("X", "Now trading iron condors on ETH.", "2026-06-01", "b")])
    p = notes.profile(n).set_index("tag")
    assert p.loc["#Options", "stance"] == 1 and p.loc["#Options", "mentions"] == 2


def test_notes_vs_book_verdicts():
    n = pd.DataFrame([_note("X", "Keen on ETH options.", "2026-06-01", "a"),
                      _note("X", "No interest in credit.", "2026-06-02", "b")])
    tr = pd.DataFrame({"product": ["Spot"], "token": ["BTC"]})
    v = notes.notes_vs_book(n, tr).set_index("tag")["verdict"]
    assert v["#Options"] == "Cross-sell" and v["#ETH"] == "Cross-sell"
    assert v["#Credit"] == "Onboarding"


def test_who_mentioned_separates_against():
    n = pd.DataFrame([_note("Yes", "Active in ETH vol.", "2026-06-01", "a"),
                      _note("No", "No vol trading, ETH spot only.", "2026-06-01", "b")])
    w = notes.who_mentioned(n, ["#Vol"]).set_index("parent")
    assert w.loc["Yes", "mentions"] == 1 and w.loc["No", "mentions"] == 0
    assert w.loc["No", "against"] == 1


def test_save_note_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_notes, "LOCAL_FILE", str(tmp_path / "n.jsonl"))
    row = data.save_note("Brevan Howard", "Tester", "Call",
                         "Wants an ETH iron condor into Sept. Follow up next week.",
                         note_ts=f"{data.TODAY}T10:00:00")
    assert {"#ETH", "#IronCondor", "#Options", "#Vol", "#FollowUp"} <= set(row["tags"])
    n = data.get_notes("Brevan Howard", *ALL)
    assert row["note_id"] in set(n["note_id"])
    assert row["note_id"] in set(notes.follow_ups(n, data.TODAY)["note_id"])


def test_app_renders_notes_tab():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(os.path.dirname(__file__), "..", "app.py"),
                           default_timeout=120)
    at.run()
    assert not at.exception
    tab = [t for t in at.tabs if t.label == "Notes"][0]
    assert any(m.label == "Notes logged" for m in tab.metric)
    assert len(tab.dataframe) >= 1                                   # book-wide search
