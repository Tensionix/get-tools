from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import asyncio
import atexit
import ctypes
import html
import importlib
import inspect
import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nicegui import app as nicegui_app, run, ui  # type: ignore

AUDION_CANONICAL_TOOLTIP_DELAY_MS = 1500
AUDION_CANONICAL_TOOLTIP_HIDE_DELAY_MS = 100
AUDION_CANONICAL_TOOLTIP_TRANSITION_MS = 100


def install_audion_canonical_tooltip_defaults() -> None:
    try:
        from nicegui.elements.tooltip import Tooltip as NiceGuiTooltip  # type: ignore
    except Exception:
        return
    if getattr(NiceGuiTooltip, "_audion_canonical_tooltip_defaults", False):
        return
    original_init = NiceGuiTooltip.__init__

    def audion_tooltip_init(self: Any, text: str = "") -> None:
        original_init(self, text)
        self.props["delay"] = AUDION_CANONICAL_TOOLTIP_DELAY_MS
        self.props["hide-delay"] = AUDION_CANONICAL_TOOLTIP_HIDE_DELAY_MS
        self.props["transition-duration"] = AUDION_CANONICAL_TOOLTIP_TRANSITION_MS
        self.classes("audion-tooltip")

    NiceGuiTooltip.__init__ = audion_tooltip_init  # type: ignore[method-assign]
    NiceGuiTooltip._audion_canonical_tooltip_defaults = True  # type: ignore[attr-defined]


install_audion_canonical_tooltip_defaults()


AUDION_CANONICAL_UI_CSS = """
<style id="audion-canonical-tooltip-icon-style">
  html body .q-tooltip,
  html body .audion-tooltip {
    background: rgb(23, 33, 43) !important;
    background-color: rgb(23, 33, 43) !important;
    color: #f4f8fb !important;
    border: 1px solid rgba(88, 166, 255, 0.24) !important;
    border-radius: 8px !important;
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.34) !important;
  }
  html body .q-icon.material-icons,
  html body .q-icon.material-symbols-outlined,
  html body .q-icon.material-symbols-rounded,
  html body i.material-icons,
  html body i.material-symbols-outlined,
  html body i.material-symbols-rounded,
  html body .q-btn .q-icon,
  html body .q-btn .material-icons,
  html body .q-btn .material-symbols-outlined,
  html body .q-btn .material-symbols-rounded,
  html body .q-field .q-field__append .q-icon,
  html body .q-field .q-field__prepend .q-icon,
  html body .q-item .q-icon,
  html body .q-menu .q-icon,
  html body .audion-label-icon,
  html body .audion-path-option-pin,
  html body .audion-select-option-pin {
    font-size: 14px !important;
    width: 14px !important;
    min-width: 14px !important;
    height: 14px !important;
    line-height: 14px !important;
  }
  html body .material-icons,
  html body .q-icon.material-icons {
    font-family: "Material Icons" !important;
  }
  html body .material-symbols-outlined,
  html body .q-icon.material-symbols-outlined {
    font-family: "Material Symbols Outlined" !important;
  }
  html body .material-symbols-rounded,
  html body .q-icon.material-symbols-rounded {
    font-family: "Material Symbols Rounded" !important;
  }
</style>
"""


def add_audion_canonical_ui_styles() -> None:
    ui.add_head_html(AUDION_CANONICAL_UI_CSS)


APPLICATION_CSS_PATH = Path(__file__).resolve().with_name("theme.css")
_application_css_cache = ""


def application_css() -> str:
    """The application stylesheet lives next to this module, not inside it."""
    global _application_css_cache
    if not _application_css_cache:
        _application_css_cache = APPLICATION_CSS_PATH.read_text(encoding="utf-8")
    return _application_css_cache



def audion_tooltip_path_text(path_value: Any) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        return str(path)
    except Exception:
        return raw


def audion_folder_button_tooltip(folder_id: str, path_value: Any) -> str:
    key = str(folder_id or "folder").strip().lower()
    path_text = audion_tooltip_path_text(path_value)
    if getattr(settings, "language", "ru") == "ru":
        descriptions = {
            "logs": "папку логов запусков и вывода терминала",
            "report": "папку отчётов и результатов операций",
            "reports": "папку отчётов и результатов операций",
            "config": "папку конфигурации проекта: manifest, GUI-настройки и кэши",
            "state": "папку рабочего состояния GUI",
            "project": "корневую папку проекта",
            "root": "корневую папку проекта",
            "data": "папку данных проекта",
            "pipeline": "папку pipeline-артефактов и промежуточных результатов",
            "github": "папку GitHub-артефактов проекта",
            "install": "папку install/runtime-артефактов проекта",
        }
        description = descriptions.get(key, f"папку {folder_id}")
        return f"Открыть {description}: {path_text}" if path_text else f"Открыть {description}."
    descriptions = {
        "logs": "the logs folder with run and terminal output",
        "report": "the reports/results folder",
        "reports": "the reports/results folder",
        "config": "the project config folder with manifest, GUI settings, and caches",
        "state": "the GUI state folder",
        "project": "the project root folder",
        "root": "the project root folder",
        "data": "the project data folder",
        "pipeline": "the pipeline artifacts and intermediate results folder",
        "github": "the project GitHub artifacts folder",
        "install": "the project install/runtime artifacts folder",
    }
    description = descriptions.get(key, f"the {folder_id} folder")
    return f"Open {description}: {path_text}" if path_text else f"Open {description}."


def audion_terminal_action_tooltip(action: str) -> str:
    key = str(action or "").strip().lower()
    if getattr(settings, "language", "ru") == "ru":
        tips = {
            "clear_terminal_window": "Очистить только видимое окно терминала. Файлы логов, отчёты и результаты операций не удаляются.",
            "expand": "Открыть терминал в большом окне, чтобы читать длинный вывод без тесной панели.",
            "expand_log": "Открыть терминал в большом окне, чтобы читать длинный вывод без тесной панели.",
            "pin_command": "Закрепить текущую команду в истории терминала для быстрого повторного запуска.",
            "unpin_command": "Открепить текущую команду от верхней части истории терминала.",
            "clear_history": "Очистить историю команд терминала. Закреплённые команды и файлы логов не удаляются.",
            "terminal_shell": "Выбрать оболочку, в которой будут запускаться команды терминала.",
            "terminal_history": "Выбрать ранее сохранённую или закреплённую команду терминала.",
            "terminal_command": "Команда, которая будет выполнена из выбранной рабочей папки.",
            "terminal_cwd": "Рабочая папка терминала. Команда будет запущена именно отсюда.",
            "pick_folder": "Выбрать рабочую папку терминала через системный диалог.",
            "terminal_run": "Запустить введённую команду в выбранной оболочке и рабочей папке.",
            "latest_report": "Открыть последний созданный отчёт, если он уже есть.",
            "command_preview": "Показать команду, которая будет запущена с текущими параметрами, без выполнения операции.",
            "report_view": "Открыть встроенный список отчётов без перехода в проводник.",
            "close": "Закрыть большое окно терминала и вернуться к основной панели.",
        }
    else:
        tips = {
            "clear_terminal_window": "Clear only the visible terminal window. Log files, reports, and operation results are not deleted.",
            "expand": "Open the terminal in a large window for reading long output comfortably.",
            "expand_log": "Open the terminal in a large window for reading long output comfortably.",
            "pin_command": "Pin the current terminal command for quick reuse.",
            "unpin_command": "Remove the current command from the pinned command list.",
            "clear_history": "Clear terminal command history. Pinned commands and log files are not deleted.",
            "terminal_shell": "Choose the shell used to run terminal commands.",
            "terminal_history": "Pick a saved or pinned terminal command.",
            "terminal_command": "Command to run from the selected working folder.",
            "terminal_cwd": "Terminal working folder. Commands are started from here.",
            "pick_folder": "Choose the terminal working folder with the system dialog.",
            "terminal_run": "Run the entered command in the selected shell and working folder.",
            "latest_report": "Open the latest generated report, if one exists.",
            "command_preview": "Show the command that would run with the current settings, without executing it.",
            "report_view": "Open the built-in reports list without switching to the file explorer.",
            "close": "Close the large terminal window and return to the main panel.",
        }
    return tips.get(key, key.replace("_", " ").strip())


from system_core.core.ansi import AnsiHtmlRenderer, terminal_lines_html as _terminal_lines_html
from system_core.core.config import load_yaml_or_json
from system_core.core.jobs import execute_operation
from system_core.core.manifest import CommandNode, Operation, load_manifest
from system_core.core.paths import ensure_project_dirs, get_project_paths, open_folder
from system_core.core.ui_theme_catalog import DEFAULT_THEME_ID, normalize_theme_id
from system_core.core.ui_settings import load_ui_settings, save_ui_settings
from system_core.services.package_links import package_archive_type
from system_core.ui_nicegui.labels import LABELS
from system_core.ui_nicegui.workbench import (
    WorkbenchAdapter,
    WorkbenchConfig,
    WorkbenchHandlers,
    WorkbenchRenderer,
    WorkbenchRole,
    WORKBENCH_FEEDBACK_CSS,
    WORKBENCH_LAYOUT_CSS,
    WORKBENCH_OVERRIDE_CSS,
    canonical_role,
)


paths = get_project_paths(ROOT)
ensure_project_dirs(paths)
manifest = load_manifest(paths.config / "tool_manifest.yaml")
settings_path = paths.config / "gui_settings.yaml"
settings = load_ui_settings(settings_path)

def _clear_portable_workspace_settings() -> None:
    for attr in ("source_path", "destination_path", "workspace_source", "workspace_target"):
        if hasattr(settings, attr):
            setattr(settings, attr, "")


_clear_portable_workspace_settings()


def display_path(path_value: Any) -> str:
    text = str(path_value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(ROOT)
    except (OSError, ValueError):
        return str(path)
    return str(relative) or "."



def terminal_lines_html(lines, *, leading_newline: bool = False, renderer: AnsiHtmlRenderer | None = None) -> str:
    return _terminal_lines_html(lines, leading_newline=False, renderer=renderer).replace("\n", "")


tool_info: dict[str, Any] = manifest.raw.get("tool", {})
ui_info: dict[str, Any] = manifest.raw.get("ui", {})
checkbox_selection_path = paths.config / "checkbox_selection.yaml"
PATH_HISTORY_PATH = paths.config / "path_history.json"
PATH_HISTORY_LIMIT = 100

PACKAGE_FIELD_SUFFIXES = {
    "packages_system": "system",
    "packages_dev": "dev",
    "packages_ai": "ai",
    "packages_pkms": "pkms",
    "packages_office": "office",
    "packages_media_images": "media_images",
    "packages_media_audio": "media_audio",
    "packages_media_video": "media_video",
    "packages_network": "network",
    "packages_hardware": "hardware",
    "packages_msvc": "msvc",
    "packages_msvc_legacy": "msvc_legacy",
}

PACKAGE_FIELD_DEFAULT_TONES = {
    "system": "utility",
    "dev": "dev",
    "ai": "ai",
    "pkms": "pkms",
    "office": "office",
    "media_images": "creator",
    "media_audio": "audio",
    "media_video": "video",
    "network": "network",
    "hardware": "hardware",
    "msvc": "runtime",
    "msvc_legacy": "runtime",
    "custom": "default",
    "other": "default",
    "installed_pins": "security",
    "pins": "security",
}

PACKAGE_TONE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("runtime", ("runtime", "vcredist", "visual c++", "dotnet.desktopruntime", "desktop runtime", "windowsappruntime", "vclibs", "directx")),
    ("archive", ("7zip", "7-zip", "peazip", "winrar", "nanazip", "bandizip", "rar", "zip", "zstd", "gzip", "bzip", "xz")),
    ("browser", ("browser", "firefox", "chrome", "chromium", "edge", "opera", "vivaldi", "brave", "zen-browser", "ungoogled", "centbrowser", "tor browser")),
    ("vpn", ("vpn", "v2ray", "karing", "happ", "wireguard", "openvpn", "tailscale", "zerotier", "warp", "proxy")),
    ("messenger", ("telegram", "discord", "signal", "whatsapp", "messenger", "slack", "teams")),
    ("security", ("bitwarden", "keepass", "1password", "authy", "gpg", "veracrypt", "pin", "password")),
    ("cloud", ("yandex.disk", "yandex disk", "onedrive", "dropbox", "google drive", "syncthing", "nextcloud")),
    ("ai", ("openai", "codex", "claude", "anthropic", "gemini", "chatgpt", "ollama", "lm studio", "lmstudio")),
    ("pkms", ("notion", "obsidian", "joplin", "evernote", "upnote", "zettlr", "appflowy", "logseq", "anytype")),
    ("office", ("libreoffice", "onlyoffice", "office", "acrobat", "reader", "pdf", "calibre", "pandoc", "xournal", "sumatra")),
    ("creator", ("krita", "gimp", "inkscape", "imagemagick", "xnview", "faststone", "sharex", "blender", "paint.net", "darktable", "digikam")),
    ("audio", ("foobar", "reaper", "audacity", "ocenaudio", "fl studio", "ableton", "musicbee", "mp3tag")),
    ("video", ("ffmpeg", "vlc", "shutterencoder", "shutter encoder", "losslesscut", "handbrake", "mpc-hc", "mpc-be", "mpv", "mkvtoolnix", "subtitle", "qbittorrent", "yt-dlp")),
    ("dev", ("visualstudiocode", "visual studio code", "vscodium", "github", "gitkraken", "git.", "notepad++", "docker", "node", "python", "rust", "golang", "postman", "jetbrains", "cmake")),
    ("terminal", ("windowsterminal", "windows terminal", "powershell", "wezterm", "terminal", "putty")),
    ("disk", ("teracopy", "rufus", "crystaldisk", "diskmark", "diskinfo", "etcher", "ventoy", "filelight", "wiztree")),
    ("benchmark", ("cinebench", "furmark", "afterburner", "cpu-z", "gpu-z", "hwinfo", "benchmark", "crystaldiskmark")),
    ("hardware", ("cpuid", "techpowerup", "realiX", "hwinfo", "hardware", "driver", "nvidia", "amd")),
    ("utility", ("everything", "winmerge", "rhash", "powertoys", "appinstaller", "winget", "sysinternals", "process explorer")),
)

ROOT_COMMAND_PRIORITY = {
    "ai_package_planner": 5,
    "install_selected_packages": 10,
    "preview_available_updates": 20,
    "update_all_available_packages": 30,
    "update_selected_packages": 40,
    "update_available_packages": 50,
    "uninstall_selected_installed_packages": 55,
    "single_package": 60,
    "pin_selected_packages": 80,
    "import_export": 90,
    "classic_scripts": 100,
    "portable": 105,
    "maintenance": 110,
}

ROOT_UNINSTALL_SELECTED_ID = "uninstall_selected_installed_packages"
UPDATE_AVAILABLE_TOOLBAR_ACTION_IDS = (
    "preview_available_updates",
    "update_all_available_packages",
)
# App Installer carries winget itself: it never belongs in a package batch, so
# it is hidden from the root list and offered at the top of the update windows.
APP_INSTALLER_COMMAND_ID = "update_app_installer"
UPDATE_WINDOW_COMMAND_IDS = (
    "update_selected_packages",
    "update_available_packages",
)


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip()}


BUILTIN_THEMES: dict[str, dict[str, Any]] = {
    "code_dark": {
        "label": "Code Dark",
        "label_ru": "Code Темная",
        "mode": "dark",
        "tokens": {
            "color-background-primary": "#141413",
            "color-background-secondary": "#1f1e1a",
            "color-background-tertiary": "#0f0f0e",
            "color-text-primary": "#faf9f5",
            "color-text-secondary": "#e8e6dc",
            "color-text-tertiary": "#b0aea5",
            "color-border-tertiary": "rgba(250, 249, 245, 0.15)",
            "color-border-secondary": "rgba(250, 249, 245, 0.3)",
            "color-border-primary": "rgba(250, 249, 245, 0.4)",
            "color-accent-primary": "#d97757",
            "color-accent-secondary": "#6a9bcc",
            "color-accent-tertiary": "#788c5d",
        },
    },
    "code_graphite": {
        "label": "Code Graphite",
        "label_ru": "Code графит",
        "mode": "dark",
        "tokens": {
            "color-background-primary": "#2c2c2a",
            "color-background-secondary": "#34332f",
            "color-background-tertiary": "#141413",
            "color-text-primary": "#faf9f5",
            "color-text-secondary": "#e8e6dc",
            "color-text-tertiary": "#b0aea5",
            "color-border-tertiary": "rgba(250, 249, 245, 0.15)",
            "color-border-secondary": "rgba(250, 249, 245, 0.3)",
            "color-border-primary": "rgba(250, 249, 245, 0.4)",
            "color-accent-primary": "#d97757",
            "color-accent-secondary": "#6a9bcc",
            "color-accent-tertiary": "#788c5d",
        },
    },
    "code_light": {
        "label": "Code Light",
        "label_ru": "Code светлая",
        "mode": "light",
        "tokens": {
            "color-background-primary": "#faf9f5",
            "color-background-secondary": "#fffdf8",
            "color-background-tertiary": "#f1efe8",
            "color-text-primary": "#141413",
            "color-text-secondary": "#5f5e5a",
            "color-text-tertiary": "#888780",
            "color-border-tertiary": "rgba(20, 20, 19, 0.15)",
            "color-border-secondary": "rgba(20, 20, 19, 0.3)",
            "color-border-primary": "rgba(20, 20, 19, 0.4)",
            "color-accent-primary": "#d97757",
            "color-accent-secondary": "#6a9bcc",
            "color-accent-tertiary": "#788c5d",
        },
    },
    "code_warm": {
        "label": "Code Warm",
        "label_ru": "Code теплая",
        "mode": "light",
        "tokens": {
            "color-background-primary": "#fffdf8",
            "color-background-secondary": "#faf9f5",
            "color-background-tertiary": "#e8e6dc",
            "color-text-primary": "#141413",
            "color-text-secondary": "#444441",
            "color-text-tertiary": "#888780",
            "color-border-tertiary": "rgba(20, 20, 19, 0.15)",
            "color-border-secondary": "rgba(20, 20, 19, 0.3)",
            "color-border-primary": "rgba(20, 20, 19, 0.4)",
            "color-accent-primary": "#d97757",
            "color-accent-secondary": "#6a9bcc",
            "color-accent-tertiary": "#788c5d",
        },
    },
    "audion_light": {
        "label": "Audion Light",
        "label_ru": "Audion светлая",
        "mode": "light",
        "tokens": {
            "color-background-primary": "#f7fbff",
            "color-background-secondary": "#ffffff",
            "color-background-tertiary": "#e6f1fb",
            "color-text-primary": "#102033",
            "color-text-secondary": "#36546f",
            "color-text-tertiary": "#6f879c",
            "color-border-tertiary": "rgba(4, 44, 83, 0.15)",
            "color-border-secondary": "rgba(4, 44, 83, 0.3)",
            "color-border-primary": "rgba(4, 44, 83, 0.4)",
            "color-accent-primary": "#378ADD",
            "color-accent-secondary": "#1D9E75",
            "color-accent-tertiary": "#534AB7",
        },
    },
    "audion_dark": {
        "label": "Audion Dark",
        "label_ru": "Audion Темная",
        "mode": "dark",
        "tokens": {
            "color-background-primary": "#08131f",
            "color-background-secondary": "#102033",
            "color-background-tertiary": "#050b12",
            "color-text-primary": "#f7fbff",
            "color-text-secondary": "#d7e7f6",
            "color-text-tertiary": "#9bb7cf",
            "color-border-tertiary": "rgba(247, 251, 255, 0.15)",
            "color-border-secondary": "rgba(247, 251, 255, 0.3)",
            "color-border-primary": "rgba(247, 251, 255, 0.4)",
            "color-accent-primary": "#6a9bcc",
            "color-accent-secondary": "#5DCAA5",
            "color-accent-tertiary": "#7F77DD",
        },
    },
    "asar_dark": {
        "label": "Asar Dark",
        "label_ru": "Asar Темная",
        "mode": "dark",
        "tokens": {
            "color-background-primary": "#181a1f",
            "color-background-secondary": "#20242b",
            "color-background-tertiary": "#0f1115",
            "color-text-primary": "#f4f7fb",
            "color-text-secondary": "#d6dde7",
            "color-text-tertiary": "#9aa7b8",
            "color-border-tertiary": "rgba(244, 247, 251, 0.15)",
            "color-border-secondary": "rgba(244, 247, 251, 0.3)",
            "color-border-primary": "rgba(244, 247, 251, 0.4)",
            "color-accent-primary": "#85B7EB",
            "color-accent-secondary": "#9FE1CB",
            "color-accent-tertiary": "#CECBF6",
        },
    },
}


