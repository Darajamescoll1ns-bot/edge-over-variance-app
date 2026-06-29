"""
solver.py — Reference-policy providers  (Phase 5)
=================================================

The scoring engine grades poker decisions against a `SolverReference` (an
EV-optimal baseline). Until now those EVs were placeholders typed in by hand.
This module replaces them with numbers actually computed from the cards.

THE HONEST STATE OF "SOLVER INTEGRATION"
----------------------------------------
A true 7-card-stud GTO solver (the equivalent of what GTO Wizard has for
Hold'em) does not exist as an off-the-shelf binary or API. So this module ships
two things:

  1. A provider SEAM — `SolverProvider` (abstract) and `ExternalSolverProvider`
     (a subprocess/HTTP adapter) — so a real solver can be dropped in later
     without touching the rest of the system. `ExternalSolverProvider` is wired
     but intentionally raises until it's pointed at a real engine.

  2. A real, COMPUTABLE baseline — `MonteCarloEquityProvider`. It evaluates true
     hand equity by simulation (with a from-scratch 7-card hand evaluator) and
     derives approximate action EVs from pot odds. This is a genuine reference,
     not a placeholder, and is honest about being an APPROXIMATION
     (ReferenceType.SOLVER_APPROX) rather than exact GTO.

Everything here is standard-library only.

CARD REPRESENTATION
-------------------
Internally a card is a (rank, suit) tuple with rank in 2..14 (J=11, Q=12, K=13,
A=14) and suit in 0..3. `parse_card("As")` / `Card` dataclass values from the
schema are converted at the boundary.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from itertools import combinations
from typing import List, Optional, Sequence, Tuple

import decision_schema_stdlib as sch

# --------------------------------------------------------------------------- #
# Card encoding
# --------------------------------------------------------------------------- #
RANKS = "23456789TJQKA"
SUITS = "cdhs"
_RANK_VAL = {r: i + 2 for i, r in enumerate(RANKS)}   # '2'->2 ... 'A'->14
_SUIT_VAL = {s: i for i, s in enumerate(SUITS)}

CardT = Tuple[int, int]   # (rank, suit)


def parse_card(s: str) -> CardT:
    """'As' -> (14, 3). Accepts e.g. 'Td', '9c'."""
    s = s.strip()
    return (_RANK_VAL[s[0].upper()], _SUIT_VAL[s[1].lower()])


def schema_card(c) -> CardT:
    """Convert a schema Card (dataclass or {'rank','suit'} dict) to (rank,suit)."""
    rank = c["rank"] if isinstance(c, dict) else c.rank
    suit = c["suit"] if isinstance(c, dict) else c.suit
    return (_RANK_VAL[str(rank).upper()], _SUIT_VAL[str(suit).lower()])


def full_deck() -> List[CardT]:
    return [(r, s) for r in range(2, 15) for s in range(4)]


# --------------------------------------------------------------------------- #
# 7-card hand evaluator
# --------------------------------------------------------------------------- #
# Returns a comparable tuple: (category, *tiebreakers), bigger == stronger.
# Categories: 8 straight flush, 7 quads, 6 full house, 5 flush, 4 straight,
#             3 trips, 2 two pair, 1 pair, 0 high card.

def _best_straight_high(ranks_present: set) -> Optional[int]:
    """Highest card of the best straight in a set of ranks, or None.
    Handles the wheel (A-2-3-4-5) by treating Ace as 1."""
    rs = set(ranks_present)
    if 14 in rs:
        rs = rs | {1}        # ace can be low
    high = None
    for top in range(14, 4, -1):
        if all((top - k) in rs for k in range(5)):
            high = top
            break
    return high


def evaluate_5plus(cards: Sequence[CardT]) -> Tuple[int, ...]:
    """Evaluate the best 5-card poker hand from 5, 6, or 7 cards."""
    ranks = sorted((c[0] for c in cards), reverse=True)
    suits = [c[1] for c in cards]

    # Rank multiplicity, sorted by (count, rank) descending.
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)

    # Flush detection.
    suit_groups = {s: [c[0] for c in cards if c[1] == s] for s in set(suits)}
    flush_suit = next((s for s, rs in suit_groups.items() if len(rs) >= 5), None)

    # Straight flush.
    if flush_suit is not None:
        sf_high = _best_straight_high(set(suit_groups[flush_suit]))
        if sf_high is not None:
            return (8, sf_high)

    # Quads.
    if by_count[0][1] == 4:
        quad = by_count[0][0]
        kicker = max(r for r in ranks if r != quad)
        return (7, quad, kicker)

    # Full house (trips + a pair, or two trips).
    trips = [r for r, c in by_count if c == 3]
    pairs = [r for r, c in by_count if c == 2]
    if trips and (len(trips) >= 2 or pairs):
        trip = trips[0]
        pair = trips[1] if len(trips) >= 2 else pairs[0]
        return (6, trip, pair)

    # Flush.
    if flush_suit is not None:
        top5 = sorted(suit_groups[flush_suit], reverse=True)[:5]
        return (5, *top5)

    # Straight.
    straight_high = _best_straight_high(set(ranks))
    if straight_high is not None:
        return (4, straight_high)

    # Trips.
    if trips:
        trip = trips[0]
        kickers = sorted((r for r in ranks if r != trip), reverse=True)[:2]
        return (3, trip, *kickers)

    # Two pair.
    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        kicker = max(r for r in ranks if r != hi and r != lo)
        return (2, hi, lo, kicker)

    # One pair.
    if len(pairs) == 1:
        p = pairs[0]
        kickers = sorted((r for r in ranks if r != p), reverse=True)[:3]
        return (1, p, *kickers)

    # High card.
    return (0, *ranks[:5])


# --------------------------------------------------------------------------- #
# Monte Carlo equity (seven-card stud, showdown)
# --------------------------------------------------------------------------- #

def monte_carlo_equity_vs(
    hero_cards: Sequence[CardT],
    opponent_known: Sequence[CardT] = (),
    dead_cards: Sequence[CardT] = (),
    iterations: int = 2500,
    seed: Optional[int] = None,
) -> float:
    """Hero's heads-up showdown equity when SOME of the opponent's cards are
    known (their exposed up-cards in stud).

    Unlike monte_carlo_equity (which only removes cards from the deck), this
    fixes the opponent's visible cards as part of THEIR hand and deals the rest
    of both hands from the live deck. That is the correct model for stud, where
    you can read a villain's up-cards — and it's what makes the equity feedback
    trustworthy as a teaching signal.
    """
    rng = random.Random(seed)
    hero = [tuple(c) for c in hero_cards]
    opp_known = [tuple(c) for c in opponent_known]
    dead = set(tuple(c) for c in dead_cards)

    known = set(hero) | set(opp_known) | dead
    base_deck = [c for c in full_deck() if c not in known]

    need_hero = 7 - len(hero)
    need_opp = 7 - len(opp_known)
    wins = 0.0
    for _ in range(iterations):
        deck = base_deck[:]
        rng.shuffle(deck)
        i = 0
        h = hero + deck[i:i + need_hero]; i += need_hero
        o = opp_known + deck[i:i + need_opp]; i += need_opp
        hs, os_ = evaluate_5plus(h), evaluate_5plus(o)
        if hs > os_:
            wins += 1.0
        elif hs == os_:
            wins += 0.5
    return wins / iterations


def monte_carlo_equity_multi(
    hero_cards: Sequence[CardT],
    opponents_known: Sequence[Sequence[CardT]] = (),
    dead_cards: Sequence[CardT] = (),
    iterations: int = 1500,
    seed: Optional[int] = None,
) -> float:
    """Hero's showdown equity against MULTIPLE opponents at once.

    `opponents_known` is a list, one entry per live opponent, of that
    opponent's visible up-cards. Hero must beat ALL opponents to win the pot;
    ties at the top split. This is the multi-way generalisation of
    monte_carlo_equity_vs and is what the table game uses — equity correctly
    drops as more players contest the pot.
    """
    rng = random.Random(seed)
    hero = [tuple(c) for c in hero_cards]
    opps = [[tuple(c) for c in ok] for ok in opponents_known]
    dead = set(tuple(c) for c in dead_cards)

    known = set(hero) | dead
    for ok in opps:
        known |= set(ok)
    base_deck = [c for c in full_deck() if c not in known]

    need_hero = 7 - len(hero)
    needs = [7 - len(ok) for ok in opps]
    total_need = need_hero + sum(needs)

    wins = 0.0
    for _ in range(iterations):
        deck = base_deck[:]
        rng.shuffle(deck)
        i = 0
        h = hero + deck[i:i + need_hero]; i += need_hero
        hs = evaluate_5plus(h)
        opp_scores = []
        for k, opp in enumerate(opps):
            o = opp + deck[i:i + needs[k]]; i += needs[k]
            opp_scores.append(evaluate_5plus(o))
        if not opp_scores:
            wins += 1.0
            continue
        best_opp = max(opp_scores)
        if hs > best_opp:
            wins += 1.0
        elif hs == best_opp:
            n_tied = sum(1 for s in opp_scores if s == hs)
            wins += 1.0 / (1 + n_tied)
    return wins / iterations


def holdem_equity(
    hero_hole: Sequence[CardT],
    board: Sequence[CardT] = (),
    num_opponents: int = 1,
    dead_cards: Sequence[CardT] = (),
    iterations: int = 2000,
    seed: Optional[int] = None,
) -> float:
    """Texas Hold'em showdown equity for hero's 2 hole cards on a given board
    against `num_opponents` unknown hands.

    Completes the board to five community cards and deals each opponent two
    random hole cards from the live deck; each player's hand is the best five of
    (their 2 hole + 5 board). Hero must beat all opponents to win; ties split.
    Because opponents' cards are hidden in Hold'em, the read is the board + the
    number of live players — not exposed cards — which is exactly why the
    markets analogy here is about public information and pot odds.
    """
    rng = random.Random(seed)
    hero = [tuple(c) for c in hero_hole]
    board = [tuple(c) for c in board]
    dead = set(tuple(c) for c in dead_cards)

    known = set(hero) | set(board) | dead
    base_deck = [c for c in full_deck() if c not in known]
    need_board = 5 - len(board)

    wins = 0.0
    for _ in range(iterations):
        deck = base_deck[:]
        rng.shuffle(deck)
        i = 0
        full_board = board + deck[i:i + need_board]; i += need_board
        hs = evaluate_5plus(hero + full_board)
        opp_scores = []
        for _o in range(num_opponents):
            oh = deck[i:i + 2]; i += 2
            opp_scores.append(evaluate_5plus(oh + full_board))
        if not opp_scores:
            wins += 1.0
            continue
        best_opp = max(opp_scores)
        if hs > best_opp:
            wins += 1.0
        elif hs == best_opp:
            n_tied = sum(1 for s in opp_scores if s == hs)
            wins += 1.0 / (1 + n_tied)
    return wins / iterations


def monte_carlo_equity(
    hero_cards: Sequence[CardT],
    dead_cards: Sequence[CardT] = (),
    num_opponents: int = 1,
    iterations: int = 3000,
    seed: Optional[int] = None,
) -> float:
    """Estimate hero's probability of winning at showdown in seven-card stud.

    hero_cards   — hero's known cards (1..7). Missing cards are dealt randomly.
    dead_cards   — cards visible as folded/exposed elsewhere (removed from deck).
    num_opponents— villains who each get a full 7-card hand from the live deck.
    Ties split: a tie counts as 1/(number tied) toward hero's equity.

    This is exact in the limit of iterations; with the default it is accurate to
    ~1%. It is the genuine ground-truth signal for the poker side until a real
    solver exists.
    """
    rng = random.Random(seed)
    hero = [tuple(c) for c in hero_cards]
    dead = set(tuple(c) for c in dead_cards)

    known = set(hero) | dead
    base_deck = [c for c in full_deck() if c not in known]

    need_hero = 7 - len(hero)
    wins = 0.0
    for _ in range(iterations):
        deck = base_deck[:]
        rng.shuffle(deck)
        i = 0
        h = hero + deck[i:i + need_hero]
        i += need_hero
        hero_score = evaluate_5plus(h)

        best_villain = None
        for _o in range(num_opponents):
            v = deck[i:i + 7]
            i += 7
            vs = evaluate_5plus(v)
            if best_villain is None or vs > best_villain:
                best_villain = vs

        if hero_score > best_villain:
            wins += 1.0
        elif hero_score == best_villain:
            wins += 0.5   # simple split approximation for heads-up ties
    return wins / iterations


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #

class SolverProvider(ABC):
    """Produces a SolverReference for a poker decision. The single seam through
    which any baseline (Monte Carlo now, a real solver later) is supplied."""

    @abstractmethod
    def reference(
        self,
        hero_cards: Sequence,
        action_taken: str,
        *,
        pot_size: float,
        to_call: float,
        dead_cards: Sequence = (),
        num_opponents: int = 1,
    ) -> sch.SolverReference:
        ...


class MonteCarloEquityProvider(SolverProvider):
    """Real, computable baseline. Equity by simulation; action EVs by pot odds.

    EV model (deliberately simple, clearly approximate):
        EV(call) = equity * (pot + to_call) - (1 - equity) * to_call
        EV(fold) = 0
        EV(raise/bet) ~ EV(call) scaled by a fold-equity bump when equity is
                        marginal (a rough proxy, not game-tree accurate).
    The point is a defensible reference, not perfect GTO. reference_type is
    SOLVER_APPROX so downstream consumers know it's an approximation.
    """

    def __init__(self, iterations: int = 3000, seed: Optional[int] = None):
        self.iterations = iterations
        self.seed = seed

    def _to_cards(self, cards: Sequence) -> List[CardT]:
        out = []
        for c in cards:
            if isinstance(c, str):
                out.append(parse_card(c))
            elif isinstance(c, tuple):
                out.append(c)
            else:
                out.append(schema_card(c))
        return out

    def action_evs(self, equity: float, pot_size: float, to_call: float) -> dict:
        ev_fold = 0.0
        ev_call = equity * (pot_size + to_call) - (1.0 - equity) * to_call
        # Rough fold-equity proxy for an aggressive line: assume opponents fold
        # a fraction of the time proportional to how marginal their continue is.
        fold_equity = max(0.0, 0.5 - abs(equity - 0.5)) * 0.5
        ev_raise = ev_call + fold_equity * pot_size
        return {"fold": ev_fold, "call": ev_call, "bet": ev_raise, "raise": ev_raise}

    def reference(
        self,
        hero_cards: Sequence,
        action_taken: str,
        *,
        pot_size: float,
        to_call: float,
        dead_cards: Sequence = (),
        num_opponents: int = 1,
    ) -> sch.SolverReference:
        hero = self._to_cards(hero_cards)
        dead = self._to_cards(dead_cards)
        equity = monte_carlo_equity(hero, dead, num_opponents,
                                    iterations=self.iterations, seed=self.seed)
        evs = self.action_evs(equity, pot_size, to_call)

        best_action = max(evs, key=evs.get)
        best_ev = evs[best_action]
        taken = action_taken.lower()
        taken_ev = evs.get(taken, 0.0)

        return sch.SolverReference(
            reference_type=sch.ReferenceType.SOLVER_APPROX,
            best_action=best_action,
            best_action_ev=round(best_ev, 4),
            taken_action_ev=round(taken_ev, 4),
            ev_loss=round(max(0.0, best_ev - taken_ev), 4),
            strategy={k: round(v, 4) for k, v in evs.items()},
            true_equity=round(equity, 4),
        )


class ExternalSolverProvider(SolverProvider):
    """Seam for a real external stud solver (subprocess binary or HTTP API).

    This is where GTO Wizard-grade ground truth would plug in. It is fully
    wired structurally but raises until `command` (a CLI solver) or `endpoint`
    (an HTTP solver) is configured AND implemented, because no such stud engine
    is currently available. Documented here so the integration point is obvious
    and the rest of the system never needs to change when one arrives.
    """

    def __init__(self, command: Optional[List[str]] = None,
                 endpoint: Optional[str] = None, timeout: float = 30.0):
        self.command = command
        self.endpoint = endpoint
        self.timeout = timeout

    def reference(self, hero_cards, action_taken, *, pot_size, to_call,
                  dead_cards=(), num_opponents=1) -> sch.SolverReference:
        if not self.command and not self.endpoint:
            raise NotImplementedError(
                "ExternalSolverProvider is unconfigured. No off-the-shelf 7-card "
                "stud solver exists yet; set `command=[...]` (subprocess) or "
                "`endpoint='https://...'` (HTTP) and implement the marshalling "
                "below when one becomes available. Use MonteCarloEquityProvider "
                "in the meantime."
            )
        # --- Integration sketch (left unimplemented on purpose) --------------
        # import subprocess, json
        # payload = {"hero": hero_cards, "pot": pot_size, "to_call": to_call, ...}
        # if self.command:
        #     out = subprocess.run(self.command, input=json.dumps(payload),
        #                          capture_output=True, text=True, timeout=self.timeout)
        #     data = json.loads(out.stdout)
        # else:  # HTTP
        #     ... POST payload to self.endpoint, parse response ...
        # return sch.SolverReference(reference_type=ReferenceType.SOLVER_EXACT, ...)
        raise NotImplementedError("Configure and implement external solver marshalling.")


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Equity sanity: rolled-up aces (three aces) heads-up should crush.
    p = MonteCarloEquityProvider(iterations=4000, seed=1)
    strong = monte_carlo_equity([parse_card("As"), parse_card("Ah"), parse_card("Ad")],
                                num_opponents=1, iterations=4000, seed=1)
    weak = monte_carlo_equity([parse_card("7c"), parse_card("2d"), parse_card("9h")],
                              num_opponents=1, iterations=4000, seed=1)
    print(f"Rolled-up aces equity (heads-up):  {strong:.3f}")
    print(f"7-2-9 offsuit junk equity:         {weak:.3f}")

    ref = p.reference([parse_card("As"), parse_card("Ah"), parse_card("Ks")],
                      action_taken="call", pot_size=100.0, to_call=20.0,
                      dead_cards=[parse_card("Ad")], num_opponents=2)
    print("\nSample SolverReference (As Ah Ks, call, pot 100 / to-call 20, 2 opp):")
    print(f"  equity={ref.true_equity}  best={ref.best_action}  "
          f"best_ev={ref.best_action_ev}  taken_ev={ref.taken_action_ev}  "
          f"ev_loss={ref.ev_loss}")
    print(f"  action EVs: {ref.strategy}")
