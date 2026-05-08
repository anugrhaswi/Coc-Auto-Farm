from ultralytics import YOLO
import easyocr
import numpy as np


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
                self.model = YOLO(self.config.model_path)
            except Exception as e:
                raise RuntimeError(f"Failed to load YOLO model from '{self.config.model_path}': {e}")
        
        if self.reader is None:
            try:
                self.reader = easyocr.Reader(['en'], gpu=self.config.gpu)
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

    def read_number(self, frame, bbox):
        """
        OCR a region of the frame to extract a number.
        
        Args:
            frame: np.ndarray, BGR format
            bbox: tuple (x1, y1, x2, y2) - region to OCR
        
        Returns:
            str: OCR'd text, cleaned (digits only)
        """
        if self.reader is None:
            self.load()
        
        x1, y1, x2, y2 = bbox
        region = frame[y1:y2, x1:x2]
        
        if region.size == 0:
            return "0"
        
        # OCR with digit allowlist
        ocr_result = self.reader.readtext(region, detail=0, allowlist='0123456789')
        
        # Return first result or "0" if empty
        if ocr_result:
            return ocr_result[0]
        return "0"
