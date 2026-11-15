"""
PulseBox Splash Screen
Embedded inside the main window content area.
Image scales to FIT within the available space (no cropping),
centred with dark background. Progress bar sits below the image.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

# Nuitka --onefile: assets are embedded; also resolve from binary's directory
# for the fallback copy kept alongside the binary.
def _asset(name: str) -> str:
    """Resolve an asset path, handling both source and compiled modes."""
    if getattr(sys, "frozen", False):
        # Try Nuitka's data dir first (embedded via --include-data-files)
        data_dir = os.path.join(os.path.dirname(sys.argv[0]), "assets")
    else:
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets"
        )
    return os.path.join(data_dir, name)

SPLASH_IMAGE = _asset("splash.png")

BG_SPLASH = "#0a0205"
PINK_NEON = "#ff2d78"
CYAN_NEON = "#00d4ff"

BAR_AREA_H = 70


class SplashScreen(tk.Frame):
    """Embedded splash — fills content area between nav bar and player bar."""

    def __init__(self, parent: tk.Widget, **kwargs):
        super().__init__(parent, bg=BG_SPLASH, **kwargs)
        self._photo        = None
        self._progress_var = tk.DoubleVar(value=0)
        self._status_var   = tk.StringVar(value="Starting up…")
        self._closed       = False
        self._build()
        self._animate_progress()

    def _build(self):
        # Progress strip packed FIRST at bottom — never shifts
        bar_frame = tk.Frame(self, bg=BG_SPLASH, height=BAR_AREA_H)
        bar_frame.pack(fill="x", side="bottom")
        bar_frame.pack_propagate(False)

        self._status_lbl = tk.Label(
            bar_frame,
            textvariable=self._status_var,
            font=("Helvetica", 11, "bold"),
            fg=PINK_NEON, bg=BG_SPLASH,
            width=60, anchor="center")
        self._status_lbl.pack(pady=(6, 2))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Splash.Horizontal.TProgressbar",
            troughcolor="#1a0010",
            background=PINK_NEON,
            darkcolor=PINK_NEON,
            lightcolor="#ff6ba0",
            bordercolor=BG_SPLASH,
            thickness=14,
        )
        self._pbar = ttk.Progressbar(
            bar_frame,
            variable=self._progress_var,
            maximum=100,
            style="Splash.Horizontal.TProgressbar",
        )
        self._pbar.pack(fill="x", padx=40)

        tk.Label(bar_frame,
                 text="Stream. Play. Enjoy.",
                 font=("Helvetica", 8),
                 fg=CYAN_NEON, bg=BG_SPLASH).pack(pady=(2, 0))

        # Image canvas fills remaining space
        self._canvas = tk.Canvas(self, bg=BG_SPLASH, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        w, h = event.width, event.height
        if w < 10 or h < 10:
            return
        self._canvas.delete("img")
        self._draw_image(w, h)

    def _draw_image(self, w: int, h: int):
        try:
            from PIL import Image, ImageTk
            img   = Image.open(SPLASH_IMAGE)
            img_w, img_h = img.size
            scale = min(w / img_w, h / img_h)
            new_w = max(1, int(img_w * scale))
            new_h = max(1, int(img_h * scale))
            img   = img.resize((new_w, new_h), Image.LANCZOS)
            self._photo = ImageTk.PhotoImage(img)
            self._canvas.create_image(
                w // 2, h // 2,
                image=self._photo, anchor="center", tags="img")
        except ImportError:
            self._draw_text_fallback(w, h)
        except FileNotFoundError:
            self._draw_text_fallback(w, h)
        except Exception as e:
            print(f"[Splash] {e}")
            self._draw_text_fallback(w, h)

    def _draw_text_fallback(self, w: int, h: int):
        cx, cy = w // 2, h // 2
        self._canvas.create_text(
            cx - 50, cy - 30, text="Pulse",
            font=("Helvetica", 52, "italic"),
            fill=PINK_NEON, anchor="center", tags="img")
        self._canvas.create_text(
            cx + 60, cy + 10, text="Box",
            font=("Helvetica", 52, "bold"),
            fill=CYAN_NEON, anchor="center", tags="img")

    def _animate_progress(self):
        self._anim_target = 78
        self._tick()

    def _tick(self):
        if self._closed:
            return
        current = self._progress_var.get()
        if current < self._anim_target:
            step = max(0.3, (self._anim_target - current) * 0.05)
            self._progress_var.set(min(current + step, self._anim_target))
            self.after(30, self._tick)

    def set_status(self, text: str):
        if not self._closed:
            self._status_var.set(text)
            self.update_idletasks()

    def set_progress(self, value: float):
        if not self._closed:
            # Drive the animation target rather than setting the var directly.
            # This prevents the easing loop and external updates from competing,
            # which caused the bar to jump backwards when scan callbacks arrived
            # out of order with the 30ms tick.
            target = min(float(value), 100)
            if target > self._anim_target:
                self._anim_target = target
                self._tick()   # nudge animation forward immediately

    def finish(self):
        if self._closed:
            return
        self._progress_var.set(100)
        self._status_var.set("Ready!")
        self.update_idletasks()
        self.after(500, self._remove)

    def _remove(self):
        if not self._closed:
            self._closed = True
            try:
                self.pack_forget()
                self.destroy()
            except Exception:
                pass
