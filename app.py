"""
app.py — Web interface  (Phase 4)
=================================

A minimal FastAPI application over the existing engine. It is intentionally
thin: every number it shows comes from scoring.py / session.py / persistence.py
/ scenarios.py — the web layer only presents, it never computes quality itself.

ROUTES
------
  GET  /                         dashboard: stored sessions + their scores
  GET  /session/{id}             one session: profile, per-decision replay, trends
  GET  /play, /play/table, /play/holdem   interactive poker (stud + Hold'em)
  GET  /desk                     Markets Desk: 3 daily news-analogy hands
  POST /api/hand/new|{id}/act|{id}/report|{id}/answer   play + grade a hand
  POST /api/desk/{id}/answer     grade a news-desk decision
  GET  /api/sessions             JSON: session index
  GET  /api/session/{id}         JSON: full profile + per-decision evaluations
  POST /api/score                JSON in (list of Decisions or {decisions:[...]}),
                                 profile out — the headless scoring endpoint
  GET  /healthz                  liveness probe (for deployment)

DEPENDENCIES
------------
This is the ONE module that needs third-party packages (fastapi, uvicorn,
jinja2). The engine itself stays standard-library only. See requirements.txt.

RUN
---
    uvicorn app:app --reload          # dev
    python3 app.py                    # also works (calls uvicorn)
"""

from __future__ import annotations

import json
import os
import random
from typing import List

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import scoring as sc
import persistence as pz
from session import Session, DIMENSIONS
from replay import iter_replay
import stud_game as game
import holdem_game as holdem
import coach as coachmod
import glossary as gloss
import news_desk

# In-memory registry of live hands (fine for a local single-user desktop app).
# Maps hand_id -> {"state": HandState, "report": dict|None, "engine": module}.
HANDS: dict = {}

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DQ_DATA_DIR", os.path.join(BASE, "data"))

app = FastAPI(title="Edge Over Variance", version="1.0")

templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
_static_dir = os.path.join(BASE, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# Human labels for the five dimensions (+ resolution), reused in templates.
DIM_LABELS = {
    "policy_adherence": "Policy adherence",
    "calibration": "Calibration",
    "resolution": "Resolution",
    "sizing_discipline": "Sizing discipline",
    "outcome_independence": "Outcome-independence",
    "tilt_control": "Tilt control",
}

# Map each dimension to its glossary definition key.
DIM_TERM_KEY = {
    "policy_adherence": "policy adherence / EV-loss",
    "calibration": "calibration",
    "resolution": "resolution",
    "sizing_discipline": "sizing discipline",
    "outcome_independence": "outcome-independence",
    "tilt_control": "tilt control",
}


def _band(score):
    """Qualitative band + interpretation hint for a 0-100 dimension score."""
    if score is None:
        return ("na", "Not enough data yet to score this dimension.")
    if score >= 80:
        return ("strong", "A clear strength — keep doing this.")
    if score >= 60:
        return ("solid", "Solid, with room to tighten up.")
    if score >= 40:
        return ("mixed", "Inconsistent — a priority to work on.")
    return ("weak", "A leak — the biggest lever for improvement.")


def _aggregate(sessions):
    """Aggregate decision-quality history across stored sessions."""
    agg = {d: [] for d in DIMENSIONS}
    total_decisions = 0
    for s in sessions:
        total_decisions += s.get("n_decisions", 0)
        for d in DIMENSIONS:
            v = (s.get("scores") or {}).get(d)
            if v is not None:
                agg[d].append(v)
    means = {d: (round(sum(vs) / len(vs), 1) if vs else None) for d, vs in agg.items()}
    scored = {d: m for d, m in means.items() if m is not None}
    strongest = max(scored, key=scored.get) if scored else None
    weakest = min(scored, key=scored.get) if scored else None
    return {
        "means": means,
        "n_sessions": len(sessions),
        "total_decisions": total_decisions,
        "strongest": strongest,
        "weakest": weakest,
    }


# --------------------------------------------------------------------------- #
# HTML pages
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    sessions = pz.list_sessions(DATA_DIR)
    agg = _aggregate(sessions)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "sessions": sessions,
        "dims": DIMENSIONS, "labels": DIM_LABELS,
        "agg": agg, "band": _band,
        "grading_terms": gloss.grading_terms(),
    })


