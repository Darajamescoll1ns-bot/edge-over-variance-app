"""
test_holdem_game.py — Validation for the Texas Hold'em engine
=============================================================

Run with:  python3 test_holdem_game.py
"""

from __future__ import annotations

import traceback

import holdem_game as h
import solver as sv
import coach as cm


def _keys(st):
    return [o["key"] for o in st.awaiting["options"]]


def _autoplay(seed, num_opponents=1, strategy="call"):
    st = h.new_hand(seed=seed, num_opponents=num_opponents)
    steps = 0
    while not st.finished and steps < 120:
        keys = _keys(st)
        if strategy == "fold" and "fold" in keys:
            choice = "fold"
        elif "call" in keys:
            choice = "call"
        elif "check" in keys:
            choice = "check"
        else:
            choice = keys[0]
        st = h.act(st, choice)
        steps += 1
    return st


def test_deal_shapes_all_sizes():
    for n in range(1, 8):                       # 1-7 opponents => 2-8 players
        st = h.new_hand(seed=4, num_opponents=n)
        assert len(st.players) == n + 1
        assert all(len(p.hole) == 2 for p in st.players)
        assert len(st.board) == 5
        # No duplicate cards across hole cards + board.
        allc = [c for p in st.players for c in p.hole] + st.board
        assert len(set(allc)) == 2 * (n + 1) + 5, f"dupes with {n} opponents"


def test_player_count_clamped():
    assert h.new_hand(seed=1, num_opponents=0).num_opponents() == 1
    assert h.new_hand(seed=1, num_opponents=99).num_opponents() == h.MAX_PLAYERS - 1


def test_preflop_has_no_board():
    st = h.new_hand(seed=3, num_opponents=3)
    assert st.public_state()["board"] == []
    assert st.public_state()["street_name"] == "preflop"


def test_blinds_seed_pot():
    st = h.new_hand(seed=3, num_opponents=2)
    assert st.pot >= h.SMALL_BLIND + h.BIG_BLIND


def test_hands_resolve_all_sizes():
    for n in range(1, 8):
        st = _autoplay(seed=10 + n, num_opponents=n)
        assert st.finished, f"{n} opponents did not resolve"
        assert st.result["pot"] > 0


def test_board_revealed_by_river_on_showdown():
    st = _autoplay(seed=1, num_opponents=2)
    if st.result["by"] == "showdown":
        assert len(st.result["board"]) == 5


def test_decisions_recorded_with_board():
    st = _autoplay(seed=7, num_opponents=3)
    assert len(st.history) >= 1
    for d in st.history:
        assert d.game == "holdem"
        assert len(d.hero_hole_cards) == 2
        assert d.action in [o["key"] for o in d.options]


def test_opponents_hidden_in_public_state():
    st = h.new_hand(seed=9, num_opponents=3)
    pub = st.public_state()
    for o in pub["opponents"]:
        assert "cards" not in o            # never leak hole cards
        assert o["hidden"] in (0, 2)


def test_showdown_matches_evaluator():
    st = _autoplay(seed=22, num_opponents=2)
    if st.result["by"] == "showdown":
        live = st.live_players()
        scores = {p.idx: sv.evaluate_5plus(p.hole + st.board) for p in live}
        best = max(scores.values())
        hero_best = scores.get(0) == best
        assert st.result["winner_is_hero"] == hero_best


def test_illegal_action_rejected():
    st = h.new_hand(seed=4, num_opponents=2)
    try:
        h.act(st, "definitely_not_an_option")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# Coach integration (grading the holdem hand)
# --------------------------------------------------------------------------- #

def test_coach_grades_holdem_hand():
    st = _autoplay(seed=7, num_opponents=4)
    rep = cm.full_report(st.history, coach=cm.LibraryMarketsCoach())
    assert len(rep["streets"]) == len(st.history)
    for s in rep["streets"]:
        assert 0.0 <= s["equity"] <= 1.0
        assert 0.0 <= s["adherence"] <= 100.0
    assert rep["translation"]["question"]


# --------------------------------------------------------------------------- #
def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}"); passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}"); traceback.print_exc(); failed += 1
    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