def _normalize_theme(theme_id: str, theme_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": str(theme_data.get("label") or theme_id).strip(),
        "label_ru": str(theme_data.get("label_ru") or theme_data.get("label") or theme_id).strip(),
        "mode": "dark" if str(theme_data.get("mode", "dark")).lower() == "dark" else "light",
        "tokens": _string_map(theme_data.get("tokens", {})),
    }


def builtin_themes() -> dict[str, dict[str, Any]]:
    return {
        theme_id: _normalize_theme(theme_id, theme_data)
        for theme_id, theme_data in BUILTIN_THEMES.items()
    }


def load_ui_colors(path: Path) -> dict[str, Any]:
    data = load_yaml_or_json(path) if path.exists() else {}
    if not isinstance(data, dict):
        data = {}
    themes: dict[str, dict[str, Any]] = builtin_themes()
    themes_raw = data.get("themes", {})
    if not isinstance(themes_raw, dict):
        themes_raw = {}
    for theme_id, theme_data in themes_raw.items():
        if not isinstance(theme_data, dict):
            continue
        normalized_id = normalize_theme_id(theme_id, default="")
        if not normalized_id:
            continue
        normalized = _normalize_theme(normalized_id, theme_data)
        if normalized_id in themes:
            base = themes[normalized_id]
            normalized["tokens"] = {**_string_map(base.get("tokens", {})), **normalized["tokens"]}
        themes[normalized_id] = normalized
    return {
        "ramps": data.get("ramps", {}) if isinstance(data.get("ramps", {}), dict) else {},
        "tokens": _string_map(data.get("tokens", {})),
        "themes": themes,
    }


ui_colors = load_ui_colors(paths.config / "ui_colors.yaml")


def tolerate_missing_process_pool() -> None:
    """NiceGUI creates a CPU process pool even when only run.io_bound is used.

    Some locked-down portable/sandbox environments deny multiprocessing pipes.
    The GUI only needs NiceGUI's thread pool, so keep startup alive in that case.
    """
    try:
        import nicegui.run as nicegui_run  # type: ignore
    except Exception:
        return

    original_setup = nicegui_run.setup

    def safe_setup() -> None:
        try:
            original_setup()
        except (OSError, PermissionError) as exc:
            logging.warning("NiceGUI process pool disabled: %s", exc)
            nicegui_run.process_pool = None

    nicegui_run.setup = safe_setup


tolerate_missing_process_pool()

