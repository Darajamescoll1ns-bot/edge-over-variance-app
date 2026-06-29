"""
test_scoring.py — Validation suite for the scoring engine
==========================================================

Run with:  python3 test_scoring.py        (no third-party test runner needed)

Covers each of the five dimensions plus the edge cases the roadmap flagged:
  * Brier at the extremes (p=0 and p=1)
  * single-class outcomes (all wins / all losses) -> calibration undefined
  * trades with no plan reference / off-plan entries
  * too few points for the sizing regression
  * no prior outcomes -> outcome-independence undefined

This is a plain-stdlib harness: each test is a function asserting expectations;
a tiny runner prints PASS/FAIL and exits non-zero if anything fails, so it can
gate CI later.
"""

from __future__ import annotations

import math
import traceback

import decision_schema_stdlib as sch
import scoring as sc


# --------------------------------------------------------------------------- #
# Builders — keep tests terse and readable.
# --------------------------------------------------------------------------- #

def poker(seq, *, ev_loss=0.0, pot=100.0, won=None, p=None, risk=0.05,
          latency=8000, eq=None, realized=None):
    return sch.Decision(
        decision_id=f"p{seq}", user_id="dara", session_id="t",
        domain=sch.Domain.POKER,
        timestamp=sch.datetime(2026, 6, 17, 14, seq % 60, 0),
        sequence_index=seq,
        context=sch.PokerContext(pot_size=pot),
        action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
        sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips", risk_fraction=risk),
        reference_policy=sch.SolverReference(
            best_action="call", best_action_ev=max(ev_loss, 1.0),
            taken_action_ev=max(ev_loss, 1.0) - ev_loss, ev_loss=ev_loss, true_equity=eq),
        decision_latency_ms=latency,
        ex_ante_estimate=(sch.ExAnteEstimate(win_probability=p,
                          target_definition="showdown") if p is not None else None),
        outcome=(sch.Outcome(resolved=True, won=won,
                 realized_value=(realized if realized is not None
                                 else (10 if won else -10)))
                 if won is not None else None),
    )


def trade(seq, *, on_plan=True, violations=None, expectancy=0.3, risk=0.01,
          won=None, p=None, latency=5000):
    return sch.Decision(
        decision_id=f"t{seq}", user_id="dara", session_id="t",
        domain=sch.Domain.TRADING,
        timestamp=sch.datetime(2026, 6, 17, 9, seq % 60, 0),
        sequence_index=seq,
        context=sch.TradingContext(account_equity=100_000.0),
        action_taken=sch.TradingAction(side=sch.TradeSide.BUY, quantity=1.0),
        sizing=sch.Sizing(absolute_size=1.0, size_unit="contracts", risk_fraction=risk),
        reference_policy=sch.PlanReference(
            was_on_plan=on_plan, setup_id="s", setup_expectancy=expectancy,
            process_violations=list(violations or [])),
        decision_latency_ms=latency,
        ex_ante_estimate=(sch.ExAnteEstimate(win_probability=p,
                          target_definition="+2R") if p is not None else None),
        outcome=(sch.Outcome(resolved=True, won=won,
                 realized_value=(10 if won else -10)) if won is not None else None),
    )


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Dimension 1 — Policy adherence (poker EV-loss branch)
# --------------------------------------------------------------------------- #

def test_poker_perfect_policy_scores_100():
    prof, _ = sc.score_session([poker(i, ev_loss=0.0) for i in range(3)])
    assert approx(prof.policy_adherence, 100.0), prof.policy_adherence


def test_poker_ev_loss_normalized_by_pot():
    # ev_loss 25 on a pot of 100 -> loss 0.25 -> adherence 75.
    loss, _ = sc.score_policy_adherence(sc._wrap(poker(0, ev_loss=25.0, pot=100.0)))
    assert approx(loss, 0.25), loss


def test_poker_ev_loss_caps_at_full_pot():
    # Giving up more than a pot of EV still caps the normalized loss at 1.0.
    loss, _ = sc.score_policy_adherence(sc._wrap(poker(0, ev_loss=500.0, pot=100.0)))
    assert approx(loss, 1.0), loss


