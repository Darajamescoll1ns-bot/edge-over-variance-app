"""
decision_schema_stdlib.py

Dependency-free equivalent of decision_schema.py.
Uses only Python standard library: dataclasses, enum, datetime, json.

Design principles are identical — only the implementation layer changes.
Prefer this on environments where Pydantic is unavailable or undesirable.
Round-trip JSON serialization validated for both poker and trading Decision objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Domain(str, Enum):
    POKER = "poker"
    TRADING = "trading"


class EstimateSource(str, Enum):
    USER_STATED = "user_stated"
    INFERRED = "inferred"


class ReferenceType(str, Enum):
    SOLVER_EXACT = "solver_exact"
    SOLVER_APPROX = "solver_approx"
    EQUITY_ONLY = "equity_only"
    VALIDATED_PLAN = "validated_plan"
    NONE = "none"


class PokerVariant(str, Enum):
    SEVEN_CARD_STUD = "seven_card_stud"
    NLHE = "nlhe"
    PLO = "plo"


class PokerActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    BRING_IN = "bring_in"
    COMPLETE = "complete"


class AssetClass(str, Enum):
    EQUITY = "equity"
    FUTURE = "future"
    FX = "fx"
    CRYPTO = "crypto"
    OPTION = "option"


class TradeDecisionType(str, Enum):
    ENTRY = "entry"
    ADD = "add"
    REDUCE = "reduce"
    EXIT = "exit"
    ADJUST_STOP = "adjust_stop"
    HOLD = "hold"


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SHORT = "short"
    COVER = "cover"


# --------------------------------------------------------------------------- #
# Shared sub-objects
# --------------------------------------------------------------------------- #
@dataclass
class ExAnteEstimate:
    """User's belief BEFORE the outcome is known. Powers calibration."""
    win_probability: float          # [0.0, 1.0]
    target_definition: str
    confidence: Optional[float] = None
    source: EstimateSource = EstimateSource.USER_STATED

    def __post_init__(self):
        if not 0.0 <= self.win_probability <= 1.0:
            raise ValueError("win_probability must be in [0, 1]")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass
class Sizing:
    """Normalized so sizing discipline is comparable across domains."""
    absolute_size: float
    size_unit: str              # "bb", "USD", "contracts", ...
    risk_fraction: float        # fraction of bankroll at risk [0, ∞)

    def __post_init__(self):
        if self.risk_fraction < 0.0:
            raise ValueError("risk_fraction must be >= 0")


# ---- Reference policy: polymorphic ---------------------------------------- #
@dataclass
class SolverReference:
    """Poker baseline. EV-optimal action exists → ev_loss is meaningful."""
    kind: str = field(default="solver", init=False)
    reference_type: ReferenceType = ReferenceType.SOLVER_EXACT
    best_action: str = ""
    best_action_ev: float = 0.0
    taken_action_ev: float = 0.0
    ev_loss: float = 0.0                        # best_action_ev - taken_action_ev
    strategy: Optional[dict] = None             # full mixed strategy
    true_equity: Optional[float] = None         # [0.0, 1.0]

    def __post_init__(self):
        if self.ev_loss < 0.0:
            raise ValueError("ev_loss must be >= 0")
        if self.true_equity is not None and not 0.0 <= self.true_equity <= 1.0:
            raise ValueError("true_equity must be in [0, 1]")


@dataclass
class PlanReference:
    """Trading baseline. No EV-optimal action — quality = adherence to a
    validated plan. Strategy validity tracked separately from execution."""
    kind: str = field(default="plan", init=False)
    reference_type: ReferenceType = ReferenceType.VALIDATED_PLAN
    was_on_plan: bool = False
    setup_id: Optional[str] = None
    planned_entry: Optional[float] = None
    planned_stop: Optional[float] = None
    planned_target: Optional[float] = None
    setup_expectancy: Optional[float] = None
    process_violations: list = field(default_factory=list)
    # e.g. ["no_stop", "off_plan_entry", "moved_stop_against_position"]


