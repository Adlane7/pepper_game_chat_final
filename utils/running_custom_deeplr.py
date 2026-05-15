#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Improved Inference for Emotion Publisher Using Fine-Tuned CLIP Model with Attention and Face Detection Fallback
- Listens for image data from Pepper's Python 2 module via ZeroMQ.
- Uses a lighter attention block and half-precision inference for faster performance.
- Performs face detection; if no face is detected, sends "neutral".
- Publishes the detected emotion via ZeroMQ on port 5560.
- Displays the Pepper camera feed with bounding boxes and emotion labels.
"""

import time
import zmq
import numpy as np
import cv2
import torch
import torch.nn as nn
from torchvision import transforms
import clip  # ensure you have installed the CLIP package
from PIL import Image

# -----------------------------
# 1. Device & Model Loading
# -----------------------------
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {device}")

# Load the pre-trained CLIP model (backbone used during training)
clip_model, clip_preprocess = clip.load("ViT-B/32", device=device)
clip_model.eval()

# -----------------------------
# 2. Define the Model Components
# -----------------------------
class AttentionBlock(nn.Module):
    """
    Minimal Transformer-like block with Multi-Head Self-Attention and FeedForward layers.
    """
    def __init__(self, embed_dim=512, num_heads=8, feedforward_dim=2048, dropout=0.3):
        super(AttentionBlock, self).__init__()
        self.self_attn = nn.MultiheadAttention(embed_dim, num_heads, dropout=dropout, batch_first=True)
        self.linear1 = nn.Linear(embed_dim, feedforward_dim)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(feedforward_dim, embed_dim)
        self.norm1 = nn.LayerNorm(embed_dim)
        self.norm2 = nn.LayerNorm(embed_dim)

    def forward(self, x):
        attn_output, _ = self.self_attn(x, x, x)
        x = x + attn_output
        x = self.norm1(x)
        ff_out = self.linear2(self.dropout(torch.relu(self.linear1(x))))
        x = x + ff_out
        x = self.norm2(x)
        return x

class FineTunedCLIPWithAttention(nn.Module):
    def __init__(self, clip_model, attention_block, num_emotions=7):
        super(FineTunedCLIPWithAttention, self).__init__()
        self.clip_model = clip_model
        self.attention_block = attention_block
        # Classifier layer on top of CLIP's visual output dimension.
        self.classifier = nn.Linear(clip_model.visual.output_dim, num_emotions)

    def forward(self, images):
        # Encode images using CLIP.
        image_features = self.clip_model.encode_image(images).float()
        # Normalize features (L2).
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        # Reshape to [batch_size, 1, embed_dim] for the attention block.
        image_features = image_features.unsqueeze(1)
        # Apply the attention block.
        attended = self.attention_block(image_features)
        # Squeeze and classify.
        attended = attended.squeeze(1)
        logits = self.classifier(attended)
        return logits

# -----------------------------
# 3. Instantiate and Load Weights
# -----------------------------
attention_block = AttentionBlock(
    embed_dim=clip_model.visual.output_dim,
    num_heads=8,
    feedforward_dim=2048,
    dropout=0.3
).to(device)

model = FineTunedCLIPWithAttention(
    clip_model=clip_model,
    attention_block=attention_block,
    num_emotions=7
).to(device)

# Replace with the path to your best model weights.
model_weights_path = "best_model.pth"
model.load_state_dict(torch.load(model_weights_path, map_location=device))
model.eval()

# Define emotion labels in the same order as during training.
emotion_labels = ['neutral', 'happiness', 'surprise', 'sadness', 'anger', 'disgust', 'fear']

# -----------------------------
# 4. Define Inference Transforms
# -----------------------------
inference_transforms = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=(0.48145466, 0.4578275, 0.40821073),
        std=(0.26862954, 0.26130258, 0.27577711)
    )
])

# -----------------------------
# 5. ZeroMQ Settings
# -----------------------------
PEPPER_IMAGE_PORT = 5558  # Port where Pepper sends images
EMOTION_PUB_PORT = 5560   # Port where we publish emotions

def main():
    # Set up ZeroMQ context
    context = zmq.Context()

    # Subscriber for images
    image_sub = context.socket(zmq.SUB)
    image_sub.connect(f"tcp://127.0.0.1:{PEPPER_IMAGE_PORT}")  # Update IP if needed
    image_sub.setsockopt(zmq.SUBSCRIBE, b"")

    # Publisher for detected emotions
    emotion_pub = context.socket(zmq.PUB)
    emotion_pub.bind(f"tcp://*:{EMOTION_PUB_PORT}")

    # Load Haar cascade for face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    print(f"Listening on port {PEPPER_IMAGE_PORT} for images...")
    print(f"Publishing detected emotions on port {EMOTION_PUB_PORT}...")
    print("Press Ctrl+C in terminal or 'q' in the image window to stop.")

    try:
        while True:
            # Receive image bytes from Pepper via ZeroMQ.
            image_bytes = image_sub.recv()
            if not image_bytes:
                continue

            # Convert bytes to a NumPy array and decode the JPEG image.
            image_array = np.frombuffer(image_bytes, dtype=np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            if image is None:
                print("Received invalid image. Skipping...")
                continue

            # For visualization, make a copy for annotations.
            annotated_image = image.copy()

            # Perform face detection.
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

            if len(faces) == 0:
                # No face detected: fallback to "neutral"
                emotion_pub.send_string("neutral")
                cv2.putText(annotated_image, "neutral", (10, 30), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                print("No face detected. Published emotion: neutral")
            else:
                # If a face is detected, use the first detected face.
                x, y, w, h = faces[0]
                cv2.rectangle(annotated_image, (x, y), (x+w, y+h), (0, 255, 0), 2)
                face_img = image[y:y+h, x:x+w]

                # Convert face image from BGR to RGB.
                face_rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(face_rgb)

                # Apply inference transforms.
                input_tensor = inference_transforms(pil_image).unsqueeze(0).to(device)

                # Run inference with mixed precision if on CUDA.
                with torch.no_grad():
                    if device == 'cuda':
                        with torch.cuda.amp.autocast():
                            outputs = model(input_tensor)
                    else:
                        outputs = model(input_tensor)

                # Determine the predicted emotion.
                pred_index = torch.argmax(outputs, dim=1).item()
                detected_emotion = emotion_labels[pred_index]
                emotion_pub.send_string(detected_emotion)
                print("Published emotion:", detected_emotion)

                # Overlay the emotion label on the annotated image.
                cv2.putText(annotated_image, detected_emotion, (x, y-10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

            # Display the annotated image.
            cv2.imshow("Pepper Camera Feed", annotated_image)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            # Optional sleep to control loop rate.
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("Emotion Publisher interrupted by user.")
    finally:
        cv2.destroyAllWindows()
        image_sub.close()
        emotion_pub.close()
        context.term()

if __name__ == '__main__':
    main()
