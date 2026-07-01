"""
holdem_game.py — Interactive Texas Hold'em engine (2-8 players)
==============================================================

A playable hand of no-limit-style Texas Hold'em with SIMPLIFIED betting (you
pick an action category each street; chips abstracted), against 1-7 equity-
driven bots. Mirrors the stud engine's resumable state machine so the web UI can
step through a hand one click at a time, and feeds the SAME grading + markets
coach.

HOLD'EM vs STUD (why a separate engine)
---------------------------------------
* Each player has 2 hidden hole cards; there are NO exposed up-cards.
* Five COMMUNITY cards are shared: flop (3), turn (1), river (1).
* Action is seeded by blinds, not a bring-in.
So your read is the board + the number of live players + pot odds — not visible
opponent cards. That makes Hold'em a looser fit for the "partial public
information" thesis (the markets analogy is more generic: acting on shared
public data and price, sizing to edge), but the decision-quality machinery is
identical.

STREETS
-------
    preflop (0 board) -> flop (3) -> turn (4) -> river (5)

Standard-library only (uses solver.py for equity + hand strength).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import solver as sv
import betting

STREET_NAMES = {0: "preflop", 1: "flop", 2: "turn", 3: "river"}
BOARD_SHOWN = {0: 0, 1: 3, 2: 4, 3: 5}
SMALL_BLIND = 0.5
BIG_BLIND = 1.0
MIN_PLAYERS = 2
MAX_PLAYERS = 8
MAX_RAISES_PER_STREET = 3
BOT_ITERS = 350

# Position labels by seat offset from the dealer button, so the player can see
# where they're sitting each hand (the button is randomised every deal).
_MIDS = {0: [], 1: ["CO"], 2: ["UTG", "CO"], 3: ["UTG", "HJ", "CO"],
         4: ["UTG", "MP", "HJ", "CO"], 5: ["UTG", "UTG+1", "MP", "HJ", "CO"]}


def _position_labels(n: int, button: int) -> dict:
    """seat index -> position label (BTN/SB/BB/UTG…/CO) for an n-handed table."""
    if n == 2:
        return {button % n: "BTN", (button + 1) % n: "BB"}   # heads-up: BTN posts SB
    labels = {button % n: "BTN", (button + 1) % n: "SB", (button + 2) % n: "BB"}
    mids = _MIDS.get(n - 3, ["UTG"] * max(0, n - 3))
    for j, lab in enumerate(mids):
        labels[(button + 3 + j) % n] = lab
    return labels


def _card_str(c) -> str:
    return f"{sv.RANKS[c[0] - 2]}{sv.SUITS[c[1]]}"


@dataclass
class Player:
    idx: int
    is_hero: bool
    hole: List
    committed: float = 0.0
    stack: float = betting.START_STACK
    folded: bool = False
    name: str = ""


@dataclass
class HoldemDecision:
    game: str
    street: int
    street_name: str
    hero_hole: List
    hero_hole_cards: List
    board: List
    board_cards: List
    num_live_opponents: int
    pot: float
    to_call: float
    options: List                    # list of option dicts {key,label,amount}
    action: str                      # chosen option key
    action_label: str


@dataclass
class HandState:
    hand_id: str
    seed: Optional[int]
    players: List[Player]
    board: List                       # the 5 community cards (revealed by street)
    street: int = 0
    pot: float = 0.0
    current_bet: float = 0.0
    raises: int = 0
    button: int = 0
    to_act: int = 0
    need_to_act: int = 0
    awaiting: Optional[dict] = None
    finished: bool = False
    result: Optional[dict] = None
    history: List[HoldemDecision] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    @property
    def hero(self) -> Player:
        return self.players[0]

    @property
    def opponents(self) -> List[Player]:
        return self.players[1:]

    def num_opponents(self) -> int:
        return len(self.players) - 1

    def bet_unit(self) -> float:
        return SMALL_BET if self.street <= 1 else BIG_BET

    def live_players(self) -> List[Player]:
        return [p for p in self.players if not p.folded]

    def live_opponents(self) -> List[Player]:
        return [p for p in self.opponents if not p.folded]

    def board_shown(self) -> List:
        return self.board[:BOARD_SHOWN[self.street]]

    def public_state(self) -> dict:
        n = len(self.players)
        pos = _position_labels(n, self.button)
        return {
            "hand_id": self.hand_id,
            "game": "holdem",
            "num_opponents": self.num_opponents(),
            "street": self.street,
            "street_name": STREET_NAMES[self.street],
            "hero": [{"card": _card_str(c), "up": True} for c in self.hero.hole],
            "hero_stack": round(self.hero.stack, 1),
            "hero_position": pos.get(0),
            "hero_is_button": self.button == 0,
            "button_seat": self.button,
            "board": [_card_str(c) for c in self.board_shown()],
            "opponents": [
                {"name": p.name,
                 "hidden": 0 if p.folded else 2,
                 "folded": p.folded,
                 "stack": round(p.stack, 1),
                 "position": pos.get(p.idx),
                 "is_button": p.idx == self.button}
                for p in self.opponents
            ],
            "pot": round(self.pot, 2),
            "to_call": round(max(0.0, self.current_bet - self.hero.committed), 2),
            "awaiting": self.awaiting,
            "finished": self.finished,
            "result": self.result,
            "log": self.log[-14:],
        }


# --------------------------------------------------------------------------- #
# Dealing & blinds
# --------------------------------------------------------------------------- #

def _deal(seed: Optional[int], num_opponents: int) -> HandState:
    n = max(MIN_PLAYERS - 1, min(MAX_PLAYERS - 1, num_opponents)) + 1   # total players
    rng = random.Random(seed)
    deck = sv.full_deck()
    rng.shuffle(deck)

    players = [Player(idx=0, is_hero=True, hole=[deck[0], deck[1]], name="You")]
    pos = 2
    for k in range(1, n):
        players.append(Player(idx=k, is_hero=False, hole=[deck[pos], deck[pos + 1]],
                              name=f"Seat {k + 1}"))
        pos += 2
    board = deck[pos:pos + 5]

    st = HandState(hand_id=uuid.uuid4().hex[:10], seed=seed, players=players,
                   board=board, street=0)
    st.button = rng.randrange(n)
    _post_blinds(st)
    st.log.append(f"New Hold'em hand — {n} players. "
                  f"Your hole cards: {_card_str(players[0].hole[0])} "
                  f"{_card_str(players[0].hole[1])}.")
    _begin_preflop(st)
    return st


def _seat_after(st: HandState, idx: int, skip_folded: bool = True) -> int:
    n = len(st.players)
    for step in range(1, n + 1):
        j = (idx + step) % n
        if not skip_folded or not st.players[j].folded:
            return j
    return idx


def _post_blinds(st: HandState) -> None:
    n = len(st.players)
    if n == 2:
        sb, bb = st.button, (st.button + 1) % n
    else:
        sb, bb = (st.button + 1) % n, (st.button + 2) % n
    st.players[sb].committed = SMALL_BLIND
    st.players[sb].stack = round(st.players[sb].stack - SMALL_BLIND, 1)
    st.players[bb].committed = BIG_BLIND
    st.players[bb].stack = round(st.players[bb].stack - BIG_BLIND, 1)
    st.current_bet = BIG_BLIND
    st.pot = SMALL_BLIND + BIG_BLIND
    st._sb, st._bb = sb, bb         # type: ignore[attr-defined]
    st.log.append(f"{st.players[sb].name} posts SB, {st.players[bb].name} posts BB.")


def _begin_preflop(st: HandState) -> None:
    st.street = 0
    n = len(st.players)
    bb = getattr(st, "_bb", (st.button + 2) % n)
    st.to_act = (st.button + 1) % n if n == 2 else _seat_after(st, bb)
    st.raises = 0
    st.need_to_act = len(st.live_players())


def _begin_postflop(st: HandState, street: int) -> None:
    st.street = street
    st.current_bet = 0.0
    st.raises = 0
    for p in st.players:
        p.committed = 0.0
    # First to act postflop is the first live seat after the button.
    st.to_act = _seat_after(st, st.button)
    st.need_to_act = len(st.live_players())
    shown = " ".join(_card_str(c) for c in st.board_shown())
    st.log.append(f"{STREET_NAMES[street].capitalize()}: {shown}")


# --------------------------------------------------------------------------- #
# Options & bot policy
# --------------------------------------------------------------------------- #

def _options_for(st: HandState, p: Player) -> List[dict]:
    return betting.legal_options(st.pot, st.current_bet, p.committed, p.stack,
                                 raises_left=st.raises < MAX_RAISES_PER_STREET)


def _player_equity(st: HandState, p: Player, iters: int = BOT_ITERS) -> float:
    others = len(st.live_players()) - 1
    return sv.holdem_equity(p.hole, st.board_shown(), max(1, others),
                            iterations=iters, seed=st.seed)


def _bot_choice(st: HandState, p: Player) -> dict:
    eq = _player_equity(st, p)
    rng = random.Random((st.seed or 0) + p.idx * 17 + st.street * 5 + int(eq * 100))
    return betting.bot_choose(_options_for(st, p), eq, rng)


# --------------------------------------------------------------------------- #
# Applying actions
# --------------------------------------------------------------------------- #

def _apply(st: HandState, p: Player, opt: dict) -> str:
    st.pot, st.current_bet, aggressive, fam = betting.apply_option(
        p, st.pot, st.current_bet, opt)
    if fam in ("bet", "raise", "allin") and aggressive:
        st.raises += 1
    st.log.append(f"{p.name} {opt['label'].lower()}." if fam not in ("fold", "check")
                  else f"{p.name} {fam}.")
    return fam


def _post_action(st: HandState, p: Player, fam: str) -> None:
    if fam in ("bet", "raise", "allin"):
        st.need_to_act = len(st.live_players()) - 1
    else:
        st.need_to_act -= 1
    st.to_act = _seat_after(st, p.idx)


# --------------------------------------------------------------------------- #
# Showdown / settle
# --------------------------------------------------------------------------- #

def _settle_single(st: HandState, winner: Player) -> None:
    st.finished = True
    st.result = {
        "winner": "hero" if winner.is_hero else winner.name,
        "winner_is_hero": winner.is_hero,
        "by": "fold",
        "pot": round(st.pot, 2),
        "board": [_card_str(c) for c in st.board_shown()],
        "showdown": [],
    }


def _showdown(st: HandState) -> None:
    contenders = st.live_players()
    scored = [(p, sv.evaluate_5plus(p.hole + st.board)) for p in contenders]
    best = max(sc for _, sc in scored)
    winners = [p for p, sc in scored if sc == best]
    hero_won = any(p.is_hero for p in winners)
    if len(winners) == 1:
        wname = "hero" if winners[0].is_hero else winners[0].name
    else:
        wname = "split" + ("/hero" if hero_won else "")
    st.finished = True
    st.result = {
        "winner": wname, "winner_is_hero": hero_won, "by": "showdown",
        "pot": round(st.pot, 2),
        "board": [_card_str(c) for c in st.board],
        "showdown": [
            {"name": ("You" if p.is_hero else p.name),
             "cards": [_card_str(c) for c in p.hole]}
            for p, _ in scored
        ],
    }
    st.log.append("Showdown. Board: " + " ".join(_card_str(c) for c in st.board) + ".")


def _advance_or_showdown(st: HandState) -> None:
    if st.street >= 3:
        _showdown(st)
    else:
        _begin_postflop(st, st.street + 1)


# --------------------------------------------------------------------------- #
# Engine loop
# --------------------------------------------------------------------------- #

def _run_until_hero(st: HandState) -> HandState:
    guard = 0
    while not st.finished:
        guard += 1
        if guard > 600:
            raise RuntimeError("state machine did not terminate")

        live = st.live_players()
        if len(live) == 1:
            _settle_single(st, live[0])
            st.log.append(f"{live[0].name} win — everyone else folded.")
            return st

        if st.need_to_act <= 0:
            _advance_or_showdown(st)
            continue

        p = st.players[st.to_act]
        if p.folded:
            st.to_act = _seat_after(st, st.to_act)
            continue

        if p.is_hero:
            st.awaiting = {
                "options": _options_for(st, p),
                "pot": round(st.pot, 2),
                "to_call": round(max(0.0, st.current_bet - p.committed), 2),
                "street_name": STREET_NAMES[st.street],
                "hero_stack": round(p.stack, 1),
            }
            return st

        opt = _bot_choice(st, p)
        fam = _apply(st, p, opt)
        _post_action(st, p, fam)
    return st


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
    st.history.append(HoldemDecision(
        game="holdem", street=st.street, street_name=STREET_NAMES[st.street],
        hero_hole=[_card_str(c) for c in hero.hole],
        hero_hole_cards=list(hero.hole),
        board=[_card_str(c) for c in st.board_shown()],
        board_cards=list(st.board_shown()),
        num_live_opponents=len(st.live_opponents()),
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
    n = _r.randint(1, 7)
    st = new_hand(seed=7, num_opponents=n)
    print("\n".join(st.log))
    steps = 0
    while not st.finished and steps < 80:
        opts = st.awaiting["options"]
        choice = "call" if "call" in opts else ("check" if "check" in opts else opts[0])
        print(f"  [hero {st.awaiting['street_name']}] {opts} -> {choice} "
              f"(pot {st.awaiting['pot']}, to_call {st.awaiting['to_call']})")
        st = act(st, choice)
        steps += 1
    print("\nResult:", st.result["winner"], "by", st.result["by"], "pot", st.result["pot"])
    print("Hero decisions:", len(st.history))
