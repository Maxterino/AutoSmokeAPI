# Releasing AutoSmokeAPI on GitHub

Step-by-step for cutting a new release that shows up under the **Releases** tab on the repo. Everything below is one-time setup the first run, then a quick repeat each new version.

## 0. One-time setup — push the repo

If you haven't pushed the repo yet:

```bash
cd "d:\SteamLibrary\steamapps\common\AutoSmokeAPI"
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/Maxterino/AutoSmokeAPI.git
git push -u origin main
```

`.gitignore` is already set up so `__pycache__/`, `.claude/`, `build/`, `dist/`, `.image_cache/`, `autosmokeapi_state.json`, and the release `.zip` won't get pushed.

## 1. Build a fresh .exe for the release

```
build.bat
```

This produces `dist\AutoSmokeAPI\AutoSmokeAPI.exe` (plus its `_internal\` support folder).

## 2. Zip the dist folder

In PowerShell from the project root:

```powershell
Compress-Archive -Path dist\AutoSmokeAPI -DestinationPath AutoSmokeAPI-v1.0.0.zip -CompressionLevel Optimal
```

Or just right-click `dist\AutoSmokeAPI\` in Explorer → **Send to → Compressed (zipped) folder** → rename it to `AutoSmokeAPI-v1.0.0.zip`.

Bump `v1.0.0` to whatever you're releasing (`v1.0.1`, `v1.1.0`, etc.). Use [semantic versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

## 3. Tag the commit

A GitHub release is anchored to a git tag. Create one and push it:

```bash
git tag -a v1.0.0 -m "AutoSmokeAPI v1.0.0"
git push origin v1.0.0
```

The `-a` flag makes it an "annotated" tag (the kind GitHub wants for releases).

## 4. Create the release on GitHub

1. Open your repo on GitHub: <https://github.com/Maxterino/AutoSmokeAPI>
2. Click **Releases** in the right sidebar (or go to `…/releases`)
3. Click **Draft a new release**
4. **Choose a tag**: pick the `v1.0.0` tag you just pushed
5. **Release title**: `AutoSmokeAPI v1.0.0` (or something nicer like `v1.0.0 — Initial release`)
6. **Describe this release**: paste the changelog (template below)
7. **Attach binaries**: drag `AutoSmokeAPI-v1.0.0.zip` into the "Attach binaries" box
8. Leave **Set as a pre-release** unchecked (or check it if it's a beta)
9. Leave **Set as the latest release** checked
10. Click **Publish release**

Done. The zip will now appear under `…/releases/latest` and users can download it.

## 5. Release notes template

```markdown
## What's new in v1.0.0

- Initial public release
- One-click SmokeAPI patching for many games at once
- Auto-scan finds every game with a Steamworks DLL on every drive
- Drag-and-drop or browse for individual `steam_api(64).dll` files
- Steam header art shown next to each game
- Detects 32-bit vs 64-bit from PE header
- Built-in SmokeAPI update check — click "Update available!" to fetch the latest from acidicoala's repo

## Install

1. Download `AutoSmokeAPI-v1.0.0.zip` below
2. Unzip anywhere (e.g. Desktop)
3. Double-click `AutoSmokeAPI.exe`

No Python, no installation. Windows SmartScreen may show "unknown publisher" on first run — click **More info → Run anyway**.

## Credits

- SmokeAPI by [acidicoala](https://github.com/acidicoala/SmokeAPI)
- GUI by [Maxterino](https://github.com/Maxterino/AutoSmokeAPI)
```

## 6. Future releases

For every subsequent release:

```bash
# 1. Bump the version in version_info.txt (filevers + ProductVersion + FileVersion strings)
# 2. Rebuild
build.bat

# 3. Zip
Compress-Archive -Path dist\AutoSmokeAPI -DestinationPath AutoSmokeAPI-v1.0.1.zip -CompressionLevel Optimal

# 4. Tag + push
git commit -am "Release v1.0.1"
git tag -a v1.0.1 -m "AutoSmokeAPI v1.0.1"
git push && git push origin v1.0.1

# 5. On GitHub: Draft a new release → tag v1.0.1 → upload the zip → Publish
```

## Tips

- The first time a user downloads your .exe, Windows SmartScreen will show "Windows protected your PC" because the binary isn't code-signed. They click **More info → Run anyway**. To make this disappear permanently you'd need an EV code signing certificate (~$200-400/year), which probably isn't worth it for a free tool.
- If a release zip turns out broken, you can edit the release on GitHub (delete the old asset, upload a new one). The download URL stays the same since it's based on the asset filename.
- Don't delete old releases — older users may still be downloading them. Just publish a new one and GitHub will surface it as "latest".