# --------------------------------------------------------------------------- #
# Dimension 1 — Policy adherence (trading plan branch)
# --------------------------------------------------------------------------- #

def test_trading_on_plan_no_violations_scores_100():
    prof, _ = sc.score_session([trade(i, on_plan=True) for i in range(3)])
    assert approx(prof.policy_adherence, 100.0), prof.policy_adherence


def test_trading_off_plan_is_maximal_loss():
    loss, viol = sc.score_policy_adherence(sc._wrap(trade(0, on_plan=False)))
    assert approx(loss, 1.0), loss
    assert "off_plan_entry" in viol, viol


def test_trading_violations_erode_adherence():
    loss, _ = sc.score_policy_adherence(
        sc._wrap(trade(0, on_plan=True, violations=["no_stop", "moved_stop"])))
    assert approx(loss, 0.5), loss   # 2 * 0.25


def test_missing_reference_returns_none():
    d = sc._wrap(poker(0))
    d.reference_policy = None
    loss, viol = sc.score_policy_adherence(d)
    assert loss is None and viol == []


# --------------------------------------------------------------------------- #
# Dimensions 2 & 3 — Calibration / resolution via Brier decomposition
# --------------------------------------------------------------------------- #

def test_brier_identity_holds():
    # BS == Reliability - Resolution + Uncertainty must hold exactly.
    f = [0.1, 0.4, 0.6, 0.9, 0.3, 0.7]
    o = [0, 0, 1, 1, 0, 1]
    d = sc.brier_decomposition(f, o)
    assert approx(d["brier"], d["reliability"] - d["resolution"] + d["uncertainty"], 1e-9), d


def test_brier_extremes_p0_p1_perfect_forecaster():
    # Forecasts of 0 and 1 that are always correct -> brier 0, reliability 0.
    f = [1.0, 0.0, 1.0, 0.0]
    o = [1, 0, 1, 0]
    d = sc.brier_decomposition(f, o)
    assert approx(d["brier"], 0.0), d
    assert approx(d["reliability"], 0.0), d


def test_single_class_outcomes_calibration_undefined():
    # All wins -> uncertainty 0 -> calibration & resolution undefined (None).
    prof, _ = sc.score_session([poker(i, won=True, p=0.6) for i in range(4)])
    assert prof.calibration is None, prof.calibration
    assert prof.resolution is None, prof.resolution


def test_wellcalibrated_session_scores_high_calibration():
    # Forecasts equal to realized base rates within bins -> low reliability.
    decs = []
    # 10 decisions at p=0.5 with exactly 5 wins -> bin is perfectly calibrated.
    for i in range(10):
        decs.append(poker(i, won=(i % 2 == 0), p=0.5, pot=100, ev_loss=0.0))
    prof, _ = sc.score_session(decs)
    assert prof.calibration is not None and prof.calibration >= 90.0, prof.calibration


# --------------------------------------------------------------------------- #
# Dimension 3 — Sizing discipline (edge-to-size regression)
# --------------------------------------------------------------------------- #

def test_sizing_tracks_edge_scores_high():
    # risk rises monotonically with edge -> strong positive correlation.
    decs = [poker(i, eq=0.5 + 0.05 * i, risk=0.02 + 0.01 * i, won=(i % 2 == 0), p=0.5)
            for i in range(5)]
    prof, _ = sc.score_session(decs)
    assert prof.sizing_discipline is not None and prof.sizing_discipline >= 95.0, \
        prof.sizing_discipline


def test_sizing_inverted_scores_zero():
    # Bigger size on smaller edge -> negative correlation -> 0.
    decs = [poker(i, eq=0.5 + 0.05 * i, risk=0.12 - 0.01 * i, won=(i % 2 == 0), p=0.5)
            for i in range(5)]
    prof, _ = sc.score_session(decs)
    assert approx(prof.sizing_discipline, 0.0), prof.sizing_discipline


def test_too_few_points_no_sizing_score():
    decs = [poker(i, eq=0.6, risk=0.05) for i in range(2)]   # below MIN_SIZING_POINTS
    prof, _ = sc.score_session(decs)
    assert prof.sizing_discipline is None, prof.sizing_discipline


