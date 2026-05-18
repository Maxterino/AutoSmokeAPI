"""AutoSmokeAPI - a GUI to apply SmokeAPI (proxy mode) to many Steam games at once.

Theme: white background with lime green (#00ff64) accents.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

from core import (
    APP_DIR,
    ARCH_32,
    ARCH_64,
    ARCH_UNKNOWN,
    AppState,
    Game,
    HOOK_DEFAULT_NAME,
    METHOD_HOOK,
    METHOD_PROXY,
    PatchError,
    SMOKE_DLL_64,
    SMOKEAPI_DIR,
    STATUS_MISSING,
    STATUS_PATCHED,
    STATUS_UNPATCHED,
    detect_appid_for_dll,
    detect_game_name,
    detect_pe_arch,
    find_all_steamapps,
    find_main_exe,
    game_root_for,
    load_state,
    patch_game,
    revert_game,
    save_state,
    scan_for_steam_apis,
    steam_dll_kind,
)
import images
from updates import (
    get_installed_version,
    get_latest_release_download_url,
    install_release,
    is_outdated,
)

LOGO_DIR = APP_DIR / "logo"
LOGO_ICO = LOGO_DIR / "smokeapilogotransparanticon.ico"
LOGO_PNG = LOGO_DIR / "smokeapilogotransparant.png"

GUI_REPO_URL = "https://github.com/Maxterino/AutoSmokeAPI"
SMOKEAPI_REPO_URL = "https://github.com/acidicoala/SmokeAPI"

# Each color is (light_mode, dark_mode). CustomTkinter widgets that accept
# a tuple here will auto-switch when ctk.set_appearance_mode() changes.
COLOR_BG = ("#FFFFFF", "#0F1411")
COLOR_BG_ALT = ("#F5F7F5", "#1A201C")
COLOR_CARD = ("#FFFFFF", "#171E1A")
COLOR_CARD_HOVER = ("#F0FBF3", "#1F2924")
COLOR_BORDER = ("#E1E6E1", "#2A332C")
COLOR_TEXT = ("#0F1B12", "#E8EFE9")
COLOR_TEXT_DIM = ("#5B6B5F", "#9CA89F")
COLOR_TEXT_FAINT = ("#8A9590", "#6A766C")
COLOR_ACCENT = ("#00FF64", "#00FF64")
COLOR_ACCENT_DARK = ("#00C24C", "#00C24C")
COLOR_ACCENT_DARKER = ("#009C3D", "#00E058")
COLOR_DANGER = ("#E5484D", "#FF6B70")
COLOR_DANGER_HOVER = ("#C03A3F", "#E0484E")
COLOR_WARN = ("#F0A93B", "#F5B454")

# Soft tints used for status badges (dark variants stay readable but distinct).
COLOR_BADGE_ARCH_X64 = ("#E8FBEE", "#0F2A18")
COLOR_BADGE_ARCH_X64_TEXT = ("#009C3D", "#00FF64")
COLOR_BADGE_ARCH_X86 = ("#FFF5DC", "#3A2F12")
COLOR_BADGE_ARCH_X86_TEXT = ("#8A6A0F", "#F5B454")
COLOR_BADGE_UNPATCHED = ("#E8ECEA", "#22282A")
COLOR_BADGE_UNPATCHED_TEXT = ("#3D4A40", "#A0AAA3")
COLOR_BADGE_MISSING = ("#FCE7E7", "#3A1E20")
COLOR_BADGE_HOVER_DANGER = ("#FCE7E7", "#3A1E20")

FONT_FAMILY = "Segoe UI"


if DND_AVAILABLE:
    class _DndCTk(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.TkdndVersion = TkinterDnD._require(self)
    RootCls = _DndCTk
else:
    RootCls = ctk.CTk


class Tooltip:
    """Hover tooltip that appears after `delay_ms`, fading in/out smoothly."""

    # ~60 FPS animation steps (16 ms apart) totalling ~200 ms each way.
    _FADE_DURATION_MS = 200
    _FRAME_MS = 16
    _STEPS = max(1, _FADE_DURATION_MS // _FRAME_MS)

    def __init__(self, widget, text: str, delay_ms: int = 500, wraplength: int = 280):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._fade_after: str | None = None
        self._tip: tk.Toplevel | None = None
        self._closing = False
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel_open()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_open(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _cancel_fade(self):
        if self._fade_after is not None:
            try:
                self.widget.after_cancel(self._fade_after)
            except tk.TclError:
                pass
            self._fade_after = None

    def _show(self):
        if self._tip is not None or not self.widget.winfo_exists():
            return
        self._closing = False
        x = self.widget.winfo_rootx() + 24
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tip.configure(bg="#2A332C")
        try:
            tip.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        inner = tk.Frame(tip, bg="#1F2A22", padx=10, pady=8)
        inner.pack(padx=1, pady=1)
        tk.Label(
            inner, text=self.text, bg="#1F2A22", fg="#FFFFFF",
            font=(FONT_FAMILY, 10), wraplength=self.wraplength,
            justify="left",
        ).pack()
        self._fade(step=0, direction=1)

    def _fade(self, *, step: int, direction: int):
        """direction=1 fades in, direction=-1 fades out then destroys."""
        if self._tip is None:
            return
        alpha = step / self._STEPS if direction > 0 else 1.0 - step / self._STEPS
        alpha = max(0.0, min(1.0, alpha))
        try:
            self._tip.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if step < self._STEPS:
            self._fade_after = self.widget.after(
                self._FRAME_MS,
                lambda: self._fade(step=step + 1, direction=direction),
            )
        else:
            self._fade_after = None
            if direction < 0:
                # Fade-out finished, destroy the toplevel.
                try:
                    self._tip.destroy()
                except tk.TclError:
                    pass
                self._tip = None
                self._closing = False

    def _hide(self, _event=None):
        self._cancel_open()
        if self._tip is None or self._closing:
            return
        self._closing = True
        self._cancel_fade()
        self._fade(step=0, direction=-1)


class GameRow(ctk.CTkFrame):
    """One row in the games list."""

    def __init__(self, master, game: Game, on_remove, on_toggle, on_open_folder):
        super().__init__(
            master,
            fg_color=COLOR_CARD,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=10,
            height=96,
        )
        self.game = game
        self._on_remove = on_remove
        self._on_toggle = on_toggle
        self._on_open_folder = on_open_folder
        self.selected = tk.BooleanVar(value=True)
        self._thumb_image = None  # holds CTkImage to prevent GC
        self.pack_propagate(False)

        self._build()
        self._wire_card_click()
        self.refresh_status()
        self._load_thumbnail_async()

    def _toggle_selected(self, _event=None):
        self.selected.set(not self.selected.get())
        self._on_toggle()

    def _wire_card_click(self):
        """Make the whole card act as a click-to-toggle area, except for the
        interactive controls (checkbox / open-folder / remove buttons).
        """
        targets = [
            self, self.thumb_label,
            self.name_label, self.path_label,
            self.arch_badge, self.status_badge, self.method_chip,
        ]
        for w in targets:
            try:
                w.bind("<Button-1>", self._toggle_selected)
                w.configure(cursor="hand2")
            except (tk.TclError, ValueError):
                pass

    def _build(self):
        # Pack the fixed-width sides FIRST so the expanding middle doesn't
        # claim their requested width.
        cb = ctk.CTkCheckBox(
            self,
            text="",
            variable=self.selected,
            command=self._on_toggle,
            width=26,
            checkbox_width=22,
            checkbox_height=22,
            corner_radius=6,
            border_width=2,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_DARK,
            border_color=COLOR_BORDER,
            checkmark_color=COLOR_TEXT,
        )
        cb.pack(side="left", padx=(14, 6), pady=10)

        self.thumb_label = ctk.CTkLabel(
            self,
            text="",
            width=92,
            height=43,  # 92x43 keeps Steam's 460x215 aspect ratio
            fg_color=COLOR_BG_ALT,
            corner_radius=6,
        )
        self.thumb_label.pack(side="left", padx=(4, 4), pady=10)

        right = ctk.CTkFrame(self, fg_color="transparent", width=170)
        right.pack(side="right", padx=(4, 10), pady=8, fill="y")
        right.pack_propagate(False)

        text_frame = ctk.CTkFrame(self, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=10)

        self.name_label = ctk.CTkLabel(
            text_frame,
            text=self.game.name or "Unknown game",
            anchor="w",
            font=(FONT_FAMILY, 15, "bold"),
            text_color=COLOR_TEXT,
            justify="left",
        )
        self.name_label.pack(anchor="w", fill="x")

        self.path_label = ctk.CTkLabel(
            text_frame,
            text=self.game.path,
            anchor="w",
            font=(FONT_FAMILY, 11),
            text_color=COLOR_TEXT_DIM,
            justify="left",
        )
        self.path_label.pack(anchor="w", fill="x")

        badge_row = ctk.CTkFrame(right, fg_color="transparent")
        badge_row.pack(anchor="e")

        self.arch_badge = ctk.CTkLabel(
            badge_row,
            text="-",
            width=44,
            height=22,
            font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_BG_ALT,
            corner_radius=11,
        )
        self.arch_badge.pack(side="left", padx=(0, 6))

        self.status_badge = ctk.CTkLabel(
            badge_row,
            text="…",
            width=100,
            height=22,
            font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_BG_ALT,
            corner_radius=11,
        )
        self.status_badge.pack(side="left")

        # Small secondary line under the badges showing patch method + whether
        # SmokeAPI.config.json was deployed. Hidden when the game isn't patched.
        self.method_chip = ctk.CTkLabel(
            right,
            text="",
            font=(FONT_FAMILY, 9, "italic"),
            text_color=COLOR_TEXT_FAINT,
            anchor="e",
        )
        self.method_chip.pack(anchor="e", pady=(2, 0))

        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.pack(anchor="e", pady=(4, 0))

        open_btn = ctk.CTkButton(
            actions,
            text="Open folder",
            width=82,
            height=24,
            font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_BG_ALT,
            hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=8,
            command=self._on_open_folder,
        )
        open_btn.pack(side="left", padx=(0, 6))

        remove_btn = ctk.CTkButton(
            actions,
            text="✕",
            width=24,
            height=24,
            font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_DANGER,
            fg_color=COLOR_BG_ALT,
            hover_color=COLOR_BADGE_HOVER_DANGER,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=8,
            command=self._on_remove,
        )
        remove_btn.pack(side="left")

    def _load_thumbnail_async(self):
        if not PIL_AVAILABLE or not self.game.appid:
            return

        def on_ready(path: Path | None):
            if path is None or not self.winfo_exists():
                return
            try:
                self.thumb_label.after(0, lambda: self._apply_thumbnail(path))
            except tk.TclError:
                pass

        images.fetch_async(self.game.appid, on_ready)

    def _apply_thumbnail(self, path: Path):
        if not PIL_AVAILABLE or not path.exists() or not self.winfo_exists():
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((92, 43), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._thumb_image = ctk_img
            self.thumb_label.configure(image=ctk_img, text="")
        except (OSError, ValueError):
            pass

    def refresh_status(self):
        self.game.refresh()
        if self.game.arch == ARCH_64:
            self.arch_badge.configure(text="x64", fg_color=COLOR_BADGE_ARCH_X64, text_color=COLOR_BADGE_ARCH_X64_TEXT)
        elif self.game.arch == ARCH_32:
            self.arch_badge.configure(text="x86", fg_color=COLOR_BADGE_ARCH_X86, text_color=COLOR_BADGE_ARCH_X86_TEXT)
        else:
            self.arch_badge.configure(text="?", fg_color=COLOR_BG_ALT, text_color=COLOR_TEXT_DIM)

        status = self.game.status()
        if status == STATUS_PATCHED:
            self.status_badge.configure(text="PATCHED", fg_color=COLOR_ACCENT, text_color=("#0F1B12", "#0F1B12"))
        elif status == STATUS_UNPATCHED:
            self.status_badge.configure(text="UNPATCHED", fg_color=COLOR_BADGE_UNPATCHED, text_color=COLOR_BADGE_UNPATCHED_TEXT)
        elif status == STATUS_MISSING:
            self.status_badge.configure(text="MISSING", fg_color=COLOR_BADGE_MISSING, text_color=COLOR_DANGER)
        else:
            self.status_badge.configure(text="UNKNOWN", fg_color=COLOR_BADGE_UNPATCHED, text_color=COLOR_TEXT_DIM)

        # Method + config chip: only meaningful while patched.
        if status == STATUS_PATCHED and self.game.patch_method:
            chip = f"via {self.game.patch_method}"
            if self.game.config_deployed:
                chip += "  +  config"
            self.method_chip.configure(text=chip)
        else:
            self.method_chip.configure(text="")

        self.name_label.configure(text=self.game.name or "Unknown game")
        self.path_label.configure(text=self.game.path)


class App(RootCls):
    DEFAULT_WIDTH = 720
    DEFAULT_HEIGHT = 900
    MIN_WIDTH = 640
    MIN_HEIGHT = 720

    def __init__(self):
        super().__init__()

        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)

        self.state_obj: AppState = load_state()
        ctk.set_appearance_mode(self.state_obj.appearance_mode)

        self.title("AutoSmokeAPI")
        self.configure(fg_color=COLOR_BG)
        self._apply_window_geometry()
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)

        self._apply_window_icon()

        self.rows: dict[str, GameRow] = {}
        self.deploy_config_var = tk.BooleanVar(value=self.state_obj.deploy_config)
        self._scan_thread: threading.Thread | None = None
        self._scan_cancel = threading.Event()

        self._build_ui()

        for g in self.state_obj.games:
            self._add_game(g, persist=False)

        if DND_AVAILABLE:
            self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(400, self._check_updates_async)

        # Some Windows shells leave the window minimized when launched from a
        # non-foreground process; force it forward.
        self.after(50, self._raise_window)

    def _raise_window(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            # Topmost flip pops the window above others without pinning it.
            self.attributes("-topmost", True)
            self.after(150, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _apply_window_geometry(self):
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        w = min(self.DEFAULT_WIDTH, int(screen_w * 0.9))
        h = min(self.DEFAULT_HEIGHT, int(screen_h * 0.9))
        w = max(self.MIN_WIDTH, w)
        h = max(self.MIN_HEIGHT, h)
        # Nudge y up a bit to account for the taskbar.
        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 2 - 20)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _apply_window_icon(self):
        # AppUserModelID makes the taskbar group the window under our own
        # icon instead of Python's.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "Maxterino.AutoSmokeAPI.1"
                )
            except (OSError, AttributeError):
                pass

        if LOGO_ICO.exists():
            try:
                self.iconbitmap(str(LOGO_ICO))
            except tk.TclError:
                pass

    def _build_ui(self):
        self._build_header()
        underline = ctk.CTkFrame(self, height=2, fg_color=COLOR_ACCENT)
        underline.pack(side="top", fill="x", padx=24, pady=(8, 0))
        self._build_action_bar()
        self._build_options_row()
        # Footer + log first (side=bottom) so they reserve their height;
        # games then fills the middle via expand=True.
        self._build_footer()
        self._build_log_panel()
        self._build_games_section()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent", height=84)
        header.pack(side="top", fill="x", padx=24, pady=(20, 0))
        header.pack_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.grid_columnconfigure(2, weight=1)

        dot = ctk.CTkFrame(title_row, width=14, height=14, corner_radius=7, fg_color=COLOR_ACCENT)
        dot.grid(row=0, column=0, padx=(0, 10), pady=(6, 0))

        ctk.CTkLabel(
            title_row,
            text="AutoSmokeAPI",
            font=(FONT_FAMILY, 26, "bold"),
            text_color=COLOR_TEXT,
        ).grid(row=0, column=1, sticky="w")

        version_box = ctk.CTkFrame(title_row, fg_color="transparent")
        version_box.grid(row=0, column=3, sticky="ne", pady=(4, 0))

        self.version_label = ctk.CTkLabel(
            version_box,
            text="",
            font=(FONT_FAMILY, 11),
            text_color=COLOR_TEXT_FAINT,
            anchor="e",
        )
        self.version_label.pack(side="top", anchor="e")

        self.update_btn = ctk.CTkButton(
            version_box,
            text="Update available!",
            command=self._on_update_smokeapi,
            width=130,
            height=22,
            corner_radius=11,
            font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_DARK,
            border_width=0,
        )
        # Hidden until an update is detected.
        self._update_btn_url: str | None = None
        self._update_btn_tag: str | None = None

        ctk.CTkLabel(
            header,
            text="Apply SmokeAPI proxy-mode DLC unlocker to many Steam games at once.",
            font=(FONT_FAMILY, 12),
            text_color=COLOR_TEXT_DIM,
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _build_action_bar(self):
        # Layout: select | scan | <spacer> | patch | revert
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(side="top", fill="x", padx=24, pady=(14, 6))
        bar.grid_columnconfigure(2, weight=1)

        self.select_btn = self._make_button(
            bar, "+  Select game", self._on_select_game, primary=False
        )
        self.select_btn.configure(width=140)
        self.select_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.scan_btn = self._make_button(
            bar, "Auto-scan Steam", self._on_scan_steam, primary=False
        )
        self.scan_btn.configure(width=150)
        self.scan_btn.grid(row=0, column=1, padx=(0, 8), sticky="w")

        self.patch_btn = ctk.CTkButton(
            bar,
            text="Patch",
            command=self._on_patch,
            width=170,
            height=44,
            corner_radius=10,
            font=(FONT_FAMILY, 16, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_ACCENT,
            hover_color=COLOR_ACCENT_DARK,
            border_width=0,
        )
        self.patch_btn.grid(row=0, column=3, padx=(0, 8), sticky="e")

        self.revert_btn = ctk.CTkButton(
            bar,
            text="Revert",
            command=self._on_revert,
            width=120,
            height=44,
            corner_radius=10,
            font=(FONT_FAMILY, 13, "bold"),
            text_color=COLOR_DANGER,
            fg_color=COLOR_BG,
            hover_color=COLOR_BADGE_HOVER_DANGER,
            border_color=COLOR_DANGER,
            border_width=2,
        )
        self.revert_btn.grid(row=0, column=4, sticky="e")

    def _make_button(self, parent, text, command, primary=False):
        if primary:
            return ctk.CTkButton(
                parent, text=text, command=command,
                height=44, corner_radius=10,
                font=(FONT_FAMILY, 13, "bold"),
                text_color=COLOR_TEXT,
                fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_DARK,
                border_width=0,
            )
        return ctk.CTkButton(
            parent, text=text, command=command,
            height=44, corner_radius=10,
            font=(FONT_FAMILY, 12, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_BG, hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER, border_width=1,
        )

    def _build_options_row(self):
        # Row 1: list-selection helpers on the left, deploy-config on the right.
        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.pack(side="top", fill="x", padx=24, pady=(0, 4))
        row1.grid_columnconfigure(4, weight=1)

        self.select_all_btn = ctk.CTkButton(
            row1, text="Select all", width=82, height=28,
            command=lambda: self._set_all_selected(True),
            corner_radius=8, font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_TEXT_DIM,
            fg_color=COLOR_BG, hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER, border_width=1,
        )
        self.select_all_btn.grid(row=0, column=0, padx=(0, 6))

        self.select_none_btn = ctk.CTkButton(
            row1, text="Deselect all", width=92, height=28,
            command=lambda: self._set_all_selected(False),
            corner_radius=8, font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_TEXT_DIM,
            fg_color=COLOR_BG, hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER, border_width=1,
        )
        self.select_none_btn.grid(row=0, column=1, padx=(0, 6))

        self.refresh_btn = ctk.CTkButton(
            row1, text="Refresh", width=82, height=28,
            command=self._refresh_all,
            corner_radius=8, font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_TEXT_DIM,
            fg_color=COLOR_BG, hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER, border_width=1,
        )
        self.refresh_btn.grid(row=0, column=2, padx=(0, 6))

        self.remove_all_btn = ctk.CTkButton(
            row1, text="Remove all", width=92, height=28,
            command=self._on_remove_all,
            corner_radius=8, font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_DANGER,
            fg_color=COLOR_BG, hover_color=COLOR_BADGE_HOVER_DANGER,
            border_color=COLOR_BORDER, border_width=1,
        )
        self.remove_all_btn.grid(row=0, column=3, sticky="w")

        # Row 2: patch method + deploy-config (both affect the next Patch click).
        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(side="top", fill="x", padx=24, pady=(0, 8))
        row2.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            row2, text="Method:",
            font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_TEXT_DIM,
        ).grid(row=0, column=0, padx=(0, 6))

        self.method_var = tk.StringVar(value=self.state_obj.patch_method.capitalize() or "Proxy")
        self.method_menu = ctk.CTkOptionMenu(
            row2,
            values=["Proxy", "Hook"],
            variable=self.method_var,
            command=self._on_method_change,
            width=100, height=28,
            corner_radius=8,
            font=(FONT_FAMILY, 11, "bold"),
            text_color=COLOR_TEXT,
            fg_color=COLOR_BG,
            button_color=COLOR_ACCENT,
            button_hover_color=COLOR_ACCENT_DARK,
            dropdown_fg_color=COLOR_BG,
            dropdown_hover_color=COLOR_CARD_HOVER,
            dropdown_text_color=COLOR_TEXT,
        )
        self.method_menu.grid(row=0, column=1, padx=(0, 8))

        self.method_hint = ctk.CTkLabel(
            row2,
            text="Didn't work? Try Hook mode",
            font=(FONT_FAMILY, 10, "italic"),
            text_color=COLOR_TEXT_FAINT,
        )
        self.method_hint.grid(row=0, column=2, sticky="w")

        method_tooltip = (
            "Proxy mode (default): SmokeAPI replaces steam_api(64).dll directly.\n"
            "Works for most games whose steam_api lives next to the game .exe.\n\n"
            "Hook mode (Self-Hook): drops SmokeAPI as version.dll next to the\n"
            "game's main .exe. Try this if proxy mode doesn't unlock DLCs - "
            "common for games where steam_api.dll is in a deep subfolder.\n\n"
            "You can switch methods anytime. Re-patching will tear down the\n"
            "old install and apply the new one cleanly."
        )
        Tooltip(self.method_menu, method_tooltip, delay_ms=500, wraplength=340)
        Tooltip(self.method_hint, method_tooltip, delay_ms=500, wraplength=340)

        cfg_holder = ctk.CTkFrame(row2, fg_color="transparent")
        cfg_holder.grid(row=0, column=4, sticky="e")

        cfg_cb = ctk.CTkCheckBox(
            cfg_holder,
            text="Deploy SmokeAPI.config.json",
            variable=self.deploy_config_var,
            command=self._on_toggle_config,
            checkbox_width=18, checkbox_height=18,
            corner_radius=4, border_width=2,
            font=(FONT_FAMILY, 11),
            text_color=COLOR_TEXT_DIM,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_DARK,
            border_color=COLOR_BORDER, checkmark_color=COLOR_TEXT,
        )
        cfg_cb.pack(side="left")

        warn_label = ctk.CTkLabel(
            cfg_holder,
            text="May impact performance",
            font=(FONT_FAMILY, 9, "italic"),
            text_color=COLOR_WARN,
        )
        warn_label.pack(side="left", padx=(6, 0))

        config_tooltip = (
            "SmokeAPI.config.json controls how SmokeAPI behaves:\n\n"
            "• logging - writes debug logs (off by default for performance).\n"
            "• default_app_status - 'unlocked' (default), 'locked', or 'original'.\n"
            "• override_app_status / override_dlc_status - opt specific DLCs in or out.\n"
            "• auto_inject_inventory - injects inventory items the game queries for.\n"
            "• extra_dlcs - manually add DLC IDs for games whose API capped the response.\n\n"
            "Most users do NOT need this file. Only enable if you have a specific reason "
            "(e.g. troubleshooting, or a game with a hardcoded DLC list)."
        )
        Tooltip(cfg_cb, config_tooltip, delay_ms=500, wraplength=320)
        Tooltip(warn_label, config_tooltip, delay_ms=500, wraplength=320)

    def _build_games_section(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(side="top", fill="both", expand=True, padx=24, pady=(8, 6))
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)

        title_row = ctk.CTkFrame(wrap, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        title_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            title_row,
            text="Games",
            font=(FONT_FAMILY, 14, "bold"),
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")

        self.count_label = ctk.CTkLabel(
            title_row,
            text="0 added",
            font=(FONT_FAMILY, 11),
            text_color=COLOR_TEXT_FAINT,
        )
        self.count_label.grid(row=0, column=2, sticky="e")

        outer = ctk.CTkFrame(
            wrap,
            fg_color=COLOR_BG_ALT,
            border_color=COLOR_BORDER,
            border_width=1,
            corner_radius=12,
        )
        outer.grid(row=1, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        self.list_frame = ctk.CTkScrollableFrame(
            outer,
            fg_color="transparent",
            scrollbar_button_color=COLOR_ACCENT_DARK,
            scrollbar_button_hover_color=COLOR_ACCENT_DARKER,
        )
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        self.empty_label = ctk.CTkLabel(
            self.list_frame,
            text=(
                "No games added yet.\n\n"
                "Click  +  Select game  or  Auto-scan Steam\n"
                + ("…or drag steam_api(64).dll files onto this window."
                   if DND_AVAILABLE else "")
            ),
            font=(FONT_FAMILY, 13),
            text_color=COLOR_TEXT_FAINT,
            justify="center",
        )
        self.empty_label.pack(pady=80)

    def _build_log_panel(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(side="bottom", fill="x", padx=24, pady=(6, 4))
        wrap.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(wrap, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            head, text="Activity",
            font=(FONT_FAMILY, 13, "bold"),
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w")

        clear_btn = ctk.CTkButton(
            head, text="Clear", width=56, height=22,
            command=self._clear_log,
            corner_radius=6,
            font=(FONT_FAMILY, 10, "bold"),
            text_color=COLOR_TEXT_DIM,
            fg_color=COLOR_BG, hover_color=COLOR_CARD_HOVER,
            border_color=COLOR_BORDER, border_width=1,
        )
        clear_btn.grid(row=0, column=1, sticky="e")

        log_outer = ctk.CTkFrame(
            wrap, fg_color=COLOR_BG_ALT,
            border_color=COLOR_BORDER, border_width=1,
            corner_radius=10,
            height=160,
        )
        log_outer.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        log_outer.grid_propagate(False)
        log_outer.grid_columnconfigure(0, weight=1)
        log_outer.grid_rowconfigure(0, weight=1)

        self.log_text = ctk.CTkTextbox(
            log_outer,
            fg_color=COLOR_BG_ALT,
            text_color=COLOR_TEXT,
            font=("Consolas", 10),
            wrap="word",
            border_width=0,
            scrollbar_button_color=COLOR_ACCENT_DARK,
            scrollbar_button_hover_color=COLOR_ACCENT_DARKER,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.log_text.configure(state="disabled")

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent", height=36)
        footer.pack(side="bottom", fill="x", padx=24, pady=(2, 14))
        footer.pack_propagate(False)
        footer.grid_columnconfigure(2, weight=1)

        # Left side: sun / moon theme toggles, then the status text.
        self.theme_box = ctk.CTkFrame(footer, fg_color="transparent")
        self.theme_box.grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.sun_btn = ctk.CTkButton(
            self.theme_box,
            text="☀",
            width=26, height=26, corner_radius=6,
            font=(FONT_FAMILY, 14),
            text_color=COLOR_TEXT,
            fg_color="transparent",
            hover_color=COLOR_CARD_HOVER,
            border_width=0,
            command=lambda: self._set_appearance("light"),
        )
        self.sun_btn.pack(side="left")

        self.moon_btn = ctk.CTkButton(
            self.theme_box,
            text="🌙",
            width=26, height=26, corner_radius=6,
            font=(FONT_FAMILY, 13),
            text_color=COLOR_TEXT,
            fg_color="transparent",
            hover_color=COLOR_CARD_HOVER,
            border_width=0,
            command=lambda: self._set_appearance("dark"),
        )
        self.moon_btn.pack(side="left", padx=(2, 0))

        self.status_label = ctk.CTkLabel(
            footer,
            text="Ready",
            anchor="w",
            font=(FONT_FAMILY, 11),
            text_color=COLOR_TEXT_DIM,
        )
        self.status_label.grid(row=0, column=1, sticky="w")

        links_box = ctk.CTkFrame(footer, fg_color="transparent")
        links_box.grid(row=0, column=3, sticky="e")

        gui_link = ctk.CTkLabel(
            links_box,
            text="GUI by Maxterino",
            font=(FONT_FAMILY, 11, "underline"),
            text_color=COLOR_ACCENT_DARKER,
            cursor="hand2",
        )
        gui_link.pack(side="left", padx=(0, 12))
        gui_link.bind("<Button-1>", lambda _e: webbrowser.open(GUI_REPO_URL))

        smoke_link = ctk.CTkLabel(
            links_box,
            text="SmokeAPI by acidicoala",
            font=(FONT_FAMILY, 11, "underline"),
            text_color=COLOR_ACCENT_DARKER,
            cursor="hand2",
        )
        smoke_link.pack(side="left")
        smoke_link.bind("<Button-1>", lambda _e: webbrowser.open(SMOKEAPI_REPO_URL))

        self._refresh_theme_toggle()

    def _hide_empty(self):
        if self.empty_label is not None and self.empty_label.winfo_exists():
            self.empty_label.pack_forget()

    def _show_empty(self):
        if not self.rows:
            self.empty_label.pack(pady=80)

    def _add_game(self, game: Game, *, persist: bool = True) -> bool:
        """Add a game to the list. Returns True if added, False if duplicate."""
        key = str(Path(game.path).resolve()) if Path(game.path).exists() else game.path
        if key in self.rows:
            return False

        # Normalize backup _o.dll paths back to the canonical filename so an
        # already-patched game shows under its real name in the list.
        p = Path(game.path)
        if p.name.lower() in ("steam_api64_o.dll", "steam_api_o.dll"):
            canonical = p.with_name("steam_api64.dll" if "64" in p.name.lower() else "steam_api.dll")
            game.path = str(canonical)
            key = str(canonical.resolve()) if canonical.exists() else str(canonical)
            if key in self.rows:
                return False

        if not game.name:
            game.name = detect_game_name(Path(game.path))
        if game.arch == ARCH_UNKNOWN:
            target = Path(game.path)
            if not target.exists() and Path(game.path).with_name(
                "steam_api64_o.dll" if "64" in Path(game.path).name.lower() else "steam_api_o.dll"
            ).exists():
                target = Path(game.path).with_name(
                    "steam_api64_o.dll" if "64" in Path(game.path).name.lower() else "steam_api_o.dll"
                )
            if target.exists():
                game.arch = detect_pe_arch(target)

        self._hide_empty()
        row = GameRow(
            self.list_frame,
            game,
            on_remove=lambda k=key: self._remove_game(k),
            on_toggle=self._update_count,
            on_open_folder=lambda g=game: self._open_folder(g),
        )
        row.pack(fill="x", padx=2, pady=4)
        self.rows[key] = row

        if persist:
            self.state_obj.games.append(game)
            self._save()

        self._update_count()
        return True

    def _remove_game(self, key: str):
        row = self.rows.pop(key, None)
        if row is None:
            return
        path = row.game.path
        row.destroy()
        self.state_obj.games = [g for g in self.state_obj.games if g.path != path]
        self._save()
        self._update_count()
        self._show_empty()
        self.log(f"Removed: {path}")

    def _update_count(self):
        total = len(self.rows)
        selected = sum(1 for r in self.rows.values() if r.selected.get())
        self.count_label.configure(text=f"{selected} selected · {total} added")

    def _set_all_selected(self, value: bool):
        for r in self.rows.values():
            r.selected.set(value)
        self._update_count()

    def _on_remove_all(self):
        if not self.rows:
            return
        n = len(self.rows)
        if not messagebox.askyesno(
            "Remove all games?",
            f"Remove all {n} game(s) from the list?\n\n"
            "This only clears them from this list - it does NOT revert any patches.\n"
            "Use 'Revert' first if you want to undo SmokeAPI on patched games.",
            parent=self,
        ):
            return
        for key in list(self.rows.keys()):
            row = self.rows.pop(key)
            row.destroy()
        self.state_obj.games.clear()
        self._save()
        self._update_count()
        self._show_empty()
        self.log(f"Removed all {n} game(s) from list.")

    def _refresh_all(self):
        for r in self.rows.values():
            r.refresh_status()
        self.log("Status refreshed for all games.")

    def _open_folder(self, game: Game):
        folder = game.folder
        if folder.exists():
            import os
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            self.log(f"Folder not found: {folder}")

    def _on_select_game(self):
        paths = filedialog.askopenfilenames(
            title="Select steam_api.dll or steam_api64.dll",
            filetypes=[("Steamworks DLL", "steam_api*.dll"), ("DLL files", "*.dll"), ("All files", "*.*")],
        )
        if not paths:
            return
        added = 0
        skipped: list[str] = []
        for p in paths:
            ok, reason = self._add_path_if_valid(p)
            if ok:
                added += 1
            elif reason:
                skipped.append(f"{Path(p).name}: {reason}")
        if added:
            self.log(f"Added {added} game(s).")
        for line in skipped:
            self.log(f"Skipped - {line}")

    def _add_path_if_valid(self, path_str: str) -> tuple[bool, str | None]:
        p = Path(path_str)
        if not p.exists():
            return False, "file not found"
        name = p.name.lower()
        if name in ("steam_api64_o.dll", "steam_api_o.dll"):
            # User picked the backup; track the canonical filename instead.
            canonical = p.with_name("steam_api64.dll" if "64" in name else "steam_api.dll")
            game = Game(path=str(canonical))
        elif steam_dll_kind(p) is None:
            return False, "not a steam_api dll"
        else:
            game = Game(path=str(p))
        if self._add_game(game):
            return True, None
        return False, "already added"

    def _on_drop(self, event):
        # tkinterdnd2 hands us a single string with paths separated by spaces;
        # paths containing spaces are wrapped in {curly braces}.
        raw = event.data
        paths = self._parse_dnd_data(raw)
        added = 0
        for p in paths:
            ok, _ = self._add_path_if_valid(p)
            if ok:
                added += 1
        if added:
            self.log(f"Dropped: added {added} game(s).")

    @staticmethod
    def _parse_dnd_data(data: str) -> list[str]:
        out: list[str] = []
        i, n = 0, len(data)
        cur: list[str] = []
        in_brace = False
        while i < n:
            c = data[i]
            if c == "{":
                in_brace = True
            elif c == "}":
                in_brace = False
                out.append("".join(cur).strip())
                cur = []
            elif c == " " and not in_brace:
                if cur:
                    out.append("".join(cur).strip())
                    cur = []
            else:
                cur.append(c)
            i += 1
        if cur:
            out.append("".join(cur).strip())
        return [x for x in out if x]

    def _on_scan_steam(self):
        if self._scan_thread is not None and self._scan_thread.is_alive():
            # Second click on the button cancels the running scan.
            self._scan_cancel.set()
            self.scan_btn.configure(text="Cancelling…", state="disabled")
            return

        self._scan_cancel.clear()
        self.scan_btn.configure(text="Cancel scan")
        self._set_status("Scanning Steam libraries…")
        self.log("Starting auto-scan…")

        def progress(msg: str):
            self._log_async(msg)

        def work():
            libraries = find_all_steamapps()
            if not libraries:
                self.after(0, lambda: self._scan_finished([], False))
                return
            self._log_async(f"Found {len(libraries)} library folder(s).")
            results = scan_for_steam_apis(
                libraries,
                on_progress=progress,
                cancel_check=self._scan_cancel.is_set,
            )
            self.after(0, lambda: self._scan_finished(results, True))

        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

    def _scan_finished(self, results, steam_found: bool):
        self.scan_btn.configure(state="normal", text="Auto-scan Steam")
        self._scan_thread = None
        if not steam_found:
            self._set_status("No Steam libraries found.")
            messagebox.showwarning(
                "Steam not found",
                "Couldn't locate any Steam library folders. Add games manually via\n"
                "'+ Select game' or drag-and-drop instead.",
                parent=self,
            )
            return

        if self._scan_cancel.is_set():
            self._set_status("Scan cancelled.")
            return

        added = 0
        for entry in results:
            if isinstance(entry, tuple):
                path, appid, name = entry
            else:
                path, appid, name = entry, "", ""
            g = Game(path=str(path), name=name, appid=appid or "")
            ok = self._add_game(g)
            if ok:
                added += 1
        self._set_status(f"Scan complete - {added} new game(s) added, {len(results)} total found.")
        self.log(f"Auto-scan: {added} added, {len(results)} discovered.")

    def _on_patch(self):
        selected = [r for r in self.rows.values() if r.selected.get()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one game to patch.", parent=self)
            return
        names = "\n".join(f"  • {r.game.name}" for r in selected[:6])
        more = f"\n  …and {len(selected) - 6} more" if len(selected) > 6 else ""
        if not messagebox.askyesno(
            "Confirm patch",
            f"Apply SmokeAPI to {len(selected)} game(s)?\n\n{names}{more}\n\n"
            "The original Steamworks DLL will be renamed to *_o.dll.",
            parent=self,
        ):
            return
        self._run_bulk(selected, "patch")

    def _on_revert(self):
        selected = [r for r in self.rows.values() if r.selected.get()]
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one game to revert.", parent=self)
            return
        names = "\n".join(f"  • {r.game.name}" for r in selected[:6])
        more = f"\n  …and {len(selected) - 6} more" if len(selected) > 6 else ""
        if not messagebox.askyesno(
            "Confirm revert",
            f"Revert SmokeAPI on {len(selected)} game(s)?\n\n{names}{more}\n\n"
            "The original Steamworks DLL will be restored from *_o.dll.",
            parent=self,
        ):
            return
        self._run_bulk(selected, "revert")

    def _run_bulk(self, rows: list[GameRow], action: str):
        deploy_config = self.deploy_config_var.get() and action == "patch"
        method = self._current_method()

        # For hook-mode patches we need to know each game's .exe up front. Try
        # auto-detect; for failures, prompt the user. Skipping a game just drops
        # it from this batch.
        if action == "patch" and method == METHOD_HOOK:
            rows = self._resolve_exe_paths(rows)
            if not rows:
                self.log("Hook patch cancelled - no games with a known .exe.")
                return

        self._set_busy(True)
        self._set_status(f"{'Patching' if action == 'patch' else 'Reverting'} {len(rows)} game(s)…")

        def work():
            ok, fail = 0, 0
            for row in rows:
                try:
                    if action == "patch":
                        patch_game(
                            row.game,
                            method=method,
                            deploy_config=deploy_config,
                        )
                        suffix = f" via {method}"
                        if deploy_config:
                            suffix += " + config"
                        self._log_async(f"Patched: {row.game.name}{suffix}")
                    else:
                        revert_game(row.game)
                        self._log_async(f"Reverted: {row.game.name}")
                    ok += 1
                except PatchError as e:
                    fail += 1
                    self._log_async(f"ERROR ({row.game.name}): {e}")
                except Exception as e:  # noqa: BLE001
                    fail += 1
                    self._log_async(f"UNEXPECTED ({row.game.name}): {e}")
                self.after(0, row.refresh_status)
            self.after(0, lambda: self._bulk_finished(action, ok, fail))

        threading.Thread(target=work, daemon=True).start()

    def _current_method(self) -> str:
        return METHOD_HOOK if self.method_var.get().lower() == "hook" else METHOD_PROXY

    def _resolve_exe_paths(self, rows: list[GameRow]) -> list[GameRow]:
        """For each row, make sure game.exe_path is set (auto-detect, then ask).
        Returns the rows the user agreed to continue with.
        """
        kept: list[GameRow] = []
        for row in rows:
            game = row.game
            if game.exe_path and Path(game.exe_path).exists():
                kept.append(row)
                continue
            guess = find_main_exe(game_root_for(game.dll_path))
            if guess is not None:
                game.exe_path = str(guess)
                self.log(f"{game.name}: detected .exe = {guess.name}")
                kept.append(row)
                continue
            # Ask the user to pick it.
            picked = filedialog.askopenfilename(
                title=f"Pick the main .exe for: {game.name}",
                initialdir=str(game_root_for(game.dll_path)),
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
                parent=self,
            )
            if not picked:
                self.log(f"Skipped (no .exe selected): {game.name}")
                continue
            game.exe_path = picked
            kept.append(row)
        self._save()
        return kept

    def _bulk_finished(self, action: str, ok: int, fail: int):
        self._set_busy(False)
        verb = "Patched" if action == "patch" else "Reverted"
        msg = f"{verb} {ok} game(s)"
        if fail:
            msg += f" - {fail} failed (see log)"
        self._set_status(msg)
        self._save()

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for w in (self.patch_btn, self.revert_btn, self.select_btn, self.scan_btn, self.refresh_btn):
            w.configure(state=state)

    def _on_toggle_config(self):
        self.state_obj.deploy_config = self.deploy_config_var.get()
        self._save()

    def _on_method_change(self, _choice: str = ""):
        self.state_obj.patch_method = self._current_method()
        self._save()
        # The hint text now points to the *other* mode.
        if self._current_method() == METHOD_HOOK:
            self.method_hint.configure(text="Didn't work? Try Proxy mode")
        else:
            self.method_hint.configure(text="Didn't work? Try Hook mode")

    def _set_appearance(self, mode: str):
        if mode not in ("light", "dark"):
            return
        ctk.set_appearance_mode(mode)
        self.state_obj.appearance_mode = mode
        self._save()
        self._refresh_theme_toggle()

    def _refresh_theme_toggle(self):
        """Highlight the active theme button with a lime-green outline."""
        active_lime = COLOR_ACCENT
        empty = ("#E1E6E1", "#2A332C")  # subtle outline for the inactive button
        if self.state_obj.appearance_mode == "dark":
            self.sun_btn.configure(border_width=1, border_color=empty)
            self.moon_btn.configure(border_width=2, border_color=active_lime)
        else:
            self.sun_btn.configure(border_width=2, border_color=active_lime)
            self.moon_btn.configure(border_width=1, border_color=empty)

    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _log_async(self, message: str):
        self.after(0, lambda: self.log(message))

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    def _check_updates_async(self):
        self._installed_version = get_installed_version(SMOKE_DLL_64)
        if self._installed_version:
            self.version_label.configure(text=f"SmokeAPI {self._installed_version}")

        def work():
            info = get_latest_release_download_url()
            self.after(0, lambda: self._update_check_done(self._installed_version, info))

        threading.Thread(target=work, daemon=True).start()

    def _update_check_done(self, installed: str | None, info):
        if not info:
            base = installed or "?"
            self.version_label.configure(text=f"SmokeAPI {base}")
            return
        tag, zip_url, html_url = info
        if installed and is_outdated(installed, tag):
            self.version_label.configure(
                text=f"SmokeAPI {installed} → {tag}",
                text_color=COLOR_ACCENT_DARKER,
            )
            self._update_btn_url = zip_url
            self._update_btn_tag = tag
            self.update_btn.pack(side="top", anchor="e", pady=(4, 0))
            self.log(f"SmokeAPI update available: {tag} (installed {installed})")
        else:
            base = installed or "?"
            self.version_label.configure(text=f"SmokeAPI {base} (latest)")
            try:
                self.update_btn.pack_forget()
            except tk.TclError:
                pass

    def _on_update_smokeapi(self):
        if not self._update_btn_url or not self._update_btn_tag:
            return
        tag = self._update_btn_tag
        url = self._update_btn_url
        if not messagebox.askyesno(
            "Update SmokeAPI?",
            f"Download SmokeAPI {tag} and replace the bundled DLLs?\n\n"
            "Your existing patched games will keep working - they don't need\n"
            "to be re-patched unless you want them on the newer SmokeAPI.\n\n"
            "The previous DLLs are backed up as smoke_api*.dll.bak so you can\n"
            "roll back manually if needed.",
            parent=self,
        ):
            return

        self.update_btn.configure(state="disabled", text="Updating…")
        self._set_status(f"Downloading SmokeAPI {tag}…")
        self.log(f"Updating SmokeAPI to {tag}")

        def progress(msg: str):
            self._log_async(msg)

        def work():
            ok, msg = install_release(url, SMOKEAPI_DIR, on_progress=progress)
            self.after(0, lambda: self._update_finished(ok, msg, tag))

        threading.Thread(target=work, daemon=True).start()

    def _update_finished(self, ok: bool, msg: str, tag: str):
        self.update_btn.configure(state="normal", text="Update available!")
        if ok:
            self.log(f"Update OK: {msg}")
            self._set_status(f"SmokeAPI updated to {tag}.")
            new_version = get_installed_version(SMOKE_DLL_64)
            self._installed_version = new_version
            if new_version:
                self.version_label.configure(
                    text=f"SmokeAPI {new_version} (latest)",
                    text_color=COLOR_TEXT_FAINT,
                )
            self.update_btn.pack_forget()
            for r in self.rows.values():
                r.refresh_status()
            messagebox.showinfo(
                "Update complete",
                f"SmokeAPI updated to {tag}.\n\n"
                "Newly patched games will use the new version automatically.\n"
                "Already-patched games keep their currently installed DLL - "
                "if you want them on the new version, click Revert then Patch.",
                parent=self,
            )
        else:
            self.log(f"Update FAILED: {msg}")
            self._set_status("Update failed - see Activity log.")
            messagebox.showerror(
                "Update failed",
                f"Couldn't update SmokeAPI:\n\n{msg}\n\n"
                "Your existing SmokeAPI installation was not changed.",
                parent=self,
            )

    def _save(self):
        save_state(self.state_obj)

    def _on_close(self):
        self._save()
        self.destroy()


def main():
    if not SMOKEAPI_DIR.exists():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing SmokeAPI folder",
            f"Could not find SmokeAPI files at:\n{SMOKEAPI_DIR}\n\n"
            "The 'SmokeAPI' folder must sit next to AutoSmokeAPI.exe / app.py "
            "with smoke_api32.dll and smoke_api64.dll inside.",
        )
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
