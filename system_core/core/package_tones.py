"""The looks of a card of a list of ticks: its tone and the small buttons it carries.

A package wears the colour of what it is - a browser, a runtime, a tool for video - told by the
words of its id and name, else by the block of the catalogue it stands in. A version of a vendor
wears the colour of its mark: a keeper gold, the newest release green, a beta amber, the rest
none. The tables are those of the former window on NiceGUI (`ui_nicegui/app.py` of 2.17), unchanged.
"""
from __future__ import annotations

import re
from typing import Any

from system_core.services.package_links import package_archive_type

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

CARD_FIELD_PREFIX = "packages_"
WINDOWS_FEATURE_PREFIX = "windows-feature:"


def field_suffix(field_id: str, known: dict[str, str]) -> str:
    key = field_id.strip().lower()
    if key in known:
        return known[key]
    for prefix in ("packages_", "uninstall_"):
        if key.startswith(prefix):
            return key.removeprefix(prefix)
    return key


def _matches(text_raw: str, text_words: str, keyword: str) -> bool:
    needle_raw = keyword.casefold()
    needle_words = needle_raw.replace("_", " ").replace("-", " ").replace(".", " ")
    return needle_raw in text_raw or needle_words in text_words


def package_tone(field_id: str, option_key: Any, option_text: str, known: dict[str, str]) -> str:
    text_raw = f"{option_key} {option_text}".casefold()
    text_words = text_raw.replace("_", " ").replace("-", " ").replace(".", " ")
    for tone, keywords in PACKAGE_TONE_KEYWORDS:
        if any(_matches(text_raw, text_words, keyword) for keyword in keywords):
            return tone
    return PACKAGE_FIELD_DEFAULT_TONES.get(field_suffix(field_id, known), "default")


def card_tone(field: dict[str, Any], field_id: str, option_key: Any, option_text: str, known: dict[str, str]) -> str:
    """The tone of a card. A field may name it outright (`tone: video`); marks in the caption win over everything."""
    text = str(option_text)
    if "\u2605" in text:
        return "gold"
    if re.search(r"\blatest\b", text):
        return "latest"
    if re.search(r"\b(beta|preview|insider|rs_prerelease)\b", text, re.IGNORECASE):
        return "beta"
    named = str(field.get("tone") or "").strip().lower()
    if str(field.get("card_actions") or "").strip() == "vendor":
        # Ordinary versions stay quiet, so the marked ones carry the meaning.
        return named or "plain"
    return named or package_tone(field_id, option_key, option_text, known)


def package_id_of(field_id: str, option_key: Any) -> str:
    """The WinGet id behind a tick, or '' when the option is not one: a group name, a Windows feature."""
    if not field_id.strip().lower().startswith(CARD_FIELD_PREFIX):
        return ""
    package_id = str(option_key or "").strip()
    if not package_id or " " in package_id or "." not in package_id:
        return ""
    if package_id.lower().startswith(WINDOWS_FEATURE_PREFIX):
        return ""
    return package_id


def card_files(field_id: str, option_key: Any) -> dict[str, bool]:
    """Which of the three small buttons a package card has: its installer, its archive or portable build, its page."""
    package_id = package_id_of(field_id, option_key)
    if not package_id:
        return {}
    return {"installer": True, "archive": bool(package_archive_type(package_id)), "page": True}
