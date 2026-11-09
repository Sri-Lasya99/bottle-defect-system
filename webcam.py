"""
Limca Bottle Defect Detection System - Webcam Stream & Processing Module
Handles:
  - OpenCV MacBook built-in webcam capture (index 0)
  - Thread-safe frame reading
  - Real-time bottle detection and temporal smoothing
  - HUD overlay with guide box, status pill, and confidence display
  - Fallback frame generator if camera is busy or permissions are pending
"""

import time
import threading
import numpy as np
import cv2
from predict import BottleDetector, PredictionSmoother


class WebcamStream:
    def __init__(self, camera_index=0, width=640, height=480, detector=None):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.cap = None
        self.is_running = False
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_annotated_frame = None
        self.thread = None
        self.fps = 0.0
        self.frame_count = 0
        self.start_time = None
        self.camera_error = None

        # Prediction engine
        self.detector = detector if detector else BottleDetector()
        self.smoother = PredictionSmoother(window_size=8)
        self.latest_prediction = {
            "status": "NO_BOTTLE",
            "label": "None",
            "confidence": 0.0,
            "probabilities": {"Defective": 0.0, "Non_Defective": 0.0},
            "message": "CAMERA STOPPED",
            "bottle_present": False
        }

    def start(self):
        """Start webcam capture thread."""
        with self.lock:
            if self.is_running:
                return True, "Camera already running."

            self.camera_error = None
            self.cap = self._open_camera(self.camera_index)

            if self.cap is None or not self.cap.isOpened():
                # Attempt camera index 1 if 0 fails
                alt_cap = self._open_camera(1)
                if alt_cap and alt_cap.isOpened():
                    self.cap = alt_cap
                    self.camera_index = 1
                else:
                    self.camera_error = "Could not open MacBook webcam. Check macOS Camera permissions."
                    print(f"[Webcam] Error: {self.camera_error}")

            self.is_running = True
            self.start_time = time.time()
            self.frame_count = 0
            self.smoother.clear()

            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            return True, "Camera started successfully."

    def _open_camera(self, index):
        """Attempt to open camera with OpenCV using AVFOUNDATION on macOS."""
        try:
            # cv2.CAP_AVFOUNDATION is native for macOS
            cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
            if not cap.isOpened():
                cap = cv2.VideoCapture(index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, 30)
                return cap
        except Exception as e:
            print(f"[Webcam] Exception opening camera index {index}: {e}")
        return None

    def stop(self):
        """Stop webcam capture thread."""
        with self.lock:
            self.is_running = False
            if self.cap:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None

            self.latest_prediction = {
                "status": "STOPPED",
                "label": "None",
                "confidence": 0.0,
                "probabilities": {"Defective": 0.0, "Non_Defective": 0.0},
                "message": "CAMERA STOPPED",
                "bottle_present": False
            }
            return True, "Camera stopped."

    def _capture_loop(self):
        """Background thread reading and processing frames continuously."""
        prev_time = time.time()

        while self.is_running:
            frame = None
            if self.cap and self.cap.isOpened():
                ret, raw_frame = self.cap.read()
                if ret and raw_frame is not None:
                    # Mirror horizontal feed for natural webcam viewing
                    frame = cv2.flip(raw_frame, 1)
                    if frame.shape[1] != self.width or frame.shape[0] != self.height:
                        frame = cv2.resize(frame, (self.width, self.height))

            if frame is None:
                # If physical camera is unavailable/denied, generate synthetic test frame
                frame = self._generate_fallback_frame()
                time.sleep(0.04)

            # Measure FPS
            now = time.time()
            dt = now - prev_time
            prev_time = now
            if dt > 0:
                instant_fps = 1.0 / dt
                self.fps = round(self.fps * 0.9 + instant_fps * 0.1, 1)
            self.frame_count += 1

            # Run inference periodically (every 2 frames to maintain 30+ FPS)
            if self.frame_count % 2 == 0:
                raw_pred = self.detector.predict(frame, check_presence=True)
                self.smoother.add(raw_pred)
                self.latest_prediction = self.smoother.get_smoothed_result()

            # Annotate frame with HUD
            annotated = self._draw_hud(frame.copy(), self.latest_prediction)

            with self.lock:
                self.latest_frame = frame
                self.latest_annotated_frame = annotated

    def _generate_fallback_frame(self):
        """Creates an informative dashboard frame when physical webcam is not active."""
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        img[:] = (24, 28, 36)

        # Draw a simulated bottle silhouette / guide
        cx, cy = self.width // 2, self.height // 2
        cv2.rectangle(img, (cx - 70, cy - 140), (cx + 70, cy + 140), (45, 55, 72), 2)
        cv2.circle(img, (cx, cy - 160), 25, (45, 55, 72), 2)

        title = "WEBCAM FEED WAITING" if not self.camera_error else "WEBCAM PERMISSION NEEDED"
        cv2.putText(img, title, (self.width // 2 - 170, 70), cv2.FONT_HERSHEY_DUPLEX, 0.75, (0, 210, 255), 2)

        msg1 = "Click 'Start Camera' below to activate."
        if self.camera_error:
            msg1 = "Grant Camera access in macOS: System Settings -> Privacy & Security -> Camera."
        cv2.putText(img, msg1, (40, self.height - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 174, 192), 1)

        return img

    def _draw_hud(self, frame, prediction):
        """Overlay bottle positioning guide, status banner, and confidence HUD."""
        h, w = frame.shape[:2]

        # 1. Bottle positioning guide box (center of frame)
        gx1, gx2 = int(w * 0.28), int(w * 0.72)
        gy1, gy2 = int(h * 0.12), int(h * 0.88)

        status = prediction.get("status", "NO_BOTTLE")
        confidence = prediction.get("confidence", 0.0)

        # Color codes: BGR
        if status == "NON-DEFECTIVE":
            color = (46, 204, 113)    # Bright Green
            status_text = "NON-DEFECTIVE"
            badge_text = "PASS"
        elif status == "DEFECTIVE":
            color = (46, 46, 235)     # Bright Red
            status_text = "DEFECTIVE"
            badge_text = "DEFECT"
        elif status == "UNCERTAIN":
            color = (0, 191, 255)     # Amber / Gold
            status_text = "REPOSITION BOTTLE"
            badge_text = "UNCERTAIN"
        else:
            color = (130, 140, 150)   # Neutral Gray
            status_text = "NO BOTTLE"
            badge_text = "READY"

        # Corner reticles on guide box
        reticle_len = 25
        t = 3
        # Top-left
        cv2.line(frame, (gx1, gy1), (gx1 + reticle_len, gy1), color, t)
        cv2.line(frame, (gx1, gy1), (gx1, gy1 + reticle_len), color, t)
        # Top-right
        cv2.line(frame, (gx2, gy1), (gx2 - reticle_len, gy1), color, t)
        cv2.line(frame, (gx2, gy1), (gx2, gy1 + reticle_len), color, t)
        # Bottom-left
        cv2.line(frame, (gx1, gy2), (gx1 + reticle_len, gy2), color, t)
        cv2.line(frame, (gx1, gy2), (gx1, gy2 - reticle_len), color, t)
        # Bottom-right
        cv2.line(frame, (gx2, gy2), (gx2 - reticle_len, gy2), color, t)
        cv2.line(frame, (gx2, gy2), (gx2, gy2 - reticle_len), color, t)

        # Subtle dashed-look center guide box
        cv2.rectangle(frame, (gx1, gy1), (gx2, gy2), color, 1, cv2.LINE_AA)

        # 2. Header banner (dark translucent background)
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 54), (18, 22, 30), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

        # Live tag
        cv2.circle(frame, (20, 27), 6, (0, 255, 120), -1)
        cv2.putText(frame, "LIVE CAM", (34, 32), cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 255, 255), 1)

        # Status text in header
        cv2.putText(frame, f"STATUS: {status_text}", (140, 33), cv2.FONT_HERSHEY_DUPLEX, 0.58, color, 2)

        # FPS counter
        cv2.putText(frame, f"{self.fps} FPS", (w - 75, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 174, 192), 1)

        # 3. Footer HUD with Confidence bar (if bottle present)
        if prediction.get("bottle_present", False):
            overlay_bot = frame.copy()
            cv2.rectangle(overlay_bot, (0, h - 42), (w, h), (18, 22, 30), -1)
            cv2.addWeighted(overlay_bot, 0.75, frame, 0.25, 0, frame)

            conf_str = f"Confidence: {confidence:.1f}%"
            cv2.putText(frame, conf_str, (20, h - 16), cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 255, 255), 1)

            # Confidence bar
            bar_x1 = 200
            bar_x2 = w - 20
            bar_w = bar_x2 - bar_x1
            bar_fill = int(bar_w * (confidence / 100.0))
            cv2.rectangle(frame, (bar_x1, h - 26), (bar_x2, h - 14), (50, 55, 65), -1)
            cv2.rectangle(frame, (bar_x1, h - 26), (bar_x1 + bar_fill, h - 14), color, -1)

        return frame

    def get_jpeg_frame(self, annotated=True):
        """Encode latest frame as JPEG bytes for HTTP multipart streaming."""
        with self.lock:
            target_frame = self.latest_annotated_frame if annotated else self.latest_frame
            if target_frame is None:
                target_frame = self._generate_fallback_frame()

        ret, buffer = cv2.imencode(".jpg", target_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ret:
            return None
        return buffer.tobytes()

    def capture_snapshot(self):
        """
        Manually capture and analyze the current frame.
        Returns: (raw_bgr, annotated_bgr, prediction_dict)
        """
        with self.lock:
            frame = self.latest_frame.copy() if self.latest_frame is not None else None

        if frame is None:
            frame = self._generate_fallback_frame()

        prediction = self.detector.predict(frame, check_presence=True)
        annotated = self._draw_hud(frame.copy(), prediction)
        return frame, annotated, prediction

    def get_status(self):
        """Return webcam status info."""
        return {
            "is_running": self.is_running,
            "camera_index": self.camera_index,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "error": self.camera_error,
            "current_prediction": self.latest_prediction
        }
