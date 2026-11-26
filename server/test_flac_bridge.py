"""Run against a configured SFU using an isolated HTTP app, DB and media room."""
import asyncio
from contextlib import ExitStack
import math
from pathlib import Path
import struct
import tempfile
import threading
from unittest.mock import patch
from wsgiref.simple_server import make_server

from livekit import rtc
from client.network import API
from . import flac
from .app import App
from .dev import ThreadedServer
from .test_app import Quiet


async def check():
    with tempfile.TemporaryDirectory() as folder, ExitStack() as cleanup:
        app = App(Path(folder)/"room.db")
        media = app.room.media
        if not media.enabled:
            raise RuntimeError("Set LiveKit credentials for an isolated test room")
        http = make_server("127.0.0.1", 0, app, ThreadedServer, Quiet)
        cleanup.callback(http.server_close)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        cleanup.callback(http.shutdown)
        peers = [API(f"http://127.0.0.1:{http.server_port}") for _ in range(4)]
        for i, peer in enumerate(peers):
            peer.request("register", dict(name=f"peer{i}",password="isolated-test-account"))
        host = peers[0].request("stage/request", dict(source="tone", rate=48000, bits=16,
                                                     codec="pcm", compression="flac"))["host"]
        end = dict(stream=host["stream"])
        cleanup.callback(peers[0].request, "stage/leave", end)
        path = "audio?compression=flac&stream="+host["stream"]
        pcm = b"".join(struct.pack("<hh", 0, round(2000*math.sin(i*2*math.pi/96))) for i in range(9600))
        packet = flac.encode(pcm, 48000, 16)
        peers[0].request(path, packet)
        assert flac.decode(peers[1].request(path)[1],48000,16) == pcm and media.bridge is None

        async def receive(peer):
            session = rtc.Room()
            tracks = asyncio.Queue()
            @session.on("track_subscribed")
            def subscribed(track, publication, participant):
                tracks.put_nowait((track, publication.sid, participant.identity))
            grant = peer.request("media", dict(end, role="listen"))
            try:
                await session.connect(media.internal.replace("http","ws",1), grant["token"])
                track, sid, publisher = await tracks.get()
                frames = rtc.AudioStream.from_track(track=track, sample_rate=48000, num_channels=2)
                try:
                    async for event in frames:
                        samples = event.frame.data
                        if max(samples) > 100:
                            return sid, publisher
                finally:
                    await frames.aclose()
            finally:
                await session.disconnect()

        async def feed():
            while True:
                await asyncio.to_thread(peers[0].request, path, packet)
                await asyncio.sleep(.18)

        with patch.object(media, "_bridge", wraps=media._bridge) as bridge:
            upload = asyncio.create_task(feed())
            try:
                first, second = await asyncio.wait_for(asyncio.gather(*(receive(p) for p in peers[2:])), 35)
                assert first == second and first[1] == "bridge"
                assert bridge.call_count == 1
                assert flac.decode(peers[1].request(path)[1],48000,16) == pcm
                print("PASS: two Opus listeners received one audible shared track; FLAC listener received exact samples")
            finally:
                upload.cancel()
                await asyncio.gather(upload, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(check())
