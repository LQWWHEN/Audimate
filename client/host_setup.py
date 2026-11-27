"""Process and upload settings for an online hosting request."""
import math
import tkinter as tk
from tkinter import ttk

from host.capture.record import AudioFormat, DEFAULT_FORMAT, QUALITY_PRESETS
from .processes import list_sources


def format_label(fmt):
    return f"{fmt.sample_rate / 1000:g} kHz · {fmt.bits_per_sample}-bit"


def threshold_ms(text, cap):
    text = text.strip()
    if text in ("", "跟随房间"):
        return None
    try:
        seconds = float(text)
        if math.isfinite(seconds) and 0 <= seconds <= cap / 1000:
            return round(seconds * 1000)
    except ValueError:
        pass
    raise ValueError(f"阈值范围 0–{cap / 1000:g} 秒")


class HostSetup(ttk.Frame):
    def __init__(self, parent, submit, back):
        super().__init__(parent, padding=20)
        self.error_text = ""
        self.sources = []
        self.rows = {}
        self.quality_keys = list(QUALITY_PRESETS)
        self.search = tk.StringVar(self)
        self.rate = tk.StringVar(self, str(DEFAULT_FORMAT.sample_rate))
        self.depth = tk.StringVar(self, str(DEFAULT_FORMAT.bits_per_sample))
        self.message = tk.StringVar(self)
        self.codec = tk.StringVar(self, "Opus 128 kbps")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text="共享进程", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(self)
        tools.grid(row=1, column=0, sticky="ew", pady=(10, 7))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="搜索").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(tools, textvariable=self.search).grid(row=0, column=1, sticky="ew")
        self.refresh_button = ttk.Button(tools, text="刷新进程", command=self.refresh)
        self.refresh_button.grid(row=0, column=2, padx=(8, 0))
        table = ttk.Frame(self)
        table.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(table, columns=("name", "title", "pid"), show="headings",
                                 selectmode="browse", height=6)
        for col, title, width in (("name", "程序", 185), ("title", "窗口标题", 270), ("pid", "PID", 75)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=width, minwidth=64, stretch=col != "pid")
        scrollbar = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", expand=True, fill="both")
        self.count = ttk.Label(self, style="Muted.TLabel")
        self.count.grid(row=3, column=0, sticky="w", pady=(4, 12))

        ttk.Label(self, text="采集音质", style="Heading.TLabel").grid(row=4, column=0, sticky="w")
        names = {"standard": "标准", "cd": "CD", "studio": "录音室"}
        values = [f"{names.get(key, key)}   {format_label(QUALITY_PRESETS[key])}" for key in self.quality_keys]
        self.quality = ttk.Combobox(self, state="readonly", values=[*values, "自定义"])
        self.quality.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.quality.current(self.quality_keys.index("standard"))
        self.custom = ttk.Frame(self)
        self.custom.grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Label(self.custom, text="采样率 (Hz)").pack(side="left")
        ttk.Entry(self.custom, width=9, textvariable=self.rate).pack(side="left", padx=(6, 16))
        ttk.Label(self.custom, text="位深 (bit)").pack(side="left")
        ttk.Combobox(self.custom, state="readonly", values=(16, 24, 32), width=5,
                     textvariable=self.depth).pack(side="left", padx=6)
        self.custom.grid_remove()

        upload = ttk.Frame(self)
        upload.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(upload, text="上传格式").pack(side="left", padx=(0, 8))
        self.codec_select = ttk.Combobox(upload, textvariable=self.codec, state="readonly",
                                         values=("Opus 64 kbps", "Opus 128 kbps", "Opus 256 kbps", "无损 PCM"))
        self.codec_select.pack(side="left")

        self.threshold = tk.StringVar(self, "跟随房间")
        ttk.Label(upload, text="同步阈值（秒）").pack(side="left", padx=8)
        ttk.Entry(upload, width=9, textvariable=self.threshold).pack(side="left")
        self.hint = ttk.Label(upload)
        self.hint.pack(side="left", padx=4)

        footer = ttk.Frame(self)
        footer.grid(row=8, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.message, wraplength=300).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="返回房间", command=back).grid(row=0, column=1, padx=8)
        self.action = ttk.Button(footer, text="开始主持", style="Primary.TButton", command=submit)
        self.action.grid(row=0, column=2)

        self.search.trace_add("write", lambda *_: self.filter_sources())
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.validate())
        self.quality.bind("<<ComboboxSelected>>", lambda _: self.change_quality())
        self.rate.trace_add("write", lambda *_: self.validate())
        self.depth.trace_add("write", lambda *_: self.validate())

    def room_state(self, state):
        suggested = state.get("sync_policy", {}).get("suggested_ms", 1000)
        self.hint.configure(text=f"建议 ≥ {suggested/1000:.1f}")
        self.action.configure(text="提交申请" if state["host"] else "开始主持")
        options = ["Opus 64 kbps", "Opus 128 kbps", "Opus 256 kbps"] if state["livekit"] else []
        options.append("无损 PCM")
        if state.get("flac"):
            options.append("无损 PCM · FLAC 压缩")
        if self.codec.get() not in options:
            self.codec.set("无损 PCM")
        self.codec_select.configure(values=options, state="readonly" if len(options)>1 else "disabled")

    def selected(self):
        rows = self.tree.selection()
        return self.rows.get(rows[0]) if rows else None

    def refresh(self):
        self.error_text = ""
        try:
            self.sources = list_sources()
        except OSError as error:
            self.sources = []
            self.error_text = f"读取进程失败，请重试。{error}"
        self.filter_sources()

    def filter_sources(self):
        keep = self.selected()
        term = self.search.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for src in self.sources:
            if term not in f"{src.name} {src.title} {src.pid}".casefold():
                continue
            row = self.tree.insert("", "end", values=(src.name, src.title or "—", src.pid))
            self.rows[row] = src
            if keep and src.key == keep.key:
                self.tree.selection_set(row)
                self.tree.see(row)
        self.count.configure(text=f"{len(self.rows)} 个进程" if self.rows else "没有匹配的进程")
        self.validate()

    def change_quality(self):
        if self.quality.current() == len(self.quality_keys):
            self.custom.grid()
        else:
            self.custom.grid_remove()
        self.validate()

    def selected_format(self):
        index = self.quality.current()
        if 0 <= index < len(self.quality_keys):
            return QUALITY_PRESETS[self.quality_keys[index]]
        try:
            return AudioFormat(int(self.rate.get()), int(self.depth.get()))
        except ValueError:
            raise ValueError("采样率须为 8000–192000 Hz 内的 100 的倍数，位深可选 16、24 或 32。") from None

    def validate(self):
        source = self.selected()
        try:
            self.selected_format()
            hint = f"将共享 {source.name} 及其子进程" if source else "请选择要共享的进程"
            valid = bool(source)
        except ValueError as exc:
            hint, valid = str(exc), False
        self.message.set(self.error_text or hint)
        self.action.state(["!disabled"] if valid else ["disabled"])
