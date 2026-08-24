"""Where a package can be picked up by hand.

The download button takes its file from `winget download`. This module answers
the other half of the question: which page a person should open to choose a
build themselves - a portable archive, an older release, another architecture.

Nothing here talks to WinGet. The caller passes the raw `winget show` text and
gets a URL back, so the resolver stays testable and locale independent: the
output is scanned for links, never for the localized field captions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re


# Pages that beat whatever the WinGet manifest points at: either the vendor
# homepage is a landing page with no builds on it, or TechPowerUp keeps the
# portable archive that the manifest installer does not ship.
PACKAGE_PAGE_OVERRIDES: dict[str, str] = {
    # Hardware utilities - portable first.
    "techpowerup.gpu-z": "https://www.techpowerup.com/download/techpowerup-gpu-z/",
    "techpowerup.nvcleanstall": "https://www.techpowerup.com/download/techpowerup-nvcleanstall/",
    "wagnardsoft.displaydriveruninstaller": "https://www.techpowerup.com/download/display-driver-uninstaller-ddu/",
    "maxon.cinebenchr23": "https://www.techpowerup.com/download/maxon-cinebench/",
    "crystaldewworld.crystaldiskinfo": "https://crystalmark.info/en/download/",
    "crystaldewworld.crystaldiskmark": "https://crystalmark.info/en/download/",
    "cpuid.cpu-z": "https://www.cpuid.com/softwares/cpu-z.html",
    "cpuid.hwmonitor": "https://www.cpuid.com/softwares/hwmonitor.html",
    "realix.hwinfo": "https://www.hwinfo.com/download/",
    "guru3d.afterburner": "https://www.guru3d.com/download/msi-afterburner-beta-download/",
    "geeks3d.furmark.2": "https://geeks3d.com/furmark/downloads/",
    "codesector.teracopy": "https://www.codesector.com/downloads",
    "rufus.rufus": "https://rufus.ie/",
    "ventoy.ventoy": "https://www.ventoy.net/en/download.html",
    "balena.etcher": "https://etcher.balena.io/",
    "antibodysoftware.wiztree": "https://diskanalyzer.com/download",
    "jamsoftware.treesize.free": "https://www.jam-software.com/treesize_free",
    # System and runtimes.
    "rarlab.winrar": "https://www.win-rar.com/download.html",
    "giorgiotani.peazip": "https://peazip.github.io/peazip-64bit.html",
    "microsoft.vcredist.2015+.x86": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2015+.x64": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2005.x86": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2008.x86": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2008.x64": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2010.x86": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2010.x64": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2013.x86": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    "microsoft.vcredist.2013.x64": "https://www.techpowerup.com/download/visual-c-redistributable-runtime-package-all-in-one/",
    # Dev and office.
    "voidtools.everything": "https://www.voidtools.com/downloads/",
    "ghisler.totalcommander": "https://www.ghisler.com/download.htm",
    "farmanager.farmanager": "https://www.farmanager.com/download.php",
    "python.python.3.12": "https://www.python.org/downloads/windows/",
    "python.python.3.13": "https://www.python.org/downloads/windows/",
    "openjs.nodejs": "https://nodejs.org/en/download",
    # WinGet carries exactly one Nerd Font, and an old one; the patcher's own
    # page is where the other ~70 families and the release archives live.
    "devcom.jetbrainsmononerdfont": "https://www.nerdfonts.com/font-downloads",
    "jetbrains.toolbox": "https://www.jetbrains.com/toolbox-app/",
    "jetbrains.intellijidea.community": "https://www.jetbrains.com/idea/download/other.html",
    "jetbrains.pycharm.community": "https://www.jetbrains.com/pycharm/download/other.html",
    "jetbrains.webstorm": "https://www.jetbrains.com/webstorm/download/other.html",
    "thedocumentfoundation.libreoffice": "https://www.libreoffice.org/download/download-libreoffice/",
    "irfanskiljan.irfanview": "https://www.irfanview.com/64bit.htm",
    # Media.
    "videolan.vlc": "https://www.videolan.org/vlc/download-windows.html",
    "cockos.reaper": "https://www.reaper.fm/download.php",
    "faststone.viewer": "https://www.faststone.org/FSViewerDownload.htm",
    "obsproject.obsstudio": "https://obsproject.com/download",
    "bandicamcompany.bandicam": "https://www.bandicam.com/downloads/",
    "bandicamcompany.bandicut": "https://www.bandicam.com/bandicut-video-cutter/",
    "mediaarea.mediainfo": "https://mediaarea.net/en/MediaInfo/Download/Windows",
    # Browsers and network.
    "mozilla.firefox": "https://www.mozilla.org/firefox/all/",
    "mozilla.thunderbird": "https://www.thunderbird.net/thunderbird/all/",
    "opera.opera": "https://www.opera.com/computer",
    "vivaldi.vivaldi": "https://vivaldi.com/download/",
    "centstudio.centbrowser": "https://www.centbrowser.com/history.html",
    "google.chrome": "https://www.google.com/chrome/",
    "zoom.zoom": "https://zoom.us/download",
    "anydesk.anydesk": "https://anydesk.com/en/downloads/windows",
    # PKMS.
    "notion.notion": "https://www.notion.com/desktop",
}


# Packages whose WinGet manifest carries an archive or a single standalone file
# next to the regular installer. Probed with `winget show --installer-type`, so
# the value is the type to pass back to `winget download`, not a guess: `zip`
# unpacks into a portable build, `portable` is one file that needs no unpacking.
# Types like `inno (zip)` are deliberately absent - that is an installer inside
# an archive, which gives a person nothing a normal install would not.
PACKAGE_ARCHIVE_TYPES: dict[str, str] = {
    # System and dev.
    "microsoft.sysinternals.processexplorer": "zip",
    "microsoft.sysinternals.autoruns": "zip",
    "voidtools.everything": "zip",
    "notepad++.notepad++": "zip",
    "rhash.rhash": "zip",
    "gyan.ffmpeg": "zip",
    "aristocratos.btop4win": "zip",
    "openjs.nodejs": "zip",
    "openai.codex": "zip",
    "giorgiotani.peazip": "portable",
    "yt-dlp.yt-dlp": "portable",
    "anthropic.claudecode": "portable",
    "rufus.rufus": "portable",
    # Office and images.
    "sumatrapdf.sumatrapdf": "portable",
    "xnsoft.xnviewmp": "zip",
    # Audio and video.
    "audacity.audacity": "zip",
    "videolan.vlc": "zip",
    "ch.losslesscut": "zip",
    "moritzbunkus.mkvtoolnix": "zip",
    "obsproject.obsstudio": "zip",
    "mediaarea.mediainfo": "zip",
    # Network.
    # rclone ships as one zip with the command-line binary inside; the manifest
    # calls it `portable (zip)`, so the archive button hands out the tool itself.
    "rclone.rclone": "zip",
    "eloston.ungoogled-chromium": "zip",
    "2dust.v2rayn": "zip",
    "telegram.telegramdesktop": "zip",
    "bitwarden.bitwarden": "portable",
    # Hardware.
    "maxon.cinebenchr23": "zip",
    "geeks3d.furmark.2": "zip",
    # fastfetch is portable-only: WinGet carries the zip, there is no installer.
    "fastfetch-cli.fastfetch": "zip",
}


@dataclass(frozen=True)
class GithubBuilds:
    """Where the vendor's own builds live when WinGet does not carry them."""

    repo: str
    installer: str = ""  # regular expression, not a glob: the version moves
    archive: str = ""


