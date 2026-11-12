/**
 * ==============================================================================
 * Automated Bottle Defect Detection & Conveyor Sorting System - ESP32 Firmware
 * ==============================================================================
 * Hardware Pinout:
 *   - Servo Signal: GPIO18 (SG90 Servo diverter)
 *   - IR Sensor:    GPIO32 (Active LOW, INPUT_PULLUP)
 *
 * Operational Logic:
 *   1. Conveyor Belt runs CONTINUOUSLY (never halted during normal sorting).
 *   2. Python AI camera inspects bottle ~12 cm before IR sensor (6-7s travel time).
 *   3. Python enqueues AI verdict into ESP32 FIFO queue (QUEUE:DEFECT, QUEUE:PASS, QUEUE:UNCERTAIN).
 *   4. When bottle arrives at IR sensor (GPIO32 active LOW):
 *      - Consumes exactly ONE verdict from the FIFO queue.
 *      - If DEFECT: Servo sweeps from 0° to 180°, holds for 1s, returns to 0°.
 *      - If PASS: Servo remains at 0° (bottle passes along conveyor).
 *      - If UNCERTAIN or ERR:QUEUE_EMPTY: Servo remains at 0° (safe bypass).
 *   5. Completely non-blocking millis() state machine so serial communication and
 *      sensor polling run with sub-millisecond responsiveness.
 * ==============================================================================
 */

#include <Arduino.h>
#include <ESP32Servo.h>

// --- Pin Definitions ---
#define PIN_SERVO      18
#define PIN_IR_SENSOR  32

// --- Servo Configuration ---
#define SERVO_REST_ANGLE    0     // Resting angle (parallel to conveyor)
#define DEFAULT_PUSH_ANGLE  180   // Pushing angle (deflects defective bottle)
#define DEFAULT_HOLD_MS     1000  // Duration to hold servo at push angle (ms)
#define SERVO_RETURN_MS     400   // Settle time when returning to rest angle (ms)

// --- IR Sensor Configuration ---
#define IR_DEBOUNCE_MS      35    // Debounce time for bottle arrival (active LOW)
#define IR_EXIT_DEBOUNCE_MS 50    // Debounce time for bottle exit (HIGH)
#define IR_COOLDOWN_MS      400   // Minimum gap between successive bottles (ms)

// --- FIFO Verdict Queue Configuration ---
#define QUEUE_CAPACITY      32    // Max in-flight bottle verdicts

enum VerdictType {
  VERDICT_NONE = 0,
  VERDICT_DEFECT,
  VERDICT_PASS,
  VERDICT_UNCERTAIN
};

enum ServoState {
  SERVO_IDLE = 0,
  SERVO_HOLDING,
  SERVO_RETURNING
};

// Global Hardware Objects
Servo myServo;

// Configurable Parameters (can be adjusted via serial CONFIG commands)
int servoRestAngle = SERVO_REST_ANGLE;
int servoPushAngle = DEFAULT_PUSH_ANGLE;
unsigned long servoHoldMs = DEFAULT_HOLD_MS;

// Servo State Machine Variables
ServoState servoState = SERVO_IDLE;
unsigned long servoTimer = 0;
bool pendingSortDefect = false;

// IR Sensor State Machine Variables
bool bottlePresentAtIR = false;
unsigned long irLowStartTime = 0;
unsigned long irHighStartTime = 0;
unsigned long lastBottleExitTime = 0;

// FIFO Circular Buffer for Verdicts
VerdictType verdictQueue[QUEUE_CAPACITY];
int queueHead = 0; // Index of oldest verdict (dequeue position)
int queueTail = 0; // Index of next insert (enqueue position)
int queueCount = 0;

// Serial Parsing Buffer
String serialCommandBuffer = "";

// Forward Declarations
bool enqueueVerdict(VerdictType v);
VerdictType dequeueVerdict();
void processIRDetection();
void updateServoStateMachine();
void processIncomingSerial();
void handleCommand(const String& cmd);
const char* verdictToString(VerdictType v);

