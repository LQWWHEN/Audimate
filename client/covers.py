"""Load the room's album art away from the audio, GUI and room-poll threads."""
from io import BytesIO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from PIL import Image


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def fetch_cover(url, size=76):
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
        img.thumbnail((size, size))
        return img.convert("RGB")
