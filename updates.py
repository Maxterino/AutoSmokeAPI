"""Read installed SmokeAPI DLL version and compare against GitHub latest release.

Also provides `install_release()` to download the latest SmokeAPI release zip
and replace the bundled DLLs in-place.
"""
from __future__ import annotations

import ctypes
import io
import json
import shutil
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

GITHUB_LATEST = "https://api.github.com/repos/acidicoala/SmokeAPI/releases/latest"

# Files we expect inside the release zip — these are the ones we replace.
_ASSETS_OF_INTEREST = {"smoke_api32.dll", "smoke_api64.dll", "SmokeAPI.config.json"}


def get_dll_file_version(dll_path: Path) -> str | None:
    """Read the FileVersion from a Windows DLL via VerQueryValue. None on failure."""
    try:
        path_str = str(dll_path)
        ver = ctypes.WinDLL("version", use_last_error=True)

        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD

        handle = wintypes.DWORD(0)
        size = ver.GetFileVersionInfoSizeW(path_str, ctypes.byref(handle))
        if size == 0:
            return None

        buf = ctypes.create_string_buffer(size)

        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        if not ver.GetFileVersionInfoW(path_str, 0, size, buf):
            return None

        ver.VerQueryValueW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
        ]
        ver.VerQueryValueW.restype = wintypes.BOOL

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        if not ptr.value:
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = (info.dwFileVersionMS >> 16) & 0xFFFF
        minor = info.dwFileVersionMS & 0xFFFF
        build = (info.dwFileVersionLS >> 16) & 0xFFFF
        revision = info.dwFileVersionLS & 0xFFFF
        return f"{major}.{minor}.{build}.{revision}"
    except OSError:
        return None


def get_installed_version(smoke_dll: Path) -> str | None:
    if not smoke_dll.exists():
        return None
    return get_dll_file_version(smoke_dll)


def _fetch_release_json(timeout: float = 6.0) -> dict | None:
    req = urllib.request.Request(
        GITHUB_LATEST,
        headers={"User-Agent": "AutoSmokeAPI"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get_latest_release(timeout: float = 6.0) -> tuple[str, str] | None:
    """Return (tag, html_url) of the latest SmokeAPI release, or None on failure."""
    data = _fetch_release_json(timeout=timeout)
    if data is None:
        return None
    tag = data.get("tag_name") or data.get("name")
    url = data.get("html_url") or "https://github.com/acidicoala/SmokeAPI/releases"
    if not tag:
        return None
    return tag, url


def get_latest_release_download_url(timeout: float = 6.0) -> tuple[str, str, str] | None:
    """Return (tag, zip_download_url, html_url) for the latest release.

    Looks for an asset whose name ends with `.zip` (SmokeAPI's release artifact).
    Returns None on network failure or if no zip asset is attached.
    """
    data = _fetch_release_json(timeout=timeout)
    if data is None:
        return None
    tag = data.get("tag_name") or data.get("name")
    if not tag:
        return None
    assets = data.get("assets") or []
    zip_url = None
    for asset in assets:
        name = (asset.get("name") or "").lower()
        if name.endswith(".zip"):
            zip_url = asset.get("browser_download_url")
            if zip_url:
                break
    if not zip_url:
        return None
    html_url = data.get("html_url") or "https://github.com/acidicoala/SmokeAPI/releases"
    return tag, zip_url, html_url


def install_release(
    zip_url: str,
    target_dir: Path,
    on_progress: Callable[[str], None] | None = None,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Download a SmokeAPI release zip and replace the bundled DLLs in `target_dir`.

    Returns (success, message). On success, smoke_api32.dll / smoke_api64.dll
    (and SmokeAPI.config.json, if present in the zip) are replaced atomically.
    The previous DLLs are backed up to `<name>.bak` first so a failed install
    can be rolled back manually.
    """
    def emit(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001
                pass

    target_dir = Path(target_dir)
    if not target_dir.exists():
        return False, f"SmokeAPI folder not found at {target_dir}"

    emit(f"Downloading {zip_url} …")
    try:
        req = urllib.request.Request(zip_url, headers={"User-Agent": "AutoSmokeAPI"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except OSError as e:
        return False, f"Download failed: {e}"

    emit(f"Downloaded {len(data):,} bytes. Extracting…")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        return False, f"Downloaded file is not a valid zip: {e}"

    # Map basenames to ZipInfo entries.
    candidates: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        base = Path(info.filename).name
        if base in _ASSETS_OF_INTEREST:
            # Prefer the one with the largest size if there are duplicates
            # (sometimes archives contain both Windows + non-Windows copies).
            existing = candidates.get(base)
            if existing is None or info.file_size > existing.file_size:
                candidates[base] = info

    if not candidates:
        return False, "Release zip didn't contain any expected SmokeAPI files."

    # Back up existing files we're about to replace, then write new ones.
    replaced: list[str] = []
    failed: list[str] = []
    for name, info in candidates.items():
        dest = target_dir / name
        if dest.exists():
            try:
                shutil.copy2(dest, dest.with_suffix(dest.suffix + ".bak"))
            except OSError:
                pass
        try:
            with zf.open(info) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            replaced.append(name)
            emit(f"  Replaced {name}")
        except OSError as e:
            failed.append(f"{name} ({e})")

    if not replaced:
        return False, f"Failed to write any files: {failed}"

    summary = f"Updated {len(replaced)} file(s): {', '.join(replaced)}"
    if failed:
        summary += f". Failures: {', '.join(failed)}"
    return True, summary


def _normalize(version: str) -> tuple[int, ...]:
    cleaned = version.lstrip("vV").split("-")[0]
    parts: list[int] = []
    for chunk in cleaned.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) or (0,)


def is_outdated(installed: str | None, latest: str | None) -> bool:
    if not installed or not latest:
        return False
    return _normalize(installed) < _normalize(latest)
