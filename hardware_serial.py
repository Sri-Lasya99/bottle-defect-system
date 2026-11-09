"""
ESP32 Hardware Serial Communication Module
Handles USB serial communication between the Python AI application and ESP32:
  - 12V geared DC conveyor motor
  - Motor driver (L298N / TB6612FNG)
  - SG90 servo diverter
  - IR bottle detection sensor
  - Automated closed-loop trigger callback (listens for 'TRIGGER_INSPECT')
"""

import os
import glob
import time
import threading
import serial
import serial.tools.list_ports


class ESP32Controller:
    """
    Manages USB Serial link to ESP32.
    Includes background read listener for automated IR sensor triggers
    and mock/simulated fallback mode.
    """
    def __init__(self, baud_rate=115200, enabled=False, on_inspect_callback=None):
        self.baud_rate = baud_rate
        self.enabled = enabled
        self.on_inspect_callback = on_inspect_callback
        self.serial_conn = None
        self.connected_port = None
        self.lock = threading.Lock()
        self.last_command = "NONE"
        self.last_command_time = None
        self.simulation_log = []
        self.listen_thread = None
        self.is_listening = False

    def scan_ports(self):
        """Find candidate serial ports on macOS and other platforms."""
        ports = []
        try:
            for p in serial.tools.list_ports.comports():
                ports.append(p.device)
        except Exception:
            pass

        mac_patterns = [
            "/dev/cu.usbserial*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.SLAB_USBtoUART*",
            "/dev/cu.usbmodem*"
        ]
        for pattern in mac_patterns:
            for match in glob.glob(pattern):
                if match not in ports:
                    ports.append(match)

        return sorted(list(set(ports)))

    def connect(self, port=None):
        """Connect to specified serial port or auto-detect first available."""
        with self.lock:
            if self.serial_conn and self.serial_conn.is_open:
                return True, f"Already connected to {self.connected_port}"

            available_ports = self.scan_ports()
            target_port = port if port else (available_ports[0] if available_ports else None)

            if not target_port:
                return False, "No serial ports detected. Operating in Simulated Mode."

            try:
                self.serial_conn = serial.Serial(
                    port=target_port,
                    baudrate=self.baud_rate,
                    timeout=0.1,
                    write_timeout=1.0
                )
                time.sleep(1.5)  # Allow ESP32 reboot delay
                self.connected_port = target_port
                self.start_listener()
                return True, f"Connected to ESP32 on {target_port}"
            except Exception as e:
                self.serial_conn = None
                self.connected_port = None
                return False, f"Failed to connect to {target_port}: {str(e)}"

    def disconnect(self):
        """Disconnect active serial link."""
        self.stop_listener()
        with self.lock:
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            self.serial_conn = None
            self.connected_port = None
            return True, "Disconnected from hardware."

    def start_listener(self):
        """Start background listener thread reading incoming serial lines."""
        if self.is_listening:
            return
        self.is_listening = True
        self.listen_thread = threading.Thread(target=self._read_loop, daemon=True)
        self.listen_thread.start()

    def stop_listener(self):
        self.is_listening = False

    def _read_loop(self):
        """Continuously reads incoming lines from ESP32."""
        line_buffer = ""
        while self.is_listening:
            try:
                if self.serial_conn and self.serial_conn.is_open:
                    data = self.serial_conn.read(self.serial_conn.in_waiting or 1)
                    if data:
                        text = data.decode("utf-8", errors="ignore")
                        for ch in text:
                            if ch in "\r\n":
                                if line_buffer.strip():
                                    self._handle_incoming_line(line_buffer.strip())
                                line_buffer = ""
                            else:
                                line_buffer += ch
                else:
                    time.sleep(0.1)
            except Exception as e:
                time.sleep(0.2)

    def _handle_incoming_line(self, line):
        """Dispatches triggers received from ESP32."""
        self._log_sim(f"[ESP32 IN] {line}")
        print(f"[HardwareSerial] Received: {line}")

        # When bottle arrives at IR sensor in front of webcam:
        if line == "TRIGGER_INSPECT":
            if self.on_inspect_callback:
                print("[HardwareSerial] Triggering automated inspection callback...")
                threading.Thread(target=self.on_inspect_callback, daemon=True).start()

    def set_enabled(self, enabled: bool):
        """Enable or disable hardware command dispatching."""
        self.enabled = enabled
        if self.enabled and not self.serial_conn:
            # Try auto-connecting
            self.connect()
        return self.enabled

    def send_command(self, cmd: str):
        """Send ASCII command over serial (or mock log if simulated)."""
        cmd_clean = cmd.strip()
        cmd_str = cmd_clean + "\n"
        with self.lock:
            self.last_command = cmd_clean
            self.last_command_time = time.time()

            if not self.enabled:
                log_msg = f"[HARDWARE DISABLED] Command '{cmd_clean}' skipped."
                self._log_sim(log_msg)
                return True, log_msg

            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.write(cmd_str.encode("utf-8"))
                    self.serial_conn.flush()
                    log_msg = f"[HARDWARE SENT] '{cmd_clean}' -> ESP32 ({self.connected_port})"
                    self._log_sim(log_msg)
                    return True, log_msg
                except Exception as e:
                    log_msg = f"[HARDWARE ERROR] Failed to write '{cmd_clean}': {e}"
                    self._log_sim(log_msg)
                    return False, log_msg
            else:
                log_msg = f"[SIMULATION] '{cmd_clean}' executed (No ESP32 attached)"
                self._log_sim(log_msg)
                return True, log_msg

    def _log_sim(self, msg):
        self.simulation_log.append({
            "timestamp": time.strftime("%H:%M:%S"),
            "message": msg
        })
        if len(self.simulation_log) > 50:
            self.simulation_log.pop(0)

    def send_verdict(self, status: str):
        """
        Actuate sorting mechanism:
          - 'NON-DEFECTIVE': 'PASS' (conveyor keeps running)
          - 'DEFECTIVE': 'DEFECT' (servo diverter triggers)
        """
        if status == "DEFECTIVE":
            return self.send_command("DEFECT")
        elif status == "NON-DEFECTIVE":
            return self.send_command("PASS")
        else:
            # Uncertain / reposition - pass through or stop
            return self.send_command("PASS")

    def start_conveyor(self):
        return self.send_command("START")

    def stop_conveyor(self):
        return self.send_command("STOP")

    def trigger_diverter(self):
        return self.send_command("DIVERT_TEST")

    def get_status(self):
        """Return diagnostic status of hardware controller."""
        is_open = bool(self.serial_conn and self.serial_conn.is_open)
        available_ports = self.scan_ports()
        return {
            "enabled": self.enabled,
            "connected": is_open,
            "port": self.connected_port,
            "baud_rate": self.baud_rate,
            "available_ports": available_ports,
            "mode": "PHYSICAL" if is_open and self.enabled else ("SIMULATED" if self.enabled else "STANDBY"),
            "last_command": self.last_command,
            "last_command_time": self.last_command_time,
            "recent_logs": self.simulation_log[-10:]
        }
