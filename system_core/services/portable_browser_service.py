from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile

from system_core.core.jobs import JobContext, hidden_subprocess_kwargs, utf8_subprocess_env


USER_AGENT = "Audion-Get-Portable"
CHROME_PLUS_REPO = "Bush2021/chrome_plus"
UNGOOGLED_PORTABLE_REPO = "portapps/ungoogled-chromium-portable"
ZEN_PORTABLE_REPO = "wysh3/Zen-Browser-Portable"
CENT_BROWSER_HOME = "https://www.centbrowser.com/"
CHROME_STANDALONE_URL = "https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe"
# The web installer is ~12 MB and picks the locale and architecture itself at
# install time, which is exactly what "matching the system" means here.
CHROME_WEB_INSTALLER_URL = "https://dl.google.com/chrome/install/latest/chrome_installer.exe"
CHROME_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"


@dataclass(frozen=True)
class DownloadedAsset:
    name: str
    url: str
    path: Path
    sha256: str
    size: int


def _param_text(context: JobContext, key: str, default: str = "") -> str:
    return str(context.operation.parameters.get(key, default) or "").strip()


def _param_bool(context: JobContext, key: str, default: bool = False) -> bool:
    value = context.operation.parameters.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "да"}
    if value is None:
        return default
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_project_path(context: JobContext, raw_path: str, default_name: str) -> Path:
    path_text = str(raw_path or "").strip().strip('"') or default_name
    path = Path(os.path.expandvars(path_text)).expanduser()
    if not path.is_absolute():
        path = context.paths.root / path
    return path.resolve()


def _output_root(context: JobContext) -> Path:
    output = _resolve_project_path(context, _param_text(context, "output_path"), "output")
    output.mkdir(parents=True, exist_ok=True)
    return output


def _input_root(context: JobContext) -> Path:
    return _resolve_project_path(context, _param_text(context, "input_path"), "input")


def _portable_root(context: JobContext) -> Path:
    portable_root = _output_root(context) / "Portable"
    portable_root.mkdir(parents=True, exist_ok=True)
    return portable_root


def _archives_dir(context: JobContext) -> Path:
    path = _portable_root(context) / "_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _downloads_dir(context: JobContext) -> Path:
    """Installers land flat in `Downloads`, next to the WinGet ones.

    `Portable\\_archives` holds the archives a portable build is unpacked from.
    A web installer is neither an archive nor portable, so it does not go there.
    """
    path = _output_root(context) / "Downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tmp_dir(context: JobContext) -> Path:
    path = _portable_root(context) / "_tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove_tree(path: Path, *, ignore_missing: bool = True) -> None:
    if not path.exists():
        if ignore_missing:
            return
        raise FileNotFoundError(str(path))

    def handle_remove_error(function: Any, item_path: str, _exc_info: Any) -> None:
        os.chmod(item_path, 0o700)
        function(item_path)

    shutil.rmtree(path, onerror=handle_remove_error)


def _reset_dir(path: Path) -> None:
    if path.exists():
        _remove_tree(path)
    path.mkdir(parents=True, exist_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return cleaned or "download"


def _http_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"HTTP request failed: {url} ({exc})") from exc


def _http_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"HTTP request failed: {url} ({exc})") from exc


def _progress_between(context: JobContext, start: float | None, end: float | None, fraction: float) -> None:
    if start is None or end is None:
        return
    context.progress(start + (end - start) * max(0.0, min(1.0, fraction)))


