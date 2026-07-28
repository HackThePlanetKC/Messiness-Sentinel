#!/bin/bash
set -e

INSTALL_DIR="/home/pi/pi-room-sentinel"

echo "[*] Updating system packages and installing camera/imaging dependencies..."
sudo apt update && sudo apt install -y python3-picamera2 python3-pip python3-opencv python3-numpy

echo "[*] Installing Python dependencies from requirements.txt..."
pip3 install -r "$INSTALL_DIR/requirements.txt" --break-system-packages

echo "[*] Downloading MobileNet TFLite detection model..."
if [ ! -f "$INSTALL_DIR/detect.tflite" ]; then
    cd "$INSTALL_DIR"
    wget -O detect.tflite https://github.com/google-coral/test_data/raw/master/coco_ssd_mobilenet_v1_switched_quant_cat_dog.tflite
fi

echo "[*] Setting up systemd service..."
sudo cp "$INSTALL_DIR/room-sentinel.service" /etc/systemd/system/room-sentinel.service
sudo systemctl daemon-reload
sudo systemctl enable room-sentinel.service
sudo systemctl restart room-sentinel.service

echo "[*] Installation complete! Service is running on port 5000 with MQTT background listener enabled."
