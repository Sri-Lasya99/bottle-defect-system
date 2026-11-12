"""
Limca Bottle Defect Detection System - Flask Backend API & Web Server
Provides:
  - Live MJPEG video stream from laptop built-in webcam
  - Real-time MobileNetV2 defect classification
  - Automated bottle entry/exit registration & FIFO verdict queue dispatch
  - ESP32 hardware serial synchronization (115200 baud, Active LOW IR, SG90 servo)
  - Continuous conveyor belt support (conveyor never halts during inspection or sorting)
  - Complete lifecycle tracking: CAMERA DETECTED -> AI CLASSIFIED -> VERDICT QUEUED -> IR DETECTED -> SORTING -> SORTED / PASSED / UNCERTAIN / MISSING VERDICT
"""

import os
import sys
import time
import json
import uuid
import threading
import cv2
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, request, send_from_directory
from flask_cors import CORS

from predict import BottleDetector
from webcam import WebcamStream
from hardware_serial import ESP32Controller

# Workspace paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURES_DIR = os.path.join(BASE_DIR, "static", "captures")
os.makedirs(CAPTURES_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)

# Global bottle counter for sequential IDs (e.g. B001, B002)
bottle_sequence_counter = 0
bottle_counter_lock = threading.Lock()

# In-memory detection history & statistics
history = []
history_lock = threading.Lock()

stats = {
    "total_inspected": 0,
    "total_defective": 0,
    "total_non_defective": 0,
    "total_uncertain": 0,
    "total_sorted_defective": 0,
    "total_passed": 0,
    "total_missing_verdicts": 0
}


def get_next_bottle_id():
    global bottle_sequence_counter
    with bottle_counter_lock:
        bottle_sequence_counter += 1
        return f"B{bottle_sequence_counter:03d}"


# ==================== HARDWARE & WEBCAM CALLBACKS ====================

def on_hardware_sort_event(bottle_data, event_type):
    """
    Called by ESP32Controller when:
      - 'TRIGGER_SORT': bottle arrives at IR sensor and verdict is consumed
      - 'SORT_COMPLETE': servo diverter returned to 0° (or pass confirmed)
    """
    b_id = bottle_data.get("id")
    # print(f"[App] Hardware Sort Event: {event_type} for Bottle {b_id} (Status: {bottle_data.get('status')})")

    with history_lock:
        for item in history:
            if item.get("id") == b_id:
                item["status"] = bottle_data.get("status", item["status"])
                item["hardware_action"] = bottle_data.get("hardware_action", item["hardware_action"])
                item["timestamp_ir"] = bottle_data.get("timestamp_ir", item.get("timestamp_ir"))
                item["timestamp_sorted"] = bottle_data.get("timestamp_sorted", item.get("timestamp_sorted"))
                item["travel_time_sec"] = bottle_data.get("travel_time_sec", item.get("travel_time_sec"))

                if event_type == "SORT_COMPLETE":
                    if item.get("verdict") == "DEFECT":
                        item["sort_confirmed"] = True
                        stats["total_sorted_defective"] += 1
                    elif item.get("verdict") == "PASS":
                        stats["total_passed"] += 1
                break


def on_hardware_missing_verdict(missing_event):
    """
    Called when an unpredicted bottle triggers the IR sensor with an empty queue.
    """
    with history_lock:
        stats["total_missing_verdicts"] += 1
        history.insert(0, missing_event)
        if len(history) > 100:
            history.pop()


