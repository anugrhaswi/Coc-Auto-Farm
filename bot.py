import logging
import queue
import threading
import time

from config import AppConfig

logger = logging.getLogger(__name__)


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
    ):
        self.config = config
        self.window = window_manager
        self.vision = vision_manager
        self.auto = automation_controller
        self.msg_queue = msg_queue

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

    # ---- Sleeping with stop awareness ----

    def _sleep(self, seconds: float):
        """
        Sleep in small chunks so we can exit quickly when stop is requested.
        Preserves timing close to the original notebook sleep calls.
        """
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop_event.is_set():
                break
            time.sleep(0.1)

    # ---- Vision helpers that replicate old detect() ----

    def _detect_single(self, class_name: str):
        """
        Replicates listMode=False behaviour:
        - retries up to max_retries
        - returns absolute screen coordinates (x, y)
        """
        max_retries = self.config.max_retries
        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            if attempt == 1:
                self._sleep(1)
            else:
                self._sleep(0.5)

            frame = self.window.capture_window()
            detections = self.vision.detect(frame, [class_name])
            if class_name in detections and detections[class_name]:
                cx, cy = detections[class_name][0]
                # Convert to absolute screen coords exactly like the old notebook
                return cx + self.window.win_left, cy + self.window.win_top

        raise RuntimeError(
            f"detect() could not find '{class_name}' after {max_retries} attempts"
        )

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
                self._sleep(1)
            else:
                self._sleep(0.5)

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
                return result

        raise RuntimeError(
            f"Could not find treasures {targets} after {max_retries} attempts"
        )

    def _scan_base_layout(self):
        """
        Replicates:
        detect(classin=['end_btn','s_goblin','elixirs','nxt_btn','golds','d_elixirs'], listMode=True)
        Returns dict: {class_name: [(cx_rel, cy_rel), ...], ...}
        """
        max_retries = self.config.max_retries
        targets = [
            self.config.END_BTN,
            self.config.S_GOBLIN,
            self.config.ELIXIRS,
            self.config.NXT_BTN,
            self.config.GOLDS,
            self.config.D_ELIXIRS,
        ]

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            if attempt == 1:
                self._sleep(3)
            else:
                self._sleep(2)

            frame = self.window.capture_window()
            details = self.vision.detect_with_details(frame, targets)
            if details:
                return {
                    cls: [item["center"] for item in items]
                    for cls, items in details.items()
                }

        raise RuntimeError(
            f"Could not find base layout {targets} after {max_retries} attempts"
        )

    def _scan_post_game(self):
        """
        Replicates:
        detect(classin=['post_gold','post_elixir','post_d_elixir'], listMode=True, store_value=True)
        """
        max_retries = self.config.max_retries
        targets = [self.config.POST_GOLD, self.config.POST_ELIXIR, self.config.POST_D_ELIXIR]

        for attempt in range(1, max_retries + 1):
            if self._stop_event.is_set():
                raise RuntimeError("Bot stopped")

            if attempt == 1:
                self._sleep(1)
            else:
                self._sleep(0.5)

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
                return result

        raise RuntimeError(
            f"Could not find post-game resources after {max_retries} attempts"
        )

    # ---- Attack logic (preserved verbatim) ----

    def _initiate_attack(self, base: dict):
        """
        Original initiate_attack logic.
        base dict comes from _scan_base_layout():
          {class_name: [(cx, cy), ...], ...}
        """
        limit = base[self.config.END_BTN][0][1]
        attack_coords = []
        for a in base.get(self.config.GOLDS, []):
            if a[1] < limit - 100:
                attack_coords.append(a[1])

        self._log(f"Attack coords: {attack_coords}")
        self.auto.click(base[self.config.S_GOBLIN][0])

        for k in attack_coords:
            # Exact formula from notebook (preserved even if it looks like width/right-edge mix)
            for a in range(self.window.win_left, self.window.win_width - 100, 50):
                if self._stop_event.is_set():
                    return
                self.auto.click(a, k)

    # ---- Farm loop (preserved notebook behaviour) ----

    def _farm_loop(self, targets, goals):
        gtarget, etarget, detarget = targets
        g_goal, e_goal, de_goal = goals

        st_time = time.time()
        totals = {"gold": 0, "elixir": 0, "delixir": 0}

        self._set_status("farming")
        try:
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
                    break

                # Phase 1: navigate to attack
                for btn in [
                    self.config.ATTACK_BTN_HOME,
                    self.config.FIND_BTN,
                    self.config.ATTACK_BTN,
                ]:
                    if self._stop_event.is_set():
                        break
                    coord = self._detect_single(btn)
                    self.auto.click(coord)
                    self._sleep(0.5)

                if self._stop_event.is_set():
                    break

                # Phase 2: search and decide
                search = True
                while search and not self._stop_event.is_set():
                    base = self._scan_base_layout()
                    base_treasure = self._scan_treasures()

                    current_gold = int(base_treasure.get(self.config.GOLD, 0))
                    current_elixir = int(base_treasure.get(self.config.ELIXIR, 0))
                    current_delixir = int(base_treasure.get(self.config.D_ELIXIR, 0))

                    self._log(f"Gold rn: {current_gold}")

                    if (
                        current_gold >= gtarget
                        and current_delixir >= detarget
                        and current_elixir >= etarget
                    ):
                        # Attack
                        self._initiate_attack(base)

                        end = base.get(self.config.END_BTN)
                        if end:
                            self.auto.click(end[0])
                            self._sleep(0.5)
                        else:
                            self._log("end_btn not found in base scan, skipping click")

                        surr = self._detect_single(self.config.SURR_CONF)
                        self.auto.click(surr)
                        self._sleep(1)

                        post_game = self._scan_post_game()
                        totals["gold"] += int(post_game.get(self.config.POST_GOLD, 0))
                        totals["elixir"] += int(post_game.get(self.config.POST_ELIXIR, 0))
                        totals["delixir"] += int(
                            post_game.get(self.config.POST_D_ELIXIR, 0)
                        )
                        self._show_totals(totals)

                        self.auto.click(self._detect_single(self.config.RETURN_HOME))
                        search = False
                    else:
                        # Next base
                        nxt = base.get(self.config.NXT_BTN)
                        if nxt:
                            self.auto.click(nxt[0])
                            self._sleep(1.5)
                        else:
                            # Original notebook behaviour: rescan layout (no-op except consuming a loop)
                            self._scan_base_layout()

        except RuntimeError as e:
            if "Bot stopped" in str(e):
                self._log("Bot stopped cleanly")
            else:
                self._log(f"error: {e}")
                mins = (time.time() - st_time) // 60
                self._alert(f"error: {e}, in {mins} min")
            self._set_status("error")
        except Exception as e:
            self._log(f"error: {e}")
            mins = (time.time() - st_time) // 60
            self._alert(f"error: {e}, in {mins} min")
            self._set_status("error")
        finally:
            self._set_status("stopped")
            self._show_totals(totals)