# Packages taken from the vendor's release page instead of the WinGet manifest.
# The reason is always the same: the manifest carries only part of what the
# vendor publishes, so `winget download` could never produce the rest. Taking
# both builds from one release also keeps the two buttons on the same version -
# a release usually lands on GitHub before the manifest catches up.
PACKAGE_GITHUB_BUILDS: dict[str, GithubBuilds] = {
    # WinGet knows Tabby only as a setup .exe; the portable build sits in the
    # same release next to it.
    "eugeny.tabby": GithubBuilds(
        repo="Eugeny/tabby",
        installer=r"tabby-[\d.]+-setup-x64\.exe",
        archive=r"tabby-[\d.]+-portable-x64\.zip",
    ),
    # The WinGet manifest carries the msi alone and trails the release, so the
    # portable build is unreachable through `winget download` at any version.
    # One release also holds arm64, Linux, macOS and Android, hence the pinned
    # architecture - and it is spelled differently in the two names: the
    # installer says `x64`, the portable archive says `x86_64`.
    "rclone-manager.rclone-manager": GithubBuilds(
        repo="Zarestia-Dev/rclone-manager",
        installer=r"RClone\.Manager_[\d.]+_x64-setup\.exe",
        archive=r"RClone\.Manager_[\d.]+_x86_64_windows_portable\.zip",
    ),
    # WinGet ships bottom only as the WiX MSI installer; the portable zip lives
    # in the same GitHub release. Installer stays on WinGet, the archive button
    # takes the zip from GitHub. The archive name carries no version.
    "clement.bottom": GithubBuilds(
        repo="ClementTsang/bottom",
        archive=r"bottom_x86_64-pc-windows-msvc\.zip",
    ),
}


