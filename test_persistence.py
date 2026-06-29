"""
test_persistence.py — Validation suite for the Phase 2 data layer
=================================================================

Run with:  python3 test_persistence.py

Covers:
  * JSON -> Decision reconstruction fidelity (poker AND trading)
  * scoring parity: a reconstructed session scores identically to the original
  * Session ordering, rolling metrics, summary
  * save / load / list round-trip + index correctness
  * session-level and decision-level queries
"""

from __future__ import annotations

import json
import os
import tempfile
import traceback

import decision_schema_stdlib as sch
import scoring as sc
from session import Session, DIMENSIONS
import persistence as pz


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

def poker(seq, *, ev_loss=0.0, pot=100.0, won=None, p=0.6, risk=0.05, eq=0.6, latency=8000):
    return sch.Decision(
        decision_id=f"p{seq}", user_id="dara", session_id="S",
        domain=sch.Domain.POKER,
        timestamp=sch.datetime(2026, 6, 17, 14, seq % 60, 0), sequence_index=seq,
        context=sch.PokerContext(
            pot_size=pot, street="fifth", street_index=3,
            hero_down_cards=[sch.Card("A", "s"), sch.Card("K", "s")],
            hero_up_cards=[sch.Card("Q", "s")],
            legal_actions=[sch.PokerActionType.CALL, sch.PokerActionType.FOLD]),
        action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL, amount=20.0),
        sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips", risk_fraction=risk),
        reference_policy=sch.SolverReference(
            best_action="call", best_action_ev=max(ev_loss, 1.0),
            taken_action_ev=max(ev_loss, 1.0) - ev_loss, ev_loss=ev_loss, true_equity=eq),
        decision_latency_ms=latency,
        ex_ante_estimate=sch.ExAnteEstimate(win_probability=p, target_definition="showdown"),
        outcome=(sch.Outcome(resolved=True, won=won, realized_value=(10 if won else -10))
                 if won is not None else None),
    )


def trade(seq, *, on_plan=True, expectancy=0.3, risk=0.01, won=None, p=0.55):
    return sch.Decision(
        decision_id=f"t{seq}", user_id="dara", session_id="S",
        domain=sch.Domain.TRADING,
        timestamp=sch.datetime(2026, 6, 17, 9, seq % 60, 0), sequence_index=seq,
        context=sch.TradingContext(
            instrument=sch.Instrument(symbol="ES", asset_class=sch.AssetClass.FUTURE,
                                      multiplier=50.0, tick_size=0.25),
            decision_type=sch.TradeDecisionType.ENTRY, account_equity=100_000.0,
            market=sch.MarketSnapshot(price=5400.0, atr=22.0), setup_tag="breakout"),
        action_taken=sch.TradingAction(side=sch.TradeSide.BUY, quantity=1.0,
                                       price=5400.0, order_type="limit"),
        sizing=sch.Sizing(absolute_size=1.0, size_unit="contracts", risk_fraction=risk),
        reference_policy=sch.PlanReference(was_on_plan=on_plan, setup_id="s",
                                           setup_expectancy=expectancy, planned_stop=5389.0),
        decision_latency_ms=5000,
        ex_ante_estimate=sch.ExAnteEstimate(win_probability=p, target_definition="+2R"),
        outcome=(sch.Outcome(resolved=True, won=won, realized_value=(10 if won else -10))
                 if won is not None else None),
    )


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Reconstruction fidelity
# --------------------------------------------------------------------------- #

def test_poker_roundtrip_json_stable():
    d = poker(0, ev_loss=5.0, won=True)
    once = sch.decision_to_json(d)
    rebuilt = pz.decision_from_dict(json.loads(once))
    twice = sch.decision_to_json(rebuilt)
    assert json.loads(once) == json.loads(twice), "poker JSON not stable across rebuild"


def test_trading_roundtrip_json_stable():
    d = trade(0, on_plan=True, won=False)
    once = sch.decision_to_json(d)
    rebuilt = pz.decision_from_dict(json.loads(once))
    twice = sch.decision_to_json(rebuilt)
    assert json.loads(once) == json.loads(twice), "trading JSON not stable across rebuild"


def test_reconstruction_types():
    d = pz.decision_from_dict(json.loads(sch.decision_to_json(poker(0))))
    assert isinstance(d, sch.Decision)
    assert isinstance(d.context, sch.PokerContext)
    assert isinstance(d.reference_policy, sch.SolverReference)
    assert isinstance(d.context.hero_down_cards[0], sch.Card)
    t = pz.decision_from_dict(json.loads(sch.decision_to_json(trade(0))))
    assert isinstance(t.context, sch.TradingContext)
    assert isinstance(t.reference_policy, sch.PlanReference)
    assert isinstance(t.context.instrument, sch.Instrument)


