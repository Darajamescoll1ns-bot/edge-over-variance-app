"""
glossary.py — Definitions of key markets & grading terms
========================================================

Two vocabularies the platform teaches:

  * MARKET_TERMS  — trading concepts used in the markets translation at the end
                    of each hand. Surfaced as a clickable "Key terms" tab so the
                    player can learn the language of trading as they go.
  * GRADING_TERMS — the concepts a decision is scored on (the five dimensions
                    plus equity, EV-loss, Brier). Surfaced on the Sessions tab so
                    players understand what each number means.

Definitions are deliberately concise, accurate, and written to connect the
poker decision to the trading idea. Standard-library only.
"""

from __future__ import annotations

from typing import Dict, List


MARKET_TERMS: Dict[str, str] = {
    "expected value (EV)":
        "The average result of a decision if you could repeat it many times — "
        "the probability-weighted sum of every outcome. Positive-EV decisions "
        "make money over the long run even when individual results swing.",
    "edge":
        "A genuine, repeatable reason to expect profit — a real advantage over "
        "the other side of the trade. With no edge you are just paying the "
        "spread to gamble.",
    "pot odds":
        "The price you are offered to continue: the cost to call versus the size "
        "of the pot. The trading analogue is risk/reward — what you risk versus "
        "what you stand to gain. You continue only when your win probability "
        "beats that price.",
    "expectancy":
        "Average profit per trade given your win rate and win/loss sizes: "
        "(win% x avg win) - (loss% x avg loss). The trading equivalent of a "
        "hand's EV.",
    "R-multiple":
        "Profit or loss measured in units of the risk you took (R). Risking $100 "
        "to make $300 and hitting target is +3R; getting stopped is -1R. It lets "
        "you compare trades of different sizes on one scale.",
    "position sizing":
        "How much capital you commit to a trade. Disciplined sizing scales with "
        "edge and is capped by a risk limit; it is the single biggest driver of "
        "long-run survival.",
    "stop-loss":
        "A predefined exit price that caps your loss. Honoring a stop is the "
        "trading version of folding a beaten hand — refusing to invest more in a "
        "negative-expectancy spot.",
    "drawdown":
        "A decline from your account's peak value. Deep drawdowns raise the risk "
        "of ruin and are a primary trigger for stress and tilt.",
    "fold equity":
        "In poker, the extra value of a bet because opponents may fold. In "
        "trading, the analogue is initiative — pressing a confirmed edge so the "
        "move works for you instead of waiting passively.",
    "breakout":
        "Price moving decisively beyond an established range or level, often "
        "signalling the start of a new trend.",
    "retest":
        "After a breakout, price returning to the broken level to confirm it now "
        "acts as support or resistance. A held retest is a higher-quality entry "
        "than chasing the first move.",
    "fakeout (false breakout)":
        "A breakout that fails and snaps back into the range, trapping traders "
        "who chased. Confirmation (volume, a holding retest) filters these out.",
    "liquidity":
        "How easily you can enter or exit without moving the price. Thin "
        "liquidity widens spreads and slippage and calls for smaller size.",
    "slippage":
        "The gap between the price you expected and the price you actually got — "
        "worse in thin or fast-moving markets.",
    "thesis":
        "The specific reason you put a trade on. When the thesis is invalidated "
        "(the catalyst fails or the level breaks), the edge is gone and the "
        "position should be cut.",
    "averaging down":
        "Adding to a losing position to lower your average entry. Dangerous when "
        "the thesis is broken — the market version of calling with dead outs.",
    "conviction sizing":
        "Sizing in proportion to how strong and confirmed your edge is — larger "
        "on high-conviction setups, smaller on marginal ones.",
    "catalyst":
        "An event or signal expected to move price (earnings, a level break, "
        "news). Trades are often built around a specific catalyst.",
    "volume confirmation":
        "Using trading volume to validate a move — a breakout on expanding "
        "volume is more trustworthy than one on fading volume.",
    "variance":
        "The natural spread of outcomes around the average. High variance means "
        "results swing widely even when decisions are correct — which is exactly "
        "why outcome does not equal decision quality.",
    "tilt":
        "Emotionally driven, off-process decisions, usually after losses or "
        "stress. Tilt control is staying disciplined when it is hardest.",
}


GRADING_TERMS: Dict[str, str] = {
    "decision quality vs. outcome":
        "This platform scores the QUALITY of a decision given what you knew — not "
        "whether it happened to win. A correct fold that would have won is still "
        "a good decision; a reckless call that won is still a bad one.",
    "equity":
        "Your probability of winning the hand at showdown given the known cards. "
        "It is the objective benchmark every decision is graded against.",
    "policy adherence / EV-loss":
        "How close your action was to the highest expected-value play. EV-loss is "
        "the value you gave up by deviating; adherence is 100 when you choose the "
        "best available action.",
    "calibration":
        "Whether your stated probabilities match reality — when you say 70%, do "
        "you actually win about 70% of the time? Measured by the reliability term "
        "of the Brier score (lower miscalibration is better).",
    "resolution":
        "Whether your probability estimates are informative — do you push them "
        "toward 0 and 1 when warranted instead of always hugging the base rate? "
        "The discrimination term of the Brier decomposition.",
    "Brier score":
        "A proper scoring rule for probability forecasts: the mean squared error "
        "between your stated probabilities and the outcomes. It decomposes into "
        "calibration (reliability), resolution, and uncertainty.",
    "sizing discipline":
        "Whether your bet or position size tracks your edge — bigger when you are "
        "a strong favorite, smaller when marginal. Graded from the relationship "
        "between size and estimated edge.",
    "outcome-independence":
        "Whether your decision quality holds steady after a loss. A drop in "
        "quality following losses is a tilt or chasing signal.",
    "tilt control":
        "Whether your decision quality survives rising stress — loss streaks, "
        "drawdown, snap decisions. Stable quality under pressure scores high.",
}


def lookup(keys: List[str], source: Dict[str, str] = None) -> List[dict]:
    """Resolve a list of term keys to [{term, definition}], skipping unknowns."""
    out = []
    seen = set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        if source is not None and k in source:
            out.append({"term": k, "definition": source[k]})
        elif k in MARKET_TERMS:
            out.append({"term": k, "definition": MARKET_TERMS[k]})
        elif k in GRADING_TERMS:
            out.append({"term": k, "definition": GRADING_TERMS[k]})
    return out


def _match_token(term: str) -> str:
    """The substring we scan for: the part of the term before any parenthesis."""
    return term.split("(")[0].strip().lower()


def find_in_text(text: str, source: Dict[str, str] = None) -> List[str]:
    """Return the market-term keys whose name appears in `text` (case-insensitive)."""
    source = source or MARKET_TERMS
    low = (text or "").lower()
    hits = []
    for term in source:
        token = _match_token(term)
        if token and token in low:
            hits.append(term)
    return hits


def grading_terms() -> List[dict]:
    """All grading terms as [{term, definition}], in display order."""
    return [{"term": k, "definition": v} for k, v in GRADING_TERMS.items()]


if __name__ == "__main__":
    print(f"{len(MARKET_TERMS)} market terms, {len(GRADING_TERMS)} grading terms")
    print("find_in_text demo:",
          find_in_text("Honor your stop-loss; the thesis is invalidated and adding "
                       "would just be averaging down past good pot odds."))