def package_archive_type(package_id: str) -> str:
    """`zip`, `portable`, or `''` when this package has no archive build."""
    key = str(package_id or "").strip().lower()
    builds = PACKAGE_GITHUB_BUILDS.get(key)
    if builds and builds.archive:
        return "zip"
    return PACKAGE_ARCHIVE_TYPES.get(key, "")


def package_archive_github(package_id: str) -> tuple[str, str] | None:
    """Repository and asset pattern when the archive comes from GitHub, not WinGet."""
    builds = PACKAGE_GITHUB_BUILDS.get(str(package_id or "").strip().lower())
    if not builds or not builds.archive:
        return None
    return builds.repo, builds.archive


def package_installer_github(package_id: str) -> tuple[str, str] | None:
    """Same, for the installer: the build a person downloads and runs."""
    builds = PACKAGE_GITHUB_BUILDS.get(str(package_id or "").strip().lower())
    if not builds or not builds.installer:
        return None
    return builds.repo, builds.installer


# Everything a release page carries besides the Windows build people want:
# other systems, other architectures, checksums, debug symbols, update deltas.
ASSET_FOREIGN = (
    "linux", "darwin", "macos", "mac-", "osx", "android", "freebsd",
    ".deb", ".rpm", ".pacman", ".appimage", ".dmg", ".pkg", ".tar", ".snap", ".flatpak",
    "arm64", "aarch64", "armv7", "armhf", "riscv",
    ".blockmap", ".sha256", ".sha512", ".sig", ".asc", ".pem", ".json", ".yml", ".yaml",
    "symbols", "debug", "-pdb", "pdbs", "src", "source", "sources",
)
ASSET_INSTALLER_SUFFIXES = (".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle")
ASSET_ARCHIVE_SUFFIXES = (".zip", ".7z")
ASSET_SIXTY_FOUR = ("x64", "win64", "amd64", "x86_64", "64bit", "64-bit")
ASSET_THIRTY_TWO = ("x86", "win32", "ia32", "32bit", "32-bit")
ASSET_PORTABLE = ("portable", "standalone", "-bin", "nosetup", "no-install")


def _asset_rank(name: str, kind: str) -> tuple[int, int, int] | None:
    """How well an asset name fits, or `None` when it does not fit at all."""
    lowered = name.lower()
    if any(mark in lowered for mark in ASSET_FOREIGN):
        return None
    suffixes = ASSET_INSTALLER_SUFFIXES if kind == "installer" else ASSET_ARCHIVE_SUFFIXES
    if not lowered.endswith(suffixes):
        return None
    if any(mark in lowered for mark in ASSET_THIRTY_TWO) and not any(
        mark in lowered for mark in ASSET_SIXTY_FOUR
    ):
        return None
    # Ranked, best first: an explicit 64-bit build; for archives also the one
    # that says it needs no installation; and a shorter name, which on a release
    # page usually means the plain build rather than a special edition.
    sixty_four = 1 if any(mark in lowered for mark in ASSET_SIXTY_FOUR) else 0
    portable = 1 if (kind == "archive" and any(mark in lowered for mark in ASSET_PORTABLE)) else 0
    return (portable, sixty_four, -len(lowered))


