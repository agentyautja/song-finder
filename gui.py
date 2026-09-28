"""Song Finder desktop window."""
import os
import queue
import sys
import threading
import time
from tkinter import filedialog, messagebox

import customtkinter as ctk
import miniaudio

import engine
from player import Player

APP_NAME = "Song Finder"
MAX_ROWS = 30

ACCENT = "#7c5cff"
ACCENT_HOVER = "#6846f0"
CARD = ("#f1f1f5", "#1f2027")
ROW = ("#e6e6ee", "#262833")
DIM = ("#5f6270", "#9a9db0")
LABEL_COLORS = {
    "Exact match": "#23a15c",
    "Likely same song": "#2f7ed8",
    "Similar": "#c28419",
    "Somewhat similar": "#6b6f80",
    "Not similar": "#4a4d5c",
}


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1000x740")
        self.minsize(780, 560)
        try:
            self.iconbitmap(resource_path(os.path.join("assets", "icon.ico")))
        except Exception:
            pass

        self.library_path = None
        self.query_path = None
        self.track_count = 0
        self.events = queue.Queue()
        self.cancel_event = None
        self.running = False
        self.last_dir = os.path.expanduser("~")
        self.player = Player()
        self.play_buttons = {}
        self.skipped = []

        self.font_title = ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        self.font_h = ctk.CTkFont(family="Segoe UI", size=15, weight="bold")
        self.font = ctk.CTkFont(family="Segoe UI", size=13)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=12)
        self.font_pct = ctk.CTkFont(family="Segoe UI", size=20, weight="bold")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self._build_header()
        self._build_inputs()
        self._build_actions()
        self._build_results()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)

    # ------------------------------------------------------------------ layout
    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        ctk.CTkLabel(header, text=APP_NAME, font=self.font_title).pack(anchor="w")
        ctk.CTkLabel(header, font=self.font, text_color=DIM,
                     text="Find which songs in your collection match the song you're looking for."
                     ).pack(anchor="w")

    def _input_row(self, parent, row, number, title, empty_text, buttons):
        badge = ctk.CTkLabel(parent, text=str(number), width=30, height=30, corner_radius=15,
                             fg_color=ACCENT, text_color="white", font=self.font_h)
        badge.grid(row=row, column=0, padx=(16, 12), pady=14, sticky="n")
        text = ctk.CTkFrame(parent, fg_color="transparent")
        text.grid(row=row, column=1, sticky="ew", pady=12)
        ctk.CTkLabel(text, text=title, font=self.font_h).pack(anchor="w")
        info = ctk.CTkLabel(text, text=empty_text, font=self.font_small, text_color=DIM,
                            anchor="w", justify="left")
        info.pack(anchor="w", fill="x")
        btns = ctk.CTkFrame(parent, fg_color="transparent")
        btns.grid(row=row, column=2, padx=16, pady=12, sticky="e")
        made = []
        for label, command, width in buttons:
            b = ctk.CTkButton(btns, text=label, command=command, width=width, height=34,
                              font=self.font, fg_color=ROW, text_color=("#1b1b22", "#e8e8f0"),
                              hover_color=("#d6d6e2", "#323545"))
            b.pack(side="left", padx=(8, 0))
            made.append(b)
        return info, made

    def _build_inputs(self):
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        card.grid(row=1, column=0, sticky="ew", padx=24, pady=8)
        card.grid_columnconfigure(1, weight=1)
        self.library_info, _ = self._input_row(
            card, 0, 1, "Your song collection",
            "Pick a .zip full of MP3s (or a folder).",
            [("Choose .zip…", self.choose_zip, 120), ("Choose folder…", self.choose_folder, 130)])
        ctk.CTkFrame(card, height=1, fg_color=("#dcdce6", "#2c2e3a")).grid(
            row=1, column=0, columnspan=3, sticky="ew", padx=16)
        self.query_info, (_, self.query_play) = self._input_row(
            card, 2, 2, "Song to find",
            "Pick the MP3 you want to find. A short clip or recording works too.",
            [("Choose song…", self.choose_query, 130), ("▶  Listen", self.toggle_query_play, 100)])
        self.query_play.configure(state="disabled")
        self.play_buttons["query"] = (self.query_play, "▶  Listen")

    def _build_actions(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(8, 4))
        bar.grid_columnconfigure(1, weight=1)
        self.find_btn = ctk.CTkButton(bar, text="Find matches", command=self.start_search,
                                      width=180, height=44, corner_radius=10, font=self.font_h,
                                      fg_color=ACCENT, hover_color=ACCENT_HOVER, state="disabled")
        self.find_btn.grid(row=0, column=0, rowspan=2, sticky="w")
        self.progress = ctk.CTkProgressBar(bar, height=10, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(18, 0), pady=(6, 2))
        self.progress.grid_remove()
        self.status = ctk.CTkLabel(bar, text="Choose your collection and a song to get started.",
                                   font=self.font_small, text_color=DIM, anchor="w")
        self.status.grid(row=1, column=1, sticky="ew", padx=(18, 0))
        self.skipped_btn = ctk.CTkButton(bar, text="", width=10, height=26, font=self.font_small,
                                         fg_color="transparent", text_color=DIM,
                                         hover_color=("#e0e0ea", "#2a2c38"),
                                         command=self.show_skipped)

    def _build_results(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=3, column=0, sticky="ew", padx=24, pady=(14, 4))
        ctk.CTkLabel(head, text="Best matches", font=self.font_h).pack(side="left")
        self.results_count = ctk.CTkLabel(head, text="", font=self.font_small, text_color=DIM)
        self.results_count.pack(side="left", padx=10)

        self.results = ctk.CTkScrollableFrame(self, fg_color=CARD, corner_radius=14)
        self.results.grid(row=4, column=0, sticky="nsew", padx=24, pady=(0, 20))
        self.results.grid_columnconfigure(0, weight=1)
        self._show_placeholder("Your matches will show up here.")

    def _show_placeholder(self, text):
        for w in self.results.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.results, text=text, font=self.font, text_color=DIM).grid(
            row=0, column=0, pady=60)

    # ------------------------------------------------------------------ inputs
    def choose_zip(self):
        path = filedialog.askopenfilename(title="Choose a zip with your songs",
                                          initialdir=self.last_dir,
                                          filetypes=[("Zip files", "*.zip"), ("All files", "*.*")])
        if path:
            self._set_library(path)

    def choose_folder(self):
        path = filedialog.askdirectory(title="Choose a folder with your songs",
                                       initialdir=self.last_dir)
        if path:
            self._set_library(path)

    def _set_library(self, path):
        self.last_dir = os.path.dirname(path)
        try:
            tracks = engine.list_tracks(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Couldn't open that collection:\n\n{exc}")
            return
        if not tracks:
            messagebox.showwarning(APP_NAME, "No MP3 (or WAV / FLAC / OGG) files were found in there.")
            return
        self.library_path = path
        self.track_count = len(tracks)
        songs = "song" if len(tracks) == 1 else "songs"
        self.library_info.configure(text=f"{os.path.basename(path)}  ·  {len(tracks)} {songs}",
                                    text_color=("#1b1b22", "#e8e8f0"))
        self._update_find_button()

    def choose_query(self):
        path = filedialog.askopenfilename(
            title="Choose the song you want to find", initialdir=self.last_dir,
            filetypes=[("Audio files", "*.mp3 *.wav *.flac *.ogg"), ("All files", "*.*")])
        if not path:
            return
        self.last_dir = os.path.dirname(path)
        try:
            duration = miniaudio.get_file_info(path).duration
        except Exception:
            messagebox.showerror(APP_NAME, "That file couldn't be read as audio.")
            return
        self.query_path = path
        self.query_info.configure(text=f"{os.path.basename(path)}  ·  {fmt_time(duration)}",
                                  text_color=("#1b1b22", "#e8e8f0"))
        self.query_play.configure(state="normal")
        self._update_find_button()

    def _update_find_button(self):
        ready = self.library_path and self.query_path and not self.running
        if not self.running:
            self.find_btn.configure(state="normal" if ready else "disabled",
                                    fg_color=ACCENT if ready else ("#cfc7f7", "#342f55"))
        if ready and not self.running:
            self.status.configure(text="Ready. Press “Find matches”.")

    # ------------------------------------------------------------------ search
    def start_search(self):
        if self.running:
            self.cancel_event.set()
            self.find_btn.configure(state="disabled", text="Stopping…")
            return
        self.running = True
        self.cancel_event = threading.Event()
        self.find_btn.configure(text="Cancel", fg_color=("#b8b8c8", "#3a3d4d"),
                                hover_color=("#a8a8ba", "#474a5c"))
        self.progress.set(0)
        self.progress.grid()
        self.skipped_btn.grid_remove()
        self.status.configure(text="Listening to your song…")
        self._show_placeholder("Comparing songs…")
        self.started = time.perf_counter()
        threading.Thread(target=self._search_worker,
                         args=(self.query_path, self.library_path, self.cancel_event),
                         daemon=True).start()

    def _search_worker(self, query, library, cancel):
        try:
            outcome = engine.search(
                query, library, cancel=cancel,
                progress=lambda done, total, name: self.events.put(("progress", done, total, name)))
            self.events.put(("done", outcome))
        except Exception as exc:
            self.events.put(("error", str(exc) or type(exc).__name__))

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, done, total, name = event
                    self.progress.set(done / total)
                    self.status.configure(text=f"Comparing {done} / {total}  ·  {name}")
                elif kind == "done":
                    self._search_finished(event[1])
                elif kind == "error":
                    self._search_ended()
                    self.status.configure(text="Something went wrong.")
                    self._show_placeholder("No results.")
                    messagebox.showerror(APP_NAME, f"The search failed:\n\n{event[1]}")
                elif kind == "play_error":
                    self._reset_play_buttons()
                    messagebox.showerror(APP_NAME, f"Couldn't play that file:\n\n{event[1]}")
        except queue.Empty:
            pass
        self._sync_play_buttons()
        self.after(80, self._poll)

    def _search_ended(self):
        self.running = False
        self.progress.grid_remove()
        self.find_btn.configure(text="Find matches", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self._update_find_button()

    def _search_finished(self, outcome):
        self._search_ended()
        if outcome.cancelled:
            self.status.configure(text="Search cancelled.")
            self._show_placeholder("Search cancelled.")
            return
        took = time.perf_counter() - self.started
        n = len(outcome.results)
        self.status.configure(text=f"Compared {n} song{'s' if n != 1 else ''} in {took:.1f} s.")
        self.skipped = outcome.failed
        if outcome.failed:
            self.skipped_btn.configure(text=f"{len(outcome.failed)} file(s) couldn't be read")
            self.skipped_btn.grid(row=1, column=2, padx=(8, 0))
        self._render_results(outcome.results)

    def show_skipped(self):
        lines = [f"• {t.name}  ({err})" for t, err in self.skipped[:40]]
        if len(self.skipped) > 40:
            lines.append(f"…and {len(self.skipped) - 40} more")
        messagebox.showinfo(APP_NAME, "These files were skipped:\n\n" + "\n".join(lines))

    # ----------------------------------------------------------------- results
    def _render_results(self, results):
        self._stop_playback()
        for w in self.results.winfo_children():
            w.destroy()
        self.play_buttons = {"query": self.play_buttons["query"]}
        if not results:
            self._show_placeholder("No songs could be compared.")
            self.results_count.configure(text="")
            return
        shown = results[:MAX_ROWS]
        self.results_count.configure(
            text=f"top {len(shown)} of {len(results)}" if len(results) > len(shown) else "")
        best = results[0]
        if best.score < 0.45:
            ctk.CTkLabel(self.results, font=self.font_small, text_color=DIM, wraplength=700,
                         text="No strong match found. The song may not be in this collection. "
                              "These are the closest ones.").grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 2))
        for i, r in enumerate(shown):
            self._result_row(i + 1, r)

    def _result_row(self, rank, r):
        row = ctk.CTkFrame(self.results, fg_color=ROW, corner_radius=10)
        row.grid(row=rank, column=0, sticky="ew", padx=6, pady=4)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=f"#{rank}", width=40, font=self.font_h, text_color=DIM).grid(
            row=0, column=0, rowspan=2, padx=(10, 4), pady=10)

        folder = os.path.dirname(r.track.name.replace("\\", "/"))
        ctk.CTkLabel(row, text=r.track.filename, font=self.font_h, anchor="w").grid(
            row=0, column=1, sticky="ew", pady=(10, 0))
        details = [fmt_time(r.duration)]
        if r.start >= 1:
            details.append(f"match starts at {fmt_time(r.start)}")
        details.append(f"melody {r.melody * 100:.0f}%  ·  sound {r.timbre * 100:.0f}%")
        if folder:
            details.append(folder)
        ctk.CTkLabel(row, text="  ·  ".join(details), font=self.font_small, text_color=DIM,
                     anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(row, text=r.label, font=self.font_small, text_color="white", corner_radius=8,
                     fg_color=LABEL_COLORS.get(r.label, "#4a4d5c"), height=26).grid(
            row=0, column=2, rowspan=2, padx=10)

        score = ctk.CTkFrame(row, fg_color="transparent")
        score.grid(row=0, column=3, rowspan=2, padx=6)
        ctk.CTkLabel(score, text=f"{r.score * 100:.0f}%", font=self.font_pct, width=70).pack()
        bar = ctk.CTkProgressBar(score, width=70, height=6, progress_color=LABEL_COLORS.get(r.label))
        bar.set(r.score)
        bar.pack(pady=(0, 4))

        key = r.track
        style = (dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white") if rank == 1
                 else dict(fg_color=CARD, hover_color=("#d6d6e2", "#323545"),
                           text_color=("#5b3ff0", "#b3a2ff")))
        play = ctk.CTkButton(row, text="▶  Play", width=86, height=32, font=self.font, **style,
                             command=lambda: self.toggle_play(key, r.track.read_bytes, r.start))
        play.grid(row=0, column=4, rowspan=2, padx=4)
        self.play_buttons[key] = (play, "▶  Play")
        ctk.CTkButton(row, text="Save…", width=70, height=32, font=self.font,
                      fg_color=CARD, text_color=("#1b1b22", "#e8e8f0"),
                      hover_color=("#d6d6e2", "#323545"),
                      command=lambda: self.save_track(r.track)).grid(
            row=0, column=5, rowspan=2, padx=(4, 12))

    def save_track(self, track):
        path = filedialog.asksaveasfilename(title="Save song", initialdir=self.last_dir,
                                            initialfile=track.filename,
                                            defaultextension=os.path.splitext(track.filename)[1])
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(track.read_bytes())
            self.last_dir = os.path.dirname(path)
            self.status.configure(text=f"Saved {os.path.basename(path)}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Couldn't save the file:\n\n{exc}")

    # ---------------------------------------------------------------- playback
    def toggle_query_play(self):
        path = self.query_path

        def read():
            with open(path, "rb") as f:
                return f.read()

        self.toggle_play("query", read, 0.0)

    def toggle_play(self, key, read_bytes, start):
        if self.player.key == key:
            self._stop_playback()
            return
        self._stop_playback()
        button, _ = self.play_buttons[key]
        button.configure(text="■  Stop")
        self.player.key = key  # claim it right away so the button stays in "Stop"

        def worker():
            try:
                self.player.play(read_bytes(), max(0.0, start - 1.0), key)
            except Exception as exc:
                self.player.key = None
                self.events.put(("play_error", str(exc) or type(exc).__name__))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_playback(self):
        self.player.stop()
        self._reset_play_buttons()

    def _reset_play_buttons(self):
        for button, text in self.play_buttons.values():
            if button.winfo_exists():
                button.configure(text=text)

    def _sync_play_buttons(self):
        if self.player.finished:
            self._stop_playback()  # reached the end of the song

    def _on_close(self):
        if self.cancel_event:
            self.cancel_event.set()
        self.player.stop()
        self.destroy()


def run():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SongFinder.App")
        except Exception:
            pass
    ctk.set_appearance_mode("dark")
    App().mainloop()