void setup() {
  Serial.begin(115200);
  delay(200);

  // Configure IR sensor with internal pullup (Active LOW)
  pinMode(PIN_IR_SENSOR, INPUT_PULLUP);

  // Configure ESP32 Servo
  ESP32PWM::allocateTimer(0);
  myServo.setPeriodHertz(50);             // Standard 50Hz servo PWM
  myServo.attach(PIN_SERVO, 500, 2400);   // SG90 standard pulse range (0.5ms - 2.4ms)

  // Initial position: 0 degrees resting (DO NOT sweep or actuate on boot)
  myServo.write(servoRestAngle);

  Serial.println("SYSTEM_READY");
  Serial.println("INFO:Bottle Sorter ESP32 Firmware initialized");
  Serial.print("INFO:Servo Pin=");
  Serial.print(PIN_SERVO);
  Serial.print(", IR Pin=");
  Serial.print(PIN_IR_SENSOR);
  Serial.print(", RestAngle=");
  Serial.print(servoRestAngle);
  Serial.print(", PushAngle=");
  Serial.println(servoPushAngle);
}

void loop() {
  // 1. Process serial commands from Python
  processIncomingSerial();

  // 2. Poll IR sensor & trigger sorting if bottle arrives
  processIRDetection();

  // 3. Update non-blocking servo movement state machine
  updateServoStateMachine();
}

/**
 * Enqueue an AI verdict received from Python into circular FIFO queue.
 */
bool enqueueVerdict(VerdictType v) {
  if (queueCount >= QUEUE_CAPACITY) {
    return false; // Queue full
  }
  verdictQueue[queueTail] = v;
  queueTail = (queueTail + 1) % QUEUE_CAPACITY;
  queueCount++;
  return true;
}

/**
 * Dequeue the oldest AI verdict when a bottle reaches the IR sensor.
 */
VerdictType dequeueVerdict() {
  if (queueCount == 0) {
    return VERDICT_NONE;
  }
  VerdictType v = verdictQueue[queueHead];
  queueHead = (queueHead + 1) % QUEUE_CAPACITY;
  queueCount--;
  return v;
}

const char* verdictToString(VerdictType v) {
  switch (v) {
    case VERDICT_DEFECT:    return "DEFECT";
    case VERDICT_PASS:      return "PASS";
    case VERDICT_UNCERTAIN: return "UNCERTAIN";
    default:                return "NONE";
  }
}

/**
 * Reads and debounces the IR sensor (Active LOW on GPIO32).
 * Consumes exactly one verdict when a bottle breaks the IR beam.
 */
void processIRDetection() {
  int rawValue = digitalRead(PIN_IR_SENSOR);
  unsigned long now = millis();

  // Active LOW detection
  if (rawValue == LOW) {
    irHighStartTime = 0; // Reset HIGH timer

    if (!bottlePresentAtIR) {
      if (irLowStartTime == 0) {
        irLowStartTime = now;
      } else if ((now - irLowStartTime) >= IR_DEBOUNCE_MS) {
        // Enforce cooldown from previous bottle to prevent chatter
        if ((now - lastBottleExitTime) >= IR_COOLDOWN_MS) {
          bottlePresentAtIR = true;

          // Notify Python that a bottle has arrived at IR sensor
          Serial.println("IR_TRIGGER");

          // Consume exactly ONE verdict from the FIFO queue
          VerdictType verdict = dequeueVerdict();

          if (verdict == VERDICT_DEFECT) {
            Serial.println("TRIGGER_SORT:DEFECT");
            // Initiate servo push immediately
            myServo.write(servoPushAngle);
            servoTimer = millis();
            servoState = SERVO_HOLDING;
          } else if (verdict == VERDICT_PASS) {
            Serial.println("TRIGGER_SORT:PASS");
            // Servo stays at rest, bottle passes on conveyor
            Serial.println("SORT_COMPLETE:PASS");
          } else if (verdict == VERDICT_UNCERTAIN) {
            Serial.println("TRIGGER_SORT:UNCERTAIN");
            // Safe bypass: do not push uncertain bottles
            Serial.println("SORT_COMPLETE:UNCERTAIN");
          } else {
            // Queue empty! No verdict available for this bottle
            Serial.println("ERR:QUEUE_EMPTY");
            // Never push an unknown bottle
          }
        }
      }
    }
  } else {
    // IR sensor is HIGH (beam unbroken)
    irLowStartTime = 0; // Reset LOW timer

    if (bottlePresentAtIR) {
      if (irHighStartTime == 0) {
        irHighStartTime = now;
      } else if ((now - irHighStartTime) >= IR_EXIT_DEBOUNCE_MS) {
        // Bottle has completely cleared the IR sensor
        bottlePresentAtIR = false;
        lastBottleExitTime = now;
        Serial.println("IR_CLEARED");
      }
    }
  }
}