def test_scoring_parity_after_reconstruction():
    decs = [poker(i, ev_loss=4.0 * i, won=(i % 2 == 0), p=0.5, eq=0.5 + 0.03 * i,
                  risk=0.03 + 0.01 * i) for i in range(6)]
    prof_orig, _ = sc.score_session(decs)
    rebuilt = [pz.decision_from_dict(json.loads(sch.decision_to_json(d))) for d in decs]
    prof_new, _ = sc.score_session(rebuilt)
    for dim in DIMENSIONS:
        a, b = getattr(prof_orig, dim), getattr(prof_new, dim)
        assert (a is None and b is None) or approx(a, b), (dim, a, b)


# --------------------------------------------------------------------------- #
# Session behaviour
# --------------------------------------------------------------------------- #

def test_session_orders_by_sequence_index():
    s = Session("S", "dara", "poker")
    for i in [3, 0, 2, 1]:
        s.add(poker(i, won=True))
    idxs = [d.sequence_index for d in s.decisions]
    assert idxs == [0, 1, 2, 3], idxs


def test_session_backfills_metadata():
    s = Session("S")            # no user/domain given
    s.add(poker(0, won=True))
    assert s.user_id == "dara"
    assert s.domain == "poker"


def test_rolling_series_length_matches():
    s = Session("S", "dara", "poker")
    for i in range(8):
        s.add(poker(i, ev_loss=2.0 * i, won=(i % 2 == 0)))
    series = s.dimension_series("policy_adherence", window=4)
    assert len(series) == 8


# --------------------------------------------------------------------------- #
# Storage round-trip
# --------------------------------------------------------------------------- #

def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as base:
        s = Session("sess-A", "dara", "poker")
        for i in range(5):
            s.add(poker(i, ev_loss=3.0 * i, won=(i % 2 == 0), eq=0.5 + 0.04 * i,
                        risk=0.02 + 0.01 * i))
        path = pz.save_session(s, base)
        assert os.path.exists(path)

        reloaded = pz.load_session_by_id("sess-A", base)
        assert len(reloaded) == 5
        assert reloaded.user_id == "dara"
        assert reloaded.domain == "poker"
        assert approx(reloaded.profile().policy_adherence, s.profile().policy_adherence)


def test_session_file_schema():
    with tempfile.TemporaryDirectory() as base:
        s = Session("sess-B", "dara", "poker")
        s.add(poker(0, won=True))
        path = pz.save_session(s, base)
        with open(path) as fh:
            payload = json.load(fh)
        for key in ("session_id", "user_id", "timestamp", "domain", "decisions"):
            assert key in payload, f"missing {key} in session file"
        assert isinstance(payload["decisions"], list)


def test_index_enables_listing_without_opening_files():
    with tempfile.TemporaryDirectory() as base:
        for j in range(3):
            s = Session(f"sess-{j}", "dara", "poker" if j < 2 else "trading")
            (s.add(poker(0, won=True)) if j < 2 else s.add(trade(0, won=True)))
            pz.save_session(s, base)
        assert os.path.exists(pz._index_path(base))
        poker_sessions = pz.list_sessions(base, domain="poker")
        assert len(poker_sessions) == 2, poker_sessions
        all_sessions = pz.list_sessions(base)
        assert len(all_sessions) == 3


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #

def test_session_level_query():
    with tempfile.TemporaryDirectory() as base:
        # Clean session.
        good = Session("good", "dara", "poker")
        for i in range(6):
            good.add(poker(i, ev_loss=0.0, won=(i % 2 == 0)))
        pz.save_session(good, base)
        # Tilting session: ev_loss grows with the losing streak.
        bad = Session("bad", "dara", "poker")
        for i in range(8):
            bad.add(poker(i, ev_loss=10.0 * i, pot=100.0, won=False))
        pz.save_session(bad, base)

        low_tilt = pz.query_sessions(base,
            predicate=lambda m: m["scores"]["tilt_control"] is not None
            and m["scores"]["tilt_control"] < 50)
        ids = {m["session_id"] for m in low_tilt}
        assert "bad" in ids and "good" not in ids, ids


def test_decision_level_query_sizing():
    with tempfile.TemporaryDirectory() as base:
        s = Session("S", "dara", "poker")
        # Disciplined sizing on all but the last, which massively over-bets
        # relative to its edge -> large standardized residual.
        for i in range(6):
            s.add(poker(i, eq=0.5 + 0.04 * i, risk=0.02 + 0.01 * i, won=(i % 2 == 0)))
        s.add(poker(99, eq=0.50, risk=0.30, won=False))   # huge size, zero edge
        pz.save_session(s, base)

        hits = pz.query_decisions(base,
            predicate=lambda e, m: e.sizing_deviation is not None and abs(e.sizing_deviation) > 1.5)
        hit_ids = {e.decision_id for _, e in hits}
        assert "p99" in hit_ids, hit_ids


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
