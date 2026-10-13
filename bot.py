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
}

# ntfy.sh caps attachments at 2 MB, so notification captures are downscaled and
# JPEG-encoded to stay comfortably under that limit.
NOTIFY_IMAGE_MAX_WIDTH = 1280
NOTIFY_IMAGE_QUALITY = 75

# Recovery/beat-the-runtime knobs (settings.json -> "recovery").
DEFAULT_RECOVERY = {
    "scan_restart_limit": 3,
}


class ScanFailed(RuntimeError):
    """A layout/treasure scan could not read the resources after its retries.

    Unlike other RuntimeErrors (ADB failure, uncalibrated button, clean stop),
    this is treated as recoverable: the farm loop taps the retry spots and
    restarts, and only alerts after several failures in a row.
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

        raise ScanFailed(
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
        rather than a fixed pause: capture immediately and keep re-reading every
        `scan_post_interval` seconds until the loot values come back, then return
        them at once. If nothing is readable within `scan_post_timeout`, warn and
        return {} so the raid counts as 0 (the caller adds via .get(..., 0)).
        """
        targets = [self.config.POST_GOLD, self.config.POST_ELIXIR, self.config.POST_D_ELIXIR]
        interval = self.waits["scan_post_interval"]
        deadline = time.time() + self.waits["scan_post_timeout"]
        started = time.time()
        polls = 0

        while True:
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            frame = self.window.capture_window()
            details = self.vision.detect_with_details(frame, targets)
            polls += 1

            if details:
                result = {}
                for cls in targets:
                    if cls in details:
                        bbox = details[cls][0]["bbox"]
                        result[cls] = self.vision.read_number(frame, bbox)
                if result:
                    logger.debug(f"post-game loot read: {result}")
                    logger.debug(
                        f"post-game loot ready after {polls} poll(s) "
                        f"({time.time() - started:.1f}s)"
                    )
                    return result

            if time.time() >= deadline:
                self._log(
                    f"post-battle loot not readable after "
                    f"{self.waits['scan_post_timeout']:g}s; counted as 0"
                )
                return {}

            self._sleep(self.waits["scan_post_interval"], why="post-game loot poll")

    # ---- Attack logic (preserved verbatim) ----

    def _initiate_attack(self, base: dict):
        """
        Original initiate_attack logic.
        base dict comes from _scan_base_layout():
          {class_name: [(cx, cy), ...], ...}
        goblin and surr are calibrated controls (not part of the scan).
        """
        _, limit = self._button_coords(self.config.SURR_BTN)
        attack_coords = []
        for a in base.get(self.config.GOLDS, []):
            if a[1] < limit - 100:
                attack_coords.append(a[1])

        self._log(f"Attack coords: {attack_coords}")
        goblin = self._button_coords(self.config.S_GOBLIN)
        self._log(f"tap s_goblin (deploy) at ({goblin[0]}, {goblin[1]})")
        self.auto.click(goblin)

        for k in attack_coords:
            # Exact formula from notebook (preserved even if it looks like width/right-edge mix)
            row_taps = 0
            for a in range(self.window.win_left, self.window.win_width - 100, 50):
                if self._stop_event.is_set():
                    break
                logger.debug(f"tap sweep at ({a}, {k})")
                self.auto.click(a, k)
                row_taps += 1
            self._log(f"swept row y={k}: {row_taps} taps")
            if self._stop_event.is_set():
                break
        time.sleep(5.0)  # original notebook had a hardcoded sleep here

    # ---- Farm loop (preserved notebook behaviour) ----

    def _farm_loop(self, targets, goals):
        gtarget, etarget, detarget = targets
        g_goal, e_goal, de_goal = goals

        st_time = time.time()
        totals = {"gold": 0, "elixir": 0, "delixir": 0}

        # Warm up the models up front so there is no stall mid-raid.
        self._set_status("loading")
        try:
            self._log("Loading YOLO + OCR models...")
            self.vision.load()
            self._log("Models ready")

            self._set_status("farming")
            scan_streak = 0
            scan_limit = max(1, int(self.recovery.get("scan_restart_limit", 3)))

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
                        f"{msg} — Gold {totals['gold']} | "
                        f"Elixir {totals['elixir']} | Dark {totals['delixir']}",
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
                            self._notify(
                                "COC Auto-Farm: scan failing",
                                f"{e} — failed {scan_streak}x in a row; "
                                f"stopping after {mins} min",
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
                        self._initiate_attack(base)

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

                        self._press_button(self.config.RETURN_HOME)
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
                self._notify("COC Auto-Farm: error", f"{e} (after {mins} min)")
            self._set_status("error")
        except Exception as e:
            self._log(f"error: {e}")
            mins = (time.time() - st_time) // 60
            self._alert(f"error: {e}, in {mins} min")
            self._notify("COC Auto-Farm: error", f"{e} (after {mins} min)")
            self._set_status("error")
        finally:
            self._set_status("stopped")
            self._show_totals(totals)
