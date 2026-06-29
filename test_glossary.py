"""
test_glossary.py — Validation for the glossary + educational coaching
=====================================================================

Run with:  python3 test_glossary.py
"""

from __future__ import annotations

import traceback

import glossary as gloss
import coach as cm


def test_glossaries_nonempty_and_stringy():
    assert len(gloss.MARKET_TERMS) >= 15
    assert len(gloss.GRADING_TERMS) >= 6
    for d in (gloss.MARKET_TERMS, gloss.GRADING_TERMS):
        for k, v in d.items():
            assert isinstance(k, str) and isinstance(v, str) and len(v) > 20


def test_find_in_text_matches_known_terms():
    hits = gloss.find_in_text("Honor your stop-loss; the thesis is broken and "
                              "averaging down ignores pot odds.")
    for expected in ("stop-loss", "thesis", "averaging down", "pot odds"):
        assert expected in hits, (expected, hits)


def test_find_in_text_ignores_absent_terms():
    hits = gloss.find_in_text("A neutral sentence with no jargon at all.")
    assert hits == []


def test_lookup_resolves_and_skips_unknown():
    out = gloss.lookup(["edge", "not-a-real-term", "drawdown"])
    names = [o["term"] for o in out]
    assert names == ["edge", "drawdown"]
    assert all("definition" in o for o in out)


def test_grading_terms_cover_dimensions():
    keys = set(gloss.GRADING_TERMS)
    for needed in ("calibration", "resolution", "sizing discipline",
                   "outcome-independence", "tilt control",
                   "policy adherence / EV-loss"):
        assert needed in keys, needed


# --- coaching now carries teaching + terms --- #

def _grade_for(archetype):
    return cm.StreetGrade(street=5, street_name="5th", action="call", options=["call"],
                          equity=0.5, option_evs={"call": 0.0}, best_action="call",
                          ev_loss_normalized=0.0, adherence=100.0,
                          archetype=archetype, why="")


def test_translation_includes_teaching_and_terms():
    lib = cm.LibraryMarketsCoach()
    for arche in cm.ARCHETYPE_MARKET:
        g = _grade_for(arche)
        out = lib.translate([g], g)
        assert out["teaching"] and len(out["teaching"]) > 60, arche
        assert out["terms"] and all("definition" in t for t in out["terms"]), arche


def test_every_archetype_has_terms_defined_in_glossary():
    for arche, entry in cm.ARCHETYPE_MARKET.items():
        for key in entry.get("terms", []):
            assert key in gloss.MARKET_TERMS, (arche, key)


# --------------------------------------------------------------------------- #
def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}"); passed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}"); traceback.print_exc(); failed += 1
    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
