#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, rclpy, json, time, datetime, fractions
import av
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

OUT_DIR = os.path.abspath("recordings/video")
TOPIC   = os.getenv("REC_TOPIC", "/camera/camera/color/image_raw")
V_CODEC = os.getenv("REC_VCODEC", "libx264")   # ← use libx264
V_PRESET= os.getenv("REC_PRESET","veryfast")
CRF     = int(os.getenv("REC_CRF","20"))

class VideoRecorderVFR(Node):
    def __init__(self):
        super().__init__('video_recorder_vfr')
        os.makedirs(OUT_DIR, exist_ok=True)
        ts = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%S')
        self.video_path = os.path.join(OUT_DIR, f"{ts}_realsense_color.mp4")
        self.meta_path  = self.video_path + ".json"

        self.bridge = CvBridge()
        self.container = None
        self.stream = None
        self.frames = 0
        self.size = None
        self.t0_ns = None              # first ROS stamp
        self.last_pts = -1             # enforce monotonic PTS
        self.start_epoch = time.time()
        self.time_base = fractions.Fraction(1, 1000)  # ← milliseconds

        self._write_meta({
            "video_start_epoch_s": self.start_epoch,
            "fps_mode": "vfr",
            "path": self.video_path,
            "topic": TOPIC,
            "codec": V_CODEC
        })

        self.get_logger().info(f"Subscribing to: {TOPIC}")
        self.sub = self.create_subscription(Image, TOPIC, self.cb, qos_profile_sensor_data)

    def _write_meta(self, obj):
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, self.meta_path)

    def _open_container(self, width, height):
        self.size = (width, height)
        self.get_logger().info(f"Opening VFR writer (codec={V_CODEC}, size={self.size})")
        # Explicit format helps some builds
        self.container = av.open(self.video_path, mode="w", format="mp4")
        # Give a nominal rate (30) but rely on PTS for true timing (VFR)
        self.stream = self.container.add_stream(V_CODEC, rate=30)
        self.stream.width  = width
        self.stream.height = height
        self.stream.pix_fmt = "yuv420p"
        self.stream.time_base = self.time_base
        # Some builds need codec_context set as well
        try:
            self.stream.codec_context.time_base = self.time_base
        except Exception:
            pass

        if V_CODEC == "libx264":
            # preset + crf
            self.stream.codec_context.options = {
                "preset": V_PRESET,
                "crf": str(CRF),
                # safer for VFR MP4:
                "keyint": "90",        # ~3s @30fps
                "scenecut": "40",
            }

        self._write_meta({
            "video_start_epoch_s": self.start_epoch,
            "fps_mode": "vfr",
            "path": self.video_path,
            "topic": TOPIC,
            "codec": V_CODEC
        })

    def cb(self, msg: Image):
        frame_bgr = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        h, w = frame_bgr.shape[:2]
        if self.container is None:
            self._open_container(w, h)

        # ROS stamp → ns; guard missing headers just in case
        stamp_ns = msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec
        if self.t0_ns is None:
            self.t0_ns = stamp_ns
        # Convert to ms in our time_base (1/1000)
        pts_ms = (stamp_ns - self.t0_ns) // 1_000_000
        # Enforce strictly increasing PTS (no duplicates)
        if pts_ms <= self.last_pts:
            pts_ms = self.last_pts + 1
        self.last_pts = pts_ms

        vf = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        # Let encoder convert to yuv420p; set pts/time_base
        vf.pts = int(pts_ms)
        vf.time_base = self.time_base

        for packet in self.stream.encode(vf):
            self.container.mux(packet)

        self.frames += 1
        if (self.frames % 100) == 0:
            self.get_logger().info(f"Wrote {self.frames} frames... (pts_ms={pts_ms})")

    def finalize(self):
        end_epoch = time.time()
        duration_s = max(0.0, end_epoch - self.start_epoch)
        # Flush safely
        try:
            if self.stream and self.container:
                for packet in self.stream.encode():
                    self.container.mux(packet)
        except Exception as e:
            self.get_logger().warn(f"Flush error (ignored): {e}")
        finally:
            try:
                if self.container:
                    self.container.close()
            except Exception as e:
                self.get_logger().warn(f"Container close error (ignored): {e}")

        # Write meta
        try:
            with open(self.meta_path, "r") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        meta.update({
            "video_end_epoch_s": end_epoch,
            "duration_s": duration_s,
            "frames": self.frames,
            "effective_fps_runtime": round(self.frames / duration_s, 3) if duration_s > 0 else None
        })
        self._write_meta(meta)

def main():
    rclpy.init()
    n = VideoRecorderVFR()
    try:
        rclpy.spin(n)
    finally:
        try:
            n.get_logger().info(f"Finalizing. Frames: {n.frames}. File: {n.video_path}")
        finally:
            n.finalize()
            n.destroy_node()
            rclpy.shutdown()

if __name__ == "__main__":
    main()
