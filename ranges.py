"""
ranges.py — Opponent ranges, GTO continue ranges (MDF) and conditioned equity
=============================================================================

The original engine graded a decision against `monte_carlo_equity(...)`, i.e.
hero's showdown equity versus a UNIFORMLY RANDOM opponent hand — "equity vs the
average hand". That is wrong the moment the opponent gets to *act*: a bet folds
out the bottom of their range, so the hands that CONTINUE are stronger than
average and hero's realised equity against them is lower. A hand like K2o is a
small favourite over a random hand but a big dog versus any sane calling range.

This module fixes that. It models the opponent as a weighted distribution over
holdings (a Range), narrows that range the way game theory says a defender must
(Minimum Defence Frequency), and computes hero equity *against the surviving
range* — "equity when called". It is game-agnostic: Hold'em and seven-card stud
both plug into the same Range / conditioned-equity machinery via small per-game
hooks.

Everything is standard-library only and reuses solver.py's 7-card evaluator.

KEY IDEAS
---------
* MDF (minimum defence frequency). Facing a bet of `b` into a pot of `P`, a
  defender must continue with at least  P / (P + b)  of their range, otherwise a
  pure bluff prints automatically. So a half-pot bet (b = .5P) is defended with
  the top 2/3 of the range; a pot-sized bet with the top 1/2. The folded portion
  is the bottom  b / (P + b)  by strength.

* GTO continue range = the top-MDF slice of the opponent's range ranked by how
  well each holding does against hero. The hands that fold are precisely the
  weakest ones — which is why conditioning lowers hero's equity for marginal
  hands and barely touches it for genuinely strong ones.

* Conditioned equity. Hero equity is then computed *against that surviving
  slice*, not against the whole deck. `equity_when_called` and
  `equity_when_raised` expose the two branches a bettor actually faces.
"""

from __future__ import annotations

import random
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

import solver as sv

CardT = Tuple[int, int]


# --------------------------------------------------------------------------- #
# Card / deck helpers
# --------------------------------------------------------------------------- #
def as_cards(cards: Sequence) -> List[CardT]:
    """Coerce strings / schema cards / tuples to (rank, suit) tuples."""
    out: List[CardT] = []
    for c in cards:
        if isinstance(c, str):
            out.append(sv.parse_card(c))
        elif isinstance(c, tuple):
            out.append((int(c[0]), int(c[1])))
        else:
            out.append(sv.schema_card(c))
    return out


def remaining_deck(known: Sequence[CardT]) -> List[CardT]:
    blocked = set(tuple(c) for c in known)
    return [c for c in sv.full_deck() if c not in blocked]


# --------------------------------------------------------------------------- #
# Pot-odds / MDF primitives
# --------------------------------------------------------------------------- #
def mdf(bet: float, pot: float) -> float:
    """Minimum defence frequency facing `bet` into `pot` (pot BEFORE the bet).

    The defender must continue with this top fraction of their range; the bottom
    `bet/(pot+bet)` may be folded. continue = pot/(pot+bet)."""
    if bet <= 0:
        return 1.0
    return pot / (pot + bet)


def required_equity(bet: float, pot: float) -> float:
    """Equity the CALLER needs to break even: risk `bet` to win `pot + bet`, so
    threshold = bet / (pot + 2*bet)."""
    denom = pot + 2.0 * bet
    return bet / denom if denom > 0 else 0.0


# --------------------------------------------------------------------------- #
# Game hooks — Hold'em
# --------------------------------------------------------------------------- #
def holdem_combos(known: Sequence[CardT]) -> List[Tuple[CardT, CardT]]:
    """All 2-card opponent holdings consistent with the cards already accounted
    for (hero hole + board + dead)."""
    return list(combinations(remaining_deck(known), 2))


def _canon_preflop(combo: Tuple[CardT, CardT]) -> Tuple[int, int, bool]:
    """Canonical 169-class key for a preflop combo: (hi, lo, suited)."""
    (r1, s1), (r2, s2) = combo
    hi, lo = max(r1, r2), min(r1, r2)
    return (hi, lo, (s1 == s2) and hi != lo)


