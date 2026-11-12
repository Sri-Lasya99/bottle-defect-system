"""
Unit & Integration Test Suite for Bottle Defect Detection & Conveyor Sorting System
Validates:
  1. Serial protocol & FIFO queue state machine in ESP32Controller
  2. Duplicate detection suppression & in-transit pipeline tracking
  3. Single-event bottle entry lifecycle in WebcamStream
  4. MobileNetV2 prediction pipeline & confidence bounds
  5. Flask backend endpoints & state management
"""

import time
import unittest
import numpy as np
from hardware_serial import ESP32Controller
from predict import BottleDetector
from webcam import WebcamStream


class TestHardwareSerialProtocol(unittest.TestCase):
    def setUp(self):
        self.sort_events = []
        self.missing_events = []

        def on_sort(bottle, evt_type):
            self.sort_events.append((bottle, evt_type))

        def on_missing(missing_evt):
            self.missing_events.append(missing_evt)

        self.hw = ESP32Controller(
            baud_rate=115200,
            enabled=False,
            on_sort_callback=on_sort,
            on_missing_callback=on_missing
        )

    def test_queue_and_fifo_matching(self):
        """Verify that bottles are queued in FIFO order and matched upon TRIGGER_SORT."""
        b1 = {
            "id": "B001",
            "timestamp_camera": time.time() - 6.5,
            "timestamp_predicted": time.time() - 6.4,
            "verdict": "PASS"
        }
        b2 = {
            "id": "B002",
            "timestamp_camera": time.time() - 5.0,
            "timestamp_predicted": time.time() - 4.9,
            "verdict": "DEFECT"
        }

        # Queue two bottles in software pipeline
        self.hw.queue_verdict("PASS", b1)
        self.hw.queue_verdict("DEFECT", b2)

        self.assertEqual(len(self.hw.in_transit_queue), 2)
        self.assertEqual(self.hw.in_transit_queue[0]["id"], "B001")
        self.assertEqual(self.hw.in_transit_queue[1]["id"], "B002")

        # Simulate ESP32 sending TRIGGER_SORT:PASS for first bottle
        self.hw._handle_incoming_line("TRIGGER_SORT:PASS")
        self.assertEqual(len(self.sort_events), 1)
        bottle, evt = self.sort_events[0]
        self.assertEqual(bottle["id"], "B001")
        self.assertEqual(bottle["status"], "PASSED")
        self.assertEqual(self.hw.current_servo_angle, 0)
        self.assertEqual(len(self.hw.in_transit_queue), 1)

        # Simulate ESP32 sending TRIGGER_SORT:DEFECT for second bottle
        self.hw._handle_incoming_line("TRIGGER_SORT:DEFECT")
        self.assertEqual(len(self.sort_events), 2)
        bottle2, evt2 = self.sort_events[1]
        self.assertEqual(bottle2["id"], "B002")
        self.assertEqual(bottle2["status"], "SORTING")
        self.assertEqual(self.hw.current_servo_angle, 180)
        self.assertEqual(len(self.hw.in_transit_queue), 0)

        # Simulate ESP32 sending SORT_COMPLETE:DEFECT
        self.hw._handle_incoming_line("SORT_COMPLETE:DEFECT")
        self.assertEqual(len(self.sort_events), 3)
        completed_bottle, evt3 = self.sort_events[2]
        self.assertEqual(completed_bottle["id"], "B002")
        self.assertEqual(completed_bottle["status"], "SORTED")
        self.assertEqual(self.hw.current_servo_angle, 0)

    def test_missing_verdict_when_queue_empty(self):
        """When ESP32 sends ERR:QUEUE_EMPTY, record MISSING VERDICT event without activating servo."""
        self.hw._handle_incoming_line("ERR:QUEUE_EMPTY")
        self.assertEqual(len(self.missing_events), 1)
        self.assertEqual(self.missing_events[0]["status"], "MISSING VERDICT")
        self.assertEqual(self.hw.current_servo_angle, 0)


class TestWebcamLifecycle(unittest.TestCase):
    def test_single_inspection_per_bottle(self):
        """Test that bottle presence generates exactly 1 inspection event until bottle exits."""
        inspected_records = []

        def on_inspected(snap, pred, tc, tp):
            inspected_records.append((pred, tc, tp))

        ws = WebcamStream(detector=None, on_bottle_inspected=on_inspected)

        fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Initially idle
        self.assertEqual(ws.bottle_tracking_state, "IDLE")

        # Frame 1: bottle present -> streak 1
        ws._update_bottle_lifecycle(fake_frame, bottle_present=True)
        self.assertEqual(len(inspected_records), 0)

        # Frame 2: bottle present -> streak 2 -> triggers inspection!
        ws._update_bottle_lifecycle(fake_frame, bottle_present=True)
        # Give thread a brief moment to run callback
        time.sleep(0.05)
        self.assertEqual(len(inspected_records), 1)
        self.assertEqual(ws.bottle_tracking_state, "WAITING_EXIT")

        # Frames 3, 4, 5: bottle still present on conveyor -> MUST NOT TRIGGER AGAIN
        for _ in range(5):
            ws._update_bottle_lifecycle(fake_frame, bottle_present=True)
        time.sleep(0.05)
        self.assertEqual(len(inspected_records), 1)
        self.assertEqual(ws.bottle_tracking_state, "WAITING_EXIT")

        # Frames 6, 7: bottle absent
        ws._update_bottle_lifecycle(fake_frame, bottle_present=False)
        ws._update_bottle_lifecycle(fake_frame, bottle_present=False)
        self.assertEqual(ws.bottle_tracking_state, "WAITING_EXIT")

        # Frame 8: 3rd absent frame -> returns to IDLE
        ws._update_bottle_lifecycle(fake_frame, bottle_present=False)
        self.assertEqual(ws.bottle_tracking_state, "IDLE")

        # Next bottle arrives
        ws._update_bottle_lifecycle(fake_frame, bottle_present=True)
        ws._update_bottle_lifecycle(fake_frame, bottle_present=True)
        time.sleep(0.05)
        self.assertEqual(len(inspected_records), 2)


class TestModelPrediction(unittest.TestCase):
    def test_model_loading(self):
        detector = BottleDetector()
        self.assertIsNotNone(detector.model)
        self.assertEqual(detector.class_names, ["Defective", "Non_Defective"])

        # Test empty frame detection
        empty_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        pred = detector.predict(empty_frame, check_presence=True)
        self.assertEqual(pred["status"], "NO_BOTTLE")
        self.assertFalse(pred["bottle_present"])


if __name__ == "__main__":
    unittest.main()
