# Limca Bottle Defect Detection & Automated Conveyor Sorting System

An end-to-end AI and computer vision inspection system built for real-time defect detection of Limca plastic bottles using a laptop's built-in webcam, transfer learning with **MobileNetV2 (TensorFlow/Keras)**, a real-time web dashboard, and USB serial actuation for an **ESP32 conveyor and servo diverter**.

---

## 🚀 Quick Start Guide

### 1. Launch with One Command

Run the automated launcher script:

```bash
chmod +x run.sh
./run.sh
```

Or manually activate the virtual environment and start:

```bash
source .venv/bin/activate
python app.py
```

Open your browser and navigate to:
👉 **[http://localhost:5001](http://localhost:5001)**

---

## 🛠️ System Architecture & File Structure

```
.
├── dataset -> Limca_Expanded_Dataset   # Dataset symlink (Defective/ and Non_Defective/)
├── Limca_Expanded_Dataset/             # 40 bottle image samples (20 defective, 20 normal)
├── requirements.txt                    # Project Python dependencies
├── train_model.py                      # Dataset preprocessing & MobileNetV2 training pipeline
├── bottle_defect_model.keras           # Trained MobileNetV2 transfer learning model (Keras 3)
├── model_metadata.json                 # Model metadata, class mappings, metrics, and configs
├── predict.py                          # Preprocessing, presence verification, inference & smoothing
├── webcam.py                           # OpenCV MacBook webcam stream & real-time HUD renderer
├── hardware_serial.py                  # USB serial controller for ESP32 with simulated fallback
├── esp32_firmware/
│   └── esp32_sorter.ino                # Complete Arduino firmware for ESP32, DC motor, & servo
├── app.py                              # Flask REST API backend & MJPEG video streaming server
├── templates/
│   └── index.html                      # Real-time industrial inspection web dashboard
├── static/
│   ├── style.css                       # Modern dark-mode cyber-industrial dashboard styling
│   ├── main.js                         # Dynamic polling, HUD updating, and control logic
│   └── captures/                       # Persisted manual snapshots and detection records
└── run.sh                              # Automated setup and launcher script
```

---

## 🧠 AI Model Training (`train_model.py`)

- **Backbone**: MobileNetV2 pretrained on ImageNet.
- **Input Size**: `224 × 224 × 3`.
- **Normalization**: Scaled to `[-1.0, 1.0]` using `layers.Rescaling(1./127.5, offset=-1.0)`.
- **Data Augmentation**: Random horizontal flip, rotation ($\pm 8\%$), zoom ($10\%$), translation ($\pm 5\%$).
- **Head**: GlobalAveragePooling2D $\rightarrow$ BatchNormalization $\rightarrow$ Dropout(0.4) $\rightarrow$ Dense(128, ReLU) $\rightarrow$ Dropout(0.3) $\rightarrow$ Dense(2, Softmax).
- **Split Strategy**: Stratified 70% Train (28), 15% Validation (6), 15% Test (6) with zero leakage and MD5 deduplication verification.
- **Evaluation**: Evaluated on unseen test set with confusion matrix, precision, recall, and F1-score.

To retrain the model at any time:

```bash
./.venv/bin/python3 train_model.py
```

---

## 📹 Real-Time Laptop Webcam Features (`webcam.py` & `predict.py`)

1. **MacBook Webcam Access**: OpenCV uses `CAP_AVFOUNDATION` (index 0) with graceful permission error handling and fallbacks.
2. **Bottle Presence Detection**: Checks gradient energy, edge density (Canny), and variance in the inspection zone to prevent blank walls from being classified.
3. **Status Banner Styles**:
   - 🟢 **NON-DEFECTIVE**: Green border, green checkmark icon, displays **`BOTTLE IS NON-DEFECTIVE`**.
   - 🔴 **DEFECTIVE**: Red border, warning icon, displays **`BOTTLE IS DEFECTIVE`**.
   - 🟡 **NO BOTTLE / UNCERTAIN**: Displays **`NO BOTTLE DETECTED`** or **`UNCERTAIN – PLEASE REPOSITION BOTTLE`**.
4. **Temporal Smoothing**: Rolling multi-frame window (8 frames) eliminates frame jitter and provides steady predictions.
5. **Capture & Analyze**: Manual snapshot button saves high-resolution frame with HUD overlay and logs it to the detection history.

---

## ⚡ ESP32 Hardware Integration (`hardware_serial.py` & `esp32_firmware/`)

### Hardware Components:
- **ESP32** (Development Board)
- **12V Geared DC Motor** (Conveyor drive)
- **L298N / TB6612FNG** (Motor Driver)
- **SG90 Micro Servo** (Diverter arm)
- **IR Obstacle Sensor** (Bottle positioning trigger)

### Wiring Pinout:
| ESP32 Pin | Component Pin | Description |
|-----------|---------------|-------------|
| **GPIO 25** | Motor Driver `ENA` | PWM speed control |
| **GPIO 26** | Motor Driver `IN1` | Direction 1 |
| **GPIO 27** | Motor Driver `IN2` | Direction 2 |
| **GPIO 18** | SG90 Servo Signal | PWM control (orange wire) |
| **GPIO 19** | IR Sensor `OUT` | Digital input (active LOW) |
| **GND** | Common Ground | Connected to 12V/5V PSU GND |

### Protocol:
- `PASS\n`: Non-defective bottle $\rightarrow$ Conveyor keeps running.
- `DEFECT\n`: Defective bottle $\rightarrow$ Diverter servo sweeps bottle into reject bin.
- `START\n` / `STOP\n`: Conveyor start/stop.
- *Default Mode*: **Simulated / Standby** (can be enabled via the dashboard toggle when physical ESP32 is plugged into USB).

---

## 🔒 macOS Camera Permissions

When launching `python app.py` for the first time in Terminal:
1. macOS will show a prompt: *"Terminal would like to access the Camera"*.
2. Click **Allow**.
3. If denied or missing: Open **System Settings $\rightarrow$ Privacy & Security $\rightarrow$ Camera**, and toggle on **Terminal** (or your IDE).
