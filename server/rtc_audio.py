"""Shared Windows publisher / server PCM-to-Opus conversion."""
import logging
from livekit import rtc

# libwebrtc warnings can contain complete ICE candidates. Use structured room diagnostics.
logging.getLogger("livekit").setLevel(logging.ERROR)


class FrameConverter:
    def __init__(self, rate, bits):
        self.rate = rate
        self.width = bits // 8
        self.resampler = rtc.AudioResampler(rate, 48000, num_channels=2) if rate != 48000 else None

    def frames(self, pcm):
        if self.width != 2:
            narrowed = bytearray(len(pcm) // self.width * 2)
            narrowed[::2] = pcm[self.width - 2::self.width]
            narrowed[1::2] = pcm[self.width - 1::self.width]
            pcm = narrowed
        frame = rtc.AudioFrame(pcm, self.rate, 2, len(pcm) // 4)
        return self.resampler.push(frame) if self.resampler else (frame,)


async def publish_source(room, source, bitrate=128000):
    track = rtc.LocalAudioTrack.create_audio_track("audimate-stereo", source)
    options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE, dtx=False, red=True)
    options.audio_encoding.max_bitrate = bitrate
    await room.local_participant.publish_track(track, options)
    return track
