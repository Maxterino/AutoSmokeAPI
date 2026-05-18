"""Core logic for AutoSmokeAPI: PE detection, patching, scanning, persistence."""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable

# APP_DIR: bundled read-only data (lives in PyInstaller's _MEIPASS when frozen).
# USER_DIR: writable state next to the .exe; survives across runs.
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
if getattr(sys, "frozen", False):
    USER_DIR = Path(sys.executable).resolve().parent
else:
    USER_DIR = Path(__file__).resolve().parent

SMOKEAPI_DIR = APP_DIR / "SmokeAPI"
SMOKE_DLL_32 = SMOKEAPI_DIR / "smoke_api32.dll"
SMOKE_DLL_64 = SMOKEAPI_DIR / "smoke_api64.dll"
SMOKE_CONFIG = SMOKEAPI_DIR / "SmokeAPI.config.json"

STATE_FILE = USER_DIR / "autosmokeapi_state.json"

ARCH_32 = "x86"
ARCH_64 = "x64"
ARCH_UNKNOWN = "unknown"

STATUS_PATCHED = "patched"
STATUS_UNPATCHED = "unpatched"
STATUS_MISSING = "missing"
STATUS_UNKNOWN = "unknown"


def detect_pe_arch(dll_path: Path) -> str:
    """Read the PE header and return ARCH_32, ARCH_64, or ARCH_UNKNOWN.

    Falls back to filename heuristic if the header is unreadable.
    """
    try:
        with open(dll_path, "rb") as f:
            mz = f.read(2)
            if mz != b"MZ":
                return _arch_from_name(dll_path)
            f.seek(0x3C)
            e_lfanew = struct.unpack("<I", f.read(4))[0]
            f.seek(e_lfanew)
            pe_sig = f.read(4)
            if pe_sig != b"PE\0\0":
                return _arch_from_name(dll_path)
            machine = struct.unpack("<H", f.read(2))[0]
            if machine == 0x014C:
                return ARCH_32
            if machine == 0x8664:
                return ARCH_64
            return ARCH_UNKNOWN
    except OSError:
        return _arch_from_name(dll_path)


def _arch_from_name(dll_path: Path) -> str:
    name = dll_path.name.lower()
    if name == "steam_api64.dll":
        return ARCH_64
    if name == "steam_api.dll":
        return ARCH_32
    return ARCH_UNKNOWN


def steam_dll_kind(path: Path) -> str | None:
    """Return '64' if path is steam_api64.dll, '32' if steam_api.dll, else None."""
    n = path.name.lower()
    if n == "steam_api64.dll":
        return "64"
    if n == "steam_api.dll":
        return "32"
    return None


def backup_path_for(steam_dll: Path) -> Path:
    """The _o (original) backup path that proxy mode uses."""
    kind = steam_dll_kind(steam_dll)
    if kind == "64":
        return steam_dll.with_name("steam_api64_o.dll")
    if kind == "32":
        return steam_dll.with_name("steam_api_o.dll")
    raise ValueError(f"Not a recognized Steamworks DLL: {steam_dll.name}")


def smoke_source_dll(arch: str) -> Path:
    if arch == ARCH_64:
        return SMOKE_DLL_64
    if arch == ARCH_32:
        return SMOKE_DLL_32
    raise ValueError(f"Cannot pick SmokeAPI DLL for arch={arch}")


@dataclass
class Game:
    """A tracked steam_api.dll / steam_api64.dll location."""
    path: str
    name: str = ""
    arch: str = ARCH_UNKNOWN
    appid: str = ""  # Steam app ID, used for the header image lookup

    @property
    def dll_path(self) -> Path:
        return Path(self.path)

    @property
    def folder(self) -> Path:
        return self.dll_path.parent

    @property
    def backup_path(self) -> Path:
        return backup_path_for(self.dll_path)

    def refresh(self) -> None:
        """Re-detect arch and game name from disk."""
        if self.dll_path.exists():
            self.arch = detect_pe_arch(self.dll_path)
        elif self.backup_path.exists():
            self.arch = detect_pe_arch(self.backup_path)
        if not self.name:
            self.name = detect_game_name(self.dll_path)
        if not self.appid:
            self.appid = detect_appid_for_dll(self.dll_path) or ""

    def status(self) -> str:
        """Determine patch status by checking the backup file and DLL contents."""
        dll = self.dll_path
        backup = self.backup_path
        if not dll.exists() and not backup.exists():
            return STATUS_MISSING
        if backup.exists() and dll.exists():
            return STATUS_PATCHED
        if dll.exists() and not backup.exists():
            return STATUS_UNPATCHED
        return STATUS_UNKNOWN