@app.get("/session/{session_id}", response_class=HTMLResponse)
def session_detail(request: Request, session_id: str):
    try:
        s = pz.load_session_by_id(session_id, DATA_DIR)
    except FileNotFoundError:
        return HTMLResponse(f"<p>No session {session_id!r}.</p>", status_code=404)
    profile = s.profile()
    steps = list(iter_replay(s))

    # Per-dimension analysis: score + band + interpretation + definition.
    analysis = []
    for d in DIMENSIONS:
        score = getattr(profile, d)
        band, hint = _band(score)
        analysis.append({
            "key": d, "label": DIM_LABELS[d], "score": score,
            "band": band, "hint": hint,
            "definition": gloss.GRADING_TERMS.get(DIM_TERM_KEY[d], ""),
        })
    scored = [a for a in analysis if a["score"] is not None]
    strongest = max(scored, key=lambda a: a["score"]) if scored else None
    weakest = min(scored, key=lambda a: a["score"]) if scored else None

    # Teaching moments: count good-decision/bad-outcome (and the inverse).
    good_bad = sum(1 for st in steps if "Good decision, bad outcome" in (st.note or ""))
    bad_good = sum(1 for st in steps if "Bad decision, good outcome" in (st.note or ""))

    return templates.TemplateResponse("session.html", {
        "request": request, "s": s, "profile": profile, "steps": steps,
        "dims": DIMENSIONS, "labels": DIM_LABELS,
        "analysis": analysis, "strongest": strongest, "weakest": weakest,
        "good_bad": good_bad, "bad_good": bad_good,
        "grading_terms": gloss.grading_terms(),
    })


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #

@app.get("/api/sessions")
def api_sessions():
    return pz.list_sessions(DATA_DIR)


@app.get("/api/session/{session_id}")
def api_session(session_id: str):
    try:
        s = pz.load_session_by_id(session_id, DATA_DIR)
    except FileNotFoundError:
        return JSONResponse({"error": "not found"}, status_code=404)
    profile, evals = s.score()
    return {
        "profile": profile.__dict__,
        "evaluations": [e.__dict__ for e in evals],
    }


@app.post("/api/score")
async def api_score(request: Request):
    """Score an ad-hoc payload: either a bare list of Decisions or
    {"decisions": [...]}. Returns the DecisionQualityProfile as JSON."""
    body = await request.json()
    if isinstance(body, dict) and "decisions" in body:
        decisions = body["decisions"]
    elif isinstance(body, list):
        decisions = body
    else:
        return JSONResponse(
            {"error": "expected a list of Decisions or {'decisions': [...]}"},
            status_code=400)
    profile, evals = sc.score_session(decisions)
    return {"profile": profile.__dict__,
            "evaluations": [e.__dict__ for e in evals]}


