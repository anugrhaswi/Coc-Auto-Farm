# Development

## Full API reference

### `config.py` — `AppConfig`

Dataclass of tuning knobs.

| Member | Type | Default |
|--------|------|---------|
| `model_path` | `str` | `"models/best.pt"` |
| `conf` | `float` | `0.5` |
| `gpu` | `bool | None` | `None` |
| `max_retries` | `int` | `4` |
| `adb_host` | `str` | `"127.0.0.1"` |
| `adb_port` | `int` | `5555` |
| `adb_path` | `str` | `""` |
| label constants (`ATTACK_BTN_HOME`, `FIND_BTN`, `ATTACK_BTN`, `GOLD`, `ELIXIR`, `D_ELIXIR`, `S_GOBLIN`, `ELIXIRS`, `NXT_BTN`, `GOLDS`, `D_ELIXIRS`, `SURR_BTN`, `SURR_CONF`, `POST_GOLD`, `POST_ELIXIR`, `POST_D_ELIXIR`, `RETURN_HOME`) | `str` | class-name strings |

### `adb.py`

```python
find_adb() -> str | None
# Any usable adb.exe: PATH -> platform-tools -> LDPlayer folder.

class EmulatorAdb:
    def __init__(self, host="127.0.0.1", port=5555, adb_path="", auto_scan=True)
    connected: bool            # property
    last_error: str
    def connect(self, timeout=15.0) -> bool   # connects + reads resolution
    def resolution(self) -> tuple[int, int]   # (width, height) guest pixels
    def screencap(self) -> np.ndarray | None  # BGR frame, None on failure
    def tap(self, x: int, y: int) -> bool     # guest pixel tap
    def keyevent(self, keycode: int) -> bool
```

Internals: `_run(args, timeout)` (subprocess, `CREATE_NO_WINDOW`),
`_is_device(serial)`, `_refresh_resolution()`.

### `window_manager.py` — `WindowManager`

```python
def __init__(self, adb)      # EmulatorAdb required; ADB-only (no desktop mode)
def focus_window(self)       # no-op (background mode needs no window activation)
def get_rect(self)           # fills win_left/top/width/height from guest resolution
def capture_window(self) -> np.ndarray  # BGR frame via adb.screencap()
```

Attributes: `win_left`, `win_top`, `win_width`, `win_height`, `.adb`.

### `automation.py` — `AutomationController`

```python
def __init__(self, adb)      # EmulatorAdb required; ADB-only
def click(self, x, y=None)   # adb.tap(); single (x, y) tuple OK
def send_key(self, key)      # adb keyevent map (back/home/enter/space/escape)
```

### `vision.py` — `VisionManager`

```python
def __init__(self, config)
def load(self)                     # lazy YOLO + EasyOCR
def detect(self, frame, class_names, conf=None) -> dict[str, list[tuple]]     # centers
def detect_with_details(self, frame, class_names, conf=None) -> dict[str, list[dict]]
    # items: {"center": (cx, cy), "bbox": (x1, y1, x2, y2)}
def read_number(self, frame, bbox) -> str
    # preprocess (upscale + grayscale) the bbox crop, OCR it, then return the
    # value as a plain integer string: separators stripped ("1,234,567" ->
    # "1234567") and K/M/B abbreviations expanded ("1.2M" -> "1200000");
    # "0" if nothing readable. Raw OCR text is logged at DEBUG.
def parse_loot_number(text) -> str   # module-level: the parsing above (pure, testable)
```

All coordinates are frame-relative (guest pixels in ADB mode).

### `bot.py` — `CocBot`

```python
class ScanFailed(RuntimeError)   # raised when a scan can't read its labels
class TreasureScanFailed(ScanFailed)   # subclass raised by _scan_treasures only
def __init__(self, config, window_manager, vision_manager,
             automation_controller, msg_queue, controls=None, waits=None,
             notifier=None, recovery=None)
def start(self, targets, goals)   # spawns worker; refuses if controls missing
def stop(self)                    # sets _stop_event (F12 points here)
def is_running(self) -> bool
# internals:
def _capture_screenshot() -> str | None     # saves downscaled logs/error_<ts>.jpg, best-effort
def _notify(title, message, success=False)  # push via notifier (skips capture if unconfigured)
def _press_button(self, name, wait_after=None)   # None -> self.waits[name]
def _button_coords(self, name) -> (x, y)   # normalized * guest resolution
def _scan_treasures(self) -> dict[str, str]
def _scan_base_layout(self) -> dict[str, list[tuple]]
def _scan_post_game(self) -> dict[str, str]
def _dump_post_frame(self, frame, poll)   # diagnostic only; needs COC_POST_DEBUG
def _provider_counts(self, base) -> dict  # stores per class (gold/elixir/dark)
def _count_providers_now(self) -> int | None  # one-shot, stop-ignoring
def _attack_rows(self, base) -> list      # sweep rows (guest y) above surr
def _sweep_rows(self, rows) -> int        # deploy one wave, return tap count
def _drain_summary(self, drains) -> str   # per-raid % + average line
def _initiate_attack(self, base: dict) -> float | None   # %drained of the raid
def _farm_loop(self, targets, goals)
```

