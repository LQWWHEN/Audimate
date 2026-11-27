"""LiveKit credentials, bounded sample timeline and a shared media bridge."""
import asyncio
import base64
from collections import deque
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from urllib.request import Request, urlopen

log = logging.getLogger("audimate.media")

if __package__:
    from .traffic import RtpTraffic
else:
    from traffic import RtpTraffic


class History:
    def __init__(self, seconds, byte_limit=64 * 1024**2, block_limit=3000):
        self.byte_limit = byte_limit
        self.block_limit = block_limit
        self.seconds = seconds
        self.clear()

    def clear(self):
        self.blocks = deque()
        self.end_frame = self.sequence = self.payload_bytes = 0
        self.last_eviction = None

    def prune(self, now, rate):
        while self.blocks:
            oldest = self.blocks[0]
            if self.payload_bytes > self.byte_limit:
                reason = "bytes"
            elif len(self.blocks) > self.block_limit:
                reason = "blocks"
            elif now - oldest[4] >= self.seconds:
                reason = "age"
            elif self.end_frame - oldest[2] > rate * self.seconds:
                reason = "duration"
            else:
                break
            self.blocks.popleft()
            self.payload_bytes -= len(oldest[1]) + len(oldest[7] or b"")
            self.last_eviction = reason

    def available_ms(self, rate):
        return round((self.end_frame - self.blocks[0][2]) * 1000 / rate) if self.blocks else 0

    def append(self, pcm, rate, width, captured=None, trace=None, *, wire=None):
        now = time.monotonic()
        start = self.end_frame
        self.end_frame += len(pcm) // (2 * width)
        self.sequence += 1
        self.payload_bytes += len(pcm) + len(wire or b"")
        self.blocks.append((self.sequence, pcm, start, self.end_frame, now, captured, trace, wire))
        self.prune(now, rate)

    def read(self, after, rate, align):
        self.prune(time.monotonic(), rate)
        if not self.blocks or align and time.monotonic()-self.blocks[-1][4] > 1:
            return None, False
        expired = 0 <= after < self.blocks[0][0] - 1
        if align or expired:
            target = self.end_frame - round(rate * 0.6)
            return next((b for b in self.blocks if b[3] > target), self.blocks[-1]), expired
        if after < 0:
            return self.blocks[-1], False
        return next((b for b in self.blocks if b[0] > after), None), False


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Media:
    def __init__(self):
        self.url = os.getenv("LIVEKIT_URL", "")
        self.internal = os.getenv("LIVEKIT_INTERNAL_URL", "http://127.0.0.1:7880")
        self.key = os.getenv("LIVEKIT_API_KEY", "")
        self.secret = os.getenv("LIVEKIT_API_SECRET", "").encode()
        self.enabled = bool(self.url and self.key and self.secret)
        self.loop = None
        self.bridge = None
        self.traffic = RtpTraffic(os.environ.get("LIVEKIT_STATS_URL"))

    def token(self, stream, identity, *, publish=False, subscribe=False, admin=False):
        now = int(time.time())
        grants = {"room": "audimate-" + stream}
        if admin:
            grants["roomCreate" if admin == "DeleteRoom" else "roomAdmin"] = True
        else:
            grants.update(roomJoin=True, canPublish=publish, canSubscribe=subscribe,
                          canPublishData=False, canPublishSources=["microphone"] if publish else [])
        claims = dict(iss=self.key, sub=identity, nbf=now-3, exp=now+45, video=grants)
        header = b64(b'{"alg":"HS256","typ":"JWT"}')
        payload = header + "." + b64(json.dumps(claims, separators=(",", ":")).encode())
        return payload + "." + b64(hmac.digest(self.secret, payload.encode(), "sha256"))

    def identity(self, stream, key):
        return hmac.new(self.secret, (stream + key).encode(), hashlib.sha256).hexdigest()[:32]

    def current_grant(self, token, room):
        if not self.enabled or not room.host or len(token) > 12000:
            return False
        try:
            header, payload, sig = token.split(".")
            if not hmac.compare_digest(sig, b64(hmac.digest(self.secret, (header+"."+payload).encode(), "sha256"))):
                return False
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            stream = room.host["stream"]
            return (claims["iss"] == self.key and claims["exp"] > time.time() and
                    claims["video"]["room"] == "audimate-" + stream and
                    any(self.identity(stream, k) == claims["sub"] for k in room.members))
        except (KeyError, ValueError, TypeError, UnicodeError):
            return False

    def remove(self, stream, key=None):
        if not self.enabled:
            return
        def work():
            method = "RemoveParticipant" if key else "DeleteRoom"
            data = {"room": "audimate-" + stream}
            if key:
                data["identity"] = self.identity(stream, key)
            for delay in (0, 0.3, 1.5):
                time.sleep(delay)
                try:
                    request = Request(self.internal + "/twirp/livekit.RoomService/" + method,
                                      json.dumps(data).encode(), {"Content-Type": "application/json",
                                      "Authorization": "Bearer " + self.token(stream, "control", admin=method)})
                    with urlopen(request, timeout=3):
                        return
                except OSError as exc:
                    if getattr(exc, "code", None) == 404:
                        return
            log.warning("LiveKit room cleanup failed: %s", method)
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.bridge:
            self.bridge.cancel()
            self.bridge = None

    def start_bridge(self, host, room):
        if self.bridge and not self.bridge.done():
            return
        if not self.loop:
            self.loop = asyncio.new_event_loop()
            threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.bridge = asyncio.run_coroutine_threadsafe(self._bridge(host.copy(), room), self.loop)

    async def _bridge(self, host, room):
        from livekit import rtc
        logging.getLogger("livekit").setLevel(logging.ERROR)
        stream = host["stream"]
        session = rtc.Room()
        record = host["codec"] == "opus"
        token = self.token(stream, "bridge", publish=not record, subscribe=record)
        async def consume(track):
            buffer = bytearray()
            start = time.monotonic()
            async for event in rtc.AudioStream.from_track(track=track, sample_rate=48000, num_channels=2):
                if not buffer:
                    start = time.monotonic()
                buffer.extend(event.frame.data.cast("B"))
                if len(buffer) >= 48000 * 4 * host["audio_block_ms"] // 1000:
                    with room.lock:
                        if not room.host or room.host["stream"] != stream:
                            return
                        trace = None
                        if room.monitor.development and room.monitor.level == "debug":
                            trace = dict(pack_ms=(time.monotonic()-start)*1000,packet_bytes=len(buffer))
                        room.history.append(bytes(buffer), 48000, 2, trace=trace)
                        room.last_audio = time.monotonic()
                        if trace:
                            trace["server_seq"] = room.history.sequence
                            room.monitor.service_sample(stream, "opus_to_pcm", "rtc", trace)
                    buffer.clear()

        tasks = []
        @session.on("track_subscribed")
        def subscribed(track, publication, participant):
            if record and participant.identity == self.identity(stream, host["key"]):
                tasks.append(asyncio.create_task(consume(track)))

        # The SDK negotiates stereo on its default transport. Forcing split peer
        # connections here downmixes the native subscriber on the pinned release.
        join = asyncio.create_task(session.connect(self.internal.replace("http", "ws", 1), token,
                                   options=rtc.RoomOptions(auto_subscribe=record, connect_timeout=10)))
        try:
            await asyncio.shield(join)
            if record:
                await asyncio.Future()
            if __package__:
                from .rtc_audio import FrameConverter, publish_source
            else:
                from rtc_audio import FrameConverter, publish_source
            source = rtc.AudioSource(48000, 2, queue_size_ms=240)
            await publish_source(session, source)
            converter = FrameConverter(host["rate"], host["bits"])
            after = -1
            while True:
                with room.lock:
                    if not room.host or room.host["stream"] != stream:
                        break
                    block, _ = room.history.read(after, host["rate"], False)
                if not block:
                    await asyncio.sleep(0.02)
                    continue
                after = block[0]
                start = time.monotonic()
                frames = converter.frames(block[1])
                converted = time.monotonic()
                for frame in frames:
                    await source.capture_frame(frame)
                if room.monitor.development and room.monitor.level == "debug":
                    finished = time.monotonic()
                    with room.lock:
                        if room.host and room.host["stream"] == stream:
                            metrics = dict(server_seq=block[0], residence_ms=(start-block[4])*1000,
                                           convert_ms=(converted-start)*1000,sdk_push_ms=(finished-converted)*1000)
                            if block[6] and "source_seq" in block[6]:
                                metrics["source_seq"] = block[6]["source_seq"]
                            room.monitor.service_sample(stream, "pcm_to_opus", "rtc", metrics)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with room.lock:
                room.monitor.event("异常", "媒体桥接失败 · " + type(exc).__name__)
            log.exception("LiveKit bridge failed")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(join, return_exceptions=True)
            await session.disconnect()
