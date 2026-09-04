"""Windows images through UUP dump: any build, any month, with the updates already inside.

Microsoft publishes Windows as UUP files, the same packages Windows Update
installs, and UUP dump keeps a catalog of every build it has seen: feature
releases, each cumulative update as its own build number, Insider flights.
For a chosen build, language and edition the site hands out a small script
package; the script downloads the files straight from Microsoft with aria2
and assembles a bootable ISO on this machine. That is how a "Windows 11 24H2
as of August 2026" ISO is made without waiting for Microsoft to refresh its
own media.

Sources, all without an account:

- `json-api/listid.php` - the catalog; `listlangs.php` and `listeditions.php`
  describe one build.
- `get.php?id=&pack=&edition=` with `autodl=2` posted - the script package
  as a zip, no link to resolve: the provider fetches it itself.

The script needs administrator rights and a folder path without spaces; the
download job checks the path before starting it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import PLATFORM_WINDOWS, PLATFORM_WINDOWS_ARM, VendorBuild


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
SITE = "https://uupdump.net"
LIST_URL = f"{SITE}/json-api/listid.php?sortByDate=1"
PACK_URL = f"{SITE}/get.php"
PLATFORMS: tuple[str, ...] = (PLATFORM_WINDOWS, PLATFORM_WINDOWS_ARM)
ARCH_BY_PLATFORM = {PLATFORM_WINDOWS: "amd64", PLATFORM_WINDOWS_ARM: "arm64"}
CATALOG_TTL_SECONDS = 1800.0
ID_PREFIX = "uup:"

PRODUCT_WIN11 = "Windows 11"
PRODUCT_WIN10 = "Windows 10"
PRODUCT_INSIDER = "Windows 11 Insider"
PRODUCTS: tuple[str, ...] = (PRODUCT_WIN11, PRODUCT_WIN10, PRODUCT_INSIDER)

# Catalog title -> (product, short label). Anything else in the catalog
# (cumulative-update packages, Server, .NET, drivers) is not an image and is skipped.
TITLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^Windows 11, version (?P<release>\w+) \((?P<build>[\d.]+)\)$"), PRODUCT_WIN11),
    (re.compile(r"^Feature update to Windows 10, version (?P<release>\w+) \((?P<build>[\d.]+)\)$"), PRODUCT_WIN10),
    (re.compile(r"^Windows 11 Insider Preview (?P<release>.*?)\s*\((?P<build>[\d.]+)\)"), PRODUCT_INSIDER),
    (re.compile(r"^Windows 11 Insider Preview (?P<build>10\.0\.[\d.]+)\s*\((?P<release>[^)]+)\)"), PRODUCT_INSIDER),
)

DEFAULT_LANG = "en-us"
DEFAULT_EDITION = "professional"
# Posted with the package request: convert to ISO, integrate updates, clean up, add .NET 3.5.
PACK_FORM = {"autodl": "2", "updates": "1", "cleanup": "1", "netfx": "1", "esd": "0"}

HttpCall = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes, dict[str, str]]]


def _default_http(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, data=body, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=120) as response:
            return int(response.status), response.read(), {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        return int(exc.code), b"", {}
    except URLError as exc:
        raise RuntimeError(f"UUP dump request failed: {exc.reason}") from exc


@dataclass(frozen=True)
class CatalogEntry:
    uuid: str
    title: str
    build: str
    arch: str
    created: int
    product: str
    release: str


def classify_title(title: str) -> tuple[str, str, str] | None:
    """`(product, release, build)` for an image title, None for anything that is not an image."""
    for pattern, product in TITLE_RULES:
        match = pattern.match(str(title or "").strip())
        if match:
            return product, match.group("release").strip(), match.group("build")
    return None


def parse_catalog(data: dict[str, Any]) -> list[CatalogEntry]:
    raw = (data.get("response") or {}).get("builds") or {}
    items = raw.values() if isinstance(raw, dict) else raw
    entries: list[CatalogEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        classified = classify_title(str(item.get("title") or ""))
        if not classified:
            continue
        product, release, build = classified
        uuid = str(item.get("uuid") or "").strip()
        if not uuid:
            continue
        entries.append(
            CatalogEntry(
                uuid=uuid,
                title=str(item.get("title") or "").strip(),
                build=build or str(item.get("build") or ""),
                arch=str(item.get("arch") or ""),
                created=int(item.get("created") or 0),
                product=product,
                release=release,
            )
        )
    entries.sort(key=lambda entry: entry.created, reverse=True)
    return entries


def short_date(created: int) -> str:
    if not created:
        return ""
    return datetime.fromtimestamp(created, tz=timezone.utc).strftime("%d %b %Y").lstrip("0")


# Editions UUP dump derives from Pro while converting, in the order its form lists them.
VIRTUAL_EDITIONS: tuple[str, ...] = (
    "ProfessionalWorkstation",
    "ProfessionalEducation",
    "Education",
    "Enterprise",
    "ServerRdsh",
    "IoTEnterprise",
    "IoTEnterpriseK",
)


def normalize_virtual(virtual: Any) -> tuple[str, ...]:
    """The requested additional editions, known ones only, in the form's order."""
    if isinstance(virtual, str):
        items = [part for part in re.split(r"[+,\s]+", virtual) if part]
    elif isinstance(virtual, (list, tuple)):
        items = [str(part).strip() for part in virtual]
    else:
        items = []
    wanted = {item.lower() for item in items}
    return tuple(name for name in VIRTUAL_EDITIONS if name.lower() in wanted)


