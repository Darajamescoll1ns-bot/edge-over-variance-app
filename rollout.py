"""
rollout.py — Multi-street EV by simulation (implied & reverse-implied odds)
==========================================================================

`ranges.py` answers "what is hero's equity against the range that CONTINUES?".
That fixes the showdown number, but a call is rarely a pure showdown: when a draw
comes in you win EXTRA bets on later streets (implied odds), and when you make a
second-best hand you PAY OFF a better one (reverse implied odds). Those only show
up if you simulate the rest of the hand including the betting.

This module does exactly that. Given a decision point it rolls the hand out to
showdown many times, sampling the opponent from a range and letting a documented
betting model play future streets for both players. The average chip result is
the action's EV — with implied odds, reverse-implied odds, fold equity and
equity realisation all emergent rather than bolted on.

HONESTY ABOUT THE MODEL
-----------------------
The future-street betting policy here is a deliberately simple, fixed heuristic
(value-bet strong hands ~2/3 pot, semi-bluff strong draws sometimes, continue by
pot odds, fold the rest). It is NOT a solved strategy, so the EVs are an
informed approximation — consistent with the rest of the engine, which labels
its baseline SOLVER_APPROX. The point is that implied odds are now MODELLED
(a flush draw is correctly worth more than its immediate pot odds) instead of
ignored. Swap in a stronger policy (or a real solver) behind the same interface
to sharpen it.

Hold'em is fully implemented; `stud_rollout_ev` reuses the same policy skeleton
over stud streets.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Tuple

import solver as sv
import ranges as R

CardT = Tuple[int, int]

DEFAULT_BET_FRAC = 0.66          # default value/semi-bluff sizing as frac of pot
SEMI_BLUFF_PROB = 0.45           # how often a strong draw bets rather than checks


# --------------------------------------------------------------------------- #
# Hand-strength reads used by the betting policy (in-sim, cards known)
# --------------------------------------------------------------------------- #
def _holdem_strength(hole: List[CardT], board: List[CardT]) -> Tuple[int, float]:
    """Return (made_category, draw_strength) for a 2-card hand on a board.

    made_category is solver's 0..8 hand class for the best 5 of hole+board.
    draw_strength in [0,1] flags flush/straight-draw potential when unmade."""
    cat = sv.evaluate_5plus(hole + board)[0] if board else 0
    draw = _draw_strength(hole, board)
    return cat, draw


def _draw_strength(hole: List[CardT], board: List[CardT]) -> float:
    """Delegates to ranges.draw_strength (single shared implementation)."""
    return R.draw_strength(hole, board)


def _value_bets(cat: int, draw: float, rng: random.Random) -> Tuple[bool, bool]:
    """Policy: (does this hand want to put money in, is it a semi-bluff?).

    Value range = two pair or better (cat>=2), or top-pair-ish (cat==1) at
    reduced frequency. Strong draws semi-bluff part of the time."""
    if cat >= 2:
        return True, False
    if cat == 1:
        return (rng.random() < 0.5), False
    if draw >= 0.7:
        return (rng.random() < SEMI_BLUFF_PROB), True
    return False, False


def _continues_vs_bet(cat: int, draw: float, price: float, rng: random.Random) -> bool:
    """Policy: does a hand continue facing a bet costing `price` (required
    equity)? Made hands continue on strength; draws continue when priced in
    (implied odds make this generous for strong draws)."""
    if cat >= 2:
        return True
    if cat == 1:
        return price <= 0.40        # call top-pair-ish unless the price is steep
    if draw >= 0.85:
        return price <= 0.45        # flush draws call all but big bets (implied)
    if draw >= 0.7:
        return price <= 0.33
    return False


# --------------------------------------------------------------------------- #
# Hold'em single-trial rollout
# --------------------------------------------------------------------------- #
def _holdem_trial(
    hero: List[CardT],
    villain: List[CardT],
    board: List[CardT],
    deck: List[CardT],
    pot: float,
    hero_stack: float,
    villain_stack: float,
    rng: random.Random,
) -> float:
    """Play board out from `board` to the river with both players acting; return
    hero's net chips for the streets PLAYED HERE (does not include chips already
    in `pot`). Stacks bound the betting; ties split."""
    hero_invested = 0.0
    hero_returned = 0.0
    board = board[:]
    need_order = [1, 1, 1]   # cards added to reach turn, river (flop assumed set)

    # Build the list of streets still to deal.
    to_deal = 5 - len(board)
    streets: List[int] = []
    if to_deal == 2:        # on the flop: turn (1) then river (1)
        streets = [1, 1]
    elif to_deal == 1:      # on the turn: river
        streets = [1]
    else:
        streets = []        # already on river: straight to showdown

    di = 0
    live = True
    for add in streets:
        board = board + deck[di:di + add]; di += add
        # One betting beat per street: the stronger of a check-or-bet exchange.
        pot, hero_invested, hero_returned, live, folded = _holdem_beat(
            hero, villain, board, pot, hero_stack - hero_invested,
            villain_stack, hero_invested, hero_returned, rng)
        if folded == "villain":
            return (pot) - hero_invested        # hero wins current pot
        if folded == "hero":
            return -hero_invested               # hero gave up
    # Showdown.
    hs = sv.evaluate_5plus(hero + board)
    os_ = sv.evaluate_5plus(villain + board)
    if hs > os_:
        hero_returned += pot
    elif hs == os_:
        hero_returned += pot / 2.0
    return hero_returned - hero_invested


def _holdem_beat(hero, villain, board, pot, hero_room, villain_room,
                 hero_invested, hero_returned, rng):
    """One simplified betting exchange on a street. Returns updated
    (pot, hero_invested, hero_returned, live, folded)."""
    hc, hd = _holdem_strength(hero, board)
    vc, vd = _holdem_strength(villain, board)

    hero_wants, _hsb = _value_bets(hc, hd, rng)
    if hero_wants and hero_room > 0:
        bet = min(round(DEFAULT_BET_FRAC * pot, 2), hero_room)
        price = R.required_equity(bet, pot)
        if _continues_vs_bet(vc, vd, price, rng) and villain_room > 0:
            call = min(bet, villain_room)
            hero_invested += bet
            pot += bet + call
            return pot, hero_invested, hero_returned, True, None
        else:
            # villain folds to hero's bet
            hero_invested += bet
            pot += bet
            return pot, hero_invested, hero_returned, True, "villain"

    # hero checks; villain may bet
    villain_wants, _vsb = _value_bets(vc, vd, rng)
    if villain_wants and villain_room > 0:
        vbet = min(round(DEFAULT_BET_FRAC * pot, 2), villain_room)
        price = R.required_equity(vbet, pot)
        if _continues_vs_bet(hc, hd, price, rng) and hero_room > 0:
            call = min(vbet, hero_room)
            hero_invested += call
            pot += vbet + call
            return pot, hero_invested, hero_returned, True, None
        else:
            return pot, hero_invested, hero_returned, True, "hero"
    # both check
    return pot, hero_invested, hero_returned, True, None


# --------------------------------------------------------------------------- #
# Public: EV of hero facing a bet (the call/fold decision) with implied odds
# --------------------------------------------------------------------------- #
def holdem_call_ev(
    hero_cards: Sequence,
    board: Sequence,
    *,
    pot: float,
    to_call: float,
    villain_range_combos: Optional[List] = None,
    hero_stack: float = 100.0,
    villain_stack: float = 100.0,
    trials: int = 400,
    seed: Optional[int] = None,
) -> dict:
    """EV of CALLING a bet of `to_call` into `pot` on `board`, simulated to
    showdown so implied / reverse-implied odds are included.

    villain_range_combos: optional list of 2-card tuples to sample villain from
    (e.g. the betting range). If None, villain is sampled from all combos
    consistent with the known cards (a neutral prior).

    Returns {ev_call, ev_immediate, implied_delta, equity_realised}. ev_immediate
    is the pure pot-odds EV using showdown equity (no future streets); the
    difference is the implied-odds contribution."""
    rng = random.Random(seed)
    hero = R.as_cards(hero_cards)
    board = R.as_cards(board)
    known = hero + board

    combos = villain_range_combos or R.holdem_combos(known)
    pot_after_call = pot + to_call     # pot once hero calls (villain bet already in `pot`)

    net = 0.0
    wins_sd = 0.0
    n = 0
    for _ in range(trials):
        villain = list(combos[rng.randrange(len(combos))])
        if set(villain) & set(known):
            continue
        deck = [c for c in sv.full_deck() if c not in set(known) | set(villain)]
        rng.shuffle(deck)
        # Hero pays to_call now; pot becomes pot_after_call.
        trial_net = _holdem_trial(
            hero, villain, board, deck, pot_after_call,
            hero_stack, villain_stack, rng) - to_call
        net += trial_net
        n += 1

    ev_call = net / n if n else 0.0

    # Immediate pot-odds EV using showdown equity vs the same range (no future).
    eq = _equity_vs_combos(hero, board, combos, known, rng, trials)
    ev_immediate = eq * pot - (1 - eq) * to_call

    return {
        "ev_call": round(ev_call, 3),
        "ev_immediate": round(ev_immediate, 3),
        "implied_delta": round(ev_call - ev_immediate, 3),
        "equity_realised": round(eq, 4),
        "required_equity": round(R.required_equity(to_call, pot), 4),
    }


def _equity_vs_combos(hero, board, combos, known, rng, trials) -> float:
    wins = 0.0
    n = 0
    need = 5 - len(board)
    for _ in range(trials):
        villain = list(combos[rng.randrange(len(combos))])
        if set(villain) & set(known):
            continue
        deck = [c for c in sv.full_deck() if c not in set(known) | set(villain)]
        rng.shuffle(deck)
        full = board + deck[:need]
        hs = sv.evaluate_5plus(hero + full)
        os_ = sv.evaluate_5plus(villain + full)
        wins += 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)
        n += 1
    return wins / n if n else 0.0


# --------------------------------------------------------------------------- #
# Seven-card stud rollout (implied / reverse-implied odds), heads-up
# --------------------------------------------------------------------------- #
def _stud_cat(cards: List[CardT]) -> int:
    """Made-hand category (0..8) for 3–7 stud cards."""
    if len(cards) >= 5:
        return sv.evaluate_5plus(cards)[0]
    counts: Dict[int, int] = {}
    for r, _s in cards:
        counts[r] = counts.get(r, 0) + 1
    m = max(counts.values()) if counts else 1
    return {1: 0, 2: 1, 3: 3, 4: 7}.get(m, 0)


def _stud_strength(cards: List[CardT]) -> Tuple[int, float]:
    """(made_category, draw_strength) read for a stud holding."""
    return _stud_cat(cards), R.draw_strength(cards, [])


def _stud_beat(hero, villain, pot, hero_room, villain_room, hero_invested, rng):
    """One betting beat on a stud street, mirroring the Hold'em policy."""
    hc, hd = _stud_strength(hero)
    vc, vd = _stud_strength(villain)

    hero_wants, _ = _value_bets(hc, hd, rng)
    if hero_wants and hero_room > 0:
        bet = min(round(DEFAULT_BET_FRAC * pot, 2), hero_room)
        price = R.required_equity(bet, pot)
        if _continues_vs_bet(vc, vd, price, rng) and villain_room > 0:
            call = min(bet, villain_room)
            hero_invested += bet
            pot += bet + call
            return pot, hero_invested, None
        hero_invested += bet
        pot += bet
        return pot, hero_invested, "villain"

    villain_wants, _ = _value_bets(vc, vd, rng)
    if villain_wants and villain_room > 0:
        vbet = min(round(DEFAULT_BET_FRAC * pot, 2), villain_room)
        price = R.required_equity(vbet, pot)
        if _continues_vs_bet(hc, hd, price, rng) and hero_room > 0:
            call = min(vbet, hero_room)
            hero_invested += call
            pot += vbet + call
            return pot, hero_invested, None
        return pot, hero_invested, "hero"
    return pot, hero_invested, None


