"""
test_rollout.py — Multi-street EV, implied & reverse-implied odds
=================================================================

Run with:  python3 test_rollout.py
"""

from __future__ import annotations

import traceback

import rollout as RO
import ranges as R
import solver as sv


def test_call_ev_has_expected_keys():
    res = RO.holdem_call_ev(["Ah", "Kh"], ["Qh", "7h", "2c"],
                            pot=100, to_call=50, trials=200, seed=1)
    for k in ("ev_call", "ev_immediate", "implied_delta",
              "equity_realised", "required_equity"):
        assert k in res, k


def test_flush_draw_gains_from_implied_odds():
    # A nut flush draw is worth MORE than its bare pot-odds EV: future streets
    # let it extract extra when it comes in.
    res = RO.holdem_call_ev(["Ah", "Kh"], ["Qh", "7h", "2c"],
                            pot=100, to_call=50, trials=500, seed=2)
    assert res["implied_delta"] > 3.0, res


def test_dominated_hand_pays_reverse_implied_odds():
    # Top pair, weak kicker vs a value-weighted betting range: looks fine on raw
    # showdown odds, but bleeds money paying off better hands across streets.
    board = R.as_cards(["Ks", "9s", "2h"])
    hero = R.as_cards(["Kd", "7c"])
    known = set(hero) | set(board)
    value_range = [c for c in R.holdem_combos(list(known))
                   if sv.evaluate_5plus(list(c) + board)[0] >= 1
                   and max(c[0][0], c[1][0]) >= 9]
    res = RO.holdem_call_ev(["Kd", "7c"], ["Ks", "9s", "2h"],
                            pot=100, to_call=60, villain_range_combos=value_range,
                            trials=500, seed=3)
    assert res["implied_delta"] < -5.0, res        # reverse implied odds bite


def test_made_nut_hand_not_penalised():
    # A made flush (very strong) should not have a large NEGATIVE implied delta —
    # it is not the one paying off.
    res = RO.holdem_call_ev(["Ah", "Th"], ["Kh", "7h", "2h"],
                            pot=100, to_call=50, trials=400, seed=4)
    assert res["implied_delta"] > -3.0, res
    assert res["equity_realised"] > 0.85, res


# --------------------------------------------------------------------------- #
# Stud rollout
# --------------------------------------------------------------------------- #
def test_stud_call_ev_keys():
    res = RO.stud_call_ev(["Ah", "Kh", "7h", "2h", "9c"], ["Qs", "Qd", "3c"],
                          pot=100, to_call=50, trials=200, seed=1)
    for k in ("ev_call", "ev_immediate", "implied_delta",
              "equity_realised", "required_equity"):
        assert k in res, k


def test_stud_made_hand_extracts_implied_value():
    # Trip aces by 5th street: a monster that value-bets future streets and gets
    # paid -> strong positive implied delta.
    res = RO.stud_call_ev(["Ah", "Ad", "As", "2c", "9h"], ["Ks", "Kd", "3c"],
                          pot=100, to_call=50, trials=400, seed=2)
    assert res["equity_realised"] > 0.85, res
    assert res["implied_delta"] > 5.0, res


def test_stud_draw_equity_sane():
    res = RO.stud_call_ev(["Ah", "Kh", "7h", "2h", "9c"], ["Qs", "Qd", "3c"],
                          pot=100, to_call=50, trials=300, seed=3)
    assert 0.2 < res["equity_realised"] < 0.7, res


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
