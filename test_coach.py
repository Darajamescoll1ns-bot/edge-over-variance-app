"""
test_coach.py — Validation for grading + markets translation
=============================================================

Run with:  python3 test_coach.py
"""

from __future__ import annotations

import os
import traceback

import coach as cm
import stud_game as game


# --------------------------------------------------------------------------- #
# EV model correctness (the bug-prone part)
# --------------------------------------------------------------------------- #

def _facing_bet_opts():
    return [
        {"key": "fold", "label": "Fold", "amount": 0.0},
        {"key": "call", "label": "Call 1", "amount": 1.0},
        {"key": "raise_small", "label": "Raise to 3", "amount": 3.0},
    ]


def _open_opts():
    return [
        {"key": "check", "label": "Check", "amount": 0.0},
        {"key": "bet_100", "label": "Bet 6 (pot)", "amount": 6.0},
    ]


def test_call_below_potodds_is_negative():
    # Pot 1.5, to_call 1.0 -> breakeven 0.40. Equity 0.30 -> calling is -EV,
    # folding (0) should be the best option.
    evs = cm._option_evs(_facing_bet_opts(), e=0.30, pot=1.5)
    assert evs["Call 1"] < 0, evs
    assert evs["Fold"] == 0.0
    assert max(evs, key=evs.get) != "Call 1"


def test_call_above_potodds_is_positive():
    evs = cm._option_evs(_facing_bet_opts(), e=0.55, pot=1.5)
    assert evs["Call 1"] > evs["Fold"], evs


def test_strong_equity_prefers_value_bet_over_check():
    evs = cm._option_evs(_open_opts(), e=0.75, pot=6.0)
    assert evs["Bet 6 (pot)"] > evs["Check"], evs


def test_archetype_classification():
    assert cm._archetype("fold", 0.25) == "disciplined_fold"
    assert cm._archetype("fold", 0.70) == "premature_fold"
    assert cm._archetype("bet", 0.70) == "press_edge"
    assert cm._archetype("check", 0.70) == "missed_value"
    assert cm._archetype("call", 0.30) == "chase"
    assert cm._archetype("call", 0.50) == "marginal_continue"


# --------------------------------------------------------------------------- #
# Grading a real hand
# --------------------------------------------------------------------------- #

def _play(seed):
    st = game.new_hand(seed=seed)
    steps = 0
    while not st.finished and steps < 40:
        keys = [o["key"] for o in st.awaiting["options"]]
        choice = "call" if "call" in keys else ("check" if "check" in keys else keys[0])
        st = game.act(st, choice)
        steps += 1
    return st


def test_grade_hand_produces_one_grade_per_decision():
    st = _play(7)
    grades = cm.grade_hand(st.history)
    assert len(grades) == len(st.history)
    for g in grades:
        assert 0.0 <= g.equity <= 1.0
        assert 0.0 <= g.adherence <= 100.0
        assert g.best_action in g.option_evs


def test_overview_and_key_decision():
    st = _play(7)
    grades = cm.grade_hand(st.history)
    ov = cm.hand_overview(grades)
    assert ov["n_decisions"] == len(grades)
    assert ov["average_adherence"] is not None
    key = cm.pick_key_decision(grades)
    assert key is not None


def test_full_report_shape():
    st = _play(11)
    rep = cm.full_report(st.history, coach=cm.LibraryMarketsCoach())
    assert "overview" in rep and "streets" in rep and "translation" in rep
    t = rep["translation"]
    for f in ("analogy", "question", "model_answer", "lesson"):
        assert f in t and t[f]


# --------------------------------------------------------------------------- #
# Markets coach backends
# --------------------------------------------------------------------------- #

def test_library_coach_covers_every_archetype():
    lib = cm.LibraryMarketsCoach()
    for arche in cm.ARCHETYPE_MARKET:
        g = cm.StreetGrade(street=5, street_name="5th", action="call", options=["call"],
                           equity=0.5, option_evs={"call": 0.0}, best_action="call",
                           ev_loss_normalized=0.0, adherence=100.0, archetype=arche, why="")
        out = lib.translate([g], g)
        assert out["question"] and out["model_answer"]


def test_llm_coach_falls_back_without_key(monkeypatch=None):
    # Ensure no key is set in this test process.
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        llm = cm.LLMMarketsCoach()
        assert llm.available is False
        g = cm.StreetGrade(street=5, street_name="5th", action="call", options=["call"],
                           equity=0.5, option_evs={"call": 0.0}, best_action="call",
                           ev_loss_normalized=0.0, adherence=100.0,
                           archetype="chase", why="")
        out = llm.translate([g], g)
        assert out["source"] == "library"          # fell back
        fb = llm.evaluate_answer("q", "model", "cut the position, honor the stop")
        assert "model_answer" in fb
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_get_coach_returns_library_without_key():
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        assert isinstance(cm.get_coach(), cm.LibraryMarketsCoach)
    finally:
        if old is not None:
            os.environ["ANTHROPIC_API_KEY"] = old


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
