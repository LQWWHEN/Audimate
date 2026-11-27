"""Native LiveKit endpoint. All SDK and audio device work stays off the Tk thread."""
import asyncio
import queue
import sys
import threading
import time
from .network import timed


class RTCWorker(threading.Thread):
    def __init__(self, api, host, publish=False):
        super().__init__(daemon=True)
        self.api, self.host, self.publish = api, host, publish
        self.cancel = threading.Event()
        self.packets = queue.Queue(24)
        self.error = self.notice = ""
        self.dropped = 0
        self.subscribed = False
        self.sync_sample = None
        self.lag = None
        self.timing = {}

    def add(self, block):
        if self.packets.full():
            try:
                self.packets.get_nowait()
                self.dropped += 1
                self.notice = "上传缓冲已满，已丢弃旧采集帧，可手动降低音质"
            except queue.Empty:
                pass
        self.packets.put_nowait((bytes(block.pcm), time.monotonic() if self.api.timing else None,
                                 getattr(block, "capture_read_ms", None)))

    def run(self):
        try:
            asyncio.run(self.connect())
        except Exception as exc:
            if not self.cancel.is_set():
                self.error = "LiveKit · " + str(exc)
                if self.api.timing:
                    self.api.report(self.host["stream"], "error", "rtc", {"detail":{"phase":"sdk", **self.timing}})

    async def connect(self):
        try:
            from livekit import rtc
        except ImportError as exc:
            raise RuntimeError(
                f"当前 Python 无法加载 LiveKit（{sys.executable}）。"
                f"请从项目根目录运行 client\\start.cmd 后重试。\n{exc}"
            ) from exc
        from server.rtc_audio import FrameConverter, publish_source
        session = rtc.Room()
        timing = self.timing if self.api.timing else None
        with timed(timing, "clock", "clock_sync_ms"):
            await asyncio.to_thread(self.api.calibrate)
        with timed(timing, "grant", "grant_ms"):
            grant = await asyncio.to_thread(self.api.request, "media", {
                "stream": self.host["stream"], "role": "publish" if self.publish else "listen",
                "clock_offset_ms": self.api.offset, "clock_error_ms": self.api.clock_error})
        tracks = asyncio.Queue()
        @session.on("track_subscribed")
        def subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                tracks.put_nowait(track)

        async def listen(track):
            from .playback import Output

            stream = rtc.AudioStream.from_track(track=track, sample_rate=48000, num_channels=2, capacity=30, frame_size_ms=20)
            try:
                with Output(48000, 16) as output:
                    output.observe = getattr(self, "observe", None)
                    output.playback_delay = getattr(self, "playback_delay", None)
                    async for event in stream:
                        with timed(self.timing if self.api.timing else None, "output", "device_ms"):
                            write = asyncio.create_task(asyncio.to_thread(output.write, bytes(event.frame.data.cast("B"))))
                            try:
                                await asyncio.shield(write)
                            finally:
                                # Cancellation must not close the device during a native write.
                                await write
            finally:
                await stream.aclose()

        player = None
        try:
            connect_start = time.monotonic()
            with timed(timing, "connect", "rtc_connect_ms"):
                await session.connect(grant["url"], grant["token"], options=rtc.RoomOptions(auto_subscribe=not self.publish))
            reported = 0
            started = time.monotonic()
            track = None
            if self.publish:
                source = rtc.AudioSource(48000, 2, queue_size_ms=200)
                converter = FrameConverter(self.host["rate"], self.host["bits"])
                with timed(timing, "publish_track", "publish_track_ms"):
                    track = await publish_source(session, source, self.host["bitrate"])
            previous = (0, 0)
            previous_send = (0, 0)
            while not self.cancel.is_set():
                if not session.isconnected():
                    raise OSError("媒体连接已中断")
                if self.publish:
                    try:
                        pcm, queued, capture_ms = self.packets.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(.006)
                        continue
                    frame_timing = self.timing if self.api.timing else None
                    if frame_timing is not None and queued is not None:
                        frame_timing.update(queue_ms=(time.monotonic()-queued)*1000, queue_depth=self.packets.qsize())
                        if capture_ms is not None:
                            frame_timing["capture_read_ms"] = capture_ms
                    with timed(frame_timing, "sdk", "convert_ms"):
                        frames = converter.frames(pcm)
                    with timed(frame_timing, "sdk", "sdk_push_ms"):
                        for frame in frames:
                            await source.capture_frame(frame)
                else:
                    if not tracks.empty():
                        track = tracks.get_nowait()
                        self.subscribed = True
                        if self.api.timing:
                            self.timing["subscribe_ms"] = (time.monotonic()-connect_start)*1000
                        if player:
                            player.cancel()
                        player = asyncio.create_task(listen(track))
                    if player and player.done():
                        player.result()
                        raise OSError("音轨已结束")
                    if track is None and time.monotonic()-started > 10:
                        raise TimeoutError("等待音轨超时")
                    await asyncio.sleep(.05)
                if track and time.monotonic()-reported >= 2:
                    metrics = {"discarded": self.dropped}
                    if self.api.timing:
                        metrics["detail"] = self.timing.copy()
                    for stat in await track.get_stats():
                        if stat.HasField("inbound_rtp"):
                            row = stat.inbound_rtp.inbound
                            if "detail" in metrics and any(f.name == "concealed_samples" for f, _ in row.ListFields()):
                                metrics["detail"]["concealed_samples"] = row.concealed_samples
                            metrics["bytes"] = row.bytes_received
                            self.position = row.bytes_received
                            stamp = row.estimated_playout_timestamp
                            if stamp > 3e12:
                                stamp -= 2208988800000
                            if self.host.get("clock_offset_ms") is not None and stamp:
                                lag = time.time()*1000 + self.api.offset - stamp - self.host["clock_offset_ms"]
                                if 0 <= lag < 60000:
                                    metrics["rtcp_lag_ms"] = lag
                                    metrics["clock_error_ms"] = self.api.clock_error + self.host["clock_error_ms"]
                            count = row.jitter_buffer_emitted_count - previous[1]
                            if count > 0:
                                self.lag = (row.jitter_buffer_delay-previous[0])*1000/count
                                metrics["buffer_ms"] = self.lag
                            self.sync_sample = (time.monotonic(), metrics.get("rtcp_lag_ms") if count > 0 else None)
                            if "rtcp_lag_ms" in metrics:
                                self.lag = metrics["rtcp_lag_ms"]
                            previous = row.jitter_buffer_delay, row.jitter_buffer_emitted_count
                            metrics["jitter_ms"] = stat.inbound_rtp.received.jitter * 1000
                            metrics["lost"] = stat.inbound_rtp.received.packets_lost
                        if stat.HasField("outbound_rtp"):
                            metrics["bytes"] = stat.outbound_rtp.sent.bytes_sent
                            outbound = stat.outbound_rtp.outbound
                            if "detail" in metrics and any(f.name == "total_packet_send_delay" for f, _ in outbound.ListFields()):
                                sent = stat.outbound_rtp.sent.packets_sent
                                delay = outbound.total_packet_send_delay
                                if sent > previous_send[1] and delay >= previous_send[0]:
                                    metrics["detail"]["rtp_send_queue_ms"] = 1000*(delay-previous_send[0])/(sent-previous_send[1])
                                previous_send = delay, sent
                        if stat.HasField("remote_inbound_rtp"):
                            metrics["rtt_ms"] = stat.remote_inbound_rtp.remote_inbound.round_trip_time * 1000
                    await asyncio.to_thread(self.api.report, self.host["stream"],
                                            "overload" if self.dropped else "publish" if self.publish else "play", "rtc", metrics)
                    for key in ("grant_ms","clock_sync_ms","rtc_connect_ms","subscribe_ms","publish_track_ms"):
                        self.timing.pop(key, None)
                    self.dropped = 0
                    reported = time.monotonic()
        finally:
            if player:
                player.cancel()
                await asyncio.gather(player, return_exceptions=True)
            await session.disconnect()
