"""Load the room's album art away from the audio, GUI and room-poll threads."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from io import BytesIO
from tkinter import ttk
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from PIL import Image, ImageTk


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch_cover(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc.endswith((".mzstatic.com", ".shazam.com")) or "@" in parts.netloc:
        return None
    with build_opener(NoRedirect).open(url, timeout=4) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        return None
    with Image.open(BytesIO(data)) as img:
        if img.width * img.height > 4_000_000:
            return None
        img.thumbnail((76, 76))
        return img.convert("RGB")


class Cover(ttk.Label):
    def __init__(self, parent):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="album-art")
        self.pending = None
        self.url = self.requested = ""
        self.photo = None

    def show(self, url):
        if url != self.url:
            self.url = url
            self.grid_remove()
            self.configure(image="")
            self.photo = None
        if self.pending and self.pending.done():
            try:
                img = self.pending.result()
                if img is not None and self.requested == self.url:
                    self.photo = ImageTk.PhotoImage(img, master=self)
                    self.configure(image=self.photo)
                    self.grid()
            except (OSError, HTTPException, ValueError, Image.DecompressionBombError):
                pass
            self.pending = None
        if not self.pending and self.requested != self.url:
            self.requested = self.url
            if self.url:
                self.pending = self.pool.submit(fetch_cover, self.url)

    def destroy(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().destroy()
