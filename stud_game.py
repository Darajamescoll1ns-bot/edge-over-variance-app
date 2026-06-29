"""
stud_game.py — Interactive seven-card stud engine (heads-up or multi-way)
=========================================================================

A playable hand of seven-card stud against 1–6 equity-driven bots, with
SIMPLIFIED betting (you choose an action category each street — chips are
abstracted to keep the focus on decision quality, not arithmetic).

WHY IT'S BUILT THIS WAY
-----------------------
The platform trains traders on decision-making under partial public
information. Stud is the right vehicle because you act while seeing opponents'
UP-cards but not their down-cards — exactly like trading on public signals with
hidden order flow. Every hero choice is snapshotted (your cards + every live
opponent's up-cards) so it can be graded later against true multi-way equity.

STREETS (stud, not hold'em — there are no community cards)
----------------------------------------------------------
    3rd : 2 down + 1 up (bring-in)   4th/5th/6th : +1 up each   7th : +1 down

PLAYERS
-------
players[0] is the hero; players[1..N] are bots. `num_opponents` may be 1
(heads-up) up to 6 (a full-ish stud table). The same engine drives both modes.

STATE MACHINE
-------------
`new_hand()` deals and runs the action until it is the hero's turn, then PAUSES
with the legal options. `act(state, action)` applies the hero's choice and runs
bots / street progression until the next hero decision or the hand ends. This
resumable design lets a web UI step through a hand one click at a time.

Standard-library only (uses solver.py for equity + hand strength).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import solver as sv
import betting

STREETS = {3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}
ANTE = 1.0
MAX_RAISES_PER_STREET = 3
BOT_ITERS = 400          # Monte Carlo iterations for a bot's read (kept low for speed)
MAX_OPPONENTS = 6


def _card_str(c) -> str:
    return f"{sv.RANKS[c[0] - 2]}{sv.SUITS[c[1]]}"


@dataclass
class Player:
    idx: int
    is_hero: bool
    cards: List                      # full 7-card hand (revealed progressively)
    up_idx: List[int] = field(default_factory=list)
    committed: float = 0.0           # chips put in THIS street
    stack: float = betting.START_STACK
    folded: bool = False
    name: str = ""


@dataclass
class HeroDecision:
    """Snapshot of one hero decision, captured when made so it can be graded on
    what was knowable THEN (including every live opponent's up-cards)."""
    street: int
    street_name: str
    hero_known: List
    hero_known_cards: List
    opponents_up: List               # list of str-lists, one per live opponent
    opponents_up_cards: List         # list of tuple-lists (for grading)
    num_live_opponents: int
    pot: float
    to_call: float
    options: List                    # list of option dicts {key,label,amount}
    action: str                      # chosen option key
    action_label: str                # chosen option label (for display)
    game: str = "stud"


@dataclass
class HandState:
    hand_id: str
    seed: Optional[int]
    players: List[Player]
    dealt: int
    street: int
    pot: float = 0.0
    current_bet: float = 0.0
    raises: int = 0
    to_act: int = 0
    need_to_act: int = 0             # live players still owing an action this street
    awaiting: Optional[dict] = None
    finished: bool = False
    result: Optional[dict] = None
    history: List[HeroDecision] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    # ---- convenience ---- #
    @property
    def hero(self) -> Player:
        return self.players[0]

    @property
    def opponents(self) -> List[Player]:
        return self.players[1:]

    def num_opponents(self) -> int:
        return len(self.players) - 1

    def bet_unit(self) -> float:
        return SMALL_BET if self.street <= 4 else BIG_BET

    def live_players(self) -> List[Player]:
        return [p for p in self.players if not p.folded]

    def live_opponents(self) -> List[Player]:
        return [p for p in self.opponents if not p.folded]

    def player_up_cards(self, p: Player) -> List:
        return [p.cards[i] for i in p.up_idx if i < self.dealt]

    def hero_known_cards(self) -> List:
        return self.hero.cards[:self.dealt]

    # ---- public (no down-card leaks) ---- #
    def public_state(self) -> dict:
        hero_cards = self.hero_known_cards()
        opps = []
        for p in self.opponents:
            opps.append({
                "name": p.name,
                "up": [_card_str(c) for c in self.player_up_cards(p)],
                "hidden": (0 if p.folded else self.dealt - len(self.player_up_cards(p))),
                "folded": p.folded,
                "stack": round(p.stack, 1),
            })
        return {
            "hand_id": self.hand_id,
            "num_opponents": self.num_opponents(),
            "street": self.street,
            "street_name": STREETS[self.street],
            "hero": [
                {"card": _card_str(c), "up": i in self.hero.up_idx}
                for i, c in enumerate(hero_cards)
            ],
            "hero_stack": round(self.hero.stack, 1),
            "opponents": opps,
            "pot": round(self.pot, 2),
            "to_call": round(max(0.0, self.current_bet - self.hero.committed), 2),
            "awaiting": self.awaiting,
            "finished": self.finished,
            "result": self.result,
            "log": self.log[-14:],
        }


# --------------------------------------------------------------------------- #
# Dealing
# --------------------------------------------------------------------------- #

def _deal(seed: Optional[int], num_opponents: int) -> HandState:
    num_opponents = max(1, min(MAX_OPPONENTS, num_opponents))
    rng = random.Random(seed)
    deck = sv.full_deck()
    rng.shuffle(deck)

    players = [Player(idx=0, is_hero=True, cards=deck[0:7], up_idx=[2], name="You")]
    for k in range(num_opponents):
        start = 7 * (k + 1)
        players.append(Player(idx=k + 1, is_hero=False,
                              cards=deck[start:start + 7], up_idx=[2],
                              name=f"Seat {k + 2}"))

    st = HandState(hand_id=uuid.uuid4().hex[:10], seed=seed, players=players,
                   dealt=3, street=3)
    for p in players:                      # post the ante from each stack
        p.stack = round(p.stack - ANTE, 1)
    st.pot = round(ANTE * len(players), 2)
    st.log.append(f"New hand — {num_opponents} opponent(s). "
                  f"Antes posted (pot {st.pot:g}, stacks {players[0].stack:g}).")
    st.log.append(f"Your 3rd street: {' '.join(_card_str(c) for c in players[0].cards[:3])} "
                  f"(up: {_card_str(players[0].cards[2])}).")
    st.log.append("Villains show: "
                  + ", ".join(f"{p.name} {_card_str(p.cards[2])}" for p in players[1:]) + ".")
    _begin_street(st, 3)
    return st


# --------------------------------------------------------------------------- #
# Equity helpers
# --------------------------------------------------------------------------- #

def _hero_equity(st: HandState, iters: int = 1000) -> float:
    opp_up = [st.player_up_cards(p) for p in st.live_opponents()]
    return sv.monte_carlo_equity_multi(st.hero_known_cards(), opp_up,
                                       iterations=iters, seed=st.seed)


def _player_equity(st: HandState, p: Player, iters: int = BOT_ITERS) -> float:
    """A bot's read: its own cards vs. every OTHER live player's up-cards."""
    known = p.cards[:st.dealt]
    others_up = [st.player_up_cards(q) for q in st.live_players() if q.idx != p.idx]
    return sv.monte_carlo_equity_multi(known, others_up, iterations=iters, seed=st.seed)


# --------------------------------------------------------------------------- #
# Options & bot policy
# --------------------------------------------------------------------------- #

def _options_for(st: HandState, p: Player) -> List[dict]:
    return betting.legal_options(st.pot, st.current_bet, p.committed, p.stack,
                                 raises_left=st.raises < MAX_RAISES_PER_STREET)


def _bot_choice(st: HandState, p: Player) -> dict:
    eq = _player_equity(st, p)
    rng = random.Random((st.seed or 0) + p.idx * 13 + st.street * 7 + int(eq * 100))
    return betting.bot_choose(_options_for(st, p), eq, rng)


# --------------------------------------------------------------------------- #
# Applying actions
# --------------------------------------------------------------------------- #

def _apply(st: HandState, p: Player, opt: dict) -> str:
    """Apply an option dict; returns the action family and updates pot/stacks."""
    st.pot, st.current_bet, aggressive, fam = betting.apply_option(
        p, st.pot, st.current_bet, opt)
    if fam in ("bet", "raise", "allin") and aggressive:
        st.raises += 1
    st.log.append(f"{p.name} {opt['label'].lower()}." if fam not in ("fold", "check")
                  else f"{p.name} {fam}.")
    return fam


# --------------------------------------------------------------------------- #
# Street lifecycle
# --------------------------------------------------------------------------- #

def _begin_street(st: HandState, street: int) -> None:
    st.street = street
    st.dealt = street
    new_idx = street - 1
    if street in (4, 5, 6):
        for p in st.players:
            if not p.folded:
                p.up_idx.append(new_idx)
    # 7th is a down card (not added to up_idx).
    st.current_bet = 0.0
    st.raises = 0
    for p in st.players:
        p.committed = 0.0

    live = st.live_players()
    # 3rd: lowest up-card brings in (acts first). 4th+: highest board acts first.
    if street == 3:
        first = min(live, key=lambda p: (p.cards[2][0], p.idx))
    else:
        first = max(live, key=lambda p: (max((p.cards[i][0] for i in p.up_idx),
                                              default=0), -p.idx))
    st.to_act = first.idx
    st.need_to_act = len(live)

    if street in (4, 5, 6):
        catches = ", ".join(f"{p.name} {_card_str(p.cards[new_idx])}"
                            for p in st.players if not p.folded and p.idx != 0)
        st.log.append(f"{STREETS[street]} street. You catch "
                      f"{_card_str(st.hero.cards[new_idx])}"
                      + (f"; {catches}." if catches else "."))
    elif street == 7:
        st.log.append("7th street (down card).")


def _next_to_act(st: HandState, after: int) -> int:
    """Next live player's index clockwise after `after`."""
    n = len(st.players)
    for step in range(1, n + 1):
        idx = (after + step) % n
        if not st.players[idx].folded:
            return idx
    return after


def _advance_or_showdown(st: HandState) -> None:
    if st.street >= 7:
        _showdown(st)
    else:
        _begin_street(st, st.street + 1)


# --------------------------------------------------------------------------- #
# Showdown / settle
# --------------------------------------------------------------------------- #

def _settle_single(st: HandState, winner: Player, by: str) -> None:
    st.finished = True
    st.result = {
        "winner": "hero" if winner.is_hero else winner.name,
        "winner_is_hero": winner.is_hero,
        "by": by,
        "pot": round(st.pot, 2),
        "hero_cards": [_card_str(c) for c in st.hero.cards],
        "showdown": [],
    }


def _showdown(st: HandState) -> None:
    contenders = st.live_players()
    scored = [(p, sv.evaluate_5plus(p.cards)) for p in contenders]
    best = max(sc for _, sc in scored)
    winners = [p for p, sc in scored if sc == best]
    st.finished = True
    hero_won = any(p.is_hero for p in winners)
    if len(winners) == 1:
        wname = "hero" if winners[0].is_hero else winners[0].name
    else:
        wname = "split" + ("/hero" if hero_won else "")
    st.result = {
        "winner": wname,
        "winner_is_hero": hero_won,
        "by": "showdown",
        "pot": round(st.pot, 2),
        "hero_cards": [_card_str(c) for c in st.hero.cards],
        "showdown": [
            {"name": ("You" if p.is_hero else p.name),
             "cards": [_card_str(c) for c in p.cards]}
            for p, _ in scored
        ],
    }
    st.log.append("Showdown.")


# --------------------------------------------------------------------------- #
# Engine loop
# --------------------------------------------------------------------------- #

def _run_until_hero(st: HandState) -> HandState:
    guard = 0
    while not st.finished:
        guard += 1
        if guard > 500:
            raise RuntimeError("state machine did not terminate")

        live = st.live_players()
        if len(live) == 1:
            _settle_single(st, live[0], by="fold")
            st.log.append(f"{live[0].name} win — everyone else folded.")
            return st

        if st.need_to_act <= 0:
            _advance_or_showdown(st)
            continue

        p = st.players[st.to_act]
        if p.folded:
            st.to_act = _next_to_act(st, st.to_act)
            continue

        if p.is_hero:
            st.awaiting = {
                "options": _options_for(st, p),
                "pot": round(st.pot, 2),
                "to_call": round(max(0.0, st.current_bet - p.committed), 2),
                "street_name": STREETS[st.street],
                "hero_stack": round(p.stack, 1),
            }
            return st

        # Bot acts.
        opt = _bot_choice(st, p)
        fam = _apply(st, p, opt)
        _post_action(st, p, fam)
    return st


def _post_action(st: HandState, p: Player, fam: str) -> None:
    """Update the need-to-act counter and advance the actor pointer."""
    if fam in ("bet", "raise", "allin"):
        # Everyone else still live must now respond.
        st.need_to_act = len(st.live_players()) - 1
    else:  # fold / check / call
        st.need_to_act -= 1
    st.to_act = _next_to_act(st, p.idx)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def new_hand(seed: Optional[int] = None, num_opponents: int = 1) -> HandState:
    st = _deal(seed, num_opponents)
    return _run_until_hero(st)


def act(st: HandState, action: str) -> HandState:
    if st.finished:
        return st
    if st.awaiting is None:
        raise RuntimeError("not awaiting a hero action")
    options = st.awaiting["options"]
    chosen = next((o for o in options if o["key"] == action), None)
    if chosen is None:
        raise ValueError(f"illegal action {action!r}; legal: {[o['key'] for o in options]}")

    hero = st.hero
    live_opps = st.live_opponents()
    st.history.append(HeroDecision(
        street=st.street, street_name=STREETS[st.street],
        hero_known=[_card_str(c) for c in st.hero_known_cards()],
        hero_known_cards=list(st.hero_known_cards()),
        opponents_up=[[_card_str(c) for c in st.player_up_cards(p)] for p in live_opps],
        opponents_up_cards=[list(st.player_up_cards(p)) for p in live_opps],
        num_live_opponents=len(live_opps),
        pot=round(st.pot, 2),
        to_call=round(max(0.0, st.current_bet - hero.committed), 2),
        options=[dict(o) for o in options], action=action,
        action_label=chosen["label"],
    ))

    st.awaiting = None
    fam = _apply(st, hero, chosen)
    _post_action(st, hero, fam)
    return _run_until_hero(st)


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import random as _r
    n = _r.randint(1, 6)
    st = new_hand(seed=7, num_opponents=n)
    print("\n".join(st.log))
    steps = 0
    while not st.finished and steps < 60:
        opts = st.awaiting["options"]
        choice = "call" if "call" in opts else ("check" if "check" in opts else opts[0])
        print(f"  [hero {st.awaiting['street_name']}] {opts} -> {choice} "
              f"(pot {st.awaiting['pot']}, to_call {st.awaiting['to_call']})")
        st = act(st, choice)
        steps += 1
    print("\nResult:", st.result["winner"], "by", st.result["by"], "pot", st.result["pot"])
    print("Hero decisions:", len(st.history))
