"""Downloads from TechPowerUp.

TechPowerUp does not publish a direct file URL. A download costs three requests
that share one cookie jar: the file page hands out the numeric file id, posting
that id returns the mirror list, and posting id + mirror answers with a 302 to a
signed, time-limited URL. There is no CAPTCHA anywhere in that chain.

The signed URL expires, so it is resolved at click time and never cached.
"""

from __future__ import annotations

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


def _slug_url(slug: str) -> str:
    return f"{TECHPOWERUP_DOWNLOAD_ROOT}{slug.strip('/')}/"


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Referer": referer,
    }


def resolve_signed_url(context: JobContext, slug: str) -> tuple[str, str, str]:
    """Walk the three-step flow and return (url, file name, file id)."""
    page_url = _slug_url(slug)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    request = Request(page_url, headers=_headers(page_url))
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        page = response.read().decode("utf-8", errors="replace")

    file_ids = FILE_ID_PATTERN.findall(page)
    if not file_ids:
        raise RuntimeError(f"TechPowerUp page has no downloadable file: {page_url}")
    # The version list is newest first, so the first id is the current release.
    file_id = file_ids[0]

    form = urlencode({"id": file_id}).encode()
    with opener.open(Request(page_url, data=form, headers=_headers(page_url)), timeout=REQUEST_TIMEOUT) as response:
        mirrors_page = response.read().decode("utf-8", errors="replace")
    server_ids = SERVER_ID_PATTERN.findall(mirrors_page) or SERVER_ID_PATTERN.findall(page)
    if not server_ids:
        raise RuntimeError(f"TechPowerUp returned no mirrors for file {file_id}")

    errors: list[str] = []
    for server_id in server_ids:
        if context.cancelled():
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
            context.log(f"[INFO] TechPowerUp file id {file_id}, mirror {server_id}: {name}")
            return final_url, name, file_id
        errors.append(f"mirror {server_id}: no file URL")

    raise RuntimeError("No TechPowerUp mirror answered with a file URL. " + "; ".join(errors))