def detect_game_name(dll_path: Path) -> str:
    """Walk up the path to find a folder under steamapps/common/ - that's the game name."""
    parts = dll_path.parts
    lower = [p.lower() for p in parts]
    try:
        idx = lower.index("common")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    # Fallback: use the deepest meaningful parent folder.
    for parent in dll_path.parents:
        n = parent.name.lower()
        if n and n not in {"bin", "binaries", "x64", "x86", "win64", "win32", "redist"}:
            return parent.name
    return dll_path.parent.name or "Unknown game"


class PatchError(Exception):
    pass


def patch_game(game: Game, *, deploy_config: bool = False) -> None:
    """Apply SmokeAPI in proxy mode to this game."""
    dll = game.dll_path
    backup = game.backup_path

    if not dll.exists() and not backup.exists():
        raise PatchError(f"DLL not found: {dll}")

    arch = detect_pe_arch(dll if dll.exists() else backup)
    if arch == ARCH_UNKNOWN:
        raise PatchError(f"Could not determine architecture of {dll.name}")
    game.arch = arch

    source = smoke_source_dll(arch)
    if not source.exists():
        raise PatchError(f"Missing SmokeAPI DLL: {source}")

    if not backup.exists():
        try:
            dll.rename(backup)
        except OSError as e:
            raise PatchError(f"Could not rename {dll.name} -> {backup.name}: {e}") from e
    elif dll.exists():
        # The backup already exists, so this DLL is the SmokeAPI proxy from a
        # previous patch. Drop it and recopy to make sure we match our bundled version.
        try:
            dll.unlink()
        except OSError as e:
            raise PatchError(f"Could not remove existing {dll.name}: {e}") from e

    try:
        shutil.copy2(source, dll)
    except OSError as e:
        # Restore the backup so the game isn't left without any steam_api dll.
        if backup.exists() and not dll.exists():
            try:
                backup.rename(dll)
            except OSError:
                pass
        raise PatchError(f"Could not copy SmokeAPI DLL: {e}") from e

    config_target = dll.with_name("SmokeAPI.config.json")
    if deploy_config and SMOKE_CONFIG.exists():
        try:
            shutil.copy2(SMOKE_CONFIG, config_target)
        except OSError as e:
            raise PatchError(f"Patched, but failed to deploy config: {e}") from e


def revert_game(game: Game) -> None:
    """Revert a SmokeAPI patch by restoring the _o backup."""
    dll = game.dll_path
    backup = game.backup_path

    if not backup.exists():
        if dll.exists() and game.status() == STATUS_UNPATCHED:
            return
        raise PatchError(f"No backup found to revert: {backup.name} missing")

    if dll.exists():
        try:
            dll.unlink()
        except OSError as e:
            raise PatchError(f"Could not remove SmokeAPI DLL: {e}") from e

    try:
        backup.rename(dll)
    except OSError as e:
        raise PatchError(f"Could not restore {backup.name} -> {dll.name}: {e}") from e

    config_target = dll.with_name("SmokeAPI.config.json")
    if config_target.exists():
        try:
            config_target.unlink()
        except OSError:
            pass


_VDF_PATH_RE = re.compile(r'"path"\s*"([^"]+)"', re.IGNORECASE)
_APPID_FILE_RE = re.compile(r'^appmanifest_(\d+)\.acf$', re.IGNORECASE)
_INSTALLDIR_RE = re.compile(r'"installdir"\s*"([^"]+)"', re.IGNORECASE)
_NAME_KV_RE = re.compile(r'"name"\s*"([^"]+)"', re.IGNORECASE)