def _hero_equity_vs_combo(
    hero: List[CardT],
    combo: Tuple[CardT, CardT],
    board: List[CardT],
    rng: random.Random,
    iterations: int,
) -> float:
    """Hero's showdown equity vs ONE specific Hold'em combo on `board`.

    Exact when 0 or 1 board cards remain; Monte-Carlo otherwise."""
    blocked = set(hero) | set(combo) | set(board)
    rem = [c for c in sv.full_deck() if c not in blocked]
    need = 5 - len(board)

    if need <= 0:
        hs = sv.evaluate_5plus(hero + board)
        os_ = sv.evaluate_5plus(list(combo) + board)
        return 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)

    if need == 1:
        # Enumerate the single remaining card — exact, cheap.
        wins = 0.0
        for c in rem:
            full = board + [c]
            hs = sv.evaluate_5plus(hero + full)
            os_ = sv.evaluate_5plus(list(combo) + full)
            wins += 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)
        return wins / len(rem)

    wins = 0.0
    for _ in range(iterations):
        rng.shuffle(rem)
        full = board + rem[:need]
        hs = sv.evaluate_5plus(hero + full)
        os_ = sv.evaluate_5plus(list(combo) + full)
        wins += 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)
    return wins / iterations


# --------------------------------------------------------------------------- #
# Conditioned equity — the core result
# --------------------------------------------------------------------------- #
class ConditionedEquity:
    """The spectrum of hero equities for one decision.

    vs_random   — equity against the opponent's whole (uniform) range. This is
                  the OLD number the engine used.
    vs_continue — equity against the top-MDF slice that continues vs a bet of
                  `bet` into `pot`. This is "equity when called/raised" and is
                  the number that should drive a betting decision.
    vs_folded   — equity against the bottom slice that folds (for intuition /
                  teaching: notice how much weaker those hands were).
    """

    def __init__(self, vs_random, vs_continue, vs_folded,
                 mdf_frac, req_equity, n_combos, n_continue, bet, pot):
        self.vs_random = vs_random
        self.vs_continue = vs_continue
        self.vs_folded = vs_folded
        self.mdf = mdf_frac
        self.required_equity = req_equity
        self.n_combos = n_combos
        self.n_continue = n_continue
        self.bet = bet
        self.pot = pot

    def as_dict(self) -> dict:
        return {
            "vs_random": round(self.vs_random, 4),
            "vs_continue": round(self.vs_continue, 4),
            "vs_folded": round(self.vs_folded, 4),
            "mdf": round(self.mdf, 4),
            "required_equity": round(self.required_equity, 4),
            "n_combos": self.n_combos,
            "n_continue": self.n_continue,
            "bet": self.bet,
            "pot": self.pot,
        }


def holdem_conditioned_equity(
    hero_cards: Sequence,
    board: Sequence = (),
    *,
    bet: float,
    pot: float,
    dead_cards: Sequence = (),
    iterations: int = 250,
    max_combos: Optional[int] = None,
    seed: Optional[int] = None,
) -> ConditionedEquity:
    """Hero equity vs random AND vs the GTO continue range for a bet of `bet`
    into `pot`, in Texas Hold'em.

    Preflop (empty board) we reduce the 1225 combos to their ≤169 strategic
    classes for speed; postflop we work combo-by-combo (optionally sampling
    `max_combos` of them for the live path). Each holding's hero-equity is
    computed, holdings are ranked by villain strength (1 − hero_equity), the top
    MDF fraction by combo-weight is kept as the continue range, and hero equity
    is re-averaged over that slice.
    """
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    board = as_cards(board)
    dead = as_cards(dead_cards)
    known = hero + board + dead

    keep = mdf(bet, pot)

    # (hero_equity, weight) samples describing the opponent range.
    samples: List[Tuple[float, int]] = []

    if not board:
        # Preflop: group remaining combos into canonical classes.
        classes: Dict[Tuple[int, int, bool], List[Tuple[CardT, CardT]]] = {}
        for combo in holdem_combos(known):
            classes.setdefault(_canon_preflop(combo), []).append(combo)
        for _cls, combos in classes.items():
            rep = combos[0]
            e = _hero_equity_vs_combo(hero, rep, board, rng, iterations)
            samples.append((e, len(combos)))
    else:
        combos = holdem_combos(known)
        if max_combos is not None and len(combos) > max_combos:
            combos = rng.sample(combos, max_combos)
        for combo in combos:
            e = _hero_equity_vs_combo(hero, combo, board, rng, iterations)
            samples.append((e, 1))

    return _summarise(samples, keep, bet, pot, required_equity(bet, pot))


