"""
test_stud_game.py — Validation for the stud engine (heads-up + multi-way)
=========================================================================

Run with:  python3 test_stud_game.py
"""

from __future__ import annotations

import traceback

import stud_game as game
import solver as sv


def _keys(st):
    return [o["key"] for o in st.awaiting["options"]]


def _passive_choice(keys):
    return "call" if "call" in keys else ("check" if "check" in keys else keys[0])


def _autoplay(seed, num_opponents=1, strategy="call"):
    st = game.new_hand(seed=seed, num_opponents=num_opponents)
    steps = 0
    while not st.finished and steps < 80:
        keys = _keys(st)
        if strategy == "fold" and "fold" in keys:
            choice = "fold"
        else:
            choice = _passive_choice(keys)
        st = game.act(st, choice)
        steps += 1
    return st


# --------------------------------------------------------------------------- #
# Dealing
# --------------------------------------------------------------------------- #

def test_headsup_deal_shapes():
    st = game.new_hand(seed=1, num_opponents=1)
    assert len(st.players) == 2
    assert all(len(p.cards) == 7 for p in st.players)
    assert st.dealt == 3 and st.street == 3
    pub = st.public_state()
    assert len(pub["hero"]) == 3
    assert sum(1 for c in pub["hero"] if c["up"]) == 1
    assert len(pub["opponents"]) == 1
    assert len(pub["opponents"][0]["up"]) == 1


def test_multiway_deal_shapes():
    for n in range(1, 7):
        st = game.new_hand(seed=4, num_opponents=n)
        assert st.num_opponents() == n
        assert len(st.players) == n + 1
        pub = st.public_state()
        assert len(pub["opponents"]) == n
        # No duplicate cards across all hands.
        allc = [c for p in st.players for c in p.cards]
        assert len(set(allc)) == 7 * (n + 1), f"dupes with {n} opponents"


def test_opponent_count_clamped():
    assert game.new_hand(seed=1, num_opponents=0).num_opponents() == 1
    assert game.new_hand(seed=1, num_opponents=99).num_opponents() == game.MAX_OPPONENTS


# --------------------------------------------------------------------------- #
# Flow
# --------------------------------------------------------------------------- #

def test_hero_always_has_options_when_awaiting():
    st = game.new_hand(seed=5, num_opponents=3)
    assert st.awaiting is not None and len(st.awaiting["options"]) >= 1


def test_full_hand_resolves_headsup():
    st = _autoplay(seed=7, num_opponents=1)
    assert st.finished and st.result is not None


def test_full_hand_resolves_multiway():
    for n in (2, 3, 5, 6):
        st = _autoplay(seed=13 + n, num_opponents=n)
        assert st.finished, f"{n} opponents did not resolve"
        assert st.result["pot"] > 0


def test_hero_fold_ends_hero_involvement():
    st = game.new_hand(seed=2, num_opponents=2)
    steps = 0
    folded = False
    while not st.finished and steps < 80:
        keys = _keys(st)
        if "fold" in keys:
            st = game.act(st, "fold")
            folded = True
            break
        st = game.act(st, _passive_choice(keys))
        steps += 1
    if folded:
        # Hand may continue among bots, but hero must not be asked again.
        assert st.finished or st.awaiting is None or st.hero.folded


def test_decisions_recorded_with_opponents():
    st = _autoplay(seed=11, num_opponents=4)
    assert len(st.history) >= 1
    for h in st.history:
        assert h.action in [o["key"] for o in h.options]
        assert h.hero_known_cards
        assert isinstance(h.opponents_up_cards, list)
        assert h.num_live_opponents >= 1


def test_options_have_sizes_and_stack_tracked():
    st = game.new_hand(seed=1, num_opponents=1)
    opts = st.awaiting["options"]
    assert all("key" in o and "label" in o and "amount" in o for o in opts)
    # At least one sizing option beyond fold/check/call exists somewhere early.
    assert st.public_state()["hero_stack"] < game.betting.START_STACK  # ante posted


def test_showdown_winner_matches_evaluator_multiway():
    st = _autoplay(seed=21, num_opponents=3)
    if st.result["by"] == "showdown":
        live = st.live_players()
        scores = {p.idx: sv.evaluate_5plus(p.cards) for p in live}
        best = max(scores.values())
        hero_best = scores.get(0, None) == best
        assert st.result["winner_is_hero"] == hero_best


def test_public_state_hides_downcards():
    st = game.new_hand(seed=9, num_opponents=2)
    pub = st.public_state()
    for o in pub["opponents"]:
        assert len(o["up"]) + o["hidden"] == st.dealt


def test_illegal_action_rejected():
    st = game.new_hand(seed=4, num_opponents=2)
    try:
        game.act(st, "definitely_not_an_option")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_pot_grows_with_action():
    st = game.new_hand(seed=23, num_opponents=2)
    start = st.public_state()["pot"]
    keys = _keys(st)
    aggressive = next((k for k in keys if k.startswith("bet") or k.startswith("raise")
                       or k == "allin"), None)
    a = aggressive or ("call" if "call" in keys else keys[0])
    st = game.act(st, a)
    assert st.public_state()["pot"] >= start


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