SHORT_PRODUCT = {PRODUCT_WIN11: "Win11", PRODUCT_WIN10: "Win10", PRODUCT_INSIDER: "Win11Insider"}
SHORT_ARCH = {"amd64": "x64", "arm64": "arm64", "x86": "x86"}
SHORT_EDITION = {
    "professional": "pro",
    "core": "home",
    "professionaln": "pron",
    "coren": "homen",
    "core;professional": "home-pro",
    "professional;core": "home-pro",
}


def pack_file_name(entry: CatalogEntry, lang: str, edition: str, virtual: tuple[str, ...] = ()) -> str:
    """Short on purpose: the build folder is named after it, and DISM inside the
    converter fails with "file name too long" once the path grows."""
    product = SHORT_PRODUCT.get(entry.product, entry.product.replace(" ", ""))
    release = re.sub(r"[^\w.]+", "", entry.release)
    arch = SHORT_ARCH.get(entry.arch, entry.arch)
    edition_short = SHORT_EDITION.get(edition.lower(), edition.lower().replace(";", "-"))
    extra = f"_plus{len(virtual)}" if virtual else ""
    return f"{product}_{release}_{entry.build}_{arch}_{lang}_{edition_short}{extra}.zip"


def build_id(uuid: str, lang: str, edition: str, virtual: tuple[str, ...] = ()) -> str:
    text = f"{ID_PREFIX}{uuid}:{lang}:{edition}"
    return f"{text}:{'+'.join(virtual)}" if virtual else text


def split_id(download_id: str) -> tuple[str, str, str, tuple[str, ...]] | None:
    if not str(download_id or "").startswith(ID_PREFIX):
        return None
    parts = download_id[len(ID_PREFIX):].split(":")
    if len(parts) not in (3, 4):
        return None
    virtual = normalize_virtual(parts[3]) if len(parts) == 4 else ()
    return parts[0], parts[1], parts[2], virtual


class UupDumpProvider:
    id = "uupdump"
    name = "Windows (UUP dump)"
    platforms = PLATFORMS

    _instance: "UupDumpProvider | None" = None

    def __init__(self, http: HttpCall | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._http: HttpCall = http or _default_http
        self._clock = clock
        self._catalog: tuple[float, list[CatalogEntry]] | None = None

    @classmethod
    def instance(cls) -> "UupDumpProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- catalog -----

    def catalog(self, *, force: bool = False) -> list[CatalogEntry]:
        now = self._clock()
        if not force and self._catalog and now - self._catalog[0] < CATALOG_TTL_SECONDS:
            return self._catalog[1]
        status, body, _headers = self._http("GET", LIST_URL, None, {"Accept": "application/json"})
        if status != 200:
            raise RuntimeError(f"UUP dump catalog answered HTTP {status}.")
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("UUP dump catalog is not valid JSON.") from exc
        entries = parse_catalog(data)
        self._catalog = (now, entries)
        return entries

    def entry_by_uuid(self, uuid: str) -> CatalogEntry | None:
        for entry in self.catalog():
            if entry.uuid == uuid:
                return entry
        return None

    def _build(self, entry: CatalogEntry, lang: str, edition: str, virtual: tuple[str, ...] = ()) -> VendorBuild:
        notes = f"{lang} {edition.lower()}"
        if virtual:
            notes += " +" + " +".join(virtual)
        return VendorBuild(
            vendor=self.id,
            product=entry.product,
            version=f"{entry.release} {entry.build}",
            platform=next((key for key, arch in ARCH_BY_PLATFORM.items() if arch == entry.arch), PLATFORM_WINDOWS),
            date=short_date(entry.created),
            download_id=build_id(entry.uuid, lang, edition, virtual),
            name=pack_file_name(entry, lang, edition, virtual),
            notes=notes,
        )

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(PRODUCTS) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        if platform not in self.platforms or product not in PRODUCTS:
            return []
        arch = ARCH_BY_PLATFORM[platform]
        lang = str(selection.get("lang") or DEFAULT_LANG)
        edition = str(selection.get("edition") or DEFAULT_EDITION)
        virtual = normalize_virtual(selection.get("virtual"))
        return [
            self._build(entry, lang, edition, virtual)
            for entry in self.catalog()
            if entry.product == product and entry.arch == arch
        ]

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        parts = split_id(download_id)
        if not parts:
            return None
        uuid, lang, edition, virtual = parts
        entry = self.entry_by_uuid(uuid)
        return self._build(entry, lang, edition, virtual) if entry else None

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        """The page a person would use; the package itself comes from `fetch`."""
        del registration
        parts = split_id(build.download_id)
        if not parts:
            raise RuntimeError(f"Not a UUP dump build id: {build.download_id}")
        uuid, lang, edition, _virtual = parts
        return f"{SITE}/download.php?id={uuid}&pack={lang}&edition={edition}"

    def fetch(self, build: VendorBuild, target: Path) -> Path:
        """Ask UUP dump for the script package and save it as `target`."""
        parts = split_id(build.download_id)
        if not parts:
            raise RuntimeError(f"Not a UUP dump build id: {build.download_id}")
        uuid, lang, edition, virtual = parts
        url = f"{PACK_URL}?{urlencode({'id': uuid, 'pack': lang, 'edition': edition})}"
        form: list[tuple[str, str]] = list(PACK_FORM.items())
        form.extend(("virtualEditions[]", name) for name in virtual)
        body = urlencode(form).encode("ascii")
        status, payload, headers = self._http(
            "POST", url, body, {"Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*"}
        )
        if status != 200 or not payload.startswith(b"PK"):
            raise RuntimeError(f"UUP dump did not return a script package for {build.name}: HTTP {status}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target
