"""
Limca Bottle Defect Detection System - Webcam Stream & Automated Inspection Module
Handles:
  - Built-in webcam capture (DirectShow on Windows / AVFoundation on macOS / V4L2 on Linux)
  - Thread-safe continuous frame capture
  - Bottle entry & exit detection state machine (detects each bottle once per entry)
  - Prevents duplicate enqueuing while bottle remains visible on conveyor
  - HUD overlay with guide box, status pill, and confidence display
  - Automated inspection event dispatch to ESP32 conveyor sorting pipeline
"""

import time
import threading
import sys
import numpy as np
import cv2
from predict import BottleDetector, PredictionSmoother


class WebcamStream:
    def __init__(self, camera_index=0, width=640, height=480, detector=None,
                 on_bottle_inspected=None):
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
        self.smoother = PredictionSmoother(window_size=6)
        self.on_bottle_inspected = on_bottle_inspected

        # Automated Bottle Entry / Exit State Machine
        # States: 'IDLE', 'INSPECTED', 'WAITING_EXIT'
        self.bottle_tracking_state = "IDLE"
        self.consecutive_present = 0
        self.consecutive_absent = 0
        self.min_presence_frames = 2  # Stable presence required to trigger inspection
        self.min_absence_frames = 3   # Stable absence required before new bottle can be inspected
        self.last_inspected_verdict = None
        self.last_inspected_time = 0.0

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
                # Attempt alternative camera index 1 if index 0 fails
                alt_cap = self._open_camera(1)
                if alt_cap and alt_cap.isOpened():
                    self.cap = alt_cap
                    self.camera_index = 1
                else:
                    self.camera_error = "Could not open camera. Please check camera connection and permissions."
                    print(f"[Webcam] Error: {self.camera_error}")

            self.is_running = True
            self.start_time = time.time()
            self.frame_count = 0
            self.smoother.clear()
            self.bottle_tracking_state = "IDLE"
            self.consecutive_present = 0
            self.consecutive_absent = 0

            self.thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.thread.start()
            return True, "Camera started successfully."

    def _open_camera(self, index):
        """Attempt to open camera with OpenCV using appropriate backend per OS."""
        try:
            if sys.platform == "win32":
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(index)
            elif sys.platform == "darwin":
                cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
            else:
                cap = cv2.VideoCapture(index)

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

            # 1. Bottle presence verification
            bottle_present, reason, metrics = self.detector.check_bottle_presence(frame)

            # 2. Automated Bottle Tracking State Machine
            self._update_bottle_lifecycle(frame, bottle_present)

            # 3. Continuous real-time display prediction
            if self.frame_count % 2 == 0:
                raw_pred = self.detector.predict(frame, check_presence=True)
                self.smoother.add(raw_pred)
                self.latest_prediction = self.smoother.get_smoothed_result()

            # 4. Annotate frame with HUD
            annotated = self._draw_hud(frame.copy(), self.latest_prediction)

            with self.lock:
                self.latest_frame = frame
                self.latest_annotated_frame = annotated

    def _update_bottle_lifecycle(self, frame, bottle_present: bool):
        """
        Ensures each bottle travelling along the conveyor is inspected exactly once.
        Workflow:
          - IDLE: waits for a bottle to enter ROI.
          - When bottle present for min_presence_frames: trigger AI inspection callback.
          - Transitions to WAITING_EXIT: suppresses duplicate triggers while bottle remains visible.
          - When bottle absent for min_absence_frames: resets back to IDLE for next bottle.
        """
        now = time.time()

        if self.bottle_tracking_state == "IDLE":
            if bottle_present:
                self.consecutive_present += 1
                self.consecutive_absent = 0
                if self.consecutive_present >= self.min_presence_frames:
                    # NEW BOTTLE ARRIVED IN INSPECTION REGION
                    self.bottle_tracking_state = "WAITING_EXIT"
                    self.consecutive_present = 0
                    self.last_inspected_time = now

                    # Run single deterministic classification for queueing
                    t_cam = now
                    pred = self.detector.predict(frame, check_presence=False)
                    t_pred = time.time()
                    self.last_inspected_verdict = pred.get("status", "UNCERTAIN")

                    # Dispatch automated inspection callback
                    if self.on_bottle_inspected:
                        def worker(snapshot, p, tc, tp):
                            try:
                                self.on_bottle_inspected(snapshot, p, tc, tp)
                            except Exception as ex:
                                print(f"[Webcam] Error in bottle inspected callback: {ex}")

                        threading.Thread(
                            target=worker,
                            args=(frame.copy(), pred, t_cam, t_pred),
                            daemon=True
                        ).start()
            else:
                self.consecutive_present = 0

        elif self.bottle_tracking_state == "WAITING_EXIT":
            if not bottle_present:
                self.consecutive_absent += 1
                self.consecutive_present = 0
                if self.consecutive_absent >= self.min_absence_frames:
                    # BOTTLE HAS DEPARTED CAMERA REGION - READY FOR NEXT BOTTLE
                    self.bottle_tracking_state = "IDLE"
                    self.consecutive_absent = 0
            else:
                self.consecutive_absent = 0

    def _generate_fallback_frame(self):
        """Creates an informative dashboard frame when physical webcam is not active."""
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        img[:] = (24, 28, 36)

        cx, cy = self.width // 2, self.height // 2
        cv2.rectangle(img, (cx - 70, cy - 140), (cx + 70, cy + 140), (45, 55, 72), 2)
        cv2.circle(img, (cx, cy - 160), 25, (45, 55, 72), 2)

        title = "WEBCAM FEED WAITING" if not self.camera_error else "CAMERA ACCESS ERROR"
        cv2.putText(img, title, (self.width // 2 - 160, 70), cv2.FONT_HERSHEY_DUPLEX, 0.75, (0, 210, 255), 2)

        msg1 = "Click 'Start Camera' below to activate inspection stream."
        if self.camera_error:
            msg1 = f"Error: {self.camera_error}"
        cv2.putText(img, msg1, (30, self.height - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 174, 192), 1)

        return img

    def _draw_hud(self, frame, prediction):
        """Overlay bottle positioning guide, status banner, and confidence HUD."""
        h, w = frame.shape[:2]

        # 1. Bottle positioning guide box (center region of conveyor camera)
        gx1, gx2 = int(w * 0.28), int(w * 0.72)
        gy1, gy2 = int(h * 0.12), int(h * 0.88)

        status = prediction.get("status", "NO_BOTTLE")
        confidence = prediction.get("confidence", 0.0)

        # Color codes: BGR
        if status == "NON-DEFECTIVE":
            color = (46, 204, 113)    # Green
            status_text = "NON-DEFECTIVE"
        elif status == "DEFECTIVE":
            color = (46, 46, 235)     # Red
            status_text = "DEFECTIVE"
        elif status == "UNCERTAIN":
            color = (0, 191, 255)     # Amber / Gold
            status_text = "UNCERTAIN"
        else:
            color = (130, 140, 150)   # Neutral Gray
            status_text = "WAITING BOTTLE"

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

        cv2.rectangle(frame, (gx1, gy1), (gx2, gy2), color, 1, cv2.LINE_AA)

        # 2. Header banner (dark translucent background)
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 54), (18, 22, 30), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

        # Live tag
        cv2.circle(frame, (20, 27), 6, (0, 255, 120), -1)
        cv2.putText(frame, "CAM LIVE", (34, 32), cv2.FONT_HERSHEY_DUPLEX, 0.48, (255, 255, 255), 1)

        # Status text in header
        cv2.putText(frame, f"AI: {status_text}", (135, 33), cv2.FONT_HERSHEY_DUPLEX, 0.55, color, 2)

        # State machine indicator
        state_str = "READY" if self.bottle_tracking_state == "IDLE" else "IN TRANSIT"
        cv2.putText(frame, state_str, (w - 180, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 210, 225), 1)

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
            "bottle_tracking_state": self.bottle_tracking_state,
            "current_prediction": self.latest_prediction
        }