# --------------------------------------------------------------------------- #
# Dimension 4 — Outcome-independence
# --------------------------------------------------------------------------- #

def test_outcome_independence_stable_scores_100():
    # Same quality regardless of prior result -> deficit 0 -> 100.
    decs = [poker(i, ev_loss=0.0, won=(i % 2 == 0), p=0.5) for i in range(6)]
    prof, _ = sc.score_session(decs)
    assert prof.outcome_independence is not None
    assert prof.outcome_independence >= 99.0, prof.outcome_independence


def test_outcome_dependence_detected():
    # Decisions following a loss are much worse (high ev_loss) -> low score.
    # Evens win, odds lose. A decision "follows a loss" when its previous index
    # lost (was odd) -> i.e. even indices >= 2. Put the high ev_loss there.
    decs = []
    for i in range(8):
        follows_loss = i > 0 and ((i - 1) % 2 == 1)
        decs.append(poker(i,
                          ev_loss=(60.0 if follows_loss else 0.0),
                          pot=100.0,
                          won=(i % 2 == 0),     # evens win, odds lose
                          p=0.5))
    prof, _ = sc.score_session(decs)
    assert prof.outcome_independence is not None
    assert prof.outcome_independence < 60.0, prof.outcome_independence


def test_no_prior_outcomes_independence_undefined():
    decs = [poker(i, ev_loss=0.0, p=0.5) for i in range(5)]   # no outcomes resolved
    prof, _ = sc.score_session(decs)
    assert prof.outcome_independence is None, prof.outcome_independence


# --------------------------------------------------------------------------- #
# Dimension 5 — Tilt control
# --------------------------------------------------------------------------- #

def test_tilt_stable_under_stress_scores_100():
    # Constant quality, increasing loss streak -> non-positive correlation.
    decs = [poker(i, ev_loss=0.0, won=False, p=0.5) for i in range(6)]
    prof, _ = sc.score_session(decs)
    assert prof.tilt_control is not None and prof.tilt_control >= 99.0, prof.tilt_control


def test_tilt_quality_collapses_under_stress():
    # As the losing streak grows, ev_loss grows too -> positive corr -> low score.
    decs = []
    for i in range(8):
        decs.append(poker(i, ev_loss=float(i) * 12.0, pot=100.0, won=False, p=0.5,
                          latency=8000))
    prof, _ = sc.score_session(decs)
    assert prof.tilt_control is not None and prof.tilt_control < 30.0, prof.tilt_control


# --------------------------------------------------------------------------- #
# Cross-cutting — JSON dict input parity with dataclass input
# --------------------------------------------------------------------------- #

def test_json_dict_input_matches_dataclass():
    decs = [poker(i, ev_loss=10.0 * i, pot=100, won=(i % 2 == 0), p=0.5, eq=0.5 + 0.02 * i)
            for i in range(5)]
    prof_obj, _ = sc.score_session(decs)
    # Round-trip through JSON to dicts, then score again.
    import json
    dicts = [json.loads(sch.decision_to_json(d)) for d in decs]
    prof_json, _ = sc.score_session(dicts)
    for field in ("policy_adherence", "calibration", "sizing_discipline"):
        a, b = getattr(prof_obj, field), getattr(prof_json, field)
        assert (a is None and b is None) or approx(a, b, 1e-6), (field, a, b)


# --------------------------------------------------------------------------- #
# Numerical helper unit checks
# --------------------------------------------------------------------------- #

def test_pearson_perfect_positive():
    assert approx(sc.pearson([1, 2, 3], [2, 4, 6]), 1.0)


def test_pearson_zero_variance_none():
    assert sc.pearson([1, 1, 1], [2, 4, 6]) is None


def test_ols_recovers_line():
    fit = sc.ols([0, 1, 2, 3], [1, 3, 5, 7])   # y = 2x + 1
    assert fit is not None
    slope, intercept, r = fit
    assert approx(slope, 2.0) and approx(intercept, 1.0) and approx(r, 1.0)


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
