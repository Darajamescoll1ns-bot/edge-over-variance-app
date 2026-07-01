"""
coach.py — Per-street grading + markets translation  (interactive trainer)
==========================================================================

Two jobs, run after a hand finishes:

1. GRADE every hero decision street-by-street against TRUE equity (computed by
   solver.py over the cards that were knowable at that moment), using the same
   EV-loss logic the core scoring engine uses. Produces a per-street report and
   a one-line "why" for each decision.

2. TRANSLATE the hand's key decision into a MARKETS analogy, pose a question to
   the trainee, then grade their free-text answer and explain the model answer.

The markets layer has two backends:
  * LLMMarketsCoach    — calls an LLM (Anthropic) with the user's own
                         ANTHROPIC_API_KEY, for a fresh analogy per hand.
  * LibraryMarketsCoach— a deterministic, offline archetype library used
                         automatically whenever no key is configured or the API
                         call fails. The app therefore ALWAYS works locally.

Grading itself is deterministic and LLM-free — the LLM only ever phrases the
markets translation, never decides quality. Standard-library only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, asdict
from typing import List, Optional

import solver as sv
import glossary as gloss
import betting
import ranges as rng_mod
import rollout as ro

GRADING_SEED = 12345        # fixed so grades are reproducible
GRADING_ITERS = 1600

# Conditioned-equity grading (heads-up). The opponent's range is narrowed the
# way game theory says a defender must (MDF), so the EVs reflect "equity when
# called" rather than equity vs a random hand. Iteration/combo budgets are kept
# modest so a full hand grades in a few seconds; raise them for sharper numbers.
COND_ITERS_PRE = 90         # preflop per-class Monte-Carlo iterations
COND_ITERS_POST = 60        # postflop per-combo iterations
COND_MAX_COMBOS = 160       # postflop combo sample cap (live speed)
STUD_ITERS = 60             # stud per-hidden-combo iterations
STUD_MAX_COMBOS = 160       # stud hidden-combo sample cap
BET_RANGE_FREQ = 0.5        # a bettor shows up with ~the strongest half of range
ROLLOUT_TRIALS = 300        # implied-odds rollout trials for the spotlight spot
MULTIWAY_ITERS = 700        # Monte-Carlo trials for a multiway conditioned grid


# =========================================================================== #
# 1. Grading
# =========================================================================== #

@dataclass
class StreetGrade:
    street: int
    street_name: str
    action: str                  # label of the chosen option (e.g. "Raise to 12")
    options: List                # the option dicts offered
    equity: float
    option_evs: dict             # {label: ev}
    best_action: str             # label of the highest-EV option
    ev_loss_normalized: float
    adherence: float
    archetype: str
    why: str
    pot: float = 0.0
    to_call: float = 0.0
    math: str = ""               # plain-language EV math for this decision
    # --- conditioned-equity additions ---------------------------------------
    equity_vs_random: float = 0.0    # hero equity vs the opponent's WHOLE range
    equity_effective: float = 0.0    # equity that actually applies to the chosen
                                     # action (when called / vs a bettor)
    fold_equity: float = 0.0         # GTO fold share an aggressive line earns
    mdf: float = 0.0                 # continue frequency implied by the bet size
    implied: Optional[dict] = None   # rollout implied-odds breakdown (key spot)
    conditioned: bool = False        # True if the range model ran (else multiway
                                     # fallback to the old vs-random EV)


def _fold_equity(amount, pot):
    """Rough chance a bet/raise of `amount` takes the pot down immediately —
    grows with size relative to the pot, capped. Bigger bets fold out more."""
    if pot <= 0:
        return 0.0
    return min(0.55, 0.10 + 0.18 * (amount / pot))


def _ev_of_option(opt, e, P):
    """EV (in chips, relative to folding=0) of one sized option.

    P is the current pot (already includes villain's bet). For a call of cost c
    you win P and risk c, so EV = eP - (1-e)c (correct pot odds). For an
    aggressive sizing of `amount`, model fold equity plus the called branch."""
    key = opt["key"]
    amt = opt.get("amount", 0.0)
    if key == "fold":
        return 0.0
    if key == "check":
        return e * P
    if key == "call":
        return e * P - (1 - e) * amt
    fe = _fold_equity(amt, P)                       # bet / raise / all-in
    called = e * (P + amt) - (1 - e) * amt
    return fe * P + (1 - fe) * called


def _option_evs(options, e, pot, to_call=None, unit=None) -> dict:
    """EV per option, keyed by label. `options` is a list of option dicts
    (sized). The model is deliberately simple and clearly approximate, but it
    correctly rewards calling only above pot odds and sizing up with strong
    equity / down (or checking) when weak — which is what we want to teach."""
    return {o["label"]: round(_ev_of_option(o, e, pot), 3) for o in options}


def _archetype(action_family: str, equity: float) -> str:
    strong, weak = equity >= 0.58, equity <= 0.42
    aggressive = action_family in ("bet", "raise", "allin")
    continuing = action_family == "call"
    passive = action_family == "check"
    if action_family == "fold":
        return "disciplined_fold" if weak else "premature_fold"
    if strong and (aggressive or continuing):
        return "press_edge"
    if strong and passive:
        return "missed_value"
    if weak and (aggressive or continuing):
        return "chase"
    return "marginal_continue"


def _grade_archetype(fam: str, eff: float, loss_norm: float) -> str:
    """Pick the teaching archetype from EV-correctness first, then equity.

    Crucially this separates a SOUND low-equity aggressive line (a bluff/semi-
    bluff that's +EV on fold equity — 'marginal_continue', defensible) from an
    EV-LOSING one ('chase', a genuine mistake). The old _archetype keyed only on
    equity and would mislabel every thin bet a chase now that equity is
    conditioned on being called."""
    mistake = loss_norm > 0.12
    strong, weak = eff >= 0.58, eff <= 0.42
    if fam == "fold":
        return "premature_fold" if (mistake and not weak) else "disciplined_fold"
    if fam in ("bet", "raise", "allin", "call"):
        if mistake:
            return "chase"
        return "press_edge" if strong else "marginal_continue"
    if fam == "check":
        return "missed_value" if strong else "marginal_continue"
    return "marginal_continue"


_WHY = {
    "press_edge": "You held the best of it (equity {e:.0%}) and applied pressure — "
                  "that's getting value with a real edge.",
    "missed_value": "Equity {e:.0%} was ahead, but checking left money on the table; "
                    "betting/raising captures value others would pay.",
    "chase": "Equity was only {e:.0%} and you put more in — that's paying off a "
             "likely-better hand. The price didn't justify continuing.",
    "disciplined_fold": "Equity {e:.0%} was behind with a poor price; folding stops "
                        "the bleed. Correct discipline.",
    "premature_fold": "Equity {e:.0%} was actually fine here; folding surrendered a "
                      "profitable continue.",
    "marginal_continue": "A close spot (equity {e:.0%}); your action is roughly "
                         "EV-neutral — defensible either way.",
}


# --------------------------------------------------------------------------- #
# Conditioned-equity helpers (heads-up). The opponent is modelled as a range
# that folds its weakest hands (MDF), so EVs use "equity when called", not
# equity vs a random hand.
# --------------------------------------------------------------------------- #
def _decision_range(game, g, seed):
    """Build the opponent-range samples ONCE for a decision, heads-up only.
    Returns (vs_random, samples, total_w) or None for multiway/fallback."""
    try:
        if game == "holdem":
            if max(1, int(g("num_live_opponents") or 1)) != 1:
                return None
            board = g("board_cards") or []
            samples, total_w, vs_random, combos = rng_mod.holdem_range_samples(
                g("hero_hole_cards"), board,
                iterations=(COND_ITERS_PRE if not board else COND_ITERS_POST),
                max_combos=(None if not board else COND_MAX_COMBOS), seed=seed)
            return samples, total_w, vs_random, combos
        else:
            opp_lists = g("opponents_up_cards")
            if opp_lists is None:
                legacy = g("villain_up_cards")
                opp_lists = [legacy] if legacy is not None else []
            if len(opp_lists) != 1:
                return None
            samples, total_w, vs_random = rng_mod.stud_range_samples(
                g("hero_known_cards"), opp_lists[0],
                iterations=STUD_ITERS, max_combos=STUD_MAX_COMBOS, seed=seed)
            return samples, total_w, vs_random, None      # no combo-level rollout for stud
    except Exception:
        return None          # never let the richer model break grading


def _multiway_grid(game, g, seed):
    """Build a multiway conditioned grid (each opponent folds its weakest hands,
    MDF) when there are 2+ live opponents. None otherwise."""
    try:
        if game == "holdem":
            n = max(1, int(g("num_live_opponents") or 1))
            if n < 2:
                return None
            return rng_mod.holdem_multiway_grid(
                g("hero_hole_cards"), g("board_cards") or [], n,
                iterations=MULTIWAY_ITERS, seed=seed)
        opp_lists = g("opponents_up_cards")
        if opp_lists is None:
            legacy = g("villain_up_cards")
            opp_lists = [legacy] if legacy is not None else []
        if len(opp_lists) < 2:
            return None
        return rng_mod.stud_multiway_grid(
            g("hero_known_cards"), opp_lists,
            iterations=MULTIWAY_ITERS, seed=seed)
    except Exception:
        return None


def _equity_vs_betting_range(samples, total_w):
    """Hero equity vs the hands that would BET — the strongest BET_RANGE_FREQ of
    the opponent's range (the low-hero-equity end of the sorted samples)."""
    if total_w <= 0:
        return 0.0
    target = BET_RANGE_FREQ * total_w
    w = e = 0.0
    for eq, ww in samples:           # ascending hero equity => strongest villain first
        if w >= target:
            break
        take = min(ww, target - w)
        e += eq * take
        w += take
    return (e / w) if w > 0 else (sum(eq * ww for eq, ww in samples) / total_w)


def _cond_option_evs(options, samples, total_w, vs_random, pot):
    """EV per option using the conditioned range: aggressive options face the
    top-MDF continue range plus GTO fold equity; calls face the betting range."""
    evs, extra = {}, {}
    for o in options:
        key, amt, label = o["key"], float(o.get("amount", 0.0)), o["label"]
        if key == "fold":
            ev, info = 0.0, {}
        elif key == "check":
            ev, info = vs_random * pot, {"e": vs_random}
        elif key == "call":
            e_call = _equity_vs_betting_range(samples, total_w)
            ev = e_call * pot - (1 - e_call) * amt
            info = {"e": e_call}
        else:                                    # bet / raise / all-in
            keep = rng_mod.mdf(amt, pot)
            e_called, _vf, _n = rng_mod.slice_continue(samples, total_w, keep)
            fe = amt / (pot + amt) if (pot + amt) > 0 else 0.0
            called = e_called * (pot + amt) - (1 - e_called) * amt
            ev = fe * pot + (1 - fe) * called
            info = {"e": e_called, "fold_equity": fe, "mdf": keep}
        evs[label] = round(ev, 3)
        extra[label] = info
    return evs, extra


def grade_hand(history: List) -> List[StreetGrade]:
    """Grade each recorded hero decision. `history` is a list of HeroDecision
    (dataclasses or dicts). Heads-up decisions are graded against a CONDITIONED
    opponent range (MDF continue range => equity when called); multiway spots
    fall back to the original equity-vs-field EV model."""
    grades: List[StreetGrade] = []
    for h in history:
        g = _wrapget(h)
        options = list(g("options") or [])
        action_key = g("action")
        chosen = next((o for o in options if o.get("key") == action_key), None)
        chosen_label = g("action_label") or (chosen["label"] if chosen else action_key)
        chosen_amt = float(chosen.get("amount", 0.0)) if chosen else 0.0
        pot = float(g("pot"))
        to_call = float(g("to_call"))
        game = g("game") or "stud"
        fam = betting.family(action_key) if action_key else "check"

        rng_ctx = _decision_range(game, g, GRADING_SEED)
        grid = None if rng_ctx is not None else _multiway_grid(game, g, GRADING_SEED)

        if rng_ctx is not None:
            # ---- conditioned (heads-up) model --------------------------------
            samples, total_w, vs_random, _combos = rng_ctx
            evs, extra = _cond_option_evs(options, samples, total_w, vs_random, pot)
            conditioned = True
            # equity that actually applies to the action hero took:
            if fam in ("bet", "raise", "allin"):
                keep = rng_mod.mdf(chosen_amt, pot)
                eff, _vf, _n = rng_mod.slice_continue(samples, total_w, keep)
                fold_eq = chosen_amt / (pot + chosen_amt) if (pot + chosen_amt) > 0 else 0.0
            elif fam == "call":
                eff = _equity_vs_betting_range(samples, total_w)
                keep, fold_eq = 1.0, 0.0
            else:
                eff, keep, fold_eq = vs_random, 1.0, 0.0
            headline = vs_random
        elif grid is not None:
            # ---- multiway conditioned model: each opponent folds weak (MDF) ---
            evs = {o["label"]: round(grid.ev_option(o, pot, to_call), 3) for o in options}
            conditioned = True
            vs_random = grid.vs_random
            if fam in ("bet", "raise", "allin"):
                keep = rng_mod.mdf(chosen_amt, pot)
                eff = grid.equity_when_called(chosen_amt, pot)
                fold_eq = grid.fold_all_prob(chosen_amt, pot)
            elif fam == "call":
                eff = grid.equity_vs_callers(to_call, pot)
                keep, fold_eq = 1.0, 0.0
            else:
                eff, keep, fold_eq = vs_random, 1.0, 0.0
            headline = vs_random
        else:
            # ---- multiway / fallback: original vs-field model ----------------
            if game == "holdem":
                headline = sv.holdem_equity(
                    g("hero_hole_cards"), g("board_cards") or [],
                    max(1, int(g("num_live_opponents") or 1)),
                    iterations=GRADING_ITERS, seed=GRADING_SEED)
            else:
                hero_cards = g("hero_known_cards")
                opp_lists = g("opponents_up_cards")
                if opp_lists is None:
                    legacy = g("villain_up_cards")
                    opp_lists = [legacy] if legacy is not None else []
                headline = sv.monte_carlo_equity_multi(
                    hero_cards, opp_lists, iterations=GRADING_ITERS, seed=GRADING_SEED)
            evs = _option_evs(options, headline, pot)
            vs_random, eff, keep, fold_eq, conditioned = headline, headline, 1.0, 0.0, False

        best_label = max(evs, key=evs.get) if evs else chosen_label
        best_opt = next((o for o in options if o["label"] == best_label), chosen)
        best_ev = evs.get(best_label, 0.0)
        taken_ev = evs.get(chosen_label, 0.0)
        denom = max(pot, 1.0)
        ev_loss = max(0.0, best_ev - taken_ev)
        loss_norm = min(1.0, ev_loss / denom)
        adherence = round(100.0 * (1.0 - loss_norm), 1)
        arche = _grade_archetype(fam, eff, loss_norm)   # EV-first, then equity
        math = _decision_math(chosen, best_opt, headline, pot, to_call,
                              taken_ev, best_ev, vs_random=vs_random,
                              eff=eff, conditioned=conditioned, fold_eq=fold_eq)
        grades.append(StreetGrade(
            street=g("street"), street_name=g("street_name"),
            action=chosen_label, options=options, equity=round(headline, 3),
            option_evs=evs, best_action=best_label,
            ev_loss_normalized=round(loss_norm, 3), adherence=adherence,
            archetype=arche, why=_WHY[arche].format(e=eff),
            pot=round(pot, 1), to_call=round(to_call, 1), math=math,
            equity_vs_random=round(vs_random, 3), equity_effective=round(eff, 3),
            fold_equity=round(fold_eq, 3), mdf=round(keep, 3),
            conditioned=conditioned,
        ))
    return grades


def _decision_math(chosen, best, e, pot, to_call, taken_ev, best_ev,
                   vs_random=None, eff=None, conditioned=False, fold_eq=0.0,
                   implied=None) -> str:
    """Plain-language EV math justifying the grade. When the conditioned model
    ran, the EV uses 'equity when called / vs a bettor' (`eff`) and contrasts it
    with the vs-a-random-hand figure (`vs_random`) — the exact lesson the trainer
    exists to teach. `implied`, if present, is the rollout implied-odds result."""
    if chosen is None:
        return ""
    key = chosen.get("key", "")
    amt = chosen.get("amount", 0.0)
    eff = e if eff is None else eff
    parts = ["(equity = your chance to win the pot; EV = average chips won/lost "
             "over many repeats.)"]

    if key == "call":
        be = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0
        # When the rollout ran, quote ITS internally-consistent showdown numbers
        # so the "true EV" line below reconciles exactly.
        e_show = implied.get("equity_realised", eff) if implied else eff
        ev_show = implied.get("ev_immediate", taken_ev) if implied else taken_ev
        parts.append(f"Pot {pot:g}, call {to_call:g}; break-even equity "
                     f"{to_call:g}/{pot + to_call:g} = {be:.0%}.")
        if conditioned and vs_random is not None:
            parts.append(
                f"Versus a RANDOM hand you'd have {vs_random:.0%} — but players betting "
                f"into you turn up with stronger hands, so your real equity is "
                f"~{e_show:.0%}. On showdown alone EV(call) = {e_show:.2f}×{pot:g} − "
                f"{1 - e_show:.2f}×{to_call:g} = {ev_show:+.2f} "
                f"({'above' if e_show >= be else 'below'} the price).")
        else:
            parts.append(f"You have {e_show:.0%}, so EV(call) = {e_show:.2f}×{pot:g} − "
                         f"{1 - e_show:.2f}×{to_call:g} = {ev_show:+.2f}.")
    elif key == "fold":
        parts.append(f"Folding = 0 EV. The best alternative was worth {best_ev:+.2f}, "
                     f"so folding is "
                     f"{'fine' if best_ev <= 0.05 else 'leaving chips behind'}.")
    elif key == "check":
        parts.append(f"Checking is free: you realise your {eff:.0%} share of the "
                     f"{pot:g} pot → EV ≈ {eff:.2f}×{pot:g} = {taken_ev:+.2f}.")
    else:  # bet / raise / all-in
        if conditioned:
            keep = rng_mod.mdf(amt, pot)
            vr = vs_random if vs_random is not None else e
            if eff < vr - 0.005:
                rel = f"drops to ~{eff:.0%}"
            elif eff > vr + 0.005:
                rel = f"is ~{eff:.0%} (a smaller but stronger field)"
            else:
                rel = f"holds ~{eff:.0%}"
            parts.append(
                f"Bet {amt:g} into {pot:g}: by MDF a defender continues with the top "
                f"{keep:.0%} of hands, folding {fold_eq:.0%} (your fold equity). Against "
                f"the hands that CALL your equity {rel} (vs {vr:.0%} against a random "
                f"hand). Blended EV ≈ {taken_ev:+.2f}.")
        else:
            fe = _fold_equity(amt, pot)
            parts.append(
                f"You risk {amt:g}. Fold equity ≈ {fe:.0%}; when called EV = "
                f"{eff:.2f}×(pot+{amt:g}) − {1 - eff:.2f}×{amt:g}. Blended ≈ "
                f"{taken_ev:+.2f}.")

    if implied:
        d = implied.get("implied_delta")
        ev_call = implied.get("ev_call")
        if d is not None and ev_call is not None:
            verb = "adds" if d >= 0 else "subtracts"
            tail = (" (you win extra when you improve)." if d >= 0
                    else " (reverse implied odds — you pay off better hands).")
            parts.append(f"Simulating the rest of the hand, implied odds {verb} "
                         f"{abs(d):.1f} chips → true EV ≈ {ev_call:+.1f}{tail}")

    if best is not None and best.get("label") != chosen.get("label"):
        gap = best_ev - taken_ev
        parts.append(f"Best option was “{best['label']}” at EV {best_ev:+.2f} — you "
                     f"gave up {gap:.2f} chips vs the optimal play.")
    else:
        parts.append("That was the highest-EV option.")
    return " ".join(parts)


def _wrapget(obj):
    if isinstance(obj, dict):
        return lambda k, d=None: obj.get(k, d)
    return lambda k, d=None: getattr(obj, k, d)


def hand_overview(grades: List[StreetGrade]) -> dict:
    scored = [g.adherence for g in grades]
    avg = round(sum(scored) / len(scored), 1) if scored else None
    worst = min(grades, key=lambda g: g.adherence) if grades else None
    best = max(grades, key=lambda g: g.adherence) if grades else None
    return {
        "average_adherence": avg,
        "n_decisions": len(grades),
        "strongest_street": best.street_name if best else None,
        "weakest_street": worst.street_name if worst else None,
        "headline": _headline(avg, worst),
    }


def _headline(avg, worst) -> str:
    if avg is None:
        return "No graded decisions in this hand."
    if avg >= 85:
        return "Strong hand — your decisions tracked the math well throughout."
    if avg >= 65:
        msg = "Solid overall, with room to tighten up"
        return f"{msg} on {worst.street_name} street." if worst else msg + "."
    return ("Several decisions drifted from the EV line"
            + (f", worst on {worst.street_name} street." if worst else "."))


def pick_key_decision(grades: List[StreetGrade]) -> Optional[StreetGrade]:
    """The most instructive decision: the biggest EV error, or if the hand was
    clean, the most decisive spot (latest street with the most extreme equity)."""
    if not grades:
        return None
    worst = max(grades, key=lambda g: g.ev_loss_normalized)
    if worst.ev_loss_normalized > 0.05:
        return worst
    # Clean hand: highlight the most decisive (extreme-equity) late decision.
    return max(grades, key=lambda g: (abs(g.equity - 0.5), g.street))


# =========================================================================== #
# 2. Markets translation
# =========================================================================== #

ARCHETYPE_MARKET = {
    "press_edge": {
        "lesson": "pressing a genuine edge",
        "analogy": (
            "Your hand is like a trade where the thesis is confirming in real "
            "time: the catalyst hit, volume backs it, and the tape agrees. In "
            "the hand you had the best of it and bet/raised to get value."),
        "question": (
            "Your breakout setup triggers exactly as planned — price clears the "
            "level on expanding volume and holds the retest. You're already long "
            "a starter. What do you do, and why?"),
        "model_answer": (
            "Add to the winner up to your planned full size and let it work with "
            "a defined stop. When the edge is real and confirming, pressing it is "
            "how you get paid — the analogue of value-betting a strong hand. "
            "Timidly holding the starter is the 'check a monster' mistake."),
        "teaching": (
            "Trading profit is lumpy: a small number of high-conviction, "
            "well-confirmed trades produce most of the year's gains. So when an "
            "edge is genuine AND confirmed (catalyst + volume + a held retest), "
            "the disciplined move is to size up to your planned maximum and let it "
            "run behind a defined stop — not to nibble. The skill is distinguishing "
            "a confirmed edge (press it) from a hopeful one (wait). Pressing "
            "unconfirmed setups is gambling; failing to press confirmed ones "
            "quietly caps your expectancy."),
        "terms": ["edge", "expected value (EV)", "conviction sizing",
                  "fold equity", "volume confirmation", "retest"],
    },
    "missed_value": {
        "lesson": "leaving value on the table",
        "analogy": (
            "You were ahead but checked — like being right on a position and "
            "scratching out a tiny scalp instead of sizing into a high-conviction "
            "move."),
        "question": (
            "Your highest-conviction signal of the week fires with everything "
            "aligned, but you take a minimal position 'to be safe.' The move runs "
            "without you. What should you have done?"),
        "model_answer": (
            "Size in proportion to conviction and edge. Under-betting your best "
            "spots quietly caps your upside the same way checking the nuts does — "
            "the rare loss you avoid doesn't pay for all the value you forgo."),
        "teaching": (
            "Most traders obsess over avoiding losses and under-attend to "
            "capturing value. But long-run results are driven as much by how big "
            "you win when you're right as by how often. Systematically taking "
            "minimal size on your strongest, most-confirmed setups is a real, "
            "invisible leak: it doesn't show up as a loss, it shows up as a "
            "ceiling on your equity curve. Conviction sizing — bigger on your best "
            "ideas, smaller on marginal ones — is how you convert being right into "
            "being paid."),
        "terms": ["conviction sizing", "edge", "expected value (EV)",
                  "position sizing", "expectancy"],
    },
    "chase": {
        "lesson": "chasing / paying off a worse position",
        "analogy": (
            "You continued with the worst of it for a bad price — like averaging "
            "down into a position whose thesis is breaking, hoping the next tick "
            "saves you."),
        "question": (
            "You're long; the catalyst you traded has been invalidated and price "
            "is grinding against you, but you 'don't want to sell the low.' Adding "
            "would lower your average. What's the disciplined move?"),
        "model_answer": (
            "Stop adding and exit (or cut to a starter). Averaging into a broken "
            "thesis for a poor price is the market version of calling with dead "
            "outs — every extra chip is negative expectancy. Protect capital; "
            "re-enter only on a fresh, valid signal."),
        "teaching": (
            "The cost of continuing is set by pot odds: the price to stay in "
            "versus what you can win. When the thesis that justified a trade is "
            "invalidated, your win probability collapses and any further capital "
            "you commit is negative-EV — no matter how 'cheap' the average looks. "
            "Averaging down feels productive but usually just enlarges a loser and "
            "ties up capital and attention. The professional habit: define an "
            "invalidation level (a stop) BEFORE entering, size to edge on the way "
            "in, and exit without negotiation when the level breaks. Capital "
            "preserved is capital available for the next real edge."),
        "terms": ["averaging down", "thesis", "pot odds", "stop-loss",
                  "expectancy", "expected value (EV)"],
    },
    "disciplined_fold": {
        "lesson": "cutting a loser cleanly",
        "analogy": (
            "You folded when you were behind with a bad price — the equivalent of "
            "hitting your stop and getting out without negotiating with the "
            "position."),
        "question": (
            "A trade hits your predefined stop. The story 'could still work out' "
            "and the spread makes exiting feel expensive. What do you do?"),
        "model_answer": (
            "Honor the stop and exit. A correct fold and a clean stop are the same "
            "skill: refusing to invest more into a negative-expectancy spot just "
            "because you're already in it. The discipline, not the individual "
            "result, is what compounds."),
        "teaching": (
            "A stop-loss is a pre-commitment that protects you from your in-the-"
            "moment self, who will always find a reason the trade 'could still "
            "work.' Honoring stops bounds your worst outcomes, which is what keeps "
            "drawdowns survivable and keeps you in the game long enough for your "
            "edge to show up. The occasional trade that stops you out and then "
            "reverses is the price of admission — over many trades, refusing to "
            "honor stops is far more expensive. Judge the decision, not the one "
            "stop that would have been better to skip."),
        "terms": ["stop-loss", "expectancy", "thesis", "drawdown",
                  "expected value (EV)"],
    },
    "premature_fold": {
        "lesson": "bailing on a still-good position",
        "analogy": (
            "You folded a hand that was actually fine — like panic-closing a valid "
            "position on a normal pullback inside your plan."),
        "question": (
            "Your thesis is intact and price is simply oscillating within the "
            "noise you expected, but it's uncomfortable. Do you cut, and why?"),
        "model_answer": (
            "Hold per the plan. Exiting a position whose edge is intact because of "
            "ordinary discomfort forfeits expected value — the analogue of folding "
            "a hand that was ahead. Let the thesis, not the wiggle, decide."),
        "teaching": (
            "Every valid trade comes with expected noise — normal wiggle that is "
            "not the same as your thesis breaking. Cutting good positions at the "
            "first discomfort feels safe but it pays the spread repeatedly and "
            "denies your edge the room it needs to work, capping winners while "
            "doing nothing for risk. The fix is mechanical: decide your "
            "invalidation level in advance, then let price move within the noise "
            "band without reacting. Distinguish 'my thesis is wrong' (exit) from "
            "'this is uncomfortable but expected' (hold)."),
        "terms": ["thesis", "variance", "position sizing", "stop-loss"],
    },
    "marginal_continue": {
        "lesson": "managing a coin-flip spot",
        "analogy": (
            "A genuinely close spot — like a trade sitting right at its expectancy "
            "threshold, where either action is defensible."),
        "question": (
            "A setup is borderline: the edge is real but thin and conditions are "
            "mixed. How do you decide whether to take it, and how do you size?"),
        "model_answer": (
            "Take it only at reduced size, or pass — and be consistent. Marginal "
            "edges should get marginal risk. The error isn't the action; it's "
            "betting big on thin edges or agonizing over spots that barely move "
            "your bottom line."),
        "teaching": (
            "Not every decision is high-stakes, and treating thin edges as if they "
            "were is its own leak. The right response to a marginal spot is "
            "marginal risk: small size, or a clean pass, applied consistently so "
            "your sizing always reflects how much edge you actually have. Save your "
            "focus and your capital for the spots that move your bottom line; don't "
            "agonize over coin-flips or — worse — size them up to feel decisive."),
        "terms": ["edge", "expectancy", "position sizing", "variance",
                  "conviction sizing"],
    },
}


def _resolve_terms(archetype: str, *texts: str) -> List[dict]:
    """Build the 'Key terms' list for a translation: the archetype's curated
    terms plus any other glossary terms that appear in the generated text, each
    with its accurate definition from the glossary (never the LLM's)."""
    entry = ARCHETYPE_MARKET.get(archetype, {})
    keys = list(entry.get("terms", []))
    for t in texts:
        for k in gloss.find_in_text(t or ""):
            if k not in keys:
                keys.append(k)
    return gloss.lookup(keys)


def _hand_summary_text(grades: List[StreetGrade], key: StreetGrade) -> str:
    lines = []
    for g in grades:
        lines.append(f"{g.street_name}: chose {g.action} (equity {g.equity:.0%}, "
                     f"best {g.best_action}, adherence {g.adherence}).")
    lines.append(f"KEY DECISION: {key.street_name} street — {key.action} with "
                 f"equity {key.equity:.0%}; archetype {key.archetype}.")
    return " ".join(lines)


class LibraryMarketsCoach:
    """Deterministic, offline analogy library keyed by decision archetype."""
    source = "library"

    def translate(self, grades, key: StreetGrade) -> dict:
        entry = ARCHETYPE_MARKET.get(key.archetype, ARCHETYPE_MARKET["marginal_continue"])
        return {
            "source": self.source,
            "lesson": entry["lesson"],
            "analogy": entry["analogy"],
            "question": entry["question"],
            "model_answer": entry["model_answer"],
            "teaching": entry.get("teaching", ""),
            "terms": _resolve_terms(key.archetype, entry.get("analogy", ""),
                                    entry.get("question", ""),
                                    entry.get("model_answer", ""),
                                    entry.get("teaching", "")),
        }

    def evaluate_answer(self, question, model_answer, user_answer) -> dict:
        # Lightweight overlap heuristic; the model answer is always shown.
        ua = (user_answer or "").lower()
        keys = ["stop", "exit", "cut", "size", "add", "plan", "edge", "expectancy",
                "risk", "thesis", "discipline", "stick", "hold"]
        hits = sum(1 for k in keys if k in ua)
        score = max(20, min(100, 30 + hits * 12)) if ua.strip() else 0
        return {
            "source": self.source,
            "score": score,
            "feedback": ("Compare your reasoning to the model answer below — the key "
                         "is whether you reasoned from edge/expectancy and risk, not "
                         "from the outcome."),
            "model_answer": model_answer,
        }


class LLMMarketsCoach:
    """Uses the user's own ANTHROPIC_API_KEY to generate a tailored analogy and
    to grade the trainee's answer. Falls back to the library on any failure."""
    source = "llm"

    def __init__(self):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = os.environ.get("DQ_LLM_MODEL", "claude-sonnet-4-6")
        self.fallback = LibraryMarketsCoach()

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _call(self, system: str, prompt: str, max_tokens: int = 900) -> str:
        body = json.dumps({
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": self.key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data.get("content", []))

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return None
        return None

    def translate(self, grades, key: StreetGrade) -> dict:
        if not self.available:
            return self.fallback.translate(grades, key)
        system = ("You are a trading mentor who teaches decision-making through "
                  "poker. Map the poker decision to a concrete markets situation "
                  "and TEACH the underlying trading principle. Reply ONLY with "
                  "strict JSON having keys: lesson, analogy, question, "
                  "model_answer, teaching. `teaching` is 3-5 sentences that "
                  "explain the trading concept educationally (edge, expected "
                  "value, pot odds/risk-reward, position sizing, stops, thesis "
                  "invalidation) so a beginner learns how to trade. The question "
                  "must ask the trainee what THEY would do in the market analogue.")
        prompt = ("Hand summary: " + _hand_summary_text(grades, key) +
                  "\n\nProduce the JSON now.")
        try:
            out = self._call(system, prompt)
            data = self._extract_json(out)
            if not data or not all(k in data for k in
                                   ("lesson", "analogy", "question", "model_answer")):
                raise ValueError("bad JSON shape")
            data["source"] = self.source
            data.setdefault("teaching", "")
            # Definitions always come from our glossary (accurate + consistent),
            # matched against the archetype and the generated text.
            data["terms"] = _resolve_terms(
                key.archetype, data.get("analogy", ""), data.get("question", ""),
                data.get("model_answer", ""), data.get("teaching", ""))
            return data
        except Exception:
            return self.fallback.translate(grades, key)

    def evaluate_answer(self, question, model_answer, user_answer) -> dict:
        if not self.available:
            return self.fallback.evaluate_answer(question, model_answer, user_answer)
        system = ("Grade the trainee's answer to a trading question for decision "
                  "QUALITY (reasoning from edge/expectancy and risk, not from "
                  "outcomes). Reply ONLY strict JSON: {\"score\": 0-100, "
                  "\"feedback\": \"...\"}.")
        prompt = (f"Question: {question}\nModel answer: {model_answer}\n"
                  f"Trainee answer: {user_answer}\n\nGrade now.")
        try:
            out = self._call(system, prompt, max_tokens=400)
            data = self._extract_json(out) or {}
            score = int(data.get("score", 60))
            return {"source": self.source, "score": max(0, min(100, score)),
                    "feedback": data.get("feedback", ""), "model_answer": model_answer}
        except Exception:
            return self.fallback.evaluate_answer(question, model_answer, user_answer)


def get_coach():
    """Return the LLM coach if a key is configured, else the library coach."""
    llm = LLMMarketsCoach()
    return llm if llm.available else LibraryMarketsCoach()


# =========================================================================== #
# Convenience: full report for a finished hand
# =========================================================================== #

def _attach_implied(key, history):
    """Run the multi-street rollout for the spotlight CALL spot (heads-up) and
    fold implied / reverse-implied odds into the key decision — Hold'em or stud."""
    try:
        h = next((x for x in history if _wrapget(x)("street") == key.street), None)
        if h is None:
            return
        g = _wrapget(h)
        game = g("game") or "stud"
        if betting.family(g("action")) != "call" or key.to_call <= 0:
            return

        res = None
        if game == "holdem":
            if max(1, int(g("num_live_opponents") or 1)) != 1:
                return
            board = g("board_cards") or []
            if not board:
                return                              # need a board to model future streets
            hero = g("hero_hole_cards")
            # Same betting range the EV grid used (deterministic seed): the
            # opponent's strongest half by equity vs hero.
            _s, _t, _vr, combos = rng_mod.holdem_range_samples(
                hero, board, iterations=COND_ITERS_POST,
                max_combos=COND_MAX_COMBOS, seed=GRADING_SEED)
            keep = max(1, int(len(combos) * BET_RANGE_FREQ))
            res = ro.holdem_call_ev(
                hero, board, pot=key.pot, to_call=key.to_call,
                villain_range_combos=combos[:keep], trials=ROLLOUT_TRIALS,
                seed=GRADING_SEED)
        else:
            opp_lists = g("opponents_up_cards")
            if opp_lists is None:
                legacy = g("villain_up_cards")
                opp_lists = [legacy] if legacy is not None else []
            if len(opp_lists) != 1:
                return
            hero, vup = g("hero_known_cards"), opp_lists[0]
            from itertools import combinations
            pool = rng_mod.remaining_deck(rng_mod.as_cards(hero) + rng_mod.as_cards(vup))
            hidden = list(combinations(pool, 2))
            hidden.sort(key=lambda hc: rng_mod._stud_partial_strength(
                rng_mod.as_cards(vup) + list(hc)), reverse=True)
            keep = max(1, int(len(hidden) * BET_RANGE_FREQ))
            res = ro.stud_call_ev(
                hero, vup, pot=key.pot, to_call=key.to_call,
                villain_hidden_range=hidden[:keep], trials=ROLLOUT_TRIALS,
                seed=GRADING_SEED)

        if not res:
            return
        key.implied = res
        chosen = next((o for o in key.options if o.get("label") == key.action), None)
        best_opt = next((o for o in key.options if o.get("label") == key.best_action), chosen)
        key.math = _decision_math(
            chosen, best_opt, key.equity, key.pot, key.to_call,
            key.option_evs.get(key.action, 0.0),
            key.option_evs.get(key.best_action, 0.0),
            vs_random=key.equity_vs_random, eff=key.equity_effective,
            conditioned=key.conditioned, fold_eq=key.fold_equity, implied=res)
    except Exception:
        pass            # implied odds are a bonus; never break the report


def analytics(history):
    """FAST path: grade every decision + attach implied odds, but SKIP the
    markets translation (a slow LLM/network call). Returns
    (report_dict, grades, key) so the caller can compute the translation
    separately/lazily. `report_dict["translation"]` is None here.

    This is what makes the hand report appear in a couple of seconds instead of
    waiting on the AI markets lesson."""
    grades = grade_hand(history)
    overview = hand_overview(grades)
    key = pick_key_decision(grades)
    if key is not None:
        _attach_implied(key, history)
    report = {
        "overview": overview,
        "streets": [asdict(g) for g in grades],
        "key_decision": asdict(key) if key else None,
        "translation": None,
    }
    return report, grades, key


def translation_for(grades, key, coach=None) -> Optional[dict]:
    """The markets translation for a graded hand — the slow step, isolated so it
    can run after the analytics are already on screen."""
    if key is None:
        return None
    coach = coach or get_coach()
    return coach.translate(grades, key)


def full_report(history, coach=None) -> dict:
    """Analytics + markets translation in one call (used by tests/CLI). The web
    app uses `analytics()` + `translation_for()` so the slow translation doesn't
    block the numbers."""
    coach = coach or get_coach()
    report, grades, key = analytics(history)
    report["translation"] = translation_for(grades, key, coach)
    report["coach_source"] = getattr(coach, "source", "library")
    return report


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import stud_game as game
    st = game.new_hand(seed=7)
    while not st.finished:
        opts = st.awaiting["options"]
        choice = "call" if "call" in opts else ("check" if "check" in opts else opts[0])
        st = game.act(st, choice)
    rep = full_report(st.history)
    print("Overview:", rep["overview"])
    for s in rep["streets"]:
        print(f"  {s['street_name']:>4}: {s['action']:<5} eq {s['equity']:.0%} "
              f"best {s['best_action']:<5} adher {s['adherence']}  [{s['archetype']}]")
    print("\nKey decision:", rep["key_decision"]["street_name"],
          rep["key_decision"]["archetype"])
    print("Coach source:", rep["coach_source"])
    print("Markets question:", rep["translation"]["question"])
