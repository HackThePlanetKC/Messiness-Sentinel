# Messiness Sentinel

A lightweight, local-edge vision and presence detection sentinel designed for the Raspberry Pi Zero 2 W. It combines real-time TensorFlow Lite (TFLite) human presence detection with an on-demand snapshot server, built to integrate cleanly with Home Assistant via MQTT and HTTP.

## Features

* **Edge Presence Detection:** Runs a quantized TFLite object detection model locally via `picamera2` to detect human presence without cloud overhead or false-positive environmental triggers (like lighting shifts or shadows).
* **On-Demand Snapshot Capture:** Serves high-resolution still frames via a lightweight Flask endpoint when requested by Home Assistant for multimodal analysis (such as AI messiness reporting).
* **Minimal Resource Footprint:** Tuned specifically for low-power hardware like the Pi Zero 2 W, keeping CPU usage low and avoiding unnecessary SD card wear.
* **Automated Installation:** Includes a streamlined installer script to handle system packages, Python dependencies, model acquisition, and systemd service registration.

## Requirements

* Raspberry Pi Zero 2 W (running Raspberry Pi OS Bookworm Core)
* Raspberry Pi Camera Module
* Python 3 & Pip

## Quick Installation

Clone the repository and run the automated installer script:

```bash
git clone [https://github.com/HackThePlanetKC/Messiness-Sentinel.git](https://github.com/HackThePlanetKC/Messiness-Sentinel.git) ~/pi-room-sentinel
cd ~/pi-room-sentinel
chmod +x install.sh
sudo ./install.sh
