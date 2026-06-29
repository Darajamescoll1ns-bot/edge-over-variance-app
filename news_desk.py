"""
news_desk.py — Markets Desk: 3 daily news-analogy hands  (Edge Over Variance)
=============================================================================

Each day the desk presents THREE "hands": a real, recent news item (macro,
markets, tech, energy, etc.) turned into a trading decision. The trainee reads
the news, picks how they'd act AND how big — then gets graded on decision
quality (was the reasoning sound? was the sizing matched to the edge?) with the
markets lesson and key terms revealed.

WHY IT WORKS THIS WAY
---------------------
The deployed app has no live news feed, so the desk reads `daily_desk.json` from
disk. That file is regenerated each morning by a scheduled task (which searches
the day's news and rewrites it). If the file is missing or stale, the in-code
default desk (seeded with the latest news at build time) is used, so the tab
always works offline.

Each option carries a `quality` (0-100) and a `note`; the highest-quality option
is the model answer. Sizing is built into the options (stand aside / part size /
full size) to keep teaching sizing discipline.

Standard-library only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import date
from typing import List, Optional

import glossary as gloss

DESK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_desk.json")


@dataclass
class Option:
    key: str
    label: str
    quality: int                 # 0-100 decision-quality score for this choice
    note: str = ""               # why this choice is good/poor


@dataclass
class NewsHand:
    hand_id: str
    category: str                # "macro" | "markets" | "tech" | "energy" | ...
    headline: str
    source: str
    source_url: str
    summary: str                 # the actual news, in a sentence or two
    analogy: str                 # how it maps to a poker/trading decision
    prompt: str                  # the question put to the trainee
    options: List[Option]
    best_key: str
    explanation: str             # model answer
    teaching: str                # the durable trading principle
    terms: List[str] = field(default_factory=list)

    def score_response(self, option_key: str) -> dict:
        chosen = next((o for o in self.options if o.key == option_key), None)
        if chosen is None:
            raise ValueError(f"unknown option {option_key!r}")
        best = next(o for o in self.options if o.key == self.best_key)
        return {
            "hand_id": self.hand_id,
            "chosen": option_key,
            "chosen_label": chosen.label,
            "quality": chosen.quality,
            "note": chosen.note,
            "correct": option_key == self.best_key,
            "best_label": best.label,
            "explanation": self.explanation,
            "teaching": self.teaching,
            "terms": gloss.lookup(self.terms),
        }

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "NewsHand":
        opts = [Option(**o) for o in d.get("options", [])]
        return NewsHand(
            hand_id=d["hand_id"], category=d.get("category", ""),
            headline=d["headline"], source=d.get("source", ""),
            source_url=d.get("source_url", ""), summary=d.get("summary", ""),
            analogy=d.get("analogy", ""), prompt=d.get("prompt", ""),
            options=opts, best_key=d["best_key"], explanation=d.get("explanation", ""),
            teaching=d.get("teaching", ""), terms=list(d.get("terms", [])),
        )


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def save_desk(hands: List[NewsHand], for_date: str, path: str = DESK_PATH) -> str:
    payload = {"date": for_date, "hands": [h.to_dict() for h in hands]}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load_desk(path: str = DESK_PATH) -> dict:
    """Return {date, hands:[NewsHand]}. Falls back to the in-code default desk
    if the file is missing or unreadable."""
    if os.path.exists(path):
        try:
            with open(path) as fh:
                data = json.load(fh)
            return {"date": data.get("date", ""),
                    "hands": [NewsHand.from_dict(h) for h in data.get("hands", [])]}
        except Exception:
            pass
    return {"date": DEFAULT_DATE, "hands": build_default_desk()}


def get_hand(hand_id: str, path: str = DESK_PATH) -> Optional[NewsHand]:
    return next((h for h in load_desk(path)["hands"] if h.hand_id == hand_id), None)


# --------------------------------------------------------------------------- #
# The in-code default desk — seeded from real news at build time (2026-06-22)
# --------------------------------------------------------------------------- #
DEFAULT_DATE = "2026-06-22"


def build_default_desk() -> List[NewsHand]:
    return [
        NewsHand(
            hand_id="fed_hawkish_hold",
            category="macro",
            headline="Fed holds rates, drops easing bias in Chair Warsh's first meeting",
            source="CNBC / Federal Reserve",
            source_url="https://www.cnbc.com/2026/06/17/fed-interest-rate-decision-june-2026.html",
            summary=("The Fed left rates at 3.50–3.75% for a fourth straight meeting but "
                     "stripped its easing bias; new projections raised PCE inflation to "
                     "3.6% and showed officials leaning toward a HIKE, with traders now "
                     "eyeing October."),
            analogy=("The regime just changed: the card you were waiting for (rate cuts) "
                     "is now dead, and the board points the other way. Your old read is "
                     "invalidated — like holding a draw that just got counterfeited."),
            prompt=("You were positioned for cuts (long long-duration bonds / rate-"
                    "sensitive growth). The Fed just turned hawkish. What's your move, "
                    "and how big?"),
            options=[
                Option("add_full", "Add full size to the rate-cut bet — it'll reverse",
                       quality=10,
                       note="Adding to a thesis the new information just invalidated — "
                            "the textbook chase / averaging into a dead draw."),
                Option("reduce", "Cut/lighten the rate-cut position; respect the new bias",
                       quality=92,
                       note="The catalyst flipped; you trade the board you're shown, not "
                            "the one you wanted. Reducing risk is the disciplined fold."),
                Option("flip_small", "Take a SMALL position for higher-for-longer",
                       quality=70,
                       note="Reasonable direction, but the move is partly priced and "
                            "headline risk is high — small size is appropriate, not full."),
                Option("stand", "Stand aside until the dust settles", quality=66,
                       note="No edge, no trade is always defensible; you forgo a bit of "
                            "expected value but take zero risk into uncertainty."),
            ],
            best_key="reduce",
            explanation=("When the catalyst behind a trade is invalidated, the first job is "
                         "to stop the bleed — cut or lighten the position. Adding is the "
                         "worst choice (negative expectancy into a broken thesis). A small "
                         "higher-for-longer position is fine, but full size ignores that "
                         "the shift is partly priced."),
            teaching=("Markets, like cards, hand you new public information constantly. When "
                      "it invalidates your thesis, your edge is gone and conviction must "
                      "drop with it. Reducing risk on a regime change isn't weakness — it's "
                      "refusing to pay into a negative-EV spot. Re-enter only on a fresh, "
                      "valid signal, sized to the new (smaller) edge."),
            terms=["thesis", "catalyst", "expected value (EV)", "position sizing",
                   "averaging down", "stop-loss"],
        ),
        NewsHand(
            hand_id="semis_selloff_broadcom",
            category="tech",
            headline="Broadcom −15% drags chips lower — but AI/memory demand still booming",
            source="CNBC",
            source_url="https://www.cnbc.com/2026/06/03/broadcom-avgo-earnings-report-q2-2026.html",
            summary=("Broadcom fell ~15% after not raising its $100B AI-chip target, "
                     "triggering a sector-wide semiconductor sell-off. Yet underlying "
                     "demand is strong — Nvidia grew 65% YoY and Micron just hit a $1T "
                     "valuation as memory shortages persist."),
            analogy=("One opponent's scary bet (Broadcom's miss) made the whole table look "
                     "dangerous. But the board (sector demand) hasn't actually changed — "
                     "you have to separate one player's story from the real strength of "
                     "your hand."),
            prompt=("You hold a basket of quality chip names. The sector is red on "
                    "Broadcom's guidance, though demand looks intact. What do you do, and "
                    "how big?"),
            options=[
                Option("panic_sell", "Dump all chip exposure now", quality=18,
                       note="Selling the whole sector on one company's guidance is an "
                            "outcome-driven overreaction — folding a strong hand to a "
                            "single scary bet."),
                Option("hold_add_small", "Hold the core; add SMALL to a confirmed strong name",
                       quality=90,
                       note="Distinguishes company-specific news from the sector thesis and "
                            "sizes the add to the (still real but uncertain) edge."),
                Option("avg_down_full", "Back up the truck — full size into the dip",
                       quality=40,
                       note="Right that demand is strong, but full-sizing into a falling, "
                            "volatile tape ignores sizing discipline and headline risk."),
                Option("stand", "Stand aside until it stabilises", quality=62,
                       note="Safe; you avoid the chop but forgo adding to a genuine edge at "
                            "a discount."),
            ],
            best_key="hold_add_small",
            explanation=("A single firm's soft guidance is not the same as the sector thesis "
                         "breaking — demand signals (Nvidia, Micron) remain strong. The "
                         "disciplined play is to hold the core and add modestly to a "
                         "confirmed leader, sizing to conviction. Panic-selling and "
                         "full-size averaging are both errors — opposite ways of letting "
                         "the tape, not the thesis, decide."),
            teaching=("Separate idiosyncratic (one company) from systematic (the whole "
                      "sector) news before you act. When the broader thesis is intact, a "
                      "sell-off is a discount, not a reason to capitulate — but you still "
                      "size to the edge, adding small into volatility rather than betting "
                      "the farm. Outcome-independence means a red screen doesn't flip a "
                      "good thesis into a bad one."),
            terms=["thesis", "conviction sizing", "position sizing", "variance",
                   "edge", "averaging down"],
        ),
        NewsHand(
            hand_id="oil_unwind_iran_deal",
            category="energy",
            headline="Oil tumbles to a 3-month low on a deal to end the Iran war",
            source="NPR / Al Jazeera",
            source_url="https://www.npr.org/2026/06/14/nx-s1-5858115/oil-prices-trump-iran-deal",
            summary=("Crude fell ~4% to a three-month low after a preliminary US-Israel-Iran "
                     "deal raised hopes the Strait of Hormuz reopens. Brent had spiked to "
                     "$80–82 during the war; analysts say full price normalisation is still "
                     "months away."),
            analogy=("The scare card that fuelled your hand — the war risk premium — is "
                     "being removed from the deck. The reason you were long is disappearing, "
                     "even though the situation is still a bit live."),
            prompt=("You're long energy on the war premium. A ceasefire deal is announced "
                    "and oil gaps down. What's your move, and how big?"),
            options=[
                Option("hold_long", "Hold the long — the spike will come back", quality=15,
                       note="Holding a position whose entire rationale (war premium) was "
                            "just removed — hoping, not reasoning from edge."),
                Option("exit_reduce", "Exit/reduce the war-premium long", quality=88,
                       note="The catalyst is gone, so the edge is gone; banking the move and "
                            "standing down is the disciplined exit."),
                Option("short_full", "Flip and SHORT oil full size on the deal", quality=45,
                       note="Right direction, but full-sizing into a 'preliminary' deal with "
                            "months of headline risk and refill demand ignores sizing."),
                Option("short_small", "Take a SMALL short on the unwind", quality=78,
                       note="Reasonable: trade the catalyst's removal, but keep size modest "
                            "given the deal isn't final and inventories must refill."),
            ],
            best_key="exit_reduce",
            explanation=("The trade's reason for being — the geopolitical risk premium — is "
                         "what's unwinding, so the first move is to exit or reduce the long. "
                         "A small short on the unwind is defensible; a full-size short "
                         "overcommits into a non-final deal with real two-way headline risk "
                         "and months of inventory refill supporting price."),
            teaching=("Always know the specific reason (catalyst) you hold a position. When "
                      "that reason is removed, the position should go with it — don't "
                      "convert a thesis trade into a hope trade. If you fade the unwind, "
                      "size for the uncertainty that remains: partial size respects that a "
                      "'preliminary' deal can reverse on a headline."),
            terms=["catalyst", "thesis", "position sizing", "stop-loss",
                   "expected value (EV)", "variance"],
        ),
    ]


if __name__ == "__main__":
    desk = build_default_desk()
    print(f"Default desk: {len(desk)} hands for {DEFAULT_DATE}")
    for hn in desk:
        best = hn.score_response(hn.best_key)
        worst = min(hn.options, key=lambda o: o.quality)
        w = hn.score_response(worst.key)
        print(f"  [{hn.category}] {hn.headline[:50]}…  best={best['quality']} "
              f"worst({worst.key})={w['quality']}")
    save_desk(desk, DEFAULT_DATE)
    print(f"wrote {DESK_PATH}")
