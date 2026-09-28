"""Builds dist/SongFinder.exe (a single file you can share).

    python build.py
"""
import os

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

PyInstaller.__main__.run([
    "main.py",
    "--name=SongFinder",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--icon=assets/icon.ico",
    f"--add-data=assets/icon.ico{os.pathsep}assets",
    "--collect-data=customtkinter",
    # miniaudio loads its compiled cffi backend in a way PyInstaller can't see
    "--hidden-import=_cffi_backend",
    # Only used by the dev tools, not by the app
    "--exclude-module=PIL",
    "--exclude-module=lameenc",
])
print("\nBuilt:", os.path.join(HERE, "dist", "SongFinder.exe"))