# Game subfolders that never hold Steamworks DLLs but can have thousands of
# files - listing them here speeds the scan up by orders of magnitude.
_SKIP_SUBFOLDERS = frozenset([
    "saves", "savedata", "savegames", "saved", "logs", "cache", "caches",
    "tmp", "temp", "crashes", "crashreports", "crashdumps",
    "redist", "_commonredist", "directx", "vcredist",
    "thumbnails", "screenshots", "movies", "music", "soundtracks", "soundtrack",
    "videos", "wallpapers",
    ".git", ".svn", "node_modules", "__pycache__",
])

_GAME_FOLDER_MAX_DEPTH = 7


def find_steam_install() -> Path | None:
    """Locate the Steam install directory via registry then common paths."""
    candidates: list[Path] = []
    try:
        import winreg  # type: ignore
        for hive, sub in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            v, _ = winreg.QueryValueEx(k, value_name)
                            if v:
                                candidates.append(Path(v))
                        except OSError:
                            pass
            except OSError:
                continue
    except ImportError:
        pass

    candidates.extend([
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ])
    for c in candidates:
        if (c / "steamapps").exists():
            return c
    return None


def steam_library_folders(steam_root: Path) -> list[Path]:
    """Read libraryfolders.vdf and return all library steamapps paths."""
    libs: list[Path] = [steam_root / "steamapps"]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.exists():
        return libs
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libs
    for match in _VDF_PATH_RE.finditer(text):
        raw = match.group(1).replace("\\\\", "\\")
        p = Path(raw) / "steamapps"
        if p.exists() and p not in libs:
            libs.append(p)
    return libs


def _candidate_drives() -> list[Path]:
    """Return all accessible drive roots on Windows (or '/' elsewhere)."""
    if os.name != "nt":
        return [Path("/")]
    import string
    drives: list[Path] = []
    for letter in string.ascii_uppercase:
        d = Path(f"{letter}:\\")
        try:
            if d.exists():
                drives.append(d)
        except OSError:
            continue
    return drives


def find_all_steamapps() -> list[Path]:
    """Find every steamapps folder we can: registry, VDF, and drive scan.

    Drive scan only checks a handful of well-known folder patterns (no deep
    walk), so it stays fast even on systems with many drives.
    """
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        if not p.exists() or not (p / "common").exists():
            return
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(p)

    # Source 1: registry-detected Steam install + libraryfolders.vdf.
    steam = find_steam_install()
    if steam:
        for lib in steam_library_folders(steam):
            add(lib)

    # Source 2: common per-drive folder patterns, in case the user installed
    # Steam somewhere unusual (or has an extra library Steam doesn't know about).
    patterns = [
        ("Steam", "steamapps"),
        ("SteamLibrary", "steamapps"),
        ("Games", "Steam", "steamapps"),
        ("Games", "SteamLibrary", "steamapps"),
        ("Program Files (x86)", "Steam", "steamapps"),
        ("Program Files", "Steam", "steamapps"),
        ("steamapps",),
    ]
    for drive in _candidate_drives():
        for parts in patterns:
            add(drive.joinpath(*parts))

    return found


def parse_appmanifest(acf: Path) -> tuple[str, str, str] | None:
    """Read an appmanifest_*.acf file. Returns (appid, name, installdir) or None."""
    m = _APPID_FILE_RE.match(acf.name)
    if not m:
        return None
    appid = m.group(1)
    try:
        text = acf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    inst_match = _INSTALLDIR_RE.search(text)
    name_match = _NAME_KV_RE.search(text)
    if not inst_match:
        return None
    return appid, (name_match.group(1) if name_match else ""), inst_match.group(1)


def build_appid_map(steamapps: Path) -> dict[str, tuple[str, str]]:
    """Map installdir.lower() -> (appid, game_name) from appmanifest files."""
    out: dict[str, tuple[str, str]] = {}
    try:
        for entry in steamapps.iterdir():
            if not entry.is_file():
                continue
            parsed = parse_appmanifest(entry)
            if parsed:
                appid, name, installdir = parsed
                out[installdir.lower()] = (appid, name)
    except OSError:
        pass
    return out


