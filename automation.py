import pyautogui


class AutomationController:
    """
    Thin wrapper around pyautogui for clicking and key presses.
    Centralizes all user input for future extensibility (humanization, delays, etc.).
    """

    def __init__(self):
        """Initialize automation controller."""
        pyautogui.FAILSAFE = True

    def click(self, x, y=None):
        """
        Click at screen coordinates (x, y).

        Args:
            x: screen x coordinate (or a tuple (x, y))
            y: screen y coordinate
        """
        if y is None:
            x, y = x
        pyautogui.click(int(x), int(y))

    def send_key(self, key):
        """
        Press a keyboard key.
        
        Args:
            key: key name (e.g., 'f12', 'enter', 'space')
        """
        pyautogui.press(key)
