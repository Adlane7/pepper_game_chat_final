#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pepper Camera Visualizer with DeepFace Emotion Analysis and Publisher
- Subscribes to Pepper's image feed via ZeroMQ.
- Detects faces using a Haar cascade.
- For each face, runs DeepFace to get the dominant emotion and confidence.
- Draws bounding boxes with overlaid emotion/confidence on the image.
- Displays the annotated image.
- Publishes the detected emotion (from the first face) via ZeroMQ on port 5560.

Usage:
    python3 pepper_visualizer_deepface_emotion.py
"""

import time
import zmq
import numpy as np
import cv2
from deepface import DeepFace

# ZeroMQ settings
PEPPER_IMAGE_PORT = 5558  # Port where Pepper sends images
EMOTION_PUB_PORT = 5560   # Port where emotions are published

def main():
    # Set up ZeroMQ subscriber for Pepper images
    context = zmq.Context()
    image_sub = context.socket(zmq.SUB)
    image_sub.connect(f"tcp://127.0.0.1:{PEPPER_IMAGE_PORT}")  # Adjust IP if needed
    image_sub.setsockopt(zmq.SUBSCRIBE, b"")

    # Set up ZeroMQ publisher for detected emotions
    emotion_pub = context.socket(zmq.PUB)
    emotion_pub.bind(f"tcp://*:{EMOTION_PUB_PORT}")

    # Load Haar cascade for face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    print(f"Pepper Visualizer with DeepFace Emotion running (Listening on {PEPPER_IMAGE_PORT}, Publishing on {EMOTION_PUB_PORT})...")

    while True:
        # Receive image bytes from Pepper via ZeroMQ
        image_bytes = image_sub.recv()
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if image is None:
            print("Received an invalid image. Skipping...")
            continue

        # Copy image for annotation
        annotated_image = image.copy()

        # Convert image to grayscale for face detection
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

        emotions_list = []  # List to collect emotion strings for publishing

        # For each detected face, run DeepFace analysis
        for (x, y, w, h) in faces:
            # Extract face region and convert to RGB for DeepFace
            face_img = image[y:y+h, x:x+w]
            face_rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
            try:
                analysis = DeepFace.analyze(face_rgb, actions=['emotion'], enforce_detection=False)
                # If multiple faces are returned, select the first one
                if isinstance(analysis, list):
                    analysis = analysis[0]
                dominant_emotion = analysis["dominant_emotion"]
                confidence = analysis["emotion"][dominant_emotion]
            except Exception as e:
                print("DeepFace analysis error:", e)
                dominant_emotion = "unknown"
                confidence = 0

            # Prepare overlay text with emotion and confidence percentage
            overlay_text = f"{dominant_emotion}"
            # Draw bounding box and overlay text on the annotated image
            cv2.rectangle(annotated_image, (x, y), (x+w, y+h), (0, 255, 0), 2)
            cv2.putText(annotated_image, overlay_text, (x, y-10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            emotions_list.append(overlay_text)

        # Publish the first detected emotion if available; otherwise publish "neutral"
        if emotions_list:
            emotion_pub.send_string(emotions_list[0])
            print("Published emotion:", emotions_list[0])
        else:
            emotion_pub.send_string("neutral")
            print("Published emotion: neutral")

        # Display the annotated image with bounding boxes
        cv2.imshow("Pepper Camera Feed with Emotion Bounding Boxes", annotated_image)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

        time.sleep(0.033)  # Adjust loop timing as needed

    cv2.destroyAllWindows()
    image_sub.close()
    emotion_pub.close()
    context.term()

if __name__ == '__main__':
    main()