def detect_appid_for_dll(dll_path: Path) -> str | None:
    """Walk up from a DLL path until we find a `common/<installdir>` folder,
    then look up the installdir in the sibling steamapps appmanifest files.
    """
    parts_lower = [p.lower() for p in dll_path.parts]
    try:
        idx = parts_lower.index("common")
    except ValueError:
        return None
    if idx + 1 >= len(dll_path.parts):
        return None
    installdir = dll_path.parts[idx + 1]
    steamapps = Path(*dll_path.parts[: idx + 1]).parent  # ".../steamapps"
    appid_map = build_appid_map(steamapps)
    info = appid_map.get(installdir.lower())
    return info[0] if info else None


def _find_steamworks_dlls_in(
    game_dir: Path,
    max_depth: int = _GAME_FOLDER_MAX_DEPTH,
) -> list[Path]:
    """Depth-limited walk of a single game folder for steam_api(.|64).dll.

    Skips known-irrelevant subdirectories (saves, caches, etc.) which keeps
    the scan fast even for games with huge content trees.
    """
    targets = ("steam_api.dll", "steam_api64.dll")
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            with os.scandir(d) as it:
                for entry in it:
                    name_lower = entry.name.lower()
                    try:
                        if entry.is_file(follow_symlinks=False):
                            if name_lower in targets:
                                found.append(Path(entry.path))
                        elif entry.is_dir(follow_symlinks=False):
                            if name_lower in _SKIP_SUBFOLDERS:
                                continue
                            walk(Path(entry.path), depth + 1)
                    except OSError:
                        continue
        except (OSError, PermissionError):
            return

    walk(game_dir, 0)
    return found


def scan_for_steam_apis(
    libraries: Iterable[Path],
    on_progress=None,
    cancel_check=None,
) -> list[tuple[Path, str, str]]:
    """Scan libraries for Steamworks DLLs. Returns (path, appid, game_name) tuples.

    `appid` is "" when we can't match the game folder to an appmanifest.
    `on_progress(msg)` is called periodically with status text.
    `cancel_check()` returning truthy aborts the scan early.
    """
    results: list[tuple[Path, str, str]] = []
    seen_paths: set[str] = set()

    def emit(msg: str) -> None:
        if on_progress is not None:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001 - UI callbacks must not break the scan
                pass

    libraries = list(libraries)
    for lib_idx, lib in enumerate(libraries, 1):
        if cancel_check and cancel_check():
            emit("Scan cancelled.")
            return results
        common = lib / "common"
        if not common.exists():
            continue

        appid_map = build_appid_map(lib)
        try:
            game_dirs = [d for d in common.iterdir() if d.is_dir()]
        except OSError:
            continue

        emit(f"[{lib_idx}/{len(libraries)}] Scanning {lib.parent} - {len(game_dirs)} games…")

        lib_hits = 0
        for game_dir in game_dirs:
            if cancel_check and cancel_check():
                emit("Scan cancelled.")
                return results
            info = appid_map.get(game_dir.name.lower())
            appid = info[0] if info else ""
            name = (info[1] if info else "") or game_dir.name

            for dll in _find_steamworks_dlls_in(game_dir):
                key = str(dll).lower()
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                results.append((dll, appid, name))
                lib_hits += 1

        emit(f"  └─ {lib.parent.name}: {lib_hits} DLL(s) found")

    emit(f"Scan complete: {len(results)} Steamworks DLL(s) across {len(libraries)} libraries.")
    return results


@dataclass
class AppState:
    games: list[Game] = field(default_factory=list)
    deploy_config: bool = False

    def to_dict(self) -> dict:
        return {
            "deploy_config": self.deploy_config,
            "games": [asdict(g) for g in self.games],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppState":
        games_raw = data.get("games", [])
        games: list[Game] = []
        for g in games_raw:
            try:
                games.append(Game(
                    path=g.get("path", ""),
                    name=g.get("name", ""),
                    arch=g.get("arch", ARCH_UNKNOWN),
                ))
            except TypeError:
                continue
        return cls(
            games=games,
            deploy_config=bool(data.get("deploy_config", False)),
        )


def load_state() -> AppState:
    if not STATE_FILE.exists():
        return AppState()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppState()
    return AppState.from_dict(data)


def save_state(state: AppState) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    except OSError:
        pass
