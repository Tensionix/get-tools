"""Blackmagic Design: DaVinci Resolve, Fusion, Blackmagic RAW, Desktop Video and the rest.

The support site keeps one JSON catalog of every release it ever published,
with a download id per platform. A signed link comes from one POST per id.

Two things about that POST are not obvious and were found by trying:

- Studio and most other products answer `{"country": "us"}` with the link
  straight away. No account, no form.
- The free DaVinci Resolve answers 403 "Must register to be able to perform
  the download". The `downloadOnly` mode other products have is closed for
  Resolve; only the registration form (name, e-mail, phone, address) unlocks
  it. No account is created and nothing is confirmed by mail, but the values
  are the person's own to enter - this module never invents them.

The link lives about three hours and supports HTTP ranges, so an interrupted
download resumes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import RegistrationRequired, VendorBuild


CATALOG_URL = "https://www.blackmagicdesign.com/api/support/us/downloads.json"
REGISTER_URL = "https://www.blackmagicdesign.com/api/register/us/download/{download_id}"
SITE = "https://www.blackmagicdesign.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
PLATFORMS: tuple[str, ...] = ("Windows", "Windows ARM", "Mac OS X", "Linux")
CATALOG_TTL_SECONDS = 3600.0

# The form the free Resolve asks for, in the order the site shows it.
REGISTRATION_FIELDS: tuple[str, ...] = (
    "firstname",
    "lastname",
    "email",
    "phone",
    "country",
    "city",
    "street",
    "state",
)

# Products people come here for sit on top; the rest of the catalog follows alphabetically.
PREFERRED_PRODUCTS: tuple[str, ...] = (
    "DaVinci Resolve Studio",
    "DaVinci Resolve",
    "Fusion Studio",
    "Fusion",
    "Blackmagic RAW",
    "Desktop Video",
    "Blackmagic Camera",  # the catalog's name for Camera Setup
    "DaVinci Resolve Project Server",
)

# "DaVinci Resolve Studio 21.0.4 Update", "Blackmagic RAW 5.1", "Fusion 21 Public Beta 2"
NAME_PATTERN = re.compile(
    r"^(?P<product>.+?) (?P<version>\d+(?:\.\d+)*)(?P<update> Update)?(?: Public Beta (?P<beta>\d+))?$"
)

HttpCall = Callable[[str, str, str | None, dict[str, str]], tuple[int, str]]


@dataclass(frozen=True)
class ParsedName:
    product: str
    version: str
    update: bool
    beta: int


def parse_build_name(name: str) -> ParsedName | None:
    match = NAME_PATTERN.match(str(name or "").strip())
    if not match:
        return None
    return ParsedName(
        product=match.group("product").strip(),
        version=match.group("version"),
        update=bool(match.group("update")),
        beta=int(match.group("beta") or 0),
    )


def version_key(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(version or ""))]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def build_sort_key(build: VendorBuild) -> tuple[Any, ...]:
    # Newest first; a final release sorts above the public betas of the same number.
    return (version_key(build.version), 0 if build.beta == 0 else -1, -build.beta)


def _default_http(method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
    request = Request(url, data=body.encode("utf-8") if body is not None else None, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=60) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the body is only for the message
            text = ""
        return int(exc.code), text
    except URLError as exc:
        raise RuntimeError(f"Blackmagic request failed: {exc.reason}") from exc


def catalog_builds(catalog: dict[str, Any], vendor_id: str = "blackmagic") -> list[VendorBuild]:
    """Every catalog entry that names a product and a version, one build per platform."""
    builds: list[VendorBuild] = []
    for entry in catalog.get("downloads", []) or []:
        if not isinstance(entry, dict):
            continue
        parsed = parse_build_name(str(entry.get("name") or ""))
        if parsed is None:
            continue
        urls = entry.get("urls") or {}
        if not isinstance(urls, dict):
            continue
        for platform, items in urls.items():
            if not isinstance(items, list) or not items:
                continue
            first = items[0] if isinstance(items[0], dict) else {}
            download_id = str(first.get("downloadId") or "").strip()
            if not download_id:
                continue
            builds.append(
                VendorBuild(
                    vendor=vendor_id,
                    product=parsed.product,
                    version=parsed.version,
                    platform=str(platform),
                    date=str(entry.get("date") or "").strip(),
                    download_id=download_id,
                    name=str(entry.get("name") or "").strip(),
                    beta=parsed.beta,
                )
            )
    return builds


def order_products(products: list[str]) -> list[str]:
    preferred = [name for name in PREFERRED_PRODUCTS if name in products]
    rest = sorted(name for name in products if name not in PREFERRED_PRODUCTS)
    return [*preferred, *rest]


def registration_body(platform: str, registration: dict[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = {"platform": platform, "policy": True, "product": "DaVinci Resolve"}
    for key in REGISTRATION_FIELDS:
        body[key] = str(registration.get(key) or "").strip()
    return body


def registration_is_filled(registration: dict[str, str] | None) -> bool:
    if not registration:
        return False
    return all(str(registration.get(key) or "").strip() for key in REGISTRATION_FIELDS)


class BlackmagicProvider:
    id = "blackmagic"
    name = "Blackmagic Design"
    platforms = PLATFORMS

    _instance: "BlackmagicProvider | None" = None

    def __init__(self, http: HttpCall | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._http: HttpCall = http or _default_http
        self._clock = clock
        self._catalog: tuple[float, list[VendorBuild]] | None = None

    @classmethod
    def instance(cls) -> "BlackmagicProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- catalog -----

    def all_builds(self, *, force: bool = False) -> list[VendorBuild]:
        now = self._clock()
        if not force and self._catalog and now - self._catalog[0] < CATALOG_TTL_SECONDS:
            return self._catalog[1]
        status, text = self._http("GET", CATALOG_URL, None, {"Accept": "application/json"})
        if status != 200:
            raise RuntimeError(f"Blackmagic catalog answered HTTP {status}.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Blackmagic catalog is not valid JSON.") from exc
        builds = catalog_builds(data, self.id)
        self._catalog = (now, builds)
        return builds

    def products(self, platform: str) -> list[str]:
        names = {build.product for build in self.all_builds() if build.platform == platform}
        return order_products(list(names))

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection
        found = [
            build
            for build in self.all_builds()
            if build.product == product and build.platform == platform
        ]
        return sorted(found, key=build_sort_key, reverse=True)

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        for build in self.all_builds():
            if build.download_id == wanted:
                return build
        return None

    # ----- links -----

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        url = REGISTER_URL.format(download_id=build.download_id)
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": SITE,
            "Referer": f"{SITE}/support/download/{build.download_id}/{build.platform}",
            "Accept-Language": "en-US,en;q=0.9",
        }
        status, text = self._http("POST", url, json.dumps({"country": "us"}), headers)
        if status == 200 and text.strip().startswith("http"):
            return text.strip()
        if status == 403:
            if registration_is_filled(registration):
                body = json.dumps(registration_body(build.platform, registration or {}))
                status, text = self._http("POST", url, body, headers)
                if status == 200 and text.strip().startswith("http"):
                    return text.strip()
                raise RuntimeError(f"Blackmagic refused the registration form: HTTP {status} {text.strip()[:200]}")
            raise RegistrationRequired(
                f"Blackmagic issues the link for '{build.name}' only after the registration form.",
                REGISTRATION_FIELDS,
            )
        raise RuntimeError(f"Blackmagic did not issue a link for '{build.name}': HTTP {status} {text.strip()[:200]}")
