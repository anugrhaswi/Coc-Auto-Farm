class WindowManager:
    """
    ADB-only capture and guest-resolution tracking. Coordinates are guest
    pixels, so win_left/win_top stay 0 and win_width/win_height track the
    guest resolution (keeps the farming code's offset math a no-op).

    Requires a connected EmulatorAdb; there is no desktop fallback.
    """

    def __init__(self, adb):
        if adb is None:
            raise ValueError("WindowManager requires a connected EmulatorAdb (ADB-only mode)")
        self.adb = adb
        self.win_left = 0
        self.win_top = 0
        self.win_width = 0
        self.win_height = 0

    def focus_window(self):
        # Background mode needs no window activation.
        pass

    def get_rect(self):
        #Get the guest rectangle (left, top, width, height)
        w, h = self.adb.resolution()
        self.win_left = 0
        self.win_top = 0
        self.win_width = w
        self.win_height = h

    def capture_window(self):
        #Capture the guest screen as a BGR image (OpenCV format)
        frame = self.adb.screencap()
        if frame is None:
            raise RuntimeError("ADB screencap failed")
        self.win_height, self.win_width = frame.shape[:2]
        self.win_left = 0
        self.win_top = 0
        return frame