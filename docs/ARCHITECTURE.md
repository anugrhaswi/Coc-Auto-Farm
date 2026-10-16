# Architecture

## Overview

COC Auto Farm captures the emulator screen, decides when to attack, and injects
taps — without the emulator window needing focus or visibility.

Input and capture go through **ADB** (`adb exec-out screencap -p`,
`adb shell input tap`). Fixed menu buttons are tapped from **calibrated
normalized coordinates** (fractions of the guest screen, stored in
`settings.json`). YOLO + EasyOCR are used only where content _moves_ per base:
the storehouses to pick from and the text resource values (Gold / Elixir /
Dark Elixir, and post-battle loot).

## Module dependency diagram

```
run.bat
   |
   v
main.py ─────────────────────────────⇢ gui.py (BotGUI)
   |  \                                     ^
   |   \- settings.py (settings.json)       |
   |   \- WindowManager ──┬─ adb.py ────────┼──── (EmulatorAdb)
   |   \- AutomationController ── adb.py ───┘
   |   \- VisionManager ─ config.py (AppConfig)
   |
   v
bot.py (CocBot, worker thread)
   |-- _press_button()      -> automation -> adb
   |-- _scan_*()            -> window.capture_window() -> adb.screencap()
   |                          vision.detect/read_number -> YOLO/OCR
   |
   v
calibrate.py (standalone; writes settings.json "controls")
```

Components and their neighbours:

| Component | Knows about | Depends on |
|-----------|-------------|------------|
| `main.py` | everything | all modules |
| `config.py` | nothing (data only) | — |
| `window_manager.py` | guest resolution, ADB capture | `adb.py` |
| `adb.py` | adb binary, guest screen | subprocess, cv2 |
| `automation.py` | tap/key target | `adb.py` |
| `vision.py` | YOLO/EasyOCR inference | torch, ultralytics, easyocr |
| `bot.py` | the game flow | everything above |
| `gui.py` | Tkinter + message queue | — |
| `calibrate.py` | screenshot + click-to-save | `adb.py`, settings.py, Pillow |

## Thread model

- **Main thread**: `main()` — ADB connect/gate, Tkinter GUI (`gui.py`), message
  queue polling (every 150ms), F12 hotkey.
- **Worker thread**: `bot.py` `CocBot._farm_loop()` — capture, inference, taps.
  Reads `_stop_event` in every sleep chunk, so stop is near-instant.
- **Communication**: the worker never touches Tk directly. It puts
  `{"type": "log" | "alert" | "status" | "totals", ...}` dicts on a
  `queue.Queue`; `BotGUI._poll_queue()` drains it on the main thread.

## Coordinate systems

Two coordinate spaces exist; the layers keep them straight:

| Space | Meaning | Producer |
|-------|---------|----------|
| **Guest pixels** | Pixels inside the emulator's Android screen (`wm size`) | `adb.screencap()`, vision detections |
| **Normalized** | Fractions `(0..1, 0..1)` of the guest screen | `calibrate.py` → `settings.json` |

`WindowManager.win_left = win_top = 0`, `win_width/win_height` = guest
resolution. `_press_button` computes `pixel = fraction * guest_dimension` and
taps via ADB. Vision returns frame-local (guest) pixels, clicked directly.
No desktop offsets, no window focus — the emulator can stay minimized.

### Fixed buttons vs. per-base content

| Item | Mechanism | Why |
|------|-----------|-----|
| `attack_btn_home`, `find_btn`, `nxt_btn`, `attack_btn`, `s_goblin`, `surr`, `surr_conf`, `return_home` | calibrated normalized coords | positions don't change between bases; calibrate once |
| `gold`, `elixir`, `d_elixir`, storehouses (`golds`/`elixirs`/`d_elixirs`), post-battle labels | YOLO + OCR | positions/layout vary per base |

