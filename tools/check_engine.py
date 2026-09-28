"""Runs the engine on the synthetic test data and prints the top matches.

    python tools/make_test_data.py   (once)
    python tools/check_engine.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import engine  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "test_data")
EXPECTED = {"q_clip_noisy.mp3": "song_05", "q_full.mp3": "song_20",
            "q_cover.mp3": "song_12", "q_unknown.mp3": None}

ok = True
for query, answer in EXPECTED.items():
    t0 = time.perf_counter()
    out = engine.search(os.path.join(DATA, query), os.path.join(DATA, "library.zip"))
    took = time.perf_counter() - t0
    print(f"\n{query}  (expected {answer})  {took:.1f}s  failed={len(out.failed)}")
    for r in out.results[:4]:
        print(f"  {r.score * 100:5.1f}%  {r.track.filename:14s} fp={r.fingerprint:.2f} "
              f"melody={r.melody:.2f} timbre={r.timbre:.2f} start={r.start:5.1f}s  {r.label}")
    top = out.results[0]
    if answer is None:
        passed = top.score < 0.5
    else:
        passed = top.track.filename.startswith(answer) and top.score >= 0.6
    ok &= passed
    print("  PASS" if passed else "  FAIL")

sys.exit(0 if ok else 1)
