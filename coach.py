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

GRADING_SEED = 12345        # fixed so grades are reproducible
GRADING_ITERS = 1600


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


def grade_hand(history: List) -> List[StreetGrade]:
    """Grade each recorded hero decision. `history` is a list of HeroDecision
    (from stud_game) — dataclasses or dicts."""
    grades: List[StreetGrade] = []
    for h in history:
        g = _wrapget(h)
        options = list(g("options") or [])
        action_key = g("action")
        chosen = next((o for o in options if o.get("key") == action_key), None)
        chosen_label = g("action_label") or (chosen["label"] if chosen else action_key)
        pot = float(g("pot"))
        to_call = float(g("to_call"))
        game = g("game") or "stud"

        if game == "holdem":
            # Hold'em: equity from hole cards + shared board vs N unknown hands.
            equity = sv.holdem_equity(
                g("hero_hole_cards"), g("board_cards") or [],
                max(1, int(g("num_live_opponents") or 1)),
                iterations=GRADING_ITERS, seed=GRADING_SEED)
        else:
            # Stud: equity vs every live opponent's exposed up-cards.
            hero_cards = g("hero_known_cards")
            opp_lists = g("opponents_up_cards")
            if opp_lists is None:
                legacy = g("villain_up_cards")     # backward-compat with old snapshots
                opp_lists = [legacy] if legacy is not None else []
            equity = sv.monte_carlo_equity_multi(
                hero_cards, opp_lists, iterations=GRADING_ITERS, seed=GRADING_SEED)

        evs = _option_evs(options, equity, pot)
        best_label = max(evs, key=evs.get) if evs else chosen_label
        best_opt = next((o for o in options if o["label"] == best_label), chosen)
        best_ev = evs.get(best_label, 0.0)
        taken_ev = evs.get(chosen_label, 0.0)
        denom = max(pot, 1.0)
        ev_loss = max(0.0, best_ev - taken_ev)
        loss_norm = min(1.0, ev_loss / denom)
        adherence = round(100.0 * (1.0 - loss_norm), 1)
        fam = betting.family(action_key) if action_key else "check"
        arche = _archetype(fam, equity)
        math = _decision_math(chosen, best_opt, equity, pot, to_call,
                              taken_ev, best_ev)
        grades.append(StreetGrade(
            street=g("street"), street_name=g("street_name"),
            action=chosen_label, options=options, equity=round(equity, 3),
            option_evs=evs, best_action=best_label,
            ev_loss_normalized=round(loss_norm, 3), adherence=adherence,
            archetype=arche, why=_WHY[arche].format(e=equity),
            pot=round(pot, 1), to_call=round(to_call, 1), math=math,
        ))
    return grades


def _decision_math(chosen, best, e, pot, to_call, taken_ev, best_ev) -> str:
    """Plain-language EV math justifying the grade — concise, but with a quick
    note on what each figure means. `e` = your win probability (equity); pot and
    amounts are in chips; EV is the average chip result of repeating the spot."""
    e_pct = f"{e:.0%}"
    parts = []
    if chosen is None:
        return ""
    key = chosen.get("key", "")
    amt = chosen.get("amount", 0.0)
    # One-line key so the numbers aren't opaque.
    parts.append(f"(equity {e_pct} = your chance to win the pot; EV = average chips "
                 f"won/lost if you made this play many times.)")

    if key == "call":
        be = to_call / (pot + to_call) if (pot + to_call) > 0 else 0.0
        parts.append(
            f"Pot is {pot:g}, it costs {to_call:g} to call. Break-even equity = "
            f"cost ÷ (pot + cost) = {to_call:g}/{pot + to_call:g} = {be:.0%} — the "
            f"win rate that makes calling free. You have {e_pct}, "
            f"{'above' if e >= be else 'below'} that, so EV(call) = "
            f"{e:.2f}×{pot:g} (win the pot) − {1 - e:.2f}×{to_call:g} (lose the call) "
            f"= {taken_ev:+.2f}.")
    elif key == "fold":
        parts.append(f"Folding always = 0 EV (you put in nothing more). With {e_pct} "
                     f"equity the best alternative was worth {best_ev:+.2f}, so folding "
                     f"is {'fine' if best_ev <= 0.05 else 'leaving chips behind'}.")
    elif key == "check":
        parts.append(f"Checking costs nothing, so you simply realise your {e_pct} share "
                     f"of the {pot:g} pot → EV ≈ {e:.2f}×{pot:g} = {taken_ev:+.2f}.")
    else:  # bet / raise / all-in
        fe = _fold_equity(amt, pot)
        parts.append(
            f"You risk {amt:g} chips. Fold equity ≈ {fe:.0%} (how often the bet wins "
            f"the {pot:g} pot uncontested); when called, EV = {e:.2f}×(pot+{amt:g}) − "
            f"{1 - e:.2f}×{amt:g}. Blended EV ≈ {taken_ev:+.2f}.")

    if best is not None and best.get("label") != chosen.get("label"):
        gap = best_ev - taken_ev
        parts.append(f"Best option was “{best['label']}” at EV {best_ev:+.2f} — you "
                     f"gave up {gap:.2f} chips versus the optimal play.")
    else:
        parts.append("That was the highest-EV option — correctly sized.")
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

def full_report(history, coach=None) -> dict:
    coach = coach or get_coach()
    grades = grade_hand(history)
    overview = hand_overview(grades)
    key = pick_key_decision(grades)
    translation = coach.translate(grades, key) if key else None
    return {
        "overview": overview,
        "streets": [asdict(g) for g in grades],
        "key_decision": asdict(key) if key else None,
        "translation": translation,
        "coach_source": getattr(coach, "source", "library"),
    }


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
