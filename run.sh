#!/usr/bin/env bash
# ==============================================================================
# Limca Bottle Defect Detection and Automated Conveyor Sorting System
# Launcher Script
# ==============================================================================

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "========================================================================"
echo " Limca Bottle Defect Detection & Conveyor Sorting System"
echo "========================================================================"

# 1. Virtual Environment Setup
if [ ! -d ".venv" ]; then
    echo "[1/4] Creating Python virtual environment (.venv)..."
    python3 -m venv .venv
else
    echo "[1/4] Python virtual environment (.venv) found."
fi

# 2. Dependencies
echo "[2/4] Verifying dependencies..."
./.venv/bin/pip install -q -r requirements.txt

# 3. Model Verification
if [ ! -f "bottle_defect_model.keras" ]; then
    echo "[3/4] Model file not found. Training MobileNetV2 defect detection model..."
    ./.venv/bin/python3 train_model.py
else
    echo "[3/4] Trained model 'bottle_defect_model.keras' ready."
fi

# 4. Launch Application
PORT=5001
echo "[4/4] Starting Web Dashboard and Live Video Inspection on port $PORT..."
echo ""
echo "------------------------------------------------------------------------"
echo " >> Open in your browser: http://localhost:$PORT"
echo " >> On macOS: If prompted for Camera Access, click 'Allow'."
echo " >> If camera is blocked: System Settings -> Privacy & Security -> Camera"
echo "------------------------------------------------------------------------"
echo ""

PORT=$PORT ./.venv/bin/python3 app.py
