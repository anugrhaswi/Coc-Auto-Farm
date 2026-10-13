"""
Pixel pinpoint helper: shows where a guest-pixel coordinate (or a calibrated
button) lands on the emulator screen.

Runs standalone (`python pinpoint.py`), captures the current screen via ADB,
overlays a crosshair + circle at the requested point, and pops a scaled Tk
window with the pixel and normalized-fraction readout.

Usage:
    python pinpoint.py 986 603                  # guest-pixel coordinates
    python pinpoint.py --button surr_conf       # a calibrated button's spot
"""
import argparse
import logging
import sys
import tkinter as tk
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

from config import AppConfig
from adb import EmulatorAdb
from logger import setup_logging
from settings import load_settings

logger = logging.getLogger(__name__)

PLAYER_COLORS = None  # reserved (mirrors calibrate styling); kept for future use


def clamp_point(x, y, w, h):
    """Clamp a point into the frame; returns clamped (x, y) and a clamped flag."""
    cx = max(0, min(w - 1, int(x)))
    cy = max(0, min(h - 1, int(y)))
    return cx, cy, (cx, cy) != (int(x), int(y))


def resolve_point(x_arg, y_arg, button, controls, w, h):
    """
    Turn the CLI inputs into guest pixels.
    Returns (x, y, keyword) where keyword is what the user asked for.
    Raises ValueError with a user-facing message.
    """
    if x_arg is not None and y_arg is not None:
        return int(x_arg), int(y_arg), f"({x_arg}, {y_arg})"
    if button:
        norm = (controls or {}).get(button)
        if not norm or "x" not in norm or "y" not in norm:
            raise ValueError(
                f"'{button}' is not calibrated - run calibrate.py first"
            )
        return (
            int(round(norm["x"] * w)),
            int(round(norm["y"] * h)),
            button,
        )
    raise ValueError("give pixel coordinates (x y) or --button NAME")


def draw_marker(frame, x, y):
    """Draw a crosshair + circle at (x, y) on a BGR frame, in place."""
    h, w = frame.shape[:2]
    cv2.circle(frame, (x, y), 20, (0, 0, 255), 4)
    cv2.line(frame, (x, 0), (x, h - 1), (0, 255, 0), 2)
    cv2.line(frame, (0, y), (w - 1, y), (0, 255, 0), 2)
    cv2.circle(frame, (x, y), 3, (255, 255, 255), -1)
    return frame


def show_window(root, frame, x, y, keyword):
    """Show the marked screenshot in a scaled window with the readout."""
    h, w = frame.shape[:2]
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    scale = min((sw * 0.85) / w, (sh * 0.85) / h)
    dw, dh = int(w * scale), int(h * scale)

    fx, fy = x / w, y / h
    info = tk.Label(
        root,
        text=f"({x}, {y})  ->  fraction ({fx:.3f}, {fy:.3f})   |   screen {w}x{h}",
        font=("Segoe UI", 11, "bold"),
        fg="#f8f9fa",
        bg="#202124",
        pady=6,
    )
    info.pack(fill="x")

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb).resize((dw, dh), Image.LANCZOS)
    photo = ImageTk.PhotoImage(pil)
    label = tk.Label(root, image=photo, bg="#15161a")
    label.image = photo  # keep a reference so Tk doesn't GC it
    label.pack()

    root.title(f"Pinpoint {keyword}")
    root.minsize(dw, dh)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="pinpoint",
        description="Show where a guest-pixel coordinate or calibrated button "
        "lands on the emulator screen.",
    )
    parser.add_argument("x", nargs="?", type=int, help="x in guest pixels")
    parser.add_argument("y", nargs="?", type=int, help="y in guest pixels")
    parser.add_argument(
        "--button",
        help="calibrated button name from settings.json (e.g. surr_conf)",
    )
    args = parser.parse_args(argv)

    has_xy = args.x is not None and args.y is not None
    if has_xy and args.button:
        parser.error("give either pixel coordinates or --button, not both")
    if not has_xy and not args.button:
        parser.error("give pixel coordinates (x y) or --button NAME")
    return args


def main():
    setup_logging()
    args = parse_args()

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

    frame = adb.screencap()
    if frame is None:
        tk.Tk().withdraw()
        messagebox.showerror(
            "Capture failed", f"Could not capture the emulator screen.\n{adb.last_error}"
        )
        sys.exit(1)

    h, w = frame.shape[:2]
    controls = load_settings().get("controls") or {}
    try:
        x, y, keyword = resolve_point(args.x, args.y, args.button, controls, w, h)
    except ValueError as e:
        messagebox.showerror("Pinpoint", str(e))
        sys.exit(1)

    cx, cy, clamped = clamp_point(x, y, w, h)
    if clamped:
        logger.warning(f"({x}, {y}) is outside the {w}x{h} screen; clamped to ({cx}, {cy})")
    x, y = cx, cy

    logger.info(f"point ({x}, {y}) at screen {w}x{h} = fraction ({x / w:.3f}, {y / h:.3f})")
    draw_marker(frame, x, y)

    root = tk.Tk()
    show_window(root, frame, x, y, keyword)
    root.mainloop()


if __name__ == "__main__":
    main()