"""Keep a single Windows output stream for the lifetime of each subscription."""
import sounddevice


class Output:
    def __init__(self, rate, bits):
        self.observe = None
        self.playback_delay = None
        self.pending = bytearray()
        self.rate = rate
        self.sample_bytes = 2 * (bits // 8)
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
            delay = round(self.rate * self.playback_delay() / 1000) * self.sample_bytes if self.playback_delay else 0
            if delay or self.pending:
                # Keep exactly the requested number of sample frames behind the
                # analyzer. Shortening the delay releases all queued samples.
                silence = bytes(min(len(chunk), max(0, delay - len(self.pending))))
                self.pending.extend(chunk)
                count = max(0, len(self.pending) - delay)
                audible = silence + self.pending[:count]
                del self.pending[:count]
            else:
                audible = chunk
            underrun |= bool(self.stream.write(audible))
            self.observe(chunk)
        return underrun

    def __exit__(self, *exc):
        try:
            self.stream.abort()
        finally:
            self.stream.close()
