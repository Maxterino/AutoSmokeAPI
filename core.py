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

METHOD_PROXY = "proxy"
METHOD_HOOK = "hook"

# Self-Hook drops SmokeAPI under one of these Windows system-DLL names next to
# the game's main .exe; version.dll has the broadest game compatibility.
HOOK_DEFAULT_NAME = "version.dll"
HOOK_VALID_NAMES = ("version.dll", "winhttp.dll", "winmm.dll")


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
    # Last patch method applied via this app: "" (none) / "proxy" / "hook".
    patch_method: str = ""
    # Hook-mode bookkeeping: where the main .exe is and which hijack DLL name
    # we used. We need both to revert cleanly.
    exe_path: str = ""
    hook_dll_name: str = ""
    # Whether we deployed a SmokeAPI.config.json the last time we patched.
    config_deployed: bool = False

    @property
    def dll_path(self) -> Path:
        return Path(self.path)

    @property
    def folder(self) -> Path:
        return self.dll_path.parent

    @property
    def backup_path(self) -> Path:
        return backup_path_for(self.dll_path)

    def hook_artifact(self) -> Path | None:
        """Path of the hook-mode DLL we installed, if any."""
        if not self.exe_path or not self.hook_dll_name:
            return None
        return Path(self.exe_path).parent / self.hook_dll_name

    def refresh(self) -> None:
        if self.dll_path.exists():
            self.arch = detect_pe_arch(self.dll_path)
        elif self.backup_path.exists():
            self.arch = detect_pe_arch(self.backup_path)
        if not self.name:
            self.name = detect_game_name(self.dll_path)
        if not self.appid:
            self.appid = detect_appid_for_dll(self.dll_path) or ""

    def has_proxy_patch(self) -> bool:
        return self.backup_path.exists() and self.dll_path.exists()

    def has_hook_patch(self) -> bool:
        artifact = self.hook_artifact()
        return artifact is not None and artifact.exists()

    def status(self) -> str:
        dll = self.dll_path
        backup = self.backup_path
        if self.has_proxy_patch() or self.has_hook_patch():
            return STATUS_PATCHED
        if dll.exists():
            return STATUS_UNPATCHED
        if backup.exists():
            # Proxy DLL gone but backup is still there - half-broken state, but
            # we can revert from it.
            return STATUS_PATCHED
        return STATUS_MISSING


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


# Filename keywords that almost certainly aren't the main game executable.
_LAUNCHER_KEYWORDS = (
    "launcher", "setup", "install", "uninstall", "unins", "redist",
    "vcredist", "vc_redist", "directx", "dxsetup", "patcher", "updater",
    "selfupdate", "crashreport", "crashpad", "crashhandler", "report",
    "easyanticheat", "battleye", "be_service",
)
# Typical Steam-game subfolders that hold the real executable when the root
# doesn't.
_EXE_SEARCH_SUBFOLDERS = (
    "bin", "binaries", "Binaries", "x64", "win64", "Win64",
    "x86", "win32", "Win32", "game", "Game", "Bin",
)


def find_main_exe(game_folder: Path) -> Path | None:
    """Best-effort guess of a game's main .exe under `game_folder`."""
    if not game_folder.exists():
        return None
    seen: set[Path] = set()
    candidates: list[Path] = []

    def collect_from(loc: Path) -> None:
        try:
            for entry in loc.iterdir():
                if not entry.is_file() or entry.suffix.lower() != ".exe":
                    continue
                name_lower = entry.stem.lower()
                if any(kw in name_lower for kw in _LAUNCHER_KEYWORDS):
                    continue
                try:
                    rp = entry.resolve()
                except OSError:
                    rp = entry
                if rp in seen:
                    continue
                seen.add(rp)
                candidates.append(entry)
        except OSError:
            return

    collect_from(game_folder)
    for sub in _EXE_SEARCH_SUBFOLDERS:
        p = game_folder / sub
        if p.exists():
            collect_from(p)
            for sub2 in _EXE_SEARCH_SUBFOLDERS:
                pp = p / sub2
                if pp.exists():
                    collect_from(pp)

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def game_root_for(dll_path: Path) -> Path:
    """Return the folder under `steamapps/common/<game>/` for any DLL inside the game tree."""
    parts = dll_path.parts
    lower = [p.lower() for p in parts]
    try:
        idx = lower.index("common")
        if idx + 1 < len(parts):
            return Path(*parts[: idx + 2])
    except ValueError:
        pass
    return dll_path.parent


# ----- Raw proxy-mode primitives -------------------------------------------

def _patch_proxy(game: Game) -> None:
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
        # Backup exists, so the current DLL is the SmokeAPI proxy from a
        # previous patch. Replace it with our bundled (possibly newer) version.
        try:
            dll.unlink()
        except OSError as e:
            raise PatchError(f"Could not remove existing {dll.name}: {e}") from e

    try:
        shutil.copy2(source, dll)
    except OSError as e:
        if backup.exists() and not dll.exists():
            try:
                backup.rename(dll)
            except OSError:
                pass
        raise PatchError(f"Could not copy SmokeAPI DLL: {e}") from e


def _revert_proxy(game: Game) -> None:
    dll = game.dll_path
    backup = game.backup_path
    if not backup.exists():
        return
    if dll.exists():
        try:
            dll.unlink()
        except OSError as e:
            raise PatchError(f"Could not remove SmokeAPI DLL: {e}") from e
    try:
        backup.rename(dll)
    except OSError as e:
        raise PatchError(f"Could not restore {backup.name} -> {dll.name}: {e}") from e


