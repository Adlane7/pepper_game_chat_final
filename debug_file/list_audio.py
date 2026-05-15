import pyaudio

def list_input_devices():
    p = pyaudio.PyAudio()
    print("Available input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            print("  Device index {}: {}".format(i, info.get("name", "Unknown")))
    p.terminate()

# Call this function (temporarily) to see the list:
if __name__ == "__main__":
    list_input_devices()
