"""Tk room panel; all HTTP and audio IO run outside the Tk event loop."""
from concurrent.futures import ThreadPoolExecutor
import time
import json
import math
import os
from pathlib import Path
from .rtc import RTCWorker
import tkinter as tk
from tkinter import ttk

from .network import API, default_server, AudioListener, AudioUpload, RoomError
from .host_setup import HostSetup, threshold_ms
from .processes import current_track
from .sharing import ProcessCapture, ShareSelection
from .sync import SyncPolicy


class OnlineRoom:
    def __init__(self, window, dll, *, development=False):
        self.development = development
        self.dll = dll
        self.window = w = window
        self.picker = None
        w.title("Audimate · 音乐房间")
        w.geometry("670x560")
        w.minsize(600, 510)
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = self.api = self.state = self.selection = None
        self.capture = self.upload = self.listener = None
        self.stream = ""
        self.pending = False
        self.polling = False
        self.after_poll = None
        self.next_poll = 0
        self.name = ""
        self.frame = ttk.Frame(w, padding=20)
        self.frame.pack(fill="both", expand=True)
        self.frame.columnconfigure(1, weight=1)
        self.status = tk.StringVar(w)
        self.addr = tk.StringVar(w, default_server())
        self.user = tk.StringVar(w)
        self.password = tk.StringVar(w)
        self.preferences = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Audimate" / "listening.json"
        try:
            saved = json.loads(self.preferences.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}
        self.sync_stream = None
        self.want_listen = False
        self.fallback_stream = ""
        self.last_sync = self.retry_at = 0
        self.receive = tk.StringVar(w, saved.get("receive", "Opus"))
        self.auto = tk.BooleanVar(w, bool(saved.get("auto", True)))
        self.interval = tk.StringVar(w, str(saved.get("interval", 5)))
        self.auth_screen()
        self.tick_id = w.after(80, self.tick)

    def clear(self):
        for item in self.frame.winfo_children():
            item.destroy()

    def auth_screen(self):
        self.clear()
        ttk.Label(self.frame, text="登录音乐房间", style="Heading.TLabel").grid(row=0, columnspan=2, sticky="w", pady=12)
        for row, (label, var) in enumerate((("服务器", self.addr), ("用户名", self.user), ("密码", self.password)), 1):
            ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10))
            entry = ttk.Entry(self.frame, textvariable=var, show="*" if row == 3 else "")
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            entry.bind("<Return>", lambda _: self.auth("login"))
            if var is self.user:
                entry.focus_set()
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=4, columnspan=2, sticky="e", pady=10)
        ttk.Button(buttons, text="注册并进入", command=lambda: self.auth("register")).pack(side="left", padx=6)
        ttk.Button(buttons, text="登录", style="Primary.TButton", command=lambda: self.auth("login")).pack(side="left")
        self.notice(5)

    def notice(self, row):
        ttk.Label(self.frame, textvariable=self.status, wraplength=455).grid(row=row, columnspan=2, sticky="w", pady=12)

    def submit(self, call, done):
        if self.future is None:
            self.future = self.pool.submit(call)
            self.done = done

    def auth(self, method):
        if self.future:
            return
        try:
            self.api = API(self.addr.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        data = dict(name=self.user.get().strip(), password=self.password.get())
        self.status.set("正在连接…")
        self.submit(lambda: self.api.request(method, data), self.enter)

    def enter(self, data):
        self.name = data["name"]
        self.password.set("")
        self.clear()
        ttk.Label(self.frame, text="在线音乐房间", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(self.frame, text="退出登录", command=self.logout).grid(row=0, column=1, sticky="e")
        self.info = ttk.Label(self.frame, wraplength=450)
        self.info.grid(row=1, columnspan=2, sticky="w", pady=14)
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=2, columnspan=2, sticky="w")
        self.listen = ttk.Button(buttons, text="收听", command=self.toggle_listen, state="disabled", style="Primary.TButton")
        self.listen.pack(side="left")
        self.stage = ttk.Button(buttons, text="开始主持", command=self.toggle_stage, state="disabled")
        self.stage.pack(side="left", padx=8)
        self.requests = ttk.Frame(self.frame)
        self.requests.grid(row=3, columnspan=2, sticky="ew")
        self.levels = ttk.Label(self.frame, style="Muted.TLabel")
        self.levels.grid(row=4, columnspan=2, sticky="w")
        self.levels.grid_remove()
        self.prefs = prefs = ttk.LabelFrame(self.frame, text="收听设置", padding=8)
        prefs.grid(row=5, columnspan=2, sticky="ew", pady=8)
        for row, (title, variable, values) in enumerate((("优先接收",self.receive,("Opus","无损 PCM")),
                                                       ("校准间隔（秒）",self.interval,("5",)))):
            ttk.Label(prefs,text=title).grid(row=row,column=0,sticky="w",padx=5)
            select = ttk.Combobox(prefs,textvariable=variable,values=values,state="readonly",width=12)
            select.grid(row=row,column=1,sticky="w",pady=2)
            select.bind("<<ComboboxSelected>>", lambda e: self.save_preferences())
            if variable is self.interval:
                self.intervals = select
        ttk.Checkbutton(prefs,text="自动同步",variable=self.auto,command=self.save_preferences).grid(row=1,column=2,padx=8)
        self.sync = ttk.Button(prefs,text="立即同步",command=self.manual_sync, state="disabled")
        self.sync.grid(row=0, column=2, padx=8)
        self.settings_button = ttk.Button(buttons, text="收听设置", command=self.toggle_preferences)
        self.settings_button.pack(side="left")
        prefs.grid_remove()
        self.threshold_bar = bar = ttk.Frame(self.frame)
        bar.grid(row=7, columnspan=2, sticky="w")
        ttk.Label(bar, text="同步阈值（秒）").pack(side="left")
        self.host_threshold = tk.StringVar(self.window, "跟随房间")
        ttk.Entry(bar, width=10, textvariable=self.host_threshold).pack(side="left", padx=6)
        ttk.Button(bar, text="应用", command=self.apply_threshold).pack(side="left")
        bar.grid_remove()
        self.notice(6)
        self.status.set("正在读取房间…")
        self.next_poll = 0

    def toggle_preferences(self):
        if self.prefs.winfo_manager():
            self.prefs.grid_remove()
            self.settings_button.configure(text="收听设置")
        else:
            self.prefs.grid()
            self.settings_button.configure(text="收起设置")

    def stop_stream(self):
        for task in (self.capture, self.upload):
            if task:
                task.cancel.set()
        self.capture = self.upload = None
        self.stream = ""

    def stop_listen(self):
        if self.listener:
            self.listener.cancel.set()
            self.listener = None

    def logout(self):
        if self.polling:
            self.after_poll = self.logout
            return
        if self.future:
            return
        self.submit(lambda: self.api.request("logout", {}), self.logged_out)

    def logged_out(self, _):
        self.stop_stream()
        self.stop_listen()
        self.sync_stream = None
        self.want_listen = False
        self.name = self.stream = ""
        self.selection = self.state = None
        self.pending = False
        self.after_poll = None
        self.fallback_stream = ""
        self.last_sync = self.retry_at = 0
        if self.picker:
            self.show_room()
            self.picker.destroy()
            self.picker = None
        self.auth_screen()
        self.status.set("已退出")

    def save_preferences(self):
        data = dict(receive=self.receive.get(), auto=self.auto.get(), interval=int(self.interval.get()))
        try:
            self.preferences.parent.mkdir(exist_ok=True, parents=True)
            self.preferences.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            self.status.set(str(exc))
        if self.state:
            self.render(self.state)

    def manual_sync(self, reason="manual"):
        if not self.listener:
            return
        if reason == "manual" and self.listener.lag is not None and self.listener.lag < 850 and not self.fallback_stream:
            self.status.set("已接近实时位置")
            return
        if reason == "manual":
            self.sync_policy = SyncPolicy(time.monotonic())
        metrics = {"detail":{"phase":reason}} if self.api.timing else {}
        self.api.report(self.listener.host["stream"], "sync", "rtc" if isinstance(self.listener,RTCWorker) else "pcm", metrics)
        self.stop_listen()
        self.fallback_stream = ""
        self.last_sync = time.monotonic()
        self.render(self.state)

    def check_listening_delay(self, lag):
        if lag > 1000:
            self.status.set(f"接收延迟偏高（约 {lag:.0f} ms），可手动同步")
        sample = self.listener.sync_sample
        if sample:
            stamp, estimate = sample
            threshold = self.state.get("sync_policy", {}).get("effective_ms", 1200)
            if self.sync_policy.observe(stamp, estimate if self.auto.get() else None,
                                        threshold, int(self.interval.get())):
                self.manual_sync("auto")

    def apply_threshold(self):
        if not self.state or not self.state["hosting"]:
            return
        stream = self.state["host"]["stream"]
        try:
            value = threshold_ms(self.host_threshold.get(), self.state["sync_policy"]["max_ms"])
        except ValueError as exc:
            self.status.set(str(exc))
            return
        def send():
            if self.state and self.state["hosting"] and self.state["host"]["stream"] == stream:
                self.submit(lambda: self.api.request("stage/sync", {"stream": stream, "sync_request_ms": value}), self.render)
        if self.polling:
            self.after_poll = send
        else:
            send()

    def toggle_listen(self):
        self.sync_stream = None
        self.want_listen = not self.want_listen
        self.fallback_stream = ""
        if not self.want_listen:
            self.status.set("已停止收听")
        self.render(self.state)

    def toggle_stage(self):
        if not self.state or (self.future and not self.polling):
            return
        if self.state["pending"] or self.state["hosting"]:
            if self.polling:
                self.after_poll = self.release
            else:
                self.release()
            return
        if not self.picker:
            self.picker = HostSetup(self.window, self.request_stage, self.show_room)
        self.picker.threshold.set("跟随房间")
        self.picker.room_state(self.state)
        self.picker.refresh()
        self.frame.pack_forget()
        self.picker.pack(fill="both", expand=True)

    def show_room(self):
        if self.after_poll == self.request_stage:
            self.after_poll = None
        self.picker.pack_forget()
        self.frame.pack(fill="both", expand=True)

    def request_stage(self):
        if not self.picker or not self.picker.winfo_manager():
            return
        if self.polling:
            self.after_poll = self.request_stage
            return
        if self.future or not self.state or self.pending or self.state["hosting"]:
            return
        src = self.picker.selected()
        if not src:
            return
        try:
            fmt = self.picker.selected_format()
        except ValueError:
            self.picker.validate()
            return
        try:
            sync_request = threshold_ms(self.picker.threshold.get(), self.state.get("sync_policy", {}).get("max_ms", 1800))
        except ValueError as exc:
            self.picker.message.set(str(exc))
            return
        self.selection = ShareSelection(src, fmt)
        codec = self.picker.codec.get()
        payload = dict(sync_request_ms=sync_request, source=src.name, rate=fmt.sample_rate, bits=fmt.bits_per_sample,
                       codec="opus" if self.state["livekit"] and codec.startswith("Opus") else "pcm")
        if payload["codec"]=="opus":
            payload["bitrate"] = int(codec.split()[1])*1000
        elif "FLAC" in codec:
            payload["compression"] = "flac"
        self.show_room()
        self.stage.state(["disabled"])
        self.status.set("正在提交主持请求…")
        self.submit(lambda: self.api.request("stage/request", payload), self.render)

    def release(self, message=None, reason=None):
        if not self.state["hosting"] and not self.state["pending"]:
            return
        stream = (self.state["host"] or {}).get("stream", "")
        if not message:
            message = "已下台" if self.state["hosting"] else "已取消申请"
        self.selection = None
        self.stop_stream()
        self.stage.state(["disabled"])

        def released(state):
            self.render(state)
            self.status.set(message)

        data = {"stream": stream}
        if reason and self.api.timing:
            data["reason"] = reason
        self.submit(lambda: self.api.request("stage/leave", data), released)

    def decide(self, action, applicant):
        if self.polling:
            self.after_poll = lambda: self.decide(action, applicant)
            return
        data = {"id": applicant, "stream": self.stream}
        self.submit(lambda: self.api.request("stage/" + action, data), self.render)

    def poll_host(self, host, source):
        track = current_track(source)
        if track != host["track"]:
            try:
                return self.api.request("stage/track", {"stream": host["stream"], "track": track})
            except RoomError as exc:
                if exc.status not in (403, 409):
                    raise
                # A handoff took place since the last poll.
        return self.api.request("room")

    def render(self, state):
        previous = self.state
        if self.state is None:
            self.status.set("")
        self.state = state
        host = state["host"]
        settings = state["config"]
        self.api.logging = settings["logging"]
        self.api.timing = self.development and settings.get("dev_timing") is True and self.api.logging != "off"
        intervals = list(map(str,settings["intervals"]))
        if self.interval.get() not in intervals:
            if self.want_listen:
                intervals.append(self.interval.get())
            else:
                self.interval.set("5")
        self.intervals.configure(values=intervals)
        if self.picker and self.picker.winfo_manager():
            self.picker.room_state(state)
        rtc = bool(host and state["livekit"] and (host["codec"]=="opus" or self.receive.get()=="Opus")
                   and self.fallback_stream != host["stream"])
        if self.listener and (not self.want_listen or not host or self.listener.host["stream"] != host["stream"]
                              or state["hosting"] or rtc != isinstance(self.listener, RTCWorker)):
            self.stop_listen()
        if host and self.want_listen and not state["hosting"] and not self.listener and time.monotonic() >= self.retry_at:
            if self.sync_stream != host["stream"]:
                self.sync_stream = host["stream"]
                self.sync_policy = SyncPolicy(time.monotonic())
            self.listener = RTCWorker(self.api, host) if rtc else AudioListener(self.api, host)
            self.last_sync = time.monotonic()
            self.listener.start()
            self.status.set("收听 LiveKit · Opus" if rtc else "收听 Opus 解码缓存" if host["codec"]=="opus"
                            else "收听无损 PCM · FLAC 解压" if host.get("compression")=="flac" else "收听无损 PCM")
        if state["hosting"] and self.selection and self.stream != host["stream"]:
            self.stop_stream()
            self.stream = host["stream"]
            self.upload = RTCWorker(self.api, host, publish=True) if host["codec"] == "opus" else AudioUpload(self.api, self.stream, host)
            self.capture = ProcessCapture(self.dll, self.selection, self.upload.add, timing=lambda: self.api.timing)
            self.upload.start()
            self.capture.start()
            self.status.set("正在共享音频")
        elif not state["hosting"] and self.stream:
            self.stop_stream()
            self.status.set("本次主持已结束")
        if state["pending"] and not self.pending:
            self.status.set("已申请主持，等待现任主持接受。")
        elif self.pending and not state["pending"] and not state["hosting"]:
            self.selection = None
            self.status.set("主持申请已结束")
        self.pending = state["pending"]
        label = (f"主持 {host['name']} · {host['source']} · {host['rate']/1000:g} kHz / {host['bits']}-bit" if host
                 else "正在等候下一位主持，开播后将自动收听" if self.want_listen else "等待主持上台")
        if host:
            label += f" · Opus {host['bitrate']//1000} kbps" if host["codec"]=="opus" else " · 无损 PCM"
            if host.get("compression") == "flac":
                label += " · FLAC 压缩"
        self.info.configure(text=f"{self.name}  ·  {state['members']} 人在线\n{label}")
        listen_label = "收听" if not self.want_listen else "停止收听" if host else "取消等候"
        self.listen.configure(text=listen_label, state="disabled" if state["hosting"] and not self.want_listen else "normal")
        self.sync.configure(state="normal" if self.listener else "disabled")
        self.stage.configure(text="下台" if state["hosting"] else "取消申请" if self.pending else "申请主持" if host else "开始主持",
                             state="normal")
        if state["hosting"] and state.get("sync_policy"):
            self.threshold_bar.grid()
            old_host = (previous or {}).get("host") or {}
            if (old_host.get("stream") != host["stream"] or old_host.get("sync_request_ms") != host.get("sync_request_ms")
                    or previous.get("sync_policy") != state["sync_policy"]):
                value = state["sync_policy"]["effective_ms"] / 1000
                self.host_threshold.set("跟随房间" if host.get("sync_request_ms") is None else f"{value:g}")
        else:
            self.threshold_bar.grid_remove()
        if self.capture:
            self.levels.grid()
        else:
            self.levels.grid_remove()
        if previous and previous["requests"] == state["requests"]:
            return
        for child in self.requests.winfo_children():
            child.destroy()
        for req in state["requests"]:
            row = ttk.Frame(self.requests)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=req["name"] + " 申请主持").pack(side="left")
            for action, title in (("accept", "接受"), ("reject", "拒绝")):
                ttk.Button(row, text=title, command=lambda a=action, i=req["id"]: self.decide(a, i)).pack(side="right")

    def tick(self):
        if self.future and self.future.done():
            result, self.future = self.future, None
            self.polling = False
            try:
                self.done(result.result())
            except Exception as exc:
                if isinstance(exc, RoomError) and exc.status == 401 and self.name:
                    self.logged_out(None)
                self.status.set(str(exc))
            self.next_poll = time.monotonic() + 1 if self.state else 0
            if self.after_poll:
                action, self.after_poll = self.after_poll, None
                if self.name:
                    action()
        if self.capture:
            levels = [f"{20 * math.log10(value):.1f}" if value > 0 else "−∞" for value in self.capture.levels]
            self.levels.configure(text=f"输入电平　左 {levels[0]} dBFS　右 {levels[1]} dBFS" if self.capture.ready.is_set() else "正在打开音源…")
        if self.name and not self.future:
            if self.capture and (self.capture.error or self.upload.error):
                if self.api.timing and self.capture.failure_timing:
                    self.api.report(self.stream, "error", "rtc" if isinstance(self.upload,RTCWorker) else "pcm",
                                    {"detail":self.capture.failure_timing})
                self.release(self.capture.error or self.upload.error, "capture" if self.capture.error else "upload")
            elif self.listener and self.listener.error:
                self.status.set(self.listener.error)
                if isinstance(self.listener, RTCWorker):
                    self.fallback_stream = self.listener.host["stream"]
                self.retry_at = time.monotonic()+1
                self.api.report(self.listener.host["stream"], "error", "rtc" if isinstance(self.listener,RTCWorker) else "pcm", {})
                self.stop_listen()
            elif self.upload and self.upload.notice:
                self.status.set(self.upload.notice)
                self.upload.notice = ""
            elif self.listener and self.listener.lag is not None:
                self.check_listening_delay(self.listener.lag)
            if not self.future and time.monotonic() >= self.next_poll:
                if self.state and self.state["hosting"] and not self.selection:
                    self.release()
                else:
                    self.polling = True
                    host = self.state and self.state["host"]
                    if self.capture and self.capture.ready.is_set() and host and "track" in host:
                        source = self.selection.source
                        self.submit(lambda: self.poll_host(host, source), self.render)
                    else:
                        route = "room?listen="+self.listener.host["stream"] if self.listener and getattr(self.listener,"subscribed",False) else "room"
                        self.submit(lambda: self.api.request(route), self.render)
        self.tick_id = self.window.after(80, self.tick)

    def close(self):
        self.window.after_cancel(self.tick_id)
        self.stop_stream()
        self.stop_listen()
        if self.api:
            api = self.api
            self.pool.submit(lambda: api.request("logout", {}) if api.cookie else None)
        self.pool.shutdown(wait=False)
        self.window.destroy()
