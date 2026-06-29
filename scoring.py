"""
scoring.py — The Decision-Quality Scoring Engine
=================================================

Phase 1 of the Poker/Trading Decision-Quality Training System.

WHAT THIS DOES
--------------
Consumes `Decision` objects (from decision_schema_stdlib.py) and produces:

  * `DecisionEvaluation`      — per-decision diagnostics (one per Decision)
  * `DecisionQualityProfile`  — a session-level, five-dimension profile (0-100)

The whole point of the system is to score the QUALITY of a decision
independently of its OUTCOME. A good decision with a bad result still scores
well; a lucky punt still scores badly. Outcomes are used only where they are
legitimately needed (calibration resolution, and detecting tilt/outcome-chasing
across a sequence) — never to reward or punish a single decision directly.

THE FIVE DIMENSIONS
-------------------
1. Policy adherence & EV-loss   — did the action match the best available policy?
2. Calibration & resolution     — were stated probabilities honest and informative?
3. Sizing discipline            — did bet/position size track the estimated edge?
4. Outcome-independence         — does decision quality survive a recent loss?
5. Tilt control                 — does decision quality survive rising stress?

DOMAIN DIVERGENCE
-----------------
The poker path and the trading path diverge in exactly ONE place:
`score_policy_adherence()`, which branches on `reference_policy.kind`
("solver" for poker, "plan" for trading). Everything else — calibration,
sizing, outcome-independence, tilt — is identical for both domains, because
both reduce to the same normalized quantities (risk_fraction, win_probability,
realized outcome, a stress index). This is the architectural payoff of the
unified schema.

DEPENDENCIES
------------
Standard library only. Runs on a stock Python 3.8+ install with no pip.
The numerical pieces (Pearson correlation, OLS, Brier decomposition) are
implemented by hand below so NumPy is not required.

INPUT FLEXIBILITY
-----------------
`score_session()` accepts either real dataclass `Decision` instances or plain
dicts (e.g. straight from `json.load`). Dicts are wrapped recursively in
SimpleNamespace so attribute access works uniformly, and enum values that have
been flattened to strings during JSON serialization are handled transparently.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import is_dataclass
from types import SimpleNamespace
from typing import List, Optional, Sequence, Tuple

# We import only the *output* dataclasses we must emit. The engine reads inputs
# structurally (duck-typed) so it works on both dataclass and dict/JSON input.
from decision_schema_stdlib import DecisionEvaluation, DecisionQualityProfile


# =========================================================================== #
# 0. Tunable constants — every magic number lives here, documented.
# =========================================================================== #

# Number of equal-width bins used for the Brier reliability/resolution
# decomposition. 10 is the conventional choice; for very small sessions the
# decomposition is reported but should be read with the sample size in mind.
BRIER_BINS = 10

# Minimum number of (edge, size) points with non-trivial edge variance needed
# before the sizing regression is meaningful. Below this we return None.
MIN_SIZING_POINTS = 3

# Minimum decisions needed before outcome-independence / tilt correlations are
# computed. Below this the dimensions are returned as None (not enough signal).
MIN_SEQUENCE_POINTS = 4

# Weights for the composite stress index (see compute_stress_index). They need
# not sum to 1 — the index is min-max normalized across the session afterwards.
STRESS_WEIGHTS = {
    "loss_streak": 1.0,       # consecutive losing decisions immediately prior
    "drawdown": 1.0,          # current drawdown from the session equity peak
    "speed": 0.5,             # how much faster than usual this decision was made
}

# A decision made faster than this fraction of the personal median latency is
# treated as "snap / impulsive" and contributes to the speed stress proxy.
SNAP_LATENCY_FRACTION = 0.5


# =========================================================================== #
# 1. Small numerical helpers (stdlib only)
# =========================================================================== #

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson correlation coefficient. Returns None if undefined (n<2 or a
    variable has zero variance)."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def ols(xs: Sequence[float], ys: Sequence[float]) -> Optional[Tuple[float, float, float]]:
    """Ordinary least squares for y = slope*x + intercept.
    Returns (slope, intercept, r) or None if undefined."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    r = pearson(xs, ys)
    return slope, intercept, (r if r is not None else 0.0)


