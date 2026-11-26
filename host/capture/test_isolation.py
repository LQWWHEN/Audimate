"""Windows integration test. Plays quiet stereo tones through the default output."""
import argparse
from array import array
from contextlib import ExitStack
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

if __package__:
    from .record import Capture, windows_build
else:
    from record import Capture, windows_build


# Keep the playback fixture fixed while varying the requested capture format.
SOURCE_RATE = 48000


def write_tone(path, left, right):
    samples = array("h")
    for i in range(SOURCE_RATE):
        for hz in (left, right):
            samples.append(round(1600 * math.sin(2 * math.pi * hz * i / SOURCE_RATE)))
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, SOURCE_RATE, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def player(path):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC |
                       winsound.SND_LOOP | winsound.SND_NODEFAULT)
    print("ready", flush=True)
    for command in sys.stdin:
        if command.strip() == "pause":
            winsound.PlaySound(None, 0)
            print("paused", flush=True)


def measure(session):
    # Discard startup/transition audio, then analyze 300 ms from this source.
    deadline = time.monotonic() + 5
    channels = [[], []]
    previous = None
    for i in range(60):
        block = session.read(1000)
        if block is None or time.monotonic() > deadline:
            raise RuntimeError("Timed out waiting for PCM")
        if i >= 30:
            if previous is not None and block.sequence != previous + 1:
                raise RuntimeError("Reader lost blocks during measurement")
            for channel in range(2):
                channels[channel].extend(block.pcm[channel::2])
            previous = block.sequence
    return channels


def magnitude(samples, hz, sample_rate=SOURCE_RATE):
    # A single-bin DFT on an integer number of tone periods.
    angle = 2 * math.pi * hz / sample_rate
    real = sum(x * math.cos(angle * i) for i, x in enumerate(samples))
    imag = sum(x * math.sin(angle * i) for i, x in enumerate(samples))
    return 2 * math.hypot(real, imag) / len(samples)


def start_process(stack, *args):
    proc = subprocess.Popen([sys.executable, __file__, *map(str, args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def finish():
        proc.stdin.close()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    stack.callback(finish)
    return proc


def expect(proc, message):
    if proc.stdout.readline().strip() != message:
        raise RuntimeError(f"Test playback process {proc.pid} failed")


def parent_player(path):
    # The selected PID itself never renders audio. Its child does.
    with ExitStack() as stack:
        child = start_process(stack, "--player", path)
        expect(child, "ready")
        print("ready", flush=True)
        for command in sys.stdin:
            child.stdin.write(command)
            child.stdin.flush()
            print(child.stdout.readline().strip(), flush=True)


def verify_wav_formats(dll, pid, directory):
    cases = [
        ([], 48000, 16),
        (["--quality", "cd"], 44100, 16),
        (["--quality", "studio"], 96000, 24),
        (["--quality", "studio", "--sample-rate", "88200", "--bit-depth", "32"], 88200, 32),
    ]
    reference = []
    for options, rate, bits in cases:
        path = directory / f"{rate}-{bits}.wav"
        subprocess.run([sys.executable, str(Path(__file__).with_name("record.py")),
                        "--dll", str(dll), "--pid", str(pid), "--seconds", "1",
                        "--output", str(path), *options], check=True, timeout=15)
        width = bits // 8
        with wave.open(str(path), "rb") as wav:
            assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels(),
                    wav.getnframes()) == (rate, width, 2, rate)
            wav.setpos(rate // 2)
            data = wav.readframes(rate // 2)
        assert len(data) == rate * width
        for channel, hz, opposite in ((0, 700, 1200), (1, 1200, 700)):
            samples = [int.from_bytes(data[i:i+width], "little", signed=True)
                       for i in range(channel * width, len(data), 2 * width)]
            amplitude = magnitude(samples, hz, rate)
            unwanted = max(magnitude(samples, f, rate) for f in (opposite, 1800))
            # Compare the same tone in 16-bit units across different WAV widths.
            normalized = amplitude / (1 << (bits - 16))
            assert normalized > 8
            if not options:
                reference.append(normalized)
            else:
                assert math.isclose(normalized, reference[channel], rel_tol=0.05)
            assert unwanted < amplitude * 0.02


def verify(dll):
    print(f"Testing process isolation on Windows build {windows_build()}, DLL {Path(dll).resolve()}", flush=True)
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        target_file = Path(tmp) / "target.wav"
        other_file = Path(tmp) / "other.wav"
        write_tone(target_file, 700, 1200)
        write_tone(other_file, 1800, 1800)
        target = start_process(stack, "--parent", target_file)
        other = start_process(stack, "--player", other_file)
        expect(target, "ready")
        expect(other, "ready")
        verify_wav_formats(dll, target.pid, Path(tmp))
        with Capture(dll, target.pid) as session:
            for samples, hz, opposite in zip(measure(session), (700, 1200), (1200, 700)):
                signal = magnitude(samples, hz)
                leaked = max(magnitude(samples, f) for f in (opposite, 1800))
                print(f"{hz} Hz amplitude {signal:.2f}, unwanted {leaked:.2f}")
                if signal < 8 or leaked > signal * 0.02:
                    raise AssertionError("Target channel missing or unwanted tone present")
            target.stdin.write("pause\n")
            target.stdin.flush()
            expect(target, "paused")
            peak = max(abs(x) for channel in measure(session) for x in channel)
            print(f"Paused target peak {peak} PCM counts")
            if peak > 2:
                raise AssertionError("Target failed to go silent")
            before = session.read(1000)
            time.sleep(0.35)
            after = session.read(1000)
            if before is None or after is None or after.sequence <= before.sequence + 1:
                raise AssertionError("Slow reader did not skip stale blocks")
            session.stop()
            try:
                session.read(0)
            except OSError as error:
                if error.hresult != 0x800703E3:
                    raise
            else:
                raise AssertionError("Stopped session returned stale audio")
        with Capture(dll, other.pid) as session:
            amplitude = min(magnitude(samples, 1800) for samples in measure(session))
            print(f"Independent 1800 Hz source amplitude {amplitude:.2f}")
            if amplitude < 8:
                raise AssertionError("Interference source was inaudible")
            other.stdin.close()
            other.wait(5)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    session.read(200)
                except OSError as error:
                    if error.hresult != 0x8007042B:
                        raise
                    break
            else:
                raise AssertionError("Capture did not report source exit")
    print("PASS: quality presets, custom WAV format, process tree, stereo, isolation, "
          "pause, overflow, stop, reselection and source exit")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--player"]:
        player(sys.argv[2])
    elif sys.argv[1:2] == ["--parent"]:
        parent_player(sys.argv[2])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--dll", required=True)
        verify(parser.parse_args().dll)
