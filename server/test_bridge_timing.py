"""Run on the SFU host against two temporary media rooms without opening the user room."""
import asyncio
from array import array
import math
import secrets


async def check(codec):
    from livekit import rtc
    if __package__:
        from .app import Room
        from .rtc_audio import publish_source
    else:
        from app import Room
        from rtc_audio import publish_source

    room = Room(development=True)
    if not room.media.enabled:
        raise RuntimeError("Run with the test SFU environment")
    stream = secrets.token_hex(12)
    room.host = dict(key="sender", stream=stream, codec=codec, rate=48000, bits=16)
    bridge = asyncio.create_task(room.media._bridge(room.host.copy(), room))
    publisher = codec == "opus"
    event = "opus_to_pcm" if publisher else "pcm_to_opus"
    session = rtc.Room()
    tracks = asyncio.Queue()

    @session.on("track_subscribed")
    def subscribed(track, *_):
        tracks.put_nowait(track)

    async def listen():
        track = await tracks.get()
        frames = rtc.AudioStream.from_track(track=track, sample_rate=48000, num_channels=2)
        try:
            async for frame in frames:
                if max(frame.frame.data) > 100:
                    return True
        finally:
            await frames.aclose()

    receiver = source = None
    try:
        key = "sender" if publisher else "receiver"
        token = room.media.token(stream, room.media.identity(stream, key), publish=publisher, subscribe=not publisher)
        await session.connect(room.media.internal.replace("http", "ws", 1), token, options=rtc.RoomOptions(auto_subscribe=not publisher))
        packet = array("h", (round(1600*math.sin(i*2*math.pi*500/48000)) for i in range(960) for _ in range(2))).tobytes()
        if publisher:
            source = rtc.AudioSource(48000, 2)
            track = await publish_source(session, source)
        else:
            receiver = asyncio.create_task(listen())
        for seq in range(1, 101):
            if publisher:
                for _ in range(10):
                    await source.capture_frame(rtc.AudioFrame(packet,48000,2,960))
                    await asyncio.sleep(.02)
            with room.lock:
                if not publisher:
                    room.history.append(packet*10,48000,2,trace={"source_seq":seq})
                block = room.history.blocks[-1] if room.history.blocks else None
                rows = [s["detail"] for s in room.monitor.samples if s["event"] == event]
            audible = (block and max(memoryview(block[1]).cast("h")) > 100) if publisher else receiver.done() and receiver.result()
            expected = {"server_seq","pack_ms"} if publisher else {"server_seq","source_seq","convert_ms","sdk_push_ms","residence_ms"}
            if audible and any(expected <= r.keys() for r in rows):
                print("PASS",event,"audible output and",", ".join(sorted(expected)),flush=True)
                return
            if not publisher:
                await asyncio.sleep(.2)
        raise AssertionError("Missing audible output or timing for "+event)
    finally:
        if receiver:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        bridge.cancel()
        await asyncio.gather(bridge,return_exceptions=True)
        await session.disconnect()
        if source:
            await source.aclose()
        room.media.remove(stream)


if __name__ == "__main__":
    async def run():
        for codec in ("pcm", "opus"):
            await asyncio.wait_for(check(codec), timeout=40)
    asyncio.run(run())
