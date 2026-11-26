"""Keep a single Windows output stream for the lifetime of each subscription."""
import sounddevice


class Output:
    def __init__(self, rate, bits):
        self.stream = sounddevice.RawOutputStream(samplerate=rate, channels=2, dtype="int"+str(bits), latency="low")

    def __enter__(self):
        self.stream.start()
        return self

    def write(self, pcm):
        return self.stream.write(pcm)

    def __exit__(self, *exc):
        try:
            self.stream.abort()
        finally:
            self.stream.close()
