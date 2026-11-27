"""Keep a single Windows output stream for the lifetime of each subscription."""
import sounddevice


class Output:
    def __init__(self, rate, bits):
        self.observe = None
        self.frame_bytes = rate // 50 * 2 * (bits // 8)
        self.stream = sounddevice.RawOutputStream(samplerate=rate, channels=2, dtype="int"+str(bits), latency="low")

    def __enter__(self):
        self.stream.start()
        return self

    def write(self, pcm):
        if self.observe is None:
            return self.stream.write(pcm)
        # HTTP audio can arrive in 200 ms packets. Advance the visual clock
        # with device writes, even when an entire packet is already buffered.
        underrun = False
        for start in range(0, len(pcm), self.frame_bytes):
            chunk = pcm[start:start + self.frame_bytes]
            underrun |= bool(self.stream.write(chunk))
            self.observe(chunk)
        return underrun

    def __exit__(self, *exc):
        try:
            self.stream.abort()
        finally:
            self.stream.close()
