"""Console entry point used to check the engine still works once frozen by PyInstaller.

    pyinstaller --onefile --distpath build/selftest tools/frozen_selftest.py
    build/selftest/frozen_selftest.exe <query.mp3> <library.zip>
"""
import sys

import engine

out = engine.search(sys.argv[1], sys.argv[2])
for r in out.results[:3]:
    print(f"{r.score * 100:5.1f}%  {r.track.filename}  {r.label}")
print("failed:", len(out.failed))