def _stud_trial(hero_known, villain_known, deck, pot, hero_stack, villain_stack, rng):
    """Deal hero & villain up to seven cards, one per street with a betting beat,
    then show down. Returns hero's net for the streets played here."""
    hero = list(hero_known)
    villain = list(villain_known)
    hero_invested = 0.0
    di = 0
    streets_left = max(7 - len(hero), 7 - len(villain))
    for _ in range(streets_left):
        if len(hero) < 7:
            hero = hero + [deck[di]]; di += 1
        if len(villain) < 7:
            villain = villain + [deck[di]]; di += 1
        pot, hero_invested, folded = _stud_beat(
            hero, villain, pot, hero_stack - hero_invested, villain_stack,
            hero_invested, rng)
        if folded == "villain":
            return pot - hero_invested
        if folded == "hero":
            return -hero_invested
    hs = sv.evaluate_5plus(hero)
    os_ = sv.evaluate_5plus(villain)
    if hs > os_:
        return pot - hero_invested
    if hs == os_:
        return pot / 2.0 - hero_invested
    return -hero_invested


def _stud_equity_vs_range(hero, vup, hidden_combos, known, rng, trials):
    wins = 0.0
    n = 0
    for _ in range(trials):
        hid = hidden_combos[rng.randrange(len(hidden_combos))]
        if set(hid) & set(known):
            continue
        villain = list(vup) + list(hid)
        deck = [c for c in sv.full_deck() if c not in set(known) | set(hid)]
        rng.shuffle(deck)
        i = 0
        h = hero + deck[i:i + (7 - len(hero))]; i += (7 - len(hero))
        o = villain + deck[i:i + (7 - len(villain))]; i += (7 - len(villain))
        hs, os_ = sv.evaluate_5plus(h), sv.evaluate_5plus(o)
        wins += 1.0 if hs > os_ else (0.5 if hs == os_ else 0.0)
        n += 1
    return wins / n if n else 0.0


