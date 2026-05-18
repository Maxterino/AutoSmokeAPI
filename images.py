"""Fetch and cache Steam header images by app ID."""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

import sys

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).resolve().parent
else:
    _BASE = Path(__file__).resolve().parent
CACHE_DIR = _BASE / ".image_cache"

# Steam's CDN URL for the 460x215 horizontal header image.
_HEADER_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
_FALLBACK_URLS = [
    "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg",
    "https://steamcdn-a.akamaihd.net/steam/apps/{appid}/header.jpg",
]

_lock = threading.Lock()
# AppIDs that failed once - skip them for the rest of the session so we don't
# hammer the network for images Steam doesn't have.
_failed: set[str] = set()


def cache_path_for(appid: str) -> Path:
    return CACHE_DIR / f"{appid}.jpg"


def get_cached(appid: str) -> Path | None:
    """Return the local cache file if we already have the image; else None."""
    if not appid:
        return None
    p = cache_path_for(appid)
    if p.exists() and p.stat().st_size > 0:
        return p
    return None


def download_header(appid: str, timeout: float = 6.0) -> Path | None:
    """Download the header image for `appid` and cache it. Returns the path or None."""
    if not appid:
        return None
    cached = get_cached(appid)
    if cached:
        return cached
    with _lock:
        if appid in _failed:
            return None

    CACHE_DIR.mkdir(exist_ok=True)
    urls = [_HEADER_URL.format(appid=appid)]
    urls.extend(u.format(appid=appid) for u in _FALLBACK_URLS)

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AutoSmokeAPI"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                data = resp.read()
            if not data:
                continue
            out = cache_path_for(appid)
            out.write_bytes(data)
            return out
        except OSError:
            continue

    with _lock:
        _failed.add(appid)
    return None


def fetch_async(appid: str, on_ready) -> None:
    """Fetch the image in a background thread, then call `on_ready(path or None)`."""
    if not appid:
        on_ready(None)
        return
    cached = get_cached(appid)
    if cached:
        on_ready(cached)
        return

    def work() -> None:
        path = download_header(appid)
        try:
            on_ready(path)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=work, daemon=True, name=f"img-{appid}").start()
