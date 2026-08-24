from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Iterable
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import yaml

from system_core.core.ansi import strip_ansi
from system_core.core.conpty import ConsoleProcess, ConsoleUnavailable, console_supported
from system_core.core.jobs import JobContext, hidden_subprocess_kwargs, utf8_subprocess_env
from system_core.core.output_decode import decode_process_bytes, decode_process_output
from system_core.core.stream_output import StreamAssembler, is_spinner_only
from system_core.services.package_links import (
    download_folder_name,
    github_repo_from_url,
    package_archive_github,
    package_archive_type,
    package_installer_github,
    pick_windows_asset,
    resolve_package_page,
)


PACKAGE_FIELD_ORDER = (
    "packages_system",
    "packages_dev",
    "packages_ai",
    "packages_pkms",
    "packages_office",
    "packages_media_images",
    "packages_media_audio",
    "packages_media_video",
    "packages_network",
    "packages_hardware",
    "packages_msvc",
    "packages_msvc_legacy",
)

PIN_FIELD_ORDER = ("packages_pins", "packages_installed_pins")
AVAILABLE_UPDATE_FIELD_ORDER = ("packages_available_updates",)
INSTALLED_UNINSTALL_FIELD_ORDER = ("packages_installed_uninstall",)
GROUPED_UNINSTALL_FIELD_ORDER = (
    "uninstall_system",
    "uninstall_dev",
    "uninstall_ai",
    "uninstall_pkms",
    "uninstall_office",
    "uninstall_media_images",
    "uninstall_media_audio",
    "uninstall_media_video",
    "uninstall_network",
    "uninstall_hardware",
    "uninstall_msvc",
    "uninstall_custom",
    "uninstall_other",
)

UNINSTALL_FIELD_TO_PACKAGE_FIELD = {
    "uninstall_system": "packages_system",
    "uninstall_dev": "packages_dev",
    "uninstall_ai": "packages_ai",
    "uninstall_pkms": "packages_pkms",
    "uninstall_office": "packages_office",
    "uninstall_media_images": "packages_media_images",
    "uninstall_media_audio": "packages_media_audio",
    "uninstall_media_video": "packages_media_video",
    "uninstall_network": "packages_network",
    "uninstall_hardware": "packages_hardware",
    "uninstall_msvc": "packages_msvc",
}

CUSTOM_LIST_NAME = "custom.txt"
PINS_LIST_NAME = "pins.txt"
INSTALLED_UNINSTALL_ALIASES_NAME = "installed_uninstall_aliases.yaml"
LOG_CLEANUP_PRESERVE_NAMES = {".gitkeep", "gui_server.pid"}
PROTECTED_UNINSTALL_PATTERNS = (
    "Microsoft.AppInstaller",
    "Microsoft.DesktopAppInstaller",
    "Microsoft.WindowsTerminal",
    "Microsoft.PowerShell",
    "Microsoft.Edge",
    "Microsoft.Edge.*",
    "Microsoft.VCRedist.*",
    "Microsoft.DotNet.*",
    "Microsoft.WindowsAppRuntime.*",
    "Microsoft.UI.Xaml.*",
    "Microsoft.VCLibs.*",
    "Microsoft.WSL",
    "ARP\\Machine\\*\\Microsoft Edge",
    "ARP\\User\\*\\Microsoft Edge",
)
PROTECTED_UNINSTALL_CONFIRM_KEYS = (
    "confirm_protected_uninstall",
    "confirm_protected_uninstall_by_id",
    "confirm_protected_uninstall_selected",
)
ANSI_YELLOW = "\x1b[33m"
ANSI_RESET = "\x1b[0m"

CONFIG_LISTS = {
    "system": "system.txt",
    "dev": "dev.txt",
    "ai": "ai.txt",
    "pkms": "pkms.txt",
    "office": "office.txt",
    "media": "media.txt",
    "network": "browsers-vpn.txt",
    "hardware": "hardware-benchmarks.txt",
    "custom": CUSTOM_LIST_NAME,
    "pins": PINS_LIST_NAME,
}

CONFIG_LIST_TARGETS: dict[str, tuple[str, str | None, str | None]] = {
    "system": ("system.txt", "packages_system", None),
    "dev": ("dev.txt", "packages_dev", None),
    "ai": ("ai.txt", "packages_ai", None),
    "pkms": ("pkms.txt", "packages_pkms", None),
    "office": ("office.txt", "packages_office", None),
    "media_images": ("media.txt", "packages_media_images", "Images"),
    "media_audio": ("media.txt", "packages_media_audio", "Audio"),
    "media_video": ("media.txt", "packages_media_video", "Video"),
    "network": ("browsers-vpn.txt", "packages_network", None),
    "hardware": ("hardware-benchmarks.txt", "packages_hardware", None),
    "custom": (CUSTOM_LIST_NAME, None, None),
    "pins": (PINS_LIST_NAME, None, None),
}

MEDIA_SECTION_NAMES = ("Images", "Audio", "Video")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


UNINSTALL_USER_WAIT_SECONDS = _env_float("AUDION_GET_UNINSTALL_WAIT_SECONDS", 600.0)
UNINSTALL_USER_WAIT_INTERVAL_SECONDS = _env_float("AUDION_GET_UNINSTALL_WAIT_INTERVAL_SECONDS", 5.0)
INSTALLED_OPTIONS_CACHE_SECONDS = _env_float("AUDION_GET_INSTALLED_CACHE_SECONDS", 60.0)
AVAILABLE_UPDATE_OPTIONS_CACHE_SECONDS = _env_float("AUDION_GET_AVAILABLE_UPDATE_CACHE_SECONDS", 60.0)
# The live line is replaced in place, so it can refresh far faster than a log row.
CONSOLE_PROGRESS_INTERVAL_SECONDS = _env_float("AUDION_GET_CONSOLE_PROGRESS_INTERVAL", 0.2)
# Disk log keeps a coarse trail instead of every repaint frame. WinGet prints
# `<done> / <total>`, so the cadence follows the size of what is downloading.
CONSOLE_TRACE_INTERVAL_SECONDS = _env_float("AUDION_GET_CONSOLE_TRACE_INTERVAL", 1.0)
CONSOLE_TRACE_STEPS: tuple[tuple[float, float], ...] = (
    (100.0, _env_float("AUDION_GET_CONSOLE_TRACE_LARGE", 10.0)),
    (50.0, _env_float("AUDION_GET_CONSOLE_TRACE_MEDIUM", 5.0)),
    (10.0, _env_float("AUDION_GET_CONSOLE_TRACE_SMALL", 1.0)),
)
CONSOLE_STREAM_COLUMNS = _env_int("AUDION_GET_CONSOLE_COLUMNS", 140)
WINGET_RECOVERY_SECONDS = _env_float("AUDION_GET_WINGET_RECOVERY_SECONDS", 120.0)
WINGET_RECOVERY_INTERVAL_SECONDS = _env_float("AUDION_GET_WINGET_RECOVERY_INTERVAL_SECONDS", 4.0)
WINGET_PROBE_TIMEOUT_SECONDS = _env_float("AUDION_GET_WINGET_PROBE_TIMEOUT_SECONDS", 25.0)
# The unfiltered `winget upgrade --include-unknown` scan reads every ARP entry,
# so it needs more room than the source-filtered one used to.
UPGRADE_SCAN_TIMEOUT_SECONDS = _env_float("AUDION_GET_UPGRADE_SCAN_TIMEOUT_SECONDS", 90.0)

# App Installer ships winget itself: updating it swaps the execution alias, so
# the very next winget call can fail until Windows finishes re-registering it.
APP_INSTALLER_PACKAGE_IDS = frozenset({"microsoft.appinstaller", "microsoft.desktopappinstaller"})

CONSOLE_STREAMING_STATE: dict[str, bool] = {"enabled": True}
_WINGET_EXECUTABLE_LOCK = threading.Lock()
_WINGET_EXECUTABLE: str | None = None

_INSTALLED_OPTIONS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_INSTALLED_OPTIONS_CACHE_LOCK = threading.Lock()
_AVAILABLE_UPDATE_OPTIONS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
_AVAILABLE_UPDATE_OPTIONS_CACHE_LOCK = threading.Lock()