# --------------------------------------------------------------------------- #
# Game hooks — seven-card stud
# --------------------------------------------------------------------------- #
def _hero_equity_vs_stud_hand(
    hero: List[CardT],
    villain_known: List[CardT],
    villain_hidden: Tuple[CardT, ...],
    rng: random.Random,
    iterations: int,
) -> float:
    """Hero equity vs a stud opponent whose visible up-cards are `villain_known`
    and whose hidden hole cards are `villain_hidden`. Both hands are completed
    to seven cards from the live deck."""
    villain = list(villain_known) + list(villain_hidden)
    blocked = set(hero) | set(villain)
    rem = [c for c in sv.full_deck() if c not in blocked]
    need_hero = 7 - len(hero)
    need_opp = 7 - len(villain)
    wins = 0.0
    for _ in range(iterations):
        rng.shuffle(rem)
        i = 0
        h = hero + rem[i:i + need_hero]; i += need_hero
        o = villain + rem[i:i + need_opp]; i += need_opp
        hs, os_ = sv.evaluate_5plus(h), sv.evaluate_5plus(o)
        wins += 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)
    return wins / iterations


def stud_conditioned_equity(
    hero_cards: Sequence,
    villain_up_cards: Sequence,
    *,
    bet: float,
    pot: float,
    dead_cards: Sequence = (),
    n_hidden: int = 2,
    iterations: int = 120,
    max_combos: Optional[int] = 400,
    seed: Optional[int] = None,
) -> ConditionedEquity:
    """Stud analogue of holdem_conditioned_equity.

    The opponent's hidden hole cards are unknown; we enumerate (or sample) the
    2-card hidden combinations consistent with every visible card, score hero's
    equity against each, then keep the top-MDF slice by villain strength. Dead
    up-cards elsewhere on the table should be passed in `dead_cards` for correct
    card removal."""
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    vup = as_cards(villain_up_cards)
    dead = as_cards(dead_cards)
    known = hero + vup + dead

    hidden_pool = remaining_deck(known)
    hidden_combos = list(combinations(hidden_pool, n_hidden))
    if max_combos is not None and len(hidden_combos) > max_combos:
        hidden_combos = rng.sample(hidden_combos, max_combos)

    samples: List[Tuple[float, int]] = []
    for hid in hidden_combos:
        e = _hero_equity_vs_stud_hand(hero, vup, hid, rng, iterations)
        samples.append((e, 1))

    return _summarise(samples, mdf(bet, pot), bet, pot, required_equity(bet, pot))


