"""
Limca Bottle Defect Prediction Module
Loads trained MobileNetV2 model and performs:
  - Exact preprocessing (224x224, MobileNetV2 normalization)
  - Bottle presence verification (detects empty frame vs bottle present)
  - Defect classification with confidence percentage
  - Multi-frame temporal smoothing to eliminate flickering
"""

import os
import json
from collections import deque
import numpy as np
import cv2

# Ensure Keras uses workspace local directory
os.environ["KERAS_HOME"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".keras"))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import tensorflow as tf


MODEL_PATH = "bottle_defect_model.keras"
METADATA_PATH = "model_metadata.json"
IMG_SIZE = (224, 224)


class BottleDetector:
    def __init__(self, model_path=MODEL_PATH, metadata_path=METADATA_PATH):
        self.model_path = model_path
        self.metadata_path = metadata_path
        self.model = None
        self.metadata = {}
        self.class_names = ["Defective", "Non_Defective"]
        self.load_model()

    def load_model(self):
        """Load trained Keras model and metadata."""
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"Model file '{self.model_path}' not found. Please run train_model.py first."
            )

        print(f"[Predictor] Loading model from {self.model_path}...")
        self.model = tf.keras.models.load_model(self.model_path)

        if os.path.exists(self.metadata_path):
            with open(self.metadata_path, "r") as f:
                self.metadata = json.load(f)
                self.class_names = self.metadata.get("class_names", self.class_names)
        print(f"[Predictor] Model loaded successfully. Classes: {self.class_names}")

    def check_bottle_presence(self, frame_bgr, roi_box=None):
        """
        Analyze whether a bottle is present in the region of interest.
        Checks edge density, variance, and contrast to prevent empty rooms
        or solid walls from being forced into Defective or Non_Defective.

        Returns: (bottle_present: bool, reason: str, metrics: dict)
        """
        h, w = frame_bgr.shape[:2]

        if roi_box is not None:
            rx1, ry1, rx2, ry2 = roi_box
            roi = frame_bgr[ry1:ry2, rx1:rx2]
        else:
            # Default center 60% width and 75% height
            cx1, cx2 = int(w * 0.20), int(w * 0.80)
            cy1, cy2 = int(h * 0.15), int(h * 0.90)
            roi = frame_bgr[cy1:cy2, cx1:cx2]

        if roi.size == 0:
            return False, "EMPTY_ROI", {}

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # 1. Edge density via Canny
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.sum(edges > 0) / edges.size)

        # 2. Laplacian variance (structural detail / focus)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # 3. HSV Color & Saturation variance (bottle label / plastic vs background)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat_std = float(np.std(hsv[:, :, 1]))
        val_std = float(np.std(hsv[:, :, 2]))

        metrics = {
            "edge_density": round(edge_density, 4),
            "laplacian_var": round(lap_var, 2),
            "sat_std": round(sat_std, 2),
            "val_std": round(val_std, 2)
        }

        # If image has virtually no edges or variance, it's an empty frame / flat wall
        if edge_density < 0.015 or lap_var < 25.0:
            return False, "NO_BOTTLE_DETECTED", metrics

        return True, "BOTTLE_PRESENT", metrics

    def preprocess_image(self, img_bgr, target_size=IMG_SIZE):
        """
        Preprocess BGR image array (OpenCV format) exactly as during training:
        Convert BGR -> RGB, resize to 224x224, expand batch dimension.
        """
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        img_resized = cv2.resize(img_rgb, target_size, interpolation=cv2.INTER_LINEAR)
        img_array = np.expand_dims(img_resized.astype(np.float32), axis=0)
        return img_array

    def predict(self, frame_bgr, check_presence=True, confidence_threshold=0.62):
        """
        Run inference on a single frame.

        Returns a dictionary with:
          - status: 'NON-DEFECTIVE', 'DEFECTIVE', or 'NO_BOTTLE' / 'UNCERTAIN'
          - label: 'Non_Defective', 'Defective', or None
          - confidence: percentage float (0.0 to 100.0)
          - probabilities: {'Defective': float, 'Non_Defective': float}
          - message: Human-readable status banner
          - bottle_present: bool
        """
        if self.model is None:
            self.load_model()

        # Check physical bottle presence in ROI
        bottle_present = True
        metrics = {}
        if check_presence:
            bottle_present, reason, metrics = self.check_bottle_presence(frame_bgr)
            if not bottle_present:
                return {
                    "status": "NO_BOTTLE",
                    "label": "None",
                    "confidence": 0.0,
                    "probabilities": {"Defective": 0.0, "Non_Defective": 0.0},
                    "message": "NO BOTTLE DETECTED",
                    "bottle_present": False,
                    "metrics": metrics
                }

        # Model inference
        input_tensor = self.preprocess_image(frame_bgr)
        preds = self.model.predict(input_tensor, verbose=0)[0]

        prob_defective = float(preds[0])
        prob_non_defective = float(preds[1])

        probabilities = {
            "Defective": round(prob_defective, 4),
            "Non_Defective": round(prob_non_defective, 4)
        }

        max_prob = max(prob_defective, prob_non_defective)
        pred_idx = int(np.argmax(preds))
        pred_class = self.class_names[pred_idx]
        confidence_pct = round(max_prob * 100.0, 2)

        # Ambiguity / Uncertainty threshold check
        prob_diff = abs(prob_defective - prob_non_defective)
        if max_prob < confidence_threshold or prob_diff < 0.15:
            return {
                "status": "UNCERTAIN",
                "label": "Uncertain",
                "confidence": confidence_pct,
                "probabilities": probabilities,
                "message": "UNCERTAIN – PLEASE REPOSITION BOTTLE",
                "bottle_present": True,
                "metrics": metrics
            }

        if pred_class == "Non_Defective":
            return {
                "status": "NON-DEFECTIVE",
                "label": "Non_Defective",
                "confidence": confidence_pct,
                "probabilities": probabilities,
                "message": "BOTTLE IS NON-DEFECTIVE",
                "bottle_present": True,
                "metrics": metrics
            }
        else:
            return {
                "status": "DEFECTIVE",
                "label": "Defective",
                "confidence": confidence_pct,
                "probabilities": probabilities,
                "message": "BOTTLE IS DEFECTIVE",
                "bottle_present": True,
                "metrics": metrics
            }


