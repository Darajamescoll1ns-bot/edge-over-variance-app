"""
test_app.py — Smoke tests for the web layer (Phase 4)
=====================================================

Uses FastAPI's TestClient (no live server needed). Confirms every route boots,
renders, and that the JSON API scores correctly. Requires the web deps from
requirements.txt; if FastAPI isn't installed the suite skips cleanly.

Run with:  python3 test_app.py
"""

from __future__ import annotations

import json
import os
import tempfile
import traceback


def _build_client(tmp_data):
    os.environ["DQ_DATA_DIR"] = tmp_data
    # Seed one of each domain so the dashboard/session routes have data.
    import seed_demo
    seed_demo.DATA_DIR = tmp_data
    seed_demo.main()

    import importlib
    import app as app_module
    importlib.reload(app_module)        # pick up DQ_DATA_DIR
    from fastapi.testclient import TestClient
    return TestClient(app_module.app), app_module


def test_all():
    from fastapi.testclient import TestClient  # noqa: F401  (import-guarded by runner)

    with tempfile.TemporaryDirectory() as tmp:
        client, _ = _build_client(tmp)

        # Dashboard
        r = client.get("/")
        assert r.status_code == 200 and "Sessions" in r.text

        # Session detail (seeded)
        r = client.get("/session/stud-tilt-demo")
        assert r.status_code == 200
        assert "Replay" in r.text and "Tilt control" in r.text

        # Missing session -> 404
        assert client.get("/session/does-not-exist").status_code == 404

        # JSON API: sessions
        r = client.get("/api/sessions")
        assert r.status_code == 200 and any(s["session_id"] == "stud-tilt-demo"
                                            for s in r.json())

        # JSON API: session detail
        r = client.get("/api/session/trade-day-demo")
        assert r.status_code == 200
        assert "profile" in r.json() and "evaluations" in r.json()

        # JSON API: ad-hoc score
        payload = {"decisions": _two_poker_decisions()}
        r = client.post("/api/score", json=payload)
        assert r.status_code == 200
        prof = r.json()["profile"]
        assert prof["sample_size"] == 2
        assert prof["policy_adherence"] is not None

        # Bad payload -> 400
        assert client.post("/api/score", json={"nope": 1}).status_code == 400

        # Health
        assert client.get("/healthz").json()["status"] == "ok"


def _two_poker_decisions():
    import decision_schema_stdlib as sch
    out = []
    for i in range(2):
        d = sch.Decision(
            decision_id=f"a{i}", user_id="u", session_id="adhoc",
            domain=sch.Domain.POKER,
            timestamp=sch.datetime(2026, 6, 17, 14, i, 0), sequence_index=i,
            context=sch.PokerContext(pot_size=100.0),
            action_taken=sch.PokerAction(action_type=sch.PokerActionType.CALL),
            sizing=sch.Sizing(absolute_size=50.0, size_unit="chips", risk_fraction=0.05),
            reference_policy=sch.SolverReference(best_action="call", best_action_ev=10.0,
                                                 taken_action_ev=10.0, ev_loss=0.0, true_equity=0.6),
            ex_ante_estimate=sch.ExAnteEstimate(win_probability=0.6, target_definition="sd"),
            outcome=sch.Outcome(resolved=True, won=(i % 2 == 0), realized_value=10),
        )
        out.append(json.loads(sch.decision_to_json(d)))
    return out


# --------------------------------------------------------------------------- #
# Runner (skips cleanly if web deps are missing)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
    except Exception as e:
        print(f"SKIP  web deps not installed ({e}); `pip install -r requirements.txt`")
        sys.exit(0)

    try:
        test_all()
        print("PASS  test_all (all web routes)")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL  test_all: {e}")
        traceback.print_exc()
        sys.exit(1)
