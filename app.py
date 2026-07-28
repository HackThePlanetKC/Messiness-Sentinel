#!/usr/bin/env python3
import io
import time
import numpy as np
from PIL import Image
from flask import Flask, jsonify, send_file
from picamera2 import Picamera2
from tflite_runtime.interpreter import Interpreter

app = Flask(__name__)

# Initialize Camera for low-res detection preview & high-res snapshots
picam2 = Picamera2()
config = picam2.create_still_configuration(main={"size": (1280, 720)})
picam2.configure(config)
picam2.start()

# Load TFLite Model (Quantized MobileNet SSD)
interpreter = Interpreter(model_path="detect.tflite")
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()

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

@app.route('/presence', methods=['GET'])
def get_presence():
    is_present = run_person_detection()
    return jsonify({"person_detected": is_present})

@app.route('/snapshot', methods=['GET'])
def get_snapshot():
    img_io = io.BytesIO()
    picam2.capture_file(img_io, format='jpeg')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/jpeg')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
