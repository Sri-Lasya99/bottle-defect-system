"""
ESP32 Hardware Serial Communication Module
Handles USB serial communication between the Python AI application and ESP32:
  - SG90 servo diverter (Active 180° sweep on defective bottles, 0° rest on non-defective)
  - IR bottle detection sensor (Active LOW on GPIO32)
  - Continuous conveyor belt support (conveyor runs uninterrupted)
  - Thread-safe FIFO verdict queue synchronization between Camera & IR sensor
  - Precise timing & latency tracking (Camera -> Model -> Queue -> IR -> Servo -> Settle)
"""

import re
import time
import threading
from collections import deque
from datetime import datetime
import serial
import serial.tools.list_ports


class ESP32Controller:
    """
    Manages USB Serial link to ESP32.
    Implements the robust FIFO verdict queue protocol:
      - Python sends: QUEUE:DEFECT, QUEUE:PASS, QUEUE:UNCERTAIN, STATUS, SERVO:TEST, CLEAR_QUEUE
      - ESP32 responds: ACK:QUEUED:..., IR_TRIGGER, TRIGGER_SORT:..., SORT_COMPLETE:..., ERR:QUEUE_EMPTY
    Maintains an in-transit bottle pipeline to match physical bottles moving on the
    conveyor (travel time ~6-7 seconds over ~12 cm distance).
    """

    def __init__(self, baud_rate=115200, enabled=False,
                 on_sort_callback=None, on_missing_callback=None):
        self.baud_rate = baud_rate
        self.enabled = enabled
        self.on_sort_callback = on_sort_callback
        self.on_missing_callback = on_missing_callback

        self.serial_conn = None
        self.connected_port = None
        self.lock = threading.Lock()
        self.last_command = "NONE"
        self.last_command_time = None
        self.simulation_log = []
        self.listen_thread = None
        self.is_listening = False

        # In-transit bottle FIFO queue (tracked in Python to match ESP32 hardware queue)
        self.queue_lock = threading.Lock()
        self.in_transit_queue = deque()
        self.active_sorting_bottle = None

        # Hardware real-time states reported by ESP32
        self.hw_queue_count = 0
        self.ir_sensor_active = False  # True when beam is broken (Active LOW)
        self.current_servo_angle = 0   # 0 = Resting, 180 = Pushing
        self.conveyor_status = "RUNNING"  # Conveyor belt runs continuously

        # Timing & Diagnostics statistics
        self.last_travel_time = 0.0
        self.last_prediction_latency = 0.0

    def scan_ports(self):
        """
        Find candidate serial ports across Windows, Linux, and macOS.
        Sorts Windows COM ports numerically (COM1, COM2, ... COM5, COM10).
        """
        raw_ports = []
        try:
            for p in serial.tools.list_ports.comports():
                raw_ports.append(p.device)
        except Exception:
            pass

        def sort_key(port_name):
            match = re.match(r"^COM(\d+)$", port_name, re.IGNORECASE)
            if match:
                return (0, int(match.group(1)))
            return (1, port_name)

        unique_ports = sorted(list(set(raw_ports)), key=sort_key)
        return unique_ports

    def get_port_details(self):
        """Return list of detected ports with detailed descriptions and hardware IDs."""
        details = []
        try:
            for p in serial.tools.list_ports.comports():
                details.append({
                    "port": p.device,
                    "description": p.description or "",
                    "hwid": p.hwid or ""
                })
        except Exception:
            pass
        return details

    def connect(self, port=None):
        """
        Connect to specified serial port or auto-detect first valid ESP32 / USB-UART port.
        Gives priority to COM5 if present on Windows.
        """
        with self.lock:
            if self.serial_conn and self.serial_conn.is_open:
                if port is None or port == self.connected_port:
                    return True, f"Already connected to {self.connected_port}"
                self._disconnect_internal()

            available_ports = self.scan_ports()

            target_port = port
            if not target_port:
                # Prefer COM5 if available, else prefer USB/CP210x/CH340/UART
                if "COM5" in available_ports:
                    target_port = "COM5"
                else:
                    port_details = self.get_port_details()
                    preferred = None
                    for d in port_details:
                        desc_lower = (d["description"] + " " + d["hwid"]).lower()
                        if any(k in desc_lower for k in ["cp210", "ch340", "ch341", "ftdi", "uart", "usb serial", "esp32"]):
                            preferred = d["port"]
                            break
                    target_port = preferred if preferred else (available_ports[0] if available_ports else None)

            if not target_port:
                return False, "No serial COM ports detected. Please plug in ESP32 via USB."

            try:
                print(f"[HardwareSerial] Opening {target_port} at {self.baud_rate} baud...")
                self.serial_conn = serial.Serial(
                    port=target_port,
                    baudrate=self.baud_rate,
                    timeout=0.1,
                    write_timeout=1.0
                )
                # ESP32 auto-resets when DTR/RTS is toggled on opening serial
                time.sleep(1.8)
                try:
                    self.serial_conn.reset_input_buffer()
                    self.serial_conn.reset_output_buffer()
                except Exception:
                    pass

                self.connected_port = target_port
                self._start_listener_internal()

                # Query initial hardware status
                self._send_raw_command("STATUS")

                msg = f"Connected to ESP32 on {target_port}"
                self._log_event(msg)
                print(f"[HardwareSerial] {msg}")
                return True, msg
            except Exception as e:
                self.serial_conn = None
                self.connected_port = None
                err_msg = f"Failed to connect to {target_port}: {str(e)}"
                self._log_event(f"[ERROR] {err_msg}")
                print(f"[HardwareSerial] {err_msg}")
                return False, err_msg

    def disconnect(self):
        """Disconnect active serial link cleanly."""
        with self.lock:
            return self._disconnect_internal()

    def _disconnect_internal(self):
        self._stop_listener_internal()
        if self.serial_conn:
            try:
                if self.serial_conn.is_open:
                    self.serial_conn.close()
            except Exception:
                pass
        prev_port = self.connected_port
        self.serial_conn = None
        self.connected_port = None
        self.enabled = False
        msg = f"Disconnected from {prev_port or 'hardware'}."
        self._log_event(msg)
        return True, msg

    def start_listener(self):
        with self.lock:
            self._start_listener_internal()

    def _start_listener_internal(self):
        if self.is_listening:
            return
        self.is_listening = True
        self.listen_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.listen_thread.start()

    def stop_listener(self):
        with self.lock:
            self._stop_listener_internal()

    def _stop_listener_internal(self):
        self.is_listening = False

    def _read_loop(self):
        """Continuously reads incoming lines from ESP32."""
        line_buffer = ""
        while self.is_listening:
            try:
                conn = self.serial_conn
                if conn and conn.is_open:
                    in_wait = conn.in_waiting
                    raw = conn.read(in_wait if in_wait > 0 else 1)
                    if raw:
                        text = raw.decode("utf-8", errors="ignore")
                        for ch in text:
                            if ch in "\r\n":
                                trimmed = line_buffer.strip()
                                if trimmed:
                                    self._handle_incoming_line(trimmed)
                                line_buffer = ""
                            else:
                                line_buffer += ch
                    else:
                        time.sleep(0.015)
                else:
                    time.sleep(0.1)
            except (serial.SerialException, OSError, PermissionError) as e:
                print(f"[HardwareSerial] Connection lost: {e}")
                self._log_event(f"[DISCONNECTED] ESP32 connection lost: {e}")
                with self.lock:
                    if self.serial_conn:
                        try:
                            self.serial_conn.close()
                        except Exception:
                            pass
                    self.serial_conn = None
                    self.connected_port = None
                    self.is_listening = False
                    self.enabled = False
                break
            except Exception as e:
                time.sleep(0.05)

    def _handle_incoming_line(self, line: str):
        """
        Dispatches messages received from ESP32:
          - ACK:QUEUED:<verdict>:<count>
          - IR_TRIGGER
          - IR_CLEARED
          - TRIGGER_SORT:<verdict>
          - SORT_COMPLETE:<verdict>
          - ERR:QUEUE_EMPTY
          - STATUS:QUEUE=<q>:SERVO=<s>:IR=<i>
        """
        self._log_event(f"[ESP32 IN] {line}")
        # print(f"[HardwareSerial] Received: {line}")

        now = time.time()

        if line.startswith("ACK:QUEUED:"):
            # Update hardware queue count
            parts = line.split(":")
            if len(parts) >= 4:
                try:
                    self.hw_queue_count = int(parts[3])
                except ValueError:
                    pass

        elif line == "IR_TRIGGER":
            self.ir_sensor_active = True
            # print("[HardwareSerial] IR Sensor: Bottle detected (Active LOW)")

        elif line == "IR_CLEARED":
            self.ir_sensor_active = False
            # print("[HardwareSerial] IR Sensor: Bottle cleared (HIGH)")

        elif line.startswith("TRIGGER_SORT:"):
            verdict = line.split(":")[1]
            self.ir_sensor_active = True

            matched_bottle = None
            with self.queue_lock:
                if self.in_transit_queue:
                    matched_bottle = self.in_transit_queue.popleft()
                    self.active_sorting_bottle = matched_bottle

            if matched_bottle:
                matched_bottle["timestamp_ir"] = now
                if matched_bottle.get("timestamp_camera"):
                    self.last_travel_time = round(now - matched_bottle["timestamp_camera"], 2)
                    matched_bottle["travel_time_sec"] = self.last_travel_time

                if verdict == "DEFECT":
                    self.current_servo_angle = 180
                    matched_bottle["status"] = "SORTING"
                    matched_bottle["hardware_action"] = "SERVO ACTUATING (180° DEFLECT)"
                elif verdict == "PASS":
                    self.current_servo_angle = 0
                    matched_bottle["status"] = "PASSED"
                    matched_bottle["hardware_action"] = "PASSED (SERVO REST 0°)"
                elif verdict == "UNCERTAIN":
                    self.current_servo_angle = 0
                    matched_bottle["status"] = "UNCERTAIN"
                    matched_bottle["hardware_action"] = "UNCERTAIN (SERVO REST 0°)"

                if self.on_sort_callback:
                    try:
                        self.on_sort_callback(matched_bottle, "TRIGGER_SORT")
                    except Exception as ex:
                        print(f"[HardwareSerial] Error in sort callback: {ex}")
            else:
                print(f"[HardwareSerial] Warning: TRIGGER_SORT:{verdict} received but Python queue was empty!")

        elif line.startswith("SORT_COMPLETE:"):
            verdict = line.split(":")[1]
            self.current_servo_angle = 0

            target_bottle = self.active_sorting_bottle
            self.active_sorting_bottle = None

            if target_bottle:
                target_bottle["timestamp_sorted"] = now
                if verdict == "DEFECT":
                    target_bottle["status"] = "SORTED"
                    target_bottle["hardware_action"] = "SORTED (PHYSICAL DEFLECT CONFIRMED)"
                elif verdict == "PASS":
                    target_bottle["status"] = "PASSED"
                    target_bottle["hardware_action"] = "PASSED NORMAL"
                elif verdict == "UNCERTAIN":
                    target_bottle["status"] = "UNCERTAIN"
                    target_bottle["hardware_action"] = "UNCERTAIN PASSED"

                if self.on_sort_callback:
                    try:
                        self.on_sort_callback(target_bottle, "SORT_COMPLETE")
                    except Exception as ex:
                        print(f"[HardwareSerial] Error in sort complete callback: {ex}")

        elif line == "ERR:QUEUE_EMPTY":
            # Bottle arrived at IR sensor with NO AI verdict available!
            self.current_servo_angle = 0
            missing_event = {
                "id": f"unpredicted_{int(now)}",
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "status": "MISSING VERDICT",
                "label": "None",
                "confidence": 0.0,
                "probabilities": {},
                "message": "NO AI VERDICT IN QUEUE WHEN IR TRIGGERED",
                "bottle_present": True,
                "hardware_action": "SERVO KEPT AT 0° (SAFE BYPASS)",
                "timestamp_ir": now
            }
            print("[HardwareSerial] WARNING: ERR:QUEUE_EMPTY - Unpredicted bottle passed IR sensor. Servo kept at 0°.")
            if self.on_missing_callback:
                try:
                    self.on_missing_callback(missing_event)
                except Exception as ex:
                    print(f"[HardwareSerial] Error in missing callback: {ex}")

        elif line.startswith("STATUS:"):
            # Parse STATUS:QUEUE=X:SERVO=Y:IR=Z
            try:
                for chunk in line[7:].split(":"):
                    if "=" in chunk:
                        k, v = chunk.split("=", 1)
                        if k == "QUEUE":
                            self.hw_queue_count = int(v)
                        elif k == "SERVO":
                            self.current_servo_angle = int(v)
                        elif k == "IR":
                            self.ir_sensor_active = (v == "ACTIVE")
            except Exception:
                pass

    def queue_verdict(self, verdict: str, bottle_record: dict = None):
        """
        Send QUEUE:<verdict> command to ESP32 immediately after AI classification.
        Maintains order in in_transit_queue to match physical travel on conveyor.
        Valid verdicts: 'DEFECT', 'PASS', 'UNCERTAIN'
        """
        v_clean = verdict.strip().upper()
        if v_clean not in ["DEFECT", "PASS", "UNCERTAIN"]:
            v_clean = "UNCERTAIN"

        cmd = f"QUEUE:{v_clean}"
        now = time.time()

        if bottle_record:
            bottle_record["verdict"] = v_clean
            bottle_record["timestamp_queued"] = now
            bottle_record["status"] = "VERDICT QUEUED"

            if bottle_record.get("timestamp_camera") and bottle_record.get("timestamp_predicted"):
                latency = bottle_record["timestamp_predicted"] - bottle_record["timestamp_camera"]
                self.last_prediction_latency = round(latency, 3)
                bottle_record["ai_latency_sec"] = self.last_prediction_latency
                if latency > 3.0:
                    print(f"[HardwareSerial] WARNING: Prediction latency is high ({self.last_prediction_latency}s)!")

            with self.queue_lock:
                self.in_transit_queue.append(bottle_record)

        # Dispatch command to ESP32 over serial
        success, msg = self.send_command(cmd)
        return success, msg

    def set_enabled(self, enabled: bool):
        """
        Enable or disable hardware command dispatching.
        Starts in safe disabled state (STANDBY).
        """
        with self.lock:
            if enabled:
                is_open = bool(self.serial_conn and self.serial_conn.is_open)
                if not is_open:
                    ports = self.scan_ports()
                    if not ports:
                        self.enabled = False
                        msg = "Cannot enable hardware: No physical ESP32 COM port detected. Connect hardware first."
                        self._log_event(f"[ERROR] {msg}")
                        return False, msg

                    self.lock.release()
                    ok, conn_msg = self.connect()
                    self.lock.acquire()
                    if not ok:
                        self.enabled = False
                        return False, f"Cannot enable hardware: {conn_msg}"

                self.enabled = True
                msg = f"Hardware enabled in PHYSICAL mode on {self.connected_port}."
                self._log_event(msg)
                return True, msg
            else:
                self.enabled = False
                msg = "Hardware disabled (STANDBY mode)."
                self._log_event(msg)
                return True, msg

    def send_command(self, cmd: str):
        """
        Send ASCII command over serial.
        Does NOT report success if hardware is disabled or disconnected.
        """
        cmd_clean = cmd.strip().upper()
        cmd_str = cmd_clean + "\n"

        with self.lock:
            self.last_command = cmd_clean
            self.last_command_time = time.time()

            if not self.enabled:
                log_msg = f"[HARDWARE DISABLED] Command '{cmd_clean}' skipped (Controller in STANDBY)."
                self._log_event(log_msg)
                return False, log_msg

            if not (self.serial_conn and self.serial_conn.is_open):
                log_msg = f"[HARDWARE ERROR] Cannot send '{cmd_clean}': ESP32 is not connected."
                self._log_event(log_msg)
                return False, log_msg

            return self._send_raw_command(cmd_clean)

    def _send_raw_command(self, cmd_clean: str):
        """Internal write holding lock or when connection is established."""
        try:
            cmd_str = cmd_clean + "\n"
            self.serial_conn.write(cmd_str.encode("utf-8"))
            self.serial_conn.flush()
            log_msg = f"[HARDWARE SENT] '{cmd_clean}' -> ESP32 ({self.connected_port})"
            self._log_event(log_msg)
            return True, log_msg
        except Exception as e:
            log_msg = f"[HARDWARE ERROR] Failed to write '{cmd_clean}': {e}"
            self._log_event(log_msg)
            return False, log_msg

    def _log_event(self, msg):
        self.simulation_log.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "message": msg
        })
        if len(self.simulation_log) > 60:
            self.simulation_log.pop(0)

    def test_diverter(self):
        """Send manual SERVO:TEST command to actuate servo diverter arm."""
        return self.send_command("SERVO:TEST")

    def clear_queue(self):
        """Clear hardware and software verdict queues."""
        with self.queue_lock:
            self.in_transit_queue.clear()
            self.active_sorting_bottle = None
        return self.send_command("CLEAR_QUEUE")

    def set_servo_config(self, angle=180, hold_ms=1000):
        """Configure servo pushing angle and hold duration."""
        res1, m1 = self.send_command(f"CONFIG:ANGLE:{angle}")
        res2, m2 = self.send_command(f"CONFIG:HOLD:{hold_ms}")
        return res1 and res2, f"{m1}; {m2}"

    def get_in_transit_bottles(self):
        """Return list of bottles currently travelling on the conveyor."""
        with self.queue_lock:
            return list(self.in_transit_queue)

    def get_status(self):
        """Return diagnostic status of hardware controller."""
        is_open = bool(self.serial_conn and self.serial_conn.is_open)
        available_ports = self.scan_ports()

        if is_open and self.enabled:
            mode = "PHYSICAL"
        elif self.enabled and not is_open:
            mode = "SIMULATED"
        else:
            mode = "STANDBY"

        with self.queue_lock:
            in_transit_count = len(self.in_transit_queue)
            pipeline_summary = [
                {
                    "id": b.get("id"),
                    "verdict": b.get("verdict"),
                    "status": b.get("status"),
                    "age_sec": round(time.time() - b.get("timestamp_camera", time.time()), 1)
                }
                for b in self.in_transit_queue
            ]

        return {
            "enabled": self.enabled,
            "connected": is_open,
            "port": self.connected_port,
            "baud_rate": self.baud_rate,
            "available_ports": available_ports,
            "mode": mode,
            "conveyor_status": self.conveyor_status,
            "ir_sensor_active": self.ir_sensor_active,
            "servo_angle": self.current_servo_angle,
            "hw_queue_count": self.hw_queue_count,
            "in_transit_count": in_transit_count,
            "in_transit_pipeline": pipeline_summary,
            "last_travel_time": self.last_travel_time,
            "last_prediction_latency": self.last_prediction_latency,
            "last_command": self.last_command,
            "last_command_time": self.last_command_time,
            "recent_logs": self.simulation_log[-12:]
        }