# --------------------------------------------------------------------------- #
# Reusable sampling + slicing  (compute the costly samples ONCE per decision,
# then slice the continue range cheaply for every candidate bet size)
# --------------------------------------------------------------------------- #
def holdem_range_samples(
    hero_cards: Sequence,
    board: Sequence = (),
    *,
    dead_cards: Sequence = (),
    iterations: int = 200,
    max_combos: Optional[int] = None,
    seed: Optional[int] = None,
) -> Tuple[List[Tuple[float, float]], float, float]:
    """Build the opponent-range description ONCE: a list of (hero_equity, weight)
    samples sorted ascending by hero equity, the total weight, and hero's
    unconditional (vs-random) equity. Slice it with `slice_continue` for any bet
    size — no need to re-simulate per option."""
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    board = as_cards(board)
    dead = as_cards(dead_cards)
    known = hero + board + dead

    triples: List[Tuple[float, float, Tuple[CardT, CardT]]] = []
    if not board:
        classes: Dict[Tuple[int, int, bool], List[Tuple[CardT, CardT]]] = {}
        for combo in holdem_combos(known):
            classes.setdefault(_canon_preflop(combo), []).append(combo)
        for _cls, combos in classes.items():
            e = _hero_equity_vs_combo(hero, combos[0], board, rng, iterations)
            triples.append((e, float(len(combos)), combos[0]))
    else:
        combos = holdem_combos(known)
        if max_combos is not None and len(combos) > max_combos:
            combos = rng.sample(combos, max_combos)
        for combo in combos:
            e = _hero_equity_vs_combo(hero, combo, board, rng, iterations)
            triples.append((e, 1.0, combo))

    triples.sort(key=lambda t: t[0])           # ascending hero equity
    samples = [(e, w) for e, w, _c in triples]
    combos_sorted = [c for _e, _w, c in triples]
    total_w = sum(w for _, w in samples)
    vs_random = (sum(e * w for e, w in samples) / total_w) if total_w else 0.0
    # combos_sorted is aligned with samples: the FRONT is the opponent's
    # strongest hands (lowest hero equity) — i.e. a natural betting range.
    return samples, total_w, vs_random, combos_sorted


def stud_range_samples(
    hero_cards: Sequence,
    villain_up_cards: Sequence,
    *,
    dead_cards: Sequence = (),
    n_hidden: int = 2,
    iterations: int = 100,
    max_combos: Optional[int] = 400,
    seed: Optional[int] = None,
) -> Tuple[List[Tuple[float, float]], float, float]:
    """Stud analogue of holdem_range_samples (over the opponent's hidden hole
    cards)."""
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    vup = as_cards(villain_up_cards)
    dead = as_cards(dead_cards)
    known = hero + vup + dead

    hidden_combos = list(combinations(remaining_deck(known), n_hidden))
    if max_combos is not None and len(hidden_combos) > max_combos:
        hidden_combos = rng.sample(hidden_combos, max_combos)

    samples = [(_hero_equity_vs_stud_hand(hero, vup, hid, rng, iterations), 1.0)
               for hid in hidden_combos]
    samples.sort(key=lambda ew: ew[0])
    total_w = sum(w for _, w in samples)
    vs_random = (sum(e * w for e, w in samples) / total_w) if total_w else 0.0
    return samples, total_w, vs_random


def slice_continue(samples, total_w, keep_frac) -> Tuple[float, float, int]:
    """Given ascending-sorted (equity, weight) samples, return
    (equity_vs_continue, equity_vs_folded, n_continue) for keeping the top
    `keep_frac` by weight as the continue range (villain folds the weakest
    `1-keep_frac`)."""
    if total_w <= 0:
        return 0.0, 0.0, 0
    target = keep_frac * total_w
    cont_w = cont_e = 0.0
    n_continue = 0
    for e, w in samples:
        if cont_w >= target:
            break
        take = min(w, target - cont_w)
        cont_e += e * take
        cont_w += take
        n_continue += 1
    vs_continue = (cont_e / cont_w) if cont_w > 0 else 0.0
    fold_w = total_w - cont_w
    grand = sum(e * w for e, w in samples)
    vs_folded = ((grand - cont_e) / fold_w) if fold_w > 1e-9 else vs_continue
    return vs_continue, vs_folded, n_continue


