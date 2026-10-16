import logging
import os
import queue
import threading
import time

import cv2

from config import AppConfig
from notify import Notifier

logger = logging.getLogger(__name__)


DEFAULT_WAITS = {
    AppConfig.ATTACK_BTN_HOME: 0.5,
    AppConfig.FIND_BTN: 0.5,
    AppConfig.NXT_BTN: 1.5,
    AppConfig.ATTACK_BTN: 0.5,
    AppConfig.SURR_BTN: 1.0,
    AppConfig.SURR_CONF: 1.0,
    AppConfig.RETURN_HOME: 0.5,
    AppConfig.RETRY_BTN_1: 0.5,
    AppConfig.RETRY_BTN_2: 0.5,
    AppConfig.RETRY_BTN_3: 0.5,
    "scan_base_first": 3.0,
    "scan_base_retry": 2.0,
    "scan_treasures_first": 1.0,
    "scan_treasures_retry": 0.5,
    "scan_post_interval": 0.3,
    "scan_post_timeout": 12.0,
    "attack_settle": 5.0,
    "attack_wave_settle": 5.0,
}

# ntfy.sh caps attachments at 2 MB, so notification captures are downscaled and
# JPEG-encoded to stay comfortably under that limit.
NOTIFY_IMAGE_MAX_WIDTH = 1280
NOTIFY_IMAGE_QUALITY = 75

# The post-battle YOLO model sometimes labels one on-screen row as two different
# resources (e.g. the Gold row also tagged as Elixir). Boxes overlapping this
# much are treated as the same row and only counted once.
_BOX_OVERLAP_IOU = 0.6