def stud_call_ev(
    hero_cards: Sequence,
    villain_up_cards: Sequence,
    *,
    pot: float,
    to_call: float,
    villain_hidden_range: Optional[List] = None,
    n_hidden: int = 2,
    hero_stack: float = 100.0,
    villain_stack: float = 100.0,
    trials: int = 300,
    seed: Optional[int] = None,
) -> dict:
    """EV of CALLING `to_call` into `pot` in heads-up seven-card stud, simulated
    out to 7th street so implied / reverse-implied odds are included. The same
    interface and return shape as holdem_call_ev."""
    rng = random.Random(seed)
    hero = R.as_cards(hero_cards)
    vup = R.as_cards(villain_up_cards)
    known = hero + vup

    from itertools import combinations
    pool = R.remaining_deck(known)
    hidden_combos = villain_hidden_range or list(combinations(pool, n_hidden))
    pot_after = pot + to_call

    net = 0.0
    n = 0
    for _ in range(trials):
        hid = hidden_combos[rng.randrange(len(hidden_combos))]
        if set(hid) & set(known):
            continue
        villain_known = list(vup) + list(hid)
        deck = [c for c in sv.full_deck() if c not in set(known) | set(hid)]
        rng.shuffle(deck)
        net += _stud_trial(hero, villain_known, deck, pot_after,
                           hero_stack, villain_stack, rng) - to_call
        n += 1
    ev_call = net / n if n else 0.0

    eq = _stud_equity_vs_range(hero, vup, hidden_combos, known, rng, trials)
    ev_immediate = eq * pot - (1 - eq) * to_call
    return {
        "ev_call": round(ev_call, 3),
        "ev_immediate": round(ev_immediate, 3),
        "implied_delta": round(ev_call - ev_immediate, 3),
        "equity_realised": round(eq, 4),
        "required_equity": round(R.required_equity(to_call, pot), 4),
    }


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # A nut flush draw on the flop facing a half-pot bet: implied odds should
    # make the true EV better than the bare pot-odds EV.
    res = holdem_call_ev(
        ["Ah", "Kh"], ["Qh", "7h", "2c"],
        pot=100, to_call=50, trials=600, seed=1)
    print("Nut flush draw (AhKh on Qh7h2c), pot 100, call 50:")
    for k, v in res.items():
        print(f"  {k:16}: {v}")
