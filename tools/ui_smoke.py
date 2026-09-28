"""Drives the app on the synthetic test data and saves screenshots to test_data/.

    python tools/ui_smoke.py [dark|light]
"""
import os
import sys

PROJ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJ)

import customtkinter as ctk  # noqa: E402
from PIL import ImageGrab  # noqa: E402

import gui  # noqa: E402

OUT = os.path.join(PROJ, "test_data")
mode = sys.argv[1] if len(sys.argv) > 1 else "dark"
ctk.set_appearance_mode(mode)
app = gui.App()
app.geometry("1000x740+40+40")


def shot(name):
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    ImageGrab.grab((x, y, x + w, y + h), all_screens=True).save(os.path.join(OUT, f"{mode}_{name}"))


def step1():
    shot("ui_empty.png")
    app._set_library(os.path.join(OUT, "library.zip"))
    app.query_path = os.path.join(OUT, "q_clip_noisy.mp3")
    app.query_info.configure(text="q_clip_noisy.mp3  ·  0:12")
    app.query_play.configure(state="normal")
    app._update_find_button()
    app.start_search()
    app.after(700, lambda: shot("ui_running.png"))
    app.after(200, wait_done)


def wait_done():
    if app.running:
        app.after(200, wait_done)
        return
    app.after(500, lambda: (shot("ui_results.png"), test_play()))


def test_play():
    first = [k for k in app.play_buttons if k != "query"][0]
    btn, _ = app.play_buttons[first]
    btn.invoke()

    def check():
        print("playing:", app.player.key is not None, "pos", round(app.player.position, 1))
        shot("ui_playing.png")
        btn.invoke()
        print("stopped:", app.player.key is None)
        app.after(300, app.destroy)

    app.after(1500, check)


app.after(800, step1)
app.mainloop()
print("ok")