def on_bottle_inspected_callback(snapshot_frame, pred, t_cam, t_pred):
    """
    Automated inspection routine called when a bottle enters the camera region:
      1. Generates sequential Bottle ID (e.g. B001)
      2. Saves inspection snapshot
      3. Determines verdict: DEFECT, PASS, or UNCERTAIN
      4. Immediately queues verdict to ESP32 FIFO queue over serial
      5. Records full diagnostic event with timestamps
    """
    bottle_id = get_next_bottle_id()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{bottle_id}_{timestamp_str}.jpg"
    filepath = os.path.join(CAPTURES_DIR, filename)

    # Persist snapshot image
    if snapshot_frame is not None and snapshot_frame.size > 0:
        cv2.imwrite(filepath, snapshot_frame)

    pred_status = pred.get("status", "UNCERTAIN")
    if pred_status == "DEFECTIVE":
        verdict = "DEFECT"
    elif pred_status == "NON-DEFECTIVE":
        verdict = "PASS"
    else:
        verdict = "UNCERTAIN"

    # Update stats
    with history_lock:
        stats["total_inspected"] += 1
        if verdict == "DEFECT":
            stats["total_defective"] += 1
        elif verdict == "PASS":
            stats["total_non_defective"] += 1
        else:
            stats["total_uncertain"] += 1

    record = {
        "id": bottle_id,
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "status": "VERDICT QUEUED",
        "label": pred.get("label", "None"),
        "confidence": pred.get("confidence", 0.0),
        "probabilities": pred.get("probabilities", {}),
        "verdict": verdict,
        "message": pred.get("message", ""),
        "bottle_present": pred.get("bottle_present", True),
        "image_url": f"/static/captures/{filename}",
        "hardware_action": "ENQUEUED TO ESP32 FIFO",
        "timestamp_camera": t_cam,
        "timestamp_predicted": t_pred,
        "timestamp_queued": time.time(),
        "timestamp_ir": None,
        "timestamp_sorted": None,
        "travel_time_sec": None,
        "ai_latency_sec": round(t_pred - t_cam, 3),
        "sort_confirmed": False
    }

    # Queue verdict immediately to ESP32
    success, hw_msg = hardware.queue_verdict(verdict, record)
    record["hardware_action"] = hw_msg

    with history_lock:
        history.insert(0, record)
        if len(history) > 100:
            history.pop()

    print(f"[App] Bottle {bottle_id} classified as {pred_status} ({pred.get('confidence', 0):.1f}%). Queued {verdict} to ESP32.")
    return record


# Initialize Core Services
print("[App] Initializing BottleDetector...")
detector = BottleDetector()

print("[App] Initializing ESP32Controller...")
hardware = ESP32Controller(
    baud_rate=115200,
    enabled=False,
    on_sort_callback=on_hardware_sort_event,
    on_missing_callback=on_hardware_missing_verdict
)

print("[App] Initializing WebcamStream with automated inspection callback...")
webcam = WebcamStream(
    detector=detector,
    on_bottle_inspected=on_bottle_inspected_callback
)


