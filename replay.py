"""
replay.py — Replay mode  (Phase 3)
==================================

The core learning loop: step through a past session one decision at a time,
revealing the quality score AFTER each decision. Seeing the per-decision verdict
in sequence is where the outcome-vs-quality lesson lands — you watch a hand you
won score badly, or a fold you regretted score perfectly.

Provides:
  * iter_replay(session)  — a generator yielding a ReplayStep per decision
  * replay_cli(session)   — a simple interactive/auto terminal walkthrough

The web layer (Phase 4) consumes iter_replay() to render a step-through UI.

Stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from session import Session
import decision_schema_stdlib as sch


@dataclass
class ReplayStep:
    index: int
    decision_id: str
    domain: str
    # A short, human description of the spot (best-effort from context).
    situation: str
    chosen: str
    # Per-decision diagnostics from the engine.
    policy_loss: Optional[float]          # 0 (perfect) .. 1 (max off-policy)
    policy_adherence: Optional[float]     # 0..100
    sizing_deviation: Optional[float]
    brier_component: Optional[float]
    process_violations: List[str] = field(default_factory=list)
    outcome_won: Optional[bool] = None
    note: str = ""


def _situation(decision) -> str:
    """Best-effort one-line description of the spot for display."""
    dom = getattr(decision, "domain", None)
    dom = getattr(dom, "value", dom)
    ctx = getattr(decision, "context", None)
    if dom == "poker":
        street = getattr(ctx, "street", "") or "?"
        pot = getattr(ctx, "pot_size", 0.0)
        return f"Stud — {street} street, pot {pot:g}"
    if dom == "trading":
        tag = getattr(ctx, "setup_tag", None) or "trade"
        eq = getattr(ctx, "account_equity", 0.0)
        return f"Trade — {tag}, equity {eq:g}"
    return "decision"


def _chosen(decision) -> str:
    act = getattr(decision, "action_taken", None)
    if act is None:
        return "?"
    at = getattr(act, "action_type", None)
    if at is not None:
        return getattr(at, "value", str(at))
    side = getattr(act, "side", None)
    if side is not None:
        qty = getattr(act, "quantity", "")
        return f"{getattr(side, 'value', side)} {qty}".strip()
    return "?"


def iter_replay(session: Session) -> Iterator[ReplayStep]:
    """Yield one ReplayStep per decision, with the per-decision evaluation
    attached. Evaluations come from the full-session scoring pass so sequence-
    dependent fields (sizing residuals) are correct."""
    _, evals = session.score()
    for i, d in enumerate(session.decisions):
        e = evals[i] if i < len(evals) else None
        loss = e.ev_loss_normalized if e else None
        adh = None if loss is None else round(100.0 * (1.0 - loss), 1)

        oc = getattr(d, "outcome", None)
        won = getattr(oc, "won", None) if oc is not None else None

        # The teaching beat: flag good-decision/bad-outcome (and its inverse).
        note = ""
        if adh is not None and won is not None:
            if adh >= 80 and won is False:
                note = "Good decision, bad outcome — the process was right."
            elif adh < 50 and won is True:
                note = "Bad decision, good outcome — you got lucky; don't bank the habit."

        yield ReplayStep(
            index=i,
            decision_id=getattr(d, "decision_id", str(i)),
            domain=getattr(getattr(d, "domain", ""), "value", getattr(d, "domain", "")),
            situation=_situation(d),
            chosen=_chosen(d),
            policy_loss=None if loss is None else round(loss, 3),
            policy_adherence=adh,
            sizing_deviation=(None if e is None or e.sizing_deviation is None
                              else round(e.sizing_deviation, 2)),
            brier_component=(None if e is None or e.brier_component is None
                             else round(e.brier_component, 3)),
            process_violations=(e.process_violations if e else []),
            outcome_won=won,
            note=note,
        )


def replay_cli(session: Session, interactive: bool = False) -> None:
    """Walk a session in the terminal, revealing each verdict in turn."""
    print(f"Replay — session {session.session_id} "
          f"({len(session)} decisions, domain {session.domain})\n")
    for step in iter_replay(session):
        print(f"[{step.index}] {step.situation}")
        print(f"     you chose: {step.chosen}")
        if interactive:
            input("     (press Enter to reveal the verdict) ")
        adh = "n/a" if step.policy_adherence is None else f"{step.policy_adherence}"
        won = {True: "won", False: "lost", None: "unresolved"}[step.outcome_won]
        print(f"     verdict: adherence {adh}/100   result: {won}")
        if step.process_violations:
            print(f"     violations: {', '.join(step.process_violations)}")
        if step.note:
            print(f"     >>> {step.note}")
        print()
    prof = session.profile()
    print("Session profile:",
          {k: getattr(prof, k) for k in
           ("policy_adherence", "calibration", "sizing_discipline",
            "outcome_independence", "tilt_control")})


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    s = Session("replay-demo", "dara", "poker")
    spec = [  # (ev_loss, pot, won, p, risk)
        (0.0, 100, False, 0.62, 0.04),   # solid fold/call, lost anyway
        (0.0, 100, True, 0.58, 0.05),
        (45.0, 100, True, 0.80, 0.13),   # bad punt that happened to win
        (5.0, 100, False, 0.55, 0.05),
    ]
    for i, (evl, pot, won, p, risk) in enumerate(spec):
        s.add(sch.Decision(
            decision_id=f"r{i}", user_id="dara", session_id="replay-demo",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0), sequence_index=i,
            context=sch.PokerContext(pot_size=pot, street="fifth"),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips", risk_fraction=risk),
            reference_policy=sch.SolverReference(best_action="call", best_action_ev=max(evl, 1),
                                                 taken_action_ev=max(evl, 1) - evl, ev_loss=evl,
                                                 true_equity=0.6),
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=p, target_definition="showdown"),
            outcome=sch.Outcome(resolved=True, won=won, realized_value=(pot if won else -risk * 1000)),
        ))
    replay_cli(s, interactive=False)
