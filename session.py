"""
session.py — Session & history management  (Phase 2)
====================================================

A `Session` is an ordered collection of `Decision` objects from a single
sitting (a poker session or a trading day). It is the unit the data layer
stores and loads, and the unit the scoring engine evaluates.

WHY THIS EXISTS
---------------
Two of the five scoring dimensions — outcome-independence and tilt control —
are inherently SEQUENTIAL: they only mean something across an ordered run of
decisions, not for one decision in isolation. The scoring engine already takes
a list, but `Session` gives that list an identity (id, user, domain, created
time), guarantees chronological order, and adds the rolling/windowed views the
web dashboard (Phase 4) will draw as trend lines.

WHAT IT DOES NOT DO
-------------------
No disk I/O lives here — saving and loading is `persistence.py`'s job. A
`Session` is a pure in-memory object so it stays trivial to test.

Stdlib-only. Accepts both real dataclass `Decision` instances and plain dicts
(e.g. loaded from JSON); the scoring engine reads either transparently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional

import scoring as sc
from decision_schema_stdlib import DecisionEvaluation, DecisionQualityProfile

# The dimension attribute names on DecisionQualityProfile, in display order.
DIMENSIONS = (
    "policy_adherence",
    "calibration",
    "resolution",
    "sizing_discipline",
    "outcome_independence",
    "tilt_control",
)


def _enum_value(v):
    return getattr(v, "value", v)


def _decision_field(d, name, default=None):
    """Read a field from a Decision that may be a dataclass or a dict."""
    if isinstance(d, dict):
        return d.get(name, default)
    return getattr(d, name, default)


class Session:
    """An ordered run of decisions plus its metadata and derived metrics."""

    def __init__(
        self,
        session_id: str,
        user_id: str = "",
        domain: str = "",
        created_at: Optional[datetime] = None,
        decisions: Optional[Iterable] = None,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.domain = _enum_value(domain)
        self.created_at = created_at or datetime.utcnow()
        self.decisions: List = list(decisions or [])
        # Keep chronological order even if decisions arrive out of order.
        self._sort()

    # ------------------------------------------------------------------ #
    # Mutation
    # ------------------------------------------------------------------ #
    def add(self, decision) -> None:
        """Append a decision and re-establish chronological order. Also back-
        fills user_id/domain from the first decision if the session was created
        without them."""
        self.decisions.append(decision)
        self._sort()
        if not self.user_id:
            self.user_id = _decision_field(decision, "user_id", "") or ""
        if not self.domain:
            self.domain = _enum_value(_decision_field(decision, "domain", "")) or ""

    def _sort(self) -> None:
        # Prefer sequence_index; fall back to timestamp; stable otherwise.
        def key(d):
            si = _decision_field(d, "sequence_index", None)
            if si is not None:
                return (0, si)
            ts = _decision_field(d, "timestamp", None)
            return (1, str(ts))
        self.decisions.sort(key=key)

    def __len__(self) -> int:
        return len(self.decisions)

    # ------------------------------------------------------------------ #
    # Scoring views
    # ------------------------------------------------------------------ #
    def profile(self) -> DecisionQualityProfile:
        """The full five-dimension profile for the whole session."""
        prof, _ = sc.score_session(self.decisions, self.user_id, self.domain)
        return prof

    def evaluations(self) -> List[DecisionEvaluation]:
        """Per-decision diagnostics, aligned 1:1 with self.decisions."""
        _, evals = sc.score_session(self.decisions, self.user_id, self.domain)
        return evals

    def score(self):
        """Both at once (avoids scoring twice). Returns (profile, evaluations)."""
        return sc.score_session(self.decisions, self.user_id, self.domain)

    # ------------------------------------------------------------------ #
    # Rolling / windowed metrics  (for trend lines)
    # ------------------------------------------------------------------ #
    def rolling_profiles(self, window: int = 10) -> List[DecisionQualityProfile]:
        """A profile computed on a trailing window ending at each decision.

        Returns one profile per decision index i, scored on decisions
        [max(0, i-window+1) .. i]. The web dashboard turns the per-dimension
        series from these into trend lines so the user can see, e.g., tilt
        control sliding as a session wears on."""
        out: List[DecisionQualityProfile] = []
        for i in range(len(self.decisions)):
            lo = max(0, i - window + 1)
            prof, _ = sc.score_session(self.decisions[lo:i + 1], self.user_id, self.domain)
            out.append(prof)
        return out

    def dimension_series(self, dimension: str, window: int = 10) -> List[Optional[float]]:
        """The trailing-window series for a single dimension (e.g. 'tilt_control').
        Values are None where the window had insufficient data for that dimension."""
        if dimension not in DIMENSIONS:
            raise ValueError(f"unknown dimension {dimension!r}; expected one of {DIMENSIONS}")
        return [getattr(p, dimension) for p in self.rolling_profiles(window)]

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    def summary(self) -> dict:
        """A compact, JSON-friendly snapshot for listing UIs and logs."""
        prof = self.profile()
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "domain": self.domain,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime)
            else self.created_at,
            "n_decisions": len(self.decisions),
            "scores": {dim: getattr(prof, dim) for dim in DIMENSIONS},
        }


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import decision_schema_stdlib as sch

    s = Session(session_id="sess-demo", user_id="dara", domain="poker")
    for i in range(6):
        s.add(sch.Decision(
            decision_id=f"d{i}", user_id="dara", session_id="sess-demo",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0),
            sequence_index=i,
            context=sch.PokerContext(pot_size=100.0),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=50.0, size_unit="chips",
                              risk_fraction=0.03 + 0.01 * i),
            reference_policy=sch.SolverReference(
                best_action="call", best_action_ev=10.0,
                taken_action_ev=10.0 - i * 4.0, ev_loss=i * 4.0,
                true_equity=0.5 + 0.04 * i),
            decision_latency_ms=9000 - i * 1200,
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=0.6,
                                                target_definition="showdown"),
            outcome=sch.Outcome(resolved=True, won=(i % 2 == 0),
                                realized_value=(20 if i % 2 == 0 else -20)),
        ))

    print("Summary:")
    for k, v in s.summary().items():
        print(f"  {k}: {v}")
    print("\nTilt-control trailing series (window=3):")
    print("  ", s.dimension_series("tilt_control", window=3))
