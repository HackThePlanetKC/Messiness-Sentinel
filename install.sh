#!/bin/bash
set -e

INSTALL_DIR="/home/pi/pi-room-sentinel"

echo "[*] Updating system packages and installing camera/imaging dependencies..."
sudo apt update && sudo apt install -y python3-picamera2 python3-pip python3-opencv python3-numpy

echo "[*] Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt" --break-system-packages

echo "[*] Downloading MobileNet TFLite detection model..."
if [ ! -f "$INSTALL_DIR/detect.tflite" ]; then
    cd "$INSTALL_DIR"
    wget -q https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
    unzip -q coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
    mv detect.tflite detect_quant.tflite 2>/dev/null || true
    # Grab the standard model file name expected by the script
    wget -O detect.tflite https://github.com/google-coral/test_data/raw/master/coco_ssd_mobilenet_v1_switched_quant_cat_dog.tflite 2>/dev/null || true
    # (Alternatively, keep standard coco model mapping if preferred)
    rm -f coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
fi

echo "[*] Setting up systemd service..."
sudo cp "$INSTALL_DIR/room-sentinel.service" /etc/systemd/system/room-sentinel.service
sudo systemctl daemon-reload
sudo systemctl enable room-sentinel.service
sudo systemctl restart room-sentinel.service

echo "[*] Installation complete! Service is running on port 5000."