# --------------------------------------------------------------------------- #
# Shared summariser
# --------------------------------------------------------------------------- #
def _summarise(samples, keep_frac, bet, pot, req_eq) -> ConditionedEquity:
    """Given (hero_equity, weight) samples, rank by villain strength and split
    into the top-MDF continue slice and the folded remainder."""
    if not samples:
        return ConditionedEquity(0.0, 0.0, 0.0, keep_frac, req_eq, 0, 0, bet, pot)

    total_w = sum(w for _, w in samples)
    vs_random = sum(e * w for e, w in samples) / total_w

    # Villain continues with hands that do BEST against hero => lowest hero
    # equity first. Sort ascending by hero equity and take from the top.
    samples.sort(key=lambda ew: ew[0])
    target = keep_frac * total_w

    cont_w = 0.0
    cont_e = 0.0
    n_continue = 0
    idx = 0
    while idx < len(samples) and cont_w < target:
        e, w = samples[idx]
        take = min(w, target - cont_w)   # fractional take at the boundary
        cont_e += e * take
        cont_w += take
        n_continue += 1
        idx += 1
    vs_continue = (cont_e / cont_w) if cont_w > 0 else vs_random

    fold_w = total_w - cont_w
    if fold_w > 1e-9:
        # remaining weight (including the unused part of the boundary sample)
        rem_e = sum(e * w for e, w in samples) - cont_e
        # subtract the continue contribution already counted from boundary
        vs_folded = (sum(e * w for e, w in samples) - cont_e) / fold_w
    else:
        vs_folded = vs_continue

    return ConditionedEquity(
        vs_random=vs_random, vs_continue=vs_continue, vs_folded=vs_folded,
        mdf_frac=keep_frac, req_equity=req_eq,
        n_combos=len(samples), n_continue=n_continue, bet=bet, pot=pot,
    )


# --------------------------------------------------------------------------- #
# Hand-strength scoring (board-aware, no Monte-Carlo) for continue/fold reads
# --------------------------------------------------------------------------- #
def draw_strength(hole: Sequence[CardT], board: Sequence[CardT]) -> float:
    """Flush/straight draw potential of `hole` on `board`, in [0,1]. Shared by
    the rollout's betting policy and the multiway continue thresholds."""
    if len(board) >= 5:
        return 0.0
    cards = list(hole) + list(board)
    suits: Dict[int, int] = {}
    for _r, s in cards:
        suits[s] = suits.get(s, 0) + 1
    flush_draw = any(v == 4 for v in suits.values())
    ranks = set(r for r, _s in cards)
    if 14 in ranks:
        ranks = ranks | {1}
    straight_draw = False
    run = 0
    for r in range(1, 15):
        run = run + 1 if r in ranks else 0
        if run >= 4:
            straight_draw = True
    if flush_draw and straight_draw:
        return 1.0
    if flush_draw:
        return 0.85
    if straight_draw:
        return 0.7
    return 0.0


def _preflop_strength(hole: Sequence[CardT]) -> float:
    (r1, s1), (r2, s2) = hole
    hi, lo = max(r1, r2), min(r1, r2)
    val = hi + lo * 0.05
    if r1 == r2:
        val += 8.0                      # a pair is a big jump
    if s1 == s2:
        val += 1.5                      # suited
    if 0 < (hi - lo) <= 4:
        val += (5 - (hi - lo)) * 0.4    # connectedness
    return val


def hand_strength(hole: Sequence[CardT], board: Sequence[CardT]) -> float:
    """A single continuous strength number used to rank a holding for the
    MDF continue/fold decision. Postflop = made-hand class + kickers + a draw
    bonus; preflop = a Chen-like high-card/pair/suited score. Only relative
    order on a given street matters."""
    board = list(board)
    if len(board) < 3:
        return _preflop_strength(hole)
    made = sv.evaluate_5plus(list(hole) + board)
    val = float(made[0])
    for i, k in enumerate(made[1:5]):
        val += k / (15.0 ** (i + 1))
    return val + 0.5 * draw_strength(hole, board)


def _stud_partial_strength(cards: Sequence[CardT]) -> float:
    """Strength of a stud holding from 3–7 known cards (the 5-card evaluator
    needs ≥5, so few-card holdings are scored by multiplicity + high card)."""
    cards = list(cards)
    if len(cards) >= 5:
        made = sv.evaluate_5plus(cards)
        val = float(made[0])
        for i, k in enumerate(made[1:5]):
            val += k / (15.0 ** (i + 1))
        return val
    ranks = sorted((r for r, _s in cards), reverse=True)
    counts: Dict[int, int] = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    mult = max(counts.values()) if counts else 1
    cat = {1: 0, 2: 1, 3: 3, 4: 7}.get(mult, 0)
    return float(cat) + (ranks[0] / 15.0 if ranks else 0.0)