@app.get("/api/history")
def api_history():
    """Time-series for the decision-quality history chart.

    Returns one point per *graded decision* (overall quality = 1 - EV-loss, on a
    0-100 scale) with its timestamp — the "stock-price" series — plus one point
    per *session* carrying that session's six dimension scores, so the chart can
    switch between Overall (per-decision) and any single dimension (per-session).
    Everything here is read straight from stored sessions; the web layer never
    computes quality itself.
    """
    decisions: List[dict] = []
    sess_points: List[dict] = []
    for meta in pz.list_sessions(DATA_DIR):
        sid = meta["session_id"]
        path = os.path.join(DATA_DIR, meta["path"])
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception:
            continue
        decs = data.get("decisions", [])
        try:
            _, evals = sc.score_session(decs)
        except Exception:
            evals = []
        for dec, ev in zip(decs, evals):
            loss = getattr(ev, "ev_loss_normalized", None)
            quality = None if loss is None else round(100.0 * (1.0 - loss), 1)
            decisions.append({
                "t": dec.get("timestamp"),
                "session_id": sid,
                "domain": meta.get("domain"),
                "seq": dec.get("sequence_index"),
                "quality": quality,
            })
        sess_points.append({
            "t": meta.get("timestamp"),
            "session_id": sid,
            "domain": meta.get("domain"),
            "n_decisions": meta.get("n_decisions"),
            "scores": meta.get("scores", {}),
        })
    decisions.sort(key=lambda d: str(d.get("t")))
    sess_points.sort(key=lambda s: str(s.get("t")))
    return {
        "decisions": decisions,
        "sessions": sess_points,
        "dimensions": DIMENSIONS,
        "labels": DIM_LABELS,
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Markets Desk — 3 daily news-analogy hands
# --------------------------------------------------------------------------- #

@app.get("/desk", response_class=HTMLResponse)
def desk_page(request: Request):
    desk = news_desk.load_desk()
    hands_json = json.dumps([h.to_dict() for h in desk["hands"]])
    return templates.TemplateResponse("desk.html", {
        "request": request, "desk_date": desk["date"], "hands": desk["hands"],
        "hands_json": hands_json,
    })


@app.post("/api/desk/{hand_id}/answer")
async def api_desk_answer(hand_id: str, request: Request):
    hand = news_desk.get_hand(hand_id)
    if hand is None:
        return JSONResponse({"error": "unknown hand"}, status_code=404)
    body = await request.json()
    try:
        return hand.score_response((body or {}).get("option", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# --------------------------------------------------------------------------- #
# Interactive play (seven-card stud)
# --------------------------------------------------------------------------- #

@app.get("/play", response_class=HTMLResponse)
def play_page(request: Request):
    coach = coachmod.get_coach()
    return templates.TemplateResponse("play.html", {
        "request": request,
        "coach_source": getattr(coach, "source", "library"),
    })


@app.get("/play/table", response_class=HTMLResponse)
def play_table_page(request: Request):
    coach = coachmod.get_coach()
    return templates.TemplateResponse("play_table.html", {
        "request": request,
        "coach_source": getattr(coach, "source", "library"),
    })


@app.get("/play/holdem", response_class=HTMLResponse)
def play_holdem_page(request: Request):
    coach = coachmod.get_coach()
    return templates.TemplateResponse("play_holdem.html", {
        "request": request,
        "coach_source": getattr(coach, "source", "library"),
    })


@app.post("/api/hand/new")
async def api_hand_new(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    seed = body.get("seed")

    if body.get("game") == "holdem":
        # Hold'em: a random table of 2-8 players => 1-7 opponents.
        engine = holdem
        num_opponents = random.randint(1, holdem.MAX_PLAYERS - 1)
    elif body.get("mode") == "table":
        engine = game
        num_opponents = random.randint(1, game.MAX_OPPONENTS)   # stud table 1-6
    else:
        engine = game
        num_opponents = int(body.get("num_opponents", 1))       # stud heads-up

    st = engine.new_hand(seed=seed, num_opponents=num_opponents)
    HANDS[st.hand_id] = {"state": st, "report": None, "engine": engine}
    return st.public_state()


@app.post("/api/hand/{hand_id}/act")
async def api_hand_act(hand_id: str, request: Request):
    entry = HANDS.get(hand_id)
    if entry is None:
        return JSONResponse({"error": "unknown hand"}, status_code=404)
    body = await request.json()
    action = (body or {}).get("action")
    engine = entry.get("engine", game)
    try:
        st = engine.act(entry["state"], action)
    except (ValueError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    entry["state"] = st
    return st.public_state()


@app.get("/api/hand/{hand_id}/report")
def api_hand_report(hand_id: str):
    entry = HANDS.get(hand_id)
    if entry is None:
        return JSONResponse({"error": "unknown hand"}, status_code=404)
    st = entry["state"]
    if not st.finished:
        return JSONResponse({"error": "hand not finished"}, status_code=400)
    if entry["report"] is None:
        entry["report"] = coachmod.full_report(st.history)
    return entry["report"]


@app.post("/api/hand/{hand_id}/answer")
async def api_hand_answer(hand_id: str, request: Request):
    """Grade the trainee's free-text answer to the markets question."""
    entry = HANDS.get(hand_id)
    if entry is None or entry.get("report") is None:
        return JSONResponse({"error": "no report for hand"}, status_code=400)
    body = await request.json()
    answer = (body or {}).get("answer", "")
    tr = entry["report"].get("translation") or {}
    coach = coachmod.get_coach()
    result = coach.evaluate_answer(tr.get("question", ""),
                                   tr.get("model_answer", ""), answer)
    return result


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", "8000")), reload=False)
