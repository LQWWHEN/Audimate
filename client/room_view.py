"""Qt's room model. Network futures complete on the GUI clock; audio stays on workers."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage

from .covers import fetch_cover
from . import local_settings
from .disc import ICE, demo_art
from .network import API, AudioListener, AudioUpload, RoomError, default_server
from .rtc import RTCWorker
from .spectrum import Spectrum, STYLES
from .sync import SyncPolicy, threshold_ms


def quality(host):
    if host.get("codec") == "opus":
        return f"Opus   {host['bitrate']//1000} kbps"
    codec = "FLAC" if host.get("compression") == "flac" else "PCM"
    return f"{codec}   {host['rate']/1000:g} kHz / {host['bits']} bit"


def load_art(url):
    img = fetch_cover(url, 768)
    if img is None:
        return QImage(), ICE
    img = img.convert("RGB")
    raw = img.tobytes()
    image = QImage(raw, img.width, img.height, img.width*3, QImage.Format_RGB888).copy()
    swatches = img.resize((60, 60)).quantize(8).convert("RGB").getcolors()
    def score(item):
        count, rgb = item
        return count * (.06 + QColor(*rgb).hsvSaturationF())**.65
    color = QColor(*max(swatches, key=score)[1])
    hue = color.hsvHueF()
    accent = QColor.fromHsvF(max(0, hue), min(.58, color.hsvSaturationF()), .94)
    return image, accent


class RoomView(QObject):
    changed = Signal()
    sourcesChanged = Signal()
    submitted = Signal()
    defaultsChanged = Signal()

    def __init__(self, dll, development=False):
        super().__init__()
        self.dll, self.development = dll, development
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.command = self.poll = self.enumeration = self.cover_job = None
        self.disc = self.analyzer = self.api = self.source = self.listener = None
        self.capture = self.publisher = self.audition = None
        self.retiring = []
        self.state, self.items, self.devices = {}, [], []
        self.view_data = {}
        self.song_info = {}
        self.control_error = ""
        self.user = self.stream = self.override = self.notice = ""
        self.cover_url = self.loaded_url = ""
        self.demo = False
        self.want = True
        self.generation = 0
        self.next_poll = self.retry = 0
        self.fallback = ""
        self.sync_policy = SyncPolicy(time.monotonic())
        self.pref_file = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Audimate" / "desktop.json"
        self.defaults = local_settings.load(self.pref_file)
        self.preview_delay = None
        self.receive, self.auto, self.interval = (self.defaults[k] for k in ("receive", "auto", "interval"))
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.tick)
        self.clock.start(75)
        self.refresh()

    @Property("QVariantMap", notify=changed)
    def view(self):
        return self.view_data

    @Property("QVariantList", notify=sourcesChanged)
    def sources(self):
        return self.items

    @Property(str, constant=True)
    def address(self):
        return default_server()

    @Property("QVariantMap", notify=defaultsChanged)
    def localDefaults(self):
        return self.defaults.copy()

    def playback_delay(self):
        return self.defaults["delay"] if self.preview_delay is None else self.preview_delay

    @Slot(int)
    def previewDelay(self, value):
        self.preview_delay = value if 0 <= value <= 200 else None

    def refresh(self):
        h = self.state.get("host") or {}
        info = h.get("recognition") or {}
        current = self.song_info
        track = h.get("track") or current
        candidate = info.get("candidate") or {}
        mine = self.state.get("hosting", False)
        pending = self.state.get("pending", False)
        playing = self.analyzer is not None and time.monotonic() - self.analyzer.last_input < 1.5
        host_style = h.get("visual_style", "pop")
        style = host_style if mine else self.override or host_style
        if style not in STYLES:
            style = "pop"
        if self.disc:
            self.disc.style = style
        lag = self.listener and self.listener.sync_sample
        lag_ms = lag[1] if lag and time.monotonic()-lag[0] < 5 else None
        receiving = "等待音源" if not h else "正在连接"
        if not self.want:
            receiving = "已停止收听"
        elif mine:
            receiving = "主持本机采集"
        elif playing and h:
            if isinstance(self.listener, RTCWorker):
                receiving = "Opus   48 kHz / 双声道"
            elif h.get("codec") == "opus":
                receiving = "Opus 解码缓存   48 kHz"
            else:
                receiving = quality(h)
        levels = self.capture.levels if self.capture else (0,0)
        data = dict(entered=bool(self.user), user=self.user, demo=self.demo, ready=bool(self.state),
                    busy=bool(self.command), host=h.get("name",""), mine=mine, pending=pending,
                    members=self.state.get("members",0), want=self.want, playing=playing,
                    title=track.get("title") or ("正在识别" if h and h.get("recognize") else "曲目信息待补充" if h else "等待主持上台"),
                    artist=track.get("artist",""), album="  /  ".join(str(x) for x in
                    (current.get("album"), current.get("released")) if x),
                    candidate=" — ".join(filter(None,(candidate.get("title"),candidate.get("artist")))),
                    recognitionEnabled=bool(h.get("recognize")),
                    notice=self.notice or self.control_error, recognitionNotice=info.get("notice") or "",
                    shared=quality(h) if h else "等待音源", received=receiving,
                    delay=round(lag_ms) if lag_ms is not None and lag_ms>1000 else 0,
                    requests=self.state.get("requests",[]), style=style, following=not self.override,
                    flac=self.state.get("flac",False), rtc=self.state.get("livekit",False),
                    recognize=self.state.get("recognition_available",False), receive=self.receive,
                    auto=self.auto, interval=self.interval, intervals=self.state.get("config",{}).get("intervals",[5, 10, 30]),
                    threshold=h.get("sync_request_ms"), canHost=sys.platform=="win32",
                    findingSources=self.enumeration is not None, process=self.source.name if mine and self.source else "",
                    left=round(levels[0],3), right=round(levels[1],3))
        if self.demo:
            data.update(title="潮汐之间", artist="Audimate Ensemble", album="海岸线习作",
                        host="", shared="本地试听", received="PCM   48 kHz / 16 bit" if playing else receiving,
                        ready=True, members=1)
        if data != self.view_data:
            self.view_data = data
            self.changed.emit()

    def submit(self, work, done):
        if self.command:
            return
        self.generation += 1
        self.notice = ""
        self.command = self.pool.submit(work)
        self.when_done = done
        self.refresh()

    @Slot(str, str, str, bool)
    def login(self, address, name, password, register):
        if self.user or self.command:
            return
        try:
            api = API(address)
        except ValueError as exc:
            self.fail(str(exc))
            return
        self.api = api
        def entered(result):
            self.user = result["name"]
            self.want = True
            self.next_poll = 0
        self.submit(lambda: api.request("register" if register else "login",
                                       dict(name=name.strip(), password=password)), entered)

    def fail(self, message):
        self.notice = message
        self.refresh()

    def bind_spectrum(self, rate, bits):
        self.clear_spectrum()
        self.analyzer = Spectrum(rate, bits)
        self.analyzer.start()
        self.disc.analyzer = self.analyzer
        return self.analyzer.feed

    def clear_spectrum(self):
        if self.analyzer:
            self.analyzer.close()
        self.analyzer = None
        if self.disc:
            self.disc.analyzer = None

    def stop_audio(self):
        self.retiring = [task for task in self.retiring if task.is_alive()]
        for task in (self.listener, self.capture, self.publisher, self.audition):
            if task:
                task.cancel.set()
                self.retiring.append(task)
        self.listener = self.capture = self.publisher = self.audition = None
        self.stream = ""
        self.clear_spectrum()

    @Slot()
    def auditionRoom(self):
        if self.command or self.user:
            return
        self.user, self.demo, self.want, self.notice = "本地试听", True, True, ""
        self.disc.artwork(demo_art(), QColor("#deb8df"))
        self.reconcile()
        self.refresh()

    def reconcile(self):
        if self.demo:
            if self.want and not self.audition:
                from .audition import Audition
                self.audition = Audition(self.bind_spectrum(48000,16))
                self.audition.playback_delay = self.playback_delay
                self.audition.start()
            elif not self.want and self.audition:
                self.stop_audio()
            return
        h = self.state.get("host")
        mine = self.state.get("hosting")
        if self.stream and (not h or h["stream"] != self.stream or
                            (not mine and (not self.want or self.capture))):
            self.stop_audio()
        if not h:
            return
        if mine and self.source and not self.capture:
            from .sharing import ProcessCapture, ShareSelection
            self.stop_audio()
            self.stream = h["stream"]
            self.publisher = (RTCWorker(self.api, h, publish=True) if h["codec"]=="opus"
                              else AudioUpload(self.api, self.stream, h))
            push = self.publisher.add
            feed = self.bind_spectrum(h["rate"], h["bits"])
            def block_received(block):
                push(block)
                feed(block.pcm)
            self.capture = ProcessCapture(self.dll, ShareSelection(self.source, self.format),
                                          block_received, timing=lambda: self.api.timing)
            self.publisher.start()
            self.capture.start()
        elif not mine and self.want and time.monotonic() >= self.retry:
            rtc = (self.state["livekit"] and (h["codec"]=="opus" or self.receive=="Opus")
                   and self.fallback != h["stream"])
            if self.listener and bool(rtc) == isinstance(self.listener, RTCWorker):
                return
            self.stop_audio()
            self.stream = h["stream"]
            self.listener = RTCWorker(self.api,h) if rtc else AudioListener(self.api,h)
            rate,bits = (48000,16) if rtc or h["codec"]=="opus" else (h["rate"],h["bits"])
            self.listener.observe = self.bind_spectrum(rate,bits)
            self.listener.playback_delay = self.playback_delay
            self.listener.start()

    def apply(self, state):
        old = self.state.get("host") or {}
        h = state.get("host") or {}
        if old.get("stream") != h.get("stream"):
            self.sync_policy = SyncPolicy(time.monotonic())
            self.fallback = ""
            self.retry = 0
        self.state = state
        if state.get("config"):
            config = state["config"]
            self.api.logging = config["logging"]
            self.api.timing = self.development and config.get("dev_timing") and self.api.logging!="off"
        info = h.get("recognition") or {}
        if (info.get("current") or h.get("track") or not info.get("candidate")
                or old.get("stream") != h.get("stream")):
            self.song_info = info.get("current") or {}
        self.cover_url = self.song_info.get("cover") or ""
        self.reconcile()
        self.refresh()

    def start_poll(self):
        api = self.api
        h = self.state.get("host") or {}
        q = {"want":int(self.want)}
        if self.state.get("cursor"):
            q["since"] = self.state["cursor"]
        if self.listener and getattr(self.listener,"position",0):
            q.update(stream=self.stream, position=self.listener.position,
                     transport="rtc" if isinstance(self.listener,RTCWorker) else "pcm")
        source = self.source if self.capture and self.capture.ready.is_set() else None
        def read():
            if source:
                from .processes import current_track
                track = current_track(source)
                if track != h.get("track"):
                    try:
                        return api.request("stage/track",dict(stream=h["stream"],track=track))
                    except RoomError as exc:
                        if exc.status not in (403,409):
                            raise
            return api.request("room?" + urlencode(q))
        self.poll_generation = self.generation
        self.poll = self.pool.submit(read)
        self.next_poll = time.monotonic()+.8

    def tick(self):
        if self.command and self.command.done():
            job,self.command = self.command,None
            try:
                self.when_done(job.result())
            except Exception as exc:
                if isinstance(exc,RoomError) and exc.status==401 and self.user:
                    self.reset()
                self.fail(str(exc))
            self.next_poll = 0
        if self.poll and self.poll.done():
            job,self.poll = self.poll,None
            if self.poll_generation == self.generation:
                try:
                    result = job.result()
                    self.control_error = ""
                    if not result.get("unchanged"):
                        self.apply(result)
                except Exception as exc:
                    if isinstance(exc,RoomError) and exc.status==401:
                        self.reset()
                    self.control_error = str(exc)
        if self.enumeration and self.enumeration.done():
            job,self.enumeration = self.enumeration,None
            try:
                self.devices = job.result()
                self.items = [dict(key=f"{s.pid}/{s.started}", name=s.name, pid=s.pid, title=s.title)
                              for s in self.devices]
                self.sourcesChanged.emit()
            except Exception as exc:
                self.fail(str(exc))
        if self.cover_job and self.cover_job.done():
            job,self.cover_job = self.cover_job,None
            if self.requested_url == self.cover_url and not self.demo:
                try:
                    img,color = job.result()
                except Exception:
                    img,color = QImage(),ICE
                self.disc.artwork(img,color)
        if not self.demo and self.loaded_url != self.cover_url and (not self.cover_url or not self.cover_job):
            self.loaded_url = self.cover_url
            if self.cover_url:
                self.requested_url = self.cover_url
                self.cover_job = self.pool.submit(load_art,self.cover_url)
            else:
                self.disc.artwork(QImage(),ICE)
        if not self.user:
            self.refresh()
            return
        if self.audition and self.audition.error:
            self.notice = "请检查播放设备后重新收听。" + self.audition.error
            self.want = False
            self.stop_audio()
        if not self.demo:
            self.check_audio()
            if not self.poll and not self.command and time.monotonic() >= self.next_poll:
                self.start_poll()
        self.refresh()

    def check_audio(self):
        if self.command:
            return
        if self.capture and (self.capture.error or self.publisher.error):
            message = self.capture.error or self.publisher.error
            self.leaveStage(message)
        elif self.listener and self.listener.error:
            self.notice = self.listener.error
            if isinstance(self.listener, RTCWorker):
                self.fallback = self.stream
            self.api.report(self.stream,"error","rtc" if isinstance(self.listener,RTCWorker) else "pcm",{})
            self.stop_audio()
            self.retry = time.monotonic()+1
        elif self.listener and self.listener.sync_sample:
            stamp,lag = self.listener.sync_sample
            threshold = self.state.get("sync_policy",{}).get("effective_ms",1200)
            if self.sync_policy.observe(stamp,lag if self.auto else None,threshold,int(self.interval)):
                self.sync_audio("auto")
        elif not self.listener and not self.capture and self.state and not self.state.get("hosting"):
            self.reconcile()
        for worker in (self.publisher,self.listener):
            if worker and worker.notice:
                self.notice,worker.notice = worker.notice,""

    @Slot()
    def listen(self):
        if self.state.get("hosting"):
            return
        self.want = not self.want
        self.notice = self.fallback = ""
        self.retry = 0
        self.reconcile()
        self.refresh()

    @Slot()
    def sync(self):
        self.sync_audio("manual")

    def sync_audio(self, reason):
        if self.listener:
            self.api.report(self.stream,"sync","rtc" if isinstance(self.listener,RTCWorker) else "pcm",
                            {"detail":{"phase":reason}} if self.api.timing else {})
            if reason=="manual":
                self.sync_policy = SyncPolicy(time.monotonic())
            self.stop_audio()
            self.fallback = ""
            self.retry = 0
            self.reconcile()

    @Slot("QVariantMap", result=str)
    def saveDefaults(self, values):
        try:
            data = local_settings.validate(values)
            local_settings.save(self.pref_file, data)
        except (OSError, ValueError) as exc:
            return str(exc)
        self.defaults = data
        self.receive,self.auto,self.interval = (data[k] for k in ("receive", "auto", "interval"))
        self.defaultsChanged.emit()
        self.reconcile()
        self.refresh()
        return ""

    @Slot(str)
    def preset(self, style):
        if style not in (*STYLES, ""):
            return
        if self.state.get("hosting"):
            if style:
                self.action("style",dict(visual_style=style,stream=self.stream))
        else:
            self.override = style
            self.refresh()

    @Slot()
    def listSources(self):
        if self.enumeration or sys.platform!="win32":
            return
        from .processes import list_sources
        self.enumeration = self.pool.submit(list_sources)
        self.refresh()

    @Slot(str,int,int,str,bool,str,str)
    def requestStage(self,key,rate,bits,codec,recognize,threshold,style):
        if self.command or self.demo or not self.state or self.state["hosting"] or self.state["pending"]:
            return
        from host.capture.record import AudioFormat
        src = next((s for s in self.devices if f"{s.pid}/{s.started}"==key),None)
        if not src:
            self.fail("请重新选择音源")
            return
        try:
            self.format = AudioFormat(rate,bits)
        except ValueError:
            self.fail("请在本次参数中选择有效位深，并输入 8000 至 192000 Hz 之间的整百采样率。")
            return
        try:
            value = threshold_ms(threshold,self.state["sync_policy"]["max_ms"])
        except ValueError as exc:
            self.fail(f"请调整本次参数中的同步阈值。{exc}")
            return
        self.source = src
        payload = dict(source=src.name,rate=rate,bits=bits,codec="opus" if codec.startswith("Opus") else "pcm",
                       compression="flac" if codec=="FLAC" else "none",recognize=recognize,
                       sync_request_ms=value,visual_style=style)
        if payload["codec"]=="opus":
            payload["bitrate"] = int(codec.split()[1])*1000
        def requested(state):
            self.apply(state)
            self.submitted.emit()
        self.action("request",payload,requested)

    def action(self, name, data, done=None):
        api = self.api
        self.submit(lambda: api.request("stage/"+name,data), done or self.apply)

    @Slot(str)
    def leaveStage(self,message=""):
        h = self.state.get("host") or {}
        def left(state):
            self.source = None
            self.apply(state)
            self.notice = message
        self.action("leave",dict(stream=h.get("stream","")),left)

    @Slot(str,str)
    def decide(self,action,key):
        self.action(action,dict(stream=self.stream,id=key))

    @Slot(str)
    def threshold(self,text):
        try:
            value = threshold_ms(text,self.state["sync_policy"]["max_ms"])
        except ValueError as exc:
            self.fail(str(exc))
            return
        self.action("sync",dict(stream=self.stream,sync_request_ms=value))

    @Slot()
    def exitRoom(self):
        if self.demo:
            self.reset()
        elif self.api and not self.command:
            api = self.api
            self.submit(lambda: api.request("logout",{}),lambda _:self.reset())

    def reset(self):
        self.stop_audio()
        self.generation += 1
        self.user = self.override = self.notice = self.fallback = ""
        self.cover_url = self.loaded_url = ""
        self.song_info = {}
        self.control_error = ""
        self.source = self.api = None
        self.state = {}
        self.demo = False
        self.disc.artwork(QImage(),ICE)
        self.refresh()

    def close(self):
        self.clock.stop()
        self.stop_audio()
        api = self.api
        if api and api.cookie:
            self.pool.submit(api.request,"logout",{})
        self.pool.shutdown(wait=False)
        # PortAudio terminates at Python exit. Close every device on its owner
        # thread before allowing the application to leave its event loop.
        for task in self.retiring:
            task.join()
