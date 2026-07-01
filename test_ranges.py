"""
test_ranges.py — Conditioned equity & MDF continue ranges
=========================================================

Run with:  python3 test_ranges.py

Monte-Carlo numbers use modest iteration counts, so assertions are directional
(inequalities with margins) rather than exact — that is what keeps them robust.
"""

from __future__ import annotations

import traceback

import ranges as R


# --------------------------------------------------------------------------- #
# MDF / pot-odds primitives
# --------------------------------------------------------------------------- #
def test_mdf_values():
    assert abs(R.mdf(50, 100) - 2 / 3) < 1e-9       # half-pot -> defend 2/3
    assert abs(R.mdf(100, 100) - 0.5) < 1e-9        # pot      -> defend 1/2
    assert R.mdf(0, 100) == 1.0                      # no bet   -> defend all


def test_required_equity():
    assert abs(R.required_equity(50, 100) - 0.25) < 1e-9    # 50 to win 200
    assert abs(R.required_equity(100, 100) - 1 / 3) < 1e-6  # 100 to win 300


def test_slice_continue_keeps_strongest_villain():
    # Ascending hero equity; villain continues with the hands hero does WORST
    # against (front of the list).
    samples = [(0.2, 1.0), (0.4, 1.0), (0.6, 1.0), (0.8, 1.0)]
    vs_cont, vs_fold, n = R.slice_continue(samples, 4.0, 0.5)
    assert abs(vs_cont - 0.3) < 1e-9, vs_cont        # mean of 0.2, 0.4
    assert abs(vs_fold - 0.7) < 1e-9, vs_fold        # mean of 0.6, 0.8
    assert vs_cont < vs_fold


# --------------------------------------------------------------------------- #
# Conditioned equity behaviour
# --------------------------------------------------------------------------- #
def test_vs_random_matches_known_equities():
    # AA crushes; 72o is trash — vs a (near) full random range.
    aa = R.holdem_conditioned_equity(["As", "Ad"], bet=1, pot=100000,
                                     iterations=150, seed=1)
    junk = R.holdem_conditioned_equity(["7c", "2d"], bet=1, pot=100000,
                                       iterations=150, seed=1)
    assert aa.vs_random > 0.80, aa.vs_random
    assert junk.vs_random < 0.45, junk.vs_random


def test_marginal_hand_drops_when_called():
    # K2o: a coin flip vs a random hand, but a dog vs the range that continues.
    ce = R.holdem_conditioned_equity(["Ks", "2d"], bet=50, pot=100,
                                     iterations=140, seed=2)
    assert ce.vs_continue < ce.vs_random - 0.03, (ce.vs_random, ce.vs_continue)
    assert ce.vs_folded > ce.vs_random, (ce.vs_folded, ce.vs_random)


def test_bigger_bet_tightens_continue_range():
    half = R.holdem_conditioned_equity(["Ks", "2d"], bet=50, pot=100,
                                       iterations=140, seed=3)
    potb = R.holdem_conditioned_equity(["Ks", "2d"], bet=100, pot=100,
                                       iterations=140, seed=3)
    # A bigger bet => tighter continue range => lower equity when called.
    assert potb.vs_continue < half.vs_continue + 0.005, (half.vs_continue, potb.vs_continue)
    assert potb.mdf < half.mdf


def test_strong_hand_barely_moves_when_called():
    ce = R.holdem_conditioned_equity(["As", "Ad"], bet=100, pot=100,
                                     iterations=140, seed=4)
    assert ce.vs_continue > 0.74, ce.vs_continue     # AA still dominates the callers


def test_stud_conditioning_runs_and_drops():
    ce = R.stud_conditioned_equity(["Ks", "2d", "9h"], ["Ah", "Td"],
                                   bet=50, pot=100, iterations=60,
                                   max_combos=120, seed=5)
    assert 0.0 <= ce.vs_continue <= 1.0
    assert ce.vs_continue <= ce.vs_random + 0.02   # conditioning never helps a weak hand


# --------------------------------------------------------------------------- #
# Multiway conditioning
# --------------------------------------------------------------------------- #
def test_multiway_equity_falls_with_more_opponents():
    eqs = []
    for n in (1, 3, 5):
        g = R.holdem_multiway_grid(["Ks", "2d"], [], n, iterations=700, seed=1)
        eqs.append(g.vs_random)
    assert eqs[0] > eqs[1] > eqs[2], eqs          # more opponents => less equity
    assert eqs[2] < 0.30, eqs                     # K2o vs 5 random is weak


def test_multiway_grid_options_and_fold_equity():
    g = R.holdem_multiway_grid(["As", "Ad"], ["Kc", "7d", "2h"], 3,
                               iterations=700, seed=2)
    # AA is strong: betting beats checking, and equity stays high vs callers.
    bet = g.ev_option({"key": "bet", "label": "b", "amount": 66.0}, 100, 0)
    chk = g.ev_option({"key": "check", "label": "c", "amount": 0.0}, 100, 0)
    assert bet > chk, (bet, chk)
    assert g.equity_when_called(66, 100) > 0.55
    assert 0.0 <= g.fold_all_prob(66, 100) <= 1.0


def test_multiway_fold_equity_grows_with_bet():
    g = R.holdem_multiway_grid(["Ks", "2d"], ["Qh", "7c", "3s"], 3,
                               iterations=700, seed=3)
    small = g.fold_all_prob(33, 100)
    big = g.fold_all_prob(150, 100)
    assert big >= small, (small, big)             # bigger bet folds more of the field


def test_stud_multiway_grid_runs():
    g = R.stud_multiway_grid(["Ah", "Kh", "7h"], [["Qs", "Qd", "3c"],
                                                  ["8s", "8d", "4h"]],
                             iterations=400, seed=4)
    assert 0.0 <= g.vs_random <= 1.0
    assert 0.0 <= g.equity_when_called(50, 100) <= 1.0


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
