#!/usr/bin/env python3
"""
evaluate.py — CLI runner for the decision-quality scoring engine
================================================================

Loads a JSON file of Decisions and prints a formatted DecisionQualityProfile.
This is the end-to-end smoke test for the whole Phase-1 stack: schema -> engine
-> human-readable output, with no UI and no third-party dependencies.

USAGE
-----
    python3 evaluate.py <session.json>
    python3 evaluate.py <session.json> --json      # machine-readable output
    python3 evaluate.py --demo                      # generate + score a sample

ACCEPTED JSON SHAPES
--------------------
Either a bare list of Decision objects:

    [ { "decision_id": ..., "domain": "poker", ... }, ... ]

or a session wrapper (the persistence format from the roadmap's Phase 2):

    { "session_id": "...", "domain": "...", "decisions": [ {...}, {...} ] }

Each Decision is the JSON produced by decision_schema_stdlib.decision_to_json().
The engine reads decisions structurally, so dicts straight from json.load work
without rebuilding dataclass instances.
"""

from __future__ import annotations

import argparse
import json
import sys

import scoring as sc


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_decisions(path: str):
    """Return (list_of_decision_dicts, session_meta_dict)."""
    with open(path, "r") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict) and "decisions" in data:
        meta = {k: v for k, v in data.items() if k != "decisions"}
        return data["decisions"], meta
    raise ValueError(
        "Unrecognized JSON. Expected a list of Decisions or an object with a "
        "'decisions' array."
    )


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

_DIMENSIONS = [
    ("policy_adherence", "Policy adherence / EV-loss"),
    ("calibration", "Calibration (probability honesty)"),
    ("resolution", "Resolution (discrimination)"),
    ("sizing_discipline", "Sizing discipline (edge->size)"),
    ("outcome_independence", "Outcome-independence"),
    ("tilt_control", "Tilt control"),
]


def _bar(score, width=20):
    """A little ASCII gauge so scores read at a glance in a terminal."""
    if score is None:
        return "  n/a (insufficient data)"
    filled = int(round(score / 100 * width))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {score:5.1f}"


def format_profile(profile, meta) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  DECISION-QUALITY PROFILE")
    lines.append("=" * 60)
    sid = meta.get("session_id", "—")
    lines.append(f"  user: {profile.user_id or '—':<18} domain: {profile.domain or '—'}")
    lines.append(f"  session: {sid:<15} decisions scored: {profile.sample_size}")
    lines.append("-" * 60)
    for key, label in _DIMENSIONS:
        score = getattr(profile, key)
        lines.append(f"  {label:<34}{_bar(score)}")
    lines.append("-" * 60)

    # Surface the Brier decomposition if calibration was computable.
    diag = profile.confidence_intervals or {}
    brier = diag.get("brier")
    if brier:
        lines.append("  Brier decomposition (Murphy 1973):")
        lines.append(f"      score={brier['brier']:.4f}  "
                     f"reliability={brier['reliability']:.4f}  "
                     f"resolution={brier['resolution']:.4f}  "
                     f"uncertainty={brier['uncertainty']:.4f}")
        lines.append(f"      base rate={brier['base_rate']:.3f}  n={brier['n']}")
    lines.append("  Coverage: "
                 f"policy={diag.get('policy_decisions_scored', 0)}  "
                 f"calib_pairs={diag.get('calibration_pairs', 0)}  "
                 f"sizing_pts={diag.get('sizing_points', 0)}")
    lines.append("=" * 60)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Demo data generator (so the CLI is runnable with zero setup)
# --------------------------------------------------------------------------- #

def write_demo(path: str):
    import decision_schema_stdlib as sch

    decs = []
    # A realistic-ish stud session that degrades after a bad beat: clean early
    # decisions, then a losing streak with widening EV-loss and snap bets.
    profile_seq = [
        # (ev_loss, pot, won, p, risk, latency, eq)
        (0.0, 120, True, 0.70, 0.04, 9000, 0.71),
        (3.0, 120, True, 0.62, 0.05, 8500, 0.63),
        (0.0, 120, False, 0.55, 0.045, 8000, 0.56),
        (8.0, 120, False, 0.60, 0.06, 6000, 0.58),   # first loss
        (22.0, 120, False, 0.65, 0.09, 3000, 0.52),  # tilt building: faster, bigger
        (40.0, 120, False, 0.78, 0.14, 1400, 0.50),  # snap punt, no edge
    ]
    for i, (evl, pot, won, p, risk, lat, eq) in enumerate(profile_seq):
        decs.append(sch.Decision(
            decision_id=f"demo-{i}", user_id="dara", session_id="demo-session",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0),
            sequence_index=i,
            context=sch.PokerContext(pot_size=pot),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips",
                              risk_fraction=risk),
            reference_policy=sch.SolverReference(
                best_action="call", best_action_ev=max(evl, 1.0),
                taken_action_ev=max(evl, 1.0) - evl, ev_loss=evl, true_equity=eq),
            decision_latency_ms=lat,
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=p,
                                                target_definition="win at showdown"),
            outcome=sch.Outcome(resolved=True, won=won,
                                realized_value=(pot if won else -risk * 1000)),
        ))
    payload = {
        "session_id": "demo-session",
        "domain": "poker",
        "decisions": [json.loads(sch.decision_to_json(d)) for d in decs],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(description="Score a JSON session of Decisions.")
    ap.add_argument("path", nargs="?", help="Path to the session JSON file.")
    ap.add_argument("--json", action="store_true",
                    help="Emit the profile as JSON instead of a formatted report.")
    ap.add_argument("--demo", action="store_true",
                    help="Write a sample session to ./demo_session.json and score it.")
    args = ap.parse_args(argv)

    if args.demo:
        args.path = write_demo("demo_session.json")
        print(f"[wrote sample session to {args.path}]\n")

    if not args.path:
        ap.error("provide a JSON file path, or use --demo")

    decisions, meta = load_decisions(args.path)
    profile, _ = sc.score_session(decisions)

    if args.json:
        out = dict(profile.__dict__)
        print(json.dumps(out, indent=2, default=str))
    else:
        print(format_profile(profile, meta))


if __name__ == "__main__":
    sys.exit(main())