# --------------------------------------------------------------------------- #
# Multiway conditioned equity (each opponent folds its weakest hands, MDF)
# --------------------------------------------------------------------------- #
class MultiwayGrid:
    """Pre-sampled multiway trials supporting conditioned equity and per-option
    EV. Each trial fixes one random hand per opponent (its decision-street
    strength + its final showdown rank) and hero's final rank. For any bet size
    we threshold each opponent at the MDF cutoff: an opponent CONTINUES only if
    its hand is in the top `pot/(pot+bet)` of strength, otherwise it folds. Hero
    must beat every continuing opponent; ties split."""

    def __init__(self, trials, vs_random, sorted_strengths, n_opponents):
        self.trials = trials                      # [(hero_rank, [(strength, rank), ...]), ...]
        self.vs_random = vs_random
        self._strengths = sorted_strengths        # ascending pooled opponent strengths
        self.n = n_opponents

    def _cutoff(self, keep_frac: float) -> float:
        """Strength value s.t. a fraction `keep_frac` of opponent hands clear it."""
        s = self._strengths
        if not s:
            return float("-inf")
        idx = int((1.0 - keep_frac) * len(s))
        idx = max(0, min(len(s) - 1, idx))
        return s[idx]

    def _equity_given_cutoff(self, theta: float) -> float:
        wins = 0.0
        contested = 0
        for hero_rank, opps in self.trials:
            callers = [r for s, r in opps if s >= theta]
            if not callers:
                continue
            contested += 1
            best = max(callers)
            if hero_rank > best:
                wins += 1.0
            elif hero_rank == best:
                wins += 1.0 / (1 + sum(1 for r in callers if r == hero_rank))
        return (wins / contested) if contested else self.vs_random

    def equity_when_called(self, bet: float, pot: float) -> float:
        return self._equity_given_cutoff(self._cutoff(mdf(bet, pot)))

    def equity_vs_callers(self, to_call: float, pot: float) -> float:
        pot_before = max(pot - to_call, to_call, 1.0)
        return self._equity_given_cutoff(self._cutoff(mdf(to_call, pot_before)))

    def fold_all_prob(self, bet: float, pot: float) -> float:
        keep = mdf(bet, pot)
        theta = self._cutoff(keep)
        folds = sum(1 for _hr, opps in self.trials
                    if all(s < theta for s, _r in opps))
        return folds / len(self.trials) if self.trials else 0.0

    def ev_option(self, opt, pot: float, to_call: float) -> float:
        key = opt["key"]
        amt = float(opt.get("amount", 0.0))
        if key == "fold":
            return 0.0
        if key == "check":
            return self.vs_random * pot
        if key == "call":
            e = self.equity_vs_callers(to_call, pot)
            return e * pot - (1 - e) * amt
        # bet / raise / all-in: simulate the multiway result hand by hand.
        theta = self._cutoff(mdf(amt, pot))
        net = 0.0
        for hero_rank, opps in self.trials:
            callers = [r for s, r in opps if s >= theta]
            if not callers:
                net += pot                              # everyone folds: win the pot
                continue
            k = len(callers)
            pot_final = pot + amt + amt * k             # hero's bet + k calls
            best = max(callers)
            if hero_rank > best:
                net += pot_final - amt
            elif hero_rank == best:
                ties = 1 + sum(1 for r in callers if r == hero_rank)
                net += pot_final / ties - amt
            else:
                net += -amt
        return net / len(self.trials)


