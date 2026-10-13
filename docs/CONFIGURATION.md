# Configuration

There are two configuration layers:

- **`config.py`** — code-level `AppConfig` dataclass (model, ADB, class labels).
- **`settings.json`** — user data written at runtime (the calibrated button map).

## `AppConfig` (`config.py`)

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `model_path` | `str` | `"models/best.pt"` | Path to the YOLO model used for layout / treasures / loot |
| `conf` | `float` | `0.5` | YOLO confidence threshold |
| `gpu` | `bool | None` | `None` | `None` = auto (CUDA when available); force `True`/`False` |
| `max_retries` | `int` | `4` | Attempts per vision scan before giving up |
| `adb_host` | `str` | `"127.0.0.1"` | Host the emulator ADB server listens on |
| `adb_port` | `int` | `5555` | First LDPlayer instance. Instance #2 = 5557, #3 = 5559, ... |
| `adb_path` | `str` | `""` | Explicit adb.exe. Empty = auto-detect (PATH → platform-tools → LDPlayer folder) |

### Class labels

YOLO class-name strings consumed by `bot.py` / `vision.py`. Change these only
if your model was trained with different names:

- Buttons (now calibrated, not YOLO): `attack_btn_home`, `find_btn`, `nxt_btn`, `attack_btn`, `s_goblin`, `surr`, `surr_conf`, `return_home`
- Top-bar resources: `gold`, `elixir`, `d_elixir`
- Base layout (dynamic): `elixirs`, `golds`, `d_elixirs`
- Post-battle labels: `post_gold`, `post_elixir`, `post_d_elixir`

## `settings.json`

Created next to the app. Example:

```json
{
  "controls": {
    "attack_btn_home": { "x": 0.50, "y": 0.85 },
    "find_btn":       { "x": 0.50, "y": 0.75 },
    "nxt_btn":        { "x": 0.50, "y": 0.78 },
    "attack_btn":     { "x": 0.50, "y": 0.62 },
    "s_goblin":       { "x": 0.50, "y": 0.92 },
    "surr":           { "x": 0.50, "y": 0.80 },
    "surr_conf":      { "x": 0.50, "y": 0.70 },
    "return_home":    { "x": 0.50, "y": 0.90 }
  },
  "waits": {
    "surr_conf": 2.5
  }
}
```

| Key | Purpose |
|-----|---------|
| `controls` | Normalized `(x, y)` fractions for the 11 fixed buttons; written by `calibrate.py`. Missing entries block farming (must calibrate) |
| `waits` | Optional per-step pause/grace times in seconds. Only the keys you list are overridden; everything else uses the code defaults below |

`waits` keys (defaults in parentheses). **Button waits** are the pause after a
tap: `attack_btn_home` (0.5), `find_btn` (0.5), `nxt_btn` (1.5), `attack_btn`
(0.5), `surr` (1.0), `surr_conf` (1.0), `return_home` (0.5), `retry_btn_1`
(0.5), `retry_btn_2` (0.5), `retry_btn_3` (0.5).
**Scan graces** are the settle time before a scan tries again:
`scan_base_first` (3.0), `scan_base_retry` (2.0), `scan_treasures_first` (1.0),
`scan_treasures_retry` (0.5).
**Post-battle loot** is data-driven instead of fixed: the bot re-reads the
screen every `scan_post_interval` (0.3)s and continues the moment the loot
values appear, giving up after `scan_post_timeout` (12.0)s (that raid is then
counted as 0 with a warning).

For example, to let the post-battle loot screen take longer before giving up:

```json
{ "waits": { "scan_post_timeout": 20 } }
```

### `notifications`

Optional, **off by default**, and the only remote channel for now is
[ntfy.sh](https://ntfy.sh). When configured, the bot pushes a message when the
farm loop hits an error, when a session reaches its goals, and when it refuses
to start (missing buttons). Error and completion pushes include a screenshot
saved under `logs/error_<timestamp>.jpg` (downscaled and JPEG-encoded to fit
ntfy.sh's 2 MB attachment limit).

```json
"notifications": {
  "enabled": true,
  "ntfy_topic": "coc-farm-8f3a91d2",
  "ntfy_server": "https://ntfy.sh",
  "ntfy_token": "",
  "attachment_max_bytes": 1800000
}
```

| Key | Default | Purpose |
| --- | --- | --- |
| `enabled` | `false` | Master switch. Nothing is sent unless this is `true` **and** a topic is set. |
| `ntfy_topic` | `""` | Topic name you subscribe to in the ntfy app. Use a long, random, hard-to-guess value. |
| `ntfy_server` | `https://ntfy.sh` | ntfy server base URL (change for a self-hosted server). |
| `ntfy_token` | `""` | Optional access token for a protected topic. |
| `attachment_max_bytes` | `1800000` | Skip the image and send text only if a capture exceeds this. Kept under ntfy.sh's 2 MB per-attachment limit. |

> **Privacy:** on the public ntfy.sh server anyone who knows the topic name can
> read your notifications. Treat `ntfy_topic` like a password. See
> `docs/USAGE.md` for setup steps.
>
> Screenshots are best-effort: they are downscaled/JPEG-compressed before upload,
> and if an upload still fails the alert is sent as text so you always hear about
> the error.

### `recovery`

Controls how the bot recovers when a scan cannot find the gold/elixir labels it
needs (usually a screen that was still loading). Instead of ending the session,
the bot taps the calibrated retry spots (`retry_btn_1`, `retry_btn_2`, then
`retry_btn_3`, one tap each — set them in `calibrate.py`) and starts the raid
loop over.

```json
"recovery": {
  "scan_restart_limit": 3
}
```

| Key | Default | Purpose |
| --- | --- | --- |
| `scan_restart_limit` | `3` | How many scans may fail **in a row** before the bot gives up. The count resets to 0 as soon as any scan succeeds, so intermittent misses keep the session alive. |

When the limit is reached the bot sends an error notification (if `notifications`
is configured) and stops.

`settings.json` is managed by `settings.load_settings()` /
`settings.save_settings()`. It is optional: without a `controls` section the
bot refuses to farm until you run `calibrate.py`.

## ADB connection notes

- LDPlayer's "ADB local connection" setting (Settings → Others) does **not**
  show a port; the port is implicit — the **first instance always uses 5555**,
  each extra instance owns the next odd port (5557, 5559…).
- `EmulatorAdb.connect()` tries the configured port, then auto-scans odd ports
  5555–5599. `adb_port` exists for cases where auto-scan can't apply.
- The host adb binary is separate from the emulator's guest ADB. The bot
  auto-detects it: `PATH` → `$ANDROID_HOME\platform-tools` → LDPlayer's install
  folder (`C:\LDPlayer\LDPlayer9\adb.exe`). Override with `adb_path`.
- Multi-instance setups: run one bot per instance by setting `adb_port`
  (and the right instance) for each.