PACKAGE_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.+-])"
    r"(?=[A-Za-z0-9_.+-]*[A-Za-z])"
    r"[A-Za-z0-9][A-Za-z0-9_+-]*(?:\.[A-Za-z0-9][A-Za-z0-9_+-]*)+"
    r"(?![A-Za-z0-9_.+-])"
)
MOJIBAKE_LABEL_MARKERS = (
    "�",
    "╨",
    "╤",
    "╬",
    "├",
    "┬",
    "тА",
    "Ã",
    "Ð",
    "Ñ",
    "â",
    "Р°",
    "Рµ",
    "Рё",
    "Рѕ",
    "Рґ",
    "РЅ",
    "СЂ",
    "СЏ",
    "СЃ",
    "С‚",
    "СЊ",
    "С‹",
    "С‡",
    "С€",
    "С‰",
)
POWERSHELL_UTF8_PREAMBLE = (
    "$audionUtf8 = [System.Text.UTF8Encoding]::new($false); "
    "[Console]::InputEncoding = $audionUtf8; "
    "[Console]::OutputEncoding = $audionUtf8; "
    "$OutputEncoding = $audionUtf8; "
    "if (Get-Variable PSStyle -ErrorAction SilentlyContinue) { $PSStyle.OutputRendering = 'ANSI' }; "
)


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class PackageActionResult:
    exit_code: int
    status: str
    notes: tuple[str, ...] = ()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _selected_values(context: JobContext, keys: Iterable[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for key in keys:
        for package_id in _string_list(context.operation.parameters.get(key, [])):
            lower = package_id.lower()
            if lower in seen:
                continue
            seen.add(lower)
            selected.append(package_id)
    return selected


def _param_text(context: JobContext, key: str, default: str = "") -> str:
    return str(context.operation.parameters.get(key, default) or "").strip()


def _param_bool(context: JobContext, key: str, default: bool = False) -> bool:
    value = context.operation.parameters.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _resolve_project_path(context: JobContext, raw_path: str) -> Path:
    path_text = raw_path.strip().strip('"')
    if not path_text:
        raise RuntimeError("Path field is empty.")
    path = Path(os.path.expandvars(path_text)).expanduser()
    if not path.is_absolute():
        path = context.paths.root / path
    return path


def _project_path(context: JobContext, value: Any, fallback: str) -> Path:
    text = str(value or "").strip()
    if not text:
        return getattr(context.paths, fallback)
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute():
        path = context.paths.root / path
    return path


def _display_command(command: list[str]) -> str:
    return " ".join(f'"{item}"' if " " in item else item for item in command)


def unbuffer_python_command(command: list[str]) -> list[str]:
    if len(command) < 2:
        return command
    executable = Path(command[0]).name.lower()
    if executable not in {"python", "python.exe", "python3", "python3.exe"}:
        return command
    if any(arg == "-u" or arg.startswith("-u") for arg in command[1:3]):
        return command
    return [command[0], "-u", *command[1:]]


def _is_spinner_only_line(line: str) -> bool:
    return is_spinner_only(line)


def _decoded_output_lines(raw_line: bytes | str) -> list[str]:
    text = decode_process_bytes(raw_line) if isinstance(raw_line, bytes) else str(raw_line)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for part in text.split("\n"):
        line = part.rstrip()
        if not line or _is_spinner_only_line(line):
            continue
        lines.append(line)
    return lines


# WinGet goes quiet while Windows shows the UAC prompt for a package installer.
ELEVATION_HINT_MARKERS = (
    "от имени администратора",
    "запросит запуск",
    "требует прав администратора",
    "с повышенными правами",
    "as administrator",
    "administrator privileges",
    "requires elevation",
    "elevated privileges",
)


def _looks_like_elevation_prompt(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ELEVATION_HINT_MARKERS)


# `  91.0 MB / 328 MB`, `1.2 GiB / 3.4 GiB` and similar download counters.
PROGRESS_SIZE_PATTERN = re.compile(
    r"([\d]+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*/\s*([\d]+(?:[.,]\d+)?)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)",
    re.IGNORECASE,
)
PROGRESS_SIZE_UNITS = {
    "b": 1.0 / (1024.0 * 1024.0),
    "kb": 1.0 / 1024.0,
    "kib": 1.0 / 1024.0,
    "mb": 1.0,
    "mib": 1.0,
    "gb": 1024.0,
    "gib": 1024.0,
    "tb": 1024.0 * 1024.0,
    "tib": 1024.0 * 1024.0,
}


def _progress_total_mib(text: str) -> float | None:
    """Total download size of a progress frame, in MiB."""
    match = PROGRESS_SIZE_PATTERN.search(text)
    if match is None:
        return None
    try:
        amount = float(match.group(3).replace(",", "."))
    except ValueError:
        return None
    return amount * PROGRESS_SIZE_UNITS.get(match.group(4).lower(), 1.0)


def _progress_trace_interval(total_mib: float | None) -> float:
    if total_mib is None:
        return CONSOLE_TRACE_INTERVAL_SECONDS
    for threshold, interval in CONSOLE_TRACE_STEPS:
        if total_mib > threshold:
            return interval
    return CONSOLE_TRACE_INTERVAL_SECONDS


def _context_language(context: JobContext) -> str:
    value = str(context.operation.parameters.get("ui_language") or "").strip().lower()
    return "ru" if value == "ru" else "en"


def _activity_text(context: JobContext, ru: str, en: str) -> str:
    return ru if _context_language(context) == "ru" else en


# Some entries in the package groups are Windows optional features, not WinGet
# packages: .NET Framework 3.5 ships only as `NetFx3` and has no WinGet ID.
# They travel through the same checkbox batch under this prefix.
WINDOWS_FEATURE_PREFIX = "windows-feature:"
WINDOWS_FEATURE_REGISTRY_PROBES: dict[str, tuple[str, str]] = {
    # Feature state without elevation; DISM would demand Administrator.
    "netfx3": (r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v3.5", "Install"),
}
DISM_ELEVATION_EXIT_CODES = {5, 740, 1726}


def _is_windows_feature_id(package_id: str) -> bool:
    return str(package_id).strip().lower().startswith(WINDOWS_FEATURE_PREFIX)


def _windows_feature_name(package_id: str) -> str:
    return str(package_id).split(":", 1)[1].strip() if ":" in str(package_id) else ""


def _windows_feature_enabled(package_id: str) -> bool | None:
    """True/False when the state is known, None when it cannot be read cheaply."""
    probe = WINDOWS_FEATURE_REGISTRY_PROBES.get(_windows_feature_name(package_id).lower())
    if not probe or os.name != "nt":
        return None
    key_path, value_name = probe
    try:
        import winreg
    except ImportError:
        return None
    for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
                value, _kind = winreg.QueryValueEx(key, value_name)
                return int(value) == 1
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return False


def _dism_executable() -> str:
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return str(Path(system_root) / "System32" / "dism.exe")


def _windows_feature_action_result(context: JobContext, action: str, package_id: str) -> PackageActionResult:
    feature = _windows_feature_name(package_id)
    if not feature:
        context.log(f"{ANSI_YELLOW}[WARN] Windows feature name is missing: {package_id}{ANSI_RESET}")
        return PackageActionResult(1, "failed", ("windows_feature_name_missing",))

    enabled = _windows_feature_enabled(package_id)
    if action == "check":
        if enabled is None:
            context.log(f"[INFO] Windows feature {feature}: state unknown without DISM.")
            return PackageActionResult(0, "checked", ("windows_feature_state_unknown",))
        context.log(f"[INFO] Windows feature {feature}: {'enabled' if enabled else 'not enabled'}.")
        return PackageActionResult(0 if enabled else 1, "checked" if enabled else "not_found")

    if action in {"update", "pin", "uninstall"}:
        context.log(f"[INFO] Windows feature {feature} is skipped for action '{action}'.")
        return PackageActionResult(0, "skipped", (f"windows_feature_not_supported_for_{action}",))

    if enabled:
        context.log(f"[OK] Windows feature {feature} is already enabled.")
        return PackageActionResult(0, "already_installed", ("windows_feature_already_enabled",))

    context.log(f"[INFO] Enabling Windows feature {feature} through DISM (payload comes from Windows Update).")
    context.activity(_activity_text(context, f"Установка компонента Windows: {feature}", f"Enabling Windows feature: {feature}"))
    result = _run_process(
        context,
        [_dism_executable(), "/online", "/enable-feature", f"/featurename:{feature}", "/All", "/NoRestart"],
        check=False,
        console=True,
    )
    if result.exit_code in DISM_ELEVATION_EXIT_CODES:
        context.log(
            f"{ANSI_YELLOW}[WARN] DISM needs Administrator rights. Restart Audion Get Tools elevated "
            f"(launcher_gui.cmd with AUDION_GUI_ELEVATE=1) and enable {feature} again.{ANSI_RESET}"
        )
        return PackageActionResult(result.exit_code, "failed", ("windows_feature_needs_elevation",))
    if result.exit_code == 0:
        context.log(f"[OK] Windows feature {feature} is enabled.")
        return PackageActionResult(0, "installed", ("windows_feature_enabled",))
    if _windows_feature_enabled(package_id):
        # DISM reports a restart-pending state with a non-zero code often enough
        # that the registry has the final word here.
        context.log(f"[OK] Windows feature {feature} is reported as enabled; a restart may still be pending.")
        return PackageActionResult(0, "installed", ("windows_feature_restart_pending",))
    return PackageActionResult(result.exit_code, "failed", ("windows_feature_enable_failed",))


WINGET_ACTIVITY_LABELS: dict[str, tuple[str, str]] = {
    "install": ("Установка пакета", "Installing package"),
    "upgrade": ("Обновление пакета", "Updating package"),
    "download": ("Скачивание пакета", "Downloading package"),
    "uninstall": ("Удаление пакета", "Removing package"),
    "pin": ("Закрепление пакета", "Pinning package"),
    "list": ("Проверка пакета", "Checking package"),
    "show": ("Проверка пакета", "Checking package"),
    "search": ("Поиск в реестре WinGet", "Searching the WinGet registry"),
    "export": ("Экспорт списка пакетов", "Exporting the package list"),
    "import": ("Импорт списка пакетов", "Importing the package list"),
    "source": ("Работа с источниками WinGet", "Working with WinGet sources"),
}


def _winget_activity_label(context: JobContext, args: list[str]) -> str:
    verb = next((item for item in args if not item.startswith("-")), "")
    labels = WINGET_ACTIVITY_LABELS.get(verb.lower())
    if labels is None:
        return _activity_text(context, "Выполняется WinGet", "Running WinGet")
    text = _activity_text(context, labels[0], labels[1])
    try:
        package_id = args[args.index("--id") + 1]
    except (ValueError, IndexError):
        return f"{text}..."
    return f"{text}: {package_id}"


class OperationCancelledError(RuntimeError):
    """Raised when the user cancels the running operation."""


def _cancelled_error() -> OperationCancelledError:
    return OperationCancelledError("Operation cancelled by user.")


def _console_streaming_enabled() -> bool:
    if not CONSOLE_STREAMING_STATE["enabled"]:
        return False
    value = str(os.environ.get("AUDION_GET_CONSOLE_STREAM", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _disable_console_streaming(context: JobContext, reason: str) -> None:
    CONSOLE_STREAMING_STATE["enabled"] = False
    context.log(f"{ANSI_YELLOW}[WARN] Live console output is unavailable, falling back to plain pipes: {reason}{ANSI_RESET}")


def _process_environment(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = utf8_subprocess_env(
        {
            "AUDION_DISABLE_FZF": "1",
            "AUDION_GUI_TERMINAL": "1",
            "CLICOLOR": "1",
            "CLICOLOR_FORCE": "1",
            "FORCE_COLOR": "1",
        }
    )
    if extra_env:
        env.update(extra_env)
    env.pop("NO_COLOR", None)
    if env.get("CLICOLOR") == "0":
        env["CLICOLOR"] = "1"
    return env


def _progress_ticker(context: JobContext, start: float):
    state = {"last": start}

    def tick() -> None:
        now = time.monotonic()
        if now - state["last"] >= 0.5:
            context.progress(min(0.95, 0.08 + max(0.0, now - start) / 600.0))
            state["last"] = now

    return tick


def _stream_piped_process(
    context: JobContext,
    command: list[str],
    working_dir: Path,
    env: dict[str, str],
    assembler: StreamAssembler,
    tick,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=str(working_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        **hidden_subprocess_kwargs(),
    )
    assert process.stdout is not None
    try:
        while True:
            chunk = process.stdout.read1(16384)
            if not chunk:
                break
            if context.cancelled():
                context.log("[CANCEL] Terminating child process...")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise _cancelled_error()
            assembler.feed(chunk)
            tick()
    finally:
        try:
            process.stdout.close()
        except OSError:
            pass
    assembler.flush()
    return int(process.wait())


def _stream_console_process(
    context: JobContext,
    command: list[str],
    working_dir: Path,
    env: dict[str, str],
    assembler: StreamAssembler,
    tick,
) -> int:
    with ConsoleProcess(command, cwd=working_dir, env=env, columns=CONSOLE_STREAM_COLUMNS) as process:
        for chunk in process.read_chunks():
            if context.cancelled():
                context.log("[CANCEL] Terminating child process...")
                process.terminate()
                raise _cancelled_error()
            assembler.feed(chunk)
            tick()
        assembler.flush()
        return int(process.wait(timeout=60))


def _run_process(
    context: JobContext,
    command: list[str],
    *,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    check: bool = True,
    console: bool = False,
    activity: str = "",
) -> ProcessResult:
    env = _process_environment(extra_env)
    command = unbuffer_python_command(command)
    working_dir = cwd or context.paths.root
    context.log(f"[CWD] {working_dir}")
    context.log(f"[CMD] {_display_command(command)}")

    lines: list[str] = []
    hinted_elevation = False
    trace_state = {"last": 0.0, "interval": CONSOLE_TRACE_INTERVAL_SECONDS}

    def emit(text: str, is_progress: bool) -> None:
        nonlocal hinted_elevation
        if is_progress:
            # Repaint frames belong to the live line, not to the log body: the
            # terminal replaces them in place and the disk log keeps a trail
            # whose cadence follows the download size.
            context.activity(text)
            plain = strip_ansi(text).strip()
            total_mib = _progress_total_mib(plain)
            if total_mib is not None:
                trace_state["interval"] = _progress_trace_interval(total_mib)
            now = time.monotonic()
            if now - trace_state["last"] >= trace_state["interval"]:
                trace_state["last"] = now
                context.trace(plain)
            return
        context.log(text)
        plain = strip_ansi(text)
        lines.append(plain)
        if activity:
            context.activity(activity)
        if not hinted_elevation and _looks_like_elevation_prompt(plain):
            hinted_elevation = True
            context.log(
                f"{ANSI_YELLOW}[WAIT] Windows is asking for administrator rights (UAC). "
                f"Confirm the prompt; the package stays paused until you answer.{ANSI_RESET}"
            )

    assembler = StreamAssembler(emit, progress_interval=CONSOLE_PROGRESS_INTERVAL_SECONDS)
    tick = _progress_ticker(context, time.monotonic())
    if activity:
        context.activity(activity)

    try:
        use_console = console and _console_streaming_enabled() and console_supported()
        if use_console:
            try:
                exit_code = _stream_console_process(context, command, working_dir, env, assembler, tick)
            except ConsoleUnavailable as exc:
                _disable_console_streaming(context, str(exc))
                exit_code = _stream_piped_process(context, command, working_dir, env, assembler, tick)
        else:
            exit_code = _stream_piped_process(context, command, working_dir, env, assembler, tick)
    finally:
        context.activity("")

    context.log(f"[EXIT] {exit_code}")
    if check and exit_code != 0:
        raise RuntimeError(f"Command failed with exit code {exit_code}.")
    return ProcessResult(exit_code=exit_code, lines=tuple(lines))


def _run_cmd_script(context: JobContext, script: str, args: list[str] | None = None) -> ProcessResult:
    script_path = _resolve_project_path(context, script)
    if not script_path.exists():
        raise RuntimeError(f"Script was not found: {script_path}")
    script_call = subprocess.list2cmdline([str(script_path), *(args or [])])
    command = ["cmd.exe", "/d", "/c", f"chcp 65001 >nul & call {script_call}"]
    return _run_process(context, command)


def _resolve_powershell() -> str:
    bundled_pwsh = Path(__file__).resolve().parents[2] / "system_core" / "powershell" / "pwsh.exe"
    if bundled_pwsh.exists():
        return str(bundled_pwsh)
    return shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"


def terminal_command(context: JobContext) -> dict[str, object]:
    command_text = _param_text(context, "command")
    if not command_text:
        raise RuntimeError("Command is empty.")

    shell = _param_text(context, "shell", "pwsh" if os.name == "nt" else "sh").lower()
    cwd_text = _param_text(context, "cwd", str(context.paths.root))
    cwd = Path(os.path.expandvars(cwd_text)).expanduser()
    if not cwd.is_absolute():
        cwd = context.paths.root / cwd
    if cwd.is_file():
        cwd = cwd.parent
    if not cwd.exists():
        raise RuntimeError(f"Working directory does not exist: {cwd}")

    if os.name == "nt":
        if shell == "cmd":
            command = ["cmd.exe", "/d", "/c", f"chcp 65001 >nul & {command_text}"]
        else:
            powershell = _resolve_powershell()
            powershell_command = POWERSHELL_UTF8_PREAMBLE + command_text
            if Path(powershell).name.lower() == "powershell.exe":
                command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", powershell_command]
            else:
                command = [powershell, "-NoLogo", "-NoProfile", "-Command", powershell_command]
    else:
        command = ["sh", "-lc", command_text]

    context.log(f"Terminal shell: {shell}")
    result = _run_process(context, command, cwd=cwd, check=False)
    if result.exit_code != 0:
        raise RuntimeError(f"Terminal command failed with exit code {result.exit_code}.")
    return {"exit_code": result.exit_code, "command": command_text, "cwd": str(cwd)}


def _app_installer_package_locations() -> list[str]:
    """Ask Windows where the App Installer package payload lives.

    `Program Files\\WindowsApps` cannot be listed by a standard user, so the
    glob above finds nothing even though the folder is reachable by full path.
    """
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-AppxPackage -Name Microsoft.DesktopAppInstaller | "
        "ForEach-Object { $_.InstallLocation }"
    )
    powershell = _resolve_powershell()
    command = [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
    if Path(powershell).name.lower() == "powershell.exe":
        command = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    try:
        process = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45.0,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    locations: list[str] = []
    for line in decode_process_output(process.stdout or b"").splitlines():
        location = line.strip()
        if location and location not in locations:
            locations.append(location)
    return locations


def _winget_candidate_paths(*, deep: bool = False) -> list[str]:
    """Every place winget can live, best first.

    `winget.exe` in `WindowsApps` is an execution alias, not a real file: while
    App Installer is being re-registered it can fail with `WinError 1920`
    (`Доступ к этому файлу из системы отсутствует`). The real package payload
    under `Program Files\\WindowsApps` keeps working in that window.
    """
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        text = str(value)
        if text not in candidates:
            candidates.append(text)

    add(shutil.which("winget"))
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        add(str(Path(local_app_data) / "Microsoft" / "WindowsApps" / "winget.exe"))

    for base in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), "C:\\Program Files"):
        if not base:
            continue
        package_root = Path(base) / "WindowsApps"
        try:
            packages = sorted(
                package_root.glob("Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe"),
                key=lambda item: item.name,
                reverse=True,
            )
        except OSError:
            packages = []
        for package in packages:
            add(str(package / "winget.exe"))

    if deep:
        for location in _app_installer_package_locations():
            add(str(Path(location) / "winget.exe"))

    return candidates


def _winget_probe(executable: str) -> bool:
    try:
        process = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=WINGET_PROBE_TIMEOUT_SECONDS,
            env=utf8_subprocess_env({"AUDION_GUI_TERMINAL": "1"}),
            **hidden_subprocess_kwargs(),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return process.returncode == 0


def _resolve_winget_executable(
    context: JobContext | None = None,
    *,
    refresh: bool = False,
    wait_seconds: float = 0.0,
) -> str:
    global _WINGET_EXECUTABLE
    with _WINGET_EXECUTABLE_LOCK:
        if not refresh and _WINGET_EXECUTABLE:
            return _WINGET_EXECUTABLE
        if refresh:
            _WINGET_EXECUTABLE = None

        deadline = time.monotonic() + max(0.0, wait_seconds)
        announced = False
        attempt = 0
        while True:
            attempt += 1
            for candidate in _winget_candidate_paths(deep=refresh or attempt > 1):
                if not _winget_probe(candidate):
                    continue
                _WINGET_EXECUTABLE = candidate
                if context is not None and (refresh or announced):
                    context.log(f"[OK] WinGet responds again: {candidate}")
                return candidate

            if time.monotonic() >= deadline:
                break
            if context is not None:
                if context.cancelled():
                    raise _cancelled_error()
                if not announced:
                    context.log(
                        "[WAIT] WinGet is not responding yet; waiting for App Installer to finish re-registering..."
                    )
                    announced = True
            time.sleep(max(0.5, min(WINGET_RECOVERY_INTERVAL_SECONDS, deadline - time.monotonic())))

        raise RuntimeError(
            "winget is not available. Microsoft App Installer is required; "
            "restart the app or the session if it was just updated."
        )


def _is_app_installer_package(package_id: str) -> bool:
    return package_id.strip().lower() in APP_INSTALLER_PACKAGE_IDS


def _settle_after_app_installer_change(context: JobContext, package_id: str, exit_code: int) -> None:
    """Wait for winget to come back after App Installer replaced itself."""
    if exit_code != 0 or not _is_app_installer_package(package_id):
        return
    context.log("[INFO] App Installer ships winget itself; waiting for the winget alias to come back...")
    try:
        _resolve_winget_executable(context, refresh=True, wait_seconds=WINGET_RECOVERY_SECONDS)
    except OperationCancelledError:
        raise
    except RuntimeError as exc:
        context.log(f"{ANSI_YELLOW}[WARN] {exc}{ANSI_RESET}")


def _run_winget(context: JobContext, args: list[str], *, check: bool = True) -> ProcessResult:
    executable = _resolve_winget_executable(context)
    activity = _winget_activity_label(context, args)
    try:
        return _run_process(context, [executable, *args], check=check, console=True, activity=activity)
    except OSError as exc:
        detail = getattr(exc, "winerror", None) or exc
        context.log(f"{ANSI_YELLOW}[WARN] WinGet could not be started ({detail}); re-resolving winget...{ANSI_RESET}")
        executable = _resolve_winget_executable(context, refresh=True, wait_seconds=WINGET_RECOVERY_SECONDS)
        return _run_process(context, [executable, *args], check=check, console=True, activity=activity)


def _run_winget_capture(args: list[str], *, cwd: Path | None = None, timeout: float = 20.0) -> ProcessResult:
    try:
        executable = _resolve_winget_executable()
    except RuntimeError as exc:
        return ProcessResult(exit_code=1, lines=(str(exc),))

    try:
        process = subprocess.run(
            [executable, *args],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=utf8_subprocess_env({"AUDION_GUI_TERMINAL": "1"}),
            **hidden_subprocess_kwargs(),
        )
    except OSError as exc:
        # The execution alias can break while App Installer re-registers itself.
        try:
            executable = _resolve_winget_executable(refresh=True)
        except RuntimeError:
            return ProcessResult(exit_code=1, lines=(f"winget could not be started: {exc}",))
        process = subprocess.run(
            [executable, *args],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=utf8_subprocess_env({"AUDION_GUI_TERMINAL": "1"}),
            **hidden_subprocess_kwargs(),
        )
    output = decode_process_output(process.stdout or b"")
    return ProcessResult(
        exit_code=int(process.returncode),
        lines=tuple(line for line in output.splitlines() if line.strip()),
    )


def _looks_like_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_LABEL_MARKERS)


def _looks_truncated(text: str) -> bool:
    return "…" in text


def _clean_package_name(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    if not cleaned or _looks_like_mojibake(cleaned):
        return ""
    return cleaned


def _label_for_installed_package(package_id: str, name: str = "") -> str:
    cleaned_name = _clean_package_name(name)
    if not cleaned_name or _looks_like_mojibake(cleaned_name):
        return package_id
    return f"{cleaned_name} | {package_id}"


def _label_for_available_update(option: dict[str, str]) -> str:
    package_id = str(option.get("value", "") or "").strip()
    name = str(option.get("name", "") or "").strip()
    current = str(option.get("version", "") or "").strip()
    available = str(option.get("available", "") or "").strip()
    prefix = f"{name} | {package_id}" if name else str(option.get("label") or package_id)
    if current and available:
        return f"{prefix} | {current} -> {available}"
    if available:
        return f"{prefix} | -> {available}"
    return prefix


def _available_update_preview_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for option in options:
        if not str(option.get("value", "") or "").strip():
            result.append(option)
            continue
        label = _label_for_available_update(option)
        item = dict(option)
        item["label"] = label
        item["label_ru"] = label
        result.append(item)
    return result


def _is_winget_table_rule(line: str) -> bool:
    """The dashed rule winget draws under the table header."""
    stripped = line.strip()
    return len(stripped) >= 3 and not stripped.strip("-")


def _localized_winget_table_columns(lines: list[str]) -> tuple[int, int, int, int] | None:
    """Read the column layout from any locale's header.

    WinGet translates `Name / Id / Version / Available / Source`, so matching
    the English words silently produced an empty table on a localized Windows:
    every row was dropped and the GUI reported `no updates available`. The
    header is instead recognized by the dashed rule printed under it, and the
    columns are taken from the header word offsets.
    """
    for index, line in enumerate(lines[:-1]):
        if not _is_winget_table_rule(lines[index + 1]):
            continue
        starts = [match.start() for match in re.finditer(r"\S+", line)]
        if len(starts) < 3:
            continue
        id_start, version_start = starts[1], starts[2]
        if len(starts) >= 5:
            available_start, source_start = starts[3], starts[4]
        elif len(starts) == 4:
            available_start, source_start = -1, starts[3]
        else:
            available_start, source_start = -1, -1
        return id_start, version_start, available_start, source_start
    return None


def _parse_winget_table_columns(lines: list[str]) -> tuple[int, int, int, int] | None:
    for line in lines:
        if "Name" not in line or "Id" not in line or "Version" not in line:
            continue
        name_start = line.find("Name")
        id_start = line.find("Id", name_start + 1)
        version_start = line.find("Version", id_start + 1)
        available_start = line.find("Available", version_start + 1)
        source_start_seed = available_start if available_start > version_start else version_start
        source_start = line.find("Source", source_start_seed + 1)
        if name_start >= 0 and id_start > name_start and version_start > id_start:
            return id_start, version_start, available_start, source_start
    return _localized_winget_table_columns(lines)


def _normalize_strict_winget_package_id(package_id: str, version: str) -> tuple[str, str]:
    raw_id = package_id.strip()
    raw_version = version.strip()
    matches = PACKAGE_ID_PATTERN.findall(raw_id)
    if not matches:
        return raw_id, raw_version

    candidate = matches[-1]
    version_head, separator, version_tail = raw_version.partition(" ")
    if (
        separator
        and raw_id != candidate
        and len(version_head) == 1
        and PACKAGE_ID_PATTERN.fullmatch(candidate + version_head)
    ):
        return candidate + version_head, version_tail.strip()
    return candidate, raw_version


def _parse_winget_package_lines(
    lines: Iterable[str],
    *,
    strict_package_ids: bool = False,
) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    seen: set[str] = set()
    line_list = list(lines)
    table_columns = _parse_winget_table_columns(line_list)
    # Whatever the header says in the local language, it is the line above the
    # dashed rule; skipping it by position keeps every locale out of the data.
    header_indexes = {
        index for index in range(len(line_list) - 1) if _is_winget_table_rule(line_list[index + 1])
    }

    for index, line in enumerate(line_list):
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        if index in header_indexes:
            continue
        if stripped.lower().startswith(("name ", "��� ", "имя ")):
            continue

        name = ""
        package_id = ""
        version = ""
        available = ""
        source = ""
        if table_columns:
            id_start, version_start, available_start, source_start = table_columns
            if len(line) > id_start:
                name = line[:id_start].strip()
                package_id = line[id_start:version_start].strip()
                if available_start > version_start:
                    version = line[version_start:available_start].strip()
                    available_end = source_start if source_start > available_start else len(line)
                    available = line[available_start:available_end].strip()
                else:
                    version_end = source_start if source_start > version_start else len(line)
                    version = line[version_start:version_end].strip()
                if source_start > version_start and len(line) > source_start:
                    source = line[source_start:].strip()
                if strict_package_ids:
                    package_id, version = _normalize_strict_winget_package_id(package_id, version)
        if not package_id:
            match = PACKAGE_ID_PATTERN.search(stripped)
            if not match:
                continue
            package_id = match.group(0)
            name = stripped[: match.start()].strip()
        if not package_id or _looks_truncated(package_id) or _looks_like_mojibake(package_id):
            continue
        if strict_package_ids and not PACKAGE_ID_PATTERN.fullmatch(package_id):
            continue
        if strict_package_ids and (not version or not available):
            continue
        if package_id.lower() in seen:
            continue
        seen.add(package_id.lower())

        label = _label_for_installed_package(package_id, name)
        package = {"value": package_id, "label": label, "label_ru": label}
        cleaned_name = _clean_package_name(name)
        if cleaned_name:
            package["name"] = cleaned_name
        if version:
            package["version"] = version
        if available:
            package["available"] = available
        if source:
            package["source"] = source
        packages.append(package)
    return packages


def _manifest_package_fields(root: Path | None = None) -> dict[str, list[dict[str, str]]]:
    project_root = Path(root) if root else Path(__file__).resolve().parents[2]
    manifest_path = project_root / "config" / "tool_manifest.yaml"
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    fields = raw.get("x-package-fields", [])
    result: dict[str, list[dict[str, str]]] = {}
    if not isinstance(fields, list):
        return result
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "").strip()
        if field_id not in PACKAGE_FIELD_ORDER:
            continue
        options = field.get("options", [])
        if not isinstance(options, list):
            continue
        normalized: list[dict[str, str]] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            value = str(option.get("value", option.get("id", "")) or "").strip()
            if not value:
                continue
            label = str(option.get("label") or value)
            label_ru = str(option.get("label_ru") or label)
            normalized.append({"value": value, "label": label, "label_ru": label_ru})
        result[field_id] = normalized

    for field_id, config_options in _config_package_options_by_field(project_root).items():
        existing = {option["value"].lower() for option in result.get(field_id, [])}
        merged = result.setdefault(field_id, [])
        for option in config_options:
            lower = option["value"].lower()
            if lower in existing:
                continue
            existing.add(lower)
            merged.append(option)
    return result


def _known_manifest_ids(root: Path | None = None) -> set[str]:
    fields = _manifest_package_fields(root)
    return {option["value"].lower() for options in fields.values() for option in options}


def _installed_uninstall_alias_patterns(root: Path | None = None) -> dict[str, list[str]]:
    project_root = Path(root) if root else Path(__file__).resolve().parents[2]
    alias_path = project_root / "config" / INSTALLED_UNINSTALL_ALIASES_NAME
    if not alias_path.exists():
        return {}

    raw = yaml.safe_load(alias_path.read_text(encoding="utf-8")) or {}
    groups = raw.get("groups", raw) if isinstance(raw, dict) else {}
    if not isinstance(groups, dict):
        return {}

    result: dict[str, list[str]] = {}
    for field_id, patterns in groups.items():
        field_key = str(field_id or "").strip()
        if field_key not in PACKAGE_FIELD_ORDER:
            continue
        pattern_items = patterns if isinstance(patterns, list) else [patterns]
        normalized = [str(pattern or "").strip() for pattern in pattern_items]
        normalized = [pattern for pattern in normalized if pattern]
        if normalized:
            result[field_key] = normalized
    return result


def _id_matches_patterns(package_id: str, patterns: Iterable[str]) -> bool:
    lower = package_id.lower()
    return any(fnmatchcase(lower, str(pattern).lower()) for pattern in patterns)


def _is_protected_uninstall_id(package_id: str) -> bool:
    return _id_matches_patterns(package_id, PROTECTED_UNINSTALL_PATTERNS)


def _protected_uninstall_ids(package_ids: Iterable[str]) -> list[str]:
    protected: list[str] = []
    seen: set[str] = set()
    for package_id in package_ids:
        lower = package_id.lower()
        if lower in seen or not _is_protected_uninstall_id(package_id):
            continue
        seen.add(lower)
        protected.append(package_id)
    return protected


def _protected_uninstall_confirmed(context: JobContext) -> bool:
    return any(_param_bool(context, key, False) for key in PROTECTED_UNINSTALL_CONFIRM_KEYS)


def _require_protected_uninstall_confirmation(context: JobContext, package_ids: list[str]) -> None:
    protected = _protected_uninstall_ids(package_ids)
    if not protected or _protected_uninstall_confirmed(context):
        return
    names = ", ".join(protected[:8])
    context.log("[WARN] Protected uninstall was blocked.")
    for package_id in protected:
        context.log(f"  - {package_id}")
    raise RuntimeError(
        "Protected uninstall requires explicit confirmation for system/runtime package IDs: "
        f"{names}"
    )


def _mark_protected_uninstall_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    marked: list[dict[str, str]] = []
    for option in options:
        value = str(option.get("value", "") or "").strip()
        if not value or not _is_protected_uninstall_id(value):
            marked.append(option)
            continue
        label = str(option.get("label") or value)
        label_ru = str(option.get("label_ru") or label)
        item = dict(option)
        if not label.startswith("Protected: "):
            item["label"] = f"Protected: {label}"
        if not label_ru.startswith("Осторожно: "):
            item["label_ru"] = f"Осторожно: {label_ru}"
        marked.append(item)
    return marked


def _package_id_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower in seen:
            continue
        seen.add(lower)
        ids.append(line)
    return ids


def _package_id_lines_from_section(path: Path, section_name: str) -> list[str]:
    if not path.exists():
        return []
    section_names = {item.lower() for item in MEDIA_SECTION_NAMES}
    target = section_name.strip().lower()
    in_target_section = False
    ids: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if heading in section_names:
                in_target_section = heading == target
            continue
        if not in_target_section or not line:
            continue
        lower = line.lower()
        if lower in seen:
            continue
        seen.add(lower)
        ids.append(line)
    return ids


def _config_package_options_by_field(root: Path) -> dict[str, list[dict[str, str]]]:
    config_dir = root / "config"
    result: dict[str, list[dict[str, str]]] = {}
    seen_by_field: dict[str, set[str]] = {}
    for _target_key, (file_name, field_id, section_name) in CONFIG_LIST_TARGETS.items():
        if not field_id:
            continue
        path = config_dir / file_name
        ids = _package_id_lines_from_section(path, section_name) if section_name else _package_id_lines(path)
        field_seen = seen_by_field.setdefault(field_id, set())
        field_options = result.setdefault(field_id, [])
        for package_id in ids:
            lower = package_id.lower()
            if lower in field_seen:
                continue
            field_seen.add(lower)
            field_options.append({"value": package_id, "label": package_id, "label_ru": package_id})
    return result


def _custom_package_ids(root: Path | None = None) -> list[str]:
    project_root = Path(root) if root else Path(__file__).resolve().parents[2]
    return _package_id_lines(project_root / "config" / CUSTOM_LIST_NAME)


def _pin_package_ids(root: Path | None = None) -> list[str]:
    project_root = Path(root) if root else Path(__file__).resolve().parents[2]
    return _package_id_lines(project_root / "config" / PINS_LIST_NAME)


def _append_pin_package_ids(config_dir: Path, package_ids: Iterable[str]) -> list[str]:
    pins_path = config_dir / PINS_LIST_NAME
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _package_id_lines(pins_path)
    seen = {item.lower() for item in existing}
    added: list[str] = []

    if not pins_path.exists():
        pins_path.write_text(
            "# Packages to pin with: winget pin add --blocking\n\n",
            encoding="utf-8",
            newline="\n",
        )

    lines = pins_path.read_text(encoding="utf-8").splitlines()
    for package_id in package_ids:
        normalized = str(package_id or "").strip()
        if not normalized:
            continue
        lower = normalized.lower()
        if lower in seen:
            continue
        lines.append(normalized)
        seen.add(lower)
        added.append(normalized)
    if added:
        _write_lines(pins_path, lines)
    return added


def _append_custom_package_id(config_dir: Path, package_id: str) -> bool:
    custom_path = config_dir / CUSTOM_LIST_NAME
    custom_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _package_id_lines(custom_path)
    if package_id.lower() in {item.lower() for item in existing}:
        return False
    if not custom_path.exists():
        custom_path.write_text(
            "# Custom WinGet package IDs installed manually or through Install by ID.\n"
            "# One WinGet package ID per line.\n\n",
            encoding="utf-8",
            newline="\n",
        )
    with custom_path.open("a", encoding="utf-8", newline="\n") as handle:
        if custom_path.stat().st_size > 0:
            handle.write("\n")
        handle.write(package_id)
    return True


def _config_list_occurrences(config_dir: Path, package_id: str) -> list[str]:
    lower = package_id.lower()
    occurrences: list[str] = []
    seen_files: set[str] = set()
    for target_key, (file_name, _field_id, _section_name) in CONFIG_LIST_TARGETS.items():
        if file_name.lower() in seen_files:
            continue
        seen_files.add(file_name.lower())
        path = config_dir / file_name
        if lower in {item.lower() for item in _package_id_lines(path)}:
            occurrences.append(target_key if not file_name == "media.txt" else "media")
    return occurrences


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def _append_package_id_to_plain_list(path: Path, package_id: str) -> None:
    if not path.exists():
        path.write_text(
            "# WinGet package IDs added from the GUI.\n"
            "# One WinGet package ID per line.\n\n",
            encoding="utf-8",
            newline="\n",
        )
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if path.stat().st_size > 0:
            handle.write("\n")
        handle.write(package_id)


def _append_package_id_to_media_section(path: Path, section_name: str, package_id: str) -> None:
    if not path.exists():
        path.write_text(
            "# Media tools: images, audio, and video.\n"
            "# One WinGet package ID per line. Blank lines split visual sections only.\n\n"
            "# Images\n\n"
            "# Audio\n\n"
            "# Video\n",
            encoding="utf-8",
            newline="\n",
        )

    lines = path.read_text(encoding="utf-8").splitlines()
    section_names = {item.lower() for item in MEDIA_SECTION_NAMES}
    target = section_name.strip().lower()
    start_index: int | None = None
    end_index = len(lines)
    for index, raw_line in enumerate(lines):
        heading = raw_line.strip().lstrip("#").strip().lower() if raw_line.strip().startswith("#") else ""
        if heading not in section_names:
            continue
        if heading == target:
            start_index = index + 1
            end_index = len(lines)
            continue
        if start_index is not None and index > start_index:
            end_index = index
            break

    if start_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"# {section_name}", package_id])
        _write_lines(path, lines)
        return

    insert_index = end_index
    while insert_index > start_index and not lines[insert_index - 1].strip():
        insert_index -= 1
    lines.insert(insert_index, package_id)
    _write_lines(path, lines)


def _append_package_id_to_config_target(config_dir: Path, target_key: str, package_id: str) -> tuple[bool, Path]:
    target = CONFIG_LIST_TARGETS.get(target_key)
    if not target:
        raise RuntimeError(f"Unknown package list target: {target_key}")
    file_name, _field_id, section_name = target
    path = config_dir / file_name
    occurrences = _config_list_occurrences(config_dir, package_id)
    if occurrences:
        return False, path
    if section_name:
        _append_package_id_to_media_section(path, section_name, package_id)
    else:
        _append_package_id_to_plain_list(path, package_id)
    return True, path


def _yaml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _manifest_package_id_locations(manifest_path: Path, package_id: str) -> list[str]:
    if not manifest_path.exists():
        return []
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    fields = raw.get("x-package-fields", []) if isinstance(raw, dict) else []
    if not isinstance(fields, list):
        return []
    lower = package_id.lower()
    locations: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "").strip()
        options = field.get("options", [])
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            value = str(option.get("value", option.get("id", "")) or "").strip()
            if value.lower() == lower:
                locations.append(field_id)
                break
    return locations


def _append_package_id_to_manifest_field(
    manifest_path: Path,
    field_id: str,
    package_id: str,
    label: str,
) -> bool:
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest was not found: {manifest_path}")
    if _manifest_package_id_locations(manifest_path, package_id):
        return False

    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    field_start: int | None = None
    field_marker = f"- id: {field_id}"
    for index, line in enumerate(lines):
        if line.strip() == field_marker:
            field_start = index
            break
    if field_start is None:
        raise RuntimeError(f"Manifest field was not found: {field_id}")

    options_index: int | None = None
    for index in range(field_start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  - id: ") or line.startswith("x-pin-fields:") or line.startswith("operations:"):
            break
        if line.strip() == "options:":
            options_index = index
            break
    if options_index is None:
        raise RuntimeError(f"Manifest field has no options block: {field_id}")

    insert_index = len(lines)
    for index in range(options_index + 1, len(lines)):
        line = lines[index]
        if line.startswith("  - id: ") or line.startswith("x-pin-fields:") or line.startswith("operations:"):
            insert_index = index
            break
    while insert_index > options_index + 1 and not lines[insert_index - 1].strip():
        insert_index -= 1

    option_label = label.strip() or package_id
    option_lines = [
        f"      - value: {_yaml_string(package_id)}",
        f"        label: {_yaml_string(option_label)}",
        f"        label_ru: {_yaml_string(option_label)}",
    ]
    lines[insert_index:insert_index] = option_lines
    _write_lines(manifest_path, lines)
    return True


def _installed_cache_key(root: Path | None = None) -> str:
    return str(Path(root).resolve()) if root else str(Path(__file__).resolve().parents[2])


def _clear_installed_options_cache() -> None:
    with _INSTALLED_OPTIONS_CACHE_LOCK:
        _INSTALLED_OPTIONS_CACHE.clear()


def _clear_available_update_options_cache() -> None:
    with _AVAILABLE_UPDATE_OPTIONS_CACHE_LOCK:
        _AVAILABLE_UPDATE_OPTIONS_CACHE.clear()


def clear_installed_options_cache() -> None:
    _clear_installed_options_cache()


def _installed_package_map(root: Path | None = None) -> dict[str, dict[str, str]]:
    return {option["value"].lower(): option for option in installed_package_options(root)}


def _installed_options_for_ids(root: Path | None, package_ids: Iterable[str]) -> list[dict[str, str]]:
    installed = _installed_package_map(root)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for package_id in package_ids:
        lower = package_id.lower()
        if lower in seen or lower not in installed:
            continue
        seen.add(lower)
        result.append(installed[lower])
    return result


def _missing_install_group_options(root: Path | None, package_field_id: str) -> list[dict[str, str]]:
    installed = _installed_package_map(root)
    manifest_options = _manifest_package_fields(root).get(package_field_id, [])
    result: list[dict[str, str]] = []
    for option in manifest_options:
        value = option["value"]
        if _is_windows_feature_id(value):
            # Windows features are absent from `winget list`; ask the feature itself.
            if _windows_feature_enabled(value):
                continue
            result.append(option)
            continue
        if value.lower() in installed:
            continue
        result.append(option)
    return result


def missing_install_system_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_system")


def missing_install_dev_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_dev")


def missing_install_ai_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_ai")


def missing_install_pkms_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_pkms")


def missing_install_office_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_office")


def missing_install_media_images_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_media_images")


def missing_install_media_audio_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_media_audio")


def missing_install_media_video_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_media_video")


def missing_install_network_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_network")


def missing_install_hardware_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_hardware")


def missing_install_msvc_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_msvc")


def missing_install_msvc_legacy_options(root: Path | None = None) -> list[dict[str, str]]:
    return _missing_install_group_options(root, "packages_msvc_legacy")


def _installed_options_for_patterns(root: Path | None, patterns: Iterable[str]) -> list[dict[str, str]]:
    pattern_list = list(patterns)
    if not pattern_list:
        return []

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in installed_package_options(root):
        value = option["value"]
        lower = value.lower()
        if lower in seen or not _id_matches_patterns(value, pattern_list):
            continue
        seen.add(lower)
        result.append(option)
    return result


def _installed_uninstall_group_options(root: Path | None, package_field_id: str) -> list[dict[str, str]]:
    manifest_options = _manifest_package_fields(root).get(package_field_id, [])
    result = _installed_options_for_ids(root, [option["value"] for option in manifest_options])
    seen = {option["value"].lower() for option in result}
    alias_patterns = _installed_uninstall_alias_patterns(root).get(package_field_id, [])
    for option in _installed_options_for_patterns(root, alias_patterns):
        lower = option["value"].lower()
        if lower in seen:
            continue
        seen.add(lower)
        result.append(option)
    return _mark_protected_uninstall_options(result)


def installed_uninstall_system_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_system")


def installed_uninstall_dev_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_dev")


def installed_uninstall_ai_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_ai")


def installed_uninstall_pkms_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_pkms")


def installed_uninstall_office_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_office")


def installed_uninstall_media_images_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_media_images")


def installed_uninstall_media_audio_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_media_audio")


def installed_uninstall_media_video_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_media_video")


def installed_uninstall_network_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_network")


def installed_uninstall_hardware_options(root: Path | None = None) -> list[dict[str, str]]:
    return _installed_uninstall_group_options(root, "packages_hardware")


def installed_uninstall_msvc_options(root: Path | None = None) -> list[dict[str, str]]:
    """Both MSVC groups in one uninstall block.

    Install splits 2015+ from the legacy family because the recommendation
    differs; removal does not - the operator looks for "the VC++ runtimes" in
    one place.
    """
    options = _installed_uninstall_group_options(root, "packages_msvc")
    seen = {option["value"].lower() for option in options}
    for option in _installed_uninstall_group_options(root, "packages_msvc_legacy"):
        if option["value"].lower() in seen:
            continue
        seen.add(option["value"].lower())
        options.append(option)
    return options


def installed_uninstall_custom_options(root: Path | None = None) -> list[dict[str, str]]:
    return _mark_protected_uninstall_options(_installed_options_for_ids(root, _custom_package_ids(root)))


def installed_uninstall_other_options(root: Path | None = None) -> list[dict[str, str]]:
    installed = installed_package_options(root)
    excluded = _known_manifest_ids(root)
    excluded.update(package_id.lower() for package_id in _custom_package_ids(root))
    alias_patterns = [
        pattern
        for patterns in _installed_uninstall_alias_patterns(root).values()
        for pattern in patterns
    ]
    return _mark_protected_uninstall_options([
        option
        for option in installed
        if option["value"].lower() not in excluded
        and not _id_matches_patterns(option["value"], alias_patterns)
    ])


def pins_config_options(root: Path | None = None) -> list[dict[str, str]]:
    pinned_ids = _pin_package_ids(root)
    if not pinned_ids:
        return [
            {
                "value": "",
                "label": "pins.txt has no package IDs yet",
                "label_ru": "В pins.txt пока нет ID пакетов",
            }
        ]

    installed = _installed_package_map(root)
    options: list[dict[str, str]] = []
    for package_id in pinned_ids:
        installed_option = installed.get(package_id.lower())
        if installed_option:
            item = dict(installed_option)
            item["value"] = package_id
            options.append(item)
            continue
        label = f"{package_id} | not installed"
        options.append({"value": package_id, "label": label, "label_ru": f"{package_id} | не установлен"})
    return options


def installed_pin_candidate_options(root: Path | None = None) -> list[dict[str, str]]:
    pinned = {package_id.lower() for package_id in _pin_package_ids(root)}
    options = [
        option
        for option in installed_package_options(root)
        if str(option.get("value", "") or "").strip()
        and option["value"].lower() not in pinned
    ]
    if not options:
        return [
            {
                "value": "",
                "label": "No additional installed WinGet packages were found",
                "label_ru": "Дополнительные установленные WinGet-пакеты не найдены",
            }
        ]
    return options


def installed_package_options(root: Path | None = None) -> list[dict[str, str]]:
    cache_key = _installed_cache_key(root)
    with _INSTALLED_OPTIONS_CACHE_LOCK:
        now = time.monotonic()
        cached = _INSTALLED_OPTIONS_CACHE.get(cache_key)
        if cached and INSTALLED_OPTIONS_CACHE_SECONDS > 0 and now - cached[0] < INSTALLED_OPTIONS_CACHE_SECONDS:
            return cached[1]

        result = _run_winget_capture(
            [
                "list",
                "--source",
                "winget",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            cwd=root,
            timeout=25.0,
        )
        if result.exit_code != 0:
            options = [
                {
                    "value": "",
                    "label": "WinGet is not available or returned no installed package list",
                    "label_ru": "WinGet недоступен или не вернул список установленных пакетов",
                }
            ]
            _INSTALLED_OPTIONS_CACHE[cache_key] = (now, options)
            return options
        packages = _parse_winget_package_lines(result.lines)
        if not packages:
            options = [
                {
                    "value": "",
                    "label": "No installed WinGet packages were found",
                    "label_ru": "Установленные WinGet-пакеты не найдены",
                }
            ]
        else:
            options = packages
        _INSTALLED_OPTIONS_CACHE[cache_key] = (now, options)
        return options


def available_update_options(root: Path | None = None) -> list[dict[str, str]]:
    cache_key = _installed_cache_key(root)
    with _AVAILABLE_UPDATE_OPTIONS_CACHE_LOCK:
        now = time.monotonic()
        cached = _AVAILABLE_UPDATE_OPTIONS_CACHE.get(cache_key)
        if cached and AVAILABLE_UPDATE_OPTIONS_CACHE_SECONDS > 0 and now - cached[0] < AVAILABLE_UPDATE_OPTIONS_CACHE_SECONDS:
            return cached[1]

        options = _available_update_options_uncached(root)
        _AVAILABLE_UPDATE_OPTIONS_CACHE[cache_key] = (now, options)
        return options


def _available_update_options_uncached(root: Path | None = None) -> list[dict[str, str]]:
    # No `--source winget` filter and `--include-unknown`: browsers and other
    # ARP/MSIX installs report an unknown installed version, and the filtered
    # scan silently hid every one of them.
    result = _run_winget_capture(
        [
            "upgrade",
            "--include-unknown",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        cwd=root,
        timeout=UPGRADE_SCAN_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        return [
            {
                "value": "",
                "label": "WinGet is not available or returned no update list",
                "label_ru": "WinGet недоступен или не вернул список обновлений",
            }
        ]
    packages = _parse_winget_package_lines(result.lines, strict_package_ids=True)
    if not packages:
        return [
            {
                "value": "",
                "label": "No updates available",
                "label_ru": "Обновления отсутствуют",
            }
        ]
    return _available_update_preview_options(packages)


def _available_update_map(root: Path | None = None) -> dict[str, dict[str, str]]:
    return {option["value"].lower(): option for option in available_update_options(root) if option.get("value")}


def _available_update_options_for_ids(root: Path | None, package_ids: Iterable[str]) -> list[dict[str, str]]:
    available = _available_update_map(root)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for package_id in package_ids:
        lower = package_id.lower()
        if lower in seen or lower not in available:
            continue
        seen.add(lower)
        result.append(available[lower])
    return result


def _available_update_options_for_patterns(root: Path | None, patterns: Iterable[str]) -> list[dict[str, str]]:
    pattern_list = list(patterns)
    if not pattern_list:
        return []

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in available_update_options(root):
        value = option.get("value", "")
        if not value:
            continue
        lower = value.lower()
        if lower in seen or not _id_matches_patterns(value, pattern_list):
            continue
        seen.add(lower)
        result.append(option)
    return result


def _available_update_group_options(root: Path | None, package_field_id: str) -> list[dict[str, str]]:
    manifest_options = _manifest_package_fields(root).get(package_field_id, [])
    result = _available_update_options_for_ids(root, [option["value"] for option in manifest_options])
    seen = {option["value"].lower() for option in result}
    alias_patterns = _installed_uninstall_alias_patterns(root).get(package_field_id, [])
    for option in _available_update_options_for_patterns(root, alias_patterns):
        lower = option["value"].lower()
        if lower in seen:
            continue
        seen.add(lower)
        result.append(option)
    return result


def available_update_system_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_system")


def available_update_dev_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_dev")


def available_update_ai_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_ai")


def available_update_pkms_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_pkms")


def available_update_office_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_office")


def available_update_media_images_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_media_images")


def available_update_media_audio_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_media_audio")


def available_update_media_video_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_media_video")


def available_update_network_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_network")


def available_update_hardware_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_hardware")


def available_update_msvc_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_msvc")


def available_update_msvc_legacy_options(root: Path | None = None) -> list[dict[str, str]]:
    return _available_update_group_options(root, "packages_msvc_legacy")


def _winget_list_contains(
    context: JobContext,
    package_id: str,
    extra_args: list[str] | None = None,
    *,
    source_args: list[str] | None = None,
) -> bool:
    result = _run_winget(
        context,
        [
            "list",
            "--id",
            package_id,
            "-e",
            *(["--source", "winget"] if source_args is None else source_args),
            "--accept-source-agreements",
            "--disable-interactivity",
            *(extra_args or []),
        ],
        check=False,
    )
    if result.exit_code != 0:
        return False
    return any(package_id.lower() in line.lower() for line in result.lines)


@dataclass(frozen=True)
class PackagePresence:
    installed: bool
    source_bound: bool


def _package_presence(context: JobContext, package_id: str) -> PackagePresence:
    """Tell an installed package apart from one winget never installed.

    `winget list --source winget` only reports packages correlated with the
    winget source. Anything installed from an MSI/EXE or the Store shows up
    only in the unfiltered list, and must not be reported as `not installed`.
    """
    if _winget_list_contains(context, package_id):
        return PackagePresence(True, True)
    if _winget_list_contains(context, package_id, source_args=[]):
        return PackagePresence(True, False)
    return PackagePresence(False, False)


def _winget_list_contains_quiet(package_id: str, extra_args: list[str] | None = None) -> bool:
    result = _run_winget_capture(
        [
            "list",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
            *(extra_args or []),
        ],
        timeout=30.0,
    )
    if result.exit_code != 0:
        return False
    return any(package_id.lower() in line.lower() for line in result.lines)


def _warn_already_installed_after_install(context: JobContext, packages: list[str]) -> None:
    if not packages:
        return
    context.log("")
    context.log(f"{ANSI_YELLOW}[WARN] WinGet сообщил, что часть выбранных пакетов уже установлена.{ANSI_RESET}")
    context.log(
        f"{ANSI_YELLOW}[WARN] Первичный скан не распознал их достаточно точно, поэтому это не считается ошибкой установки.{ANSI_RESET}"
    )
    context.log(f"{ANSI_YELLOW}[WARN] Проверьте обновления для этих пакетов:{ANSI_RESET}")
    for package_id in packages:
        context.log(f"{ANSI_YELLOW}[WARN]   - {package_id}{ANSI_RESET}")


# Options the WinGet manifest never asks for, though the MSI supports them.
# WinGet runs the installer with defaults, so a package can land half-configured:
# PowerShell, for instance, arrives without its context menus and - worse -
# without registration in Microsoft Update, so it never updates itself.
#
# The property names are read out of the package itself (tables Component and
# InstallExecuteSequence), not guessed.
POST_INSTALL_MSI_PROPERTIES: dict[str, tuple[str, ...]] = {
    "microsoft.powershell": (
        "ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1",
        "ADD_FILE_CONTEXT_MENU_RUNPOWERSHELL=1",
        "ADD_PATH=1",
        "REGISTER_MANIFEST=1",
        "USE_MU=1",
        "ENABLE_MU=1",
    ),
}

# Where the installer records the properties it was called with. Reading them
# back keeps a deliberate choice - telemetry off, remoting on - from being
# silently reset by our own pass.
POST_INSTALL_KEEP: dict[str, tuple[tuple[str, str], ...]] = {
    "microsoft.powershell": (
        ("DisableTelemetry", "DISABLE_TELEMETRY=1"),
        ("EnablePSRemoting", "ENABLE_PSREMOTING=1"),
    ),
}


def _powershell_product_code() -> str:
    """The MSI product code of the installed PowerShell 7, or `''`."""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    path = r"SOFTWARE\Microsoft\PowerShellCore\InstalledVersions"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    return ""
                index += 1
                with winreg.OpenKey(root, name) as item:
                    try:
                        code, _ = winreg.QueryValueEx(item, "ProductCode")
                    except FileNotFoundError:
                        continue
                    if code:
                        return str(code)
    except OSError:
        return ""
    return ""


def _installer_properties_saved(package_id: str) -> dict[str, str]:
    if package_id.strip().lower() != "microsoft.powershell" or os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    saved: dict[str, str] = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\PowerShellCore\InstallerProperties"
        ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                saved[str(name)] = str(value)
    except OSError:
        return {}
    return saved


def _apply_post_install_options(context: JobContext, package_id: str) -> None:
    """Turn on what WinGet left off, without reinstalling anything.

    The package is already on disk and cached by Windows Installer, so the
    options are applied by repairing the installed product: same files, new
    properties. Failure here never fails the package - it was installed.
    """
    properties = POST_INSTALL_MSI_PROPERTIES.get(package_id.strip().lower())
    if not properties:
        return

    product_code = _powershell_product_code()
    if not product_code:
        context.log(f"{ANSI_YELLOW}[WARN] {package_id}: product code not found, options left as WinGet set them.{ANSI_RESET}")
        return

    arguments = list(properties)
    saved = _installer_properties_saved(package_id)
    for registry_name, property_text in POST_INSTALL_KEEP.get(package_id.strip().lower(), ()):
        if saved.get(registry_name) == "1":
            arguments.append(property_text)

    context.log(f"[INFO] Turning on what WinGet leaves off: {' '.join(arguments)}")
    command = [
        "msiexec.exe",
        "/i",
        product_code,
        "/quiet",
        "/norestart",
        "REINSTALL=ALL",
        "REINSTALLMODE=amus",
        *arguments,
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed command, no shell
            command,
            capture_output=True,
            **hidden_subprocess_kwargs(),
        )
    except OSError as error:
        context.log(f"{ANSI_YELLOW}[WARN] Could not run msiexec: {error}{ANSI_RESET}")
        return

    if completed.returncode in (0, 3010):
        context.log("[OK] Context menus, event log manifest and Microsoft Update are on.")
        return
    if completed.returncode == 1223:
        context.log(f"{ANSI_YELLOW}[WARN] Elevation refused - options left as WinGet set them.{ANSI_RESET}")
        return
    context.log(f"{ANSI_YELLOW}[WARN] msiexec exit code {completed.returncode}; options may be incomplete.{ANSI_RESET}")


def _install_package(context: JobContext, package_id: str) -> int:
    return _run_winget(
        context,
        [
            "install",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--no-upgrade",
        ],
        check=False,
    ).exit_code


def _update_package(context: JobContext, package_id: str) -> int:
    return _update_package_action_result(context, package_id).exit_code


def _pin_package(context: JobContext, package_id: str) -> int:
    pin_state = _run_winget(
        context,
        [
            "pin",
            "list",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
        ],
        check=False,
    )
    if pin_state.exit_code == 0 and any(package_id.lower() in line.lower() for line in pin_state.lines):
        context.log(f"[SKIP] Pin already exists: {package_id}")
        return 0
    return _run_winget(
        context,
        [
            "pin",
            "add",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--blocking",
            "--accept-source-agreements",
        ],
        check=False,
    ).exit_code


def _check_package(context: JobContext, package_id: str) -> int:
    return _run_winget(
        context,
        [
            "list",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        check=False,
    ).exit_code


def _uninstall_package(context: JobContext, package_id: str) -> int:
    return _run_winget(
        context,
        [
            "uninstall",
            "--id",
            package_id,
            "-e",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        check=False,
    ).exit_code


def _package_is_listed_quiet(context: JobContext, package_id: str) -> bool:
    try:
        result = _run_winget_capture(
            [
                "list",
                "--id",
                package_id,
                "-e",
                "--source",
                "winget",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            cwd=context.paths.root,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        context.log(f"[WARN] Timed out while checking uninstall result; assuming still listed: {package_id}")
        return True
    return result.exit_code == 0


def _verified_uninstall_exit_code(context: JobContext, package_id: str, exit_code: int) -> tuple[int, bool]:
    context.log(f"[INFO] Verifying uninstall result after exit code {exit_code}: {package_id}")
    if not _package_is_listed_quiet(context, package_id):
        context.log(f"[OK] Package is no longer listed after uninstall: {package_id}")
        return 0, False

    wait_seconds = max(0.0, UNINSTALL_USER_WAIT_SECONDS)
    interval_seconds = max(1.0, UNINSTALL_USER_WAIT_INTERVAL_SECONDS)
    waited = bool(wait_seconds)
    if wait_seconds:
        context.log(
            f"[WAIT] Package is still listed. If a UAC or uninstaller window is open, "
            f"finish or cancel it; waiting up to {int(wait_seconds)} seconds: {package_id}"
        )

    deadline = time.monotonic() + wait_seconds
    while wait_seconds and time.monotonic() < deadline:
        if context.cancelled():
            raise _cancelled_error()
        time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
        if not _package_is_listed_quiet(context, package_id):
            context.log(f"[OK] Package is no longer listed after uninstall: {package_id}")
            return 0, waited

    if exit_code == 0:
        context.log(
            f"[WARN] Package is still listed after winget reported success. "
            f"The external uninstaller may have been cancelled or may still require user action: {package_id}"
        )
        return 0, waited
    context.log(f"[WARN] Package is still listed after uninstall: {package_id}")
    return exit_code, waited


# Installer exit codes that mean success but do not look like it. `1638` is
# ERROR_PRODUCT_VERSION - an equal or newer build is already installed, which
# is how the VC++ redistributables answer all the time - and `3010` / `1641`
# are the reboot codes. Windows reports them raw or wrapped in an HRESULT
# (`0x8007<code>`), and Python hands back the signed form of that HRESULT.
BENIGN_INSTALLER_EXIT_CODES: dict[int, tuple[str, str]] = {
    1638: ("already_installed", "installer_reported_same_or_newer_version"),
    0x80070666: ("already_installed", "installer_reported_same_or_newer_version"),
    3010: ("reboot_required", "installer_requests_reboot"),
    0x80070BC2: ("reboot_required", "installer_requests_reboot"),
    1641: ("reboot_required", "installer_started_reboot"),
    0x80070669: ("reboot_required", "installer_started_reboot"),
}


def _unsigned_exit_code(exit_code: int) -> int:
    return exit_code + 0x100000000 if exit_code < 0 else exit_code


def _benign_installer_result(context: JobContext, package_id: str, exit_code: int) -> PackageActionResult | None:
    """Turn a known "it actually worked" installer code into a clean result."""
    known = BENIGN_INSTALLER_EXIT_CODES.get(_unsigned_exit_code(exit_code))
    if known is None:
        return None
    status, note = known
    if status == "reboot_required":
        context.log(f"{ANSI_YELLOW}[WARN] {package_id}: installed, Windows wants a restart (exit code {exit_code}).{ANSI_RESET}")
    else:
        context.log(f"[INFO] {package_id}: the same or a newer build is already installed (exit code {exit_code}).")
    return PackageActionResult(0, status, (note, f"installer_exit_code_{_unsigned_exit_code(exit_code)}"))


def _update_package_action_result(context: JobContext, package_id: str) -> PackageActionResult:
    presence = _package_presence(context, package_id)
    if not presence.installed:
        context.log(f"[SKIP] Not installed: {package_id}")
        return PackageActionResult(0, "skipped", ("not_installed",))

    source_args = None if presence.source_bound else []
    outside_notes: tuple[str, ...] = () if presence.source_bound else ("not_from_winget_source",)
    if not presence.source_bound:
        context.log(f"[INFO] Installed outside the winget source; updating the local entry: {package_id}")

    # `--include-unknown` keeps packages with an unreadable installed version
    # (most browsers) from being reported as having no update.
    if not _winget_list_contains(
        context,
        package_id,
        ["--upgrade-available", "--include-unknown"],
        source_args=source_args,
    ):
        context.log(f"[SKIP] No update available: {package_id}")
        return PackageActionResult(0, "skipped", ("no_update_available", *outside_notes))

    exit_code = _run_winget(
        context,
        [
            "upgrade",
            "--id",
            package_id,
            "-e",
            *(["--source", "winget"] if presence.source_bound else []),
            "--include-unknown",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ],
        check=False,
    ).exit_code
    if exit_code != 0:
        benign = _benign_installer_result(context, package_id, exit_code)
        if benign is not None:
            return PackageActionResult(benign.exit_code, benign.status, (*benign.notes, *outside_notes))
    return PackageActionResult(exit_code, "updated" if exit_code == 0 else "failed", outside_notes)


def _summary_counts(items: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _write_action_summary(context: JobContext, summary: dict[str, object]) -> dict[str, str]:
    context.report_dir.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    report_json = context.report_dir / "action_summary.json"
    report_md = context.report_dir / "action_summary.md"
    log_json = context.log_file.with_suffix(".summary.json")

    report_json.write_text(json_text + "\n", encoding="utf-8", newline="\n")
    log_json.write_text(json_text + "\n", encoding="utf-8", newline="\n")

    results = list(summary.get("results", [])) if isinstance(summary.get("results"), list) else []
    lines = [
        f"# Action Summary: {summary.get('action', '')}",
        "",
        f"- Selected: {summary.get('selected_count', 0)}",
        f"- Failed: {summary.get('failed_count', 0)}",
        f"- Already installed: {summary.get('already_installed_count', 0)}",
        f"- Waited: {summary.get('waited_count', 0)}",
        "",
        "| Status | Package | Exit code | Notes |",
        "| --- | --- | ---: | --- |",
    ]
    for item in results:
        if not isinstance(item, dict):
            continue
        notes = item.get("notes", [])
        if isinstance(notes, list):
            notes_text = ", ".join(str(note) for note in notes)
        else:
            notes_text = str(notes or "")
        lines.append(
            f"| {item.get('status', '')} | `{item.get('package', '')}` | {item.get('exit_code', '')} | {notes_text} |"
        )
    report_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    context.log(f"[SUMMARY] {report_json}")
    context.log(f"[SUMMARY] {log_json}")
    return {
        "report_json": str(report_json),
        "report_md": str(report_md),
        "log_json": str(log_json),
    }


def _write_report_pair(context: JobContext, stem: str, payload: dict[str, object], markdown_lines: list[str]) -> dict[str, str]:
    context.report_dir.mkdir(parents=True, exist_ok=True)
    json_path = context.report_dir / f"{stem}.json"
    md_path = context.report_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    context.log(f"[REPORT] {json_path}")
    context.log(f"[REPORT] {md_path}")
    return {"json": str(json_path), "markdown": str(md_path)}


def _run_package_batch(context: JobContext, action: str, packages: list[str]) -> dict[str, object]:
    if not packages:
        raise RuntimeError("Select at least one WinGet package.")

    if action not in {"install", "update", "pin", "uninstall", "check"}:
        raise RuntimeError(f"Unsupported package action: {action}")

    context.log(f"[INFO] Action: {action}")
    context.log(f"[INFO] Selected packages: {len(packages)}")
    if action == "uninstall":
        _require_protected_uninstall_confirmation(context, packages)

    failed: list[dict[str, object]] = []
    already_installed: list[str] = []
    results: list[dict[str, object]] = []
    total = len(packages)
    try:
        for index, package_id in enumerate(packages, start=1):
            if context.cancelled():
                raise _cancelled_error()

            context.log("")
            context.log("----------------------------------------------------------------")
            context.log(f"[{index}/{total}] {package_id}")
            context.activity(f"[{index}/{total}] {package_id}")
            context.progress((index - 1) / max(1, total))

            try:
                if _is_windows_feature_id(package_id):
                    action_result = _windows_feature_action_result(context, action, package_id)
                    exit_code = action_result.exit_code
                elif action == "install":
                    exit_code = _install_package(context, package_id)
                    benign = _benign_installer_result(context, package_id, exit_code) if exit_code != 0 else None
                    if benign is not None:
                        action_result = benign
                    elif exit_code != 0 and _winget_list_contains_quiet(package_id):
                        already_installed.append(package_id)
                        action_result = PackageActionResult(
                            0,
                            "already_installed",
                            ("not_detected_by_initial_scan", f"winget_install_exit_code_{exit_code}"),
                        )
                    else:
                        action_result = PackageActionResult(exit_code, "installed" if exit_code == 0 else "failed")
                    exit_code = action_result.exit_code
                    _settle_after_app_installer_change(context, package_id, exit_code)
                    if exit_code == 0:
                        _apply_post_install_options(context, package_id)
                elif action == "update":
                    action_result = _update_package_action_result(context, package_id)
                    exit_code = action_result.exit_code
                    _settle_after_app_installer_change(context, package_id, exit_code)
                    if exit_code == 0:
                        _apply_post_install_options(context, package_id)
                elif action == "pin":
                    exit_code = _pin_package(context, package_id)
                    action_result = PackageActionResult(exit_code, "ok" if exit_code == 0 else "failed")
                elif action == "uninstall":
                    exit_code = _uninstall_package(context, package_id)
                    exit_code, waited = _verified_uninstall_exit_code(context, package_id, exit_code)
                    notes = ("waited_for_user_or_external_uninstaller",) if waited else ()
                    action_result = PackageActionResult(exit_code, "uninstalled" if exit_code == 0 else "failed", notes)
                else:
                    exit_code = _check_package(context, package_id)
                    action_result = PackageActionResult(exit_code, "checked" if exit_code == 0 else "not_found")
            except OperationCancelledError:
                raise
            except Exception as exc:
                if context.cancelled():
                    raise
                # One broken package must not abort the rest of the batch.
                detail = f"{exc.__class__.__name__}: {exc}"
                context.log(f"{ANSI_YELLOW}[WARN] {package_id} failed: {detail}{ANSI_RESET}")
                context.log(f"{ANSI_YELLOW}[WARN] Continuing with the remaining packages.{ANSI_RESET}")
                exit_code = 1
                action_result = PackageActionResult(1, "failed", ("error", detail[:300]))

            result_item = {
                "package": package_id,
                "exit_code": exit_code,
                "status": action_result.status,
                "notes": list(action_result.notes),
            }
            results.append(result_item)

            if action_result.status == "already_installed":
                context.log(f"{ANSI_YELLOW}[WARN] Уже установлено; проверьте обновления: {package_id}{ANSI_RESET}")
            elif exit_code == 0:
                context.log(f"[OK] {package_id}")
            elif action == "check":
                context.log(f"[INFO] Check returned exit code {exit_code}: {package_id}")
            else:
                context.log(f"[WARN] Exit code {exit_code}: {package_id}")
                failed.append({"package": package_id, "exit_code": exit_code})

            context.progress(index / max(1, total))
    finally:
        if action in {"install", "uninstall"}:
            _clear_installed_options_cache()
        if action == "update":
            _clear_available_update_options_cache()

    if failed:
        _warn_already_installed_after_install(context, already_installed)
        summary = {
            "action": action,
            "selected_count": len(packages),
            "failed_count": len(failed),
            "already_installed_count": len(already_installed),
            "waited_count": sum(1 for item in results if "waited_for_user_or_external_uninstaller" in item.get("notes", [])),
            "status_counts": _summary_counts(results),
            "packages": packages,
            "results": results,
            "failed": failed,
            "already_installed": already_installed,
        }
        _write_action_summary(context, summary)
        names = ", ".join(str(item["package"]) for item in failed[:8])
        raise RuntimeError(f"{len(failed)} package(s) failed: {names}")

    _warn_already_installed_after_install(context, already_installed)
    summary = {
        "action": action,
        "selected_count": len(packages),
        "failed_count": 0,
        "already_installed_count": len(already_installed),
        "waited_count": sum(1 for item in results if "waited_for_user_or_external_uninstaller" in item.get("notes", [])),
        "status_counts": _summary_counts(results),
        "packages": packages,
        "results": results,
        "failed": failed,
        "already_installed": already_installed,
    }
    summary_paths = _write_action_summary(context, summary)
    return {"action": action, "packages": packages, "failed": failed, "summary": summary_paths}


def run_selected_packages(context: JobContext) -> dict[str, object]:
    action = _param_text(context, "package_action", "install").lower()
    packages = _selected_values(context, PACKAGE_FIELD_ORDER)
    return _run_package_batch(context, action, packages)


def pin_selected_packages(context: JobContext) -> dict[str, object]:
    packages = _selected_values(context, PIN_FIELD_ORDER)
    added = _append_pin_package_ids(context.paths.config, packages)
    for package_id in added:
        context.log(f"[INFO] Added to pins.txt: {package_id}")
    if packages and not added:
        context.log("[INFO] Selected packages are already listed in pins.txt.")
    return _run_package_batch(context, "pin", packages)


def check_selected_packages(context: JobContext) -> dict[str, object]:
    packages = _selected_values(context, PACKAGE_FIELD_ORDER)
    return _run_package_batch(context, "check", packages)


def update_available_packages(context: JobContext) -> dict[str, object]:
    packages = _selected_values(context, AVAILABLE_UPDATE_FIELD_ORDER)
    return _run_package_batch(context, "update", packages)


def update_all_available_packages(context: JobContext) -> dict[str, object]:
    _clear_available_update_options_cache()
    packages = [option["value"] for option in available_update_options(context.paths.root) if option.get("value")]
    if not packages:
        context.log("[INFO] No available WinGet updates were found.")
        summary = {
            "action": "update",
            "selected_count": 0,
            "failed_count": 0,
            "waited_count": 0,
            "status_counts": {},
            "packages": [],
            "results": [],
            "failed": [],
        }
        summary_paths = _write_action_summary(context, summary)
        return {"action": "update", "packages": [], "failed": [], "summary": summary_paths}
    return _run_package_batch(context, "update", packages)


def preview_available_updates(context: JobContext) -> dict[str, object]:
    _clear_available_update_options_cache()
    updates = [option for option in available_update_options(context.paths.root) if option.get("value")]
    context.log(f"[INFO] Available WinGet updates: {len(updates)}")
    rows: list[dict[str, str]] = []
    for option in updates:
        row = {
            "name": str(option.get("name") or ""),
            "id": str(option.get("value") or ""),
            "current": str(option.get("version") or ""),
            "available": str(option.get("available") or ""),
            "source": str(option.get("source") or ""),
            "label": str(option.get("label") or option.get("value") or ""),
        }
        rows.append(row)
        current = row["current"] or "?"
        available = row["available"] or "?"
        name = row["name"] or row["label"] or row["id"]
        context.log(f"[UPDATE] {name} | {row['id']} | {current} -> {available}")

    markdown = [
        "# WinGet Update Preview",
        "",
        f"Available updates: {len(rows)}",
        "",
        "| Name | ID | Current | Available | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        markdown.append(
            f"| {row['name'] or row['label']} | `{row['id']}` | {row['current']} | {row['available']} | {row['source']} |"
        )
    if not rows:
        markdown.append("| No updates available |  |  |  |  |")

    payload = {"available_count": len(rows), "updates": rows}
    paths = _write_report_pair(context, "update_preview", payload, markdown)
    context.progress(1.0)
    return {"available_count": len(rows), "updates": rows, "report": paths}


def uninstall_selected_installed_packages(context: JobContext) -> dict[str, object]:
    packages = _selected_values(context, (*GROUPED_UNINSTALL_FIELD_ORDER, *INSTALLED_UNINSTALL_FIELD_ORDER))
    return _run_package_batch(context, "uninstall", packages)


def install_package_by_id(context: JobContext) -> dict[str, object]:
    package_id = _param_text(context, "package_id")
    if not package_id:
        raise RuntimeError("Package ID is empty.")
    result = _run_package_batch(context, "install", [package_id])
    if package_id.lower() not in _known_manifest_ids(context.paths.root):
        if _append_custom_package_id(context.paths.config, package_id):
            context.log(f"[INFO] Added to custom package list: {package_id}")
        else:
            context.log(f"[INFO] Already in custom package list: {package_id}")
    return result


def uninstall_package_by_id(context: JobContext) -> dict[str, object]:
    package_id = _param_text(context, "package_id")
    if not package_id:
        raise RuntimeError("Package ID is empty.")
    return _run_package_batch(context, "uninstall", [package_id])


def search_winget(context: JobContext) -> dict[str, object]:
    query = _param_text(context, "query")
    if not query:
        raise RuntimeError("Search query is empty.")
    result = _run_winget(
        context,
        [
            "search",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
            query,
        ],
        check=False,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"WinGet search failed with exit code {result.exit_code}.")
    return {"query": query, "lines": len(result.lines)}


def add_package_to_list(context: JobContext) -> dict[str, object]:
    package_id = _param_text(context, "package_id")
    target_key = _param_text(context, "target_list", "custom")
    label = _param_text(context, "package_label", package_id) or package_id
    if not package_id:
        raise RuntimeError("Package ID is empty.")
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        raise RuntimeError(f"Package ID does not look like an exact WinGet ID: {package_id}")

    target = CONFIG_LIST_TARGETS.get(target_key)
    if not target:
        raise RuntimeError(f"Unknown package list target: {target_key}")
    _file_name, field_id, _section_name = target

    config_added, list_path = _append_package_id_to_config_target(context.paths.config, target_key, package_id)
    if config_added:
        context.log(f"[OK] Added to config list: {list_path}")
    else:
        occurrences = ", ".join(_config_list_occurrences(context.paths.config, package_id)) or "config"
        context.log(f"[INFO] Already tracked in config list(s): {occurrences}")

    manifest_added = False
    manifest_path = context.paths.config / "tool_manifest.yaml"
    if field_id:
        manifest_added = _append_package_id_to_manifest_field(manifest_path, field_id, package_id, label)
        if manifest_added:
            context.log(f"[OK] Added to GUI manifest group: {field_id}")
        else:
            locations = ", ".join(_manifest_package_id_locations(manifest_path, package_id)) or "manifest"
            context.log(f"[INFO] Already tracked in GUI manifest: {locations}")
    else:
        context.log("[INFO] Target list has no GUI checkbox group; manifest was not changed.")

    return {
        "package_id": package_id,
        "target_list": target_key,
        "config_added": config_added,
        "manifest_added": manifest_added,
    }


def export_winget(context: JobContext) -> dict[str, object]:
    export_text = _param_text(context, "export_path", "output\\winget-export.json")
    if export_text.replace("/", "\\").lower() == "output\\winget-export.json":
        export_path = _project_path(context, context.operation.parameters.get("output_path"), "output") / "winget-export.json"
    else:
        export_path = _resolve_project_path(context, export_text)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "export",
        "-o",
        str(export_path),
        "--source",
        "winget",
        "--accept-source-agreements",
    ]
    if _param_bool(context, "include_versions", True):
        args.append("--include-versions")
    result = _run_winget(context, args, check=False)
    if result.exit_code != 0:
        raise RuntimeError(f"WinGet export failed with exit code {result.exit_code}.")
    return {"export_path": str(export_path)}


def import_winget(context: JobContext) -> dict[str, object]:
    import_text = _param_text(context, "import_path", "input\\winget-export.json")
    if import_text.replace("/", "\\").lower() == "input\\winget-export.json":
        import_path = _project_path(context, context.operation.parameters.get("input_path"), "input") / "winget-export.json"
    else:
        import_path = _resolve_project_path(context, import_text)
    if not import_path.exists():
        raise RuntimeError(f"Import file was not found: {import_path}")
    args = [
        "import",
        "-i",
        str(import_path),
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    if _param_bool(context, "ignore_versions", False):
        args.append("--ignore-versions")
    if _param_bool(context, "no_upgrade", False):
        args.append("--no-upgrade")
    if _param_bool(context, "ignore_unavailable", True):
        args.append("--ignore-unavailable")
    result = _run_winget(context, args, check=False)
    if result.exit_code != 0:
        raise RuntimeError(f"WinGet import failed with exit code {result.exit_code}.")
    return {"import_path": str(import_path)}


def run_cmd_operation(context: JobContext) -> dict[str, object]:
    script = _param_text(context, "script")
    args = _string_list(context.operation.parameters.get("args", []))
    result = _run_cmd_script(context, script, args)
    return {"script": script, "exit_code": result.exit_code}


def update_winget_self(context: JobContext) -> dict[str, object]:
    result = _run_cmd_script(context, "system_core\\winget\\package_apps\\WinGet-Update.cmd")
    return {"exit_code": result.exit_code}


APP_INSTALLER_UPDATE_ID = "Microsoft.AppInstaller"


def update_app_installer(context: JobContext) -> dict[str, object]:
    """Update WinGet itself.

    App Installer carries the winget binary, so it is kept out of the package
    checkboxes: updating it mid-batch swaps the execution alias under the
    running operation. Here it is the only package, and the caller is told to
    restart Audion Get afterwards.
    """
    context.log(
        f"{ANSI_YELLOW}[WARN] Updating WinGet itself (Microsoft App Installer). "
        f"Restart Audion Get Tools after this update so the GUI picks up the new winget.{ANSI_RESET}"
    )
    presence = _package_presence(context, APP_INSTALLER_UPDATE_ID)
    if not presence.installed:
        context.log(f"[SKIP] Not installed: {APP_INSTALLER_UPDATE_ID}")
        return {"package": APP_INSTALLER_UPDATE_ID, "status": "skipped", "restart_required": False}

    result = _update_package_action_result(context, APP_INSTALLER_UPDATE_ID)
    _settle_after_app_installer_change(context, APP_INSTALLER_UPDATE_ID, result.exit_code)
    _clear_available_update_options_cache()
    _clear_installed_options_cache()

    if result.status == "updated":
        context.log(
            f"{ANSI_YELLOW}[WARN] WinGet was replaced. Close and start Audion Get Tools again "
            f"before running further package operations.{ANSI_RESET}"
        )
    version = _run_winget(context, ["--version"], check=False)
    for line in version.lines:
        context.log(f"[INFO] winget {line}")

    return {
        "package": APP_INSTALLER_UPDATE_ID,
        "status": result.status,
        "exit_code": result.exit_code,
        "restart_required": result.status == "updated",
    }


WINGET_MCP_SERVER_PATTERN = re.compile(r'"([A-Za-z]:\\\\.+?MCPServer\.exe)"')


def _winget_mcp_status(context: JobContext) -> dict[str, object]:
    """WinGet 1.29 ships its own MCP server; report it, do not route through it.

    Its two tools (`find-winget-packages`, `install-winget-package`) are a subset
    of the exact-ID paths Audion Get already runs with logging, reports, and the
    protected-ID guard, so the planner keeps calling WinGet directly.
    """
    result = _run_winget(context, ["mcp"], check=False)
    if result.exit_code != 0:
        context.log("[INFO] WinGet MCP server: not available in this WinGet build.")
        return {"mcp_available": False, "mcp_server_path": ""}

    server_path = ""
    for line in result.lines:
        match = WINGET_MCP_SERVER_PATTERN.search(line)
        if match:
            server_path = match.group(1).replace("\\\\", "\\")
            break
    if server_path:
        context.log(f"[INFO] WinGet MCP server: {server_path}")
    else:
        context.log("[INFO] WinGet MCP server: reported, path not parsed.")
    context.log(
        "[INFO] MCP tools (find-winget-packages, install-winget-package) stay outside Audion Get Tools: "
        "package actions run through the exact-ID paths with logs, reports, and protected-ID checks."
    )
    return {"mcp_available": True, "mcp_server_path": server_path}


def health_doctor(context: JobContext) -> dict[str, object]:
    context.log("[INFO] Audion Get Tools health check")
    try:
        winget_path = _resolve_winget_executable(context)
    except RuntimeError as exc:
        winget_path = ""
        context.log(f"{ANSI_YELLOW}[WARN] {exc}{ANSI_RESET}")
    context.log(f"[INFO] winget path: {winget_path or 'not found'}")

    version_lines: tuple[str, ...] = ()
    source_lines: tuple[str, ...] = ()
    mcp_status: dict[str, object] = {"mcp_available": False, "mcp_server_path": ""}
    if winget_path:
        version = _run_winget(context, ["--version"], check=False)
        version_lines = version.lines
        source_status = _run_winget(context, ["source", "list"], check=False)
        source_lines = source_status.lines
        mcp_status = _winget_mcp_status(context)
    else:
        context.log("[WARN] winget was not found in PATH.")

    installed = [option for option in installed_package_options(context.paths.root) if option.get("value")]
    updates = [option for option in available_update_options(context.paths.root) if option.get("value")]
    context.log(f"[INFO] Installed WinGet IDs: {len(installed)}")
    context.log(f"[INFO] Available updates: {len(updates)}")

    doctor_exit_code: int | None = None
    doctor_path = context.paths.system_core / "doctor.py"
    if doctor_path.exists():
        bundled_python = context.paths.root / "runtime" / "python.exe"
        python = str(bundled_python if bundled_python.exists() else Path(sys.executable))
        doctor_result = _run_process(context, [python, str(doctor_path)], check=False)
        doctor_exit_code = doctor_result.exit_code
    else:
        context.log(f"[WARN] Doctor script was not found: {doctor_path}")

    payload = {
        "winget_path": winget_path or "",
        "winget_version": list(version_lines),
        "winget_sources": list(source_lines),
        "installed_count": len(installed),
        "available_update_count": len(updates),
        "doctor_exit_code": doctor_exit_code,
        **mcp_status,
    }
    markdown = [
        "# Audion Get Tools Health",
        "",
        f"- WinGet path: `{winget_path or 'not found'}`",
        f"- WinGet version: `{version_lines[0] if version_lines else 'unknown'}`",
        f"- Installed WinGet IDs: {len(installed)}",
        f"- Available updates: {len(updates)}",
        f"- WinGet MCP server: `{mcp_status.get('mcp_server_path') or ('available' if mcp_status.get('mcp_available') else 'not available')}`",
        f"- Doctor exit code: {doctor_exit_code if doctor_exit_code is not None else 'not run'}",
        "",
        "## Sources",
        "",
        "```text",
        *source_lines,
        "```",
    ]
    paths = _write_report_pair(context, "health_doctor", payload, markdown)
    context.progress(1.0)
    return {**payload, "report": paths}


def validate_config_lists(context: JobContext) -> dict[str, object]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    total = 0

    for label, file_name in CONFIG_LISTS.items():
        path = context.paths.config / file_name
        context.log("")
        context.log(f"[LIST] {label}: {path}")
        if not path.exists():
            context.log("[WARN] Missing list.")
            continue

        count = 0
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            count += 1
            total += 1
            lower = line.lower()
            if lower in seen:
                duplicates.append(f"{line} ({seen[lower]} + {label})")
            else:
                seen[lower] = label
        context.log(f"[OK] Packages: {count}")

    if duplicates:
        context.log("")
        context.log("[WARN] Duplicates:")
        for item in duplicates:
            context.log(f"  - {item}")

    context.progress(1.0)
    return {"package_lines": total, "duplicates": duplicates}


def cleanup_input_output(context: JobContext) -> dict[str, object]:
    removed = 0
    for folder in [
        _project_path(context, context.operation.parameters.get("input_path"), "input"),
        _project_path(context, context.operation.parameters.get("output_path"), "output"),
    ]:
        folder.mkdir(parents=True, exist_ok=True)
        if folder.resolve().parent != context.paths.root.resolve():
            raise RuntimeError(f"Refusing to clean outside project root: {folder}")
        for item in folder.iterdir():
            if item.name == ".gitkeep":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
            context.log(f"[REMOVED] {item}")
    context.progress(1.0)
    return {"removed_items": removed}


def _path_is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def cleanup_logs(context: JobContext) -> dict[str, object]:
    logs_dir = context.paths.logs
    logs_dir.mkdir(parents=True, exist_ok=True)
    if logs_dir.resolve().parent != context.paths.root.resolve():
        raise RuntimeError(f"Refusing to clean outside project root: {logs_dir}")

    current_log = context.log_file.resolve()
    removed = 0
    skipped: list[str] = []
    for item in sorted(logs_dir.iterdir(), key=lambda path: path.name.lower()):
        try:
            if item.name in LOG_CLEANUP_PRESERVE_NAMES:
                context.log(f"[SKIP] Preserved service file: {item}")
                skipped.append(str(item))
                continue
            if item.resolve() == current_log:
                context.log(f"[SKIP] Current cleanup log: {item}")
                skipped.append(str(item))
                continue
            if _path_is_junction(item):
                context.log(f"[WARN] Skipping junction in logs folder: {item}")
                skipped.append(str(item))
                continue

            if item.is_dir() and not item.is_symlink():
                if not item.resolve().is_relative_to(logs_dir.resolve()):
                    raise RuntimeError(f"Refusing to remove log directory outside logs folder: {item}")
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
            context.log(f"[REMOVED] {item}")
        except FileNotFoundError:
            continue
        except OSError as exc:
            context.log(f"[WARN] Could not remove log item: {item} ({exc})")
            skipped.append(str(item))

    context.progress(1.0)
    return {"removed_items": removed, "skipped_items": skipped}


DOWNLOAD_DIRECTORY_NAME = "Downloads"
PORTABLE_DIRECTORY_NAME = "Portable"
INSTALL_DIRECTORY_NAME = "Install"
DOWNLOAD_ARTEFACT_SKIP_SUFFIXES = {".yaml", ".yml"}


def _winget_show_output(package_id: str) -> str:
    """Raw `winget show` text. Callers read the links, never the captions."""
    result = _run_winget_capture(
        [
            "show",
            "--id",
            package_id,
            "-e",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
        ],
        timeout=45.0,
    )
    if result.exit_code != 0:
        return ""
    return "\n".join(result.lines)


def package_page_link(package_id: str) -> dict[str, str]:
    """Resolve the page a person opens to pick a build for this package.

    This is a GUI helper, not an operation: opening a vendor page has to stay
    possible while a batch is running, so it never enters the job queue.
    """
    package_id = str(package_id or "").strip()
    if not package_id or _is_windows_feature_id(package_id):
        return {"package_id": package_id, "url": "", "origin": ""}
    page = resolve_package_page(package_id)
    if page is None:
        page = resolve_package_page(package_id, _winget_show_output(package_id))
    if page is None:
        return {"package_id": package_id, "url": "", "origin": ""}
    return {"package_id": package_id, "url": page.url, "origin": page.origin}


def _output_base(context: JobContext) -> Path:
    raw = str(context.operation.parameters.get("output_path") or "").strip()
    return Path(raw) if raw else context.paths.output


def _download_target_dir(context: JobContext) -> Path:
    """Installers land flat in `Downloads`; they are picked up and run, not kept."""
    return _output_base(context) / DOWNLOAD_DIRECTORY_NAME


def package_display_name(package_id: str) -> str:
    """The caption the checkbox shows, used for folder names instead of the id."""
    package_id = str(package_id or "").strip()
    if not package_id:
        return ""
    lowered = package_id.lower()
    try:
        fields = _manifest_package_fields()
    except Exception:  # noqa: BLE001 - a folder name must never break a download
        return ""
    for options in fields.values():
        for option in options:
            if str(option.get("value", "")).strip().lower() == lowered:
                return str(option.get("label") or "").strip()
    return ""


def _archive_target_dir(context: JobContext, package_id: str) -> Path:
    """A portable build keeps its own folder under `Portable`, named after the app."""
    name = download_folder_name(package_display_name(package_id), package_id.split(".")[-1])
    return _output_base(context) / PORTABLE_DIRECTORY_NAME / (name or "Portable build")


def _github_latest_assets(repo: str) -> tuple[str, list[tuple[str, str]]]:
    """Tag and `(name, url)` of every asset in the latest release.

    The API answers in one request but allows only 60 anonymous calls an hour,
    and that runs out on a busy day. The same list is on the release pages,
    where there is no quota at all, so the HTML is the fallback.
    """
    from urllib.request import Request, urlopen

    headers = {"User-Agent": "Audion-Get", "Accept": "application/vnd.github+json"}
    try:
        request = Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        with urlopen(request, timeout=60) as response:
            release = json.loads(response.read().decode("utf-8"))
        assets = [
            (str(item.get("name") or ""), str(item.get("browser_download_url") or ""))
            for item in release.get("assets", [])
        ]
        if assets:
            return str(release.get("tag_name") or "latest"), assets
    except Exception:  # noqa: BLE001 - any network or quota trouble falls back
        pass

    request = Request(f"https://github.com/{repo}/releases/latest", headers={"User-Agent": "Audion-Get"})
    with urlopen(request, timeout=60) as response:
        tag = response.geturl().rstrip("/").rsplit("/", 1)[-1]
    request = Request(
        f"https://github.com/{repo}/releases/expanded_assets/{tag}",
        headers={"User-Agent": "Audion-Get"},
    )
    with urlopen(request, timeout=60) as response:
        page = response.read().decode("utf-8", "replace")
    seen: list[tuple[str, str]] = []
    for path in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', page):
        name = path.rsplit("/", 1)[-1]
        pair = (name, f"https://github.com{path}")
        if pair not in seen:
            seen.append(pair)
    return tag, seen


def _download_github_build(
    context: JobContext,
    package_id: str,
    repo: str,
    pattern: str,
    target: Path,
    kind: str = "installer",
) -> dict[str, object]:
    """Take the build straight from the vendor's release page."""
    from system_core.services.portable_browser_service import download_file_to

    tag, assets = _github_latest_assets(repo)
    if not assets:
        raise RuntimeError(f"GitHub release {repo} has no files to download.")

    name = ""
    link = ""
    if pattern:
        for asset_name, asset_url in assets:
            if re.fullmatch(pattern, asset_name, re.IGNORECASE):
                name, link = asset_name, asset_url
                break
    if not name:
        name = pick_windows_asset([asset_name for asset_name, _ in assets], kind)
        link = next((url for asset_name, url in assets if asset_name == name), "")
    if not name or not link:
        listed = ", ".join(asset_name for asset_name, _ in assets[:12])
        raise RuntimeError(f"No Windows {kind} in the latest release of {repo} ({tag}). Assets: {listed}")

    target.mkdir(parents=True, exist_ok=True)
    label = package_display_name(package_id) or package_id
    context.log(f"[INFO] Taken from the release page, not the WinGet manifest: {repo} {tag}")
    # The link is already resolved, so this asks GitHub nothing more - the API
    # quota cannot bite between finding the file and fetching it.
    asset = download_file_to(context, link, target / name, f"{label} {tag}")
    return {
        "package_id": package_id,
        "download_directory": str(target),
        "files": [str(asset.path)],
        "exit_code": 0,
    }


def _github_repo_for_package(package_id: str) -> str:
    """The repository behind a package, when WinGet points at one."""
    page = resolve_package_page(package_id)
    if page is None:
        page = resolve_package_page(package_id, _winget_show_output(package_id))
    if page is None:
        return ""
    return github_repo_from_url(page.url)


def _download_artefacts(target: Path) -> list[Path]:
    """Every downloaded file, including the `Dependencies\\` MSIX packages."""
    if not target.is_dir():
        return []
    return sorted(
        item
        for item in target.rglob("*")
        if item.is_file() and item.suffix.lower() not in DOWNLOAD_ARTEFACT_SKIP_SUFFIXES
    )


def _download_package_to(
    context: JobContext,
    package_id: str,
    target: Path,
    installer_type: str = "",
) -> dict[str, object]:
    """Run `winget download` into `target` and report only what this run added.

    `Downloads` is shared by every package, so the folder is compared before and
    after instead of listed: otherwise one download would claim every file there.
    """
    target.mkdir(parents=True, exist_ok=True)
    before = set(_download_artefacts(target))
    context.log(f"[INFO] Download only, nothing is installed: {package_id}")
    context.log(f"[INFO] Target folder: {target}")
    context.progress(0.05)

    arguments = [
        "download",
        "--id",
        package_id,
        "-e",
        "--source",
        "winget",
        "--download-directory",
        str(target),
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
    ]
    if installer_type:
        arguments.extend(["--installer-type", installer_type])
    result = _run_winget(context, arguments, check=False)

    artefacts = [item for item in _download_artefacts(target) if item not in before]
    context.progress(1.0)
    if result.exit_code != 0 and not artefacts:
        detail = (
            f"WinGet has no {installer_type} build for {package_id} (exit code {result.exit_code}). "
            "Use the page button and pick an archive by hand."
            if installer_type
            else f"WinGet could not download {package_id} (exit code {result.exit_code}). "
            "Some packages ship no standalone installer; use the vendor page button instead."
        )
        raise RuntimeError(detail)

    for item in artefacts:
        context.log(f"[FILE] {item.name} ({item.stat().st_size:,} bytes)")
    if not artefacts:
        context.log(f"{ANSI_YELLOW}[WARN] WinGet reported success but left no file in {target}.{ANSI_RESET}")

    return {
        "package_id": package_id,
        "download_directory": str(target),
        "files": [str(item) for item in artefacts],
        "exit_code": result.exit_code,
    }


def download_package(context: JobContext) -> dict[str, object]:
    """Fetch the installer WinGet would run, without installing anything."""
    package_id = str(context.operation.parameters.get("download_package_id") or "").strip()
    if not package_id:
        raise RuntimeError("No package id to download.")
    if _is_windows_feature_id(package_id):
        raise RuntimeError(f"{package_id} is a Windows optional feature, not a downloadable package.")

    # For a package taken from its release page, the installer comes from there
    # too - otherwise the two buttons could hand out different versions, since
    # a release lands on GitHub before the WinGet manifest catches up.
    github_source = package_installer_github(package_id)
    if github_source:
        repo, pattern = github_source
        return _download_github_build(context, package_id, repo, pattern, _download_target_dir(context))

    try:
        return _download_package_to(context, package_id, _download_target_dir(context))
    except RuntimeError as winget_error:
        # WinGet keeps installers it cannot hand out: a manifest with no
        # standalone file, a broken hash, a package pulled from the index. If
        # the package lives on GitHub, its own release page still has one.
        repo = _github_repo_for_package(package_id)
        if not repo:
            raise
        context.log(f"{ANSI_YELLOW}[WARN] {winget_error}{ANSI_RESET}")
        context.log(f"[INFO] Trying the release page instead: {repo}")
        try:
            return _download_github_build(context, package_id, repo, "", _download_target_dir(context))
        except Exception as github_error:  # noqa: BLE001 - report both attempts
            raise RuntimeError(f"{winget_error} GitHub did not help either: {github_error}") from github_error


def download_package_archive(context: JobContext) -> dict[str, object]:
    """Fetch the archive or standalone build, the one that needs no installation."""
    package_id = str(context.operation.parameters.get("download_package_id") or "").strip()
    if not package_id:
        raise RuntimeError("No package id to download.")

    # Some vendors ship a portable build that never reaches the WinGet manifest.
    # Then the file comes straight from the release, into the same folder the
    # archive button would have filled.
    github_source = package_archive_github(package_id)
    if github_source:
        repo, pattern = github_source
        return _download_github_build(
            context,
            package_id,
            repo,
            pattern,
            _archive_target_dir(context, package_id),
            "archive",
        )

    installer_type = package_archive_type(package_id)
    if not installer_type:
        raise RuntimeError(f"{package_id} has no archive build in WinGet.")
    try:
        return _download_package_to(
            context,
            package_id,
            _archive_target_dir(context, package_id),
            installer_type,
        )
    except RuntimeError as winget_error:
        repo = _github_repo_for_package(package_id)
        if not repo:
            raise
        context.log(f"{ANSI_YELLOW}[WARN] {winget_error}{ANSI_RESET}")
        context.log(f"[INFO] Trying the release page instead: {repo}")
        try:
            return _download_github_build(
                context,
                package_id,
                repo,
                "",
                _archive_target_dir(context, package_id),
                "archive",
            )
        except Exception as github_error:  # noqa: BLE001 - report both attempts
            raise RuntimeError(f"{winget_error} GitHub did not help either: {github_error}") from github_error
