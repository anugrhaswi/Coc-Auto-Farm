# Troubleshooting

## "Could not connect to the emulator via ADB"

Check, in order:

1. LDPlayer is running. (ADB port only listens while an instance is up.)
2. **Settings → Others → ADB local connection** is enabled.
3. The host adb binary is findable. Auto-detection covers:
   - `adb` on `PATH` / `platform-tools`,
   - `$ANDROID_HOME\platform-tools\adb.exe`,
   - `C:\LDPlayer\LDPlayer9\adb.exe` (install folder).
   Fix: run `adb connect 127.0.0.1:5555` to see which one fails, or set
   `config.adb_path` explicitly.
4. Your instance really is `5555`. Only the first instance uses 5555; others
   use 5557, 5559… Set `config.adb_port` if you're not on the first instance.
5. `adb devices` shows the serial as **`device`** (not `offline`). If `offline`,
   restart the adb server: `adb kill-server` then connect again.

## "Run calibrate.py first" when pressing Start

`settings.json` has no `controls` entry for one of the 11 buttons. Fix:

```
python calibrate.py
```

Set all 11 buttons, Save, relaunch `main.py`.

## "Could not find [class] after X attempts"

A single scan gave up: YOLO saw no storehouses or gold text within
`max_retries` captures. This is no longer fatal — the bot taps the calibrated
retry spots (`retry_btn_1`, `retry_btn_2`, `retry_btn_3`; no attack has started)
and restarts the raid loop from the beginning. If this happens
`recovery.scan_restart_limit` times **in a row** (default 3) the bot stops and
sends an error notification. To make it more tolerant:

- Raise `recovery.scan_restart_limit` in `settings.json`.
- Slow the scans down with `waits.scan_base_first` / `scan_treasures_first`.
- Lower `config.conf` (e.g. 0.4) if boxes exist but are being thresholded out.
- Check `logs/bot.log` for what phase it was in.
- Confirm the model `models/best.pt` includes that class name (see labels in
  `config.py`).

## "Failed to load YOLO model"

- Does `models/best.pt` exist? (Default `config.model_path` is
  `models/best.pt`, relative to the script.)
- Is it a valid Ultralytics `.pt`? First time, Ultralytics downloads any
  boilerplate — an offline/airgapped machine will fail.

## Buttons work but nothing happens in the game

Background ADB taps are injected into the guest, not the desktop, so the
taps are fine even when the window is hidden. If the game state is wrong:

1. Redo calibration with the correct screens visible (see
   [Usage](USAGE.md#calibrate-buttons-calibratepy)).
2. Confirm the guest resolution the bot uses matches what you calibrated on —
   `adb -s 127.0.0.1:5555 shell wm size`. Normalized coords adapt automatically,
   but a *changed* resolution between calibration and farming is fine (that's
   the point); a *calibration done on the wrong screen* is not.

## No stored window / picker is gone

There is no window picker anymore — the emulator is identified purely by its
ADB port (`config.adb_port`, default `127.0.0.1:5555`). If the bot connects to
a port that isn't your emulator (e.g. a leftover from another instance),
`adb devices` will show it; fix by setting `config.adb_port` to that instance's
port (5555, 5557, 5559…).

## High memory usage

YOLO + EasyOCR are heavy. Close other apps, and set `config.gpu = True` if you
have an NVIDIA GPU (or leave `None` to auto-detect CUDA).

## F12 hotkey not registered

If the hotkey fails (permission or locked keyboard on the host), the warning is
logged. The GUI stays visible while farming, so the **Stop Farm** button is
always available as a fallback. To restore F12, try running the terminal as
Administrator so the `keyboard` library can register global hotkeys.