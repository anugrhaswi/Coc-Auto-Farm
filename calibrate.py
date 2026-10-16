"""
Button calibration tool.

Runs standalone (`python calibrate.py`) and works on the CURRENT game state:
captures the emulator screen via ADB, shows it scaled, and lets you click each
of the 11 fixed buttons. Positions are stored in settings.json as normalized
coordinates (fractions of the screen), so they survive resolution changes and
no YOLO retraining is ever needed.

Run this once after installing, and again after a game update that moves buttons.

The screenshot is captured when the tool starts, but the game screen changes
between buttons (village -> search -> battle -> quit dialog). Press **Recapture**
at any time to grab the current screen again, so you can navigate the emulator
and re-capture without restarting the script. The row you selected stays selected
across recaptures, so the workflow is: pick a button's row, navigate the game to
that screen, press Recapture, then click the spot.

To re-fix just ONE button (e.g. an update moved a single spot), click its row in
the progress list, (optionally Recapture to change screens), click the new spot on
the screenshot, then press Save — the other buttons keep their saved coordinates.
"""

# The eleven calibrated controls, in farm-loop press order, with plain-English
# descriptions shown in the UI. Everything positionally fixed lives here;
# YOLO is left with only per-base content (storehouses, resource text).
BUTTONS = [
    ("attack_btn_home", "Attack button  (village, bottom right)"),
    ("find_btn", "Find Match  (green button)"),
    ("nxt_btn", "Next base  (on the search screen)"),
    ("attack_btn", "Attack  (confirm the battle)"),
    ("s_goblin", "Goblin troop  (bottom troop bar)"),
    ("surr", "Surrender button  (in battle, bottom-left)"),
    ("surr_conf", "Surrender confirm  (red, on the quit dialog)"),
    ("return_home", "Return Home  (post-battle button)"),
    ("retry_btn_1", "Retry spot 1  (tap 1 of 3 when a scan fails)"),
    ("retry_btn_2", "Retry spot 2  (tap 2 of 3 when a scan fails)"),
    ("retry_btn_3", "Retry spot 3  (tap 3 of 3 when a scan fails)"),
]

BUTTON_COLORS = [
    "#e6194b",
    "#3cb44b",
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#00e5ff",
    "#f032e6",
    "#ffff33",
    "#46f0f0",
    "#d4a017",
    "#7fbf7f",
]

import sys
import time
import tkinter as tk
from tkinter import messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

from config import AppConfig
from adb import EmulatorAdb
from settings import load_settings, save_settings


