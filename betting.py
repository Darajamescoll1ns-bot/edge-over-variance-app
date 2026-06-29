"""
betting.py — shared chip-stack + sizing logic for both poker engines
====================================================================

Both the stud and Hold'em engines share the same betting mechanics: players
have a chip STACK, there's a real POT, and on each turn the hero gets discrete
SIZING options (fold / check / call / a couple of bet or raise sizes / all-in).
Centralising it here keeps the two engines consistent and means sizing is taught
the same way everywhere.

Options are dicts: {key, label, amount}
  * key    — stable id ("fold","check","call","bet_50","bet_100","raise_small",
             "raise_big","allin"); the UI sends this back.
  * label  — what the button shows ("Raise to 12", "Bet 6 (½ pot)", ...).
  * amount — additional chips this action puts in (0 for fold/check).

Sizing is expressed as fractions of the pot — the way real position sizing is
discussed — capped by the player's stack (an action that would exceed the stack
becomes all-in).
"""

from __future__ import annotations

from typing import List

START_STACK = 100.0          # chips each player starts a hand with


def _round(x: float) -> float:
    return round(x, 1)


def legal_options(pot: float, current_bet: float, committed: float,
                  stack: float, raises_left: bool) -> List[dict]:
    """Build the discrete option list for a player to act on."""
    owed = _round(current_bet - committed)
    opts: List[dict] = []

    if owed <= 1e-6:
        # No bet to face: check, or open with a sized bet.
        opts.append({"key": "check", "label": "Check", "amount": 0.0})
        if raises_left and stack > 0:
            _add_aggressive(opts, pot, committed, stack, owed=0.0,
                            sizes=[(0.5, "bet_50", "½ pot"), (1.0, "bet_100", "pot")],
                            verb="Bet")
    else:
        opts.append({"key": "fold", "label": "Fold", "amount": 0.0})
        call_amt = _round(min(owed, stack))
        opts.append({"key": "call", "label": f"Call {call_amt:g}", "amount": call_amt})
        if raises_left and stack > owed:
            _add_aggressive(opts, pot, committed, stack, owed=owed,
                            sizes=[(0.75, "raise_small", "small"), (1.5, "raise_big", "big")],
                            verb="Raise to")
    return _dedupe(opts, stack)


def _add_aggressive(opts, pot, committed, stack, owed, sizes, verb):
    for frac, key, name in sizes:
        # Additional chips = call portion + a raise/bet of `frac` of the post-call pot.
        raise_part = max(1.0, (pot + owed) * frac)
        amount = _round(min(stack, owed + raise_part))
        total = _round(committed + amount)
        if verb == "Bet":
            label = f"Bet {amount:g} ({name})"
        else:
            label = f"Raise to {total:g} ({name})"
        opts.append({"key": key, "label": label, "amount": amount})
    # An all-in option when the stack is within reach of the pot (short stack).
    if stack <= pot * 1.2:
        opts.append({"key": "allin", "label": f"All-in {_round(stack):g}", "amount": _round(stack)})


def _dedupe(opts, stack):
    """Drop sized options that collapse to the same amount (tiny pots / short
    stacks), and relabel any bet/raise that equals the whole stack as all-in."""
    seen_amounts = set()
    out = []
    for o in opts:
        amt = o["amount"]
        if o["key"] in ("fold", "check", "call"):
            out.append(o)
            continue
        if amt <= 0:
            continue
        if amt in seen_amounts:
            continue
        seen_amounts.add(amt)
        if abs(amt - round(stack, 1)) < 1e-6 and o["key"] != "allin":
            o = {"key": "allin", "label": f"All-in {_round(stack):g}", "amount": amt}
            if amt in seen_amounts and any(x["key"] == "allin" for x in out):
                continue
        out.append(o)
    # Keep a single all-in.
    final, allin_done = [], False
    for o in out:
        if o["key"] == "allin":
            if allin_done:
                continue
            allin_done = True
        final.append(o)
    return final


def family(key: str) -> str:
    """Group an option key into an action family for archetype/grading logic."""
    if key == "fold":
        return "fold"
    if key == "check":
        return "check"
    if key == "call":
        return "call"
    if key.startswith("bet"):
        return "bet"
    if key.startswith("raise"):
        return "raise"
    if key == "allin":
        return "allin"
    return key


def apply_option(player, pot: float, current_bet: float, opt: dict):
    """Apply an option to a player's stack and the pot. Returns
    (new_pot, new_current_bet, aggressive, family)."""
    key = opt["key"]
    amt = opt.get("amount", 0.0)
    if key == "fold":
        player.folded = True
        return pot, current_bet, False, "fold"
    if key == "check":
        return pot, current_bet, False, "check"
    player.committed = _round(player.committed + amt)
    player.stack = _round(player.stack - amt)
    pot = _round(pot + amt)
    aggressive = player.committed > current_bet + 1e-6
    if aggressive:
        current_bet = player.committed
    return pot, current_bet, aggressive, family(key)


def bot_choose(options: List[dict], equity: float, rng) -> dict:
    """Pick an option for a bot from `equity`. Sizes up with strength; folds weak
    hands facing a bet; takes free cards when behind."""
    keys = {o["key"]: o for o in options}
    facing_bet = "call" in keys
    aggressive = [o for o in options if family(o["key"]) in ("bet", "raise", "allin")]
    e = equity + rng.uniform(-0.04, 0.04)

    if facing_bet:
        if e < 0.40:
            return keys["fold"]
        if e > 0.66 and aggressive:
            return aggressive[-1] if e > 0.80 else aggressive[0]   # bigger when very strong
        return keys["call"]
    else:
        if e > 0.60 and aggressive:
            return aggressive[-1] if e > 0.78 else aggressive[0]
        return keys["check"]