def holdem_multiway_grid(
    hero_cards: Sequence,
    board: Sequence,
    n_opponents: int,
    *,
    dead_cards: Sequence = (),
    iterations: int = 500,
    seed: Optional[int] = None,
) -> MultiwayGrid:
    """Build a MultiwayGrid for Hold'em: hero vs `n_opponents` unknown hands on
    `board`. Opponents decide to continue on their DECISION-street strength; the
    showdown uses the completed board."""
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    board = as_cards(board)
    dead = as_cards(dead_cards)
    known = set(hero) | set(board) | set(dead)
    base = [c for c in sv.full_deck() if c not in known]
    need_board = 5 - len(board)

    trials = []
    strengths = []
    wins_random = 0.0
    for _ in range(iterations):
        deck = base[:]
        rng.shuffle(deck)
        i = 0
        full_board = board + deck[i:i + need_board]; i += need_board
        hero_rank = sv.evaluate_5plus(hero + full_board)
        opps = []
        opp_ranks = []
        for _o in range(n_opponents):
            oh = deck[i:i + 2]; i += 2
            s = hand_strength(oh, board)
            r = sv.evaluate_5plus(oh + full_board)
            opps.append((s, r))
            opp_ranks.append(r)
            strengths.append(s)
        best = max(opp_ranks)
        if hero_rank > best:
            wins_random += 1.0
        elif hero_rank == best:
            wins_random += 1.0 / (1 + sum(1 for r in opp_ranks if r == hero_rank))
        trials.append((hero_rank, opps))

    strengths.sort()
    return MultiwayGrid(trials, wins_random / iterations, strengths, n_opponents)


def stud_multiway_grid(
    hero_cards: Sequence,
    opponents_up_cards: Sequence[Sequence],
    *,
    dead_cards: Sequence = (),
    iterations: int = 400,
    seed: Optional[int] = None,
) -> MultiwayGrid:
    """Build a MultiwayGrid for seven-card stud: hero vs several opponents whose
    visible up-cards are known. Each opponent's hidden cards are dealt from the
    live deck; an opponent continues if its current (up + hidden) holding clears
    the MDF strength cutoff — so a scary board continues more often than a ragged
    one, which is the real read stud gives you."""
    rng = random.Random(seed)
    hero = as_cards(hero_cards)
    opp_ups = [as_cards(u) for u in opponents_up_cards]
    dead = as_cards(dead_cards)
    known = set(hero) | set(dead)
    for u in opp_ups:
        known |= set(u)
    base = [c for c in sv.full_deck() if c not in known]

    need_hero = 7 - len(hero)
    needs = [7 - len(u) for u in opp_ups]

    trials = []
    strengths = []
    wins_random = 0.0
    for _ in range(iterations):
        deck = base[:]
        rng.shuffle(deck)
        i = 0
        h = hero + deck[i:i + need_hero]; i += need_hero
        hero_rank = sv.evaluate_5plus(h)
        opps = []
        opp_ranks = []
        for k, up in enumerate(opp_ups):
            hidden = deck[i:i + 2]; i += 2              # the two down cards
            rest = deck[i:i + (needs[k] - 2)]; i += (needs[k] - 2)
            decision_known = up + hidden               # what they'd act on
            s = _stud_partial_strength(decision_known)
            r = sv.evaluate_5plus(up + hidden + rest)
            opps.append((s, r))
            opp_ranks.append(r)
            strengths.append(s)
        best = max(opp_ranks)
        if hero_rank > best:
            wins_random += 1.0
        elif hero_rank == best:
            wins_random += 1.0 / (1 + sum(1 for r in opp_ranks if r == hero_rank))
        trials.append((hero_rank, opps))

    strengths.sort()
    return MultiwayGrid(trials, wins_random / iterations, strengths, len(opp_ups))


# --------------------------------------------------------------------------- #
# Demo / sanity
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("MDF check: half-pot ->", round(mdf(50, 100), 3),
          "| pot ->", round(mdf(100, 100), 3))

    # The headline example: K2o.
    ce = holdem_conditioned_equity(
        ["Ks", "2d"], bet=50, pot=100, iterations=300, seed=1)
    print("\nK2o preflop, facing/representing a half-pot bet (MDF "
          f"{ce.mdf:.0%} continue):")
    print(f"  equity vs RANDOM hand : {ce.vs_random:.1%}")
    print(f"  equity vs CONTINUE    : {ce.vs_continue:.1%}  (when called)")
    print(f"  equity vs folded slice: {ce.vs_folded:.1%}")