# ---- Poker specialization -------------------------------------------------- #
@dataclass
class Card:
    rank: str   # "A","K",...,"2"
    suit: str   # "s","h","d","c"


@dataclass
class PokerContext:
    domain: str = field(default=Domain.POKER.value, init=False)
    variant: PokerVariant = PokerVariant.SEVEN_CARD_STUD
    street: str = ""
    street_index: int = 1
    hero_down_cards: list = field(default_factory=list)     # list[Card]
    hero_up_cards: list = field(default_factory=list)
    board: list = field(default_factory=list)
    exposed_cards: list = field(default_factory=list)
    dead_cards: list = field(default_factory=list)
    pot_size: float = 0.0
    to_call: float = 0.0
    effective_stack: float = 0.0
    num_active_players: int = 2
    seat: int = 0
    antes: float = 0.0
    bring_in: Optional[float] = None
    legal_actions: list = field(default_factory=list)       # list[PokerActionType]


@dataclass
class PokerAction:
    kind: str = field(default="poker", init=False)
    action_type: PokerActionType = PokerActionType.FOLD
    amount: Optional[float] = None


# ---- Trading specialization ------------------------------------------------ #
@dataclass
class Instrument:
    symbol: str = ""
    asset_class: AssetClass = AssetClass.FUTURE
    currency: str = "USD"
    multiplier: float = 1.0
    tick_size: Optional[float] = None


@dataclass
class MarketSnapshot:
    """State at decision time — context, not outcome."""
    price: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    atr: Optional[float] = None


@dataclass
class TradingContext:
    domain: str = field(default=Domain.TRADING.value, init=False)
    instrument: Instrument = field(default_factory=Instrument)
    decision_type: TradeDecisionType = TradeDecisionType.ENTRY
    account_equity: float = 0.0
    existing_position: float = 0.0
    market: MarketSnapshot = field(default_factory=MarketSnapshot)
    setup_tag: Optional[str] = None


@dataclass
class TradingAction:
    kind: str = field(default="trading", init=False)
    side: TradeSide = TradeSide.BUY
    quantity: float = 0.0
    price: Optional[float] = None
    order_type: str = "market"


# ---- Outcome --------------------------------------------------------------- #
@dataclass
class Outcome:
    """Resolved AFTER the decision. Quarantined from decision-quality scoring.
    Used only for calibration (realized binary) and strategy-validity axis."""
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    realized_value: Optional[float] = None
    realized_value_unit: Optional[str] = None
    won: Optional[bool] = None
    mae: Optional[float] = None     # max adverse excursion (trading)
    mfe: Optional[float] = None     # max favorable excursion (trading)


# --------------------------------------------------------------------------- #
# The atomic unit the scoring engine consumes
# --------------------------------------------------------------------------- #
@dataclass
class Decision:
    """One decision under uncertainty — a poker action or a trade."""
    decision_id: str
    user_id: str
    session_id: str
    domain: Domain
    timestamp: datetime
    sequence_index: int
    context: object                             # PokerContext | TradingContext
    action_taken: object                        # PokerAction | TradingAction
    sizing: Sizing
    reference_policy: object                    # SolverReference | PlanReference
    decision_latency_ms: Optional[int] = None
    ex_ante_estimate: Optional[ExAnteEstimate] = None
    outcome: Optional[Outcome] = None


# --------------------------------------------------------------------------- #
# Derived by the scoring engine
# --------------------------------------------------------------------------- #
@dataclass
class DecisionEvaluation:
    decision_id: str
    ev_loss_normalized: Optional[float] = None
    sizing_deviation: Optional[float] = None
    brier_component: Optional[float] = None
    process_violations: list = field(default_factory=list)
    stress_context: dict = field(default_factory=dict)


@dataclass
class DecisionQualityProfile:
    """Aggregate five-dimension profile on a 0–100 scale."""
    user_id: str
    domain: Domain
    sample_size: int
    policy_adherence: float
    calibration: float
    resolution: float
    sizing_discipline: float
    outcome_independence: float
    tilt_control: float
    confidence_intervals: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# JSON serialization helpers
# --------------------------------------------------------------------------- #
class _DecisionEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