def brier_decomposition(
    forecasts: Sequence[float],
    outcomes: Sequence[int],
    n_bins: int = BRIER_BINS,
) -> Optional[dict]:
    """Murphy (1973) decomposition of the Brier score.

        BS = Reliability - Resolution + Uncertainty

    where, binning forecasts into k bins:
        Uncertainty  = obar * (1 - obar)                  # base-rate variance
        Resolution   = (1/N) * Σ_k n_k * (obar_k - obar)^2  # discrimination (higher = better)
        Reliability  = (1/N) * Σ_k n_k * (f_k - obar_k)^2    # miscalibration (lower = better)

    `forecasts` are stated win-probabilities in [0,1]; `outcomes` are realized
    binary results (1 win / 0 loss). Returns a dict of the components plus the
    raw Brier score, or None if there is not enough variation to decompose
    (e.g. all outcomes identical -> uncertainty 0).

    EXACTNESS CAVEAT (important — the roadmap flagged this):
    The identity BS = REL - RES + UNC holds EXACTLY only when each bin contains
    a single distinct forecast value. With fixed-width binning, forecasts that
    share a bin (e.g. 0.60, 0.62, 0.65 all in the 0.6-0.7 bin) introduce a small
    within-bin variance residual, so REL - RES + UNC will differ from the raw
    Brier score by that residual. This is the standard reliability-diagram
    behaviour and is acceptable for session-level scoring, but REL/RES should be
    read as well-grounded estimates, not exact identities, on small samples.
    The raw `brier` value returned here is always exact.
    """
    n = len(forecasts)
    if n == 0 or len(outcomes) != n:
        return None

    obar = _mean(outcomes)                 # overall base rate of "win"
    uncertainty = obar * (1.0 - obar)
    brier = _mean([(f - o) ** 2 for f, o in zip(forecasts, outcomes)])

    # Bin the forecasts into equal-width bins on [0,1].
    bins: List[List[Tuple[float, int]]] = [[] for _ in range(n_bins)]
    for f, o in zip(forecasts, outcomes):
        idx = min(n_bins - 1, int(_clamp(f) * n_bins))
        bins[idx].append((f, o))

    reliability = 0.0
    resolution = 0.0
    for b in bins:
        if not b:
            continue
        nk = len(b)
        fk = _mean([f for f, _ in b])      # mean forecast in this bin
        ok = _mean([o for _, o in b])      # observed win rate in this bin
        reliability += nk * (fk - ok) ** 2
        resolution += nk * (ok - obar) ** 2
    reliability /= n
    resolution /= n

    return {
        "brier": brier,
        "reliability": reliability,   # lower is better (well-calibrated)
        "resolution": resolution,     # higher is better (discriminating)
        "uncertainty": uncertainty,   # property of the outcomes, not the forecaster
        "base_rate": obar,
        "n": n,
    }


# =========================================================================== #
# 2. Structural access helpers — work on dataclasses OR JSON dicts
# =========================================================================== #

