#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Pepper Image Publisher for DeepFace (ZeroMQ Bridge)
- Captures images from Pepper's top camera using ALVideoDevice.
- Sends the images via ZeroMQ to a Python 3 module for DeepFace analysis.

Usage:
    python2 pepper_camera_publisher.py --pip <Pepper_IP> --pport <Port>
"""

from optparse import OptionParser
import naoqi
import time
import zmq
import cv2
import numpy as np
from naoqi import ALProxy

# ZeroMQ port to send images
IMAGE_PUB_PORT = 5558  

def main():
    # Parse command-line arguments for Pepper's IP and port.
    parser = OptionParser()
    parser.add_option("--pip", help="Pepper IP address", dest="pip")
    parser.add_option("--pport", help="Pepper port (default: 9559)", dest="pport", type="int", default=9559)
    (opts, args_) = parser.parse_args()
    pepper_ip = opts.pip
    pepper_port = opts.pport

    # Connect to Pepper
    try:
        video_device = ALProxy("ALVideoDevice", pepper_ip, pepper_port)
        print("Connected to Pepper's camera at {}:{}".format(pepper_ip, pepper_port))
    except Exception as e:
        print("Cannot connect to Pepper:", e)
        return

    # Subscribe to the top camera (index 0), resolution: 2 (QVGA), color space: 11 (RGB), frame rate: 10.
    subscriber_id = video_device.subscribeCamera("PepperCamera", 0, 2, 11, 30)

    # Set up ZeroMQ publisher
    context = zmq.Context()
    image_pub = context.socket(zmq.PUB)
    image_pub.bind("tcp://*:{0}".format(IMAGE_PUB_PORT))

    print("Pepper Camera Publisher running (Publishing on {})...".format(IMAGE_PUB_PORT))

    try:
        while True:
            # Capture an image from Pepper's camera.
            result = video_device.getImageRemote(subscriber_id)
            if result is None:
                print("Failed to get image from Pepper's camera.")
                time.sleep(1)
                continue

            # Extract image data
            width = result[0]
            height = result[1]
            image_data = result[6]

            if image_data is None:
                continue

            # Convert binary image data to NumPy array
            image_array = np.frombuffer(image_data, dtype=np.uint8)
            try:
                image_array = image_array.reshape((height, width, 3))
            except Exception as e:
                print("Reshape error:", e)
                continue

            # Convert image to JPEG format
            _, jpeg_image = cv2.imencode('.jpg', image_array)

            # Send image via ZeroMQ
            image_pub.send(jpeg_image.tobytes())
            print("Published image to ZeroMQ.")

            time.sleep(0.033)  # Send every 5 seconds
            
    except KeyboardInterrupt:
        print("Pepper Camera Publisher interrupted by user.")
    finally:
        video_device.unsubscribe(subscriber_id)
        image_pub.close()
        context.term()

if __name__ == '__main__':
    main()