/**
 * Non-blocking servo timer:
 * - Moves to push angle (180°)
 * - Holds for servoHoldMs (1000ms)
 * - Returns to rest angle (0°)
 * - Allows settle time then sends SORT_COMPLETE:DEFECT
 */
void updateServoStateMachine() {
  if (servoState == SERVO_IDLE) {
    return;
  }

  unsigned long now = millis();

  if (servoState == SERVO_HOLDING) {
    if (now - servoTimer >= servoHoldMs) {
      // Return servo back to 0° resting position
      myServo.write(servoRestAngle);
      servoTimer = now;
      servoState = SERVO_RETURNING;
    }
  } else if (servoState == SERVO_RETURNING) {
    if (now - servoTimer >= SERVO_RETURN_MS) {
      servoState = SERVO_IDLE;
      Serial.println("SORT_COMPLETE:DEFECT");
    }
  }
}

/**
 * Reads incoming characters from serial into a line buffer.
 */
void processIncomingSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      serialCommandBuffer.trim();
      if (serialCommandBuffer.length() > 0) {
        handleCommand(serialCommandBuffer);
      }
      serialCommandBuffer = "";
    } else {
      if (serialCommandBuffer.length() < 64) {
        serialCommandBuffer += c;
      }
    }
  }
}

/**
 * Dispatches ASCII protocol commands received from Python.
 */
void handleCommand(const String& cmd) {
  if (cmd == "QUEUE:DEFECT") {
    if (enqueueVerdict(VERDICT_DEFECT)) {
      Serial.print("ACK:QUEUED:DEFECT:");
      Serial.println(queueCount);
    } else {
      Serial.println("ERR:QUEUE_FULL");
    }
  }
  else if (cmd == "QUEUE:PASS") {
    if (enqueueVerdict(VERDICT_PASS)) {
      Serial.print("ACK:QUEUED:PASS:");
      Serial.println(queueCount);
    } else {
      Serial.println("ERR:QUEUE_FULL");
    }
  }
  else if (cmd == "QUEUE:UNCERTAIN") {
    if (enqueueVerdict(VERDICT_UNCERTAIN)) {
      Serial.print("ACK:QUEUED:UNCERTAIN:");
      Serial.println(queueCount);
    } else {
      Serial.println("ERR:QUEUE_FULL");
    }
  }
  else if (cmd == "STATUS") {
    Serial.print("STATUS:QUEUE=");
    Serial.print(queueCount);
    Serial.print(":SERVO=");
    Serial.print(servoState == SERVO_IDLE ? servoRestAngle : servoPushAngle);
    Serial.print(":IR=");
    Serial.println(digitalRead(PIN_IR_SENSOR) == LOW ? "ACTIVE" : "IDLE");
  }
  else if (cmd == "CLEAR_QUEUE") {
    queueHead = 0;
    queueTail = 0;
    queueCount = 0;
    Serial.println("ACK:QUEUE_CLEARED");
  }
  else if (cmd == "SERVO:TEST") {
    // Manual testing actuation from dashboard
    if (servoState == SERVO_IDLE) {
      Serial.println("ACK:SERVO_TEST_STARTED");
      myServo.write(servoPushAngle);
      servoTimer = millis();
      servoState = SERVO_HOLDING;
    } else {
      Serial.println("ERR:SERVO_BUSY");
    }
  }
  else if (cmd.startsWith("CONFIG:ANGLE:")) {
    int angle = cmd.substring(13).toInt();
    if (angle >= 30 && angle <= 180) {
      servoPushAngle = angle;
      Serial.print("ACK:CONFIG_ANGLE:");
      Serial.println(servoPushAngle);
    } else {
      Serial.println("ERR:INVALID_ANGLE");
    }
  }
  else if (cmd.startsWith("CONFIG:HOLD:")) {
    int hold = cmd.substring(12).toInt();
    if (hold >= 200 && hold <= 5000) {
      servoHoldMs = (unsigned long)hold;
      Serial.print("ACK:CONFIG_HOLD:");
      Serial.println(servoHoldMs);
    } else {
      Serial.println("ERR:INVALID_HOLD");
    }
  }
  else {
    Serial.print("ERR:UNKNOWN_COMMAND:");
    Serial.println(cmd);
  }
}
