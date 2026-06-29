"""
test_solver.py — Validation for the hand evaluator and Monte Carlo equity
=========================================================================

Run with:  python3 test_solver.py

The evaluator is the riskiest piece for correctness, so it gets exhaustive
known-hand checks. Equity tests are statistical, so they use a fixed seed and
generous margins to stay deterministic and non-flaky.
"""

from __future__ import annotations

import traceback

import solver as sv


def C(*names):
    return [sv.parse_card(n) for n in names]


# --------------------------------------------------------------------------- #
# Hand category ordering
# --------------------------------------------------------------------------- #

def test_category_ordering():
    royal = sv.evaluate_5plus(C("As", "Ks", "Qs", "Js", "Ts", "2d", "3c"))
    quads = sv.evaluate_5plus(C("9s", "9d", "9h", "9c", "Ks", "2d", "3c"))
    boat = sv.evaluate_5plus(C("9s", "9d", "9h", "Kc", "Ks", "2d", "3c"))
    flush = sv.evaluate_5plus(C("As", "Js", "9s", "5s", "2s", "Kd", "3c"))
    straight = sv.evaluate_5plus(C("9s", "8d", "7h", "6c", "5s", "Kd", "2c"))
    trips = sv.evaluate_5plus(C("9s", "9d", "9h", "Kc", "5s", "2d", "3c"))
    two_pair = sv.evaluate_5plus(C("9s", "9d", "Kc", "Ks", "5s", "2d", "3c"))
    pair = sv.evaluate_5plus(C("9s", "9d", "Kc", "8s", "5s", "2d", "3c"))
    high = sv.evaluate_5plus(C("9s", "7d", "Kc", "8s", "5s", "2d", "3c"))
    order = [high, pair, two_pair, trips, straight, flush, boat, quads, royal]
    for a, b in zip(order, order[1:]):
        assert a < b, (a, b)
    assert royal[0] == 8 and quads[0] == 7 and boat[0] == 6


def test_wheel_straight_recognized():
    wheel = sv.evaluate_5plus(C("As", "2d", "3h", "4c", "5s", "Kd", "9c"))
    assert wheel[0] == 4, wheel          # it's a straight
    assert wheel[1] == 5, wheel          # five-high, not ace-high


def test_flush_beats_straight():
    flush = sv.evaluate_5plus(C("As", "Js", "9s", "5s", "2s"))
    straight = sv.evaluate_5plus(C("9d", "8c", "7h", "6c", "5s"))
    assert flush > straight


def test_straight_flush_beats_quads():
    sf = sv.evaluate_5plus(C("9s", "8s", "7s", "6s", "5s", "Ad", "Ac"))
    quads = sv.evaluate_5plus(C("Ad", "Ac", "Ah", "As", "Ks", "2d", "3c"))
    assert sf > quads


def test_higher_pair_wins():
    aces = sv.evaluate_5plus(C("As", "Ad", "Kc", "8s", "5s"))
    kings = sv.evaluate_5plus(C("Ks", "Kd", "Ac", "8s", "5s"))
    assert aces > kings


def test_kicker_breaks_tie():
    a = sv.evaluate_5plus(C("As", "Ad", "Kc", "8s", "5s"))
    b = sv.evaluate_5plus(C("Ac", "Ah", "Qc", "8d", "5h"))
    assert a > b           # both pair of aces; K kicker beats Q


def test_best_five_of_seven_selected():
    # Seven cards containing a flush AND a pair: must pick the flush.
    h = sv.evaluate_5plus(C("As", "Ks", "9s", "5s", "2s", "Ad", "Ah"))
    assert h[0] == 5, h    # flush, not trips/pair


# --------------------------------------------------------------------------- #
# Monte Carlo equity
# --------------------------------------------------------------------------- #

def test_strong_start_beats_weak_headsup():
    strong = sv.monte_carlo_equity(C("As", "Ah", "Ad"), num_opponents=1,
                                   iterations=3000, seed=7)
    weak = sv.monte_carlo_equity(C("7c", "2d", "9h"), num_opponents=1,
                                 iterations=3000, seed=7)
    assert strong > 0.85, strong
    assert weak < strong


def test_equity_falls_with_more_opponents():
    one = sv.monte_carlo_equity(C("As", "Ah", "Kd"), num_opponents=1,
                                iterations=3000, seed=11)
    five = sv.monte_carlo_equity(C("As", "Ah", "Kd"), num_opponents=5,
                                 iterations=3000, seed=11)
    assert one > five, (one, five)


def test_equity_in_unit_interval():
    e = sv.monte_carlo_equity(C("Ts", "Td", "4h"), num_opponents=2,
                              iterations=1500, seed=3)
    assert 0.0 <= e <= 1.0


def test_dead_cards_removed_from_deck():
    # Holding trip aces, the case ace dead -> opponents can't make a better set
    # of aces; equity should remain very high and not error.
    e = sv.monte_carlo_equity(C("As", "Ah", "Ad"), dead_cards=C("Ac"),
                              num_opponents=1, iterations=2000, seed=5)
    assert e > 0.85, e


# --------------------------------------------------------------------------- #
# Provider
# --------------------------------------------------------------------------- #

def test_provider_reference_shape():
    p = sv.MonteCarloEquityProvider(iterations=2000, seed=1)
    ref = p.reference(C("As", "Ah", "Ks"), "call", pot_size=100.0, to_call=20.0,
                      num_opponents=1)
    assert ref.reference_type.value == "solver_approx"
    assert 0.0 <= ref.true_equity <= 1.0
    assert ref.ev_loss >= 0.0
    assert ref.best_action in ref.strategy
    assert ref.best_action_ev >= ref.taken_action_ev - 1e-9


def test_provider_flags_clear_misplay():
    # Folding rolled-up aces getting a tiny price is a large EV error.
    p = sv.MonteCarloEquityProvider(iterations=2500, seed=2)
    ref = p.reference(C("As", "Ah", "Ad"), "fold", pot_size=100.0, to_call=5.0,
                      num_opponents=1)
    assert ref.best_action != "fold"
    assert ref.ev_loss > 0.0


def test_external_provider_raises_until_configured():
    ext = sv.ExternalSolverProvider()
    try:
        ext.reference(C("As", "Ah"), "call", pot_size=10, to_call=2)
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
