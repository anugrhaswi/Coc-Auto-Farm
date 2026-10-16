"""
Entry point: composes all modules and starts the Tkinter GUI.
"""

print("COC Auto-Farm: starting...", flush=True)

import logging
import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox

import keyboard

from config import AppConfig
from adb import EmulatorAdb
from logger import setup_logging
from window_manager import WindowManager
from settings import load_settings
from vision import VisionManager
from automation import AutomationController
from bot import CocBot
from gui import BotGUI
from notify import Notifier

logger = logging.getLogger(__name__)


def main():
    setup_logging()
    logger.info("COC Auto-Farm starting")

    config = AppConfig()

    # Optional: ensure models directory exists next to script for PyInstaller friendliness
    model_full_path = os.path.join(os.path.dirname(__file__), config.model_path)
    if os.path.exists(model_full_path):
        config.model_path = model_full_path

    settings = load_settings()
    notifier = Notifier(settings.get("notifications"))

    # Root is kept visible from the start so error dialogs (and the GUI) work.
    root = tk.Tk()

    adb = EmulatorAdb(
        host=config.adb_host, port=config.adb_port, adb_path=config.adb_path
    )
    if not adb.connect():
        logger.error(f"ADB connect failed: {adb.last_error}")
        # Process is about to exit, so send synchronously (text only: ADB is down).
        notifier.notify(
            "COC Auto-Farm: ADB error",
            "Could not connect to the emulator via ADB. "
            f"Details: {adb.last_error}",
            blocking=True,
            priority="high",
            tags="warning",
        )
        messagebox.showerror(
            "ADB Error",
            "Could not connect to the emulator via ADB.\n\n"
            "Start the emulator and enable 'ADB local connection' in LDPlayer "
            "settings (Settings > Others).\n"
            "Expected device: 127.0.0.1:5555 (first instance).\n\n"
            f"Details: {adb.last_error}",
        )
        sys.exit(1)

    window = WindowManager(adb=adb)
    vision = VisionManager(config)
    automation = AutomationController(adb=adb)
    controls = settings.get("controls") or {}
    waits = settings.get("waits") or {}
    recovery = settings.get("recovery") or {}
    attack = settings.get("attack") or {}

    msg_queue = queue.Queue()
    bot = CocBot(config, window, vision, automation, msg_queue, controls=controls, waits=waits, notifier=notifier, recovery=recovery, attack=attack)

    root.deiconify()
    app = BotGUI(root, bot, msg_queue)

    # Emergency keyboard stop
    try:
        keyboard.add_hotkey("f12", bot.stop)
    except Exception as e:
        logger.warning(f"Could not register F12 hotkey: {e}")

    root.mainloop()


if __name__ == "__main__":
    main()