# ----- Raw hook-mode primitives --------------------------------------------

def _patch_hook(game: Game, hook_dll_name: str = HOOK_DEFAULT_NAME) -> None:
    if hook_dll_name not in HOOK_VALID_NAMES:
        raise PatchError(f"Invalid hook DLL name: {hook_dll_name}")
    if not game.exe_path:
        raise PatchError("Hook mode requires the game's main .exe path")
    exe = Path(game.exe_path)
    if not exe.exists():
        raise PatchError(f"Game .exe not found: {exe}")

    # Detect arch from the actual game DLL since the .exe alone doesn't always tell us.
    target = game.dll_path if game.dll_path.exists() else game.backup_path
    arch = detect_pe_arch(target) if target.exists() else detect_pe_arch(exe)
    if arch == ARCH_UNKNOWN:
        raise PatchError("Could not determine architecture for hook install")
    game.arch = arch

    source = smoke_source_dll(arch)
    if not source.exists():
        raise PatchError(f"Missing SmokeAPI DLL: {source}")

    target_dll = exe.parent / hook_dll_name
    try:
        shutil.copy2(source, target_dll)
    except OSError as e:
        raise PatchError(f"Could not write {target_dll}: {e}") from e

    game.hook_dll_name = hook_dll_name


def _revert_hook(game: Game) -> None:
    artifact = game.hook_artifact()
    if artifact is None or not artifact.exists():
        return
    try:
        artifact.unlink()
    except OSError as e:
        raise PatchError(f"Could not remove {artifact.name}: {e}") from e


# ----- Config deploy/cleanup -----------------------------------------------

def _config_target_for(game: Game, method: str) -> Path | None:
    """Where SmokeAPI.config.json belongs for the given method."""
    if method == METHOD_PROXY:
        return game.dll_path.with_name("SmokeAPI.config.json")
    if method == METHOD_HOOK and game.exe_path:
        return Path(game.exe_path).parent / "SmokeAPI.config.json"
    return None


def _cleanup_stale_config(game: Game) -> None:
    """Remove any SmokeAPI.config.json this game might have deployed previously,
    regardless of which method was used.
    """
    candidates = [
        game.dll_path.with_name("SmokeAPI.config.json"),
    ]
    if game.exe_path:
        candidates.append(Path(game.exe_path).parent / "SmokeAPI.config.json")
    for p in candidates:
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


# ----- High-level patch/revert ---------------------------------------------

def patch_game(
    game: Game,
    *,
    method: str = METHOD_PROXY,
    deploy_config: bool = False,
    hook_dll_name: str = HOOK_DEFAULT_NAME,
) -> None:
    """Apply SmokeAPI in `method` mode, switching cleanly if the game was
    previously patched with the other method. Also reconciles
    `SmokeAPI.config.json` with `deploy_config`.
    """
    if method not in (METHOD_PROXY, METHOD_HOOK):
        raise PatchError(f"Unknown patch method: {method}")

    # If switching methods, tear down the old one first so we don't end up
    # with both installed at once.
    if method == METHOD_PROXY and game.has_hook_patch():
        _revert_hook(game)
    if method == METHOD_HOOK and game.has_proxy_patch():
        _revert_proxy(game)

    # Wipe any stale config from previous patches (either folder) before we
    # decide whether to redeploy one in the new location.
    _cleanup_stale_config(game)

    if method == METHOD_PROXY:
        _patch_proxy(game)
    else:
        _patch_hook(game, hook_dll_name=hook_dll_name)

    game.patch_method = method

    if deploy_config and SMOKE_CONFIG.exists():
        target = _config_target_for(game, method)
        if target is not None:
            try:
                shutil.copy2(SMOKE_CONFIG, target)
                game.config_deployed = True
            except OSError as e:
                raise PatchError(f"Patched, but failed to deploy config: {e}") from e
    else:
        game.config_deployed = False


def revert_game(game: Game) -> None:
    """Revert whichever patch is currently installed (or recorded). Cleans up
    any deployed SmokeAPI.config.json too.
    """
    reverted_anything = False

    if game.has_proxy_patch() or game.backup_path.exists():
        _revert_proxy(game)
        reverted_anything = True

    if game.has_hook_patch():
        _revert_hook(game)
        reverted_anything = True

    _cleanup_stale_config(game)

    if not reverted_anything and game.dll_path.exists():
        # Already vanilla.
        game.patch_method = ""
        game.config_deployed = False
        return

    game.patch_method = ""
    game.config_deployed = False
    game.hook_dll_name = ""


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
    patch_method: str = METHOD_PROXY
    appearance_mode: str = "light"  # "light" or "dark"

    def to_dict(self) -> dict:
        return {
            "deploy_config": self.deploy_config,
            "patch_method": self.patch_method,
            "appearance_mode": self.appearance_mode,
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
                    appid=g.get("appid", ""),
                    patch_method=g.get("patch_method", ""),
                    exe_path=g.get("exe_path", ""),
                    hook_dll_name=g.get("hook_dll_name", ""),
                    config_deployed=bool(g.get("config_deployed", False)),
                ))
            except TypeError:
                continue
        method = data.get("patch_method", METHOD_PROXY)
        if method not in (METHOD_PROXY, METHOD_HOOK):
            method = METHOD_PROXY
        mode = data.get("appearance_mode", "light")
        if mode not in ("light", "dark"):
            mode = "light"
        return cls(
            games=games,
            deploy_config=bool(data.get("deploy_config", False)),
            patch_method=method,
            appearance_mode=mode,
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