def generate_frames():
    """Generator function for streaming multipart MJPEG video to frontend."""
    while True:
        frame_bytes = webcam.get_jpeg_frame(annotated=True)
        if frame_bytes is not None:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
        time.sleep(0.033)


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
    Manually capture a high-resolution snapshot from the webcam.
    Performs visual inspection on demand.
    To prevent duplicate FIFO enqueuing during conveyor operation, manual captures
    do NOT automatically enqueue to ESP32 unless explicitly requested via {enqueue: true}.
    """
    data = request.get_json(silent=True) or {}
    should_enqueue = data.get("enqueue", False)

    raw_frame, annotated_frame, pred = webcam.capture_snapshot()

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_id = f"manual_{timestamp_str}_{uuid.uuid4().hex[:4]}"
    filename = f"{capture_id}.jpg"
    filepath = os.path.join(CAPTURES_DIR, filename)

    if annotated_frame is not None and annotated_frame.size > 0:
        cv2.imwrite(filepath, annotated_frame)
    elif raw_frame is not None and raw_frame.size > 0:
        cv2.imwrite(filepath, raw_frame)

    pred_status = pred.get("status", "NO_BOTTLE")
    hw_msg = "Manual inspection preview (not queued to conveyor)"

    if should_enqueue and pred_status in ["DEFECTIVE", "NON-DEFECTIVE", "UNCERTAIN"]:
        verdict = "DEFECT" if pred_status == "DEFECTIVE" else ("PASS" if pred_status == "NON-DEFECTIVE" else "UNCERTAIN")
        record = {
            "id": capture_id,
            "timestamp": datetime.now().strftime("%I:%M:%S %p"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "VERDICT QUEUED",
            "label": pred.get("label", "None"),
            "confidence": pred.get("confidence", 0.0),
            "probabilities": pred.get("probabilities", {}),
            "verdict": verdict,
            "message": pred.get("message", ""),
            "bottle_present": pred.get("bottle_present", True),
            "image_url": f"/static/captures/{filename}",
            "hardware_action": "ENQUEUED (MANUAL TRIGGER)",
            "timestamp_camera": time.time(),
            "timestamp_predicted": time.time(),
            "timestamp_queued": time.time(),
            "timestamp_ir": None,
            "timestamp_sorted": None,
            "sort_confirmed": False
        }
        success, hw_msg = hardware.queue_verdict(verdict, record)
        with history_lock:
            history.insert(0, record)
            if len(history) > 100:
                history.pop()
    else:
        record = {
            "id": capture_id,
            "timestamp": datetime.now().strftime("%I:%M:%S %p"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "status": "AI CLASSIFIED",
            "label": pred.get("label", "None"),
            "confidence": pred.get("confidence", 0.0),
            "probabilities": pred.get("probabilities", {}),
            "message": pred.get("message", ""),
            "bottle_present": pred.get("bottle_present", False),
            "image_url": f"/static/captures/{filename}",
            "hardware_action": hw_msg,
            "sort_confirmed": False
        }

    return jsonify({
        "success": True,
        "record": record,
        "stats": stats
    })


@app.route("/api/history", methods=["GET", "DELETE"])
def handle_history():
    """Retrieve or clear detection history."""
    global history, stats, bottle_sequence_counter
    if request.method == "DELETE":
        with history_lock:
            history = []
            stats = {
                "total_inspected": 0,
                "total_defective": 0,
                "total_non_defective": 0,
                "total_uncertain": 0,
                "total_sorted_defective": 0,
                "total_passed": 0,
                "total_missing_verdicts": 0
            }
        with bottle_counter_lock:
            bottle_sequence_counter = 0
        hardware.clear_queue()
        return jsonify({"success": True, "message": "History and queues reset."})

    with history_lock:
        return jsonify({
            "history": history,
            "stats": stats
        })


@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Retrieve current inspection statistics."""
    with history_lock:
        return jsonify(stats)


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
    """Retrieve ESP32 serial link status, queue status, IR sensor, and servo angle."""
    hw_status = hardware.get_status()
    hw_status["stats"] = stats
    return jsonify(hw_status)


@app.route("/api/hardware/ports", methods=["GET"])
def get_hardware_ports():
    """Retrieve list of available serial COM ports and connection details."""
    ports = hardware.scan_ports()
    details = hardware.get_port_details()
    return jsonify({
        "ports": ports,
        "details": details,
        "connected_port": hardware.connected_port
    })


@app.route("/api/hardware/toggle", methods=["POST"])
def toggle_hardware():
    """Toggle hardware actuation on or off."""
    data = request.get_json(silent=True) or {}
    enable = data.get("enable", not hardware.enabled)
    success, msg = hardware.set_enabled(enable)
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


@app.route("/api/hardware/connect", methods=["POST"])
def connect_hardware():
    """Connect to a designated or auto-detected serial port (e.g. COM5)."""
    data = request.get_json(silent=True) or {}
    port = data.get("port", None)
    success, msg = hardware.connect(port)
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


@app.route("/api/hardware/disconnect", methods=["POST"])
def disconnect_hardware():
    """Disconnect from ESP32 serial port cleanly."""
    success, msg = hardware.disconnect()
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


@app.route("/api/hardware/test", methods=["POST"])
def test_hardware():
    """Send manual test command to servo diverter or request status."""
    data = request.get_json(silent=True) or {}
    cmd = data.get("command", "STATUS")
    if cmd == "SERVO_TEST":
        success, msg = hardware.test_diverter()
    elif cmd == "CLEAR_QUEUE":
        success, msg = hardware.clear_queue()
    else:
        success, msg = hardware.send_command(cmd)
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


@app.route("/api/hardware/config", methods=["POST"])
def config_hardware():
    """Configure servo angle (default 180) and hold duration (default 1000ms)."""
    data = request.get_json(silent=True) or {}
    angle = int(data.get("angle", 180))
    hold_ms = int(data.get("hold_ms", 1000))
    success, msg = hardware.set_servo_config(angle, hold_ms)
    return jsonify({"success": success, "message": msg, "status": hardware.get_status()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"\n[Limca Defect Detection System] Server starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