`msg_queue` receives `{"type": "log"|"alert"|"status"|"totals", ...}` dicts.
`controls` maps button name → `{"x": fraction, "y": fraction}`.
`waits` overrides `DEFAULT_WAITS` (module-level dict) — merge is
`{**DEFAULT_WAITS, **waits}` with each value clamped to `>= 0`, so unknown or
missing keys fall back to code defaults. Keys are the button names plus
`scan_base_first/retry`, `scan_treasures_first/retry`, `attack_settle`,
`attack_wave_settle`, plus
`scan_post_interval` and `scan_post_timeout` for the data-driven post-battle
read: `_scan_post_game` polls every interval at a slightly reduced confidence
(a missed Dark Elixir box is common), keeps the max value per resource, ignores
boxes that overlap a row already read (`_iou`), and returns early once all three
are non-zero — or once the summary's `return_home` button is visible and any
value is readable (a genuinely empty resource shouldn't force the whole
timeout). At the timeout it returns the best partial, or `{}` so the raid counts
as 0. Point the `COC_POST_DEBUG` environment variable at a directory to make
`_scan_post_game` save every poll frame there as a full-res PNG (diagnostic
only; off by default).

`_initiate_attack` deploys goblin **waves** until the base is drained. It counts
the resource providers (gold/elixir/dark stores) seen at scan time via
`_provider_counts`, then loops: `_attack_rows` (rows from all three classes, kept
`_ATTACK_ROW_MARGIN` above the surr Y and merged within `_ATTACK_ROW_TOLERANCE`)
→ tap `s_goblin` → `_sweep_rows` → sleep `waits.attack_wave_settle` →
`_scan_base_layout()` again → if at least `attack.drain_ratio` (default 0.8) of
the providers are gone, stop; else deploy another wave. The loop also stops after
`attack.time_limit` seconds (default 90, measured from the first sweep), on a
Stop between waves, or if a re-scan raises `ScanFailed` (then it surrenders
gracefully). A Stop is honored before the first deploy and between waves but
never mid-sweep, so a started sweep always completes; `_sweep_rows` logs
`attack sweep done: …`. A failed deploy tap logs the coordinate and propagates
(aborting the session). After the loop it sleeps `waits.attack_settle` (default
5.0) via `_sleep` before the caller surrenders. `attack` overrides
`DEFAULT_ATTACK` (module-level dict) like `recovery`.

Before that final settle, `_initiate_attack` takes one **pre-surrender reading**
via `_count_providers_now` (single capture, no retries, **no stop check**, so a
manually-stopped raid still reads the live screen) and logs
`drain at surrender: providers R/N remaining (D% drained)` — falling back to the
last known count (`, last known`) if the capture fails. It then **returns** the
drained fraction (`0.0–1.0`) or `None` when no providers were detected. A hard
error raises earlier and skips this entirely, so an aborted raid is not recorded.

`_farm_loop` keeps `raid_drains` and, in its `finally` (so it runs on goals-met,
error, **and** manual stop), logs `_drain_summary` — e.g. `drain per raid: 73%,
91%, 80% | average 81% over 3 raid(s)` (`none completed` if there were no raids).
The same average is appended to the completion notification and to **every error
push** (generic failures, the stopped `RuntimeError`, the scan give-up, and the
0-loot stop) via `_drain_average`. A raid is only added to `raid_drains` after its
post-battle read succeeds, so an error mid-raid is excluded and every error push
reports the average of the **previous fully-completed** raids only. Transient
scan failures that recover do not carry the average; only the final, stopping one.

`recovery` overrides `DEFAULT_RECOVERY` (module-level dict) the same way, with
the single key `scan_restart_limit` (default 3). `_scan_base_layout` raises
`ScanFailed` and `_scan_treasures` raises `TreasureScanFailed` (a `ScanFailed`
subclass) when they exhaust their retries. `_farm_loop` warms up the models
(`vision.load()`) before the first raid, then wraps only those two scan calls in
`except ScanFailed`. Every failure first calls `_notify` with a screenshot of the
failing screen (`_capture_screenshot` runs before the recovery taps below, so the
image is the pre-recovery screen); the terminal one adds " (stopping)". The
**first** consecutive failure, if it was the resource scan
(`isinstance(e, TreasureScanFailed) and scan_streak == 1`), taps `surr`; every
other failure taps `retry_btn_1`, `retry_btn_2`, then `retry_btn_3` (the attack
has not started, so no `surr_conf` or `return_home`) — both then restart the
outer loop. The count resets on any successful scan; on the
`scan_restart_limit`-th consecutive failure it stops (the notification already
said "stopping").

