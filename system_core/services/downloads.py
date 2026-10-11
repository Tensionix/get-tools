"""One download for every service: a live line, the size, the sha256, and no half of a file under its name.

A file is fetched into `<name>.part` and takes its name only when the whole of it came; a note
beside it (`<name>.download.json`) says it is a finished download, so a second run takes it from
the disk. Here too is the reading of a GitHub release - its latest tag and the file of it that
answers a pattern.

Until 3.0.0 these lived in the service of portable browsers, which the program no longer has.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import hashlib
import json
import re

from system_core.core.jobs import JobContext


USER_AGENT = "Audion-Get-Portable"


@dataclass(frozen=True)
class DownloadedAsset:
    name: str
    url: str
    path: Path
    sha256: str
    size: int


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
    from system_core.services.github_auth import github_headers

    headers = github_headers() if "api.github.com" in url else {"Accept": "application/vnd.github+json"}
    headers["User-Agent"] = USER_AGENT
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"HTTP request failed: {url} ({exc})") from exc


def _progress_between(context: JobContext, start: float | None, end: float | None, fraction: float) -> None:
    if start is None or end is None:
        return
    context.progress(start + (end - start) * max(0.0, min(1.0, fraction)))


def _done_marker(target: Path) -> Path:
    """The note of a finished download: its size and SHA256, written only after a complete download."""
    return target.with_name(target.name + ".ok")


def _cached(target: Path, expected_sha256: str) -> tuple[int, str] | None:
    """A file counts as cached only with its note of a finished download, and when it still matches it."""
    marker = _done_marker(target)
    if not target.is_file() or not marker.is_file():
        return None
    try:
        note = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    size = target.stat().st_size
    if size <= 0 or int(note.get("size", -1)) != size:
        return None
    digest = _sha256(target)
    if str(note.get("sha256", "")).lower() != digest or (expected_sha256 and expected_sha256 != digest):
        return None
    return size, digest


def _download(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
    *,
    progress_start: float | None = None,
    progress_end: float | None = None,
    expected_sha256: str = "",
) -> DownloadedAsset:
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = expected_sha256.lower().removeprefix("sha256:").strip()
    cached = _cached(target, expected)
    if cached:
        size, digest = cached
        context.log(f"[CACHE] {label}: {target} ({size} bytes)")
        context.log(f"[SHA256] {digest}")
        _progress_between(context, progress_start, progress_end, 1.0)
        return DownloadedAsset(label, url, target, digest, size)
    if target.exists():
        context.log(f"[CACHE] {label}: {target.name} has no note of a finished download - fetching it again")

    context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    part = target.with_name(target.name + ".part")
    if part.exists():
        part.unlink()
    _done_marker(target).unlink(missing_ok=True)
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
        # A connection that closed early is not a download: nothing is published under the final name.
        if total > 0 and downloaded != total:
            raise IncompleteRead(b"", total - downloaded)
        digest = _sha256(part)
        if expected and digest != expected:
            raise RuntimeError(f"SHA256 mismatch: expected {expected}, got {digest}")
        part.replace(target)
    except (HTTPError, URLError, TimeoutError, IncompleteRead, OSError, RuntimeError) as exc:
        if part.exists():
            part.unlink()
        if isinstance(exc, IncompleteRead):
            raise RuntimeError(f"Download incomplete: {url} ({downloaded} of {total} bytes) - run it again") from exc
        raise RuntimeError(f"Download failed: {url} ({exc})") from exc
    size = target.stat().st_size
    _done_marker(target).write_text(json.dumps({"size": size, "sha256": digest, "url": url}), encoding="utf-8")
    context.log(f"[OK] {target} ({size} bytes)")
    context.log(f"[SHA256] {digest}" + (" (matches the release)" if expected else ""))
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
