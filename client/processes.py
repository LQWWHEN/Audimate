"""Selectable Windows processes in the current desktop session."""
from contextlib import contextmanager
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import ntpath
import os
import re


@dataclass(frozen=True)
class ProcessSource:
    pid: int
    started: int  # Windows FILETIME, also distinguishes a reused PID
    name: str
    title: str

    @property
    def key(self):
        return self.pid, self.started


kernel = ct.WinDLL("kernel32", use_last_error=True)
user = ct.WinDLL("user32", use_last_error=True)
window_callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def bind(library, name, result, *args):
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


enum_pids = bind(kernel, "K32EnumProcesses", wt.BOOL, ct.POINTER(wt.DWORD), wt.DWORD, ct.POINTER(wt.DWORD))
session_id = bind(kernel, "ProcessIdToSessionId", wt.BOOL, wt.DWORD, ct.POINTER(wt.DWORD))
open_process = bind(kernel, "OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
close_handle = bind(kernel, "CloseHandle", wt.BOOL, wt.HANDLE)
process_times = bind(kernel, "GetProcessTimes", wt.BOOL, wt.HANDLE, *([ct.POINTER(wt.FILETIME)] * 4))
image_name = bind(kernel, "QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD))
wait = bind(kernel, "WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
enum_windows = bind(user, "EnumWindows", wt.BOOL, window_callback, wt.LPARAM)
visible = bind(user, "IsWindowVisible", wt.BOOL, wt.HWND)
window_pid = bind(user, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ct.POINTER(wt.DWORD))
window_text = bind(user, "GetWindowTextW", ct.c_int, wt.HWND, wt.LPWSTR, ct.c_int)


@contextmanager
def process_handle(pid):
    # SYNCHRONIZE and PROCESS_QUERY_LIMITED_INFORMATION
    handle = open_process(0x100000 | 0x1000, False, pid)
    if not handle:
        raise ct.WinError(ct.get_last_error())
    try:
        yield handle
    finally:
        close_handle(handle)


def creation_time(handle):
    times = [wt.FILETIME() for _ in range(4)]
    if not process_times(handle, *(ct.byref(t) for t in times)):
        raise ct.WinError(ct.get_last_error())
    return times[0].dwHighDateTime << 32 | times[0].dwLowDateTime


@contextmanager
def selected_process(source):
    """Keep this process object alive while the capture DLL opens its PID."""
    with process_handle(source.pid) as handle:
        if creation_time(handle) != source.started or wait(handle, 0) != 258:  # WAIT_TIMEOUT = still running
            raise OSError("所选进程已退出或重启，请刷新后重新选择。")
        yield


def window_titles(only_pid=None):
    titles = {}
    text = ct.create_unicode_buffer(512)

    @window_callback
    def collect(hwnd, _):
        if visible(hwnd):
            pid = wt.DWORD()
            window_pid(hwnd, ct.byref(pid))
            if (only_pid is None or pid.value == only_pid) and window_text(hwnd, text, len(text)):
                titles.setdefault(pid.value, []).append(text.value)
        return True

    if not enum_windows(collect, 0):
        raise ct.WinError(ct.get_last_error())
    return titles


def track_from_title(process_name, caption):
    """NetEase uses title - artist. Elsewhere require explicit song notation."""
    parts = re.split(r"\s+[-–—]\s+", " ".join(caption.split()))
    netease = process_name.casefold() == "cloudmusic.exe"
    player_names = {"网易云音乐", "网易云音乐播放器", "netease cloud music"}
    if netease and parts[-1].casefold() in player_names:
        parts.pop()
    if len(parts) != 2:
        return None
    title, artist = parts
    if title.startswith("《") and title.endswith("》"):
        title = title[1:-1].strip()
    elif not netease:
        return None
    if any(not value or len(value) > 160 or not value.isprintable() or
           value.casefold() in player_names for value in (title, artist)):
        return None
    return {"title": title, "artist": artist}


def current_track(source):
    try:
        with selected_process(source):
            captions = window_titles(source.pid).get(source.pid, ())
    except OSError:
        return None
    tracks = [track for caption in captions if (track := track_from_title(source.name, caption))]
    if tracks and all(track == tracks[0] for track in tracks):
        return tracks[0]
    return None


def list_sources():
    own_session = wt.DWORD()
    if not session_id(os.getpid(), ct.byref(own_session)):
        raise ct.WinError(ct.get_last_error())
    capacity = 512
    used = wt.DWORD()
    while True:
        pids = (wt.DWORD * capacity)()
        if not enum_pids(pids, ct.sizeof(pids), ct.byref(used)):
            raise ct.WinError(ct.get_last_error())
        if used.value < ct.sizeof(pids):
            break
        capacity *= 2

    titles = window_titles()
    sources = []
    path = ct.create_unicode_buffer(32768)
    for pid in pids[:used.value // ct.sizeof(wt.DWORD)]:
        session = wt.DWORD()
        if pid == os.getpid() or not session_id(pid, ct.byref(session)) or session.value != own_session.value:
            continue
        try:
            with process_handle(pid) as handle:
                length = wt.DWORD(len(path))
                if image_name(handle, 0, path, ct.byref(length)):
                    sources.append(ProcessSource(pid, creation_time(handle), ntpath.basename(path.value),
                                                 titles[pid][0] if pid in titles else ""))
        except OSError:
            continue  # Protected or exited between enumeration and inspection.
    return sorted(sources, key=lambda src: (not src.title, src.name.casefold(), src.pid))
