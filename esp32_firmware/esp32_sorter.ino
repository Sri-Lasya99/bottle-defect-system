/*
  Bottle Defect Detection and Automated Conveyor Sorting System
  ESP32 Firmware Sketch (Automated Closed-Loop Sorting)

  Workflow:
  1. Conveyor motor runs, carrying Limca bottle.
  2. IR sensor at inspection station detects bottle in front of webcam.
  3. Conveyor STOPS immediately to allow camera to capture a clear image.
  4. ESP32 sends "TRIGGER_INSPECT\n" over USB Serial to Python.
  5. Python analyzes webcam image and sends back:
       - "PASS\n"   -> Normal bottle: Conveyor resumes, servo stays 0°.
       - "DEFECT\n" -> Defective bottle: Servo arm sweeps bottle into reject bin, then conveyor resumes.
  6. After bottle clears IR sensor, conveyor keeps running for the next bottle.

  Pinout Connections (Default):
  -------------------------------------------------------------
  ESP32 Pin     Component Pin         Description
  -------------------------------------------------------------
  GPIO 25       Motor Driver ENA/PWM  Conveyor Speed (PWM)
  GPIO 26       Motor Driver IN1      Direction Pin 1
  GPIO 27       Motor Driver IN2      Direction Pin 2
  GPIO 18       SG90 Servo (Orange)   Diverter Servo Signal
  GPIO 19       IR Sensor (OUT)       Bottle Detector (Active LOW)
  GND           Common Ground         Connected to PSU Ground
*/

#include <ESP32Servo.h>

// Pin Definitions
#define PIN_MOTOR_ENA  25
#define PIN_MOTOR_IN1  26
#define PIN_MOTOR_IN2  27
#define PIN_SERVO      18
#define PIN_IR_SENSOR  19
#define PIN_STATUS_LED 2

// Servo Angles
#define SERVO_REST_ANGLE    0    // Rest position (conveyor lane open)
#define SERVO_DIVERT_ANGLE  90   // Sweeps defective bottle off the belt

// Conveyor Speed (0 to 255)
#define CONVEYOR_SPEED      200

// Machine States
enum SystemState {
  STATE_RUNNING,       // Conveyor moving
  STATE_WAIT_INSPECT,  // Stopped in front of cam, waiting for Python AI verdict
  STATE_SORTING,       // Actuating servo / clearing bottle
  STATE_MANUAL_STOP    // Paused by user
};

SystemState currentState = STATE_RUNNING;
Servo diverterServo;
String serialBuffer = "";
unsigned long stateTimer = 0;
bool bottlePresent = false;

void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(PIN_MOTOR_ENA, OUTPUT);
  pinMode(PIN_MOTOR_IN1, OUTPUT);
  pinMode(PIN_MOTOR_IN2, OUTPUT);
  pinMode(PIN_STATUS_LED, OUTPUT);
  pinMode(PIN_IR_SENSOR, INPUT_PULLUP);

  // Initialize Servo
  ESP32PWM::allocateTimer(0);
  diverterServo.setPeriodHertz(50);
  diverterServo.attach(PIN_SERVO, 500, 2400);
  diverterServo.write(SERVO_REST_ANGLE);

  // Start Conveyor
  startConveyor();
  currentState = STATE_RUNNING;

  Serial.println("[ESP32_READY]");
}

void loop() {
  // 1. Process Serial Commands from Python
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialBuffer.length() > 0) {
        handleCommand(serialBuffer);
        serialBuffer = "";
      }
    } else {
      serialBuffer += c;
    }
  }

  // 2. Read IR Sensor (Active LOW when bottle detected)
  int irVal = digitalRead(PIN_IR_SENSOR);
  bottlePresent = (irVal == LOW);
  digitalWrite(PIN_STATUS_LED, bottlePresent ? HIGH : LOW);

  // 3. Automated State Machine
  switch (currentState) {
    case STATE_RUNNING:
      // Bottle arrived at inspection station
      if (bottlePresent) {
        stopConveyor();
        currentState = STATE_WAIT_INSPECT;
        stateTimer = millis();
        // Give bottle a moment to settle, then request inspection
        delay(250);
        Serial.println("TRIGGER_INSPECT");
      }
      break;

    case STATE_WAIT_INSPECT:
      // Safety timeout: if Python takes more than 7 seconds, resume conveyor
      if (millis() - stateTimer > 7000) {
        Serial.println("[TIMEOUT_RESUMING]");
        startConveyor();
        currentState = STATE_RUNNING;
      }
      break;

    case STATE_SORTING:
      // Bottle is being diverted or passed, wait until bottle clears sensor
      if (!bottlePresent) {
        startConveyor();
        currentState = STATE_RUNNING;
      }
      break;

    case STATE_MANUAL_STOP:
      stopConveyor();
      break;
  }
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "PASS") {
    // Non-defective bottle: keep servo at rest, resume conveyor
    diverterServo.write(SERVO_REST_ANGLE);
    startConveyor();
    currentState = STATE_SORTING;
    Serial.println("ACK:PASS");

  } else if (cmd == "DEFECT") {
    // Defective bottle: sweep off conveyor belt
    divertDefectiveBottle();
    startConveyor();
    currentState = STATE_SORTING;
    Serial.println("ACK:DEFECT_DIVERTED");

  } else if (cmd == "START") {
    startConveyor();
    currentState = STATE_RUNNING;
    Serial.println("ACK:CONVEYOR_STARTED");

  } else if (cmd == "STOP") {
    stopConveyor();
    currentState = STATE_MANUAL_STOP;
    Serial.println("ACK:CONVEYOR_STOPPED");

  } else if (cmd == "DIVERT_TEST") {
    divertDefectiveBottle();
    Serial.println("ACK:TEST_COMPLETE");

  } else if (cmd == "STATUS") {
    Serial.print("STATUS:STATE=");
    Serial.print(currentState);
    Serial.print(",IR=");
    Serial.println(bottlePresent ? "BOTTLE_DETECTED" : "CLEAR");
  }
}

void startConveyor() {
  digitalWrite(PIN_MOTOR_IN1, HIGH);
  digitalWrite(PIN_MOTOR_IN2, LOW);
  analogWrite(PIN_MOTOR_ENA, CONVEYOR_SPEED);
}

void stopConveyor() {
  digitalWrite(PIN_MOTOR_IN1, LOW);
  digitalWrite(PIN_MOTOR_IN2, LOW);
  analogWrite(PIN_MOTOR_ENA, 0);
}

void divertDefectiveBottle() {
  // Servo arm pushes the bottle off the belt into the defective box
  diverterServo.write(SERVO_DIVERT_ANGLE);
  delay(1000);  // Wait for bottle to drop
  diverterServo.write(SERVO_REST_ANGLE);
  delay(300);
}
