"""
Limca Bottle Defect Detection System - Flask Backend API & Web Server
Provides:
  - Live MJPEG video stream from MacBook webcam
  - Real-time prediction and confidence metrics
  - Detection history tracking with snapshot persistence
  - Hardware serial integration control for ESP32 conveyor/diverter
  - RESTful APIs for camera control and inspection analytics
"""

import os
import sys
import time
import json
import uuid
import cv2
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from predict import BottleDetector
from webcam import WebcamStream
from hardware_serial import ESP32Controller

# Ensure workspace paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURES_DIR = os.path.join(BASE_DIR, "static", "captures")
os.makedirs(CAPTURES_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# Initialize core services
print("[App] Initializing BottleDetector...")
detector = BottleDetector()
print("[App] Initializing WebcamStream...")
webcam = WebcamStream(detector=detector)
print("[App] Initializing ESP32Controller (Standby Mode)...")
hardware = ESP32Controller(enabled=False)

# In-memory detection history & statistics
history = []
stats = {
    "total_analyzed": 0,
    "total_defective": 0,
    "total_non_defective": 0,
    "total_uncertain": 0
}


def perform_inspection():
    """
    Core inspection routine:
    Captures current webcam frame, evaluates with MobileNetV2,
    records history, and transmits PASS / DEFECT command to ESP32.
    """
    raw_frame, annotated_frame, pred = webcam.capture_snapshot()

    # Generate unique filename
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_id = f"cap_{timestamp_str}_{uuid.uuid4().hex[:6]}"
    filename = f"{capture_id}.jpg"
    filepath = os.path.join(CAPTURES_DIR, filename)

    # Save annotated snapshot
    cv2.imwrite(filepath, annotated_frame)

    # Determine status & update statistics
    status = pred.get("status", "NO_BOTTLE")
    if status in ["NON-DEFECTIVE", "DEFECTIVE", "UNCERTAIN"]:
        stats["total_analyzed"] += 1
        if status == "NON-DEFECTIVE":
            stats["total_non_defective"] += 1
        elif status == "DEFECTIVE":
            stats["total_defective"] += 1
        elif status == "UNCERTAIN":
            stats["total_uncertain"] += 1

    # Send command to ESP32 conveyor/diverter
    hw_success, hw_msg = hardware.send_verdict(status)

    record = {
        "id": capture_id,
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "status": status,
        "label": pred.get("label", "None"),
        "confidence": pred.get("confidence", 0.0),
        "probabilities": pred.get("probabilities", {}),
        "message": pred.get("message", ""),
        "bottle_present": pred.get("bottle_present", False),
        "image_url": f"/static/captures/{filename}",
        "hardware_action": hw_msg
    }

    # Prepend to history (most recent first)
    history.insert(0, record)
    if len(history) > 100:
        history.pop()

    return record, stats


# Link ESP32 automated trigger callback
hardware.on_inspect_callback = perform_inspection


def generate_frames():
    """Generator function for streaming multipart MJPEG video to frontend."""
    while True:
        frame_bytes = webcam.get_jpeg_frame(annotated=True)
        if frame_bytes is not None:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
        time.sleep(0.033)  # ~30 FPS throttle


# ==================== WEB DASHBOARD ====================

@app.route("/")
def index():
    """Serve the Defect Detection Dashboard."""
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    """MJPEG streaming endpoint for HTML <img> tag."""
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ==================== CAMERA APIS ====================

@app.route("/api/camera/start", methods=["POST"])
def start_camera():
    """Start the webcam capture thread."""
    success, msg = webcam.start()
    return jsonify({"success": success, "message": msg, "status": webcam.get_status()})


@app.route("/api/camera/stop", methods=["POST"])
def stop_camera():
    """Stop the webcam capture thread."""
    success, msg = webcam.stop()
    return jsonify({"success": success, "message": msg, "status": webcam.get_status()})


@app.route("/api/camera/status", methods=["GET"])
def camera_status():
    """Return camera state, FPS, error, and current real-time prediction."""
    return jsonify(webcam.get_status())


# ==================== PREDICTION & CAPTURE APIS ====================

@app.route("/api/predict/current", methods=["GET"])
def get_current_prediction():
    """Return the latest smoothed real-time prediction from the webcam feed."""
    return jsonify(webcam.latest_prediction)


@app.route("/api/capture", methods=["POST"])
def capture_and_analyze():
    """
    Manually capture a high-resolution snapshot from the webcam,
    run full model inference, persist image and metadata to history,
    and dispatch hardware actuation if enabled.
    """
    record, current_stats = perform_inspection()
    return jsonify({
        "success": True,
        "record": record,
        "stats": current_stats
    })


@app.route("/api/history", methods=["GET", "DELETE"])
def handle_history():
    """Retrieve or clear detection history."""
    global history, stats
    if request.method == "DELETE":
        history = []
        stats = {
            "total_analyzed": 0,
            "total_defective": 0,
            "total_non_defective": 0,
            "total_uncertain": 0
        }
        return jsonify({"success": True, "message": "History reset."})

    return jsonify({
        "history": history,
        "stats": stats
    })


@app.route("/api/metrics", methods=["GET"])
def get_metrics():
    """Retrieve model metadata, training accuracy, loss, and confusion matrix."""
    meta_path = os.path.join(BASE_DIR, "model_metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r") as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"error": "Model metadata not found. Train the model first."}), 404


# ==================== HARDWARE SERIAL APIS ====================

@app.route("/api/hardware/status", methods=["GET"])
def get_hardware_status():
    """Retrieve ESP32 serial link status."""
    return jsonify(hardware.get_status())


@app.route("/api/hardware/toggle", methods=["POST"])
def toggle_hardware():
    """Toggle hardware actuation on or off."""
    data = request.get_json(silent=True) or {}
    enable = data.get("enable", not hardware.enabled)
    hardware.set_enabled(enable)
    return jsonify(hardware.get_status())


@app.route("/api/hardware/connect", methods=["POST"])
def connect_hardware():
    """Connect to a designated or auto-detected serial port."""
    data = request.get_json(silent=True) or {}
    port = data.get("port", None)
    success, msg = hardware.connect(port)
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


@app.route("/api/hardware/test", methods=["POST"])
def test_hardware():
    """Send manual test command to conveyor or servo diverter."""
    data = request.get_json(silent=True) or {}
    cmd = data.get("command", "STATUS")
    success, msg = hardware.send_command(cmd)
    return jsonify({"success": success, "message": msg})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n[Limca Defect Detection System] Server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