def _wrap(obj):
    """Recursively wrap plain dicts/lists in SimpleNamespace so we can use
    attribute access uniformly, whether the input came from a live dataclass
    or from json.load(). Dataclass instances are passed through unchanged."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _wrap(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_wrap(v) for v in obj]
    return obj


def _enum_value(v):
    """Normalize an enum-or-string to its plain string value."""
    return getattr(v, "value", v)


def _get(obj, name, default=None):
    return getattr(obj, name, default)


# =========================================================================== #
# 3. Dimension 1 — Policy adherence & EV-loss  (THE domain branch point)
# =========================================================================== #

def score_policy_adherence(decision) -> Tuple[Optional[float], List[str]]:
    """Return (policy_loss_normalized, process_violations) for one decision.

    `policy_loss_normalized` is a UNIFIED [0,1] "how far from the best policy"
    measure where 0 == perfectly on-policy and 1 == maximally off-policy. Both
    domains collapse to this same scale, but they compute it differently:

      POKER  (reference_policy.kind == "solver"):
          A solver gives an EV-optimal action, so EV-loss is meaningful.
          loss = ev_loss / pot_size   (EV given up, as a fraction of the pot —
          the standard poker normalization), clamped to [0,1]. If no pot is
          available we fall back to normalizing by the EV magnitude itself.

      TRADING (reference_policy.kind == "plan"):
          There is no EV-optimal action, so quality == adherence to a validated
          plan. loss starts at 0 when fully on-plan and rises with process
          violations; entering off-plan is the maximal violation.

    This is the ONLY function where the two domains diverge. Everything
    downstream consumes `policy_loss_normalized` identically.
    """
    ref = _get(decision, "reference_policy")
    if ref is None:
        return None, []

    kind = _enum_value(_get(ref, "kind"))

    # ---------------- POKER / solver branch ---------------- #
    if kind == "solver":
        ev_loss = _get(ref, "ev_loss")
        if ev_loss is None:
            # Reconstruct from best/taken EV if ev_loss wasn't precomputed.
            best = _get(ref, "best_action_ev")
            taken = _get(ref, "taken_action_ev")
            if best is not None and taken is not None:
                ev_loss = max(0.0, best - taken)
        if ev_loss is None:
            return None, []

        # Prefer pot-relative normalization (standard in poker analysis).
        ctx = _get(decision, "context")
        pot = _get(ctx, "pot_size") if ctx is not None else None
        if pot and pot > 0:
            loss = _clamp(ev_loss / pot)
        else:
            # Fallback: saturating normalization against the EV scale present.
            best = _get(ref, "best_action_ev") or 0.0
            scale = max(abs(best), 1.0)
            loss = _clamp(ev_loss / scale)
        return loss, []

    # ---------------- TRADING / plan branch ---------------- #
    if kind == "plan":
        on_plan = bool(_get(ref, "was_on_plan", False))
        violations = list(_get(ref, "process_violations", []) or [])
        if not on_plan:
            # Off-plan entry is the maximal policy failure regardless of count.
            return 1.0, violations or ["off_plan_entry"]
        # On plan: each logged process violation erodes adherence.
        # 0 violations -> loss 0; saturates at 1.0 after ~4 violations.
        loss = _clamp(0.25 * len(violations))
        return loss, violations

    # Unknown reference kind — no policy signal.
    return None, []


# =========================================================================== #
# 4. Edge extraction — also domain-aware, feeds sizing discipline
# =========================================================================== #

def extract_edge(decision) -> Optional[float]:
    """A scalar 'how much edge did this decision have', used by the sizing
    regression. Branches by domain but returns a single comparable number:

      POKER:   prefer true_equity-0.5 (equity advantage); else best_action_ev.
      TRADING: setup_expectancy (expected R per unit risk) from the plan.

    Returns None if no edge proxy is available."""
    ref = _get(decision, "reference_policy")
    if ref is None:
        return None
    kind = _enum_value(_get(ref, "kind"))

    if kind == "solver":
        eq = _get(ref, "true_equity")
        if eq is not None:
            return eq - 0.5          # advantage over a coin flip
        return _get(ref, "best_action_ev")

    if kind == "plan":
        return _get(ref, "setup_expectancy")

    return None


# =========================================================================== #
# 5. Stress index — concrete definition for the tilt dimension
# =========================================================================== #

def compute_stress_index(decisions: List) -> List[float]:
    """Compute a per-decision composite stress index in [0,1] across the
    session. The roadmap flagged tilt proxies as 'named but not specified';
    this is the concrete commitment.

    Three observable proxies, each derived from the decision SEQUENCE (no extra
    user input required):

      loss_streak — consecutive losing decisions immediately before this one.
      drawdown    — current drawdown from the running peak of cumulative
                    realized P&L (fraction of peak, in [0,1]).
      speed       — how much faster than the personal median latency this
                    decision was taken (snap decisions signal agitation).

    Each proxy is min-max normalized across the session, combined with
    STRESS_WEIGHTS, then the composite is itself min-max normalized to [0,1].
    A flat session (no stress variation) yields all-zeros, which correctly
    makes the tilt correlation undefined rather than spuriously signalled.
    """
    n = len(decisions)
    if n == 0:
        return []

    # --- loss streak prior to each decision --- #
    loss_streak = [0] * n
    streak = 0
    for i, d in enumerate(decisions):
        loss_streak[i] = streak       # streak BEFORE this decision resolves
        outcome = _get(d, "outcome")
        won = _get(outcome, "won") if outcome is not None else None
        if won is False:
            streak += 1
        elif won is True:
            streak = 0
        # won is None (unresolved) leaves the streak unchanged

    # --- running drawdown from cumulative realized P&L peak --- #
    drawdown = [0.0] * n
    cum = 0.0
    peak = 0.0
    for i, d in enumerate(decisions):
        drawdown[i] = (peak - cum) / peak if peak > 0 else 0.0  # state BEFORE this decision
        outcome = _get(d, "outcome")
        rv = _get(outcome, "realized_value") if outcome is not None else None
        if rv is not None:
            cum += rv
            peak = max(peak, cum)

    # --- decision speed relative to personal median --- #
    latencies = [_get(d, "decision_latency_ms") for d in decisions]
    known = [x for x in latencies if x is not None]
    speed = [0.0] * n
    if known:
        med = statistics.median(known)
        if med > 0:
            for i, x in enumerate(latencies):
                if x is not None and x < SNAP_LATENCY_FRACTION * med:
                    # The faster than the snap threshold, the higher the proxy.
                    speed[i] = _clamp((SNAP_LATENCY_FRACTION * med - x) /
                                      (SNAP_LATENCY_FRACTION * med))

    def _minmax(xs: List[float]) -> List[float]:
        lo, hi = min(xs), max(xs)
        if hi <= lo:
            return [0.0] * len(xs)
        return [(x - lo) / (hi - lo) for x in xs]

    nls = _minmax([float(x) for x in loss_streak])
    ndd = _minmax(drawdown)
    nsp = _minmax(speed)

    raw = [
        STRESS_WEIGHTS["loss_streak"] * nls[i]
        + STRESS_WEIGHTS["drawdown"] * ndd[i]
        + STRESS_WEIGHTS["speed"] * nsp[i]
        for i in range(n)
    ]
    return _minmax(raw)


# =========================================================================== #
# 6. Per-decision evaluation
# =========================================================================== #

def evaluate_decision(decision, stress: float = 0.0) -> DecisionEvaluation:
    """Compute the per-decision diagnostics. `sizing_deviation` is filled later
    by the session pass (it needs the fitted edge-to-size regression)."""
    loss, violations = score_policy_adherence(decision)

    # Brier component for this single decision, if both forecast & outcome exist.
    brier_component = None
    est = _get(decision, "ex_ante_estimate")
    outcome = _get(decision, "outcome")
    won = _get(outcome, "won") if outcome is not None else None
    if est is not None and won is not None:
        f = _get(est, "win_probability")
        if f is not None:
            brier_component = (f - (1.0 if won else 0.0)) ** 2

    return DecisionEvaluation(
        decision_id=_get(decision, "decision_id", ""),
        ev_loss_normalized=loss,
        sizing_deviation=None,             # filled in score_session()
        brier_component=brier_component,
        process_violations=violations,
        stress_context={"stress_index": stress},
    )


# =========================================================================== #
# 7. Session-level five-dimension profile
# =========================================================================== #

def score_session(decisions: Sequence, user_id: str = "", domain: str = "") -> Tuple[
        DecisionQualityProfile, List[DecisionEvaluation]]:
    """The main entry point. Takes an ordered sequence of Decisions (dataclass
    instances or JSON dicts) and returns (DecisionQualityProfile, [DecisionEvaluation]).

    Decisions MUST be in chronological order — outcome-independence and tilt
    both depend on the sequence."""
    ds = [_wrap(d) for d in decisions]
    n = len(ds)

    if not domain and ds:
        domain = _enum_value(_get(ds[0], "domain", ""))
    if not user_id and ds:
        user_id = _get(ds[0], "user_id", "")

    # ---- per-decision pass ---- #
    stress = compute_stress_index(ds)
    evals = [evaluate_decision(d, stress[i]) for i, d in enumerate(ds)]

    # Convenience views.
    losses = [e.ev_loss_normalized for e in evals if e.ev_loss_normalized is not None]

    # ------------------------------------------------------------------ #
    # Dimension 1 — Policy adherence (100 = always on the best policy)
    # ------------------------------------------------------------------ #
    policy_adherence = 100.0 * (1.0 - _mean(losses)) if losses else None

    # ------------------------------------------------------------------ #
    # Dimensions 2 & 3 — Calibration & resolution (Brier decomposition)
    # ------------------------------------------------------------------ #
    forecasts, outcomes = [], []
    for d in ds:
        est = _get(d, "ex_ante_estimate")
        oc = _get(d, "outcome")
        won = _get(oc, "won") if oc is not None else None
        f = _get(est, "win_probability") if est is not None else None
        if f is not None and won is not None:
            forecasts.append(float(f))
            outcomes.append(1 if won else 0)

    calibration = None
    resolution = None
    brier_detail = None
    decomp = brier_decomposition(forecasts, outcomes)
    if decomp is not None:
        brier_detail = decomp
        u = decomp["uncertainty"]
        if u > 0:
            # Reliability relative to uncertainty is the natural 0..1 scale:
            # reliability == 0 -> perfectly calibrated -> 100.
            calibration = 100.0 * (1.0 - _clamp(decomp["reliability"] / u))
            # Resolution maxes out at uncertainty (perfect discrimination).
            resolution = 100.0 * _clamp(decomp["resolution"] / u)
        # If uncertainty == 0 (all wins or all losses) calibration/resolution
        # are genuinely undefined -> leave as None.

    # ------------------------------------------------------------------ #
    # Dimension 3 — Sizing discipline (edge-to-size regression)
    # ------------------------------------------------------------------ #
    # Good discipline = bet/position size (risk_fraction) tracks the estimated
    # edge. We regress risk_fraction on edge across the session; a positive,
    # well-correlated relationship is disciplined. Per-decision sizing_deviation
    # is the standardized residual from that fit (how much this decision over-
    # or under-bet relative to its own edge).
    sizing_discipline = None
    edge_vals, size_vals, idx_map = [], [], []
    for i, d in enumerate(ds):
        edge = extract_edge(d)
        sz = _get(_get(d, "sizing"), "risk_fraction") if _get(d, "sizing") else None
        if edge is not None and sz is not None:
            edge_vals.append(float(edge))
            size_vals.append(float(sz))
            idx_map.append(i)

    if len(edge_vals) >= MIN_SIZING_POINTS:
        fit = ols(edge_vals, size_vals)
        if fit is not None:
            slope, intercept, r = fit
            # Reward positive correlation (size rises with edge). Negative or
            # zero correlation == undisciplined sizing.
            sizing_discipline = 100.0 * _clamp(r)
            # Fill per-decision standardized residuals.
            resid = [size_vals[k] - (slope * edge_vals[k] + intercept)
                     for k in range(len(edge_vals))]
            try:
                sd = statistics.pstdev(resid)
            except statistics.StatisticsError:
                sd = 0.0
            for k, i in enumerate(idx_map):
                evals[i].sizing_deviation = (resid[k] / sd) if sd > 0 else 0.0

    # ------------------------------------------------------------------ #
    # Dimension 4 — Outcome-independence
    # ------------------------------------------------------------------ #
    # Does decision quality (1 - policy loss) survive a recent loss? We compare
    # mean adherence on decisions that FOLLOW a losing decision vs. those that
    # follow a non-loss. A drop is outcome-dependence (a tilt/chasing signal).
    outcome_independence = None
    after_loss, after_nonloss = [], []
    for i in range(1, n):
        prev_oc = _get(ds[i - 1], "outcome")
        prev_won = _get(prev_oc, "won") if prev_oc is not None else None
        loss_i = evals[i].ev_loss_normalized
        if prev_won is None or loss_i is None:
            continue
        adherence_i = 1.0 - loss_i
        (after_loss if prev_won is False else after_nonloss).append(adherence_i)

    if len(after_loss) >= 1 and len(after_nonloss) >= 1 \
            and (len(after_loss) + len(after_nonloss)) >= MIN_SEQUENCE_POINTS:
        deficit = _mean(after_nonloss) - _mean(after_loss)   # >0 means worse after losses
        outcome_independence = 100.0 * (1.0 - _clamp(deficit))

    # ------------------------------------------------------------------ #
    # Dimension 5 — Tilt control
    # ------------------------------------------------------------------ #
    # Correlate the stress index with policy loss. If loss rises as stress rises
    # (positive correlation), tilt control is poor. Stable/improving quality
    # under stress (<=0 correlation) scores 100.
    tilt_control = None
    stress_pairs = [(stress[i], evals[i].ev_loss_normalized)
                    for i in range(n) if evals[i].ev_loss_normalized is not None]
    if len(stress_pairs) >= MIN_SEQUENCE_POINTS:
        sx = [p[0] for p in stress_pairs]
        sl = [p[1] for p in stress_pairs]
        if statistics.pstdev(sx) > 0:           # only assess if stress actually varied
            if statistics.pstdev(sl) == 0:
                # Quality is perfectly stable while stress moves: ideal tilt control.
                tilt_control = 100.0
            else:
                corr = pearson(sx, sl)
                if corr is not None:
                    tilt_control = 100.0 * (1.0 - _clamp(max(0.0, corr)))
        # If stress never varied there is nothing to test -> leave as None.

    # ------------------------------------------------------------------ #
    # Assemble the profile.
    # ------------------------------------------------------------------ #
    diagnostics = {
        "policy_decisions_scored": len(losses),
        "calibration_pairs": len(forecasts),
        "sizing_points": len(edge_vals),
        "after_loss_n": len(after_loss),
        "after_nonloss_n": len(after_nonloss),
    }
    if brier_detail is not None:
        diagnostics["brier"] = brier_detail

    profile = DecisionQualityProfile(
        user_id=user_id,
        domain=domain,
        sample_size=n,
        policy_adherence=_round(policy_adherence),
        calibration=_round(calibration),
        resolution=_round(resolution),
        sizing_discipline=_round(sizing_discipline),
        outcome_independence=_round(outcome_independence),
        tilt_control=_round(tilt_control),
        confidence_intervals=diagnostics,
    )
    return profile, evals


def _round(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(x, 1)


# =========================================================================== #
# 8. Demo when run directly
# =========================================================================== #

if __name__ == "__main__":
    # Reuse the two example decisions from the schema module's smoke test by
    # importing them lazily; if that fails, just report import success.
    import decision_schema_stdlib as sch

    # Build a tiny 3-decision poker "session" by hand to show the engine works.
    def mk(seq, ev_loss, pot, won, p, risk, latency, eq):
        return sch.Decision(
            decision_id=f"demo-{seq}",
            user_id="dara",
            session_id="demo",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, seq, 0),
            sequence_index=seq,
            context=sch.PokerContext(pot_size=pot),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=risk * 1000, size_unit="chips",
                              risk_fraction=risk),
            reference_policy=sch.SolverReference(
                best_action="call", best_action_ev=ev_loss, taken_action_ev=0.0,
                ev_loss=ev_loss, true_equity=eq),
            decision_latency_ms=latency,
            ex_ante_estimate=sch.ExAnteEstimate(
                win_probability=p, target_definition="win at showdown"),
            outcome=sch.Outcome(resolved=True, won=won, realized_value=(10 if won else -10)),
        )

    session = [
        mk(0, ev_loss=0.0, pot=100, won=True, p=0.70, risk=0.05, latency=8000, eq=0.72),
        mk(1, ev_loss=5.0, pot=100, won=False, p=0.60, risk=0.04, latency=7000, eq=0.62),
        mk(2, ev_loss=2.0, pot=100, won=True, p=0.55, risk=0.03, latency=6500, eq=0.55),
        mk(3, ev_loss=40.0, pot=100, won=False, p=0.80, risk=0.12, latency=1500, eq=0.50),
    ]

    profile, evals = score_session(session)
    print("Demo session profile:")
    for k, v in profile.__dict__.items():
        if k != "confidence_intervals":
            print(f"  {k:>22}: {v}")
    print("  diagnostics:", profile.confidence_intervals)
