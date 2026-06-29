"""
seed_demo.py — populate the data store with example sessions
============================================================

Creates two sessions so the web dashboard has something to show on first run:

  1. "stud-tilt-demo"  — a poker session that degrades into tilt, with its
     SolverReference EVs produced by the REAL Monte Carlo provider (Phase 5),
     not hand-typed numbers.
  2. "trade-day-demo"  — a trading session mixing on-plan and off-plan actions.

Run:  python3 seed_demo.py
"""

from __future__ import annotations

import os

import decision_schema_stdlib as sch
from session import Session
import persistence as pz
from solver import MonteCarloEquityProvider, parse_card

DATA_DIR = os.environ.get("DQ_DATA_DIR",
                          os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))


def stud_session() -> Session:
    prov = MonteCarloEquityProvider(iterations=1500, seed=42)
    s = Session("stud-tilt-demo", "dara", "poker")
    # (hero cards, action, pot, to_call, won, p, risk, latency)
    hands = [
        (["As", "Ah", "Ks"], "call", 100, 20, True, 0.72, 0.04, 9000),
        (["Qd", "Qs", "7c"], "call", 80, 15, False, 0.60, 0.05, 8500),
        (["9h", "9c", "2d"], "call", 90, 18, False, 0.52, 0.05, 7000),
        (["Jd", "5s", "3c"], "call", 70, 25, False, 0.40, 0.08, 3500),   # thin call, tilt
        (["8c", "4d", "2h"], "raise", 60, 0, False, 0.70, 0.14, 1400),   # snap bluff punt
    ]
    for i, (cards, action, pot, to_call, won, p, risk, lat) in enumerate(hands):
        ref = prov.reference([parse_card(c) for c in cards], action,
                             pot_size=pot, to_call=to_call, num_opponents=1)
        s.add(sch.Decision(
            decision_id=f"h{i}", user_id="dara", session_id="stud-tilt-demo",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0), sequence_index=i,
            context=sch.PokerContext(
                pot_size=pot, to_call=to_call, street="fifth",
                hero_down_cards=[sch.Card(cards[0][0], cards[0][1]),
                                 sch.Card(cards[1][0], cards[1][1])],
                hero_up_cards=[sch.Card(cards[2][0], cards[2][1])]),
            action_taken=sch.PokerAction(
                action_type=(sch.PokerActionType.RAISE if action == "raise"
                             else sch.PokerActionType.CALL)),
            sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips", risk_fraction=risk),
            reference_policy=ref,
            decision_latency_ms=lat,
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=p, target_definition="showdown"),
            outcome=sch.Outcome(resolved=True, won=won, realized_value=(pot if won else -risk * 1000)),
        ))
    return s


def trade_session() -> Session:
    s = Session("trade-day-demo", "dara", "trading")
    # (on_plan, violations, expectancy, risk, won, p)
    trades = [
        (True, [], 0.35, 0.010, True, 0.55),
        (True, [], 0.30, 0.012, False, 0.50),
        (False, ["off_plan_entry", "no_confirmation"], 0.10, 0.020, False, 0.65),  # chased
        (True, [], 0.40, 0.011, True, 0.58),
        (False, ["oversize_vs_liquidity_rule"], 0.25, 0.030, False, 0.60),         # oversize
    ]
    for i, (on_plan, viol, exp, risk, won, p) in enumerate(trades):
        s.add(sch.Decision(
            decision_id=f"x{i}", user_id="dara", session_id="trade-day-demo",
            domain=sch.Domain.TRADING,
            timestamp=sch.datetime(2026, 6, 17, 9, 30 + i, 0), sequence_index=i,
            context=sch.TradingContext(
                instrument=sch.Instrument(symbol="ES", asset_class=sch.AssetClass.FUTURE,
                                          multiplier=50.0),
                account_equity=100_000.0, setup_tag="breakout_retest"),
            action_taken=sch.TradingAction(side=sch.TradeSide.BUY, quantity=1.0),
            sizing=sch.Sizing(absolute_size=1.0, size_unit="contracts", risk_fraction=risk),
            reference_policy=sch.PlanReference(was_on_plan=on_plan, setup_id="breakout_retest_v2",
                                               setup_expectancy=exp, process_violations=viol),
            decision_latency_ms=6000,
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=p, target_definition="+2R"),
            outcome=sch.Outcome(resolved=True, won=won, realized_value=(10 if won else -10)),
        ))
    return s


def main():
    for s in (stud_session(), trade_session()):
        pz.save_session(s, DATA_DIR)
        prof = s.profile()
        print(f"seeded {s.session_id}: policy={prof.policy_adherence} "
              f"tilt={prof.tilt_control} sizing={prof.sizing_discipline}")
    print(f"data dir: {DATA_DIR}")


if __name__ == "__main__":
    main()