class Calibrator:
    def __init__(self, root, adb, settings):
        self.root = root
        self.adb = adb
        self.settings = settings
        self.controls = dict(settings.get("controls") or {})

        self.index = self._first_uncalibrated()
        self.fix_mode = False  # True = user picked a specific row; stay on it
        self.row_widgets = []
        self._photo = None
        self._img = None
        self._scale = 1.0
        self._offset_x = 0
        self._offset_y = 0
        self._undo_stack = []
        self._render_job = None

        root.title("COC Auto-Farm - Button Calibration")
        root.minsize(720, 560)
        self._size_to_screen()

        # Row 0: instructions. Row 1: canvas (grows/shrinks). Row 2: progress
        # list. Row 3: action buttons.
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)

        self.instruction = tk.StringVar()
        tk.Label(
            root,
            textvariable=self.instruction,
            font=("Segoe UI", 12, "bold"),
            fg="#f8f9fa",
            bg="#202124",
            pady=8,
        ).grid(row=0, column=0, sticky="we")

        sub = tk.Label(
            root,
            text="Left-click each button below in the screenshot. "
            "Coordinates are saved as fractions of the screen.",
            font=("Segoe UI", 9),
            fg="#bdc1c6",
            bg="#202124",
        )
        sub.grid(row=0, column=0, sticky="we")

        self.canvas = tk.Canvas(
            root, bg="#15161a", highlightthickness=0,
        )
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Configure>", self._on_resize)

        list_wrap = tk.Frame(root, bg="#202124")
        list_wrap.grid(row=2, column=0, sticky="we")
        self.status_labels = {}
        for i, (name, desc) in enumerate(BUTTONS):
            row = tk.Frame(list_wrap, bg="#202124")
            row.pack(fill="x", padx=12, pady=1)
            dot = tk.Label(
                row, text="\u25cf", fg=BUTTON_COLORS[i], bg="#202124", font=(None, 11)
            )
            dot.pack(side="left")
            label = tk.Label(
                row, text=desc, fg="#e8eaed", bg="#202124", anchor="w",
                font=("Segoe UI", 10),
            )
            label.pack(side="left")
            value = tk.Label(
                row, text=self._fmt(self.controls.get(name)), fg="#9aa0a6",
                bg="#202124", width=26, anchor="e", font=("Consolas", 9),
            )
            value.pack(side="right")
            self.status_labels[name] = value
            self.row_widgets.append(row)
            self._bind_row_click(row, name)
        self._paint_rows()

        btn_bar = tk.Frame(root, bg="#202124")
        btn_bar.grid(row=3, column=0, sticky="we", pady=(4, 10))
        self.status_var = tk.StringVar()
        tk.Label(
            btn_bar, textvariable=self.status_var, fg="#9aa0a6", bg="#202124",
            font=("Segoe UI", 9),
        ).pack(side="right", padx=10)
        for text, cmd, color in [
            ("Undo", self.undo, "#5f6368"),
            ("Clear selected", self.clear_selected, "#a86418"),
            ("Clear all", self.clear_all, "#8b0000"),
            ("Recapture", self.recapture, "#1a73e8"),
            ("Save to settings", self.save, "#188038"),
        ]:
            tk.Button(
                btn_bar, text=text, command=cmd, bg=color, fg="white",
                relief="flat", padx=16, pady=4, font=("Segoe UI", 10, "bold"),
            ).pack(side="left", padx=6)

        self._capture()
        root.after(50, self._render)
        self._update_instruction()

    # ---- setup helpers ----

    def _size_to_screen(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = max(720, int(sw * 0.92))
        h = max(560, int(sh * 0.92))
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _fmt(self, norm):
        if not norm or "x" not in norm:
            return "not set"
        return f"({norm['x']:.3f}, {norm['y']:.3f})"

    def _first_uncalibrated(self):
        for i, (name, _) in enumerate(BUTTONS):
            if name not in self.controls:
                return i
        return 0

    def _paint_rows(self):
        """Highlight the row for the current/selected button."""
        base = "#202124"
        highlight = "#3d4957"
        for row in self.row_widgets:
            row.configure(bg=base)
            for child in row.winfo_children():
                child.configure(bg=base)
        if self.index < len(self.row_widgets):
            row = self.row_widgets[self.index]
            row.configure(bg=highlight)
            for child in row.winfo_children():
                child.configure(bg=highlight)

    def _bind_row_click(self, widget, name):
        widget.bind("<Button-1>", lambda _e, n=name: self._select(n))
        for child in widget.winfo_children():
            self._bind_row_click(child, name)

    def _capture(self):
        frame = self.adb.screencap()
        if frame is None:
            messagebox.showerror(
                "Capture failed",
                f"Could not capture the emulator screen.\n{self.adb.last_error}",
            )
            sys.exit(1)
        self._img = frame

    def recapture(self):
        """
        Grab the current emulator screen again, so the user can navigate the
        game to another button's screen without restarting the tool. On failure
        the previous screenshot is kept (the app does not exit); the current
        row/selection and undo history are left untouched.
        """
        frame = self.adb.screencap()
        if frame is None:
            messagebox.showerror(
                "Recapture failed",
                f"Could not capture the emulator screen.\n{self.adb.last_error}",
            )
            return
        self._img = frame
        self._render()
        self.status_var.set("Recaptured " + time.strftime("%H:%M:%S"))

    def _on_resize(self, _event):
        if self._render_job is not None:
            try:
                self.root.after_cancel(self._render_job)
            except ValueError:
                pass
        self._render_job = self.root.after(60, self._render)

    def _render(self):
        """Fit the screenshot to the current canvas size and redraw markers."""
        self._render_job = None
        if self._img is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 4 or ch < 4:
            return  # not mapped/laid out yet

        im_h, im_w = self._img.shape[:2]
        scale = min(cw / im_w, ch / im_h)
        dw, dh = int(im_w * scale), int(im_h * scale)
        self._scale = scale
        self._offset_x = (cw - dw) // 2
        self._offset_y = (ch - dh) // 2

        rgb = cv2.cvtColor(self._img, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb).resize((dw, dh), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(pil)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

        for i, (name, _) in enumerate(BUTTONS):
            norm = self.controls.get(name)
            if norm and "x" in norm:
                x = self._offset_x + norm["x"] * im_w * scale
                y = self._offset_y + norm["y"] * im_h * scale
                is_current = i == self.index
                r = 14 if is_current else 12
                self.canvas.create_oval(
                    x - r, y - r, x + r, y + r,
                    outline=BUTTON_COLORS[i], width=(6 if is_current else 3),
                )

    def _update_instruction(self):
        if self.index >= len(BUTTONS):
            self.instruction.set("All buttons set. Press Save, then run the farm.")
            return
        if self.fix_mode:
            name, _ = BUTTONS[self.index]
            self.instruction.set(
                f"Editing {name} — click its new spot on the screenshot, "
                "then press Save. Other buttons keep their saved positions."
            )
            return
        _, desc = BUTTONS[self.index]
        self.instruction.set(
            f"({self.index + 1}/{len(BUTTONS)}) Click the button:  {desc}"
        )

    def _to_orig(self, cx, cy):
        ox = (cx - self._offset_x) / self._scale
        oy = (cy - self._offset_y) / self._scale
        im_w, im_h = self._img.shape[1], self._img.shape[0]
        ox = max(0, min(im_w - 1, int(ox)))
        oy = max(0, min(im_h - 1, int(oy)))
        return ox, oy

    # ---- interactions ----

    def _select(self, name):
        """Jump to a specific button (single-button edit mode)."""
        self.index = next(i for i, (n, _) in enumerate(BUTTONS) if n == name)
        self.fix_mode = True
        self._paint_rows()
        self._render()
        self._update_instruction()

    def on_canvas_click(self, event):
        if self.index >= len(BUTTONS) or self._img is None:
            return
        name, _ = BUTTONS[self.index]
        ox, oy = self._to_orig(event.x, event.y)
        im_w, im_h = self._img.shape[1], self._img.shape[0]
        norm = {"x": round(ox / im_w, 5), "y": round(oy / im_h, 5)}

        self._undo_stack.append((name, self.controls.get(name)))
        self.controls[name] = norm
        self.status_labels[name].config(text=self._fmt(norm))
        if not self.fix_mode:
            self.index = min(len(BUTTONS), self.index + 1)
        self._paint_rows()
        self._render()
        self._update_instruction()

    def undo(self):
        if not self._undo_stack:
            return
        name, old = self._undo_stack.pop()
        if old is None:
            self.controls.pop(name, None)
        else:
            self.controls[name] = old
        self.status_labels[name].config(text=self._fmt(self.controls.get(name)))
        self.index = next(i for i, (n, _) in enumerate(BUTTONS) if n == name)
        self.fix_mode = True
        self._paint_rows()
        self._render()
        self._update_instruction()

    def clear_selected(self):
        """Unset only the currently selected button (stays selected)."""
        if self.index >= len(BUTTONS):
            return
        name, _ = BUTTONS[self.index]
        self.controls.pop(name, None)
        self.status_labels[name].config(text=self._fmt(None))
        self.fix_mode = True
        self._paint_rows()
        self._render()
        self._update_instruction()

    def clear_all(self):
        self.controls = {}
        for name, _ in BUTTONS:
            self.status_labels[name].config(text="not set")
        self.index = 0
        self.fix_mode = False
        self._paint_rows()
        self._render()
        self._update_instruction()

    def save(self):
        missing = [name for name, _ in BUTTONS if name not in self.controls]
        if missing:
            messagebox.showwarning(
                "Missing buttons",
                "Still missing:\n" + "\n".join(f" - {name}" for name in missing)
                + "\n\nSet every button before saving.",
            )
            return
        self.settings["controls"] = self.controls
        ok = save_settings(self.settings)
        if ok:
            messagebox.showinfo(
                "Saved",
                "Calibration saved to settings.json.\n\nThe farm bot can now start.",
            )
        else:
            messagebox.showerror("Save failed", "Could not write settings.json.")


def main():
    config = AppConfig()
    adb = EmulatorAdb(host=config.adb_host, port=config.adb_port, adb_path=config.adb_path)
    if not adb.connect():
        tk.Tk().withdraw()
        messagebox.showerror(
            "ADB Error",
            "Could not connect to the emulator via ADB.\n\n"
            f"Details: {adb.last_error}",
        )
        sys.exit(1)

    settings = load_settings()
    root = tk.Tk()
    Calibrator(root, adb, settings)
    root.mainloop()


if __name__ == "__main__":
    main()