def _iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = min(ax2, bx2) - max(ax1, bx1)
    ih = min(ay2, by2) - max(ay1, by1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0

# Recovery/beat-the-runtime knobs (settings.json -> "recovery").
DEFAULT_RECOVERY = {
    "scan_restart_limit": 3,
}

# Multi-wave attack knobs (settings.json -> "attack"). The bot keeps re-deploying
# goblin waves until at least `drain_ratio` of the resource providers counted at
# scan time are gone, or `time_limit` seconds (measured from the first sweep)
# have elapsed, whichever comes first. There is no wave cap on purpose: the
# clock is the only guaranteed stop (the bot cannot read remaining troops).
DEFAULT_ATTACK = {
    "drain_ratio": 0.8,
    "time_limit": 30.0,
}

# Sweep geometry (preserved from the notebook): tap every `_SWEEP_STEP` px from
# the left edge to `_SWEEP_RIGHT_MARGIN` before the right edge.
_SWEEP_STEP = 50
_SWEEP_RIGHT_MARGIN = 100

# Only rows at least this far *above* the surr button are swept, so a sweep can
# never land on the surr tap itself.
_ATTACK_ROW_MARGIN = 100

# Provider rows closer than this (guest px) are merged into a single sweep row,
# so a gold and an elixir store on the same line are only swept once.
_ATTACK_ROW_TOLERANCE = 20


class ScanFailed(RuntimeError):
    """A layout/treasure scan could not read the resources after its retries.

    Unlike other RuntimeErrors (ADB failure, uncalibrated button, clean stop),
    this is treated as recoverable: the farm loop taps the retry spots and
    restarts, and only alerts after several failures in a row.
    """


class TreasureScanFailed(ScanFailed):
    """The resource (gold/elixir/dark) values could not be detected at all.

    A subclass of :class:`ScanFailed` so callers can special-case it: the first
    consecutive failure of this kind taps ``surr`` (rather than the retry spots)
    before restarting Phase 1 — the resource row is often hidden by a dialog or
    a lingering battle, and ``surr`` backs out of those states.
    """


class CocBot:
    """
    Core farming bot. Preserves the exact notebook logic but wraps it in a
    thread-safe class with graceful stop support and GUI queue messaging.
    """

    def __init__(
        self,
        config: AppConfig,
        window_manager,
        vision_manager,
        automation_controller,
        msg_queue: queue.Queue,
        controls: dict | None = None,
        waits: dict | None = None,
        notifier: Notifier | None = None,
        recovery: dict | None = None,
        attack: dict | None = None,
    ):
        self.config = config
        self.window = window_manager
        self.vision = vision_manager
        self.auto = automation_controller
        self.msg_queue = msg_queue
        self.controls = controls or {}
        self.waits = {
            k: max(0.0, float(v)) for k, v in {**DEFAULT_WAITS, **(waits or {})}.items()
        }
        self.recovery = {**DEFAULT_RECOVERY, **(recovery or {})}
        self.attack = {**DEFAULT_ATTACK, **(attack or {})}
        self.notifier = notifier or Notifier()

        self._stop_event = threading.Event()
        self._thread = None

    # ---- Public lifecycle ----

    def start(self, targets, goals):
        """
        Start the farm loop in a daemon thread.

        Args:
            targets: tuple (gtarget, etarget, detarget) — minimum resources to attack
            goals:   tuple (g_goal, e_goal, de_goal) — total resources to farm before stopping
        """
        if self._thread and self._thread.is_alive():
            self._log("Bot is already running")
            return

        missing = [
            name
            for name in (
                self.config.ATTACK_BTN_HOME,
                self.config.FIND_BTN,
                self.config.NXT_BTN,
                self.config.ATTACK_BTN,
                self.config.S_GOBLIN,
                self.config.SURR_BTN,
                self.config.SURR_CONF,
                self.config.RETURN_HOME,
                self.config.RETRY_BTN_1,
                self.config.RETRY_BTN_2,
                self.config.RETRY_BTN_3,
            )
            if name not in self.controls
        ]
        if missing:
            msg = (
                "Cannot start: button positions missing for "
                + ", ".join(missing)
                + ". Run calibrate.py first."
            )
            self._log(msg)
            self._alert(msg)
            self._notify("COC Auto-Farm: cannot start", msg)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._farm_loop,
            args=(targets, goals),
            daemon=True,
        )
        self._thread.start()
        self._set_status("running")
        self._log("Bot started")

    def stop(self):
        """Request graceful stop."""
        self._log("Stop requested")
        self._stop_event.set()
        self._set_status("stopping")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- GUI communication helpers ----

    def _log(self, text: str):
        logger.info(text)
        if self.msg_queue is not None:
            self.msg_queue.put({"type": "log", "text": text})

    def _alert(self, text: str):
        if self.msg_queue is not None:
            self.msg_queue.put({"type": "alert", "text": text})

    def _set_status(self, state: str):
        if self.msg_queue is not None:
            self.msg_queue.put({"type": "status", "state": state})

    def _show_totals(self, totals: dict):
        if self.msg_queue is not None:
            self.msg_queue.put({"type": "totals", "data": totals})

    @staticmethod
    def _drain_summary(drains: list) -> str:
        """End-of-session line: every raid's %drained plus the average."""
        if not drains:
            return "drain per raid: none completed"
        per = ", ".join(f"{d * 100:.0f}%" for d in drains)
        avg = sum(drains) / len(drains)
        return (
            f"drain per raid: {per} | "
            f"average {avg * 100:.0f}% over {len(drains)} raid(s)"
        )

    @staticmethod
    def _drain_average(drains: list) -> str:
        """Short ``' | avg drain N%'`` suffix for notifications ('' if no raids)."""
        if not drains:
            return ""
        avg = sum(drains) / len(drains)
        return f" | avg drain {avg * 100:.0f}%"

    # ---- Notifications (best-effort, never disturb the loop) ----

    def _capture_screenshot(self) -> str | None:
        """
        Capture the emulator screen, shrink it, and save a compressed JPEG under
        logs/. ntfy.sh caps attachments at 2 MB, so the frame is downscaled to
        NOTIFY_IMAGE_MAX_WIDTH and JPEG-encoded (NOTIFY_IMAGE_QUALITY). Best-effort:
        returns the path, or None if the capture/save fails (e.g. ADB is down).
        """
        try:
            frame = self.window.capture_window()
        except Exception as e:
            logger.debug(f"screenshot capture failed: {e}")
            return None

        try:
            h, w = frame.shape[:2]
            if w > NOTIFY_IMAGE_MAX_WIDTH:
                scale = NOTIFY_IMAGE_MAX_WIDTH / float(w)
                frame = cv2.resize(
                    frame,
                    (NOTIFY_IMAGE_MAX_WIDTH, max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(
                log_dir, "error_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg"
            )
            cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, NOTIFY_IMAGE_QUALITY])
            return path
        except Exception as e:
            logger.debug(f"screenshot save failed: {e}")
            return None

    def _notify(self, title: str, message: str, success: bool = False):
        """
        Push a notification through the configured notifier, attaching a
        screenshot when possible. Does nothing (and skips the capture) unless a
        channel is configured, and never raises.
        """
        if not getattr(self.notifier, "is_configured", False):
            return
        try:
            image = self._capture_screenshot()
            self.notifier.notify(
                title,
                message,
                image_path=image,
                priority="default" if success else "high",
                tags="white_check_mark" if success else "warning",
            )
        except Exception as e:
            logger.debug(f"notification dispatch failed: {e}")

    # ---- Sleeping with stop awareness ----

    def _sleep(self, seconds: float, why: str | None = None):
        """
        Sleep in small chunks so we can exit quickly when stop is requested.
        Preserves timing close to the original notebook sleep calls.
        When `why` is given, logs the actual elapsed time afterward (DEBUG,
        file only).
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop_event.is_set():
                break
            time.sleep(0.1)
        if why is not None:
            logger.debug(
                f"waited {time.time() - (deadline - seconds):.2f}s "
                f"(target {seconds:.2f}s) for {why}"
            )

    # ---- Fixed button helpers (normalized coords, no YOLO) ----

    def _button_coords(self, name: str):
        """
        Convert a normalized control (fractions of the screen, 0..1) to pixel
        coordinates using the current capture resolution.
        """
        norm = self.controls.get(name)
        if not norm or "x" not in norm or "y" not in norm:
            raise KeyError(name)

        if not self.window.win_width or not self.window.win_height:
            self.window.get_rect()
        return (
            int(norm["x"] * self.window.win_width),
            int(norm["y"] * self.window.win_height),
        )

    def _press_button(self, name: str, wait_after: float | None = None):
        """
        Tap a calibrated UI button and wait the configured amount afterward.
        There is no YOLO fallback: if the button was never calibrated the farm
        loop refuses to start, so missing entries here are a programming error
        / stale settings. An explicit wait_after overrides the settings value.
        """
        try:
            x, y = self._button_coords(name)
        except KeyError:
            raise RuntimeError(f"'{name}' is not calibrated") from None
        norm = self.controls.get(name, {})
        wait = self.waits.get(name, DEFAULT_WAITS.get(name, 0.5)) if wait_after is None else wait_after
        self._log(
            f"tap {name} at ({x}, {y})"
            f" [{norm.get('x', 0):.3f}, {norm.get('y', 0):.3f}] wait {wait:g}s"
        )
        self.auto.click(x, y)
        self._sleep(wait)

    # ---- Vision helpers (YOLO/OCR for content that moves per base) ----

    def _scan_treasures(self):
        """
        Replicates:
        detect(classin=['gold','elixir','d_elixir'], listMode=True, store_value=True)
        Returns dict: {class_name: str_value, ...}
        """
        max_retries = self.config.max_retries
        targets = [self.config.GOLD, self.config.ELIXIR, self.config.D_ELIXIR]

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            if attempt == 1:
                self._sleep(self.waits["scan_treasures_first"], why="first treasure scan")
            else:
                self._sleep(self.waits["scan_treasures_retry"], why="treasure scan retry")

            frame = self.window.capture_window()
            details = self.vision.detect_with_details(frame, targets)
            if not details:
                continue

            result = {}
            for cls in targets:
                if cls in details:
                    bbox = details[cls][0]["bbox"]
                    result[cls] = self.vision.read_number(frame, bbox)

            if result:
                logger.debug(f"base treasures read: {result}")
                return result

        raise TreasureScanFailed(
            f"Could not find treasures {targets} after {max_retries} attempts"
        )

    def _scan_base_layout(self):
        """
        Scan for the storehouses available to attack. The fixed UI buttons
        (surr / s_goblin / nxt_btn) are calibrated controls now, so only the
        per-base storehouse labels stay on YOLO.
        Returns dict: {class_name: [(cx_rel, cy_rel), ...], ...}
        """
        max_retries = self.config.max_retries
        targets = [
            self.config.ELIXIRS,
            self.config.GOLDS,
            self.config.D_ELIXIRS,
        ]

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            if attempt == 1:
                self._sleep(self.waits["scan_base_first"], why="base layout scan")
            else:
                self._sleep(self.waits["scan_base_retry"], why="base layout scan retry")

            frame = self.window.capture_window()
            details = self.vision.detect_with_details(frame, targets)
            if details:
                return {
                    cls: [item["center"] for item in items]
                    for cls, items in details.items()
                }

        raise ScanFailed(
            f"Could not find base layout {targets} after {max_retries} attempts"
        )

    def _scan_post_game(self):
        """
        Read the post-battle loot (YOLO boxes -> OCR values), driven by data
        rather than a fixed pause.

        The first frames after confirming the surrender are usually still
        rendering (blank loot, or only some labels drawn), and a single early
        read therefore reports 0 even for a successful attack. Since the summary
        is static while the bot holds on it, poll for the whole timeout and keep
        the maximum value read per resource: OCR misses read 0, so the max over
        the window recovers the real number once the screen settles. Returns
        early once all three resources are non-zero; at timeout returns the best
        (possibly partial) values so the raid still counts what it could read.

        The boxes are detected at a slightly lower confidence than the global
        default (a missed Dark Elixir box is common) and the summary's Return
        Home button is used as a settle signal: once it is drawn and any resource
        has a real value, the read stops, so a genuinely empty resource does not
        force a full-timeout wait.
        """
        targets = [
            self.config.POST_GOLD,
            self.config.POST_ELIXIR,
            self.config.POST_D_ELIXIR,
        ]
        interval = self.waits["scan_post_interval"]
        deadline = time.time() + self.waits["scan_post_timeout"]
        # The post-battle labels are small and low-contrast; at the default
        # confidence a real box (often Dark Elixir) is missed, while pushing the
        # confidence much lower starts inventing boxes, so use 0.35 but never go
        # below 0.3 even if config.conf is set lower.
        post_conf = max(0.3, min(self.config.conf, 0.35))
        summary_marker = self.config.RETURN_HOME
        best = {}
        polls = 0

        while True:
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            frame = self.window.capture_window()
            details = self.vision.detect_with_details(
                frame, targets + [summary_marker], conf=post_conf
            )
            polls += 1

            kept = []
            for cls in targets:
                for item in details.get(cls, []):
                    bbox = item["bbox"]
                    # Skip a box that is really the same row as one already read
                    # (the model sometimes tags one row as two resources).
                    if any(_iou(bbox, seen) > _BOX_OVERLAP_IOU for seen in kept):
                        continue
                    kept.append(bbox)
                    value = int(self.vision.read_number(frame, bbox))
                    if value > best.get(cls, 0):
                        best[cls] = value

            logger.debug(f"post-game poll {polls}: {best}")
            self._dump_post_frame(frame, polls)

            if all(best.get(cls, 0) > 0 for cls in targets):
                result = {cls: str(v) for cls, v in best.items()}
                logger.debug(f"post-game loot read: {result} (after {polls} poll(s))")
                return result

            # The summary is on screen once its Return Home button is drawn; if we
            # already have a real number, stop now. A resource still at 0 is
            # genuinely empty rather than unreadable, so polling the rest of the
            # window would only waste time.
            if details.get(summary_marker) and any(v > 0 for v in best.values()):
                result = {cls: str(v) for cls, v in best.items()}
                logger.debug(
                    f"post-game loot read: {result} "
                    f"(summary up after {polls} poll(s))"
                )
                return result

            if time.time() >= deadline:
                if not any(v > 0 for v in best.values()):
                    self._log(
                        f"post-battle loot not readable after "
                        f"{self.waits['scan_post_timeout']:g}s; counted as 0"
                    )
                    return {}
                result = {cls: str(v) for cls, v in best.items()}
                logger.debug(
                    f"post-game loot read: {result} (partial after {polls} poll(s))"
                )
                return result

            self._sleep(interval, why="post-game loot poll")

    def _dump_post_frame(self, frame, poll):
        """
        Diagnostic only: when the ``COC_POST_DEBUG`` environment variable points
        at a directory, save every post-game poll frame there as a full-res PNG
        so the post-battle screen can be inspected offline.
        """
        debug_dir = os.environ.get("COC_POST_DEBUG")
        if not debug_dir:
            return
        try:
            os.makedirs(debug_dir, exist_ok=True)
            path = os.path.join(
                debug_dir,
                "post_" + time.strftime("%Y%m%d_%H%M%S") + f"_poll{poll}.png",
            )
            cv2.imwrite(path, frame)
        except Exception as e:
            logger.debug(f"post-frame dump failed: {e}")

    # ---- Attack logic ----

    def _provider_counts(self, base: dict) -> dict:
        """Number of detected stores per provider class in ``base``."""
        return {
            cls: len(base.get(cls, []))
            for cls in (self.config.GOLDS, self.config.ELIXIRS, self.config.D_ELIXIRS)
        }

    def _count_providers_now(self) -> int | None:
        """
        Best-effort single-shot provider count for the pre-surrender reading.
        Unlike ``_scan_base_layout`` it does not retry and does **not** check the
        stop event, so it still reads after a manual stop. Returns the total
        store count, or ``None`` if the frame can't be read (ADB down, etc.).
        """
        try:
            frame = self.window.capture_window()
            details = self.vision.detect_with_details(
                frame,
                [self.config.GOLDS, self.config.ELIXIRS, self.config.D_ELIXIRS],
            )
            return sum(self._provider_counts(details).values())
        except Exception as e:
            logger.debug(f"pre-surrender provider scan failed: {e}")
            return None

    def _attack_rows(self, base: dict) -> list:
        """
        Guest-pixel y of every sweep row. Rows come from all three provider
        classes (gold/elixir/dark), keep only the ones at least
        ``_ATTACK_ROW_MARGIN`` above the surr button (so a sweep never taps
        surr), and merge rows closer than ``_ATTACK_ROW_TOLERANCE`` so a store is
        not swept twice.
        """
        _, limit = self._button_coords(self.config.SURR_BTN)
        rows = []
        for cls in (self.config.GOLDS, self.config.ELIXIRS, self.config.D_ELIXIRS):
            for item in base.get(cls, []):
                y = item[1]
                if y >= limit - _ATTACK_ROW_MARGIN:
                    continue
                if any(abs(y - seen) <= _ATTACK_ROW_TOLERANCE for seen in rows):
                    continue
                rows.append(y)
        return rows

    def _sweep_rows(self, rows: list) -> int:
        """
        Deploy goblins across every row and return the total tap count. A failing
        tap is logged with its coordinate and re-raised (aborting the session).
        """
        started = time.time()
        total_taps = 0
        for k in rows:
            row_taps = 0
            for a in range(
                self.window.win_left,
                self.window.win_width - _SWEEP_RIGHT_MARGIN,
                _SWEEP_STEP,
            ):
                logger.debug(f"tap sweep at ({a}, {k})")
                try:
                    self.auto.click(a, k)
                except Exception as e:
                    logger.error(f"deploy tap failed at ({a}, {k}): {e}")
                    raise
                row_taps += 1
            total_taps += row_taps
            self._log(f"swept row y={k}: {row_taps} taps")
        self._log(
            f"attack sweep done: {len(rows)} row(s), {total_taps} taps "
            f"in {time.time() - started:.1f}s"
        )
        return total_taps

    def _initiate_attack(self, base: dict):
        """
        Deploy goblin waves until the resource providers are drained. ``base``
        comes from ``_scan_base_layout``: ``{class_name: [(cx, cy), ...], ...}``;
        goblin and surr are calibrated controls, not part of the scan.

        The count of providers (gold/elixir/dark stores) seen at scan time is the
        baseline. Each wave deploys goblins across every provider row, then the
        base is re-scanned and the remaining provider count compared: if at least
        ``attack.drain_ratio`` (default 80%) are gone the loop stops, otherwise
        another wave is deployed — until ``attack.time_limit`` seconds (default
        90, measured from the first sweep) have passed, a Stop is requested, or a
        re-scan fails. There is no wave cap: the clock is the only guaranteed
        stop because the bot cannot read remaining troops.

        A Stop request (F12) is honored before the first deploy and between
        waves, but never mid-sweep, so a started sweep always completes and troops
        are not left half-deployed. The final pause before the caller surrenders
        is ``waits.attack_settle``.
        """
        if self._stop_event.is_set():
            self._log("attack skipped: stop requested before deploy")
            return

        drain_ratio = float(self.attack.get("drain_ratio", 0.8))
        time_limit = float(self.attack.get("time_limit", 90.0))
        wave_settle = self.waits.get("attack_wave_settle", 5.0)

        counts = self._provider_counts(base)
        total = sum(counts.values())
        self._log(
            f"providers at scan: {total} "
            f"(gold {counts[self.config.GOLDS]} / "
            f"elixir {counts[self.config.ELIXIRS]} / "
            f"dark {counts[self.config.D_ELIXIRS]})"
        )

        current = base
        started = None
        wave = 0
        remaining = total
        while True:
            wave += 1
            rows = self._attack_rows(current)
            goblin = self._button_coords(self.config.S_GOBLIN)
            self._log(f"wave {wave}: sweeping {len(rows)} row(s)")
            self._log(f"tap s_goblin (deploy) at ({goblin[0]}, {goblin[1]})")
            self.auto.click(goblin)
            if started is None:
                started = time.time()
            self._sweep_rows(rows)

            self._sleep(wave_settle, why="attack wave settle")

            if self._stop_event.is_set():
                self._log("stop requested between waves; surrendering")
                break

            elapsed = time.time() - started
            if elapsed >= time_limit:
                self._log(
                    f"attack time limit reached ({elapsed:.0f}s >= "
                    f"{time_limit:g}s, wave {wave}); surrendering"
                )
                break

            if total == 0:
                self._log("no providers detected; not re-scanning")
                break

            try:
                current = self._scan_base_layout()
            except ScanFailed as e:
                self._log(f"post-wave rescan failed ({e}); surrendering")
                break

            counts = self._provider_counts(current)
            remaining = sum(counts.values())
            drained = 1.0 - (remaining / total)
            self._log(
                f"wave {wave}: providers {remaining}/{total} remaining "
                f"({drained * 100:.0f}% drained)"
            )
            if drained >= drain_ratio:
                self._log(
                    f"drain target reached ({drained * 100:.0f}% >= "
                    f"{drain_ratio * 100:.0f}%) after {wave} wave(s)"
                )
                break

        # Final pre-surrender reading: how many providers are left right now.
        # A manual stop lands here (the wave loop breaks normally); a hard error
        # raises earlier and skips this, so an erroring raid is not recorded.
        drained = None
        if total > 0:
            now = self._count_providers_now()
            fresh = now is not None
            if fresh:
                remaining = now
            remaining = max(0, min(remaining, total))
            drained = 1.0 - (remaining / total)
            self._log(
                f"drain at surrender: providers {remaining}/{total} remaining "
                f"({drained * 100:.0f}% drained"
                + ("" if fresh else ", last known")
                + ")"
            )

        settle = self.waits.get("attack_settle", 5.0)
        self._sleep(settle, why="attack settle before surrender")
        return drained

    # ---- Farm loop (preserved notebook behaviour) ----

    def _farm_loop(self, targets, goals):
        gtarget, etarget, detarget = targets
        g_goal, e_goal, de_goal = goals

        st_time = time.time()
        totals = {"gold": 0, "elixir": 0, "delixir": 0}
        raid_drains = []

        # Warm up the models up front so there is no stall mid-raid.
        self._set_status("loading")
        try:
            self._log("Loading YOLO + OCR models...")
            self.vision.load()
            self._log("Models ready")

            self._set_status("farming")
            scan_streak = 0
            scan_limit = max(1, int(self.recovery.get("scan_restart_limit", 3)))
            zero_loot_streak = 0

            while not self._stop_event.is_set():
                # Check total goals
                if (
                    totals["gold"] > g_goal
                    and totals["elixir"] > e_goal
                    and totals["delixir"] > de_goal
                ):
                    e_time = time.time()
                    mins = (e_time - st_time) // 60
                    msg = f"Completed in {mins} min"
                    self._log(msg)
                    self._alert(msg)
                    self._notify(
                        "COC Auto-Farm: completed",
                        f"Gold {totals['gold']} | Elixir {totals['elixir']} | "
                        f"Dark {totals['delixir']}, in {mins} min"
                        + self._drain_average(raid_drains),
                        success=True,
                    )
                    break

                # Phase 1: navigate to attack (calibrated fixed buttons)
                for btn in [
                    self.config.ATTACK_BTN_HOME,
                    self.config.FIND_BTN,
                    self.config.ATTACK_BTN,
                ]:
                    if self._stop_event.is_set():
                        break
                    self._press_button(btn)

                if self._stop_event.is_set():
                    break

                # Phase 2: search and decide
                gave_up = False
                search = True
                while search and not self._stop_event.is_set():
                    try:
                        base = self._scan_base_layout()
                        base_treasure = self._scan_treasures()
                    except ScanFailed as e:
                        scan_streak += 1
                        # Notify on every failure, attaching a screenshot of the
                        # failing screen (captured here, before the recovery taps
                        # below, so the image is not the post-recovery screen).
                        self._notify(
                            f"COC Auto-Farm: scan failed "
                            f"({scan_streak}/{scan_limit})",
                            f"{e}"
                            + (" (stopping)" if scan_streak >= scan_limit else "")
                            + (
                                self._drain_average(raid_drains)
                                if scan_streak >= scan_limit
                                else ""
                            ),
                        )
                        # The resource row is often hidden behind a dialog or a
                        # lingering battle; on the very first resource-scan
                        # failure, tap surr to back out and restart Phase 1.
                        # From the second failure on, use the retry spots.
                        if isinstance(e, TreasureScanFailed) and scan_streak == 1:
                            self._log(
                                f"resource scan failed ({scan_streak}/{scan_limit}): "
                                f"{e}; tapping surr and restarting phase 1"
                            )
                            self._press_button(self.config.SURR_BTN)
                        else:
                            self._log(
                                f"scan failed ({scan_streak}/{scan_limit}): {e}; "
                                "backing out (retry_btn_1/2/3) and restarting"
                            )
                            self._press_button(self.config.RETRY_BTN_1)
                            self._press_button(self.config.RETRY_BTN_2)
                            self._press_button(self.config.RETRY_BTN_3)
                        if scan_streak >= scan_limit:
                            mins = (time.time() - st_time) // 60
                            self._alert(
                                f"error: {e}, in {mins} min "
                                f"(scan failed {scan_streak}x, stopping)"
                            )
                            gave_up = True
                        search = False
                        break

                    # A successful scan clears the consecutive-failure streak.
                    scan_streak = 0

                    current_gold = int(base_treasure.get(self.config.GOLD, 0))
                    current_elixir = int(base_treasure.get(self.config.ELIXIR, 0))
                    current_delixir = int(base_treasure.get(self.config.D_ELIXIR, 0))

                    self._log(
                        f"base loot: Gold {current_gold} | "
                        f"Elixir {current_elixir} | Dark {current_delixir}"
                    )

                    if (
                        current_gold >= gtarget
                        and current_delixir >= detarget
                        and current_elixir >= etarget
                    ):
                        # Attack
                        drained = self._initiate_attack(base)

                        self._press_button(self.config.SURR_BTN)
                        self._press_button(self.config.SURR_CONF)

                        post_game = self._scan_post_game()
                        g_gain = int(post_game.get(self.config.POST_GOLD, 0))
                        e_gain = int(post_game.get(self.config.POST_ELIXIR, 0))
                        de_gain = int(post_game.get(self.config.POST_D_ELIXIR, 0))
                        totals["gold"] += g_gain
                        totals["elixir"] += e_gain
                        totals["delixir"] += de_gain
                        self._log(
                            f"loot this raid: Gold +{g_gain} | Elixir +{e_gain} | "
                            f"Dark +{de_gain}  (totals Gold {totals['gold']} | "
                            f"Elixir {totals['elixir']} | Dark {totals['delixir']})"
                        )
                        self._show_totals(totals)

                        # Only count a raid once it has fully completed: the loot
                        # read landed in totals. If the session errors during
                        # surrender or the post-game read, this never runs, so the
                        # error-time average covers only completed raids.
                        if drained is not None:
                            raid_drains.append(drained)

                        # Watchdog: a raid that collected nothing at all usually
                        # means the post-battle read failed (e.g. a missed
                        # surrender) rather than a genuinely empty base, so a run
                        # of them means the bot is desynced.
                        if g_gain == 0 and e_gain == 0 and de_gain == 0:
                            zero_loot_streak += 1
                        else:
                            zero_loot_streak = 0

                        self._press_button(self.config.RETURN_HOME)

                        if zero_loot_streak >= scan_limit:
                            mins = (time.time() - st_time) // 60
                            self._alert(
                                f"error: no loot read for {zero_loot_streak} raids "
                                f"in a row, in {mins} min"
                            )
                            self._notify(
                                "COC Auto-Farm: no loot read",
                                f"{zero_loot_streak} raids in a row collected 0 "
                                f"loot; stopping after {mins} min"
                                + self._drain_average(raid_drains),
                            )
                            gave_up = True

                        search = False
                    else:
                        # Next base (calibrated button; low loot -> skip)
                        self._press_button(self.config.NXT_BTN)

                if gave_up:
                    self._set_status("error")
                    break

        except RuntimeError as e:
            if "Bot stopped" in str(e):
                self._log("Bot stopped cleanly")
            else:
                self._log(f"error: {e}")
                mins = (time.time() - st_time) // 60
                self._alert(f"error: {e}, in {mins} min")
                self._notify(
                    "COC Auto-Farm: error",
                    f"{e} (after {mins} min)" + self._drain_average(raid_drains),
                )
            self._set_status("error")
        except Exception as e:
            self._log(f"error: {e}")
            mins = (time.time() - st_time) // 60
            self._alert(f"error: {e}, in {mins} min")
            self._notify(
                "COC Auto-Farm: error",
                f"{e} (after {mins} min)" + self._drain_average(raid_drains),
            )
            self._set_status("error")
        finally:
            self._set_status("stopped")
            self._show_totals(totals)
            self._log(self._drain_summary(raid_drains))
