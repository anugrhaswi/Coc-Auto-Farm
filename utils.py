import os
import sys


def resource_path(relative_path: str) -> str:
    """
    Get absolute path to a resource.
    Works both when running from source and when bundled with PyInstaller.
    """
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(__file__), relative_path)
