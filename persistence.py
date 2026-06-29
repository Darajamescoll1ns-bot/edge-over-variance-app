"""
persistence.py — Storage for sessions  (Phase 2)
================================================

Flat-JSON storage, one file per session, plus a lightweight index so listing
and filtering sessions doesn't require opening every file.

ON-DISK LAYOUT
--------------
    <base_dir>/
        index.json                 # session metadata, for cheap listing/queries
        sessions/
            <session_id>.json      # one Session per file

SESSION FILE SCHEMA  (the format the roadmap specified)
-------------------------------------------------------
    {
      "session_id": "...",
      "user_id":    "...",
      "timestamp":  "<ISO8601>",   # session created_at
      "domain":     "poker" | "trading",
      "decisions":  [ <Decision JSON>, ... ]
    }

Each Decision is exactly what decision_schema_stdlib.decision_to_json() emits.

WHY AN INDEX
------------
The roadmap warned that flat JSON has no query capability. The index stores
per-session metadata (id, user, domain, timestamp, count, headline scores) so
session-level questions ("show my trading sessions from last week", "which
sessions had tilt_control < 50") are answered without a full scan. Decision-
LEVEL queries ("every decision where sizing_deviation > 2") still need to read
the relevant files, but `query_decisions()` makes that one call.

DESIGN CHOICE: STILL JSON, NOT SQLITE (YET)
-------------------------------------------
Per the roadmap, we stay on flat files until they hurt. The index pushes the
"JSON can't query" pain point out far enough that SQLite isn't needed for v1.
If/when decision-level queries across thousands of sessions get slow, the
migration target is a single SQLite table keyed on (session_id, decision_id)
— the schema here is deliberately compatible with that shape.

Stdlib-only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import scoring as sc
from session import Session, DIMENSIONS
import decision_schema_stdlib as sch


DEFAULT_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# =========================================================================== #
# JSON -> Decision reconstruction
# =========================================================================== #
# decision_schema_stdlib.decision_from_json() deliberately returns a plain dict
# and leaves typed reconstruction to the caller. The scoring engine reads dicts
# fine, but a real data layer should be able to hand back typed Decision objects
# so the rest of the system isn't forced to special-case "loaded" decisions.
# These helpers do that reconstruction faithfully for both domains.

def _dt(v):
    """ISO string -> datetime; pass through anything else (incl. None)."""
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return v
    return v


def _enum(enum_cls, v):
    """String value -> enum member; pass through if already an enum or unknown."""
    if v is None or isinstance(v, enum_cls):
        return v
    try:
        return enum_cls(v)
    except (ValueError, KeyError):
        return v


def _cards(lst):
    return [sch.Card(**c) if isinstance(c, dict) else c for c in (lst or [])]


def _reference_from_dict(r: dict):
    """Rebuild the polymorphic reference_policy from its 'kind' tag."""
    if r is None:
        return None
    kind = r.get("kind")
    if kind == "solver":
        return sch.SolverReference(
            reference_type=_enum(sch.ReferenceType, r.get("reference_type",
                                 sch.ReferenceType.SOLVER_EXACT)),
            best_action=r.get("best_action", ""),
            best_action_ev=r.get("best_action_ev", 0.0),
            taken_action_ev=r.get("taken_action_ev", 0.0),
            ev_loss=r.get("ev_loss", 0.0),
            strategy=r.get("strategy"),
            true_equity=r.get("true_equity"),
        )
    if kind == "plan":
        return sch.PlanReference(
            reference_type=_enum(sch.ReferenceType, r.get("reference_type",
                                 sch.ReferenceType.VALIDATED_PLAN)),
            was_on_plan=r.get("was_on_plan", False),
            setup_id=r.get("setup_id"),
            planned_entry=r.get("planned_entry"),
            planned_stop=r.get("planned_stop"),
            planned_target=r.get("planned_target"),
            setup_expectancy=r.get("setup_expectancy"),
            process_violations=list(r.get("process_violations", []) or []),
        )
    return r  # unknown kind: leave as dict (scoring tolerates it)


def decision_from_dict(d: dict) -> sch.Decision:
    """Reconstruct a typed Decision from its JSON dict form (both domains)."""
    domain = _enum(sch.Domain, d.get("domain"))
    domain_val = getattr(domain, "value", domain)

    # ---- context ---- #
    ctx_d = d.get("context") or {}
    if domain_val == sch.Domain.POKER.value:
        context = sch.PokerContext(
            variant=_enum(sch.PokerVariant, ctx_d.get("variant", sch.PokerVariant.SEVEN_CARD_STUD)),
            street=ctx_d.get("street", ""),
            street_index=ctx_d.get("street_index", 1),
            hero_down_cards=_cards(ctx_d.get("hero_down_cards")),
            hero_up_cards=_cards(ctx_d.get("hero_up_cards")),
            board=_cards(ctx_d.get("board")),
            exposed_cards=_cards(ctx_d.get("exposed_cards")),
            dead_cards=_cards(ctx_d.get("dead_cards")),
            pot_size=ctx_d.get("pot_size", 0.0),
            to_call=ctx_d.get("to_call", 0.0),
            effective_stack=ctx_d.get("effective_stack", 0.0),
            num_active_players=ctx_d.get("num_active_players", 2),
            seat=ctx_d.get("seat", 0),
            antes=ctx_d.get("antes", 0.0),
            bring_in=ctx_d.get("bring_in"),
            legal_actions=[_enum(sch.PokerActionType, a)
                           for a in ctx_d.get("legal_actions", [])],
        )
        action_d = d.get("action_taken") or {}
        action = sch.PokerAction(
            action_type=_enum(sch.PokerActionType, action_d.get("action_type",
                              sch.PokerActionType.FOLD)),
            amount=action_d.get("amount"),
        )
    else:
        inst_d = ctx_d.get("instrument") or {}
        mkt_d = ctx_d.get("market") or {}
        context = sch.TradingContext(
            instrument=sch.Instrument(
                symbol=inst_d.get("symbol", ""),
                asset_class=_enum(sch.AssetClass, inst_d.get("asset_class",
                                  sch.AssetClass.FUTURE)),
                currency=inst_d.get("currency", "USD"),
                multiplier=inst_d.get("multiplier", 1.0),
                tick_size=inst_d.get("tick_size"),
            ),
            decision_type=_enum(sch.TradeDecisionType, ctx_d.get("decision_type",
                                sch.TradeDecisionType.ENTRY)),
            account_equity=ctx_d.get("account_equity", 0.0),
            existing_position=ctx_d.get("existing_position", 0.0),
            market=sch.MarketSnapshot(
                price=mkt_d.get("price", 0.0),
                timestamp=_dt(mkt_d.get("timestamp")) or datetime.utcnow(),
                atr=mkt_d.get("atr"),
            ),
            setup_tag=ctx_d.get("setup_tag"),
        )
        action_d = d.get("action_taken") or {}
        action = sch.TradingAction(
            side=_enum(sch.TradeSide, action_d.get("side", sch.TradeSide.BUY)),
            quantity=action_d.get("quantity", 0.0),
            price=action_d.get("price"),
            order_type=action_d.get("order_type", "market"),
        )

    # ---- sizing ---- #
    sz_d = d.get("sizing") or {}
    sizing = sch.Sizing(
        absolute_size=sz_d.get("absolute_size", 0.0),
        size_unit=sz_d.get("size_unit", ""),
        risk_fraction=sz_d.get("risk_fraction", 0.0),
    )

    # ---- ex-ante estimate ---- #
    est = None
    est_d = d.get("ex_ante_estimate")
    if est_d:
        est = sch.ExAnteEstimate(
            win_probability=est_d.get("win_probability", 0.0),
            target_definition=est_d.get("target_definition", ""),
            confidence=est_d.get("confidence"),
            source=_enum(sch.EstimateSource, est_d.get("source",
                         sch.EstimateSource.USER_STATED)),
        )

    # ---- outcome ---- #
    outcome = None
    oc_d = d.get("outcome")
    if oc_d:
        outcome = sch.Outcome(
            resolved=oc_d.get("resolved", False),
            resolved_at=_dt(oc_d.get("resolved_at")),
            realized_value=oc_d.get("realized_value"),
            realized_value_unit=oc_d.get("realized_value_unit"),
            won=oc_d.get("won"),
            mae=oc_d.get("mae"),
            mfe=oc_d.get("mfe"),
        )

    return sch.Decision(
        decision_id=d.get("decision_id", ""),
        user_id=d.get("user_id", ""),
        session_id=d.get("session_id", ""),
        domain=domain,
        timestamp=_dt(d.get("timestamp")) or datetime.utcnow(),
        sequence_index=d.get("sequence_index", 0),
        context=context,
        action_taken=action,
        sizing=sizing,
        reference_policy=_reference_from_dict(d.get("reference_policy")),
        decision_latency_ms=d.get("decision_latency_ms"),
        ex_ante_estimate=est,
        outcome=outcome,
    )


# =========================================================================== #
# Paths / index
# =========================================================================== #

def _sessions_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "sessions")


def _index_path(base_dir: str) -> str:
    return os.path.join(base_dir, "index.json")


def _ensure_dirs(base_dir: str) -> None:
    os.makedirs(_sessions_dir(base_dir), exist_ok=True)


def _load_index(base_dir: str) -> dict:
    p = _index_path(base_dir)
    if not os.path.exists(p):
        return {}
    with open(p) as fh:
        return json.load(fh)


def _write_index(base_dir: str, index: dict) -> None:
    with open(_index_path(base_dir), "w") as fh:
        json.dump(index, fh, indent=2)


# =========================================================================== #
# Save / load / list
# =========================================================================== #

def save_session(session: Session, base_dir: str = DEFAULT_BASE_DIR) -> str:
    """Write a Session to <base_dir>/sessions/<id>.json and update the index.
    Returns the file path."""
    _ensure_dirs(base_dir)

    decisions_json = []
    for d in session.decisions:
        # Accept dataclass Decisions or dicts; normalize to dict for storage.
        if isinstance(d, dict):
            decisions_json.append(d)
        else:
            decisions_json.append(json.loads(sch.decision_to_json(d)))

    created = session.created_at
    payload = {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "timestamp": created.isoformat() if isinstance(created, datetime) else created,
        "domain": session.domain,
        "decisions": decisions_json,
    }
    path = os.path.join(_sessions_dir(base_dir), f"{session.session_id}.json")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)

    # Update the index with headline scores so listing/filtering is cheap.
    index = _load_index(base_dir)
    index[session.session_id] = {
        "path": os.path.relpath(path, base_dir),
        "user_id": session.user_id,
        "domain": session.domain,
        "timestamp": payload["timestamp"],
        "n_decisions": len(decisions_json),
        "scores": session.summary()["scores"],
    }
    _write_index(base_dir, index)
    return path


def load_session(path: str, reconstruct: bool = True) -> Session:
    """Load a Session from a session JSON file. If reconstruct=True (default),
    decisions are rebuilt as typed Decision objects; otherwise they stay dicts
    (still fully scoreable)."""
    with open(path) as fh:
        data = json.load(fh)
    decisions = data.get("decisions", [])
    if reconstruct:
        decisions = [decision_from_dict(d) for d in decisions]
    return Session(
        session_id=data.get("session_id", ""),
        user_id=data.get("user_id", ""),
        domain=data.get("domain", ""),
        created_at=_dt(data.get("timestamp")),
        decisions=decisions,
    )


def load_session_by_id(session_id: str, base_dir: str = DEFAULT_BASE_DIR,
                       reconstruct: bool = True) -> Session:
    path = os.path.join(_sessions_dir(base_dir), f"{session_id}.json")
    return load_session(path, reconstruct=reconstruct)


def list_sessions(base_dir: str = DEFAULT_BASE_DIR,
                  domain: Optional[str] = None,
                  user_id: Optional[str] = None) -> List[dict]:
    """Return index metadata for stored sessions, optionally filtered by domain
    or user. Reads only index.json — no session files are opened."""
    index = _load_index(base_dir)
    rows = []
    for sid, meta in index.items():
        if domain is not None and meta.get("domain") != _enum(sch.Domain, domain) \
                and meta.get("domain") != getattr(_enum(sch.Domain, domain), "value", domain):
            continue
        if user_id is not None and meta.get("user_id") != user_id:
            continue
        row = dict(meta)
        row["session_id"] = sid
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("timestamp")))
    return rows


# =========================================================================== #
# Querying
# =========================================================================== #

def query_sessions(base_dir: str = DEFAULT_BASE_DIR,
                   predicate: Callable[[dict], bool] = lambda m: True) -> List[dict]:
    """Session-level query against the index only (fast). `predicate` receives
    the index metadata row (including 'scores'). Example:

        # sessions where tilt control dropped below 50
        query_sessions(predicate=lambda m: (m['scores']['tilt_control'] or 100) < 50)
    """
    return [m for m in list_sessions(base_dir) if predicate(m)]


def query_decisions(
    base_dir: str = DEFAULT_BASE_DIR,
    predicate: Callable[["sch.DecisionEvaluation", dict], bool] = lambda e, m: True,
) -> List[Tuple[str, "sch.DecisionEvaluation"]]:
    """Decision-level query. Loads each indexed session, scores it, and returns
    (session_id, DecisionEvaluation) pairs whose evaluation satisfies `predicate`.
    `predicate` receives (evaluation, session_index_meta). Example — the exact
    query the roadmap called out:

        # every decision where sizing discipline was poor (large size residual)
        query_decisions(predicate=lambda e, m:
            e.sizing_deviation is not None and abs(e.sizing_deviation) > 2.0)

    This necessarily opens the session files (decision-level data isn't in the
    index), but it's a single call and only touches indexed sessions.
    """
    out: List[Tuple[str, "sch.DecisionEvaluation"]] = []
    for meta in list_sessions(base_dir):
        sid = meta["session_id"]
        path = os.path.join(base_dir, meta["path"])
        with open(path) as fh:
            data = json.load(fh)
        _, evals = sc.score_session(data.get("decisions", []))
        for e in evals:
            if predicate(e, meta):
                out.append((sid, e))
    return out


# =========================================================================== #
# Demo
# =========================================================================== #
if __name__ == "__main__":
    import tempfile

    base = tempfile.mkdtemp(prefix="dq_store_")
    print(f"[demo store at {base}]")

    # Build a small session, save, reload, query.
    s = Session(session_id="sess-demo", user_id="dara", domain="poker")
    for i in range(6):
        s.add(sch.Decision(
            decision_id=f"d{i}", user_id="dara", session_id="sess-demo",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0), sequence_index=i,
            context=sch.PokerContext(pot_size=100.0),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=50.0, size_unit="chips",
                              risk_fraction=0.03 + 0.02 * (i == 5)),  # last one over-bets
            reference_policy=sch.SolverReference(
                best_action="call", best_action_ev=10.0,
                taken_action_ev=10.0 - i * 3.0, ev_loss=i * 3.0,
                true_equity=0.6),
            decision_latency_ms=8000,
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=0.6,
                                                target_definition="showdown"),
            outcome=sch.Outcome(resolved=True, won=(i % 2 == 0),
                                realized_value=(20 if i % 2 == 0 else -20)),
        ))

    path = save_session(s, base)
    print("saved:", os.path.basename(path))

    reloaded = load_session_by_id("sess-demo", base)
    print("reloaded decisions:", len(reloaded))
    print("reloaded profile policy_adherence:", reloaded.profile().policy_adherence)

    print("\nlist_sessions:")
    for row in list_sessions(base):
        print("  ", row["session_id"], row["domain"], row["scores"])

    print("\nquery_decisions (sizing residual > 1):")
    for sid, e in query_decisions(base,
            predicate=lambda e, m: e.sizing_deviation is not None and abs(e.sizing_deviation) > 1.0):
        print("  ", sid, e.decision_id, "sizing_deviation=", round(e.sizing_deviation, 2))
