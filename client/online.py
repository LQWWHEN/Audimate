"""Tk room panel; all HTTP and audio IO run outside the Tk event loop."""
from concurrent.futures import ThreadPoolExecutor
import time
import json
import os
from pathlib import Path
from .rtc import RTCWorker
import tkinter as tk
from tkinter import ttk

from .network import API, default_server, AudioListener, AudioUpload, RoomError
from .sharing import LocalCapture, ShareSelection


class OnlineRoom:
    def __init__(self, client):
        self.client = client
        self.window = w = tk.Toplevel(client.root)
        w.title("Audimate · 在线音乐房间")
        w.geometry("575x550")
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
        self.want_listen = False
        self.fallback_stream = ""
        self.last_sync = self.retry_at = 0
        self.codec = tk.StringVar(w, "Opus 128 kbps")
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
        ttk.Label(self.frame, text="连接音乐房间", style="Heading.TLabel").grid(row=0, columnspan=2, sticky="w", pady=12)
        for row, (label, var) in enumerate((("服务器", self.addr), ("用户名", self.user), ("密码", self.password)), 1):
            ttk.Label(self.frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10))
            ttk.Entry(self.frame, textvariable=var, show="*" if row == 3 else "").grid(row=row, column=1, sticky="ew", pady=6)
        buttons = ttk.Frame(self.frame)
        buttons.grid(row=4, columnspan=2, sticky="e", pady=10)
        for key, label in (("register", "注册并进入"), ("login", "登录")):
            ttk.Button(buttons, text=label, command=lambda k=key: self.auth(k)).pack(side="left", padx=4)
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
        self.listen = ttk.Button(buttons, text="收听", command=self.toggle_listen)
        self.listen.pack(side="left")
        self.stage = ttk.Button(buttons, text="主持", command=self.toggle_stage)
        self.stage.pack(side="left", padx=8)
        self.requests = ttk.Frame(self.frame)
        self.requests.grid(row=3, columnspan=2, sticky="ew")
        prefs = ttk.LabelFrame(self.frame, text="传输与同步", padding=8)
        prefs.grid(row=4, columnspan=2, sticky="ew", pady=8)
        for row, (title, variable, values) in enumerate((("主持上传",self.codec,("Opus 64 kbps","Opus 128 kbps","Opus 256 kbps","无损 PCM")),
                                                       ("优先接收",self.receive,("Opus","无损 PCM")),
                                                       ("校准间隔（秒）",self.interval,("5",)))):
            ttk.Label(prefs,text=title).grid(row=row,column=0,sticky="w",padx=5)
            select = ttk.Combobox(prefs,textvariable=variable,values=values,state="readonly",width=12)
            select.grid(row=row,column=1,sticky="w",pady=2)
            select.bind("<<ComboboxSelected>>", lambda e: self.save_preferences())
            if variable is self.codec:
                self.codec_select = select
            if variable is self.interval:
                self.intervals = select
        ttk.Checkbutton(prefs,text="自动同步",variable=self.auto,command=self.save_preferences).grid(row=2,column=2,padx=8)
        ttk.Button(buttons,text="手动同步",command=self.manual_sync).pack(side="left")
        self.notice(5)
        self.status.set("请选择主窗口的进程与音质，再点击此处主持。")
        self.next_poll = 0

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
        self.want_listen = False
        self.name = self.stream = ""
        self.selection = self.state = None
        self.pending = False
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

    def manual_sync(self):
        if not self.listener:
            return
        if self.listener.lag is not None and self.listener.lag < 850 and not self.fallback_stream:
            self.status.set("已接近实时位置")
            return
        self.api.report(self.listener.host["stream"], "sync", "rtc" if isinstance(self.listener,RTCWorker) else "pcm", {})
        self.stop_listen()
        self.fallback_stream = ""
        self.last_sync = time.monotonic()
        self.render(self.state)

    def toggle_listen(self):
        self.want_listen = not self.want_listen
        self.fallback_stream = ""
        self.render(self.state)

    def toggle_stage(self):
        if self.polling:
            self.after_poll = self.toggle_stage
            return
        if self.future or not self.state:
            return
        if self.state["pending"] or self.state["hosting"]:
            self.release()
            return
        src = self.client.selected()
        if not src:
            self.status.set("请在主窗口选中共享进程，再申请主持。")
            return
        try:
            fmt = self.client.selected_format()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.selection = ShareSelection(src, fmt)
        payload = dict(source=src.name, rate=fmt.sample_rate, bits=fmt.bits_per_sample,
                       codec="opus" if self.state["livekit"] and self.codec.get().startswith("Opus") else "pcm")
        if payload["codec"]=="opus":
            payload["bitrate"] = int(self.codec.get().split()[1])*1000
        self.submit(lambda: self.api.request("stage/request", payload), self.render)

    def release(self):
        stream = (self.state["host"] or {}).get("stream", "")
        self.selection = None
        self.stop_stream()
        self.submit(lambda: self.api.request("stage/leave", {"stream": stream}), self.render)

    def decide(self, action, applicant):
        if self.polling:
            self.after_poll = lambda: self.decide(action, applicant)
            return
        data = {"id": applicant, "stream": self.stream}
        self.submit(lambda: self.api.request("stage/" + action, data), self.render)

    def render(self, state):
        self.state = state
        host = state["host"]
        settings = state["config"]
        self.api.logging = settings["logging"]
        intervals = list(map(str,settings["intervals"]))
        if self.interval.get() not in intervals:
            if self.want_listen:
                intervals.append(self.interval.get())
            else:
                self.interval.set("5")
        self.intervals.configure(values=intervals)
        if not state["livekit"]:
            self.codec.set("无损 PCM")
        self.codec_select.configure(state="readonly" if state["livekit"] and not state["hosting"] and not state["pending"] else "disabled")
        rtc = bool(host and state["livekit"] and (host["codec"]=="opus" or self.receive.get()=="Opus")
                   and self.fallback_stream != host["stream"])
        if self.listener and (not self.want_listen or not host or self.listener.host["stream"] != host["stream"]
                              or state["hosting"] or rtc != isinstance(self.listener, RTCWorker)):
            self.stop_listen()
        if host and self.want_listen and not state["hosting"] and not self.listener and time.monotonic() >= self.retry_at:
            self.listener = RTCWorker(self.api, host) if rtc else AudioListener(self.api, host)
            self.listener.start()
            self.status.set("收听 LiveKit · Opus" if rtc else "收听 Opus 解码缓存" if host["codec"]=="opus" else "收听无损 PCM")
        if state["hosting"] and self.selection and self.stream != host["stream"]:
            self.stop_stream()
            self.stream = host["stream"]
            self.upload = RTCWorker(self.api, host, publish=True) if host["codec"] == "opus" else AudioUpload(self.api, self.stream)
            self.capture = LocalCapture(self.client.dll, self.selection, self.upload.add)
            self.upload.start()
            self.capture.start()
            self.status.set("正在共享 " + self.selection.source.name)
        elif not state["hosting"] and self.stream:
            self.stop_stream()
            self.status.set("本次主持已结束")
        if self.pending and not state["pending"] and not state["hosting"]:
            self.selection = None
            self.status.set("主持申请已结束")
        self.pending = state["pending"]
        label = f"主持 {host['name']} · {host['source']} · {host['rate']/1000:g} kHz / {host['bits']}-bit" if host else "等待主持上台"
        if host:
            label += f" · Opus {host['bitrate']//1000} kbps" if host["codec"]=="opus" else " · 无损 PCM"
        self.info.configure(text=f"{self.name}  ·  {state['members']} 人在线\n{label}")
        self.listen.configure(text="停止收听" if self.want_listen else "收听", state="disabled" if state["hosting"] and not self.want_listen else "normal")
        self.stage.configure(text="下台" if state["hosting"] else "取消申请" if self.pending else "申请主持" if host else "开始主持")
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
            self.next_poll = time.monotonic() + 1
            if self.after_poll:
                action, self.after_poll = self.after_poll, None
                if self.name:
                    action()
        if self.name and not self.future:
            if self.capture and (self.capture.error or self.upload.error):
                self.status.set(self.capture.error or self.upload.error)
                self.release()
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
            elif self.listener and self.listener.lag is not None and self.listener.lag > 1000:
                lag = self.listener.lag
                self.status.set(f"接收延迟偏高（约 {lag:.0f} ms），可手动同步")
                if lag >= self.state["config"]["history_s"]*1000 or (self.auto.get() and time.monotonic()-self.last_sync >= int(self.interval.get())):
                    self.manual_sync()
            if not self.future and time.monotonic() >= self.next_poll:
                if self.state and self.state["hosting"] and not self.selection:
                    self.release()
                else:
                    self.polling = True
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
        self.client.online = None