def _decode_decision(d: dict) -> dict:
    """Best-effort post-process: convert ISO strings back to datetime."""
    for k, v in d.items():
        if isinstance(v, str):
            try:
                d[k] = datetime.fromisoformat(v)
            except ValueError:
                pass
        elif isinstance(v, dict):
            d[k] = _decode_decision(v)
    return d


def decision_to_json(decision: Decision) -> str:
    return json.dumps(asdict(decision), cls=_DecisionEncoder, indent=2)


def decision_from_json(s: str) -> dict:
    """Returns a plain dict — reconstruct dataclass instances manually if needed."""
    return _decode_decision(json.loads(s))


# --------------------------------------------------------------------------- #
# Round-trip smoke test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # --- Stud poker decision ---
    poker_decision = Decision(
        decision_id="pok-001",
        user_id="dara",
        session_id="sess-2026-06-17",
        domain=Domain.POKER,
        timestamp=datetime(2026, 6, 17, 14, 0, 0),
        sequence_index=1,
        context=PokerContext(
            variant=PokerVariant.SEVEN_CARD_STUD,
            street="fifth",
            street_index=3,
            hero_down_cards=[Card("A", "s"), Card("K", "s")],
            hero_up_cards=[Card("Q", "s")],
            exposed_cards=[Card("J", "h"), Card("T", "d"), Card("9", "c")],
            dead_cards=[Card("J", "h"), Card("T", "d")],
            pot_size=120.0,
            to_call=20.0,
            effective_stack=480.0,
            num_active_players=3,
            seat=2,
            bring_in=5.0,
        ),
        action_taken=PokerAction(
            action_type=PokerActionType.CALL,
            amount=20.0,
        ),
        sizing=Sizing(
            absolute_size=20.0,
            size_unit="chips",
            risk_fraction=0.04,
        ),
        reference_policy=SolverReference(
            reference_type=ReferenceType.SOLVER_APPROX,
            best_action="call",
            best_action_ev=18.5,
            taken_action_ev=18.5,
            ev_loss=0.0,
            true_equity=0.71,
        ),
        ex_ante_estimate=ExAnteEstimate(
            win_probability=0.68,
            target_definition="hero wins at showdown",
            source=EstimateSource.USER_STATED,
        ),
    )

    # --- Futures trading decision ---
    trade_decision = Decision(
        decision_id="trd-001",
        user_id="dara",
        session_id="sess-2026-06-17",
        domain=Domain.TRADING,
        timestamp=datetime(2026, 6, 17, 9, 35, 0),
        sequence_index=1,
        context=TradingContext(
            instrument=Instrument(
                symbol="ES",
                asset_class=AssetClass.FUTURE,
                multiplier=50.0,
                tick_size=0.25,
            ),
            decision_type=TradeDecisionType.ENTRY,
            account_equity=100_000.0,
            existing_position=0.0,
            market=MarketSnapshot(
                price=5400.25,
                atr=22.5,
                timestamp=datetime(2026, 6, 17, 9, 35, 0),
            ),
            setup_tag="breakout_retest",
        ),
        action_taken=TradingAction(
            side=TradeSide.BUY,
            quantity=1.0,
            price=5400.25,
            order_type="limit",
        ),
        sizing=Sizing(
            absolute_size=1.0,
            size_unit="contracts",
            risk_fraction=0.01,
        ),
        reference_policy=PlanReference(
            was_on_plan=True,
            setup_id="breakout_retest_v2",
            planned_entry=5400.00,
            planned_stop=5389.00,
            planned_target=5422.00,
            setup_expectancy=0.35,
            process_violations=[],
        ),
        ex_ante_estimate=ExAnteEstimate(
            win_probability=0.55,
            target_definition="+2R reached before -1R stop",
            source=EstimateSource.USER_STATED,
        ),
    )

    for label, d in [("Stud poker", poker_decision), ("ES futures", trade_decision)]:
        serialized = decision_to_json(d)
        recovered = decision_from_json(serialized)
        print(f"[{label}] round-trip OK — {len(serialized)} bytes")

    print("All tests passed.")
