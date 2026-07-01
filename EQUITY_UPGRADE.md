# Equity engine upgrade — range-conditioned equity + implied odds

## The problem this fixes
The grader scored decisions against **equity vs a random hand** (`solver.monte_carlo_equity*`).
That overstates marginal hands the moment the opponent gets to act: a bet folds out
the bottom of their range, so the hands that *continue* are stronger than average.
K2o is a coin-flip vs a random hand but a dog vs any calling range — the old engine
couldn't see that, and it ignored implied / reverse-implied odds entirely.

## What changed
**New modules (stdlib only):**
- `ranges.py` — opponent **Range**, **MDF** (minimum-defence-frequency) continue
  ranges, and **conditioned equity** (`equity vs random` → `equity when called`).
  The opponent folds their weakest hands; hero equity is re-measured vs the survivors.
- `rollout.py` — multi-street **simulation** that plays the hand out against the
  narrowing range with a documented betting model. Implied odds, reverse-implied
  odds, fold equity and equity realisation all **emerge** from the sim.

**Modified:** `coach.py` — heads-up decisions grade against the conditioned
range (analytic EV grid, fast) and the **key decision** additionally gets the
implied-odds rollout (the hybrid you picked). **Multiway** decisions grade against
a conditioned Monte-Carlo grid where each opponent independently folds its weakest
hands (MDF), with correct multiway pot growth and fold equity. New `StreetGrade`
fields: `equity_vs_random`, `equity_effective`, `fold_equity`, `mdf`, `implied`,
`conditioned`. The plain-language "math" now teaches the vs-random → when-called
contrast and quantifies implied odds.

**Multiway + stud rollout (follow-up):** `ranges.holdem_multiway_grid` /
`stud_multiway_grid` give a `MultiwayGrid` with `vs_random`, `equity_when_called`
(vs the surviving field), `fold_all_prob`, and `ev_option`. A neat consequence:
in a multiway pot, folds shrink the field even as they strengthen it, so
"equity when called" can be *higher* than vs-random — the model shows that
correctly. `rollout.stud_call_ev` plays 4th→7th streets with the same betting
model, so heads-up stud now gets implied / reverse-implied odds too (a made hand
extracts; a semi-exposed draw is valued lower than in Hold'em, as it should be).

## Validated behaviour
| Spot | Old (vs random) | New (conditioned) |
|---|---|---|
| K2o, half-pot bet | 50% | **43% when called** (MDF 67%) |
| K2o, pot bet | 50% | **38% when called** (MDF 50%) |
| K2o, 2× overbet | 50% | **32% when called** (MDF 33%) |
| AA, pot bet | 85% | **81% when called** (stays strong) |
| Nut flush draw, half-pot | pot-odds EV +60 | **+80** (implied odds +20) |
| K7 on K-high vs value range | "+36" looks fine | **−62** (reverse implied odds) |

## Design choices (per your answers)
- **GTO continue range** via MDF: defender keeps the top `pot/(pot+bet)` of range.
- **Hybrid**: analytic conditioned equity for the live EV grid; rollout simulation
  for the spotlight decision's implied odds.
- **Both games**: Hold'em fully conditioned + rollout; stud conditioned equity
  (`stud_conditioned_equity`) over hidden hole cards. (Stud rollout reuses the
  same skeleton and can be added next.)

## Honest limits
- The rollout's future-street betting is a **documented heuristic** (value-bet ~2/3
  pot, semi-bluff strong draws, continue by pot odds), not a solved strategy — so
  EVs are an informed approximation, consistent with the engine's `SOLVER_APPROX`
  labelling. Swap a stronger policy behind the same interface to sharpen it.
- Multiway is now conditioned (per-opponent MDF grid), but the **implied-odds
  rollout** is heads-up only — multiway pots use the single-decision conditioned
  EV (no multi-street side-pot simulation, which gets complex fast).
- The multiway continue read uses a board-aware **strength heuristic** for the
  fold/continue cutoff (fast, no per-hand rollout); the heads-up path ranks by
  exact equity vs hero.
- Iteration/combo budgets are tuned for a few-seconds grade; raise them in
  `coach.py` constants (`COND_ITERS_*`, `*_MAX_COMBOS`, `ROLLOUT_TRIALS`,
  `MULTIWAY_ITERS`) for sharper numbers.

## Tests
`python3 test_ranges.py` (12) · `python3 test_rollout.py` (7) · `python3 test_coach.py` (10)
— all pass, plus the existing scoring/persistence/solver/holdem/stud/glossary/news
suites (97) = **116 stdlib tests**. The web-route suite (`test_app.py`) needs
FastAPI installed.

## Pushing to GitHub
Changed files: `ranges.py`, `rollout.py`, `coach.py`, `test_ranges.py`,
`test_rollout.py` (mirrored into `deploy-upload/`). From your local clone of
`darajamescoll1ns-bot/PT-Training-Platform`:

```bash
cp ranges.py rollout.py coach.py test_ranges.py test_rollout.py /path/to/clone/
cd /path/to/clone
git add ranges.py rollout.py coach.py test_ranges.py test_rollout.py
git commit -m "Range-conditioned equity (MDF) + implied-odds rollout"
git push origin main
```
(The GitHub Pages marketing page is a separate, encrypted static site and is
unaffected by this change.)
