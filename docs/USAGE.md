# Usage

## Setup checklist

1. **LDPlayer** running, logged into Clash of Clans, on your main village.
2. **ADB local connection enabled**: LDPlayer → Settings → Others →
   *ADB local connection* = on. (Verification: `adb connect 127.0.0.1:5555`)
3. **Python 3.10+** virtual env created and `requirements.txt` installed.
4. **Model present** at `models/best.pt` (or change `config.model_path`).
5. **Buttons calibrated** (below).

## Calibrate buttons (`calibrate.py`)

This is the only per-install / per-game-update setup:

```
python calibrate.py
```

1. The tool connects via ADB and shows a screenshot of your current screen.
2. Click the button described at the top, in order:
   - **Attack button** (village screen, bottom-right cluster)
   - **Find Match** (green button)
   - **Next base** (on the search screen)
   - **Attack** (confirm match)
   - **Goblin troop** (bottom troop bar)
   - **Surrender button** (in battle, bottom-left)
   - **Surrender confirm** (red button on the quit dialog)
   - **Return Home** (post-battle screen)
3. `Undo` / `Clear all` are available if you mis-click.
4. Press **Save** — this writes `controls` to `settings.json`.

Recalibrate after a game update that moves button positions; there is no model
retraining involved.

To re-fix just **one** button later (e.g. a single spot moved): click its row
in the progress list on the left, click its new spot on the screenshot, then
press **Save** — the other buttons keep their saved positions. `Undo` reverses
the last click, and `Clear selected` unsets only the currently selected button.

> Note: if you calibrated with an older version of this project, your
> `settings.json` will be missing buttons that were once YOLO-detected
> (`nxt_btn`, `s_goblin`, `surr`). The bot refuses to farm
> ("Run calibrate.py first") until you calibrate once more with the current
> version.

> Tip: calibrate while on the exact screens the bot will click, i.e. have the
> main village up (for Attack + Find Match), then the attack screen (Attack),
> then in a battle (Surrender), then the victory screen (Return Home). The bot
> uses the same relative spots every time, so a single calibration on your
> normal screen is usually enough for the Attack/Find/Attack buttons — but
> being on the actual screen for each click makes it unambiguous.

## Run the farm

Easiest: double-click **`run.bat`** (selects `.venv`, checks that the
dependencies import, and starts `main.py`).

Manual:

```
.venv\Scripts\Activate.ps1
python main.py
```

On startup, `main.py` connects via ADB to the configured port (default
`127.0.0.1:5555`, first LDPlayer instance) and shows an error dialog if the
connection fails. There is no window picking.

## GUI

| Control | Description |
|---------|-------------|
| Thresholds (Gold / Elixir / Dark Elixir) | Minimum resources on a base before attacking |
| Goals (Gold / Elixir / Dark Elixir) | Total farmed before the bot stops and alerts you |
| **Start** | Begin farming (blocked with an alert if buttons are uncalibrated) |
| **Stop** | Graceful stop against the current phase |
| Log viewer | Live messages from the worker |
| **F12** | Emergency stop (global hotkey) |

The window stays visible while farming — the **Stop Farm (F12)** button remains
clickable for the whole run, so you always have an on-screen way to stop. You can
also minimize it yourself; input and capture use ADB, not the desktop, so the
bot keeps running either way. The status line shows
`running - press F12 or Stop Farm` while active.

## What the bot does

- Opens the attack flow (Attack button → Find Match → Attack).
- Loads its YOLO/OCR models up front when you press **Start** (the status shows
  `loading` first, so the first raid is not slowed down).
- Scans each base's visible resources; attacks (goblins) when your thresholds
  are met, otherwise clicks **Next**.
- If a scan can't read the gold/elixir labels (usually a screen still loading),
  taps the calibrated retry spots (`retry_btn_1`, `retry_btn_2`, `retry_btn_3`)
  and restarts the raid loop instead of ending the session. It only stops after
  `recovery.scan_restart_limit` failures **in a row** (default 3), resetting the
  count on any successful scan.
- Reads the post-battle loot into the running totals.
- Reads the post-battle loot into running totals; surrenders and returns home.
- Stops when goals are met, F12 is pressed, or an error is reported.

## Tuning timings

Every wait is configurable in `settings.json` under `waits` (seconds) — you only
list the ones you want to change; the rest keep their defaults. Useful when your
emulator is slow and a step fires before the screen is ready:

```json
{ "waits": { "surr_conf": 1.0, "return_home": 1.5 } }
```

The post-battle loot read is **data-driven**: instead of a fixed pause it
re-reads the screen every `scan_post_interval` (0.3)s and continues as soon as
the loot values come back, giving up after `scan_post_timeout` (12.0)s (that
raid is then counted as 0 with a warning). So you rarely need to pad the
surrender steps — raise `scan_post_timeout` only if your loot screen is very
slow to appear.

Each raid logs what it counted and the running total, e.g.
`loot this raid: Gold +128400 | Elixir +92000 | Dark +320 (totals Gold 1284000 | ...)`.
The raw OCR text behind each number is written to `logs/bot.log` at DEBUG level,
so if a total ever looks wrong you can compare the logged values against the
in-game loot screen.

Each tap line in the log shows the wait that followed it, e.g.
`tap surr_conf at (987, 603) [0.617, 0.670] wait 1.0s`. The full per-step
timings (and the scan graces) are written to `logs/bot.log` at DEBUG level.
See `docs/CONFIGURATION.md` for the full key list and defaults.

## Getting notified (optional)

The bot can push a phone notification through [ntfy.sh](https://ntfy.sh) so you
know if something fails while you are away. It is **off by default**.

1. Install the **ntfy** app (iOS/Android) and subscribe to a topic name you
   invent, e.g. `coc-farm-8f3a91d2`. Pick something long and random — anyone who
   knows the name can read the topic on the public server.
2. In `settings.json`, enable it with the **same** topic:

   ```json
   "notifications": { "enabled": true, "ntfy_topic": "coc-farm-8f3a91d2" }
   ```

3. Start the bot. You will get a push when:
   - the farm loop hits an **error** (the emulator screen is attached),
   - a session **completes** its goals (current totals attached),
   - the bot **refuses to start** because buttons are not calibrated.

Errors and completion attach a screenshot saved to `logs/error_<timestamp>.jpg`
(downscaled and JPEG-compressed to fit ntfy.sh's 2 MB attachment limit; if an
upload still fails, the alert is sent as text). An ADB connection failure at
startup is text-only (there is no capture to attach). Notifications are
best-effort: a network problem never stops the farm loop, and failures are only
written to `logs/bot.log`. See `docs/CONFIGURATION.md` for the full key list.

## Useful commands

```bash
python calibrate.py         # (re)calibrate the 11 buttons
python pinpoint.py 986 603          # crosshair over guest-pixel (986, 603)
python pinpoint.py --button surr_conf   # crosshair over a calibrated button's spot
python main.py              # run the GUI bot
adb connect 127.0.0.1:5555  # verify the first LDPlayer instance is reachable
adb -s 127.0.0.1:5555 shell wm size   # print guest resolution
```