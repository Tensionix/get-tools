"""Downloads from TechPowerUp.

TechPowerUp does not publish a direct file URL. A download costs three requests
that share one cookie jar: the file page hands out the numeric file id, posting
that id returns the mirror list, and posting id + mirror answers with a 302 to a
signed, time-limited URL. There is no CAPTCHA anywhere in that chain.

The signed URL expires, so it is resolved at click time and never cached.

The same file page also lists every earlier version with its files, dates and
checksums; `list_versions` reads that list so a version store can pick an
older build, not only the current one.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
import re

from system_core.core.jobs import JobContext


TECHPOWERUP_DOWNLOAD_ROOT = "https://www.techpowerup.com/download/"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Audion-Get"
REQUEST_TIMEOUT = 60.0

FILE_ID_PATTERN = re.compile(r'name="id"\s+value="(\d+)"', re.IGNORECASE)
SERVER_ID_PATTERN = re.compile(r'name="server_id"\s+value="(\d+)"', re.IGNORECASE)

VERSION_BLOCK_PATTERN = re.compile(r'<div class="version[^"]*">(.*?)</ul>\s*</div>', re.DOTALL)
VERSION_TITLE_PATTERN = re.compile(r'<h3 class="title">\s*(.*?)\s*</h3>', re.DOTALL)
VERSION_DATE_PATTERN = re.compile(r'<span class="date">([^<]*)</span>')
FILE_BLOCK_PATTERN = re.compile(r'<li class="file[^"]*">(.*?)</li>', re.DOTALL)
FILE_SIZE_PATTERN = re.compile(r'<div class="filesize">([^<]*)</div>')
FILE_TITLE_PATTERN = re.compile(r'<h4 class="title">([^<]*)</h4>')
FILE_NAME_PATTERN = re.compile(r'<div class="filename"[^>]*>([^<]*)</div>')
FILE_SHA256_PATTERN = re.compile(r'SHA256:</div>\s*<div class="hash-value">([0-9A-Fa-f]+)</div>')


@dataclass(frozen=True)
class TechPowerUpFile:
    version: str
    date: str
    kind: str  # "Installer", "Portable", or "" when the page lists one file
    file_name: str
    file_id: str
    size: str
    sha256: str


def _slug_url(slug: str) -> str:
    return f"{TECHPOWERUP_DOWNLOAD_ROOT}{slug.strip('/')}/"


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Referer": referer,
    }


def fetch_page(slug: str) -> str:
    page_url = _slug_url(slug)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    with opener.open(Request(page_url, headers=_headers(page_url)), timeout=REQUEST_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_versions(page: str) -> list[TechPowerUpFile]:
    """Every file of every version on a TechPowerUp download page, newest first."""
    files: list[TechPowerUpFile] = []
    for block in VERSION_BLOCK_PATTERN.finditer(page):
        html = block.group(1)
        title_match = VERSION_TITLE_PATTERN.search(html)
        date_match = VERSION_DATE_PATTERN.search(html)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        version_match = re.search(r"v?(\d+(?:\.\d+)+)", title)
        version = version_match.group(1) if version_match else title
        for file_block in FILE_BLOCK_PATTERN.finditer(html):
            file_html = file_block.group(1)
            id_match = FILE_ID_PATTERN.search(file_html)
            name_match = FILE_NAME_PATTERN.search(file_html)
            if not id_match or not name_match:
                continue
            kind_match = FILE_TITLE_PATTERN.search(file_html)
            size_match = FILE_SIZE_PATTERN.search(file_html)
            sha_match = FILE_SHA256_PATTERN.search(file_html)
            files.append(
                TechPowerUpFile(
                    version=version,
                    date=date_match.group(1).strip() if date_match else "",
                    kind=kind_match.group(1).strip() if kind_match else "",
                    file_name=name_match.group(1).strip(),
                    file_id=id_match.group(1),
                    size=size_match.group(1).strip() if size_match else "",
                    sha256=sha_match.group(1).upper() if sha_match else "",
                )
            )
    return files


def list_versions(slug: str) -> list[TechPowerUpFile]:
    return parse_versions(fetch_page(slug))


def resolve_signed_url(
    context: JobContext | None,
    slug: str,
    file_id: str | None = None,
) -> tuple[str, str, str]:
    """Walk the three-step flow and return (url, file name, file id).

    Without `file_id` the current release is taken: the version list is newest
    first, so the first id on the page is the latest file.
    """
    page_url = _slug_url(slug)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    request = Request(page_url, headers=_headers(page_url))
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        page = response.read().decode("utf-8", errors="replace")

    file_ids = FILE_ID_PATTERN.findall(page)
    if not file_ids:
        raise RuntimeError(f"TechPowerUp page has no downloadable file: {page_url}")
    if file_id:
        if str(file_id) not in file_ids:
            raise RuntimeError(f"TechPowerUp no longer lists file {file_id} on {page_url}")
        file_id = str(file_id)
    else:
        file_id = file_ids[0]

    form = urlencode({"id": file_id}).encode()
    with opener.open(Request(page_url, data=form, headers=_headers(page_url)), timeout=REQUEST_TIMEOUT) as response:
        mirrors_page = response.read().decode("utf-8", errors="replace")
    server_ids = SERVER_ID_PATTERN.findall(mirrors_page) or SERVER_ID_PATTERN.findall(page)
    if not server_ids:
        raise RuntimeError(f"TechPowerUp returned no mirrors for file {file_id}")

    errors: list[str] = []
    for server_id in server_ids:
        if context is not None and context.cancelled():
            raise RuntimeError("Cancelled by user.")
        form = urlencode({"id": file_id, "server_id": server_id}).encode()
        try:
            # Only the headers are needed: the body is fetched again from the
            # signed URL, with progress, by the shared downloader.
            with opener.open(
                Request(page_url, data=form, headers=_headers(page_url)), timeout=REQUEST_TIMEOUT
            ) as response:
                final_url = response.geturl()
        except OSError as exc:
            errors.append(f"mirror {server_id}: {exc}")
            continue
        name = Path(urlparse(final_url).path).name
        if name and final_url != page_url:
            if context is not None:
                context.log(f"[INFO] TechPowerUp file id {file_id}, mirror {server_id}: {name}")
            return final_url, name, file_id
        errors.append(f"mirror {server_id}: no file URL")

    raise RuntimeError("No TechPowerUp mirror answered with a file URL. " + "; ".join(errors))
