import logging
import os
import sys


def setup_logging(level=logging.INFO):
    """
    Setup root logger with file + console output.
    Logs are written to ./logs/bot.log
    """
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    fh = logging.FileHandler(
        os.path.join(log_dir, "bot.log"),
        encoding="utf-8"
    )
    fh.setFormatter(formatter)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # avoid duplicate handlers if called multiple times
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(ch)
