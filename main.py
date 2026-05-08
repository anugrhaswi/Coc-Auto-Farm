"""
Entry point: composes all modules and starts the Tkinter GUI.
"""

import logging
import os
import queue
import sys
import tkinter as tk
from tkinter import messagebox

import keyboard

from config import AppConfig
from logger import setup_logging
from window_manager import WindowManager
from vision import VisionManager
from automation import AutomationController
from bot import CocBot
from gui import BotGUI

logger = logging.getLogger(__name__)


def main():
    setup_logging()

    config = AppConfig()

    # Optional: ensure models directory exists next to script for PyInstaller friendliness
    model_full_path = os.path.join(os.path.dirname(__file__), config.model_path)
    if os.path.exists(model_full_path):
        config.model_path = model_full_path

    # Connect to LDPlayer window
    try:
        window = WindowManager(config)
    except Exception as e:
        logger.error(f"Failed to connect to LDPlayer window: {e}")
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Connection Error",
            f"Could not connect to LDPlayer window.\n\n{e}\n\n"
            "Please start LDPlayer and make sure its title contains 'LDPlayer'."
        )
        sys.exit(1)

    vision = VisionManager(config)
    automation = AutomationController()

    msg_queue = queue.Queue()
    bot = CocBot(config, window, vision, automation, msg_queue)

    root = tk.Tk()
    app = BotGUI(root, bot, msg_queue)

    # Emergency keyboard stop
    try:
        keyboard.add_hotkey("f12", bot.stop)
    except Exception as e:
        logger.warning(f"Could not register F12 hotkey: {e}")

    root.mainloop()


if __name__ == "__main__":
    main()
