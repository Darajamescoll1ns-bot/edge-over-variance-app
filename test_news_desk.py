"""
test_news_desk.py — Validation for the Markets Desk
===================================================

Run with:  python3 test_news_desk.py
"""

from __future__ import annotations

import os
import tempfile
import traceback

import news_desk as nd


def test_default_desk_has_three_hands():
    desk = nd.build_default_desk()
    assert len(desk) == 3
    cats = {h.category for h in desk}
    assert len(cats) >= 2                       # a spread of topics


def test_every_hand_wellformed():
    for h in nd.build_default_desk():
        assert h.headline and h.summary and h.analogy and h.prompt
        assert h.source_url.startswith("http")
        keys = [o.key for o in h.options]
        assert len(keys) == len(set(keys)), f"{h.hand_id} dup option keys"
        assert h.best_key in keys
        assert all(0 <= o.quality <= 100 for o in h.options)


def test_best_option_is_highest_quality():
    for h in nd.build_default_desk():
        best = max(h.options, key=lambda o: o.quality)
        assert h.best_key == best.key, h.hand_id


def test_score_response_correct_and_terms():
    h = nd.build_default_desk()[0]
    fb = h.score_response(h.best_key)
    assert fb["correct"] is True
    assert fb["quality"] >= 70
    assert fb["terms"] and all("definition" in t for t in fb["terms"])
    # a clearly poor option scores low
    worst = min(h.options, key=lambda o: o.quality)
    assert h.score_response(worst.key)["quality"] < 50


def test_unknown_option_raises():
    h = nd.build_default_desk()[0]
    try:
        h.score_response("nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "desk.json")
        nd.save_desk(nd.build_default_desk(), "2026-06-22", path)
        loaded = nd.load_desk(path)
        assert loaded["date"] == "2026-06-22"
        assert len(loaded["hands"]) == 3
        # scoring survives the round-trip
        h = loaded["hands"][0]
        assert h.score_response(h.best_key)["correct"] is True


def test_load_missing_falls_back_to_default():
    loaded = nd.load_desk("/nonexistent/path/desk.json")
    assert len(loaded["hands"]) == 3


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
