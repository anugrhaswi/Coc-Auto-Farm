class AutomationController:
    """
    Wrapper for tapping and key events, injected straight into the Android
    guest via ADB (guest-pixel coordinates, works with the window
    backgrounded). There is no desktop fallback.
    """

    def __init__(self, adb):
        """Initialize automation controller."""
        if adb is None:
            raise ValueError("AutomationController requires a connected EmulatorAdb (ADB-only mode)")
        self.adb = adb

    def click(self, x, y=None):
        """
        Tap at coordinates (guest pixels). A single (x, y) tuple may also
        be passed.
        """
        if y is None:
            x, y = x
        if not self.adb.tap(int(x), int(y)):
            raise RuntimeError("ADB tap failed")

    def send_key(self, key):
        """
        Send a key event to the guest via ADB.

        Args:
            key: key name (e.g., 'f12', 'enter', 'space')
        """
        keycode = _KEYEVENT_MAP.get(key)
        if keycode is None:
            raise ValueError(f"Unsupported key for ADB: {key!r}")
        if not self.adb.keyevent(keycode):
            raise RuntimeError(f"ADB keyevent failed for {key!r}")


_KEYEVENT_MAP = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "space": 62,
    "escape": 111,
}