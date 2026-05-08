from pywinauto import Application
import pyautogui
import cv2
import numpy as np


class WindowManager:
    """
    Manages window interaction: focus, capture, and rectangle tracking.
    Removes globals: win, win_left, win_top, win_width, win_height
    """

    def __init__(self, config):
        self.config = config
        self.win_left = None
        self.win_top = None
        self.win_width = None
        self.win_height = None
        self.app = Application(backend="uia").connect(title_re=self.config.window_title_re)
        self.win = self.app.top_window()

    def focus_window(self):
        #Switch the window to the front
        try:
            self.win.set_focus()
        except Exception as e:
            # If set_focus fails, the "restore" trick usually forces it
            self.win.minimize()
            self.win.restore()
            self.win.set_focus()

    def get_rect(self):
        #Get the window rectangle (left, top, width, height)
        rect = self.win.rectangle()
        self.win_left = rect.left
        self.win_top = rect.top
        self.win_width = rect.width()
        self.win_height = rect.height()

    def capture_window(self):
        #Capture the window content as a BGR image (OpenCV format)
        self.focus_window()
        self.get_rect()
        screenshot = pyautogui.screenshot(region=(self.win_left, self.win_top, self.win_width, self.win_height))
        frame = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        return frame