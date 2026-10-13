from dataclasses import dataclass


@dataclass
class AppConfig:
    model_path: str = "models/best.pt"
    conf: float = 0.5
    # None = auto-detect (use CUDA when available). Set True/False to force.
    gpu: bool | None = None
    max_retries: int = 4

    # ADB background mode (the only mode). Coordinates from the vision layer
    # are guest pixels; no window focus/visibility is required.
    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    adb_path: str = ""  # empty = auto-detect (PATH / platform-tools / LDPlayer)

    # YOLO class labels used across the bot
    ATTACK_BTN_HOME: str = "attack_btn_home"
    FIND_BTN: str = "find_btn"
    ATTACK_BTN: str = "attack_btn"

    # Resource text labels (top bar during search)
    GOLD: str = "gold"
    ELIXIR: str = "elixir"
    D_ELIXIR: str = "d_elixir"

    # Base layout labels
    S_GOBLIN: str = "s_goblin"
    ELIXIRS: str = "elixirs"
    NXT_BTN: str = "nxt_btn"
    GOLDS: str = "golds"
    D_ELIXIRS: str = "d_elixirs"

    # Post-battle / surrender labels
    SURR_BTN: str = "surr"
    SURR_CONF: str = "surr_conf"

    # Scan-failure recovery: spots tapped (one each) before retrying
    RETRY_BTN_1: str = "retry_btn_1"
    RETRY_BTN_2: str = "retry_btn_2"
    RETRY_BTN_3: str = "retry_btn_3"

    POST_GOLD: str = "post_gold"
    POST_ELIXIR: str = "post_elixir"
    POST_D_ELIXIR: str = "post_d_elixir"
    RETURN_HOME: str = "return_home"
