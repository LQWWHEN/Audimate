"""A local sharing selection and its capture lifetime, independent of room roles."""
from dataclasses import dataclass
import threading
import time

from host.capture.record import AudioFormat, Capture
from .processes import ProcessSource, selected_process


@dataclass(frozen=True)
class ShareSelection:
    source: ProcessSource
    audio_format: AudioFormat


class LocalCapture(threading.Thread):
    def __init__(self, dll, selection, on_block=None):
        super().__init__(name="Audimate capture consumer")
        self.dll = dll
        self.selection = selection
        self.on_block = on_block
        self.ready = threading.Event()
        self.cancel = threading.Event()
        self.levels = (0.0, 0.0)
        self.error = ""

    def run(self):
        source = self.selection.source
        fmt = self.selection.audio_format
        try:
            with selected_process(source), Capture(self.dll, source.pid, fmt) as session:
                self.ready.set()
                peaks = [0, 0]
                next_update = time.monotonic() + 0.1
                scale = 1 << (fmt.bits_per_sample - 1)
                while not self.cancel.is_set():
                    block = session.read(100)
                    if block is not None:
                        if self.on_block:
                            self.on_block(block)
                        samples = block.samples()
                        for channel in range(2):
                            peaks[channel] = max(peaks[channel], max(map(abs, samples[channel::2])))
                    if time.monotonic() >= next_update:
                        self.levels = peaks[0] / scale, peaks[1] / scale
                        peaks = [0, 0]
                        next_update = time.monotonic() + 0.1
        except (OSError, ValueError, AttributeError) as exc:
            self.error = str(exc)