def pick_windows_asset(names: Iterable[str], kind: str) -> str:
    """The Windows build a person means by "installer" or "archive", or `''`.

    A release page is a mixed bag: several systems, several architectures,
    checksums and symbol bundles. This picks the one file that answers the
    question the button asks, and returns nothing rather than a wrong guess.
    """
    best_name = ""
    best_rank: tuple[int, int, int] | None = None
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        rank = _asset_rank(name, kind)
        if rank is None:
            continue
        if best_rank is None or rank > best_rank:
            best_name, best_rank = name, rank
    return best_name


def github_repo_from_url(url: str) -> str:
    """`owner/repo` out of any github.com link, or `''`."""
    match = GITHUB_PATTERN.match(str(url or "").strip())
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2)
    if owner.lower() in GITHUB_RESERVED_OWNERS:
        return ""
    return f"{owner}/{repo}"


# Only what Windows refuses in a folder name. The product's own spelling stays:
# `MPC-BE` keeps its hyphen, `Notepad++` its pluses, `MSVC All-in-One
# (TechPowerUp)` its brackets. What must not appear is the machine name -
# `Google.Chrome`, `_archives`, `Microsoft_PowerShell`.
FOLDER_NAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
FOLDER_NAME_SPACES = re.compile(r"\s+")


def download_folder_name(display_name: str, fallback: str = "") -> str:
    """The folder a person recognizes: the product name as its vendor writes it."""
    text = FOLDER_NAME_FORBIDDEN.sub(" ", str(display_name or ""))
    text = FOLDER_NAME_SPACES.sub(" ", text).strip(" .")
    if text:
        return text
    return download_folder_name(fallback) if fallback else ""


# Links that answer a different question than "where do I get the build".
PAGE_URL_NOISE = (
    "license",
    "licence",
    "eula",
    "privacy",
    "terms",
    "support",
    "/faq",
    "faq.",
    "changelog",
    "release-notes",
    "/wiki",
    "/issues",
    "/docs",
    "documentation",
    "twitter.com",
    "x.com/",
    "facebook.com",
    "youtube.com",
)

DOWNLOAD_PATH_HINTS = ("download", "/dl/", "/get", "getting-started")

URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+")
GITHUB_PATTERN = re.compile(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)", re.IGNORECASE)
GITHUB_RESERVED_OWNERS = {"orgs", "sponsors", "features", "topics", "marketplace", "apps", "settings"}


@dataclass(frozen=True)
class PackagePage:
    url: str
    origin: str  # "override" | "github" | "vendor"


def _clean_url(raw: str) -> str:
    url = str(raw or "").strip().strip(",;")
    while url and url[-1] in ".)]}>":
        url = url[:-1]
    return url


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for match in URL_PATTERN.findall(str(text or "")):
        url = _clean_url(match)
        if url and url not in seen:
            seen.append(url)
    return seen


def github_releases_page(url: str) -> str:
    """Turn any github.com link into that repository's latest-release page."""
    match = GITHUB_PATTERN.match(_clean_url(url))
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2)
    if owner.lower() in GITHUB_RESERVED_OWNERS:
        return ""
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return ""
    return f"https://github.com/{owner}/{repo}/releases/latest"


def _is_noise(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in PAGE_URL_NOISE)


def _vendor_page(urls: list[str]) -> str:
    candidates = [url for url in urls if not _is_noise(url)]
    if not candidates:
        candidates = list(urls)
    for url in candidates:
        if any(hint in url.lower() for hint in DOWNLOAD_PATH_HINTS):
            return url
    return candidates[0] if candidates else ""


def resolve_package_page(package_id: str, show_output: str = "") -> PackagePage | None:
    """Pick the page a person should open to download this package by hand."""
    key = str(package_id or "").strip().lower()
    if not key:
        return None
    override = PACKAGE_PAGE_OVERRIDES.get(key)
    if override:
        return PackagePage(override, "override")

    urls = extract_urls(show_output)
    for url in urls:
        releases = github_releases_page(url)
        if releases:
            return PackagePage(releases, "github")

    vendor = _vendor_page(urls)
    if vendor:
        return PackagePage(vendor, "vendor")
    return None