There is **no YOLO fallback** for fixed buttons: if a `controls` entry is
missing, `CocBot.start()` refuses to farm and tells the user to run
`calibrate.py`. This is deliberate — the whole point is that button layout
changes (game updates, resolution) never require model retraining.

## Normalized controls (in plain words)

Instead of remembering buttons as exact pixels ("left: 800, top: 630") — which
break when the game changes resolution or the emulator is resized — each button
is stored as a percentage of the screen:

```
"rules of thumb" example:
  attack_home -> x = 0.50  (halfway left->right)
                 y = 0.85  (85% of the way down)
```

`bot.py` multiplies the percentage by the current guest resolution
(`0.85 × 900 = 765` pixels down on a 900-tall screen). Same tap position on
any resolution — and when a game update moves buttons, you recalibrate by
clicking the new spots, no YOLO retraining.

## Farm loop (bot.py `_farm_loop`)

0. **Warm-up — models**: set status `loading` and call `vision.load()` (YOLO +
   EasyOCR) once, before the first raid, so detection has no cold-start stall
   later. A load failure becomes a normal error (log + optional notification).
1. **Phase 1 — navigate**: `_press_button("attack_btn_home")`,
   `("find_btn")`, `("attack_btn")`, ~0.5s between taps (calibrated).
2. **Phase 2 — search & decide**: `_scan_base_layout()` (YOLO storehouses) +
   `_scan_treasures()` (YOLO boxes → OCR values). If resources meet targets →
   `_initiate_attack()` (multi-wave: taps `s_goblin` and sweeps the horizontal
   rows of every gold/elixir/dark store above the calibrated `surr` Y, then
   re-scans and repeats the wave until `attack.drain_ratio` of the providers seen
   at scan time are gone, `attack.time_limit` seconds elapse, or a Stop is
   requested between waves; a started sweep always runs to completion, then
   pauses `waits.attack_settle`), then
   `_press_button("surr")` (opens the quit
   dialog), `_press_button("surr_conf")` (red confirm),
   `_scan_post_game()` (data-driven: polls every interval, keeps the max value
   per resource and drops duplicate-row boxes, stopping when all three read
   non-zero or the summary's Return Home is up with a readable value; else
   best-of/0 at the timeout), add to totals, `_press_button("return_home")`.
   Otherwise `_press_button("nxt_btn")` to skip and loop.
3. **Scan-failure recovery**: if a scan gives up it raises `ScanFailed` and the
   loop restarts at Phase 1. Every failure first sends a notification with a
   screenshot of the failing screen (captured before the recovery taps). If the
   **resource** scan (`_scan_treasures`) fails
   on the *first* consecutive failure it raises `TreasureScanFailed` and the bot
   taps `surr` (the resource row is often hidden behind a dialog/battle); from
   the second failure on, and for every base-layout failure, it taps
   `retry_btn_1`, `retry_btn_2`, then `retry_btn_3` (calibrated). A
   consecutive-failure streak counter is reset by any successful scan and, at
   `recovery.scan_restart_limit`, stops the session (with a notification).
4. **0-loot watchdog**: a raid whose post-battle Gold/Elixir/Dark are all 0
   (usually a missed surrender / unreadable post-battle screen) increments a
   separate streak; any raid with loot resets it. At
   `recovery.scan_restart_limit` empty raids in a row the bot notifies and stops,
   so a desync can't run silently all night.
5. Stop when totals exceed goals, F12 pressed, or an error occurs.

`_scan_base_layout` retries up to `config.max_retries` then raises `ScanFailed`;
`_scan_treasures` does the same but raises `TreasureScanFailed` (a `ScanFailed`
subclass); `_scan_post_game` polls every `scan_post_interval` up to `scan_post_timeout`,
keeping the max value per resource and dropping boxes that overlap a row already
read; it returns once all three are non-zero, or once `return_home` is visible
with a readable value (otherwise the best partial, or 0 if nothing was
readable). On F12 a `RuntimeError("Bot stopped")` unwinds the loop cleanly (it
is not a `ScanFailed`).