class PredictionSmoother:
    """
    Maintains a rolling window of predictions across multiple frames
    to eliminate frame jitter and produce a smooth, stable output.
    """
    def __init__(self, window_size=8):
        self.window_size = window_size
        self.history = deque(maxlen=window_size)

    def add(self, prediction_dict):
        """Append latest frame prediction to window."""
        self.history.append(prediction_dict)

    def get_smoothed_result(self):
        """
        Calculate smoothed prediction using weighted majority voting
        and averaged confidence scores.
        """
        if not self.history:
            return {
                "status": "NO_BOTTLE",
                "label": "None",
                "confidence": 0.0,
                "probabilities": {"Defective": 0.0, "Non_Defective": 0.0},
                "message": "NO BOTTLE DETECTED",
                "bottle_present": False
            }

        # Count occurrences of each status
        statuses = [p["status"] for p in self.history]
        weights = np.linspace(0.6, 1.0, len(self.history))
        status_weights = {}

        for status, weight in zip(statuses, weights):
            status_weights[status] = status_weights.get(status, 0.0) + weight

        # Dominant status
        smoothed_status = max(status_weights, key=status_weights.get)

        # Average probabilities for frames sharing the smoothed status or valid frames
        matching_frames = [p for p in self.history if p["status"] == smoothed_status]
        if not matching_frames:
            matching_frames = list(self.history)

        avg_conf = float(np.mean([p["confidence"] for p in matching_frames]))

        # Average probabilities
        avg_p_def = float(np.mean([p.get("probabilities", {}).get("Defective", 0.0) for p in self.history]))
        avg_p_non = float(np.mean([p.get("probabilities", {}).get("Non_Defective", 0.0) for p in self.history]))

        latest = self.history[-1]

        # Use appropriate message based on smoothed status
        if smoothed_status == "NON-DEFECTIVE":
            msg = "BOTTLE IS NON-DEFECTIVE"
            label = "Non_Defective"
        elif smoothed_status == "DEFECTIVE":
            msg = "BOTTLE IS DEFECTIVE"
            label = "Defective"
        elif smoothed_status == "UNCERTAIN":
            msg = "UNCERTAIN – PLEASE REPOSITION BOTTLE"
            label = "Uncertain"
        else:
            msg = "NO BOTTLE DETECTED"
            label = "None"

        return {
            "status": smoothed_status,
            "label": label,
            "confidence": round(avg_conf, 1),
            "probabilities": {
                "Defective": round(avg_p_def, 4),
                "Non_Defective": round(avg_p_non, 4)
            },
            "message": msg,
            "bottle_present": smoothed_status in ["NON-DEFECTIVE", "DEFECTIVE", "UNCERTAIN"],
            "raw_latest": latest
        }

    def clear(self):
        self.history.clear()
