"""Read audio from the selected process for an authorized room publisher."""
from dataclasses import dataclass
import threading
import time

from host.capture.record import AudioFormat, Capture
from .processes import ProcessSource, selected_process


@dataclass(frozen=True)
class ShareSelection:
    source: ProcessSource
    audio_format: AudioFormat


class ProcessCapture(threading.Thread):
    def __init__(self, dll, selection, on_block, timing=lambda: False):
        super().__init__(name="Audimate room capture")
        self.dll = dll
        self.selection = selection
        self.on_block = on_block
        self.timing = timing
        self.ready = threading.Event()
        self.cancel = threading.Event()
        self.levels = (0.0, 0.0)
        self.error = ""
        self.failure_timing = None

    def run(self):
        source = self.selection.source
        fmt = self.selection.audio_format
        read_start = time.monotonic() if self.timing() else None
        try:
            with selected_process(source), Capture(self.dll, source.pid, fmt) as session:
                self.ready.set()
                peaks = [0, 0]
                next_update = time.monotonic() + 0.1
                scale = 1 << (fmt.bits_per_sample - 1)
                while not self.cancel.is_set():
                    read_start = time.monotonic() if self.timing() else None
                    block = session.read(100)
                    if block is not None and not self.cancel.is_set():
                        if read_start is not None:
                            block.capture_read_ms = (time.monotonic()-read_start)*1000
                        self.on_block(block)
                        samples = block.samples()
                        for channel in range(2):
                            peaks[channel] = max(peaks[channel], max(map(abs, samples[channel::2])))
                    if time.monotonic() >= next_update:
                        self.levels = peaks[0] / scale, peaks[1] / scale
                        peaks = [0, 0]
                        next_update = time.monotonic() + 0.1
        except (OSError, ValueError, AttributeError) as exc:
            if read_start is not None and self.timing():
                self.failure_timing = {"phase":"capture", "elapsed_ms":(time.monotonic()-read_start)*1000}
            self.error = str(exc)
