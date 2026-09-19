"""A GitHub token for the release lookups, kept under Windows DPAPI.

GitHub allows 60 anonymous API calls an hour per address; a busy day of
Tabby, rclone and vendor lookups runs through that and the program falls back
to scraping the release pages. A personal token (no scopes needed) raises the
quota to 5000 an hour. The token is stored in `config\\github_token.dpapi`,
encrypted with the Windows Data Protection API for the current user: the
file is useless on another machine or under another account, and nothing in
the program ever prints the token.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from system_core.core.jobs import JobContext

TOKEN_FILE_NAME = "github_token.dpapi"
ENTROPY = b"Audion Get Tools GitHub token"
USER_AGENT = "Audion-Get"
RATE_LIMIT_URL = "https://api.github.com/rate_limit"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _crypt(func_name: str, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI is a Windows service; the GitHub token store needs Windows.")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    func = getattr(crypt32, func_name)
    source = _blob(data)
    entropy = _blob(ENTROPY)
    result = _DataBlob()
    ok = func(ctypes.byref(source), None, ctypes.byref(entropy), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result))
    if not ok:
        raise RuntimeError(f"{func_name} failed (Windows error {ctypes.GetLastError()}).")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt for the current Windows user (CryptProtectData)."""
    return _crypt("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    """Decrypt what `protect` produced, on the same machine and account."""
    return _crypt("CryptUnprotectData", data)


# ----- the token file -----


def default_root() -> Path:
    return Path(__file__).resolve().parents[2]


def token_path(root: Path | str | None = None) -> Path:
    return Path(root or default_root()) / "config" / TOKEN_FILE_NAME


def load_token(root: Path | str | None = None) -> str:
    """The stored token, or '' when there is none or it cannot be decrypted here."""
    path = token_path(root)
    if not path.exists():
        return ""
    try:
        return unprotect(base64.b64decode(path.read_text(encoding="ascii").strip())).decode("utf-8").strip()
    except Exception:  # noqa: BLE001 - another account or machine: the file is simply not ours
        return ""


def save_token(token: str, root: Path | str | None = None) -> Path:
    path = token_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.b64encode(protect(token.strip().encode("utf-8"))).decode("ascii") + "\n", encoding="ascii")
    return path


def clear_token(root: Path | str | None = None) -> bool:
    path = token_path(root)
    if path.exists():
        path.unlink()
        return True
    return False


def github_headers(root: Path | str | None = None, **extra: str) -> dict[str, str]:
    """Request headers for api.github.com: the user agent, the JSON accept, and the token when there is one."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    headers.update(extra)
    token = load_token(root)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ----- jobs -----


def _root(context: JobContext) -> Path:
    paths = getattr(context, "paths", None)
    root = getattr(paths, "root", None)
    return Path(root) if root else default_root()


def _rate_limit(headers: dict[str, str]) -> dict[str, Any]:
    request = Request(RATE_LIMIT_URL, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("GitHub rejected the token (HTTP 401): it is wrong, expired or revoked.") from exc
        raise RuntimeError(f"GitHub answered HTTP {exc.code} to the rate-limit query.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub is unreachable: {getattr(exc, 'reason', exc)}") from exc
    core = (payload.get("resources") or {}).get("core") or payload.get("rate") or {}
    return {"limit": int(core.get("limit") or 0), "remaining": int(core.get("remaining") or 0)}


def check_github_token(context: JobContext) -> dict[str, object]:
    """Say whether a token is stored and what quota GitHub grants this program right now."""
    root = _root(context)
    token = load_token(root)
    headers = github_headers(root)
    context.log(f"[GITHUB] token: {'stored (' + token[:4] + '...)' if token else 'none, anonymous requests'}")
    quota = _rate_limit(headers)
    context.log(f"[GITHUB] API quota: {quota['remaining']} of {quota['limit']} calls left this hour")
    if not token:
        context.log("[HINT] Anonymous GitHub allows 60 calls an hour. Store a token with 'GitHub token' to get 5000.")
    return {"token": bool(token), **quota, "path": str(token_path(root))}


def set_github_token(context: JobContext) -> dict[str, object]:
    """Store the token the prompt handed over (DPAPI, this user only), or remove it when the field is empty."""
    root = _root(context)
    token = str(context.operation.parameters.get("github_token") or "").strip()
    if not token:
        removed = clear_token(root)
        context.log("[GITHUB] token removed; requests go anonymous again" if removed else "[GITHUB] no token was stored")
        return {"token": False, "removed": removed}
    quota = _rate_limit(github_headers(root, Authorization=f"Bearer {token}"))
    path = save_token(token, root)
    context.log(f"[GITHUB] token accepted: {quota['remaining']} of {quota['limit']} calls left this hour")
    context.log(f"[OK] stored under DPAPI for this Windows account: {path}")
    return {"token": True, **quota, "path": str(path)}
