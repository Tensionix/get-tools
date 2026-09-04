"""Affinity: the version 2 apps from Serif and the unified Affinity from Canva.

Two generations, two sources, both without an account:

- Affinity Photo 2, Designer 2 and Publisher 2 keep an updates page each at
  store.serif.com, one per operating system, listing every 2.x release with
  signed links on downloads.affinstatic.com. The signature expires within
  hours, so a build is identified by the link without its query string and
  the page is read again when the link is needed.
- Affinity (by Canva, version 3) is five fixed files on
  downloads.affinity.studio: x64 and ARM64, each as exe and msix, plus the
  macOS dmg. Only the current release is published; the file's Last-Modified
  header is the release date shown next to it.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import PLATFORM_MAC, PLATFORM_WINDOWS, PLATFORM_WINDOWS_ARM, VendorBuild


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
PLATFORMS: tuple[str, ...] = (PLATFORM_WINDOWS, PLATFORM_WINDOWS_ARM, PLATFORM_MAC)
PAGE_TTL_SECONDS = 1800.0

UPDATE_PAGE = "https://store.serif.com/en-us/update/{os}/{slug}/2/"
V2_PRODUCTS: dict[str, str] = {
    "Affinity Photo 2": "photo",
    "Affinity Designer 2": "designer",
    "Affinity Publisher 2": "publisher",
}
V3_PRODUCT = "Affinity 3"
V3_FILES: dict[str, tuple[tuple[str, str], ...]] = {
    PLATFORM_WINDOWS: (
        ("https://downloads.affinity.studio/Affinity%20x64.exe", "exe"),
        ("https://downloads.affinity.studio/Affinity%20x64.msix", "msix"),
    ),
    PLATFORM_WINDOWS_ARM: (
        ("https://downloads.affinity.studio/Affinity%20arm64.exe", "exe"),
        ("https://downloads.affinity.studio/Affinity%20arm64.msix", "msix"),
    ),
    PLATFORM_MAC: (("https://downloads.affinity.studio/Affinity.dmg", "dmg"),),
}
PRODUCT_ORDER: tuple[str, ...] = (V3_PRODUCT, "Affinity Photo 2", "Affinity Designer 2", "Affinity Publisher 2")

# href="https://downloads.affinstatic.com/windows/photo2/2.6.5/affinity-photo-msi-2.6.5.exe?Expires=...">
#     MSIX (x64) – 689.04MB
LINK_PATTERN = re.compile(
    r'href="(?P<url>https://downloads\.affinstatic\.com/[^"]+)"[^>]*>(?P<text>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
SIZE_PATTERN = re.compile(r"([\d.]+\s*[KMG]B)", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")

HttpCall = Callable[[str, str, str | None, dict[str, str]], tuple[int, str]]
HeadCall = Callable[[str], dict[str, str]]


def _default_http(method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
    request = Request(url, data=body.encode("utf-8") if body is not None else None, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=60) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return int(exc.code), ""
    except URLError as exc:
        raise RuntimeError(f"Affinity request failed: {exc.reason}") from exc


def _default_head(url: str) -> dict[str, str]:
    request = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            return {key.lower(): value for key, value in response.headers.items()}
    except (HTTPError, URLError, TimeoutError, OSError):
        return {}


def strip_query(url: str) -> str:
    return url.split("?", 1)[0].replace("&amp;", "&")


def version_key(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(version or ""))]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def variant_of(file_name: str) -> str:
    lowered = file_name.lower()
    if lowered.endswith(".msix"):
        return "msix"
    if lowered.endswith(".exe"):
        return "exe"
    if lowered.endswith(".dmg"):
        return "dmg"
    return lowered.rsplit(".", 1)[-1] if "." in lowered else ""


def platform_of(file_name: str) -> str:
    lowered = file_name.lower()
    if lowered.endswith(".dmg"):
        return PLATFORM_MAC
    return PLATFORM_WINDOWS_ARM if "arm64" in lowered else PLATFORM_WINDOWS


def parse_update_page(html: str, product: str, vendor_id: str = "affinity") -> list[tuple[VendorBuild, str]]:
    """Every installer on a Serif updates page: the build and its currently signed URL."""
    found: list[tuple[VendorBuild, str]] = []
    seen: set[str] = set()
    for match in LINK_PATTERN.finditer(html):
        signed = match.group("url").replace("&amp;", "&")
        plain = strip_query(signed)
        if plain in seen:
            continue
        file_name = plain.rsplit("/", 1)[-1]
        version_match = re.search(r"/(\d+\.\d+(?:\.\d+)*)/", plain)
        if not version_match:
            continue
        seen.add(plain)
        size_match = SIZE_PATTERN.search(TAG_PATTERN.sub(" ", match.group("text")))
        found.append(
            (
                VendorBuild(
                    vendor=vendor_id,
                    product=product,
                    version=version_match.group(1),
                    platform=platform_of(file_name),
                    date=size_match.group(1).replace(" ", "") if size_match else "",
                    download_id=plain,
                    name=file_name,
                    variant=variant_of(file_name),
                ),
                signed,
            )
        )
    return found


def _build_sort_key(build: VendorBuild) -> tuple[tuple[int, ...], int]:
    # Newest first; within a version msix before exe, the order Serif shows them.
    return (version_key(build.version), {"msix": 1, "exe": 0, "dmg": 0}.get(build.variant, 0))


class AffinityProvider:
    id = "affinity"
    name = "Affinity"
    platforms = PLATFORMS

    _instance: "AffinityProvider | None" = None

    def __init__(
        self,
        http: HttpCall | None = None,
        head: HeadCall | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http: HttpCall = http or _default_http
        self._head: HeadCall = head or _default_head
        self._clock = clock
        # page key -> (fetched at, [(build, signed url)])
        self._pages: dict[str, tuple[float, list[tuple[VendorBuild, str]]]] = {}
        self._v3: dict[str, tuple[float, VendorBuild]] = {}

    @classmethod
    def instance(cls) -> "AffinityProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- catalog -----

    def _page(self, product: str, os_name: str, *, force: bool = False) -> list[tuple[VendorBuild, str]]:
        key = f"{os_name}/{V2_PRODUCTS[product]}"
        now = self._clock()
        cached = self._pages.get(key)
        if not force and cached and now - cached[0] < PAGE_TTL_SECONDS:
            return cached[1]
        url = UPDATE_PAGE.format(os=os_name, slug=V2_PRODUCTS[product])
        status, html = self._http("GET", url, None, {"Accept": "text/html"})
        if status != 200:
            raise RuntimeError(f"Serif updates page answered HTTP {status}: {url}")
        entries = parse_update_page(html, product, self.id)
        self._pages[key] = (now, entries)
        return entries

    def _v3_build(self, url: str, variant: str, platform: str) -> VendorBuild:
        now = self._clock()
        cached = self._v3.get(url)
        if cached and now - cached[0] < PAGE_TTL_SECONDS:
            return cached[1]
        headers = self._head(url)
        date = str(headers.get("last-modified") or "")
        # "Wed, 15 Jul 2026 09:00:22 GMT" -> "15 Jul 2026"
        short = re.sub(r"^\w+,\s*", "", date)
        short = re.sub(r"\s+\d\d:\d\d:\d\d.*$", "", short)
        build = VendorBuild(
            vendor=self.id,
            product=V3_PRODUCT,
            version="latest",
            platform=platform,
            date=short,
            download_id=url,
            name=url.rsplit("/", 1)[-1].replace("%20", " "),
            variant=variant,
        )
        self._v3[url] = (now, build)
        return build

    @staticmethod
    def _os_name(platform: str) -> str:
        return "macos" if platform == PLATFORM_MAC else "windows"

    def products(self, platform: str) -> list[str]:
        if platform not in self.platforms:
            return []
        return list(PRODUCT_ORDER)

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection
        if platform not in self.platforms:
            return []
        if product == V3_PRODUCT:
            return [self._v3_build(url, variant, platform) for url, variant in V3_FILES.get(platform, ())]
        if product not in V2_PRODUCTS:
            return []
        entries = self._page(product, self._os_name(platform))
        found = [build for build, _signed in entries if build.platform == platform]
        return sorted(found, key=_build_sort_key, reverse=True)

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        for platform, files in V3_FILES.items():
            for url, variant in files:
                if url == wanted:
                    return self._v3_build(url, variant, platform)
        match = re.search(r"affinstatic\.com/(windows|macos)/(photo|designer|publisher)2/", wanted)
        if not match:
            return None
        product = next((name for name, slug in V2_PRODUCTS.items() if slug == match.group(2)), "")
        if not product:
            return None
        for build, _signed in self._page(product, match.group(1)):
            if build.download_id == wanted:
                return build
        return None

    # ----- links -----

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        del registration  # Affinity never asks for a form
        if build.product == V3_PRODUCT:
            return build.download_id
        os_name = self._os_name(build.platform)
        for attempt in (False, True):
            for candidate, signed in self._page(build.product, os_name, force=attempt):
                if candidate.download_id == build.download_id:
                    return signed
        raise RuntimeError(f"Serif no longer lists {build.name} on the {build.product} updates page.")
