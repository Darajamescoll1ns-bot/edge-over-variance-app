# Edge Over Variance

A training and analysis platform that scores decisions under uncertainty —
seven-card stud poker and futures trading — on **quality, independent of
outcome**. A good decision that lost still scores well; a lucky punt still
scores badly.

## Play a hand (interactive trainer)

Three play modes (in the top nav):

- **Heads-up** — seven-card stud, one opponent (`/play`).
- **Stud table** — seven-card stud, a random 1–6 opponents per hand (`/play/table`).
- **Hold'em** — Texas Hold'em, a random 2–8 players per hand (`/play/holdem`).
  Hidden hole cards + a shared board; the read is the board, live-player count,
  and pot odds, so its markets analogy is more generic.

In every mode you play a full hand against equity-driven bots, choosing an
action each street. At showdown the platform:

1. **Grades every decision** street-by-street against true Monte Carlo equity,
   showing your equity, the best action, a quality score, and *why*.
2. **Summarises** the hand and picks the single most instructive decision.
3. **Translates that decision into a markets scenario**, asks what *you* would
   do, scores your written answer, and reveals the model answer.

The markets coach uses your own `ANTHROPIC_API_KEY` for a fresh, tailored
analogy per hand if it's set; otherwise it uses a built-in analogy library so
the trainer **always works fully offline**.

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # optional — enables AI-tailored coaching
export DQ_LLM_MODEL=claude-sonnet-4-6    # optional — override the model
```

Engine pieces for this mode: `solver.py` (equity), `stud_game.py` (the game),
`coach.py` (grading + markets translation).

## What it does

Every decision is graded on five dimensions:

1. **Policy adherence / EV-loss** — poker vs. a solver baseline; trading vs. a validated plan
2. **Calibration** — were your stated probabilities honest? (Brier reliability)
3. **Resolution** — were they informative? (Brier resolution)
4. **Sizing discipline** — did bet/position size track your edge?
5. **Outcome-independence** & **Tilt control** — does quality survive losses and stress?

## Architecture

The engine is **standard-library only** — no installs needed to score.

| File | Role |
|------|------|
| `decision_schema_stdlib.py` | The unified `Decision` schema (poker + trading) |
| `scoring.py` | The five-dimension scoring engine |
| `session.py` | Ordered runs of decisions + rolling metrics |
| `persistence.py` | Flat-JSON storage, session index, queries |
| `solver.py` | Monte Carlo equity + 7-card evaluator + external-solver seam |
| `betting.py` | Chip stacks + discrete sizing options (shared by both engines) |
| `stud_game.py` / `holdem_game.py` | Interactive stud + Hold'em engines |
| `coach.py` | Per-street grading, the EV math, and markets translation |
| `news_desk.py` / `daily_desk.json` | Markets Desk: 3 daily news-analogy hands |
| `glossary.py` | Markets + grading term definitions |
| `replay.py` | Step-through replay (the learning loop) |
| `evaluate.py` | CLI: score a JSON session |
| `app.py` + `templates/` + `static/` | FastAPI web interface (the one part needing deps) |
| `seed_demo.py` | Seeds demo sessions for the dashboard |

Run the tests: `python3 test_scoring.py` (and `test_persistence`, `test_solver`,
`test_stud_game`, `test_holdem_game`, `test_coach`, `test_glossary`,
`test_news_desk`, `test_app`). 117 tests total.

**Markets Desk** (`/desk`) shows 3 hands built from the day's real news. A daily
scheduled task regenerates `daily_desk.json` each morning; if it's missing the
app falls back to a built-in default desk so the tab always works offline.

## Run as a Mac desktop app (no terminal needed)

Double-click **`Start Trading Trainer.command`** in Finder.

- The first launch sets up a private environment (~30–60s, one time) and needs
  an internet connection to download the web components.
- Every launch after that starts instantly, runs the app **entirely on your
  Mac**, and opens it in your default browser automatically.
- A small Terminal window stays open while the app runs — close it (or press
  Ctrl-C) to stop the app.

First time only: if macOS blocks the double-click, **right-click → Open** once,
or run `chmod +x "Start Trading Trainer.command"` in Terminal.

Requires Python 3 (`python3`). If it's missing, install from
<https://www.python.org/downloads/> or `brew install python`.

## Run locally (terminal)

```bash
./run.sh
# then open http://localhost:8000
```

Or manually:

```bash
pip install -r requirements.txt
python3 -c "import scenarios; scenarios.dump_library()"
python3 seed_demo.py
uvicorn app:app --reload
```

## Deploy (make it a real website)

The app is containerized and reads `$PORT`, so it runs on any modern host.

**Docker (anywhere):**
```bash
docker build -t dq-trainer .
docker run -p 8000:8000 dq-trainer
```

**Hosted platforms** (each gives a public URL; all read the Dockerfile):
- **Render / Railway / Fly.io** — connect this repo (or push the image), they build
  the Dockerfile and assign a URL. Mount a volume at `/app/data` to persist sessions.
- **Google Cloud Run / AWS App Runner / Azure Container Apps** — `docker push` to
  their registry, deploy the image; scales to zero when idle.

> **Note:** going live needs *your* hosting account (and a paid plan for an
> always-on URL). This repo gets you to a one-command deploy; the account and
> any spend are yours to set up.

## Honest limitations

- **No real stud solver exists** off the shelf. `MonteCarloEquityProvider` gives
  genuine, computed equity and pot-odds-based EVs (an *approximation*, flagged
  `SOLVER_APPROX`). `ExternalSolverProvider` is the wired seam for a real engine
  when one is available — it raises until configured.
- **Brier decomposition** is exact only when forecasts don't share a bin; with
  fixed-width bins there's a small within-bin residual (documented in `scoring.py`).
- **Auth, accounts, and payments** are not built — add them before exposing it
  publicly to multiple users.
```