def _download(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
    *,
    progress_start: float | None = None,
    progress_end: float | None = None,
) -> DownloadedAsset:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        size = target.stat().st_size
        digest = _sha256(target)
        context.log(f"[CACHE] {label}: {target} ({size} bytes)")
        context.log(f"[SHA256] {digest}")
        _progress_between(context, progress_start, progress_end, 1.0)
        return DownloadedAsset(label, url, target, digest, size)

    context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    part = target.with_name(target.name + ".part")
    if part.exists():
        part.unlink()
    try:
        with urlopen(request, timeout=120) as response, part.open("wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            last_logged_percent = -10
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    fraction = downloaded / total
                    _progress_between(context, progress_start, progress_end, fraction)
                    percent = int(fraction * 100)
                    if percent >= last_logged_percent + 10:
                        context.log(f"[DOWNLOAD] {label}: {percent}% ({downloaded}/{total} bytes)")
                        last_logged_percent = percent
            if total <= 0:
                _progress_between(context, progress_start, progress_end, 1.0)
        part.replace(target)
    except (HTTPError, URLError, TimeoutError) as exc:
        if part.exists():
            part.unlink()
        raise RuntimeError(f"Download failed: {url} ({exc})") from exc
    size = target.stat().st_size
    digest = _sha256(target)
    context.log(f"[OK] {target} ({size} bytes)")
    context.log(f"[SHA256] {digest}")
    return DownloadedAsset(label, url, target, digest, size)


def _github_latest_release(repo: str) -> dict[str, Any]:
    return _http_json(f"https://api.github.com/repos/{repo}/releases/latest")


def _github_asset(release: dict[str, Any], pattern: str) -> dict[str, Any]:
    regex = re.compile(pattern, re.IGNORECASE)
    for asset in release.get("assets", []):
        name = str(asset.get("name") or "")
        if regex.fullmatch(name):
            return asset
    asset_names = ", ".join(str(asset.get("name") or "") for asset in release.get("assets", []))
    raise RuntimeError(f"Release asset not found by pattern {pattern!r}. Assets: {asset_names}")


def _seven_zip_path(context: JobContext) -> Path:
    return context.paths.root / "Tools" / "7zip" / "bin" / "7za.exe"


def _seven_zip_version(context: JobContext) -> str:
    exe = _seven_zip_path(context)
    if not exe.exists():
        return ""
    result = subprocess.run(
        [str(exe), "i"],
        cwd=str(context.paths.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if "7-Zip" in line:
            return line.strip()
    return "7-Zip available"


def _require_7zip(context: JobContext) -> Path:
    exe = _seven_zip_path(context)
    version = _seven_zip_version(context)
    if not exe.exists() or not version:
        raise RuntimeError("Portable 7-Zip is not available. Run 'Check / install portable 7-Zip' first.")
    context.log(f"[7ZIP] {version}")
    return exe


def _run_7z(context: JobContext, args: list[str], *, cwd: Path | None = None) -> None:
    exe = _require_7zip(context)
    command = [str(exe), *args]
    context.log("[7Z] " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd or context.paths.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        **hidden_subprocess_kwargs(),
    )
    for line in result.stdout.splitlines():
        if line.strip():
            context.log(line)
    for line in result.stderr.splitlines():
        if line.strip():
            context.log("[STDERR] " + line)
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip failed with exit code {result.returncode}.")


def _extract_archive(context: JobContext, archive: Path, target: Path) -> None:
    _reset_dir(target)
    _run_7z(context, ["x", str(archive), f"-o{target}", "-y"])


def _copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            if destination.exists():
                _remove_tree(destination)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


def _find_dir(root: Path, name: str) -> Path | None:
    lowered = name.lower()
    for item in root.rglob("*"):
        if item.is_dir() and item.name.lower() == lowered:
            return item
    return None


def _find_file(root: Path, name: str) -> Path | None:
    lowered = name.lower()
    for item in root.rglob("*"):
        if item.is_file() and item.name.lower() == lowered:
            return item
    return None


def _zip_dir(context: JobContext, source_dir: Path, zip_path: Path) -> Path:
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    context.log(f"[ZIP] {source_dir} -> {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in source_dir.rglob("*"):
            if item.is_dir():
                rel = item.relative_to(source_dir.parent).as_posix().rstrip("/") + "/"
                archive.writestr(rel, b"")
            elif item.is_file():
                archive.write(item, item.relative_to(source_dir.parent))
    context.log(f"[OK] ZIP: {zip_path} ({zip_path.stat().st_size} bytes)")
    return zip_path


def _archive_format(context: JobContext) -> str:
    value = _param_text(context, "archive_format", "zip").lower()
    return value if value in {"zip", "7z"} else "zip"


def _archive_portable_dir(context: JobContext, source_dir: Path, base_name: str) -> Path:
    archive_format = _archive_format(context)
    if archive_format == "7z":
        archive_path = _portable_root(context) / f"{base_name}.7z"
        if archive_path.exists():
            archive_path.unlink()
        context.log(f"[7Z] {source_dir} -> {archive_path}")
        _run_7z(context, ["a", "-t7z", "-mx=9", str(archive_path), source_dir.name], cwd=source_dir.parent)
        context.log(f"[OK] 7Z: {archive_path} ({archive_path.stat().st_size} bytes)")
        return archive_path
    return _zip_dir(context, source_dir, _portable_root(context) / f"{base_name}.zip")


def _safe_output_child(context: JobContext, name: str) -> Path:
    portable = _portable_root(context).resolve()
    target = (portable / name).resolve()
    if target == portable or not target.is_relative_to(portable):
        raise RuntimeError(f"Refusing unsafe portable output path: {target}")
    return target


def _publish_portable_dir(context: JobContext, source_dir: Path, name: str) -> Path:
    target = _safe_output_child(context, name)
    if target.exists():
        _remove_tree(target)
    context.log(f"[PUBLISH] {source_dir} -> {target}")
    shutil.copytree(source_dir, target)
    context.log(f"[OK] FOLDER: {target}")
    return target


def _download_github_asset(
    context: JobContext,
    repo: str,
    pattern: str,
    label: str,
    *,
    progress_start: float | None = None,
    progress_end: float | None = None,
) -> DownloadedAsset:
    release = _github_latest_release(repo)
    asset = _github_asset(release, pattern)
    tag = str(release.get("tag_name") or release.get("name") or "latest")
    name = str(asset.get("name") or f"{label}.download")
    url = str(asset.get("browser_download_url") or "")
    if not url:
        raise RuntimeError(f"GitHub asset has no download URL: {repo} {name}")
    context.log(f"[GITHUB] {repo} latest={tag} asset={name}")
    return _download(
        context,
        url,
        _archives_dir(context) / _safe_name(name),
        f"{label} {tag}",
        progress_start=progress_start,
        progress_end=progress_end,
    )


def download_file_to(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
) -> DownloadedAsset:
    """Fetch a known link into a known file.

    Used when the caller already resolved the release itself - for instance off
    the HTML release page, because the GitHub API had no quota left.
    """
    return _download(context, url, target, label)


def _cent_portable_url() -> tuple[str, str]:
    html = _http_text(CENT_BROWSER_HOME)
    match = re.search(r"https://static\.centbrowser\.com/win_stable/([^\"'<>]+?/centbrowser_[^\"'<>]+?_x64_portable\.exe)", html)
    if not match:
        raise RuntimeError("Cent Browser portable x64 URL was not found on the official page.")
    url = "https://static.centbrowser.com/win_stable/" + match.group(1)
    version_match = re.search(r"centbrowser_([0-9.]+)_x64_portable\.exe", url)
    version = version_match.group(1) if version_match else "latest"
    return url, version


def _find_single_payload_dir(extract_root: Path, preferred_names: Iterable[str] = ()) -> Path:
    for name in preferred_names:
        found = _find_dir(extract_root, name)
        if found:
            return found
    children = [item for item in extract_root.iterdir() if item.is_dir()]
    if len(children) == 1:
        return children[0]
    return extract_root


def _package_extracted_archive(context: JobContext, archive: Path, label: str, preferred_dirs: Iterable[str] = ()) -> Path:
    work = _tmp_dir(context) / _safe_name(label)
    _extract_archive(context, archive, work)
    payload = _find_single_payload_dir(work, preferred_dirs)
    final_zip = _portable_root(context) / f"{label}.zip"
    return _zip_dir(context, payload, final_zip)


def _copy_output_artifact(context: JobContext, source: Path, filename: str) -> Path:
    target = _portable_root(context) / filename
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    context.log(f"[OK] ARTIFACT: {target} ({target.stat().st_size} bytes)")
    return target


def _download_chrome_plus(
    context: JobContext,
    *,
    progress_start: float | None = None,
    progress_end: float | None = None,
) -> DownloadedAsset:
    return _download_github_asset(
        context,
        CHROME_PLUS_REPO,
        r"Chrome\+\+_v.+_x86_x64_arm64\.7z",
        "Chrome++",
        progress_start=progress_start,
        progress_end=progress_end,
    )


def _download_chrome_standalone(
    context: JobContext,
    *,
    progress_start: float | None = None,
    progress_end: float | None = None,
) -> DownloadedAsset:
    url = _param_text(context, "chrome_download_url", CHROME_STANDALONE_URL) or CHROME_STANDALONE_URL
    filename = _safe_name(url.rsplit("/", 1)[-1].split("?", 1)[0] or "ChromeStandaloneSetup64.exe")
    return _download(
        context,
        url,
        _archives_dir(context) / filename,
        "Google Chrome standalone x64",
        progress_start=progress_start,
        progress_end=progress_end,
    )


def _find_chrome_payload_archive(context: JobContext, root: Path, depth: int = 0) -> Path | None:
    chrome_7z = _find_file(root, "Chrome.7z")
    if chrome_7z:
        return chrome_7z
    if depth >= 4:
        return None

    candidates: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        lowered = item.name.lower()
        if lowered.endswith(".7z") or lowered.endswith("chrome_installer.exe"):
            candidates.append(item)

    for index, candidate in enumerate(candidates, start=1):
        if candidate.name.lower() == "chrome.7z":
            return candidate
        target = _tmp_dir(context) / f"chrome_payload_{depth}_{index}_{_safe_name(candidate.stem)[:48]}"
        context.log(f"[PROBE] Chrome payload candidate: {candidate}")
        try:
            _extract_archive(context, candidate, target)
        except RuntimeError as exc:
            context.log(f"[SKIP] Not a readable Chrome payload archive: {candidate} ({exc})")
            continue
        found = _find_chrome_payload_archive(context, target, depth + 1)
        if found:
            return found
    return None


def _extract_chrome_bin(context: JobContext, chrome_installer: Path) -> Path:
    installer_extract = _tmp_dir(context) / "chrome_installer"
    _extract_archive(context, chrome_installer, installer_extract)
    chrome_7z = _find_chrome_payload_archive(context, installer_extract)
    if not chrome_7z:
        raise RuntimeError("Chrome.7z was not found inside the Chrome standalone installer.")
    context.log(f"[FOUND] Chrome.7z: {chrome_7z}")
    chrome_extract = _tmp_dir(context) / "chrome_7z"
    _extract_archive(context, chrome_7z, chrome_extract)
    chrome_bin = _find_dir(chrome_extract, "Chrome-bin")
    if chrome_bin:
        return chrome_bin
    if (chrome_extract / "chrome.exe").exists():
        return chrome_extract
    raise RuntimeError("Chrome-bin was not found after extracting Chrome.7z.")


def _chrome_plus_arch(context: JobContext) -> str:
    value = _param_text(context, "chrome_plus_arch", "x64").lower()
    return value if value in {"x86", "x64", "arm64"} else "x64"


def _chrome_plus_arch_dir(context: JobContext, chrome_plus_archive: Path) -> Path:
    plus_extract = _tmp_dir(context) / "chrome_plus"
    _extract_archive(context, chrome_plus_archive, plus_extract)
    arch = _chrome_plus_arch(context)
    arch_dir = plus_extract / arch
    if not arch_dir.is_dir():
        arch_dir = _find_dir(plus_extract, arch) or arch_dir
    if not arch_dir.is_dir():
        raise RuntimeError(f"{arch} folder was not found inside Chrome++ archive.")
    return arch_dir


def _prepare_chrome_plus_clean_root(context: JobContext, chrome_plus_archive: Path) -> Path:
    x64_dir = _chrome_plus_arch_dir(context, chrome_plus_archive)
    portable_dir = _tmp_dir(context) / "Google Chrome Portable"
    if portable_dir.exists():
        _remove_tree(portable_dir)
    shutil.copytree(x64_dir, portable_dir)
    return portable_dir


def _seed_chrome_plus_app(context: JobContext, portable_dir: Path, chrome_plus_archive: Path) -> None:
    x64_dir = _chrome_plus_arch_dir(context, chrome_plus_archive)
    app_dir = _clear_chrome_app_dir(portable_dir)
    template_app = x64_dir / "App"
    if template_app.is_dir():
        context.log(f"[COPY] Chrome++ App template -> {app_dir}")
        _copy_tree_contents(template_app, app_dir)
    for folder_name in ("Cache", "Data"):
        target = portable_dir / folder_name
        template = x64_dir / folder_name
        if not target.exists():
            if template.is_dir():
                shutil.copytree(template, target)
            else:
                target.mkdir(parents=True, exist_ok=True)


def _find_input_chrome_portable(context: JobContext) -> Path:
    root = _input_root(context)
    if root.name.lower() == "google chrome portable" and root.is_dir():
        return root
    candidate = root / "Google Chrome Portable"
    if candidate.is_dir():
        return candidate
    raise RuntimeError("Google Chrome Portable was not found in input. Put it into input\\Google Chrome Portable or select that folder as Source.")


def _clear_chrome_app_dir(portable_dir: Path) -> Path:
    app_dir = portable_dir / "App"
    if portable_dir.name != "Google Chrome Portable":
        raise RuntimeError(f"Refusing to update unexpected portable root: {portable_dir}")
    if app_dir.resolve().parent != portable_dir.resolve():
        raise RuntimeError(f"Refusing to clear unsafe App path: {app_dir}")
    if app_dir.exists():
        _remove_tree(app_dir)
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def _remove_legacy_chrome_runtime_dirs(context: JobContext, portable_dir: Path) -> None:
    for folder_name in ("Chrome",):
        folder = portable_dir / folder_name
        if not folder.exists():
            continue
        if folder.resolve().parent != portable_dir.resolve():
            raise RuntimeError(f"Refusing to remove unsafe legacy runtime path: {folder}")
        context.log(f"[CLEAN] Legacy runtime folder: {folder}")
        _remove_tree(folder)


def _place_chrome_bin(context: JobContext, portable_dir: Path, chrome_bin: Path, *, clear_app: bool = False) -> None:
    app_dir = _clear_chrome_app_dir(portable_dir) if clear_app else portable_dir / "App"
    app_dir.mkdir(parents=True, exist_ok=True)
    context.log(f"[COPY] Chrome-bin contents -> {app_dir}")
    _copy_tree_contents(chrome_bin, app_dir)
    if not any(app_dir.rglob("chrome.exe")):
        raise RuntimeError("chrome.exe was not found in Google Chrome Portable\\App after copy.")


def _system_ui_language() -> str:
    if os.name != "nt":
        return "en"
    try:
        import ctypes
        import locale

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
        name = locale.windows_locale.get(lang_id)
    except Exception:
        return "en"
    return name.replace("_", "-") if name else "en"


def _chrome_web_language(context: JobContext) -> str:
    explicit = _param_text(context, "chrome_language")
    if explicit:
        return explicit
    system_language = _system_ui_language()
    if system_language:
        return system_language
    ui_language = _param_text(context, "ui_language")
    return ui_language or "en"


def _installed_chrome_path() -> Path | None:
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, CHROME_APP_PATHS_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        candidate = Path(str(value).strip('"'))
        if candidate.exists():
            return candidate
    return None


def _chrome_version(chrome_path: Path | None) -> str:
    if chrome_path is None:
        return ""
    version_dirs = sorted(
        (item.name for item in chrome_path.parent.iterdir() if item.is_dir() and re.fullmatch(r"[\d.]+", item.name)),
        key=lambda name: [int(part) for part in name.split(".") if part.isdigit()],
    )
    return version_dirs[-1] if version_dirs else ""


def install_google_chrome_web(context: JobContext) -> dict[str, object]:
    """Download the Google Chrome web installer for this system and run it."""
    language = _chrome_web_language(context)
    url = f"{CHROME_WEB_INSTALLER_URL}?hl={language}"
    context.log(f"[INFO] Google Chrome web installer, system language: {language}")
    before = _installed_chrome_path()
    if before is not None:
        context.log(f"[INFO] Chrome is already installed: {before} ({_chrome_version(before) or 'version unknown'})")

    asset = _download(
        context,
        url,
        _downloads_dir(context) / "ChromeSetup.exe",
        "Google Chrome web installer",
        progress_start=0.0,
        progress_end=0.6,
    )
    context.progress(0.6)

    context.log(f"[RUN] {asset.path} /silent /install")
    context.activity("Google Chrome: install")
    try:
        result = subprocess.run(
            [str(asset.path), "/silent", "/install"],
            cwd=str(context.paths.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            env=utf8_subprocess_env(),
            **hidden_subprocess_kwargs(),
        )
    finally:
        context.activity("")
    for stream in (result.stdout, result.stderr):
        for line in (stream or "").splitlines():
            if line.strip():
                context.log(line)

    chrome_path = _installed_chrome_path()
    version = _chrome_version(chrome_path)
    if chrome_path is None:
        raise RuntimeError(
            f"Chrome was not registered after the installer finished (exit code {result.returncode}). "
            f"The downloaded installer is kept at {asset.path}."
        )
    context.log(f"[OK] Google Chrome: {chrome_path} ({version or 'version unknown'})")
    context.progress(1.0)
    return {
        "installer": str(asset.path),
        "url": url,
        "language": language,
        "exit_code": result.returncode,
        "chrome_path": str(chrome_path),
        "version": version,
        "was_installed": before is not None,
    }


def install_portable_7zip(context: JobContext) -> dict[str, object]:
    before = _seven_zip_version(context)
    if before:
        context.log(f"[OK] Portable 7-Zip already available: {before}")
        return {"installed": False, "version": before, "path": str(_seven_zip_path(context))}

    script = context.paths.root / "install" / "Install-Portable-7Zip.cmd"
    if not script.exists():
        raise RuntimeError(f"Portable 7-Zip installer was not found: {script}")
    context.log(f"[RUN] {script} /NOPAUSE")
    result = subprocess.run(
        [str(script), "/NOPAUSE"],
        cwd=str(context.paths.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=utf8_subprocess_env(),
        **hidden_subprocess_kwargs(),
    )
    for line in result.stdout.splitlines():
        if line.strip():
            context.log(line)
    for line in result.stderr.splitlines():
        if line.strip():
            context.log("[STDERR] " + line)
    if result.returncode != 0:
        raise RuntimeError(f"Portable 7-Zip install failed with exit code {result.returncode}.")
    version = _seven_zip_version(context)
    if not version:
        raise RuntimeError("Portable 7-Zip installer finished, but 7za.exe is not usable.")
    context.log(f"[OK] Portable 7-Zip installed: {version}")
    context.progress(1.0)
    return {"installed": True, "version": version, "path": str(_seven_zip_path(context))}


def download_portable_browsers(context: JobContext) -> dict[str, object]:
    selected = _string_list(context.operation.parameters.get("portable_browsers", []))
    if not selected:
        raise RuntimeError("Select at least one portable browser.")
    _require_7zip(context)
    keep_temp = _param_bool(context, "keep_temp", False)
    results: list[dict[str, str]] = []
    total = len(selected)

    for index, browser_id in enumerate(selected, start=1):
        item_start = (index - 1) / max(1, total)
        item_end = index / max(1, total)
        download_end = item_start + (item_end - item_start) * 0.7
        if browser_id == "ungoogled_chromium_portable":
            asset = _download_github_asset(
                context,
                UNGOOGLED_PORTABLE_REPO,
                r"ungoogled-chromium-portable-win64-.+\.7z",
                "Ungoogled Chromium Portable",
                progress_start=item_start,
                progress_end=download_end,
            )
            zip_path = _package_extracted_archive(context, asset.path, "Ungoogled Chromium Portable", ["ungoogled-chromium-portable"])
            results.append({"browser": browser_id, "archive": str(asset.path), "zip": str(zip_path), "sha256": asset.sha256})
        elif browser_id == "zen_browser_portable":
            asset = _download_github_asset(
                context,
                ZEN_PORTABLE_REPO,
                r"zen-windows-portable\.zip",
                "Zen Browser Portable",
                progress_start=item_start,
                progress_end=download_end,
            )
            final_zip = _portable_root(context) / "Zen Browser Portable.zip"
            shutil.copy2(asset.path, final_zip)
            context.log(f"[OK] ZIP: {final_zip} ({final_zip.stat().st_size} bytes)")
            results.append({"browser": browser_id, "archive": str(asset.path), "zip": str(final_zip), "sha256": asset.sha256})
        elif browser_id == "cent_browser_portable":
            url, version = _cent_portable_url()
            asset = _download(
                context,
                url,
                _archives_dir(context) / Path(url).name,
                f"Cent Browser Portable {version}",
                progress_start=item_start,
                progress_end=download_end,
            )
            artifact = _copy_output_artifact(context, asset.path, "Cent Browser Portable x64.exe")
            results.append({"browser": browser_id, "archive": str(asset.path), "artifact": str(artifact), "sha256": asset.sha256})
        else:
            raise RuntimeError(f"Unknown portable browser: {browser_id}")
        context.progress(item_end)

    if not keep_temp:
        _remove_tree(_tmp_dir(context))
    return {"downloaded": len(results), "results": results, "output": str(_portable_root(context))}


def build_google_chrome_portable(context: JobContext) -> dict[str, object]:
    _require_7zip(context)
    keep_temp = _param_bool(context, "keep_temp", False)
    package_archive = _param_bool(context, "package_archive", False)
    chrome_plus = _download_chrome_plus(context, progress_start=0.0, progress_end=0.2)
    context.progress(0.2)
    chrome_installer = _download_chrome_standalone(context, progress_start=0.2, progress_end=0.4)
    context.progress(0.4)
    portable_dir = _prepare_chrome_plus_clean_root(context, chrome_plus.path)
    context.progress(0.55)
    chrome_bin = _extract_chrome_bin(context, chrome_installer.path)
    context.progress(0.75)
    _place_chrome_bin(context, portable_dir, chrome_bin)
    result_path = (
        _archive_portable_dir(context, portable_dir, "Google Chrome Portable")
        if package_archive
        else _publish_portable_dir(context, portable_dir, "Google Chrome Portable")
    )
    if not keep_temp:
        _remove_tree(_tmp_dir(context))
    context.progress(1.0)
    return {
        "mode": "clean",
        "packaged": package_archive,
        "archive_format": _archive_format(context) if package_archive else "",
        "artifact": str(result_path),
        "chrome_plus_archive": str(chrome_plus.path),
        "chrome_archive": str(chrome_installer.path),
        "output": str(_portable_root(context)),
    }


def update_google_chrome_portable(context: JobContext) -> dict[str, object]:
    _require_7zip(context)
    keep_temp = _param_bool(context, "keep_temp", False)
    package_archive = _param_bool(context, "package_archive", False)
    source = _find_input_chrome_portable(context)
    work = _tmp_dir(context) / "Google Chrome Portable"
    if work.exists():
        _remove_tree(work)
    context.log(f"[COPY] {source} -> {work}")
    shutil.copytree(source, work)
    _remove_legacy_chrome_runtime_dirs(context, work)
    context.progress(0.2)
    chrome_plus = _download_chrome_plus(context, progress_start=0.2, progress_end=0.25)
    context.progress(0.25)
    chrome_installer = _download_chrome_standalone(context, progress_start=0.25, progress_end=0.45)
    context.progress(0.45)
    chrome_bin = _extract_chrome_bin(context, chrome_installer.path)
    context.progress(0.7)
    _seed_chrome_plus_app(context, work, chrome_plus.path)
    _place_chrome_bin(context, work, chrome_bin)
    result_path = (
        _archive_portable_dir(context, work, "Google Chrome Portable.updated")
        if package_archive
        else _publish_portable_dir(context, work, "Google Chrome Portable")
    )
    if not keep_temp:
        _remove_tree(_tmp_dir(context))
    context.progress(1.0)
    return {
        "mode": "update",
        "source": str(source),
        "packaged": package_archive,
        "archive_format": _archive_format(context) if package_archive else "",
        "artifact": str(result_path),
        "chrome_plus_archive": str(chrome_plus.path),
        "chrome_archive": str(chrome_installer.path),
        "output": str(_portable_root(context)),
    }


def download_google_chrome_web(context: JobContext) -> dict[str, object]:
    """Fetch the Google Chrome web installer and stop; nothing is installed."""
    language = _chrome_web_language(context)
    url = f"{CHROME_WEB_INSTALLER_URL}?hl={language}"
    context.log(f"[INFO] Download only, nothing is installed. System language: {language}")
    asset = _download(
        context,
        url,
        _downloads_dir(context) / "ChromeSetup.exe",
        "Google Chrome web installer",
        progress_start=0.0,
        progress_end=1.0,
    )
    context.log(f"[FILE] {asset.path} ({asset.size:,} bytes)")
    context.progress(1.0)
    return {
        "language": language,
        "url": url,
        "path": str(asset.path),
        "size": asset.size,
        "sha256": asset.sha256,
    }
