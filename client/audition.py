"""An original looping synth étude for auditioning the three visual presets offline."""
import threading

import numpy as np

from .playback import Output


def passage():
    rate, beat = 48000, .48
    t = np.arange(round(rate * beat * 32)) / rate
    step = (t / beat).astype(int)
    phase = t % beat
    pitch = np.array([110, 87.307, 130.813, 97.999])[(step // 8) % 4]
    bass = np.sin(2*np.pi*phase*pitch) * np.exp(-phase*7)
    kick = np.sin(2*np.pi*(46*phase + 3*(1-np.exp(-phase*27)))) * np.exp(-phase*16)
    noise = np.random.default_rng(311).normal(0, .35, len(t))
    snare = (noise + .38*np.sin(2*np.pi*182*phase))*np.exp(-phase*24)*(step % 4 == 2)
    hat = np.diff(noise, prepend=0)*np.exp(-(t % (beat/2))*98)
    arp = (np.sin(2*np.pi*pitch*np.choose(step % 4, [2,3,4,3])*phase)
           * np.exp(-phase*8)*np.minimum(1, phase*90))
    beat_gain = np.where(step < 24, 1., .12)
    core = .15*bass + beat_gain*(.32*kick + .29*snare + .055*hat)
    stereo = np.column_stack((core+.09*arp, core+.085*np.roll(arp, 400)))
    envelope = np.minimum(1, np.minimum(t, len(t)/rate-t)*45)
    return (stereo * envelope[:, None] * 32767).clip(-32767,32767).astype("<i2").tobytes()


class Audition(threading.Thread):
    def __init__(self, observe):
        super().__init__(name="Audimate audition", daemon=True)
        self.observe = observe
        self.cancel = threading.Event()
        self.error = ""

    def run(self):
        try:
            audio = passage()
            with Output(48000, 16) as output:
                output.observe = self.observe
                while not self.cancel.is_set():
                    for start in range(0, len(audio), 3840):
                        if self.cancel.is_set():
                            return
                        output.write(audio[start:start+3840])
        except Exception as exc:
            self.error = str(exc)
