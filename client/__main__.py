"""Run the Windows desktop prototype with python -m client."""
import argparse
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from host.capture.record import AudioFormat, DEFAULT_FORMAT, QUALITY_PRESETS
from .processes import list_sources
from .sharing import LocalCapture, ShareSelection


DEFAULT_DLL = Path(__file__).resolve().parents[1] / "build" / "Release" / "audimate_capture.dll"
FONT = "Microsoft YaHei UI"
PAPER, INK, MUTED, ACCENT = "#f6f7f9", "#1c2935", "#526270", "#006c60"


def format_label(fmt):
    return f"{fmt.sample_rate / 1000:g} kHz · {fmt.bits_per_sample}-bit"


class Client:
    def __init__(self, root, dll):
        self.root, self.dll = root, dll
        self.worker = None
        self.closing = False
        self.error_text = ""
        self.sources = []
        self.rows = {}
        self.quality_keys = list(QUALITY_PRESETS)
        self.search = tk.StringVar(root)
        self.rate = tk.StringVar(root, str(DEFAULT_FORMAT.sample_rate))
        self.depth = tk.StringVar(root, str(DEFAULT_FORMAT.bits_per_sample))
        self.message = tk.StringVar(root)

        root.title("Audimate · 本地客户端")
        root.geometry("820x650")
        root.minsize(670, 565)
        root.configure(bg=PAPER)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=PAPER, foreground=INK)
        style.configure("Heading.TLabel", font=(FONT, 14, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=29)
        style.map("Treeview", background=[("selected", "#c7e9e2")], foreground=[("selected", INK)])
        style.configure("Primary.TButton", background=ACCENT, foreground="white", padding=(18, 8))
        style.map("Primary.TButton", background=[("disabled", "#586973"), ("active", "#005b52")],
                  foreground=[("disabled", "#e4e9ed")])
        style.configure("Level.Horizontal.TProgressbar", background=ACCENT, troughcolor="#dce4e6")

        bar = tk.Frame(root, bg=INK, padx=24, pady=12)
        bar.grid(row=0, column=0, sticky="ew")
        tk.Label(bar, text="Audimate", fg="white", bg=INK, font=("Segoe UI", 19, "bold")).pack(side="left")
        tk.Label(bar, text="本地房间", fg="#b6c7c8", bg=INK, font=(FONT, 10)).pack(side="left", padx=14)
        self.role = tk.Label(bar, text="听众", fg="#a8e2d7", bg=INK, font=(FONT, 11))
        self.role.pack(side="right")

        self.settings = ttk.Frame(root, padding=(24, 18))
        self.settings.grid(row=1, column=0, sticky="nsew")
        self.settings.columnconfigure(0, weight=1)
        self.settings.rowconfigure(2, weight=1)
        ttk.Label(self.settings, text="共享进程", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        tools = ttk.Frame(self.settings)
        tools.grid(row=1, column=0, sticky="ew", pady=(10, 7))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="搜索").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(tools, textvariable=self.search).grid(row=0, column=1, sticky="ew")
        self.refresh_button = ttk.Button(tools, text="刷新进程", command=self.refresh)
        self.refresh_button.grid(row=0, column=2, padx=(8, 0))
        table = ttk.Frame(self.settings)
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
        self.count = ttk.Label(self.settings, style="Muted.TLabel")
        self.count.grid(row=3, column=0, sticky="w", pady=(4, 12))

        ttk.Label(self.settings, text="采集音质", style="Heading.TLabel").grid(row=4, column=0, sticky="w")
        names = {"standard": "标准", "cd": "CD", "studio": "录音室"}
        values = [f"{names.get(key, key)}   {format_label(QUALITY_PRESETS[key])}" for key in self.quality_keys]
        self.quality = ttk.Combobox(self.settings, state="readonly", values=[*values, "自定义"])
        self.quality.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.quality.current(self.quality_keys.index("standard"))
        self.custom = ttk.Frame(self.settings)
        self.custom.grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Label(self.custom, text="采样率 (Hz)").pack(side="left")
        ttk.Entry(self.custom, width=9, textvariable=self.rate).pack(side="left", padx=(6, 16))
        ttk.Label(self.custom, text="位深 (bit)").pack(side="left")
        ttk.Combobox(self.custom, state="readonly", values=(16, 24, 32), width=5,
                     textvariable=self.depth).pack(side="left", padx=6)
        self.custom.grid_remove()

        self.live = ttk.Frame(root, padding=(24, 24))
        self.live.columnconfigure(1, weight=1)
        self.source_name = ttk.Label(self.live, style="Heading.TLabel")
        self.source_name.grid(row=0, column=0, columnspan=3, sticky="w")
        self.source_info = ttk.Label(self.live, style="Muted.TLabel")
        self.source_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 28))
        ttk.Label(self.live, text="输入电平", style="Heading.TLabel").grid(row=2, column=0, columnspan=3, sticky="w")
        self.meters = []
        for row, name in enumerate(("左", "右"), 3):
            ttk.Label(self.live, text=name).grid(row=row, column=0, padx=(0, 12), pady=6)
            meter = ttk.Progressbar(self.live, style="Level.Horizontal.TProgressbar", maximum=60)
            meter.grid(row=row, column=1, sticky="ew")
            reading = ttk.Label(self.live, width=14, anchor="e")
            reading.grid(row=row, column=2)
            self.meters.append((meter, reading))

        footer = ttk.Frame(root, padding=(24, 8, 24, 16))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.notice = ttk.Label(footer, textvariable=self.message, wraplength=450)
        self.notice.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.action = ttk.Button(footer, text="开始主持", style="Primary.TButton", command=self.activate)
        self.action.grid(row=0, column=1)

        self.search.trace_add("write", lambda *_: self.filter_sources())
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.validate())
        self.quality.bind("<<ComboboxSelected>>", lambda _: self.change_quality())
        self.rate.trace_add("write", lambda *_: self.validate())
        self.depth.trace_add("write", lambda *_: self.validate())
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.close)

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
        if self.worker:
            return
        source = self.selected()
        try:
            self.selected_format()
            hint = f"将共享 {source.name} 及其子进程" if source else "请选择要共享的进程"
            valid = bool(source)
        except ValueError as exc:
            hint, valid = str(exc), False
        self.message.set(self.error_text or hint)
        self.action.state(["!disabled"] if valid else ["disabled"])

    def activate(self):
        if self.worker:
            self.worker.cancel.set()
            self.action.state(["disabled"])
            self.message.set("正在结束采音…")
            return
        source = self.selected()
        if not source:
            return
        try:
            selection = ShareSelection(source, self.selected_format())
        except ValueError:
            self.validate()
            return
        self.worker = LocalCapture(self.dll, selection)
        self.settings.grid_remove()
        self.live.grid(row=1, column=0, sticky="nsew")
        self.source_name.configure(text=source.name)
        self.source_info.configure(text=f"PID {source.pid}   ·   {format_label(selection.audio_format)}   ·   双声道")
        self.action.configure(text="取消")
        self.role.configure(text="准备中")
        self.message.set("正在打开所选音源…")
        self.update_levels((0, 0))
        self.worker.start()
        self.poll()

    def update_levels(self, levels):
        for level, (meter, reading) in zip(levels, self.meters):
            db = 20 * math.log10(level) if level else -math.inf
            meter.configure(value=max(0, 60 + db))
            reading.configure(text=f"{db:.1f} dBFS" if level else "−∞ dBFS")

    def poll(self):
        worker = self.worker
        if worker.is_alive():
            if worker.ready.is_set() and not worker.cancel.is_set():
                self.role.configure(text="本地主持")
                self.action.configure(text="下台")
                self.message.set("正在采集所选进程的声音")
                self.update_levels(worker.levels)
            self.root.after(80, self.poll)
            return
        worker.join()
        self.worker = None
        if self.closing:
            self.root.destroy()
            return
        self.live.grid_remove()
        self.settings.grid()
        self.role.configure(text="听众")
        self.action.configure(text="开始主持")
        self.refresh()
        if worker.error:
            self.error_text = f"采音已结束，请刷新或重新选择。{worker.error}"
            self.validate()

    def close(self):
        self.closing = True
        if self.worker:
            self.worker.cancel.set()
            self.root.withdraw()
        else:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Audimate 本地客户端")
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL, help="采音 DLL 路径")
    args = parser.parse_args()
    root = tk.Tk()
    Client(root, args.dll)
    root.mainloop()


if __name__ == "__main__":
    main()