A separate `zero_loot_streak` watchdog counts raids whose post-battle Gold,
Elixir and Dark are all 0 (a missed surrender / unreadable end screen); any raid
with loot resets it, and at the same `scan_restart_limit` it notifies and stops.
A plain `RuntimeError("Bot stopped")` from a stopped scan is still treated as a
clean stop, not a failure.

### `notify.py` - `Notifier`

```python
notifier = Notifier(settings.get("notifications"))   # off unless enabled + topic set
notifier.is_configured          # True only when enabled and a topic is present
notifier.notify(title, message, image_path=None, *, blocking=False,
                priority=None, tags=None)            # -> bool (True if attempted)
```

Push notifications over [ntfy.sh](https://ntfy.sh), standard library only
(`urllib`). A text message is a `POST` to `{server}/{topic}`; when `image_path`
is given the file is uploaded with `PUT` (ntfy shows it inline). If the capture
is missing, over `attachment_max_bytes` (default 1.8 MB), or the upload fails
(e.g. ntfy.sh's 2 MB limit → HTTP 413), it **falls back to the text-only
message** so the alert still arrives. Calls run on a daemon thread by default so
a slow/down server never blocks the caller (`blocking=True` is used at startup,
right before `sys.exit`). **Best-effort: every error is caught, logged at
WARNING, and never raised.** `CocBot` builds its messages in `_notify(...)`,
attaching the downscaled JPEG from `_capture_screenshot()`; if a channel is not
configured it skips the capture entirely.

### `settings.py`

```python
SETTINGS_PATH: str
load_settings() -> dict
save_settings(data) -> bool
```

### `logger.py`

```python
setup_logging(level=logging.INFO)   # file (logs/bot.log) + console
```

Level routing: console handler shows `level` (default INFO) and up; the file
handler keeps DEBUG and up. Sweep taps log via `logger.debug` (file-only audit)
while fixed buttons and per-row sweep summaries use `bot._log` (GUI + file).
Scan-grace waits (`waited N.Ns (target N.Ns) for <step>`) are also DEBUG-only.
Button taps log `tap <name> at (x, y) [fx, fy] wait N.Ns`, where the wait is the
value from `settings["waits"]` (or the code default).

### `gui.py` — `BotGUI`

```python
def __init__(self, root: tk.Tk, bot, msg_queue: queue.Queue)
# Builds the control panel; _poll_queue() drains msg_queue on an after() timer.
```

### `calibrate.py`

```python
BUTTONS: list[tuple[str, str]]           # (control name, description)
def main()
class Calibrator:
    def __init__(self, root, adb, settings)
    def select(self, name)   # jump to a button (single-button edit mode)
    def on_canvas_click(self, event)
    def recapture(self)      # grab the screen again (keeps selection/undo)
    def undo(self); def clear_selected(self); def clear_all(self); def save(self)
```

Saves `settings["controls"] = {name: {"x", "y"}}` normalized to 5 decimals.

### `main.py`

```python
def main()
```

Startup order that matters: logging → model-path resolve → `adb.connect()`
→ ADB error gate (`sys.exit(1)` with a dialog if it fails) → build
`WindowManager(adb)` + `AutomationController(adb)` → settings/controls/waits →
`CocBot` → `BotGUI` → F12 hotkey → `mainloop()`. There is no window picking
and no pre-`tk.Tk()` enumeration stage.

## Extending the bot

- **New strategy** (e.g. dragons): add a method next to `_initiate_attack`
  that consumes the `_scan_base_layout()` dict; swap it in `_farm_loop`.
- **New fixed button**: add the key to `config.py` (optional), to
  `calibrate.BUTTONS` (so the UI calibrates it), and to the required list in
  `CocBot.start()`; call `_press_button(name)`.
- **Humanization**: extend `AutomationController.click` (jitter, dwell times).
- **Different emulator**: point `adb_host`/`adb_port` at its ADB server (works
  with any emulator exposing an ADB port — no desktop window involved).
- **New game screen to read**: add a `_scan_*` helper around
  `window.capture_window()` + `vision.detect_with_details()`.

## Testing

No formal test framework is wired in; verification is done with the bundled
helper scripts / one-liners:

```bash
python -m py_compile *.py                       # syntax check all modules
python calibrate.py                             # UI + round-trip to settings.json

# ADB smoke (read-only):
python -c "from adb import EmulatorAdb; a=EmulatorAdb(); print(a.connect(), a.resolution(), a.screencap().shape)"

# Unit-ish checks (no emulator needed):
python -c "from unittest import mock; from config import AppConfig; from bot import CocBot; ..."
```

Run `main.py` and watch `logs/bot.log` for a full manual test.