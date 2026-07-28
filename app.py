#!/usr/bin/env python3
import io
import time
import threading
import numpy as np
import paho.mqtt.client as mqtt
from PIL import Image
from flask import Flask, jsonify, send_file
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter

app = Flask(__name__)

# MQTT Configuration (Update broker IP or use localhost if running on HA host)
MQTT_BROKER = "192.168.1.50"
MQTT_PORT = 1883
MQTT_TOPIC_COMMAND = "home/livingroom_cam/set"
MQTT_TOPIC_STATE = "home/livingroom_cam/state"
MQTT_TOPIC_SNAPSHOT_READY = "home/livingroom_cam/snapshot_ready"

# Initialize Camera
picam2 = Picamera2()
config = picam2.create_still_configuration(main={"size": (1280, 720)})
picam2.configure(config)
picam2.start()

# Load TFLite Model (Quantized MobileNet SSD)
interpreter = Interpreter(model_path="detect.tflite")
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

latest_snapshot = None
snapshot_lock = threading.Lock()

def run_person_detection():
    """Captures a frame and checks for a person via TFLite (COCO class 0)."""
    frame = picam2.capture_array()
    
    input_shape = input_details[0]['shape']
    h, w = input_shape[1], input_shape[2]
    
    img = Image.fromarray(frame).resize((w, h))
    input_data = np.expand_dims(np.array(img, dtype=np.float32), axis=0)
    
    interpreter.set_tensor(input_details[0]['index'], input_data)
    interpreter.invoke()
    
    classes = interpreter.get_tensor(output_details[1]['index'])
    scores = interpreter.get_tensor(output_details[2]['index'])
    
    person_detected = False
    for i, score in enumerate(scores[0]):
        if score > 0.5 and int(classes[0][i]) == 0:
            person_detected = True
            break
            
    return person_detected

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with code {rc}")
    client.subscribe(MQTT_TOPIC_COMMAND)

def on_message(client, userdata, msg):
    payload = msg.payload.decode()
    if payload == "capture_messiness_snapshot":
        print("Received snapshot trigger from Home Assistant via MQTT.")
        with snapshot_lock:
            img_io = io.BytesIO()
            picam2.capture_file(img_io, format='jpeg')
            img_io.seek(0)
            global latest_snapshot
            latest_snapshot = img_io.read()
        
        # Notify Home Assistant that the snapshot is ready to fetch via HTTP
        client.publish(MQTT_TOPIC_SNAPSHOT_READY, "true")

# Initialize MQTT Client
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def start_mqtt():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT connection failed: {e}")

# --- Flask Endpoints ---
@app.route('/presence', methods=['GET'])
def get_presence():
    is_present = run_person_detection()
    try:
        mqtt_client.publish(MQTT_TOPIC_STATE, "ON" if is_present else "OFF")
    except Exception:
        pass
    return jsonify({"person_detected": is_present})

@app.route('/snapshot', methods=['GET'])
def get_snapshot():
    global latest_snapshot
    with snapshot_lock:
        if latest_snapshot:
            return send_file(io.BytesIO(latest_snapshot), mimetype='image/jpeg')
    return "No snapshot available", 404

if __name__ == '__main__':
    start_mqtt()
    app.run(host='0.0.0.0', port=5000)
