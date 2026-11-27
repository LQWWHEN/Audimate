"""Start the Windows music room with python -m client."""
import argparse
from pathlib import Path


DEFAULT_DLL = Path(__file__).resolve().parents[1] / "build" / "Release" / "audimate_capture.dll"
FONT = "Microsoft YaHei UI"
PAPER, INK, MUTED, ACCENT = "#f6f7f9", "#1c2935", "#526270", "#006c60"


def main(*, development=False):
    parser = argparse.ArgumentParser(description="Audimate 在线音乐房间")
    parser.add_argument("--dll", type=Path, default=DEFAULT_DLL, help="采音 DLL 路径")
    parser.add_argument("--demo", action="store_true", help="直接试听本地合成曲目与频谱")
    parser.add_argument("--legacy", action="store_true", help="使用初版 Tk 界面")
    args = parser.parse_args()
    if not args.legacy:
        from .desktop import launch
        return launch(args.dll, development, args.demo)
    import tkinter as tk
    from tkinter import ttk
    from .online import OnlineRoom
    root = tk.Tk()
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
    OnlineRoom(root, args.dll, development=development)
    root.mainloop()


if __name__ == "__main__":
    main()
