from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from tkinter import ttk

from PIL import Image, ImageTk
from .covers import fetch_cover


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