PICKER_BOOTSTRAP = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AudionDpiAwareness {
  [DllImport("user32.dll")]
  public static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);
  [DllImport("shcore.dll")]
  public static extern int SetProcessDpiAwareness(int value);
}
"@
  try { [AudionDpiAwareness]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null }
  catch { [AudionDpiAwareness]::SetProcessDpiAwareness(2) | Out-Null }
} catch {}
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
"""

TERMINAL_HISTORY_PATH = paths.config / "terminal_commands.json"
TERMINAL_HISTORY_LIMIT = 200
TERMINAL_RENDER_LINE_LIMIT = 1500


def clean_terminal_commands(items: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(items, list):
        return result
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result[:TERMINAL_HISTORY_LIMIT]


def resolved_terminal_cwd(value: Any) -> str:
    """Absolute terminal CWD, falling back to the project root.

    The project is portable, so a cached folder can outlive the copy that produced it.
    A relative value is read against the current ROOT, and anything that is no longer a
    directory resets to the start state instead of failing on the first command.
    """
    text = str(value or "").strip()
    if not text:
        return str(ROOT)
    candidate = Path(os.path.expandvars(text)).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return str(ROOT)
    return str(candidate) if candidate.is_dir() else str(ROOT)


def stored_terminal_cwd(value: Any) -> str:
    """Terminal CWD as written to disk: project-local folders stay relative to ROOT."""
    resolved = Path(resolved_terminal_cwd(value))
    try:
        return str(resolved.relative_to(Path(ROOT).resolve()))
    except ValueError:
        return str(resolved)


def load_terminal_cache() -> dict[str, Any]:
    default = {
        "history": [],
        "pinned": [],
        "last": "",
        "shell": "pwsh" if os.name == "nt" else "sh",
        "cwd": str(ROOT),
    }
    if not TERMINAL_HISTORY_PATH.exists():
        return default
    try:
        raw = json.loads(TERMINAL_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not load terminal command history: %s", exc)
        return default
    if not isinstance(raw, dict):
        return default
    shell = str(raw.get("shell") or default["shell"]).strip().lower()
    if os.name == "nt":
        if shell not in {"pwsh", "cmd"}:
            shell = "pwsh"
    else:
        shell = "sh"
    cwd = resolved_terminal_cwd(raw.get("cwd"))
    return {
        "history": clean_terminal_commands(raw.get("history", [])),
        "pinned": clean_terminal_commands(raw.get("pinned", [])),
        "last": str(raw.get("last") or "").strip(),
        "shell": shell,
        "cwd": cwd,
    }


initial_terminal_cache = load_terminal_cache()


def _legacy_workspace_paths() -> dict[str, str]:
    legacy_path = paths.config / "workspace_paths.json"
    if not legacy_path.exists():
        return {}
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Could not load legacy workspace paths: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "source_path": str(raw.get("workspace_source_path") or raw.get("source_path") or "").strip(),
        "destination_path": str(raw.get("workspace_target_path") or raw.get("destination_path") or "").strip(),
    }


legacy_workspace_paths = _legacy_workspace_paths()

state: dict[str, Any] = {
    "running": False,
    "cancel": False,
    "progress": 0.0,
    "status": "",
    "lines": [],
    "log_version": 0,
    "terminal_scroll_top_seq": 0,
    "terminal_activity": "",
    "terminal_activity_version": 0,
    "exit_code": None,
    "command_path": [],
    "pending_command": None,
    "ai_planner_tab": "planner",
    "field_values": {},
    "checkbox_filters": {},
    "terminal_cache": initial_terminal_cache,
    "terminal_command": str(initial_terminal_cache.get("last") or ""),
    "terminal_shell": str(initial_terminal_cache.get("shell") or ("pwsh" if os.name == "nt" else "sh")),
    "terminal_cwd": str(initial_terminal_cache.get("cwd") or ROOT),
    "source_path": str(getattr(settings, "source_path", "") or legacy_workspace_paths.get("source_path") or paths.input),
    "destination_path": str(getattr(settings, "destination_path", "") or legacy_workspace_paths.get("destination_path") or paths.output),
    "workspace_feedback": {},
}

dynamic_option_cache: dict[str, tuple[float, list[Any]]] = {}
dynamic_option_tasks: set[str] = set()


def clear_dynamic_option_cache(source_filter: str | None = None) -> None:
    for cache_key in list(dynamic_option_cache):
        if source_filter is None or cache_key == source_filter or cache_key.startswith(source_filter + "|"):
            dynamic_option_cache.pop(cache_key, None)


def operation_by_id(operation_id: str) -> Operation | None:
    for operation in [*manifest.operations, *manifest.maintenance_operations]:
        if operation.id == operation_id:
            return operation
    return None


def tr(key: str, **kwargs: Any) -> str:
    lang = settings.language if settings.language in LABELS else "en"
    text = LABELS.get(lang, LABELS["en"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def l10n(ru: str, en: str) -> str:
    return ru if settings.language == "ru" else en


def em(key: str) -> str:
    if not bool(getattr(settings, "emoji", False)):
        return ""
    return {
        "workspace": "📁 ",
        "operations": "⚙ ",
        "maintenance": "🧰 ",
        "status": "● ",
        "log": "🖥 ",
    }.get(key, "")


def app_title() -> str:
    title = str(ui_info.get("title") or tool_info.get("name") or "Audion GUI Tool")
    return title[:-3] if title.endswith(" UI") else title


def active_theme() -> str:
    theme_id = normalize_theme_id(settings.theme)
    themes = ui_colors["themes"]
    if theme_id in themes:
        return theme_id
    return DEFAULT_THEME_ID if DEFAULT_THEME_ID in themes else next(iter(themes))


def active_theme_data() -> dict[str, Any]:
    return dict(ui_colors["themes"][active_theme()])


def active_theme_mode() -> str:
    return str(active_theme_data().get("mode", "dark"))


def theme_label(theme_id: str) -> str:
    theme_data = ui_colors["themes"].get(theme_id, {})
    label_key = "label_ru" if settings.language == "ru" else "label"
    return str(theme_data.get(label_key) or theme_data.get("label") or theme_id)


def theme_options() -> dict[str, str]:
    return {theme_id: theme_label(theme_id) for theme_id in ui_colors["themes"]}


def set_theme(theme_id: Any) -> None:
    selected = normalize_theme_id(theme_id)
    if selected not in ui_colors["themes"]:
        return
    settings.theme = selected
    save_ui_settings(settings_path, settings)
    safe_notify(tr("theme_saved"), "positive")
    ui.run_javascript("window.location.reload()")


def theme_change_handler(event: Any) -> None:
    set_theme(getattr(event, "value", None))


def theme_variables() -> dict[str, str]:
    variables: dict[str, str] = {}
    for ramp_name, stops in ui_colors["ramps"].items():
        if not isinstance(stops, dict):
            continue
        for stop, color in stops.items():
            variables[f"color-{ramp_name}-{stop}"] = str(color).strip()
    variables.update(ui_colors["tokens"])
    variables.update(_string_map(active_theme_data().get("tokens", {})))
    variables.setdefault("color-background-primary", "#141413")
    variables.setdefault("color-background-secondary", "#1f1e1a")
    variables.setdefault("color-background-tertiary", "#0f0f0e")
    variables.setdefault("color-text-primary", "#faf9f5")
    variables.setdefault("color-text-secondary", "#e8e6dc")
    variables.setdefault("color-text-tertiary", "#b0aea5")
    variables.setdefault("color-border-tertiary", "rgba(250, 249, 245, 0.15)")
    variables.setdefault("color-border-secondary", "rgba(250, 249, 245, 0.3)")
    variables.setdefault("color-border-primary", "rgba(250, 249, 245, 0.4)")
    variables.setdefault("color-accent-primary", "#d97757")
    variables.setdefault("color-accent-secondary", "#6a9bcc")
    variables.setdefault("color-accent-tertiary", "#788c5d")
    variables.setdefault("font-sans", "Inter, Segoe UI, Arial, sans-serif")
    variables.setdefault("font-mono", "Cascadia Mono, Consolas, monospace")
    variables.setdefault("border-radius-md", "8px")
    variables.setdefault("border-radius-lg", "12px")
    return variables


def add_log(message: str) -> None:
    state["lines"].append(str(message).rstrip())
    overflow = max(0, len(state["lines"]) - TERMINAL_RENDER_LINE_LIMIT)
    if overflow:
        del state["lines"][:overflow]
    state["log_version"] = int(state["log_version"]) + 1


def set_terminal_activity(message: str) -> None:
    """One live line under the terminal output; it is replaced, never appended."""
    text = str(message).rstrip()
    if text == str(state.get("terminal_activity") or ""):
        return
    state["terminal_activity"] = text
    state["terminal_activity_version"] = int(state["terminal_activity_version"]) + 1


def terminal_activity_html() -> str:
    text = str(state.get("terminal_activity") or "")
    if not text:
        return ""
    # A private renderer: the live line must not carry ANSI state into the log.
    body = terminal_lines_html([text], renderer=AnsiHtmlRenderer())
    return f'<span class="audion-terminal-live-spinner"></span>{body}'


def reset_terminal_log() -> None:
    state["lines"] = []
    state["log_version"] = int(state["log_version"]) + 1


def terminal_log_body_html() -> str:
    """Inner HTML of the terminal `<pre>`; the element itself is owned by NiceGUI."""
    return terminal_lines_html(state["lines"], renderer=AnsiHtmlRenderer())


def terminal_activity_block_html() -> str:
    """The live line as a whole block, or an empty string when there is no activity."""
    body = terminal_activity_html()
    if not body:
        return ""
    return f'<div class="audion-terminal-live">{body}</div>'


def progress_text() -> str:
    return f"{round(max(0.0, min(1.0, float(state['progress']))) * 100):.0f}%"


def safe_notify(message: str, kind: str = "info", **notify_kwargs: Any) -> None:
    notify_type = str(notify_kwargs.pop("type", kind))
    options = {"message": str(message), "type": notify_type, **notify_kwargs}
    delivered = False
    for client in list(nicegui_app.clients()):
        if getattr(client, "_deleted", False) or not client.has_socket_connection:
            continue
        try:
            client.outbox.enqueue_message("notify", options, client.id)
            delivered = True
        except Exception as exc:
            logging.warning("NiceGUI notification delivery failed for client %s: %s", getattr(client, "id", "?"), exc)
    if delivered:
        return

    try:
        ui.notify(message, type=notify_type, **notify_kwargs)
    except RuntimeError as exc:
        message_text = str(exc)
        if "slot belongs to has been deleted" not in message_text and "current slot cannot be determined" not in message_text:
            raise
        logging.warning("NiceGUI notification skipped because no live client slot was available: %s", message)


RUN_STATE_LABELS = {
    "idle": ("idle", "audion-status-idle"),
    "running": ("running", "audion-status-running"),
    "done": ("done", "audion-status-done"),
    "error": ("error", "audion-status-error"),
}


def run_state() -> str:
    """Which of the four states the panel is showing.

    Colour carries this everywhere it appears, so it is decided once.
    """
    if bool(state["running"]):
        return "running"
    exit_code = state.get("exit_code")
    if exit_code is None:
        return "idle"
    return "done" if int(exit_code or 0) == 0 else "error"


def status_row_classes() -> str:
    return f"audion-status-row {RUN_STATE_LABELS[run_state()][1]}"


def status_state_text() -> str:
    return tr(RUN_STATE_LABELS[run_state()][0]).upper()


def elapsed_text(seconds: float | None) -> str:
    """A run's own clock, mm:ss, or an em dash before anything has run.

    The start is noticed by the refresh timer rather than written by the code that
    starts a run: there are several such places, and none of them has to know
    about the panel.
    """
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def status_dot_classes() -> str:
    base = "audion-status-dot text-lg leading-none"
    if bool(state["running"]):
        return f"{base} text-sky-400 animate-pulse"
    if state.get("exit_code") is None:
        return f"{base} text-gray-500"
    if int(state.get("exit_code") or 0) == 0:
        return f"{base} text-green-400"
    return f"{base} text-red-400"


def set_progress(value: float) -> None:
    state["progress"] = max(0.0, min(1.0, float(value)))


def cancel_requested() -> bool:
    return bool(state["cancel"])


def hidden_subprocess_flags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def hidden_subprocess_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def hidden_subprocess_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    flags = hidden_subprocess_flags()
    startupinfo = hidden_subprocess_startupinfo()
    if flags:
        kwargs["creationflags"] = flags
    if startupinfo is not None:
        kwargs["startupinfo"] = startupinfo
    return kwargs


def resolve_dialog_powershell() -> list[str]:
    candidates = [
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command"],
        [str(paths.system_core / "powershell" / "pwsh.exe"), "-NoLogo", "-NoProfile", "-STA", "-Command"],
        ["pwsh.exe", "-NoLogo", "-NoProfile", "-STA", "-Command"],
    ]
    for candidate in candidates:
        exe = candidate[0]
        if Path(exe).exists() or shutil.which(exe):
            return candidate
    raise RuntimeError("PowerShell was not found for Windows picker.")


def parse_picker_paths(text: str) -> list[Path]:
    import json

    payload = text.strip()
    if not payload:
        return []
    data = json.loads(payload)
    if isinstance(data, str):
        data = [data]
    return [Path(str(item)).resolve() for item in data if str(item).strip()]


_PICKER_RUN_LOCK = threading.Lock()
_PICKER_JOB_LOCK = threading.Lock()
_PICKER_SHUTDOWN = threading.Event()
_PICKER_JOB_HANDLE: int | None = None


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def close_picker_job() -> None:
    global _PICKER_JOB_HANDLE
    _PICKER_SHUTDOWN.set()
    with _PICKER_JOB_LOCK:
        handle = _PICKER_JOB_HANDLE
        _PICKER_JOB_HANDLE = None
    if os.name == "nt" and handle:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(handle))


def _picker_job_handle() -> int | None:
    global _PICKER_JOB_HANDLE
    if os.name != "nt" or _PICKER_SHUTDOWN.is_set():
        return None
    with _PICKER_JOB_LOCK:
        if _PICKER_SHUTDOWN.is_set():
            return None
        if _PICKER_JOB_HANDLE:
            return _PICKER_JOB_HANDLE
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            logging.warning("Could not create the Windows picker job: %s", ctypes.get_last_error())
            return None
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(wintypes.HANDLE(job))
            logging.warning("Could not configure the Windows picker job: %s", error)
            return None
        _PICKER_JOB_HANDLE = int(job)
        return _PICKER_JOB_HANDLE


def _assign_picker_to_job(process: subprocess.Popen[str]) -> None:
    handle = _picker_job_handle()
    if os.name != "nt" or not handle:
        if _PICKER_SHUTDOWN.is_set() and process.poll() is None:
            process.kill()
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(handle),
        wintypes.HANDLE(int(process._handle)),  # type: ignore[attr-defined]
    ):
        logging.warning("Could not attach picker PID %s to its Windows job: %s", process.pid, ctypes.get_last_error())


def run_picker_script(script: str, error_message: str) -> list[Path]:
    if not _PICKER_RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("A Windows picker is already open.")
    process: subprocess.Popen[str] | None = None
    try:
        if _PICKER_SHUTDOWN.is_set():
            raise RuntimeError("Windows picker supervisor is shutting down.")
        _picker_job_handle()
        process = subprocess.Popen(
            [*resolve_dialog_powershell(), script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **hidden_subprocess_kwargs(),
        )
        _assign_picker_to_job(process)
        if _PICKER_SHUTDOWN.is_set():
            if process.poll() is None:
                process.kill()
            raise RuntimeError("Windows picker supervisor is shutting down.")
        try:
            stdout, stderr = process.communicate(timeout=3600)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RuntimeError("Windows picker timed out.") from exc
        if process.returncode != 0:
            raise RuntimeError(stderr.strip() or error_message)
        return parse_picker_paths(stdout)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
        _PICKER_RUN_LOCK.release()


atexit.register(close_picker_job)
nicegui_app.on_shutdown(close_picker_job)


def pick_single_file(title: str = "Select source file", file_filter: str = "All files|*.*") -> Path | None:
    safe_title = title.replace("'", "''")
    safe_filter = file_filter.replace("'", "''")
    script = PICKER_BOOTSTRAP + f"""
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = '{safe_title}'
$dialog.Multiselect = $false
$dialog.Filter = '{safe_filter}'
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
  @($dialog.FileName) | ConvertTo-Json -Compress
}}
"""
    selected = run_picker_script(script, "File picker failed.")
    return selected[0] if selected else None


def pick_folder(title: str = "Add folder to input", allow_new_folder: bool = False) -> list[Path]:
    script = PICKER_BOOTSTRAP + r"""
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '__TITLE__'
$dialog.ShowNewFolderButton = __ALLOW_NEW_FOLDER__
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  @($dialog.SelectedPath) | ConvertTo-Json -Compress
}
""".replace("__TITLE__", title.replace("'", "''")).replace("__ALLOW_NEW_FOLDER__", "$true" if allow_new_folder else "$false")
    return run_picker_script(script, "Folder picker failed.")


def absolute_project_path(path_value: Any) -> Path:
    path = Path(str(path_value or "")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def remove_path_tree(path: Path) -> int:
    is_junction = bool(getattr(os.path, "isjunction", lambda _path: False)(path))
    if path.is_symlink() or is_junction:
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
        return 1
    if path.is_file():
        path.unlink()
        return 1
    if path.is_dir():
        shutil.rmtree(path)
        return 1
    return 0


def clear_directory_contents(folder: Path) -> int:
    removed = 0
    if not folder.exists():
        return removed
    for child in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        # .gitkeep is not spared: input and output must be genuinely empty after
        # a clear, so nobody has to wonder what the leftover file is or whether it
        # is safe to delete. The folders come from install/init_folders.cmd.
        removed += remove_path_tree(child)
    return removed


def normalized_absolute_path(path_value: Any) -> Path:
    return absolute_project_path(path_value).resolve(strict=False)


def paths_equal(left: Any, right: Any) -> bool:
    return os.path.normcase(str(normalized_absolute_path(left))) == os.path.normcase(str(normalized_absolute_path(right)))


def validate_workspace_delete_target(path_value: Any) -> Path:
    target = normalized_absolute_path(path_value)
    if target.parent == target:
        raise RuntimeError(f"Refusing to delete a filesystem root: {target}")
    if paths_equal(target, ROOT):
        raise RuntimeError(f"Refusing to delete the project root: {target}")
    return target


def delete_workspace_path_contents(path_value: Any) -> dict[str, Any]:
    target = validate_workspace_delete_target(path_value)
    if not target.exists() and not target.is_symlink():
        return {"path": str(target), "kind": "missing", "removed": 0}
    is_junction = bool(getattr(os.path, "isjunction", lambda _path: False)(target))
    if target.is_file() or target.is_symlink() or is_junction:
        return {"path": str(target), "kind": "file", "removed": remove_path_tree(target)}
    if not target.is_dir():
        raise RuntimeError(f"Unsupported workspace path: {target}")
    return {"path": str(target), "kind": "folder", "removed": clear_directory_contents(target)}


def delete_workspace_io_contents(source: Path, target: Path) -> dict[str, Any]:
    source_result = delete_workspace_path_contents(source)
    target_result = (
        {"path": str(normalized_absolute_path(target)), "kind": "same", "removed": 0}
        if paths_equal(source, target)
        else delete_workspace_path_contents(target)
    )
    return {"source": source_result, "target": target_result}


def input_file_list_lines(source: Path) -> list[str]:
    if not source.exists():
        return [tr("file_list_missing", path=source)]
    if source.is_file():
        return [" No.  List", "----  ----", f"001. {source.name}"]
    if not source.is_dir():
        return [f"SOURCE is not a file or folder: {source}"]

    names = sorted((path.name for path in source.rglob("*") if path.is_file()), key=lambda item: item.casefold())
    if not names:
        return [tr("file_list_empty")]

    number_width = max(3, len(str(len(names))))
    lines = [
        f"{'No.':>{number_width}}  List",
        f"{'-' * number_width}  ----",
    ]
    lines.extend(f"{index:0{number_width}d}. {name}" for index, name in enumerate(names, start=1))
    return lines


async def show_input_file_list() -> None:
    if state["running"]:
        safe_notify(tr("another_running"), "warning")
        return

    reset_terminal_log()
    title = tr("file_list")
    state.update(
        {
            "running": True,
            "cancel": False,
            "progress": 0.02,
            "status": f"{tr('running')}: {title}",
            "exit_code": None,
        }
    )
    try:
        lines = await run.io_bound(input_file_list_lines, current_source_path())
        for line in lines:
            add_log(line)
        count = max(0, len(lines) - 2)
        state["terminal_scroll_top_seq"] = int(state["log_version"])
        state["exit_code"] = 0
        state["progress"] = 1.0
        state["status"] = f"{tr('done')}: {title} [{count}]"
        safe_notify(tr("file_list_ready", count=count), "positive")
    except Exception as exc:
        state["exit_code"] = 1
        state["progress"] = max(float(state["progress"]), 0.98)
        state["status"] = f"{tr('error')}: {exc}"
        add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
        safe_notify(str(exc), "negative")
    finally:
        state["running"] = False


async def start_operation(operation: Operation) -> None:
    if state["running"]:
        safe_notify(tr("another_running"), "warning")
        return
    parameters = dict(operation.parameters)
    parameters["input_path"] = str(current_source_path())
    parameters["output_path"] = str(current_target_path())
    parameters["ui_language"] = settings.language
    operation = Operation(
        id=operation.id,
        title=operation.title,
        description=operation.description,
        service=operation.service,
        kind=operation.kind,
        title_ru=operation.title_ru,
        description_ru=operation.description_ru,
        parameters=parameters,
        fields=operation.fields,
    )

    if operation.kind == "dangerous":
        with ui.dialog() as dialog, ui.card().classes("rounded-lg"):
            ui.label(tr("confirm_title")).classes("text-base font-semibold")
            ui.label(operation.display_description(settings.language)).classes("text-sm text-gray-400")
            ui.label(tr("confirm_note")).classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                ui.button(tr("cancel"), on_click=dialog.close).props("dense flat")
                ui.button(tr("run"), on_click=lambda: dialog.submit(True)).props("dense flat no-wrap no-caps").classes("audion-action audion-run-action rounded-lg")
        confirmed = await dialog
        if not confirmed:
            return

    reset_terminal_log()
    set_terminal_activity("")
    state.update(
        {
            "running": True,
            "cancel": False,
            "progress": 0.02,
            "status": f"{tr('running')}: {operation.display_title(settings.language)}",
            "exit_code": None,
        }
    )
    started = time.perf_counter()
    try:
        result = await run.io_bound(
            execute_operation,
            paths,
            operation,
            add_log,
            set_progress,
            cancel_requested,
            set_terminal_activity,
        )
        elapsed = time.perf_counter() - started
        state["exit_code"] = 0 if result.ok else 1
        state["progress"] = 1.0
        state["status"] = f"{tr('done') if result.ok else tr('error')}: {operation.display_title(settings.language)} [{state['exit_code']}] {elapsed:.1f}s"
        safe_notify(result.message, "positive" if result.ok else "negative")
    except Exception as exc:
        state["exit_code"] = 1
        state["progress"] = max(float(state["progress"]), 0.98)
        state["status"] = f"{tr('error')}: {exc}"
        add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
        safe_notify(str(exc), "negative")
    finally:
        state["running"] = False
        set_terminal_activity("")


def current_source_path() -> Path:
    return Path(str(state.get("source_path") or paths.input)).expanduser()


def current_target_path() -> Path:
    return Path(str(state.get("destination_path") or paths.output)).expanduser()

def save_workspace_path(kind: str, value: Any) -> None:
    text = str(value or "").strip()
    if kind == "destination":
        settings.destination_path = ""
        state["destination_path"] = text or str(paths.output)
    elif kind == "source":
        settings.source_path = ""
        state["source_path"] = text or str(paths.input)
    else:
        raise RuntimeError(f"Unsupported workspace path kind: {kind}")
    save_ui_settings(settings_path, settings)


def reload_ui(delay_ms: int = 0) -> None:
    delay = max(0, int(delay_ms))
    ui.run_javascript(f"window.setTimeout(() => window.location.reload(), {delay})")


def open_workspace_folder(role: str) -> None:
    folder = current_target_path() if role == "target" else current_source_path()
    if role != "target" and not folder.exists():
        raise FileNotFoundError(tr("file_list_missing", path=folder))
    if folder.is_file():
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", f"/select,{folder}"], **hidden_subprocess_kwargs())
        else:
            open_folder(folder.parent)
        return
    open_folder(folder)


def mark_workspace_feedback(role: str, action: str) -> None:
    state["workspace_feedback"] = {"role": canonical_role(role), "action": str(action or "path")}


def _save_workspace_adapter_path(role: WorkbenchRole, value: Any) -> None:
    save_workspace_path("destination" if role == "target" else "source", value)


def _workspace_feedback() -> dict[str, str]:
    value = state.get("workspace_feedback")
    return dict(value) if isinstance(value, dict) else {}


def _clear_workspace_feedback() -> None:
    state["workspace_feedback"] = {}


WORKBENCH_CONFIG = WorkbenchConfig(
    root=ROOT,
    input_path=paths.input,
    output_path=paths.output,
    history_path=PATH_HISTORY_PATH,
    history_limit=PATH_HISTORY_LIMIT,
)
WORKBENCH_ADAPTER = WorkbenchAdapter(
    config=WORKBENCH_CONFIG,
    current_path_callback=lambda role: current_target_path() if role == "target" else current_source_path(),
    save_path_callback=_save_workspace_adapter_path,
    language_callback=lambda: settings.language,
    translate_callback=tr,
    log_callback=add_log,
    notify_callback=safe_notify,
    reload_callback=reload_ui,
    busy_callback=lambda: bool(state.get("running")),
    feedback_callback=_workspace_feedback,
    set_feedback_callback=mark_workspace_feedback,
    clear_feedback_callback=_clear_workspace_feedback,
)
WORKBENCH_ADAPTER.validate()
WORKBENCH_ADAPTER.ensure_initial_history()


def workspace_pin_click_handler(role: str, pinned: bool):
    async def handler() -> None:
        path_value = str(current_target_path() if role == "target" else current_source_path())
        if not path_value:
            safe_notify(tr("path_required"), "warning")
            return
        try:
            await run.io_bound(WORKBENCH_ADAPTER.set_path_pinned, role, path_value, pinned)
            mark_workspace_feedback(role, "pin" if pinned else "unpin")
            add_log(f"{'Pinned' if pinned else 'Unpinned'} {role} path: {path_value}")
            reload_ui()
        except Exception as exc:
            add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
            safe_notify(str(exc), "negative")

    return handler


def workspace_delete_path_click_handler(role: str):
    async def handler() -> None:
        if state["running"]:
            safe_notify(tr("another_running"), "warning")
            return
        path = current_target_path() if role == "target" else current_source_path()
        path_value = str(path)
        external_source = role != "target" and not paths_equal(path, paths.input)
        if external_source:
            with ui.dialog() as dialog, ui.card().classes("audion-dialog rounded-lg"):
                is_file = path.is_file()
                ui.label(
                    ("Удалить исходный файл?" if is_file else "Очистить внешний ИСТОЧНИК?")
                    if settings.language == "ru"
                    else ("Delete the source file?" if is_file else "Clear the external SOURCE?")
                ).classes("text-base font-semibold")
                ui.label(str(normalized_absolute_path(path))).classes("max-w-3xl break-all font-mono text-xs text-gray-400")
                with ui.row().classes("gap-2"):
                    ui.button(tr("cancel"), on_click=dialog.close).props("dense flat")
                    ui.button(tr("delete_io_short"), on_click=lambda: dialog.submit(True)).props("dense color=negative")
            if not await dialog:
                return
        if not path_value:
            safe_notify(tr("path_required"), "warning")
            return
        try:
            result = await run.io_bound(delete_workspace_path_contents, path)
            if result.get("kind") == "file":
                await run.io_bound(WORKBENCH_ADAPTER.delete_path_history, role, path_value)
                save_workspace_path("destination" if role == "target" else "source", "")
            mark_workspace_feedback(role, "delete")
            add_log(f"Cleared {role.upper()}: {result.get('path')} [kind={result.get('kind')}, removed={result.get('removed', 0)}]")
            reload_ui(150)
        except Exception as exc:
            add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
            safe_notify(str(exc), "negative")

    return handler


def workspace_open_click_handler(role: str):
    async def handler() -> None:
        try:
            await run.io_bound(open_workspace_folder, role)
            add_log(f"Opened {'target' if role == 'target' else 'source'} folder: {current_target_path() if role == 'target' else current_source_path()}")
        except Exception as exc:
            add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
            safe_notify(str(exc), "negative")

    return handler


def reset_workspace_paths_click_handler():
    async def handler() -> None:
        if state["running"]:
            safe_notify(tr("another_running"), "warning")
            return
        result = await run.io_bound(WORKBENCH_ADAPTER.clear_path_history_cache_keep_pins)
        save_workspace_path("source", "")
        save_workspace_path("destination", "")
        add_log(f"Workspace route reset: SOURCE -> {paths.input}")
        add_log(f"Workspace route reset: TARGET -> {paths.output}")
        add_log(
            "Workspace path cache cleared: "
            f"sources={result.get('removed_sources', 0)}, targets={result.get('removed_targets', 0)}, "
            f"pins kept={result.get('kept_pins', 0)}"
        )
        safe_notify(tr("operation_done"), "positive")
        reload_ui()

    return handler


def workspace_path_select_handler(role: str):
    async def handler(event: Any) -> None:
        path_value = str(getattr(event, "value", "") or "").strip()
        if not path_value:
            return
        save_workspace_path("destination" if role == "target" else "source", path_value)
        await run.io_bound(WORKBENCH_ADAPTER.remember_path, role, path_value)
        mark_workspace_feedback(role, "path")
        add_log(f"{'TARGET' if role == 'target' else 'SOURCE'} -> {path_value}")
        reload_ui()

    return handler


def workspace_pick_click_handler(role: str):
    async def handler() -> None:
        if state["running"]:
            safe_notify(tr("another_running"), "warning")
            return
        try:
            selected = await run.io_bound(pick_folder)
        except Exception as exc:
            safe_notify(str(exc), "negative")
            return
        if not selected:
            add_log(tr("picker_cancelled"))
            return
        save_workspace_path("destination" if role == "target" else "source", str(selected[0]))
        await run.io_bound(WORKBENCH_ADAPTER.remember_path, role, str(selected[0]))
        mark_workspace_feedback(role, "path")
        add_log(f"{'TARGET' if role == 'target' else 'SOURCE'} -> {selected[0]}")
        safe_notify(tr("target_selected") if role == "target" else tr("source_selected"), "positive")
        reload_ui()

    return handler


def workspace_single_file_click_handler():
    async def handler() -> None:
        if state["running"]:
            safe_notify(tr("another_running"), "warning")
            return
        try:
            selected = await run.io_bound(pick_single_file, "Select source file", "All files|*.*")
        except Exception as exc:
            add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
            safe_notify(str(exc), "negative")
            return
        if not selected:
            add_log(tr("picker_cancelled"))
            return
        path_value = str(selected)
        save_workspace_path("source", path_value)
        await run.io_bound(WORKBENCH_ADAPTER.remember_path, "source", path_value)
        mark_workspace_feedback("source", "path")
        add_log(f"SOURCE FILE -> {path_value}")
        reload_ui(150)

    return handler


def workspace_delete_both_click_handler():
    async def handler() -> None:
        if state["running"]:
            safe_notify(tr("another_running"), "warning")
            return
        source = current_source_path()
        target = current_target_path()
        with ui.dialog() as dialog, ui.card().classes("audion-dialog rounded-lg"):
            ui.label("Удалить содержимое I/O?" if settings.language == "ru" else "Delete I/O contents?").classes("text-base font-semibold")
            ui.label(
                "Будут удалены файлы ИСТОЧНИКА и НАЗНАЧЕНИЯ. Внешний ИСТОЧНИК может быть единственным экземпляром."
                if settings.language == "ru"
                else "SOURCE and TARGET files will be deleted. The external SOURCE may be the only copy."
            ).classes("text-sm text-gray-300")
            ui.label(f"SOURCE: {normalized_absolute_path(source)}").classes("max-w-3xl break-all font-mono text-xs text-gray-400")
            ui.label(f"TARGET: {normalized_absolute_path(target)}").classes("max-w-3xl break-all font-mono text-xs text-gray-400")
            with ui.row().classes("gap-2"):
                ui.button(tr("cancel"), on_click=dialog.close).props("dense flat")
                ui.button(tr("delete_io_short"), on_click=lambda: dialog.submit(True)).props("dense color=negative")
        if not await dialog:
            return
        state["running"] = True
        try:
            result = await run.io_bound(delete_workspace_io_contents, source, target)
            for role, path in (("source", source), ("target", target)):
                role_result = result.get(role, {})
                if role_result.get("kind") == "file":
                    await run.io_bound(WORKBENCH_ADAPTER.delete_path_history, role, str(path))
                    save_workspace_path("destination" if role == "target" else "source", "")
                add_log(f"Cleared {role.upper()}: {role_result.get('path')} [kind={role_result.get('kind')}, removed={role_result.get('removed', 0)}]")
            mark_workspace_feedback("source", "delete")
            reload_ui(150)
        except Exception as exc:
            add_log(f"ERROR: {exc.__class__.__name__}: {exc}")
            safe_notify(str(exc), "negative")
        finally:
            state["running"] = False

    return handler


WORKBENCH_RENDERER = WorkbenchRenderer(
    adapter=WORKBENCH_ADAPTER,
    handlers=WorkbenchHandlers(
        delete_path=workspace_delete_path_click_handler,
        pin_path=workspace_pin_click_handler,
        select_path=workspace_path_select_handler,
        pick_path=workspace_pick_click_handler,
        open_path=workspace_open_click_handler,
        add_file=workspace_single_file_click_handler,
        reset_paths=reset_workspace_paths_click_handler,
        delete_io=workspace_delete_both_click_handler,
        list_files=show_input_file_list,
    ),
    display_path_callback=display_path,
)


def toggle_language() -> None:
    settings.language = "en" if settings.language == "ru" else "ru"
    save_ui_settings(settings_path, settings)
    ui.run_javascript("window.location.reload()")


def folder_button(label: str, folder: Path) -> None:
    with ui.row().classes("w-full items-center gap-3"):
        ui.button(label, on_click=lambda item=folder: open_folder(item)).props("dense flat no-wrap").classes("audion-action w-20 rounded-lg")
        ui.label(str(folder)).classes("min-w-0 flex-1 truncate font-mono text-xs text-gray-300")


def terminal_cache() -> dict[str, Any]:
    cache = state.get("terminal_cache")
    if not isinstance(cache, dict):
        cache = load_terminal_cache()
        state["terminal_cache"] = cache
    cache["history"] = clean_terminal_commands(cache.get("history", []))
    cache["pinned"] = clean_terminal_commands(cache.get("pinned", []))
    return cache


def save_terminal_cache() -> None:
    cache = terminal_cache()
    cache["last"] = str(state.get("terminal_command") or "").strip()
    shell = str(state.get("terminal_shell") or ("pwsh" if os.name == "nt" else "sh")).strip().lower()
    if os.name == "nt":
        cache["shell"] = shell if shell in {"pwsh", "cmd"} else "pwsh"
    else:
        cache["shell"] = "sh"
    cache["cwd"] = stored_terminal_cwd(state.get("terminal_cwd"))
    TERMINAL_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    TERMINAL_HISTORY_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_terminal_command(command: str) -> None:
    command = command.strip()
    if not command:
        return
    cache = terminal_cache()
    history = [command, *[item for item in cache["history"] if item != command]]
    cache["history"] = history[:TERMINAL_HISTORY_LIMIT]
    state["terminal_command"] = command
    save_terminal_cache()


def terminal_command_options() -> dict[str, str]:
    cache = terminal_cache()
    pinned = clean_terminal_commands(cache.get("pinned", []))
    history = [item for item in clean_terminal_commands(cache.get("history", [])) if item not in pinned]
    last = str(cache.get("last") or "").strip()
    current = str(state.get("terminal_command") or "").strip()
    ordered = [*pinned]
    for command in (current, last, *history):
        if command and command not in ordered:
            ordered.append(command)
    options: dict[str, str] = {}
    for command in ordered[:TERMINAL_HISTORY_LIMIT]:
        options[command] = terminal_command_option_label(command, command in pinned)
    if not options:
        options[""] = tr("terminal_history_empty")
    return options


def terminal_command_option_label(command: str, pinned: bool = False) -> str:
    text = " ".join(str(command or "").split())
    if len(text) > 120:
        text = f"{text[:117]}..."
    return f"PIN {text}" if pinned else text


def terminal_history_value() -> str | None:
    options = terminal_command_options()
    current = str(state.get("terminal_command") or "").strip()
    last = str(terminal_cache().get("last") or "").strip()
    for command in (current, last):
        if command and command in options:
            return command
    if "" in options:
        return ""
    return None


def terminal_command_is_pinned() -> bool:
    command = str(state.get("terminal_command") or "").strip()
    return bool(command and command in terminal_cache().get("pinned", []))


def event_value(event: Any) -> Any:
    if hasattr(event, "value"):
        return event.value
    args = getattr(event, "args", None)
    if isinstance(args, list) and args:
        return args[0]
    if isinstance(args, dict):
        return args.get("value") or args.get("inputValue") or args.get("input")
    return args


def resolve_terminal_history_value(value: Any) -> str:
    value = event_value(value)
    if isinstance(value, dict):
        value = value.get("value") or value.get("label") or value.get("name") or ""
    if isinstance(value, list) and value:
        value = value[0]
    text = str(value or "").strip()
    options = terminal_command_options()
    if text in options:
        return text
    for command, label in options.items():
        if text == str(label).strip():
            return command
    return text


def set_terminal_command(value: Any) -> None:
    state["terminal_command"] = str(value or "").strip()
    save_terminal_cache()


def select_terminal_history(value: Any) -> None:
    set_terminal_command(resolve_terminal_history_value(value))
    terminal_command_bar.refresh()


def set_terminal_shell(value: Any) -> None:
    shell = str(value or ("pwsh" if os.name == "nt" else "sh")).strip().lower()
    if os.name == "nt":
        state["terminal_shell"] = shell if shell in {"pwsh", "cmd"} else "pwsh"
    else:
        state["terminal_shell"] = "sh"
    save_terminal_cache()


def set_terminal_cwd(value: Any) -> None:
    state["terminal_cwd"] = str(value or "").strip() or str(ROOT)
    save_terminal_cache()


def append_terminal_argument(value: Path | str) -> None:
    text = str(value)
    quoted = f'"{text}"' if any(char.isspace() for char in text) else text
    current = str(state.get("terminal_command") or "").rstrip()
    state["terminal_command"] = f"{current} {quoted}".strip() if current else quoted
    save_terminal_cache()
    terminal_command_bar.refresh()


def pin_terminal_command() -> None:
    command = str(state.get("terminal_command") or "").strip()
    if not command:
        safe_notify(tr("command_required"), "warning")
        return
    cache = terminal_cache()
    pinned = [item for item in cache["pinned"] if item != command]
    pinned.insert(0, command)
    cache["pinned"] = pinned[:TERMINAL_HISTORY_LIMIT]
    remember_terminal_command(command)
    terminal_command_bar.refresh()


def unpin_terminal_command() -> None:
    command = str(state.get("terminal_command") or "").strip()
    if not command:
        safe_notify(tr("command_required"), "warning")
        return
    cache = terminal_cache()
    cache["pinned"] = [item for item in cache["pinned"] if item != command]
    remember_terminal_command(command)
    terminal_command_bar.refresh()


def clear_terminal_history() -> None:
    cache = terminal_cache()
    cache["history"] = [item for item in cache["history"] if item in cache["pinned"]]
    cache["last"] = ""
    state["terminal_command"] = ""
    save_terminal_cache()
    safe_notify(tr("history_cleared"), "positive")
    terminal_command_bar.refresh()


def clear_terminal_command_cache() -> None:
    cache = terminal_cache()
    cache["history"] = []
    cache["pinned"] = []
    cache["last"] = ""
    state["terminal_command"] = ""
    save_terminal_cache()
    safe_notify(tr("command_cache_cleared"), "positive")
    terminal_command_bar.refresh()


async def pick_terminal_location(kind: str) -> None:
    try:
        if kind == "file":
            selected = await run.io_bound(pick_single_file, "Select terminal file", "All files|*.*")
        else:
            picked = await run.io_bound(pick_folder, "Select terminal folder", True)
            selected = picked[0] if picked else None
    except Exception as exc:
        safe_notify(str(exc), "negative")
        return
    if selected is None:
        safe_notify(tr("picker_cancelled"), "warning")
        return
    if selected.is_file():
        set_terminal_cwd(str(selected.parent))
        append_terminal_argument(selected)
    else:
        set_terminal_cwd(str(selected))
    terminal_command_bar.refresh()


def terminal_location_click_handler(kind: str):
    async def handler() -> None:
        await pick_terminal_location(kind)

    return handler


async def start_terminal_command() -> None:
    command = str(state.get("terminal_command") or "").strip()
    if not command:
        safe_notify(tr("command_required"), "warning")
        return
    remember_terminal_command(command)
    terminal_command_bar.refresh()
    shell = str(state.get("terminal_shell") or ("pwsh" if os.name == "nt" else "sh")).strip().lower()
    cwd = str(state.get("terminal_cwd") or ROOT).strip()
    operation = Operation(
        id="terminal_command",
        title="Terminal command",
        title_ru="Команда терминала",
        description=command,
        description_ru=command,
        service="system_core.services.winget_service:terminal_command",
        kind="safe",
        parameters={"command": command, "shell": shell, "cwd": cwd},
    )
    await start_operation(operation)


async def terminal_enter_handler(_event: Any = None) -> None:
    await start_terminal_command()


def operation_to_command_node(operation: Operation) -> CommandNode:
    return CommandNode(
        id=operation.id,
        title=operation.title,
        description=operation.description,
        service=operation.service,
        kind=operation.kind,
        title_ru=operation.title_ru,
        description_ru=operation.description_ru,
        parameters=dict(operation.parameters),
        fields=operation.fields,
    )


def maintenance_command_node() -> CommandNode | None:
    if not manifest.maintenance_operations:
        return None
    return CommandNode(
        id="maintenance",
        title="Service procedures",
        title_ru="Служебные процедуры",
        description="Health checks, list validation, and managed cleanup actions.",
        description_ru="Health/Doctor, проверка списков и управляемая очистка.",
        children=tuple(operation_to_command_node(operation) for operation in manifest.maintenance_operations),
    )


def ordered_root_command_nodes(nodes: list[CommandNode]) -> list[CommandNode]:
    return [
        node
        for _index, node in sorted(
            enumerate(nodes),
            key=lambda item: (ROOT_COMMAND_PRIORITY.get(item[1].id, 1000 + item[0]), item[0]),
        )
    ]


def root_uninstall_selected_node(node: CommandNode) -> CommandNode:
    return CommandNode(
        id=node.id,
        title="Uninstall selected",
        title_ru="Удалить выбранные",
        description=(
            "Load installed WinGet packages into thematic blocks and uninstall checked packages. "
            "Protected system/runtime packages require an extra checkbox."
        ),
        description_ru=(
            "Найти установленные WinGet-пакеты, разложить по группам и удалить отмеченные пакеты. "
            "Защищённые системные/рантайм пакеты требуют отдельного флажка."
        ),
        service=node.service,
        kind=node.kind,
        parameters=dict(node.parameters),
        fields=node.fields,
    )


def promote_root_command_nodes(nodes: list[CommandNode]) -> list[CommandNode]:
    promoted_node: CommandNode | None = None
    root_nodes: list[CommandNode] = []

    for node in nodes:
        if node.id == ROOT_UNINSTALL_SELECTED_ID:
            root_nodes.append(root_uninstall_selected_node(node))
            continue

        if node.id != "single_package":
            root_nodes.append(node)
            continue

        children: list[CommandNode] = []
        for child in node.children:
            if child.id == ROOT_UNINSTALL_SELECTED_ID:
                promoted_node = root_uninstall_selected_node(child)
            else:
                children.append(child)
        root_nodes.append(
            CommandNode(
                id=node.id,
                title=node.title,
                description=node.description,
                tooltip=node.tooltip,
                service=node.service,
                kind=node.kind,
                title_ru=node.title_ru,
                description_ru=node.description_ru,
                tooltip_ru=node.tooltip_ru,
                parameters=dict(node.parameters),
                fields=node.fields,
                children=tuple(children),
            )
        )

    if promoted_node and not any(node.id == promoted_node.id for node in root_nodes):
        root_nodes.append(promoted_node)
    return root_nodes


def root_command_nodes() -> list[CommandNode]:
    if manifest.operation_groups:
        nodes = list(manifest.operation_groups)
    else:
        nodes = [operation_to_command_node(operation) for operation in manifest.operations]
    nodes = promote_root_command_nodes(nodes)

    maintenance_node = maintenance_command_node()
    if maintenance_node and not any(node.id == maintenance_node.id for node in nodes):
        nodes.append(maintenance_node)
    return ordered_root_command_nodes(nodes)


def current_command_level() -> tuple[list[CommandNode], list[CommandNode]]:
    trail: list[CommandNode] = []
    nodes = root_command_nodes()
    for node_id in list(state.get("command_path", [])):
        node = next((candidate for candidate in nodes if candidate.id == node_id), None)
        if node is None:
            state["command_path"] = []
            state["pending_command"] = None
            return [], root_command_nodes()
        trail.append(node)
        nodes = list(node.children)
    return trail, nodes


def enter_command_node(node: CommandNode) -> None:
    state["pending_command"] = None
    state["command_path"] = [*state.get("command_path", []), node.id]
    command_tree.refresh()


def select_command_node(node: CommandNode) -> None:
    previous = state.get("pending_command")
    if not isinstance(previous, CommandNode) or previous.id != node.id:
        state["checkbox_filters"] = {}
    state["pending_command"] = node
    command_tree.refresh()


def command_node_quick_run(node: CommandNode) -> bool:
    parameters = dict(getattr(node, "parameters", {}) or {})
    return bool(parameters.get("quick_run") or parameters.get("gui_quick_run"))


def single_leaf_child(node: CommandNode) -> CommandNode | None:
    if len(node.children) != 1:
        return None
    child = node.children[0]
    return None if child.children else child


def command_node_runs_immediately(node: CommandNode) -> bool:
    child = single_leaf_child(node)
    if child is not None:
        return command_node_runs_immediately(child)
    if node.children:
        return False
    return command_node_quick_run(node) or not node.fields


async def activate_command_node(node: CommandNode) -> None:
    child = single_leaf_child(node)
    if child is not None:
        await activate_command_node(child)
        return
    if node.children:
        enter_command_node(node)
        return
    if command_node_quick_run(node):
        state["pending_command"] = None
        await start_operation(node.to_operation(dict(node.parameters)))
        return
    if node.fields:
        select_command_node(node)
        return
    state["pending_command"] = None
    await start_operation(node.to_operation(dict(node.parameters)))


def command_click_handler(node: CommandNode):
    async def handler() -> None:
        await activate_command_node(node)

    return handler


def go_back_command() -> None:
    if state.get("pending_command") is not None:
        state["pending_command"] = None
    else:
        path = list(state.get("command_path", []))
        if path:
            path.pop()
        state["command_path"] = path
    command_tree.refresh()


def field_id(field: dict[str, Any]) -> str:
    return str(field.get("id") or field.get("name") or "").strip()


def field_label(field: dict[str, Any]) -> str:
    language = settings.language
    if language == "ru" and field.get("label_ru"):
        return str(field["label_ru"])
    return str(field.get("label") or field.get("title") or field_id(field))


def field_hint(field: dict[str, Any]) -> str:
    language = settings.language
    if language == "ru" and field.get("hint_ru"):
        return str(field["hint_ru"])
    return str(field.get("hint") or "")


def field_tooltip(field: dict[str, Any]) -> str:
    language = settings.language
    if language == "ru":
        for key in ("tooltip_ru", "hint_ru", "description_ru"):
            if field.get(key):
                return str(field[key])
    for key in ("tooltip", "hint", "description"):
        if field.get(key):
            return str(field[key])
    return ""


def attach_tooltip(element: Any, text: str) -> Any:
    clean_text = str(text or "").strip()
    if clean_text:
        element.tooltip(clean_text)
    return element


def field_default(field: dict[str, Any]) -> Any:
    if "default" in field:
        return field["default"]
    kind = str(field.get("type", field.get("kind", "text"))).lower()
    options = field.get("options", [])
    if kind in {"checkboxes", "multi_checkbox", "multicheckbox", "multi-select", "multiselect"}:
        if not isinstance(options, list):
            return []
        selected: list[Any] = []
        for option in options:
            if isinstance(option, dict) and option.get("default", False):
                selected.append(option.get("value", option.get("id", option.get("label"))))
        return selected
    if isinstance(options, list) and options:
        first = options[0]
        if isinstance(first, dict):
            return first.get("value", first.get("id", ""))
        return first
    return ""


def current_field_value(field: dict[str, Any]) -> Any:
    key = field_id(field)
    values = state.setdefault("field_values", {})
    if key not in values:
        values[key] = field_default(field)
    return values[key]


# One window can now show the same field id in several inline commands: a typed
# WinGet ID feeds install, uninstall and "add to list" alike. The value is shared,
# so every visible copy has to follow the control the user typed into.
field_controls: dict[str, list[Any]] = {}


def reset_field_controls() -> None:
    field_controls.clear()


def register_field_control(key: str, control: Any) -> None:
    if key:
        field_controls.setdefault(key, []).append(control)


def mirror_field_value(key: str, value: Any) -> None:
    for control in field_controls.get(key, []):
        try:
            if control.value == value:
                continue
            control.set_value(value)
        except Exception:
            continue


def set_field_value(key: str, value: Any) -> None:
    state.setdefault("field_values", {})[key] = value
    mirror_field_value(key, value)


def adjusted_number_value(field: dict[str, Any], current: Any, direction: int) -> int | float:
    step_raw = field.get("step", 1)
    try:
        step = float(step_raw)
    except (TypeError, ValueError):
        step = 1.0

    seed = current
    if seed is None or seed == "":
        seed = field_default(field) or 0
    try:
        value = float(seed)
    except (TypeError, ValueError):
        value = 0.0

    value += step * (1 if direction > 0 else -1)
    for bound_key, clamp in (("min", max), ("max", min)):
        bound = field.get(bound_key)
        if bound is None or bound == "":
            continue
        try:
            value = clamp(value, float(bound))
        except (TypeError, ValueError):
            continue

    kind = str(field.get("type", field.get("kind", "number"))).lower()
    integer_like = kind in {"number", "int", "integer"} and float(step).is_integer()
    return int(round(value)) if integer_like else round(value, 6)


def spin_number_field(key: str, field: dict[str, Any], control: Any, direction: int) -> None:
    value = adjusted_number_value(field, state.setdefault("field_values", {}).get(key), direction)
    set_field_value(key, value)
    control.set_value(value)


def dynamic_option_source(field: dict[str, Any]) -> str:
    configured = str(field.get("options_source") or field.get("source") or "").strip()
    if configured:
        return configured

    pending = state.get("pending_command")
    pending_id = pending.id if isinstance(pending, CommandNode) else ""
    suffix = PACKAGE_FIELD_SUFFIXES.get(field_id(field))
    if not suffix:
        return ""
    if pending_id == "install_selected_packages":
        return f"system_core.services.winget_service:missing_install_{suffix}_options"
    if pending_id == "update_selected_packages":
        return f"system_core.services.winget_service:available_update_{suffix}_options"
    return ""


def refresh_dynamic_options(field: dict[str, Any]) -> None:
    source = dynamic_option_source(field)
    if source:
        dynamic_option_cache.pop(source, None)
    key = field_id(field)
    if key:
        state.setdefault("field_values", {}).pop(key, None)
    command_tree.refresh()


def refresh_options_click_handler(field: dict[str, Any]):
    def handler() -> None:
        refresh_dynamic_options(field)

    return handler


def clear_ai_dynamic_options() -> None:
    for source in AI_OPTION_SOURCES:
        clear_dynamic_option_cache(source)


def ai_provider_for_key(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if normalized.startswith("openai_"):
        return "openai"
    if normalized.startswith("gemini_"):
        return "gemini"
    return str(state.setdefault("field_values", {}).get("provider") or "openai").strip().lower() or "openai"


def ai_provider_for_field(field: dict[str, Any]) -> str:
    return ai_provider_for_key(field_id(field))


def select_change_handler(field: dict[str, Any]):
    def handler(event: Any) -> None:
        key = field_id(field)
        value = getattr(event, "value", None)
        set_field_value(key, value)
        if key == "provider":
            clear_ai_dynamic_options()
            command_tree.refresh()
            return
        if key == "ai_prompt_ref":
            load_ai_prompt_ref(value)
            command_tree.refresh()

    return handler


def load_ai_prompt_ref(prompt_ref: Any) -> None:
    ref = str(prompt_ref or "").strip()
    if not ref or ref.startswith("__"):
        return
    try:
        from system_core.services.winget_ai_service import prompt_cache_entry

        entry = prompt_cache_entry(ref)
    except Exception as exc:
        safe_notify(str(exc), "negative")
        return
    if not entry:
        safe_notify("Prompt was not found in cache.", "warning")
        return
    values = state.setdefault("field_values", {})
    values["ai_prompt"] = str(entry.get("content") or "")
    values["ai_prompt_label"] = str(entry.get("label") or "")
    values["ai_prompt_note"] = str(entry.get("note") or "")


def ai_control_operation(mode: str, parameters: dict[str, Any] | None = None) -> Operation:
    values = state.setdefault("field_values", {})
    provider = str((parameters or {}).get("provider") or values.get("provider") or "openai").strip().lower() or "openai"
    params: dict[str, Any] = {"provider": provider}
    if mode in {"pin_api_key", "unpin_api_key"}:
        params[f"{provider}_api_key_ref"] = values.get(f"{provider}_api_key_ref", "")
    elif mode in {"pin_model", "unpin_model", "delete_model", "check_model"}:
        params[f"{provider}_api_key_ref"] = values.get(f"{provider}_api_key_ref", "")
        params[f"{provider}_model"] = values.get(f"{provider}_model", "")
        params[f"{provider}_model_override"] = values.get(f"{provider}_model_override", "")
        if provider == "openai":
            params["openai_reasoning"] = values.get("openai_reasoning", "low")
    elif mode in {"save_prompt", "pin_prompt", "unpin_prompt", "delete_prompt"}:
        params["ai_prompt_ref"] = values.get("ai_prompt_ref", "")
        params["ai_prompt"] = values.get("ai_prompt", "")
        params["ai_prompt_label"] = values.get("ai_prompt_label", "")
        params["ai_prompt_note"] = values.get("ai_prompt_note", "")
    params.update(parameters or {})
    params["mode"] = mode
    return Operation(
        id=f"ai_control_{mode}",
        title=f"AI control: {mode}",
        title_ru=f"AI control: {mode}",
        description=f"AI package planner control action: {mode}",
        description_ru=f"AI package planner control action: {mode}",
        service="system_core.services.winget_ai_service:run_ai_package_control",
        kind="safe",
        parameters=params,
    )


async def run_ai_control(mode: str, parameters: dict[str, Any] | None = None) -> None:
    await start_operation(ai_control_operation(mode, parameters))
    clear_ai_dynamic_options()
    command_tree.refresh()


def ai_control_click_handler(mode: str, parameters: dict[str, Any] | None = None):
    async def handler() -> None:
        await run_ai_control(mode, parameters)

    return handler


def clear_ai_prompt_editor() -> None:
    values = state.setdefault("field_values", {})
    values["ai_prompt_ref"] = ""
    values["ai_prompt"] = ""
    values["ai_prompt_label"] = ""
    values["ai_prompt_note"] = ""
    safe_notify(tr("prompt_cleared"), "positive")
    command_tree.refresh()


def ai_select_action_items(field: dict[str, Any]) -> list[dict[str, str]]:
    key = field_id(field)
    provider = ai_provider_for_field(field)
    if key in {"openai_api_key_ref", "gemini_api_key_ref"}:
        return [
            {
                "mode": "pin_api_key",
                "icon": "push_pin",
                "label": tr("ai_pin"),
                "tooltip": l10n("Закрепить выбранный API key для этого провайдера.", "Pin the selected API key for this provider."),
                "provider": provider,
            },
            {
                "mode": "unpin_api_key",
                "icon": "block",
                "label": tr("ai_unpin"),
                "tooltip": l10n("Снять закрепление API key, не удаляя сам ключ.", "Unpin the API key without deleting key material."),
                "provider": provider,
            },
        ]
    if key in {"openai_model", "gemini_model"}:
        return [
            {
                "mode": "pin_model",
                "icon": "push_pin",
                "label": tr("ai_pin"),
                "tooltip": l10n("Закрепить выбранную модель в быстром списке.", "Pin the selected model in the quick list."),
                "provider": provider,
            },
            {
                "mode": "unpin_model",
                "icon": "block",
                "label": tr("ai_unpin"),
                "tooltip": l10n("Снять закрепление модели, оставив её в кэше.", "Unpin the model while keeping it in cache."),
                "provider": provider,
            },
            {
                "mode": "delete_model",
                "icon": "delete",
                "label": tr("ai_delete"),
                "tooltip": l10n("Удалить выбранную модель из локального кэша.", "Delete the selected model from the local cache."),
                "provider": provider,
            },
        ]
    if key == "ai_prompt_ref":
        return [
            {
                "mode": "save_prompt",
                "icon": "save",
                "label": tr("ai_save"),
                "tooltip": l10n("Сохранить текст редактора в кэш prompt.", "Save the editor text to the prompt cache."),
                "provider": provider,
            },
            {
                "mode": "pin_prompt",
                "icon": "push_pin",
                "label": tr("ai_pin"),
                "tooltip": l10n("Закрепить выбранный prompt вверху списка.", "Pin the selected prompt at the top of the list."),
                "provider": provider,
            },
            {
                "mode": "unpin_prompt",
                "icon": "block",
                "label": tr("ai_unpin"),
                "tooltip": l10n("Снять закрепление prompt, не удаляя его.", "Unpin the prompt without deleting it."),
                "provider": provider,
            },
            {
                "mode": "delete_prompt",
                "icon": "delete",
                "label": tr("ai_delete"),
                "tooltip": l10n("Удалить выбранный prompt из кэша.", "Delete the selected prompt from cache."),
                "provider": provider,
            },
        ]
    return []


def render_ai_select_action_button(item: dict[str, str]) -> None:
    mode = item["mode"]
    parameters = {"provider": item.get("provider", "")} if item.get("provider") else {}
    button = ui.button(
        icon=item["icon"],
        on_click=ai_control_click_handler(mode, parameters),
    ).props(f'dense flat round aria-label="{item["label"]}"').classes("audion-action audion-field-icon-button")
    button.tooltip(item.get("tooltip") or item["label"])


def render_dynamic_refresh_icon(field: dict[str, Any]) -> None:
    button = ui.button(
        icon="sync",
        on_click=refresh_options_click_handler(field),
    ).props(f'dense flat round aria-label="{tr("ai_refresh")}"').classes("audion-action audion-field-icon-button")
    button.tooltip(tr("refresh_options"))


def _dynamic_option_error(exc: Exception) -> list[dict[str, str]]:
    message = f"Option source failed: {exc.__class__.__name__}: {exc}"
    return [{"value": "", "label": message, "label_ru": message}]


def loading_options_label(field: dict[str, Any], source: str = "") -> str:
    source = source or dynamic_option_source(field)
    if "available_update_" in source or source.endswith(":available_update_options"):
        return tr("loading_update_options")
    if "missing_install_" in source or "installed_uninstall_" in source:
        return tr("loading_installed_options")
    return tr("loading_options")


def _dynamic_option_loading(field: dict[str, Any], source: str = "") -> list[dict[str, str]]:
    message = loading_options_label(field, source)
    return [{"value": "", "label": message, "label_ru": message}]


def resolve_dynamic_options(source: str, values: dict[str, Any] | None = None) -> list[Any]:
    if ":" not in source:
        raise RuntimeError(f"Dynamic option source must use module:function syntax: {source}")
    module_name, function_name = source.split(":", 1)
    module = importlib.import_module(module_name)
    provider = getattr(module, function_name)
    try:
        params = inspect.signature(provider).parameters
        if len(params) >= 2:
            options = provider(ROOT, dict(values or {}))
        elif len(params) == 1:
            options = provider(ROOT)
        else:
            options = provider()
    except (TypeError, ValueError):
        try:
            options = provider(ROOT)
        except TypeError:
            options = provider()
    if not isinstance(options, list):
        raise RuntimeError(f"Dynamic option source returned {type(options).__name__}, expected list.")
    return options


async def resolve_dynamic_options_background(source: str, values: dict[str, Any] | None = None) -> None:
    try:
        options = await run.io_bound(resolve_dynamic_options, source, values)
    except Exception as exc:
        options = _dynamic_option_error(exc)
    dynamic_option_cache[source] = (time.monotonic(), options)
    dynamic_option_tasks.discard(source)
    try:
        command_tree.refresh()
    except RuntimeError as exc:
        message = str(exc)
        if "slot belongs to has been deleted" not in message and "current slot cannot be determined" not in message:
            raise


def schedule_dynamic_options(source: str) -> None:
    if source in dynamic_option_tasks:
        return
    dynamic_option_tasks.add(source)
    values = dict(state.get("field_values", {}))
    try:
        asyncio.get_running_loop().create_task(resolve_dynamic_options_background(source, values))
    except RuntimeError:
        try:
            options = resolve_dynamic_options(source, values)
        except Exception as exc:
            options = _dynamic_option_error(exc)
        dynamic_option_cache[source] = (time.monotonic(), options)
        dynamic_option_tasks.discard(source)


def apply_preset(preset: dict[str, Any]) -> None:
    values = preset.get("values", {})
    if not isinstance(values, dict):
        return
    field_values = state.setdefault("field_values", {})
    for key, value in values.items():
        field_values[str(key)] = value
    command_tree.refresh()


def preset_label(preset: dict[str, Any]) -> str:
    if settings.language == "ru" and preset.get("label_ru"):
        return str(preset["label_ru"])
    return str(preset.get("label") or preset.get("title") or preset.get("id") or "Preset")


def preset_click_handler(preset: dict[str, Any]):
    def handler() -> None:
        apply_preset(preset)

    return handler


def load_dynamic_options(field: dict[str, Any]) -> list[Any]:
    source = dynamic_option_source(field)
    if not source:
        return []

    cache_seconds = float(field.get("cache_seconds", 45) or 0)
    now = time.monotonic()
    cached = dynamic_option_cache.get(source)
    if cached and cache_seconds > 0 and now - cached[0] < cache_seconds:
        return cached[1]

    schedule_dynamic_options(source)
    return cached[1] if cached else _dynamic_option_loading(field, source)


def field_options(field: dict[str, Any]) -> list[Any]:
    if dynamic_option_source(field):
        return load_dynamic_options(field)
    options = field.get("options", [])
    return options if isinstance(options, list) else []


def select_options(field: dict[str, Any]) -> dict[Any, str] | list[Any]:
    options = field_options(field)
    if all(isinstance(option, dict) for option in options):
        result: dict[Any, str] = {}
        html_labels = select_options_have_pins(options)
        for option in options:
            value = option.get("value", option.get("id", ""))
            if settings.language == "ru" and option.get("label_ru"):
                label = str(option["label_ru"])
            else:
                label = str(option.get("label") or option.get("title") or value)
            if html_labels:
                escaped = html.escape(label, quote=False)
                if option_is_pinned(option):
                    label = f'<span class="material-icons audion-select-pin-icon">push_pin</span><span>{escaped}</span>'
                else:
                    label = escaped
            result[value] = label
        return result
    return options


def option_is_pinned(option: dict[str, Any]) -> bool:
    return str(option.get("pinned") or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def select_options_have_pins(options: list[Any]) -> bool:
    return any(isinstance(option, dict) and option_is_pinned(option) for option in options)


def field_select_uses_html(field: dict[str, Any]) -> bool:
    return select_options_have_pins(field_options(field))


def option_value(option: Any) -> Any:
    if isinstance(option, dict):
        return option.get("value", option.get("id", option.get("label", "")))
    return option


def option_label(option: Any) -> str:
    if not isinstance(option, dict):
        return str(option)
    language = settings.language
    if language == "ru" and option.get("label_ru"):
        return str(option["label_ru"])
    return str(option.get("label") or option.get("title") or option_value(option))


def checkbox_options(field: dict[str, Any]) -> list[tuple[Any, str]]:
    options = field_options(field)
    return [(option_value(option), option_label(option)) for option in options]


def empty_options_label(field: dict[str, Any], source: str = "") -> str:
    source = source or dynamic_option_source(field)
    if "available_update_" in source or source.endswith(":available_update_options"):
        return tr("no_updates")
    if "missing_install_" in source:
        return tr("all_installed")
    return tr("no_options")


def is_checkbox_group(field: dict[str, Any]) -> bool:
    kind = str(field.get("type", field.get("kind", "text"))).lower()
    return kind in {"checkboxes", "multi_checkbox", "multicheckbox", "multi-select", "multiselect"}


MARKDOWN_EDITOR_KINDS = {"markdown_editor", "markdown", "md_editor", "codemirror", "code_editor"}


AI_OPTION_SOURCES = {
    "system_core.services.winget_ai_service:openai_api_key_options",
    "system_core.services.winget_ai_service:gemini_api_key_options",
    "system_core.services.winget_ai_service:openai_model_options",
    "system_core.services.winget_ai_service:gemini_model_options",
    "system_core.services.winget_ai_service:ai_package_prompt_options",
    "system_core.services.winget_ai_service:ai_plan_package_options",
}


def bool_field_option(field: dict[str, Any], key: str, default: bool = False) -> bool:
    value = field.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def codemirror_theme_for_field(field: dict[str, Any]) -> str:
    if active_theme_mode() == "dark":
        return str(field.get("theme_dark") or field.get("editor_theme_dark") or field.get("theme") or "vscodeDark")
    return str(field.get("theme_light") or field.get("editor_theme_light") or field.get("theme") or "vscodeLight")


def checkbox_fields(node: CommandNode) -> list[dict[str, Any]]:
    return [field for field in node.fields if is_checkbox_group(field)]


def normalize_selected_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [item for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [item for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def checkbox_grid_min_px(field: dict[str, Any]) -> int:
    raw_value = field.get("grid_min_px", field.get("checkbox_min_px", ""))
    try:
        configured = int(raw_value)
    except (TypeError, ValueError):
        configured = 0

    options = checkbox_options(field)
    longest_label = max((len(label) for _value, label in options), default=0)
    inferred = 180
    if longest_label > 24:
        inferred = 220
    if longest_label > 36:
        inferred = 260
    width = max(160, min(340, configured or inferred))
    # The icon buttons live inside the card, so the card needs room for them
    # before the package name starts wrapping. A group where some package also
    # has an archive build carries three icons instead of two.
    package_ids = [package_card_option_id(field, value) for value, _label in options]
    if any(package_ids):
        width = min(380, width + PACKAGE_CARD_ACTIONS_PX)
    if any(package_id and package_archive_type(package_id) for package_id in package_ids):
        width = min(390, width + PACKAGE_CARD_ARCHIVE_PX)
    return width


def checkbox_grid_style(field: dict[str, Any]) -> str:
    min_px = checkbox_grid_min_px(field)
    return f"grid-template-columns: repeat(auto-fit, minmax({min_px}px, 1fr));"


def package_field_suffix(field: dict[str, Any]) -> str:
    key = field_id(field).strip().lower()
    if key in PACKAGE_FIELD_SUFFIXES:
        return PACKAGE_FIELD_SUFFIXES[key]
    if key.startswith("packages_"):
        return key.removeprefix("packages_")
    if key.startswith("uninstall_"):
        return key.removeprefix("uninstall_")
    return key


def package_keyword_matches(text_raw: str, text_words: str, keyword: str) -> bool:
    needle_raw = keyword.casefold()
    needle_words = needle_raw.replace("_", " ").replace("-", " ").replace(".", " ")
    return needle_raw in text_raw or needle_words in text_words


def package_checkbox_tone(field: dict[str, Any], option_key: Any, option_text: str) -> str:
    text_raw = f"{option_key} {option_text}".casefold()
    text_words = text_raw.replace("_", " ").replace("-", " ").replace(".", " ")
    for tone, keywords in PACKAGE_TONE_KEYWORDS:
        if any(package_keyword_matches(text_raw, text_words, keyword) for keyword in keywords):
            return tone
    return PACKAGE_FIELD_DEFAULT_TONES.get(package_field_suffix(field), "default")


def checkbox_card_classes(field: dict[str, Any], option_key: Any, option_text: str) -> str:
    tone = package_checkbox_tone(field, option_key, option_text)
    return f"audion-checkbox-card audion-package-tone-{tone}"


PACKAGE_CARD_FIELD_PREFIX = "packages_"
PACKAGE_CARD_WINDOWS_FEATURE_PREFIX = "windows-feature:"
# The card stretches to the column anyway; this only nudges the wrap threshold
# so the icon pair does not squeeze the name out at the narrowest layout.
PACKAGE_CARD_ACTIONS_PX = 28
PACKAGE_CARD_ARCHIVE_PX = 14


def package_card_option_id(field: dict[str, Any], option_key: Any) -> str:
    """The WinGet id behind a checkbox, or `''` when the option is not one.

    Some `packages_*` fields carry group names (`system`, `dev`) instead of ids,
    and a Windows optional feature has nothing to download, so the option value
    itself is the test rather than the field it lives in.
    """
    if not field_id(field).strip().lower().startswith(PACKAGE_CARD_FIELD_PREFIX):
        return ""
    package_id = str(option_key or "").strip()
    if not package_id or " " in package_id or "." not in package_id:
        return ""
    if package_id.lower().startswith(PACKAGE_CARD_WINDOWS_FEATURE_PREFIX):
        return ""
    return package_id


def package_download_operation(package_id: str) -> Operation:
    return Operation(
        id=f"package_download_{package_id}",
        title=tr("package_download_title", package=package_id),
        description=tr("package_download_hint"),
        service="system_core.services.winget_service:download_package",
        kind="safe",
        parameters={"download_package_id": package_id},
    )


def package_download_click_handler(package_id: str):
    async def handler(_event: Any = None) -> None:
        await start_operation(package_download_operation(package_id))
        command_tree.refresh()

    return handler


def package_archive_operation(package_id: str) -> Operation:
    return Operation(
        id=f"package_archive_{package_id}",
        title=tr("package_archive_title", package=package_id),
        description=tr("package_archive_hint"),
        service="system_core.services.winget_service:download_package_archive",
        kind="safe",
        parameters={"download_package_id": package_id},
    )


def package_archive_click_handler(package_id: str):
    async def handler(_event: Any = None) -> None:
        await start_operation(package_archive_operation(package_id))
        command_tree.refresh()

    return handler


def open_external_page(url: str) -> None:
    """Vendor pages open in the system browser, not in the application shell."""
    url = str(url or "").strip()
    if not url:
        return
    webbrowser.open(url)
    safe_notify(url, "positive")


def package_page_click_handler(package_id: str):
    async def handler(_event: Any = None) -> None:
        from system_core.services.winget_service import package_page_link

        safe_notify(tr("package_page_lookup", package=package_id), "info")
        try:
            link = await run.io_bound(package_page_link, package_id)
        except Exception as exc:
            safe_notify(str(exc), "negative")
            return
        url = str((link or {}).get("url") or "")
        if not url:
            safe_notify(tr("package_page_missing", package=package_id), "warning")
            return
        open_external_page(url)

    return handler


def package_card_actions(field: dict[str, Any], option_key: Any) -> None:
    """Small buttons inside the package card: the file, the archive, the page.

    They live in the card so the group keeps following its package through every
    reflow of the checkbox grid, and they sit outside the checkbox label so a
    click never toggles the selection. The archive button appears only for the
    packages WinGet really has an archive for.
    """
    package_id = package_card_option_id(field, option_key)
    if not package_id:
        return
    with ui.row().classes("audion-package-card-actions items-center flex-nowrap"):
        download_button = ui.button(
            icon="file_download",
            on_click=package_download_click_handler(package_id),
        ).props("dense flat round unelevated").classes(
            "audion-package-icon-button audion-package-icon-download"
        )
        attach_tooltip(download_button, f"{package_id} — {tr('package_download_hint')}")
        if package_archive_type(package_id):
            archive_button = ui.button(
                icon="archive",
                on_click=package_archive_click_handler(package_id),
            ).props("dense flat round unelevated").classes(
                "audion-package-icon-button audion-package-icon-archive"
            )
            attach_tooltip(archive_button, f"{package_id} — {tr('package_archive_hint')}")
        page_button = ui.button(
            icon="open_in_new",
            on_click=package_page_click_handler(package_id),
        ).props("dense flat round unelevated").classes(
            "audion-package-icon-button audion-package-icon-page"
        )
        attach_tooltip(page_button, f"{package_id} — {tr('package_page_hint')}")


def checkbox_filter_value(key: str) -> str:
    filters = state.setdefault("checkbox_filters", {})
    if not isinstance(filters, dict):
        filters = {}
        state["checkbox_filters"] = filters
    return str(filters.get(key, "") or "").strip()


def set_checkbox_filter_value(key: str, value: Any) -> None:
    filters = state.setdefault("checkbox_filters", {})
    if not isinstance(filters, dict):
        filters = {}
        state["checkbox_filters"] = filters
    text = str(value or "").strip()
    if text:
        filters[key] = text
    else:
        filters.pop(key, None)
    command_tree.refresh()


WINDOW_CHECKBOX_FILTER_KEY = "__window__"


def checkbox_window_filter_value() -> str:
    return checkbox_filter_value(WINDOW_CHECKBOX_FILTER_KEY)


def set_checkbox_window_filter_value(value: Any) -> None:
    set_checkbox_filter_value(WINDOW_CHECKBOX_FILTER_KEY, value)


def field_uses_local_checkbox_filter(field: dict[str, Any]) -> bool:
    key = field_id(field)
    source = str(field.get("options_source") or field.get("source") or "")
    return key == "uninstall_other" or source.endswith(":installed_uninstall_other_options")


def filter_checkbox_options(options: list[tuple[Any, str]], query: str) -> list[tuple[Any, str]]:
    needle = query.casefold()
    if not needle:
        return options
    filtered: list[tuple[Any, str]] = []
    for option_key, option_text in options:
        if not str(option_key).strip():
            filtered.append((option_key, option_text))
            continue
        haystack = f"{option_text} {option_key}".casefold()
        if needle in haystack:
            filtered.append((option_key, option_text))
    return filtered


def checkbox_filter_count(fields: list[dict[str, Any]], query: str) -> tuple[int, int]:
    total = 0
    visible = 0
    for field in fields:
        options = checkbox_options(field)
        selectable = [(option_key, option_text) for option_key, option_text in options if str(option_key).strip()]
        total += len(selectable)
        visible += len(filter_checkbox_options(selectable, query)) if query else len(selectable)
    return visible, total


def render_checkbox_filter_action(node: CommandNode) -> None:
    button = ui.button(
        node.display_title(settings.language),
        on_click=command_click_handler(node),
    ).props("dense flat no-wrap no-caps").classes(
        "audion-action audion-checkbox-filter-action audion-operation-action-button rounded-lg"
    )
    attach_tooltip(button, node.display_description(settings.language) or node.display_title(settings.language))


def render_checkbox_window_filter(fields: list[dict[str, Any]], actions: list[CommandNode] | None = None) -> None:
    if not fields:
        return
    query = checkbox_window_filter_value()
    visible_count, total_count = checkbox_filter_count(fields, query)
    # The filter input and its actions live inside a panel: no field or select
    # is left floating on the bare background.
    with ui.element("section").classes("audion-field-section audion-window-filter-group"):
        ui.label(tr("parameters")).classes("audion-section-title")
        with ui.row().classes("audion-checkbox-window-filter-row w-full items-center gap-2 flex-nowrap"):
            ui.input(
                label=tr("checkbox_filter"),
                value=query,
                placeholder=tr("checkbox_filter_placeholder"),
                on_change=lambda event: set_checkbox_window_filter_value(event.value),
            ).props("dense outlined clearable debounce=250").classes("audion-checkbox-filter audion-checkbox-window-filter")
            ui.label(
                tr("checkbox_filter_count", visible=visible_count, total=total_count)
            ).classes("audion-checkbox-filter-count")
            if actions:
                with ui.row().classes("audion-checkbox-filter-actions items-center gap-2"):
                    for action in actions:
                        render_checkbox_filter_action(action)


def render_parameters_header(fields: list[dict[str, Any]], actions: list[CommandNode] | None = None) -> None:
    if fields:
        render_checkbox_window_filter(fields, actions)
        return
    ui.label(tr("parameters")).classes("text-sm font-semibold text-gray-300")


def export_checkbox_selection(node: CommandNode) -> None:
    import yaml  # type: ignore

    selections: dict[str, list[Any]] = {}
    for field in checkbox_fields(node):
        key = field_id(field)
        if not key:
            continue
        selections[key] = normalize_selected_list(current_field_value(field))

    if not selections:
        safe_notify(tr("checkbox_config_empty"), "warning")
        return

    payload = {
        "version": 1,
        "operation_id": node.id,
        "operation_title": node.display_title(settings.language),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "selections": selections,
    }
    checkbox_selection_path.parent.mkdir(parents=True, exist_ok=True)
    checkbox_selection_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    add_log(f"[CONFIG] Exported checkbox selection -> {checkbox_selection_path}")
    safe_notify(tr("checkbox_config_saved", path=str(checkbox_selection_path)), "positive")


def import_checkbox_selection(node: CommandNode) -> None:
    import yaml  # type: ignore

    if not checkbox_selection_path.exists():
        safe_notify(tr("checkbox_config_missing", path=str(checkbox_selection_path)), "warning")
        return

    data = yaml.safe_load(checkbox_selection_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        safe_notify(tr("checkbox_config_empty"), "warning")
        return
    selections = data.get("selections", data)
    if not isinstance(selections, dict):
        safe_notify(tr("checkbox_config_empty"), "warning")
        return

    values = state.setdefault("field_values", {})
    loaded = 0
    for field in checkbox_fields(node):
        key = field_id(field)
        if not key or key not in selections:
            continue
        values[key] = normalize_selected_list(selections.get(key))
        loaded += 1

    if loaded == 0:
        safe_notify(tr("checkbox_config_empty"), "warning")
        return

    add_log(f"[CONFIG] Imported checkbox selection <- {checkbox_selection_path}")
    safe_notify(tr("checkbox_config_loaded", path=str(checkbox_selection_path)), "positive")
    command_tree.refresh()


def export_checkbox_click_handler(node: CommandNode):
    def handler() -> None:
        export_checkbox_selection(node)

    return handler


def import_checkbox_click_handler(node: CommandNode):
    def handler() -> None:
        import_checkbox_selection(node)

    return handler


def field_container_classes(field: dict[str, Any]) -> str:
    span = str(field.get("span") or field.get("width") or "").lower()
    if span in {"full", "wide", "100%", "1/-1"}:
        return "audion-field audion-field-wide"
    span_classes = {
        "half": "audion-field audion-field-span-6",
        "50%": "audion-field audion-field-span-6",
        "6": "audion-field audion-field-span-6",
        "third": "audion-field audion-field-span-4",
        "33%": "audion-field audion-field-span-4",
        "4": "audion-field audion-field-span-4",
        "quarter": "audion-field audion-field-span-3",
        "25%": "audion-field audion-field-span-3",
        "3": "audion-field audion-field-span-3",
    }
    if span in span_classes:
        return span_classes[span]
    kind = str(field.get("type", field.get("kind", "text"))).lower()
    if kind in {"select", "choice", "format"}:
        return "audion-field audion-field-select"
    if kind in {"textarea", "multiline", "path", "file", "folder"} or kind in MARKDOWN_EDITOR_KINDS:
        return "audion-field audion-field-wide"
    if kind in {"preset_buttons", "presets", "profile_buttons", "profiles"}:
        return "audion-field audion-field-wide"
    if kind in {"checkbox", "bool", "boolean", "toggle"}:
        return "audion-field audion-control-field"
    if kind in {"checkboxes", "multi_checkbox", "multicheckbox", "multi-select", "multiselect"}:
        return "audion-field audion-field-wide"
    return "audion-field"


def field_has_explicit_section(field: dict[str, Any]) -> bool:
    return bool(str(field.get("section") or field.get("group") or field.get("field_section") or "").strip())


def field_section_id(field: dict[str, Any]) -> str:
    explicit = str(field.get("section") or field.get("group") or field.get("field_section") or "").strip().lower()
    if explicit:
        return explicit.replace(" ", "_").replace("-", "_")
    key = field_id(field).lower()
    if any(token in key for token in ("provider", "api_key", "model", "reasoning")):
        return "provider"
    if any(token in key for token in ("prompt", "instruction")):
        return "prompt"
    if any(token in key for token in ("request", "query", "search")):
        return "request"
    if any(token in key for token in ("plan", "action")):
        return "plan"
    if any(token in key for token in ("package", "target_list", "exact_id")):
        return "package"
    if any(token in key for token in ("limit", "scan", "known", "timeout", "retry", "max_output")):
        return "generation"
    return "options"


def field_section_title(section_id: str) -> str:
    normalized = str(section_id or "options").strip().lower().replace("-", "_")
    known = {
        "request": "section_request",
        "provider": "section_provider",
        "prompt": "section_prompt",
        "generation": "section_generation",
        "plan": "section_plan",
        "package": "section_package",
        "options": "section_options",
        "parameters": "parameters",
    }
    if normalized in known:
        return tr(known[normalized])
    return normalized.replace("_", " ").strip().title()


def grouped_fields(fields: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    order = ["request", "provider", "prompt", "generation", "plan", "package", "options"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        groups.setdefault(field_section_id(field), []).append(field)
    ordered = [(section_id, groups.pop(section_id)) for section_id in order if section_id in groups]
    ordered.extend(groups.items())
    return ordered


def render_fields_grid(fields: list[dict[str, Any]]) -> None:
    if not fields:
        return
    if not any(field_has_explicit_section(field) for field in fields):
        with ui.element("div").classes("audion-fields-grid audion-parameters-block"):
            for field in fields:
                render_field(field)
        return
    with ui.element("div").classes("audion-fields-grid audion-fields-sectioned"):
        for section_id, section_fields in grouped_fields(fields):
            with ui.element("section").classes(f"audion-field-section audion-field-section-{section_id}"):
                ui.label(field_section_title(section_id)).classes("audion-section-title")
                with ui.element("div").classes("audion-section-fields"):
                    for field in section_fields:
                        render_field(field)


def render_field(field: dict[str, Any]) -> None:
    key = field_id(field)
    if not key:
        return
    kind = str(field.get("type", field.get("kind", "text"))).lower()
    label = field_label(field)
    value = current_field_value(field)
    hint = field_hint(field)
    tooltip = field_tooltip(field)

    field_container = ui.element("div").classes(field_container_classes(field))
    if tooltip and (kind in {"select", "choice", "format"} or kind in MARKDOWN_EDITOR_KINDS):
        attach_tooltip(field_container, tooltip)

    with field_container:
        if kind in {"preset_buttons", "presets", "profile_buttons", "profiles"}:
            presets = field.get("presets", field.get("options", []))
            if not isinstance(presets, list):
                presets = []
            with ui.row().classes("audion-profile-row w-full items-center gap-2"):
                ui.label(label).classes("audion-field-label audion-profile-label mb-0")
                for preset in presets:
                    if not isinstance(preset, dict):
                        continue
                    button = ui.button(
                        preset_label(preset),
                        on_click=preset_click_handler(preset),
                    ).props("dense flat no-wrap").classes("audion-action rounded-lg")
                    attach_tooltip(button, str(preset.get("tooltip_ru" if settings.language == "ru" else "tooltip") or preset.get("hint_ru" if settings.language == "ru" else "hint") or preset_label(preset)))
            return

        if kind in {"select", "choice", "format"}:
            action_items = ai_select_action_items(field)
            props = "dense outlined dropdown-icon=expand_more popup-content-class=audion-select-popup"
            if field_select_uses_html(field):
                props += " options-html display-value-html"
            if bool(field.get("searchable", field.get("with_input", False))):
                props += " use-input input-debounce=0"
            if action_items:
                with ui.row().classes("audion-select-action-row w-full items-center gap-2"):
                    select_control = ui.select(
                        options=select_options(field),
                        label=label,
                        value=value,
                        on_change=select_change_handler(field),
                    ).props(props).classes("audion-select min-w-0 flex-1")
                    with ui.row().classes("audion-field-icon-row gap-1"):
                        if dynamic_option_source(field):
                            render_dynamic_refresh_icon(field)
                        for item in action_items:
                            render_ai_select_action_button(item)
                        if key == "ai_prompt_ref":
                            clear_button = ui.button(
                                icon="backspace",
                                on_click=clear_ai_prompt_editor,
                            ).props(f'dense flat round aria-label="{tr("ai_clear")}"').classes("audion-action audion-field-icon-button")
                            clear_button.tooltip(tr("ai_clear"))
            else:
                select_control = ui.select(
                    options=select_options(field),
                    label=label,
                    value=value,
                    on_change=select_change_handler(field),
                ).props(props).classes("audion-select w-full")
            register_field_control(key, select_control)
            if dynamic_option_source(field) and not action_items:
                refresh_button = ui.button(
                    tr("refresh_options"),
                    on_click=refresh_options_click_handler(field),
                ).props("dense flat no-wrap").classes("audion-action mt-1 rounded-lg")
                refresh_button.tooltip(tr("refresh_options"))
            if hint:
                ui.label(hint).classes("audion-field-hint")
            return

        if kind in {"radio", "radiobuttons", "radio-buttons"}:
            attach_tooltip(ui.label(label).classes("audion-field-label"), tooltip)
            radio_control = ui.radio(
                options=select_options(field),
                value=value,
                on_change=select_change_handler(field),
            ).props("dense inline").classes("audion-choice-row")
            register_field_control(key, radio_control)
            attach_tooltip(radio_control, tooltip)
            if hint:
                ui.label(hint).classes("audion-field-hint")
            return

        if kind in {"number", "int", "integer", "float"}:
            number_input = ui.number(
                label=label,
                value=value if value != "" else None,
                min=field.get("min"),
                max=field.get("max"),
                step=field.get("step", 1),
                on_change=lambda event, item_key=key: set_field_value(item_key, event.value),
            ).props("dense outlined").classes("audion-number w-full")
            register_field_control(key, number_input)
            attach_tooltip(number_input, tooltip)
            with number_input.add_slot("append"):
                with ui.element("div").classes("audion-number-spinner"):
                    ui.button(
                        icon="keyboard_arrow_up",
                        on_click=lambda item_key=key, item_field=field, control=number_input: spin_number_field(item_key, item_field, control, 1),
                    ).props("dense flat round tabindex=-1").classes("audion-number-spin-button")
                    ui.button(
                        icon="keyboard_arrow_down",
                        on_click=lambda item_key=key, item_field=field, control=number_input: spin_number_field(item_key, item_field, control, -1),
                    ).props("dense flat round tabindex=-1").classes("audion-number-spin-button")
            if hint:
                ui.label(hint).classes("audion-field-hint")
            return

        if kind in {"checkbox", "bool", "boolean", "toggle"}:
            # Chip and hint stay on one line and truncate; the full wording
            # lives in the tooltip instead of stacking four wrapped rows.
            with ui.element("div").classes("audion-check-row"):
                with ui.element("div").classes("audion-control-chip audion-control-chip-check"):
                    checkbox = ui.checkbox(
                        label,
                        value=bool(value),
                        on_change=lambda event, item_key=key: set_field_value(item_key, bool(event.value)),
                    ).props("dense").classes("audion-single-checkbox")
                register_field_control(key, checkbox)
                attach_tooltip(checkbox, " — ".join(part for part in (tooltip, hint) if part) or label)
                if hint:
                    hint_label = ui.label(hint).classes("audion-field-hint audion-check-row-hint")
                    attach_tooltip(hint_label, hint)
            return

        if is_checkbox_group(field):
            selected = set(value if isinstance(value, list) else [])
            controls: dict[Any, Any] = {}
            options = checkbox_options(field)
            window_filter_query = checkbox_window_filter_value()
            local_filter_query = checkbox_filter_value(key) if field_uses_local_checkbox_filter(field) else ""
            visible_options = filter_checkbox_options(
                filter_checkbox_options(options, window_filter_query),
                local_filter_query,
            )
            source = dynamic_option_source(field)
            has_selectable_options = any(str(option_key).strip() for option_key, _option_text in options)
            all_option_keys = {option_key for option_key, _option_text in options if str(option_key).strip()}
            visible_option_keys = {
                option_key for option_key, _option_text in visible_options if str(option_key).strip()
            }
            visible_count = len(visible_option_keys)
            total_count = len(all_option_keys)
            has_active_filter = bool(window_filter_query or local_filter_query)

            def sync_checkboxes(item_key: str = key) -> None:
                current = normalize_selected_list(state.setdefault("field_values", {}).get(item_key, field_default(field)))
                preserved = [item for item in current if has_active_filter and item not in visible_option_keys]
                seen_preserved = set(preserved)
                checked = [
                    option_key
                    for option_key, checkbox in controls.items()
                    if bool(checkbox.value) and option_key not in seen_preserved
                ]
                set_field_value(
                    item_key,
                    [*preserved, *checked],
                )

            def set_group_checkboxes(checked: bool) -> None:
                for checkbox in controls.values():
                    checkbox.set_value(checked)
                sync_checkboxes()

            with ui.element("div").classes("audion-checkbox-block"):
                with ui.row().classes("audion-log-toolbar w-full items-center gap-2"):
                    attach_tooltip(ui.label(label).classes("audion-field-label mb-0"), tooltip)
                    ui.space()
                    if has_selectable_options:
                        select_button = ui.button(
                            tr("select_visible") if has_active_filter else tr("select_group_all"),
                            on_click=lambda: set_group_checkboxes(True),
                        ).props("dense flat no-wrap").classes("audion-action rounded-lg")
                        select_button.tooltip(tr("select_visible") if has_active_filter else tr("select_group_all"))
                        clear_button = ui.button(
                            tr("clear_visible") if has_active_filter else tr("clear_group"),
                            on_click=lambda: set_group_checkboxes(False),
                        ).props("dense flat no-wrap").classes("audion-action rounded-lg")
                        clear_button.tooltip(tr("clear_visible") if has_active_filter else tr("clear_group"))
                    if source:
                        refresh_button = ui.button(
                            tr("refresh_options"),
                            on_click=refresh_options_click_handler(field),
                        ).props("dense flat no-wrap").classes("audion-action rounded-lg")
                        refresh_button.tooltip(tr("refresh_options"))
                if has_selectable_options and field_uses_local_checkbox_filter(field):
                    with ui.row().classes("audion-checkbox-filter-row w-full items-center gap-2"):
                        filter_input = ui.input(
                            label=tr("checkbox_filter"),
                            value=local_filter_query,
                            placeholder=tr("checkbox_filter_placeholder"),
                            on_change=lambda event, item_key=key: set_checkbox_filter_value(item_key, event.value),
                        ).props("dense outlined clearable debounce=250").classes("audion-checkbox-filter")
                        filter_input.tooltip(tr("checkbox_filter_placeholder"))
                        ui.label(
                            tr("checkbox_filter_count", visible=visible_count, total=total_count)
                        ).classes("audion-checkbox-filter-count")
                with ui.element("div").classes("audion-checkbox-grid").style(checkbox_grid_style(field)):
                    if has_active_filter and has_selectable_options and not visible_option_keys:
                        ui.label(tr("checkbox_filter_no_matches")).classes("audion-empty-options")
                    elif not options:
                        ui.label(empty_options_label(field, source)).classes("audion-empty-options")
                    else:
                        for option_key, option_text in visible_options:
                            if not str(option_key).strip():
                                ui.label(option_text).classes("audion-empty-options")
                                continue
                            with ui.element("div").classes(checkbox_card_classes(field, option_key, option_text)):
                                checkbox = ui.checkbox(
                                    option_text,
                                    value=option_key in selected,
                                    on_change=lambda _event: sync_checkboxes(),
                                ).props("dense")
                                checkbox.tooltip(str(option_text))
                                controls[option_key] = checkbox
                                package_card_actions(field, option_key)
            if hint:
                ui.label(hint).classes("audion-field-hint")
            loading_placeholder = bool(source) and source in dynamic_option_tasks and not has_selectable_options
            if not loading_placeholder:
                sync_checkboxes()
            return

        if kind in MARKDOWN_EDITOR_KINDS:
            ui.label(label).classes("audion-field-label")
            editor_value = str(value) if value is not None else ""
            editor_classes = f"w-full audion-markdown-editor {str(field.get('classes') or '').strip()}".strip()
            try:
                editor = ui.codemirror(
                    value=editor_value,
                    language=str(field.get("language") or field.get("editor_language") or "Markdown"),
                    theme=codemirror_theme_for_field(field),
                    indent=str(field.get("indent") or "  "),
                    line_wrapping=bool_field_option(field, "line_wrapping", True),
                    highlight_whitespace=bool_field_option(field, "highlight_whitespace", False),
                    on_change=lambda event, item_key=key: set_field_value(item_key, event.value),
                ).classes(editor_classes)
            except Exception as exc:
                logging.warning("CodeMirror field %s failed, falling back to textarea: %s", key, exc)
                rows = max(3, min(24, int(field.get("rows", 10) or 10)))
                textarea = ui.textarea(
                    label=label,
                    value=editor_value,
                    placeholder=str(field.get("placeholder", "")),
                    on_change=lambda event, item_key=key: set_field_value(item_key, event.value),
                ).props("outlined autogrow stack-label").classes("w-full audion-textarea")
                textarea.style(f"min-height: {rows * 22}px;")
            if hint:
                ui.label(hint).classes("audion-field-hint")
            return

        if kind in {"textarea", "multiline"}:
            rows = max(3, min(24, int(field.get("rows", 8) or 8)))
            textarea = ui.textarea(
                label=label,
                value=str(value) if value is not None else "",
                placeholder=str(field.get("placeholder", "")),
                on_change=lambda event, item_key=key: set_field_value(item_key, event.value),
            ).props("outlined autogrow").classes("w-full audion-textarea")
            textarea.style(f"min-height: {rows * 22}px;")
            register_field_control(key, textarea)
            attach_tooltip(textarea, tooltip)
            if hint:
                ui.label(hint).classes("audion-field-hint")
            return

        input_control = ui.input(
            label=label,
            value=str(value) if value is not None else "",
            placeholder=str(field.get("placeholder", "")),
            on_change=lambda event, item_key=key: set_field_value(item_key, event.value),
        ).props("dense outlined").classes("w-full")
        register_field_control(key, input_control)
        attach_tooltip(input_control, tooltip)
        if hint:
            ui.label(hint).classes("audion-field-hint")


def operation_from_pending_command(node: CommandNode) -> Operation:
    parameters = dict(node.parameters)
    values = state.setdefault("field_values", {})
    for field in node.fields:
        key = field_id(field)
        if key:
            parameters[key] = values.get(key, field_default(field))
    return node.to_operation(parameters)


def validate_pending_fields(node: CommandNode) -> bool:
    values = state.setdefault("field_values", {})
    for field in node.fields:
        if not is_checkbox_group(field):
            continue
        min_selected = int(field.get("min_selected", 0) or 0)
        if min_selected <= 0:
            continue
        key = field_id(field)
        selected = values.get(key, field_default(field))
        if not isinstance(selected, list) or len(selected) < min_selected:
            safe_notify(tr("select_required", field=field_label(field)), "warning")
            return False
    return True


async def run_pending_command(node: CommandNode) -> None:
    if validate_pending_fields(node):
        await start_operation(operation_from_pending_command(node))


def run_pending_click_handler(node: CommandNode):
    async def handler() -> None:
        await run_pending_command(node)

    return handler


def field_signature(fields: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(field_id(field) for field in fields if field_id(field))


def child_inherits_parent_fields(parent: CommandNode, child: CommandNode) -> bool:
    parent_signature = field_signature(parent.fields)
    child_signature = field_signature(child.fields)
    return bool(parent_signature) and child_signature[: len(parent_signature)] == parent_signature


def inline_child_actions(parent: CommandNode | None, children: list[CommandNode]) -> list[tuple[CommandNode, str]]:
    if parent is None or not parent.fields:
        return []
    actions: list[tuple[CommandNode, str]] = []
    parent_signature = field_signature(parent.fields)
    for child in children:
        if child.children or not child_inherits_parent_fields(parent, child):
            continue
        mode = "run" if field_signature(child.fields) == parent_signature else "open"
        actions.append((child, mode))
    return actions


def render_inline_child_action(node: CommandNode, mode: str) -> None:
    handler = run_pending_click_handler(node) if mode == "run" else command_click_handler(node)
    button = ui.button(
        node.display_title(settings.language),
        on_click=handler,
    ).props("dense flat no-wrap no-caps").classes("audion-action audion-inline-action rounded-lg")
    description = node.display_description(settings.language)
    if description:
        button.tooltip(description)


def render_inline_action_row(actions: list[tuple[CommandNode, str]]) -> None:
    if not actions:
        return
    with ui.row().classes("audion-inline-actions w-full gap-2"):
        for node, mode in actions:
            render_inline_child_action(node, mode)


def update_available_toolbar_actions(nodes: list[CommandNode]) -> list[CommandNode]:
    lookup = {node.id: node for node in nodes}
    return [lookup[node_id] for node_id in UPDATE_AVAILABLE_TOOLBAR_ACTION_IDS if node_id in lookup]


def find_command_descendant(node: CommandNode | None, node_id: str) -> CommandNode | None:
    if node is None:
        return None
    if node.id == node_id:
        return node
    for child in node.children:
        found = find_command_descendant(child, node_id)
        if found is not None:
            return found
    return None


def fields_by_id(fields: tuple[dict[str, Any], ...] | list[dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {field_id(field): field for field in fields}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def set_ai_planner_tab(tab: str) -> None:
    state["ai_planner_tab"] = tab if tab in {"planner", "models"} else "planner"
    state["pending_command"] = None
    command_tree.refresh()


def ai_tab_button(tab: str, label: str) -> None:
    active = str(state.get("ai_planner_tab") or "planner") == tab
    classes = "audion-ai-tab"
    if active:
        classes += " audion-ai-tab-active"
    ui.button(label, on_click=lambda _event=None, item=tab: set_ai_planner_tab(item)).props("dense flat no-wrap no-caps").classes(classes)


AI_COMMAND_ICONS = {
    "ai_plan_packages": "fact_check",
    "ai_search_winget": "search",
    "ai_run_selected_plan_packages": "playlist_add_check",
    "ai_add_exact_package_to_list": "playlist_add",
    "ai_install_exact_package": "download",
    "ai_update_exact_package": "upgrade",
    "ai_uninstall_exact_package": "delete",
}


PORTABLE_CHROME_STANDALONE_URL = "https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe"
PORTABLE_CHROME_URL_PRESETS = (
    ("STANDALONE X64", PORTABLE_CHROME_STANDALONE_URL),
    ("RU", f"{PORTABLE_CHROME_STANDALONE_URL}?hl=ru"),
    ("EN", f"{PORTABLE_CHROME_STANDALONE_URL}?hl=en"),
)


def ai_command_icon(node: CommandNode | None) -> str:
    return AI_COMMAND_ICONS.get(node.id, "") if node is not None else ""


# The run button says what it does. Order matters: the first match wins, so
# `uninstall` has to be tested before `install`, and `download` before `load`.
COMMAND_ACTION_VERBS: tuple[tuple[str, str], ...] = (
    ("uninstall", "run_uninstall"),
    ("remove", "run_uninstall"),
    ("install", "run_install"),
    ("upgrade", "run_update"),
    ("update", "run_update"),
    ("search", "run_search"),
    ("pin", "run_pin"),
    ("check", "run_check"),
    ("validate", "run_check"),
    ("export", "run_export"),
    ("import", "run_import"),
    ("add", "run_add"),
    ("download", "run_download"),
    ("cleanup", "run_clean"),
    ("clear", "run_clean"),
    ("build", "run_build"),
    ("enable", "run_enable"),
)


def command_action_verb(node: CommandNode) -> str:
    action = str(dict(getattr(node, "parameters", {}) or {}).get("package_action") or "").strip().lower()
    haystack = f"{action} {node.id}".lower()
    for token, key in COMMAND_ACTION_VERBS:
        if token in haystack:
            return tr(key)
    return tr("run")


def pending_run_button_label(node: CommandNode) -> str:
    return node.display_title(settings.language) if ai_command_icon(node) else command_action_verb(node)


def render_ai_tab_switch() -> None:
    with ui.row().classes("audion-ai-tabs w-full items-end gap-3"):
        ai_tab_button("planner", tr("ai_tab_planner"))
        ai_tab_button("models", tr("ai_tab_models"))


def ai_action_button(node: CommandNode | None, icon: str, *, direct_run: bool = False) -> None:
    if node is None:
        return
    async def direct_handler() -> None:
        await run_pending_command(node)
        clear_ai_dynamic_options()
        command_tree.refresh()

    handler = direct_handler if direct_run else command_click_handler(node)
    button = ui.button(
        node.display_title(settings.language),
        icon=icon,
        on_click=handler,
    ).props("dense flat no-wrap no-caps").classes(
        "audion-action audion-run-action audion-ai-action-button rounded-lg"
    )
    description = node.display_description(settings.language)
    if description:
        button.tooltip(description)


def render_ai_prompt_toolbar() -> None:
    with ui.row().classes("audion-prompt-toolbar w-full items-center gap-1"):
        ui.label(tr("ai_prompt_actions")).classes("audion-subsection-label")
        ui.space()
        actions = [
            ("save_prompt", "save", tr("ai_save")),
            ("pin_prompt", "push_pin", tr("ai_pin")),
            ("unpin_prompt", "block", tr("ai_unpin")),
            ("delete_prompt", "delete", tr("ai_delete")),
        ]
        for mode, icon, label in actions:
            button = ui.button(icon=icon, on_click=ai_control_click_handler(mode)).props(f'dense flat round aria-label="{label}"').classes("audion-action audion-field-icon-button")
            button.tooltip(label)
        clear_button = ui.button(icon="backspace", on_click=clear_ai_prompt_editor).props(f'dense flat round aria-label="{tr("ai_clear")}"').classes("audion-action audion-field-icon-button")
        clear_button.tooltip(tr("ai_clear"))


def render_ai_process_actions(parent: CommandNode) -> None:
    ui.label(tr("ai_process_actions")).classes("audion-subsection-label")
    with ui.row().classes("audion-ai-action-grid w-full gap-2"):
        ai_action_button(find_command_descendant(parent, "ai_plan_packages"), AI_COMMAND_ICONS["ai_plan_packages"], direct_run=True)
        ai_action_button(find_command_descendant(parent, "ai_search_winget"), AI_COMMAND_ICONS["ai_search_winget"])
        ai_action_button(find_command_descendant(parent, "ai_run_selected_plan_packages"), AI_COMMAND_ICONS["ai_run_selected_plan_packages"])
        ai_action_button(find_command_descendant(parent, "ai_add_exact_package_to_list"), AI_COMMAND_ICONS["ai_add_exact_package_to_list"])
        ai_action_button(find_command_descendant(parent, "ai_install_exact_package"), AI_COMMAND_ICONS["ai_install_exact_package"])
        ai_action_button(find_command_descendant(parent, "ai_update_exact_package"), AI_COMMAND_ICONS["ai_update_exact_package"])
        ai_action_button(find_command_descendant(parent, "ai_uninstall_exact_package"), AI_COMMAND_ICONS["ai_uninstall_exact_package"])


def render_ai_planner_pane(parent: CommandNode) -> None:
    llm_node = find_command_descendant(parent, "ai_llm_planning")
    plan_node = find_command_descendant(parent, "ai_plan_packages")
    all_fields = list(plan_node.fields if plan_node else (llm_node.fields if llm_node else ()))
    planner_fields = fields_by_id(
        all_fields,
        [
            "package_request",
            "ai_prompt_ref",
            "ai_prompt",
            "ai_prompt_label",
            "ai_prompt_note",
            "include_installed_scan",
            "include_known_packages",
            "search_limit",
        ],
    )
    render_fields_grid(planner_fields)


def render_ai_models_pane(parent: CommandNode) -> None:
    llm_node = find_command_descendant(parent, "ai_llm_planning")
    fields = list(llm_node.fields if llm_node else ())
    provider = str(current_field_value(next((field for field in fields if field_id(field) == "provider"), {"id": "provider", "default": "openai"})) or "openai")
    provider = provider if provider in {"openai", "gemini"} else "openai"
    provider_specific = (
        ["provider", "openai_api_key_ref", "openai_model", "openai_model_override", "openai_reasoning"]
        if provider == "openai"
        else ["provider", "gemini_api_key_ref", "gemini_model", "gemini_model_override"]
    )
    model_fields = fields_by_id(
        fields,
        [
            *provider_specific,
            "llm_max_output_tokens",
            "llm_max_retries",
            "llm_timeout_sec",
        ],
    )
    render_fields_grid(model_fields)
    check_button = ui.button(
        tr("ai_check_model"),
        icon="fact_check",
        on_click=ai_control_click_handler("check_model", {"provider": provider}),
    ).props("dense flat no-wrap no-caps").classes(
        "audion-action audion-run-action audion-ai-action-button rounded-lg"
    )
    check_button.tooltip(tr("ai_check_model"))


def render_ai_package_planner(parent: CommandNode) -> None:
    render_ai_tab_switch()
    if str(state.get("ai_planner_tab") or "planner") == "planner":
        render_ai_process_actions(parent)
    with ui.element("div").classes("audion-ai-pane"):
        if str(state.get("ai_planner_tab") or "planner") == "models":
            render_ai_models_pane(parent)
        else:
            render_ai_planner_pane(parent)


def portable_action_button(node: CommandNode | None, download_node: CommandNode | None = None) -> None:
    if node is None:
        return

    async def handler() -> None:
        await run_pending_command(node)
        command_tree.refresh()

    # Same row shape as every other operation: action button plus description.
    # The portable pane keeps its own fields, so the click runs the node
    # directly instead of navigating into a parameter view.
    description = node.display_description(settings.language)
    tooltip = node.display_tooltip(settings.language) or description
    with ui.element("div").classes("audion-operation-row audion-portable-operation-row"):
        button = ui.button(
            node.display_title(settings.language),
            on_click=handler,
        ).props("dense flat no-wrap").classes(
            "audion-action audion-operation-button audion-operation-action-button rounded-lg"
        )
        attach_tooltip(button, tooltip or node.display_title(settings.language))
        if download_node is not None:
            # Same pair as a package card: run the action, or just take the file.
            async def download_handler(_event: Any = None, item: CommandNode = download_node) -> None:
                await run_pending_command(item)
                command_tree.refresh()

            download_button = ui.button(
                icon="file_download",
                on_click=download_handler,
            ).props("dense flat round unelevated").classes(
                "audion-package-icon-button audion-package-icon-download audion-portable-download-button"
            )
            attach_tooltip(
                download_button,
                download_node.display_tooltip(settings.language)
                or download_node.display_description(settings.language)
                or download_node.display_title(settings.language),
            )
        ui.label(description).classes("audion-operation-description")


def render_portable_actions(parent: CommandNode) -> None:
    ui.label(l10n("ДЕЙСТВИЯ", "ACTIONS")).classes("audion-subsection-label")
    with ui.element("div").classes("audion-portable-action-list"):
        for node_id in (
            "portable_install_google_chrome_web",
            "portable_install_7zip",
            "portable_download_browsers",
            "portable_build_google_chrome",
            "portable_update_google_chrome",
        ):
            companion = (
                find_command_descendant(parent, "portable_download_google_chrome_web")
                if node_id == "portable_install_google_chrome_web"
                else None
            )
            portable_action_button(find_command_descendant(parent, node_id), companion)


def set_chrome_download_url(url: str) -> None:
    set_field_value("chrome_download_url", url)
    command_tree.refresh()


def chrome_source_badge(label: str, url: str) -> None:
    current = str(state.setdefault("field_values", {}).get("chrome_download_url") or PORTABLE_CHROME_STANDALONE_URL)
    classes = "audion-action audion-portable-source-badge rounded-full"
    if current == url:
        classes += " audion-portable-source-badge-active"
    button = ui.button(label, on_click=lambda _event=None, item=url: set_chrome_download_url(item)).props("dense flat no-wrap no-caps").classes(classes)
    button.tooltip(url)


def render_portable_chrome_source(chrome_node: CommandNode | None) -> None:
    if chrome_node is None:
        return
    url_field = next((field for field in chrome_node.fields if field_id(field) == "chrome_download_url"), None)
    if url_field is None:
        return
    value = str(current_field_value(url_field) or PORTABLE_CHROME_STANDALONE_URL)
    with ui.element("div").classes("audion-portable-chrome-source"):
        with ui.row().classes("audion-portable-chrome-source-row w-full items-center gap-2"):
            ui.label(l10n("Источник Chrome", "Chrome source")).classes("audion-field-label audion-portable-source-label mb-0")
            with ui.row().classes("audion-portable-source-badges items-center gap-1"):
                for label, url in PORTABLE_CHROME_URL_PRESETS:
                    chrome_source_badge(label, url)
            url_input = ui.input(
                label=field_label(url_field),
                value=value,
                on_change=lambda event: set_field_value("chrome_download_url", event.value),
            ).props("dense outlined").classes("audion-portable-url-input min-w-0")
            attach_tooltip(url_input, field_tooltip(url_field))


def render_portable_panel(parent: CommandNode) -> None:
    render_portable_actions(parent)
    download_node = find_command_descendant(parent, "portable_download_browsers")
    chrome_node = find_command_descendant(parent, "portable_build_google_chrome")
    with ui.element("div").classes("audion-portable-pane"):
        with ui.element("section").classes("audion-portable-browser-block"):
            render_fields_grid(fields_by_id(download_node.fields, ["portable_browsers"]) if download_node is not None else [])
        with ui.element("section").classes("audion-portable-block audion-portable-chrome-block"):
            render_portable_chrome_source(chrome_node)
            chrome_options: list[dict[str, Any]] = []
            if chrome_node is not None:
                chrome_options.extend(fields_by_id(chrome_node.fields, ["chrome_plus_arch", "package_archive", "archive_format"]))
            if download_node is not None:
                chrome_options.extend(fields_by_id(download_node.fields, ["keep_temp"]))
            render_fields_grid(chrome_options)


def command_node_button(node: CommandNode, *, compact_root: bool = False) -> None:
    has_children = bool(node.children)
    label = node.display_title(settings.language)
    description = node.display_description(settings.language)
    tooltip = node.display_tooltip(settings.language) or description
    if has_children and not description:
        description = tr("open_menu")
        tooltip = tooltip or description

    row_classes = "audion-operation-row"
    if compact_root:
        row_classes += " audion-root-operation-row"
    button_classes = "audion-action audion-operation-button rounded-lg"
    if command_node_runs_immediately(node):
        button_classes += " audion-operation-action-button"
    page_url = str(dict(getattr(node, "parameters", {}) or {}).get("page_url") or "").strip()
    if page_url:
        row_classes += " audion-operation-row-with-page"
    with ui.element("div").classes(row_classes):
        button = ui.button(
            label,
            on_click=command_click_handler(node),
        ).props("dense flat no-wrap").classes(button_classes)
        attach_tooltip(button, tooltip)
        if page_url:
            # Same blue arrow as a package card: the row downloads, the arrow
            # opens the release page so a build can be picked by hand.
            page_button = ui.button(
                icon="open_in_new",
                on_click=lambda _event=None, url=page_url: open_external_page(url),
            ).props("dense flat round unelevated").classes(
                "audion-package-icon-button audion-package-icon-page audion-operation-page-button"
            )
            attach_tooltip(page_button, page_url)
        ui.label(description).classes("audion-operation-description")


def render_command_group(title: str, nodes: list[CommandNode]) -> None:
    """Child windows show their commands inside a titled panel, never bare rows."""
    if not nodes:
        return
    with ui.element("section").classes("audion-field-section audion-command-group"):
        if title:
            ui.label(title).classes("audion-section-title")
        with ui.element("div").classes("audion-command-group-body"):
            for node in nodes:
                command_node_button(node)


def command_node_is_inlineable(node: CommandNode) -> bool:
    """A leaf command with light fields can live inside the parent window."""
    if node.children or not node.fields:
        return False
    for field in node.fields:
        if is_checkbox_group(field):
            return False
        if str(field.get("type", field.get("kind", "text"))).lower() in MARKDOWN_EDITOR_KINDS:
            return False
    return True


def render_inline_command_section(node: CommandNode) -> None:
    """A child command shown in place: own title, own fields, own run button."""
    description = node.display_description(settings.language)
    with ui.element("section").classes("audion-field-section audion-inline-command"):
        with ui.row().classes("audion-inline-command-head w-full items-center gap-2"):
            # The button carries the command name: a column of identical
            # `Run` buttons says nothing about what each panel does.
            run_button = ui.button(
                node.display_title(settings.language),
                on_click=run_pending_click_handler(node),
            ).props("dense flat no-wrap").classes(
                "audion-action audion-run-action audion-inline-command-run rounded-lg"
            )
            attach_tooltip(run_button, node.display_tooltip(settings.language) or description or tr("run"))
            ui.space()
        if description:
            ui.label(description).classes("audion-inline-command-description")
        render_fields_grid(list(node.fields))


def render_child_window_commands(parent: CommandNode, nodes: list[CommandNode]) -> None:
    """Child windows no longer link onward: their commands are laid out here."""
    inline_ids = {node.id for node in nodes if command_node_is_inlineable(node)}
    for node in nodes:
        if node.id in inline_ids:
            render_inline_command_section(node)
    render_command_group(
        parent.display_title(settings.language),
        [node for node in nodes if node.id not in inline_ids],
    )


def app_installer_command_node() -> CommandNode | None:
    return next((node for node in root_command_nodes() if node.id == APP_INSTALLER_COMMAND_ID), None)


def render_app_installer_action() -> None:
    """WinGet updates itself, so it sits above the groups, not inside them."""
    node = app_installer_command_node()
    if node is None:
        return
    with ui.element("div").classes("audion-operation-row audion-app-installer-row"):
        button = ui.button(
            node.display_title(settings.language),
            icon="system_update_alt",
            on_click=command_click_handler(node),
        ).props("dense flat no-wrap").classes(
            "audion-action audion-operation-button audion-operation-action-button audion-app-installer-button rounded-lg"
        )
        attach_tooltip(button, node.display_tooltip(settings.language) or node.display_description(settings.language))
        ui.label(node.display_description(settings.language)).classes(
            "audion-operation-description audion-app-installer-description"
        )


def command_nav_row(trail: list[CommandNode], pending: CommandNode | None) -> None:
    can_go_back = pending is not None or bool(trail)
    if pending is not None:
        title = pending.display_title(settings.language)
    elif trail:
        title = " / ".join(node.display_title(settings.language) for node in trail)
    else:
        title = ""

    if pending is not None:
        with ui.row().classes("audion-command-nav audion-pending-nav w-full items-center gap-2"):
            ui.button(
                tr("back"),
                on_click=go_back_command,
            ).props("dense flat no-wrap").classes("audion-action w-28 rounded-lg")
            ui.label(title).classes("min-w-0 flex-1 truncate text-sm font-semibold text-gray-300")
            if checkbox_fields(pending):
                export_button = ui.button(
                    tr("export_checkboxes"),
                    on_click=export_checkbox_click_handler(pending),
                ).props("dense flat no-wrap").classes("audion-action w-32 rounded-lg")
                export_button.tooltip(tr("export_checkboxes"))
                import_button = ui.button(
                    tr("import_checkboxes"),
                    on_click=import_checkbox_click_handler(pending),
                ).props("dense flat no-wrap").classes("audion-action w-32 rounded-lg")
                import_button.tooltip(tr("import_checkboxes"))
            run_icon = ai_command_icon(pending)
            run_label = pending_run_button_label(pending)
            run_button_kwargs: dict[str, Any] = {"on_click": run_pending_click_handler(pending)}
            if run_icon:
                run_button_kwargs["icon"] = run_icon
            run_button = ui.button(
                run_label,
                **run_button_kwargs,
            ).props("dense flat no-wrap no-caps").classes(
                "audion-action audion-run-action audion-pending-final-action rounded-lg"
                if run_icon
                else "audion-action audion-run-action audion-run-action-verb rounded-lg"
            )
            attach_tooltip(run_button, pending.display_description(settings.language) or run_label)
        return

    with ui.row().classes("audion-command-nav w-full items-center gap-2"):
        if can_go_back:
            ui.button(
                tr("back"),
                on_click=go_back_command,
            ).props("dense flat no-wrap").classes("audion-action w-28 rounded-lg")
        ui.label(title).classes("min-w-0 flex-1 truncate text-sm text-gray-400")
        if (
            pending is None
            and trail
            and trail[-1].id == "ai_package_planner"
            and str(state.get("ai_planner_tab") or "planner") == "planner"
        ):
            run_selected_node = find_command_descendant(trail[-1], "ai_run_selected_plan_packages")
            if run_selected_node is not None:
                cta_button = ui.button(
                    tr("ai_run_selected_cta"),
                    icon=ai_command_icon(run_selected_node),
                    on_click=command_click_handler(run_selected_node),
                ).props("dense flat no-wrap no-caps").classes(
                    "audion-action audion-run-action audion-ai-nav-final-action rounded-lg"
                )
                attach_tooltip(cta_button, run_selected_node.display_description(settings.language))


@ui.refreshable
def command_tree() -> None:
    reset_field_controls()
    trail, nodes = current_command_level()
    pending = state.get("pending_command")
    parent = trail[-1] if trail else None
    command_nav_row(trail, pending)

    if pending is not None:
        if pending.id in UPDATE_WINDOW_COMMAND_IDS:
            render_app_installer_action()
        if pending.fields:
            pending_checkbox_fields = checkbox_fields(pending)
            filter_actions = update_available_toolbar_actions(nodes) if pending.id == "update_available_packages" else None
            render_parameters_header(pending_checkbox_fields, filter_actions)
            render_fields_grid(list(pending.fields))
        return

    if parent is not None and parent.id == "ai_package_planner":
        render_ai_package_planner(parent)
        return

    if parent is not None and parent.id == "portable":
        render_portable_panel(parent)
        return

    inline_actions = inline_child_actions(parent, nodes)
    if parent is not None and parent.fields and any(field_has_explicit_section(field) for field in parent.fields):
        parent_checkbox_fields = checkbox_fields(parent)
        render_parameters_header(parent_checkbox_fields)
        render_fields_grid(list(parent.fields))
        render_inline_action_row(inline_actions)
        rendered_inline_ids = {node.id for node, _mode in inline_actions}
        render_child_window_commands(
            parent,
            [node for node in nodes if node.id not in rendered_inline_ids],
        )
        return

    if parent is not None:
        render_child_window_commands(parent, list(nodes))
        return

    for node in nodes:
        if node.id in UPDATE_AVAILABLE_TOOLBAR_ACTION_IDS or node.id == APP_INSTALLER_COMMAND_ID:
            continue
        command_node_button(node, compact_root=True)


@ui.refreshable
def terminal_command_bar() -> None:
    shell_options = {"pwsh": "PowerShell", "cmd": "CMD"} if os.name == "nt" else {"sh": "Shell"}
    with ui.column().classes("audion-terminal-command w-full gap-1"):
        with ui.row().classes("audion-terminal-command-row w-full items-center"):
            shell_select = ui.select(
                options=shell_options,
                label=tr("terminal_shell"),
                value=str(state.get("terminal_shell") or next(iter(shell_options))),
                on_change=lambda event: set_terminal_shell(event.value),
            )
            shell_select.props("dense outlined dropdown-icon=expand_more popup-content-class=audion-select-popup").classes("audion-terminal-shell")

            history_select = ui.select(
                options=terminal_command_options(),
                label=tr("terminal_history"),
                value=terminal_history_value(),
                on_change=lambda event: select_terminal_history(event),
            )
            history_select.props("dense outlined dropdown-icon=expand_more popup-content-class=audion-select-popup").classes("audion-terminal-history min-w-0 flex-1")

            pin_button = ui.button(
                icon="push_pin",
                on_click=pin_terminal_command,
            ).props("dense flat round").classes("audion-action audion-terminal-icon-button audion-terminal-pin")
            pin_button.tooltip(audion_terminal_action_tooltip("pin_command"))
            unpin_button = ui.button(
                icon="block",
                on_click=unpin_terminal_command,
            ).props("dense flat round").classes("audion-action audion-terminal-icon-button audion-terminal-unpin")
            unpin_button.tooltip(audion_terminal_action_tooltip("unpin_command"))
            clear_button = ui.button(
                icon="delete",
                on_click=clear_terminal_history,
            ).props("dense flat round").classes("audion-action audion-terminal-icon-button audion-terminal-clear")
            clear_button.tooltip(tr("clear_terminal_history"))
            ui.button(
                tr("terminal_run"),
                on_click=start_terminal_command,
            ).props("dense flat no-wrap").classes("audion-action audion-terminal-run rounded-lg")

        command_area = ui.textarea(
            label=tr("terminal_command"),
            value=str(state.get("terminal_command") or ""),
            on_change=lambda event: set_terminal_command(event.value),
        )
        command_area.props("dense outlined autogrow rows=3").classes("audion-terminal-command-text w-full")
        command_area.on("keydown.ctrl.enter", terminal_enter_handler)

        with ui.row().classes("w-full items-center gap-2"):
            ui.input(
                label=tr("terminal_cwd"),
                value=str(state.get("terminal_cwd") or ROOT),
                on_change=lambda event: set_terminal_cwd(event.value),
            ).props("dense outlined").classes("audion-terminal-cwd min-w-0 flex-1")
            ui.button(
                tr("terminal_folder"),
                on_click=terminal_location_click_handler("folder"),
            ).props("dense flat no-wrap").classes("audion-action audion-terminal-picker rounded-lg")
            ui.button(
                tr("terminal_file"),
                on_click=terminal_location_click_handler("file"),
            ).props("dense flat no-wrap").classes("audion-action audion-terminal-picker rounded-lg")


def add_styles() -> None:
    add_audion_canonical_ui_styles()
    variables_css = "\n".join(
        f"  --{key}: {value};"
        for key, value in sorted(theme_variables().items())
    )
    ui.add_head_html(
        "<style>\n"
        ":root {\n"
        f"{variables_css}\n"
        "}\n"
        + application_css()
        + "\n</style>\n"
    )
    ui.add_head_html(
        '<style id="audion-canonical-workbench-style">'
        + WORKBENCH_LAYOUT_CSS
        + WORKBENCH_OVERRIDE_CSS
        + "</style>"
        + WORKBENCH_FEEDBACK_CSS
    )


def build_ui() -> None:
    ensure_project_dirs(paths)
    state.setdefault("source_path", str(paths.input))
    state.setdefault("destination_path", str(paths.output))
    if not state["status"]:
        state["status"] = tr("idle")
    if active_theme_mode() == "dark":
        ui.dark_mode().enable()
    else:
        ui.dark_mode().disable()
    add_styles()
    initial_terminal_html = terminal_log_body_html()
    initial_activity_html = terminal_activity_block_html()

    with ui.header().classes("audion-header h-[42px] items-center justify-between px-4"):
        ui.label(app_title()).classes("audion-header-title text-lg font-bold")
        with ui.row().classes("audion-header-controls items-center gap-2"):
            ui.icon("palette").classes("text-lg")
            ui.select(
                options=theme_options(),
                value=active_theme(),
                on_change=theme_change_handler,
            ).props("dense outlined options-dense dropdown-icon=expand_more").classes("audion-theme-select")
            ui.button(tr("lang_switch"), on_click=toggle_language).props("dense flat").classes("audion-action rounded-lg")
            cancel_button = ui.button(tr("cancel"), on_click=lambda: state.update({"cancel": True})).props("dense flat color=negative")
            cancel_button.visible = False

    with ui.element("div").classes("audion-shell"):
        with ui.column().classes("audion-pane audion-scroll gap-3"):
            with ui.column().classes("audion-panel audion-workspace-panel w-full gap-2 p-2"):
                WORKBENCH_RENDERER.render_address_rows()
                WORKBENCH_RENDERER.render_action_bar()

            ui.label(f"{em('operations')}{tr('operations')}").classes("text-lg font-bold")
            command_tree()

        ui.element("div").classes("audion-splitter").props('title="Resize panels"')

        with ui.element("div").classes("audion-pane audion-right gap-2 pt-3"):
            with ui.column().classes("audion-panel w-full gap-2 p-3"):
                        with ui.element("div").classes(status_row_classes()) as status_row:
                            status_dot_main = ui.element("span").classes("audion-status-dot-mark")
                            status_state_label = ui.label(status_state_text()).classes("audion-status-state")
                            status_label = ui.label(str(state["status"])).classes("audion-status-message")
                            status_clock = ui.label(elapsed_text(None)).classes("audion-status-clock")
                            with ui.element("div").classes("audion-status-bar"):
                                status_bar_fill = ui.element("i").style("width: 0%")
                            status_percent = ui.label(progress_text()).classes("audion-status-percent")

            with ui.column().classes("audion-terminal-panel w-full gap-2 p-3"):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{em('log')}{tr('log')}").classes("text-base font-semibold")
                    ui.space()
                    ui.button(tr("logs"), on_click=lambda: open_folder(paths.logs)).props("dense flat").classes("audion-action rounded-lg").tooltip(audion_folder_button_tooltip("logs", paths.logs))
                    ui.button(tr("report"), on_click=lambda: open_folder(paths.report)).props("dense flat").classes("audion-action rounded-lg").tooltip(audion_folder_button_tooltip("report", paths.report))
                    ui.button(tr("config"), on_click=lambda: open_folder(paths.config)).props("dense flat").classes("audion-action rounded-lg").tooltip(audion_folder_button_tooltip("config", paths.config))
                    clear_log_button = ui.button(icon="delete_sweep", on_click=reset_terminal_log).props("dense flat round").classes("audion-action audion-log-icon-button")
                    clear_log_button.tooltip(audion_terminal_action_tooltip("clear_terminal_window"))
                    expand_log_button = ui.button(icon="open_in_full", on_click=lambda: log_dialog.open()).props("dense flat round").classes("audion-action audion-log-icon-button")
                    expand_log_button.tooltip(audion_terminal_action_tooltip("expand"))
                with ui.element("div").classes("audion-terminal w-full min-h-[58vh]"):
                    log_view = ui.html(initial_terminal_html, sanitize=False, tag="pre").classes("audion-terminal-pre")
                    activity_view = ui.html(initial_activity_html, sanitize=False)
                terminal_command_bar()
                with ui.row().classes("audion-terminal-footer w-full items-center gap-2 px-1 pt-1"):
                    status_dot = ui.label("●").classes(status_dot_classes())
                    terminal_status_label = ui.label(str(state["status"])).classes("min-w-0 flex-1 truncate text-xs")

    with ui.dialog() as log_dialog:
        with ui.card().classes("audion-dialog h-[92vh] w-[92vw] rounded-lg p-3"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"{em('log')}{tr('log')}").classes("text-base font-semibold")
                ui.space()
                ui.button(tr("config"), on_click=lambda: open_folder(paths.config)).props("dense flat").classes("audion-action rounded-lg").tooltip(audion_folder_button_tooltip("config", paths.config))
                clear_dialog_log_button = ui.button(icon="delete_sweep", on_click=reset_terminal_log).props("dense flat round").classes("audion-action audion-log-icon-button")
                clear_dialog_log_button.tooltip(audion_terminal_action_tooltip("clear_terminal_window"))
                ui.button(tr("close"), on_click=log_dialog.close).props("dense flat").classes("audion-action rounded-lg").tooltip(audion_terminal_action_tooltip("close"))
            with ui.element("div").classes("audion-terminal audion-terminal-expanded w-full"):
                expanded_log_view = ui.html(initial_terminal_html, sanitize=False, tag="pre").classes("audion-terminal-pre")
                expanded_activity_view = ui.html(initial_activity_html, sanitize=False)

    ui.run_javascript(
        """
        (() => {
          const storageKey = 'audion_get_terminal_width_px';
          const defaultWidth = 500;
          const minLeft = 520;
          const minRight = 460;

          const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

          const applyWidth = (width) => {
            const shell = document.querySelector('.audion-shell');
            if (!shell) return;
            const rect = shell.getBoundingClientRect();
            const maxRight = Math.max(minRight, rect.width - minLeft - 40);
            const next = clamp(Number(width) || defaultWidth, minRight, maxRight);
            shell.style.setProperty('--audion-terminal-width', `${Math.round(next)}px`);
            localStorage.setItem(storageKey, String(Math.round(next)));
          };

          const setup = () => {
            const shell = document.querySelector('.audion-shell');
            const splitter = document.querySelector('.audion-splitter');
            if (!shell || !splitter) {
              setTimeout(setup, 80);
              return;
            }
            if (splitter.dataset.audionReady === '1') return;
            splitter.dataset.audionReady = '1';

            applyWidth(localStorage.getItem(storageKey) || defaultWidth);

            let dragging = false;
            const updateFromEvent = (event) => {
              if (!dragging) return;
              const rect = shell.getBoundingClientRect();
              const rightWidth = rect.right - event.clientX - 10;
              applyWidth(rightWidth);
            };

            const beginDrag = (event) => {
              dragging = true;
              if (event.pointerId !== undefined) {
                splitter.setPointerCapture?.(event.pointerId);
              }
              document.body.classList.add('audion-resizing');
              event.preventDefault();
            };

            const endDrag = (event) => {
              dragging = false;
              if (event.pointerId !== undefined) {
                splitter.releasePointerCapture?.(event.pointerId);
              }
              document.body.classList.remove('audion-resizing');
            };

            splitter.addEventListener('pointerdown', beginDrag);
            splitter.addEventListener('pointermove', updateFromEvent);
            splitter.addEventListener('pointerup', endDrag);
            splitter.addEventListener('pointercancel', () => {
              dragging = false;
              document.body.classList.remove('audion-resizing');
            });
            splitter.addEventListener('mousedown', beginDrag);
            window.addEventListener('mousemove', updateFromEvent);
            window.addEventListener('mouseup', endDrag);
            window.addEventListener('resize', () => applyWidth(localStorage.getItem(storageKey) || defaultWidth));
          };

          setup();
        })();
        """
    )
    working_title = "Scanning..."
    working_body = (
        "WinGet обновляет список установленных ID. GUI продолжит работу."
        if settings.language == "ru"
        else "WinGet is updating the installed ID list. The GUI will continue shortly."
    )
    ui.add_head_html(
        f"""
        <script>
          (() => {{
            const title = {json.dumps(working_title)};
            const body = {json.dumps(working_body)};
            const patchPopup = () => {{
              const popup = document.getElementById('popup');
              if (!popup) {{
                setTimeout(patchPopup, 50);
                return;
              }}
              const spans = popup.querySelectorAll('span');
              if (spans[0]) spans[0].textContent = title;
              if (spans[1]) spans[1].textContent = body;
            }};
            if (document.readyState === 'loading') {{
              document.addEventListener('DOMContentLoaded', patchPopup);
            }} else {{
              patchPopup();
            }}
          }})();
        </script>
        """
    )

    # Terminal text is element content, not a JavaScript side effect: NiceGUI owns the DOM,
    # so the log is written through `ui.html.content` and survives reconnects and re-renders.
    # The only thing left to JavaScript is scrolling, which the DOM model cannot express.
    ui.run_javascript(
        """
        (() => {
          const stickToBottom = new WeakMap();
          const isAtBottom = (el) => Math.abs(el.scrollHeight - el.scrollTop - el.clientHeight) <= 6;
          const hasSelection = (el) => {
            const selection = window.getSelection?.();
            if (!selection || selection.isCollapsed) return false;
            return el.contains(selection.anchorNode) || el.contains(selection.focusNode);
          };
          const attach = (el) => {
            if (stickToBottom.has(el)) return;
            stickToBottom.set(el, true);
            el.scrollTop = el.scrollHeight;
            el.addEventListener('scroll', () => stickToBottom.set(el, isAtBottom(el)));
            new MutationObserver(() => {
              if (stickToBottom.get(el) && !hasSelection(el)) {
                el.scrollTop = el.scrollHeight;
              }
            }).observe(el, { childList: true, subtree: true, characterData: true });
          };
          let scanScheduled = false;
          const scan = () => {
            scanScheduled = false;
            document.querySelectorAll('.audion-terminal').forEach(attach);
          };
          scan();
          new MutationObserver(() => {
            if (scanScheduled) return;
            scanScheduled = true;
            setTimeout(scan, 0);
          }).observe(document.body, { childList: true, subtree: true });
        })();
        """
    )

    last_log_version = {"value": -1}
    last_activity_version = {"value": -1}

    refresh_timer: Any | None = None

    # Every one of these used to be written twice a second whether or not it had
    # changed, so an idle window still sent ten element updates a second. Holding
    # the last value makes an idle panel cost nothing and pays for the clock.
    shown = {"status": None, "state": None, "row": None, "clock": None, "percent": None, "fill": None}
    run_clock: dict[str, float | None] = {"started": None, "frozen": None}

    def refresh() -> None:
        nonlocal refresh_timer
        try:
            running = bool(state["running"])
            if running and run_clock["started"] is None:
                run_clock["started"] = time.monotonic()
                run_clock["frozen"] = None
            elif not running and run_clock["started"] is not None:
                run_clock["frozen"] = time.monotonic() - run_clock["started"]
                run_clock["started"] = None
            seconds = (
                time.monotonic() - run_clock["started"]
                if run_clock["started"] is not None
                else run_clock["frozen"]
            )

            def show(key: str, value: Any, assign: Any) -> None:
                if shown[key] != value:
                    shown[key] = value
                    assign(value)

            message = str(state["status"])
            show("status", message, lambda value: (
                setattr(status_label, "text", value),
                setattr(terminal_status_label, "text", value),
            ))
            show("state", status_state_text(), lambda value: setattr(status_state_label, "text", value))
            show("row", status_row_classes(), lambda value: (
                status_row.classes(replace=value),
                status_dot.classes(replace=status_dot_classes()),
            ))
            show("clock", elapsed_text(seconds), lambda value: setattr(status_clock, "text", value))
            show("percent", progress_text(), lambda value: setattr(status_percent, "text", value))
            show("fill", f"{float(state['progress']) * 100:.1f}%",
                lambda value: status_bar_fill.style(f"width: {value}"))
            log_version = int(state["log_version"])
            if log_version != last_log_version["value"]:
                last_log_version["value"] = log_version
                # `state["lines"]` is already capped at TERMINAL_RENDER_LINE_LIMIT, so a full
                # re-render stays bounded and keeps ANSI colour runs correct across lines.
                log_html = terminal_log_body_html()
                log_view.set_content(log_html)
                expanded_log_view.set_content(log_html)
            activity_version = int(state["terminal_activity_version"])
            if activity_version != last_activity_version["value"]:
                last_activity_version["value"] = activity_version
                # The live line stays a separate element, so a log update never restarts
                # the CSS spinner and progress frames never enter the append stream.
                activity_html = terminal_activity_block_html()
                activity_view.set_content(activity_html)
                expanded_activity_view.set_content(activity_html)
            cancel_button.visible = bool(state["running"])
        except RuntimeError as exc:
            message = str(exc)
            if "slot belongs to has been deleted" not in message and "current slot cannot be determined" not in message:
                raise
            logging.warning("NiceGUI refresh timer stopped because the client slot was deleted.")
            if refresh_timer is not None:
                refresh_timer.deactivate()

    refresh_timer = ui.timer(0.5, refresh)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audion NiceGUI shell.")
    parser.add_argument("--host", default=str(ui_info.get("host", "127.0.0.1")))
    parser.add_argument("--port", type=int, default=int(ui_info.get("port", 8080)))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def assert_gui_host_allowed(host: str) -> None:
    normalized = str(host or "").strip().lower().strip("[]")
    try:
        is_loopback = normalized == "localhost" or ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        is_loopback = normalized == "localhost"
    allow_remote = str(os.environ.get("AUDION_ALLOW_REMOTE_GUI", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not is_loopback and not allow_remote:
        raise SystemExit(
            "Refusing non-loopback host for a GUI with process execution. "
            "Use 127.0.0.1/localhost/::1, or set AUDION_ALLOW_REMOTE_GUI=1 explicitly."
        )


def build_ui_once() -> dict[str, int]:
    """Build the whole page once, headlessly, and report what came of it.

    `--smoke` used to print a line and return, so an app could ship a `build_ui`
    that raised on its first statement and still pass — twice in this fleet it did.
    Here the page is actually built: no browser and no HTTP request, so whatever
    the app defers until a client attaches is skipped, but every widget is
    constructed and the stylesheet has to arrive.
    """
    import asyncio
    import logging
    import re

    from nicegui import core
    from nicegui.client import Client
    from nicegui.page import page as page_definition

    async def build() -> tuple[int, str]:
        core.loop = asyncio.get_running_loop()
        # Work deferred to a connected browser fails here and says nothing about
        # the build. An exception raised by build_ui itself still propagates.
        core.loop.set_exception_handler(lambda _loop, _context: None)
        logging.getLogger("nicegui").setLevel(logging.CRITICAL)
        client = Client(page_definition("/__smoke__"))
        with client:
            build_ui()
        report = len(client.elements), client.shared_head_html + client.head_html
        # The page starts work that waits for a browser to attach. Nothing will
        # attach, so stop it deliberately instead of letting the loop close on it.
        pending = asyncio.all_tasks(core.loop) - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return report

    element_count, head = asyncio.run(build())
    if element_count < 2:
        raise RuntimeError("build_ui produced no widgets")
    # Token prefixes differ between apps, so look for any custom property rather
    # than for one project's naming.
    if not re.search(r"--[\w-]+\s*:", head):
        raise RuntimeError("the stylesheet never reached the page")
    return {"elements": element_count, "stylesheet_bytes": len(head)}


def main() -> int:
    args = parse_args()
    assert_gui_host_allowed(args.host)
    ensure_project_dirs(paths)
    if args.smoke:
        try:
            report = build_ui_once()
        except Exception as error:  # noqa: BLE001
            print(f"FAIL nicegui shell: {ROOT}: {error}")
            return 1
        print(
            f"OK nicegui shell: {ROOT}"
            f" | widgets={report['elements']}"
            f" | stylesheet={report['stylesheet_bytes']} bytes"
        )
        return 0

    if port_is_open(args.host, args.port):
        url = f"http://{args.host}:{args.port}/"
        print(f"GUI already appears to be running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    ui.run(
        root=build_ui,
        title=app_title(),
        host=args.host,
        port=args.port,
        reload=False,
        native=False,
        show=not args.no_browser,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
