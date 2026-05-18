# AutoSmokeAPI

A GUI front-end for applying [SmokeAPI](https://github.com/acidicoala/SmokeAPI) (Steamworks DLC unlocker) in **proxy mode** to many games at once.

![screenshot](docs/screenshot.png)

## Download

Grab the latest **AutoSmokeAPI.zip** from the [releases page](https://github.com/Maxterino/AutoSmokeAPI/releases), unzip it anywhere, and double-click **AutoSmokeAPI.exe**.

No installation, no Python, no `pip` — everything is bundled.

## Features

- **Add games** by browsing for `steam_api.dll` / `steam_api64.dll`, drag-and-drop, or auto-scan
- **Auto-scan** every drive for Steam libraries — typically finishes in seconds with live progress in the activity log
- **Steam header image** shown next to each game (cached locally after first download)
- **Detects 32-bit vs 64-bit** from the DLL's PE header and picks the right SmokeAPI DLL
- **Bulk Patch / Revert** with confirmation prompts
- **Patch status** per game (PATCHED / UNPATCHED / MISSING)
- **Remove all** to clear the list in one click
- **Persistent game list** across sessions
- **Optional `SmokeAPI.config.json` deployment** with a hover tooltip explaining what it does
- **Activity log** for visibility into each operation
- **Update check** against the latest SmokeAPI GitHub release

## How patching works (proxy mode)

For each selected game:

- 64-bit game: `steam_api64.dll` → renamed to `steam_api64_o.dll`, then `smoke_api64.dll` is copied in as `steam_api64.dll`
- 32-bit game: `steam_api.dll`   → renamed to `steam_api_o.dll`,   then `smoke_api32.dll` is copied in as `steam_api.dll`

Revert restores the `_o` backup. If you opted in to "Deploy SmokeAPI.config.json", that file is dropped next to the DLL on patch and removed on revert.

Architecture is detected from the DLL's PE header (`Machine` field — `0x014c` x86, `0x8664` x64), not the filename.

## DLC updates

SmokeAPI detects DLCs dynamically from Steam's API at game launch — **you don't need to re-patch when new DLC releases.** Edge case: games with more than 64 unowned DLCs may need manual `extra_dlcs` entries in `SmokeAPI.config.json`, but this is rare.

## Antivirus / SmartScreen

PyInstaller-built executables sometimes trigger Windows SmartScreen ("unknown publisher") or antivirus heuristics. The exe is not signed (code-signing certificates cost money), so SmartScreen may show a warning the first time you run it — click **More info → Run anyway**.

If your antivirus quarantines `AutoSmokeAPI.exe`, you can:

1. Add an exception in your AV settings, **or**
2. Build the .exe yourself from source — `build.bat` does this in one step.

The source is short and auditable: ~3 files (`app.py`, `core.py`, `images.py`, `updates.py`).

## Build from source

Requires Python 3.10+. From the project folder:

```
build.bat
```

This installs the build dependencies, runs PyInstaller, and produces `dist\AutoSmokeAPI\` containing the standalone .exe + its support files. Zip that folder to distribute it.

## Caveats

- Run as Administrator if the game lives in `Program Files`.
- Close the game before patching/reverting — Windows won't let you replace a loaded DLL.
- AutoSmokeAPI only handles SmokeAPI's **proxy mode**. If a game refuses to load with proxy mode, check SmokeAPI's docs about hook mode + Koaloader.

## What NOT to commit to git

`.gitignore` already excludes the right things, but for reference, do **not** push:

- `.claude/` — Claude Code workspace metadata
- `__pycache__/`, `*.pyc` — Python bytecode caches
- `build/`, `dist/` — PyInstaller build artifacts
- `*.spec` is intentionally **included** (it's part of the build config)
- `autosmokeapi_state.json` — your local game list
- `.image_cache/` — downloaded Steam header thumbnails
- `.venv/`, `venv/`, `.vscode/`, `.idea/`

Credits:

- SmokeAPI itself by [acidicoala](https://github.com/acidicoala/SmokeAPI)
- GUI by [Maxterino](https://github.com/Maxterino/AutoSmokeAPI)
