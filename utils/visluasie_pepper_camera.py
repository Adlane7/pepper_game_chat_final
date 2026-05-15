#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pepper Camera Visualizer with Face Bounding Boxes
- Subscribes to the Pepper camera feed via ZeroMQ.
- Decodes the JPEG images.
- Detects faces using a Haar cascade classifier.
- Displays the images in a window with bounding boxes drawn around detected faces.

Usage:
    python3 pepper_camera_visualizer_with_bbox.py
"""

import cv2
import zmq
import numpy as np

PEPPER_IMAGE_PORT = 5558  # Port where Pepper sends images

def main():
    # Set up ZeroMQ subscriber for images from Pepper
    context = zmq.Context()
    image_sub = context.socket(zmq.SUB)
    # Adjust IP address if the publisher is on a different machine
    image_sub.connect(f"tcp://127.0.0.1:{PEPPER_IMAGE_PORT}")
    image_sub.setsockopt(zmq.SUBSCRIBE, b"")

    # Load Haar cascade for face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    print(f"Pepper Camera Visualizer with Bounding Boxes running (Listening on {PEPPER_IMAGE_PORT})...")
    
    while True:
        # Receive the image from Pepper (as bytes)
        image_bytes = image_sub.recv()
        
        # Convert bytes to NumPy array and decode the JPEG image
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        
        if image is None:
            print("Received an invalid image. Skipping...")
            continue

        # Convert image to grayscale for face detection
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Detect faces; adjust parameters if needed
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        
        # Draw bounding boxes on the detected faces
        for (x, y, w, h) in faces:
            cv2.rectangle(image, (x, y), (x+w, y+h), (0, 255, 0), 2)

        # Display the image with bounding boxes
        cv2.imshow("Pepper Camera Feed with Bounding Boxes", image)
        # Wait for 1ms and break loop if 'q' is pressed
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()
    image_sub.close()
    context.term()

if __name__ == '__main__':
    main()
