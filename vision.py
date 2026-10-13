import logging

import cv2

logger = logging.getLogger(__name__)

# Characters EasyOCR is allowed to return when reading a resource value.
# Letters are included so an abbreviated value like "1.2M" is not silently
# truncated to "12" by a digits-only allowlist.
_OCR_ALLOWLIST = "0123456789.,KMBkmb"

_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_loot_number(text: str) -> str:
    """
    Convert raw OCR text for a resource value into a plain integer string.

    Handles thousands separators ("1,234,567", "1.234.567") and abbreviated
    values ("650K", "1.2M", "3.5B"). Returns "0" when nothing numeric is found.

    Examples:
        "1,234,567" -> "1234567"
        "+650K"     -> "650000"
        "1.2M"      -> "1200000"
        "Gold"      -> "0"
    """
    if not text:
        return "0"

    cleaned = (
        text.strip().lower().replace(",", "").replace(" ", "").replace("+", "")
    )
    if not cleaned:
        return "0"

    suffix = cleaned[-1]
    if suffix in _SUFFIX_MULTIPLIERS:
        core = cleaned[:-1]
        try:
            value = float(core) * _SUFFIX_MULTIPLIERS[suffix]
        except ValueError:
            digits = "".join(ch for ch in core if ch.isdigit())
            value = float(digits or "0") * _SUFFIX_MULTIPLIERS[suffix]
        return str(int(round(value)))

    # No multiplier: every separator is a thousands separator, so drop all
    # non-digits ("1.234.567" and "1,234,567" both become "1234567").
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    return digits or "0"


class VisionManager:
    """
    Manages YOLO inference and EasyOCR reading.
    Removes globals: model, reader
    Pure inference only — no retry logic, no window handling.
    """

    def __init__(self, config):
        """
        Initialize with config. Models are NOT loaded until load() is called.
        
        Args:
            config: AppConfig with model_path, gpu
        """
        self.config = config
        self.model = None
        self.reader = None
    
    def load(self):
        """
        Lazy-load YOLO model and EasyOCR reader.
        Call this before first detect() or read_number() call.
        """
        if self.model is None:
            try:
                from ultralytics import YOLO
                self.model = YOLO(self.config.model_path)
            except Exception as e:
                raise RuntimeError(f"Failed to load YOLO model from '{self.config.model_path}': {e}")

        if self.reader is None:
            try:
                import torch
                import easyocr
                use_gpu = torch.cuda.is_available() if self.config.gpu is None else self.config.gpu
                self.reader = easyocr.Reader(['en'], gpu=use_gpu)
            except Exception as e:
                raise RuntimeError(f"Failed to load EasyOCR reader: {e}")
    
    def detect(self, frame, class_names, conf=None):
        """
        Run YOLO inference on frame, return center-point detections.

        Args:
            frame: np.ndarray, BGR format (OpenCV compatible)
            class_names: list of class name strings to filter for (e.g., ['gold', 'elixir'])
            conf: confidence threshold. If None, uses config.conf

        Returns:
            dict: {class_name: [(cx, cy), (cx, cy), ...]}
                  with center coordinates relative to frame origin (0,0)
                  (NOT window origin)
        """
        details = self.detect_with_details(frame, class_names, conf)
        return {
            cls: [item["center"] for item in items]
            for cls, items in details.items()
        }

    def detect_with_details(self, frame, class_names, conf=None):
        """
        Run YOLO inference on frame, returning centers and bounding boxes.

        Args:
            frame: np.ndarray, BGR format
            class_names: list of class name strings to filter for
            conf: confidence threshold. If None, uses config.conf

        Returns:
            dict: {class_name: [{"center": (cx, cy), "bbox": (x1, y1, x2, y2)}, ...]}
        """
        if self.model is None:
            self.load()

        if conf is None:
            conf = self.config.conf

        result = self.model.predict(source=frame, conf=conf, verbose=False)
        detections = {}

        for box in result[0].boxes:
            class_id = int(box.cls.item())
            class_name = str(self.model.names[class_id])

            if class_name in class_names:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                cx = x1 + (x2 - x1) / 2
                cy = y1 + (y2 - y1) / 2

                if class_name not in detections:
                    detections[class_name] = []
                detections[class_name].append({
                    "center": (cx, cy),
                    "bbox": (x1, y1, x2, y2)
                })

        return detections

    def _preprocess(self, region):
        """
        Prepare a small resource-value crop for OCR: upscale short crops so the
        glyphs are large enough for the recognizer, then convert to grayscale.
        """
        h, w = region.shape[:2]
        if h and h < 64:
            scale = 64.0 / h
            region = cv2.resize(
                region,
                (max(1, int(w * scale)), 64),
                interpolation=cv2.INTER_CUBIC,
            )
        return cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    def read_number(self, frame, bbox):
        """
        OCR a region of the frame to extract a number.

        Args:
            frame: np.ndarray, BGR format
            bbox: tuple (x1, y1, x2, y2) - region to OCR

        Returns:
            str: the value as a plain integer string (separators removed,
            abbreviations expanded), or "0" if nothing readable.
        """
        if self.reader is None:
            self.load()

        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]

        if region.size == 0:
            return "0"

        ocr_result = self.reader.readtext(
            self._preprocess(region), detail=0, allowlist=_OCR_ALLOWLIST
        )

        # EasyOCR can split a value into several fragments; keep them all.
        raw = " ".join(str(part) for part in ocr_result) if ocr_result else ""
        value = parse_loot_number(raw)
        logger.debug(f"OCR bbox={(x1, y1, x2, y2)} raw={raw!r} -> {value}")
        return value
