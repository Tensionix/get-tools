"""Vendor providers: builds a vendor publishes itself, every version, not only the latest.

WinGet answers "install the current release". A vendor provider answers the
other question: which versions exist for a product on a platform, and where
the archive for a chosen one is. The GUI lists the versions, the person ticks
some, and the download service fetches them into a per-build folder.

A provider knows nothing about folders, progress or the GUI. It reads the
vendor's catalog, turns entries into `VendorBuild` records, and resolves a
direct download link for one of them. Everything else is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Platform keys are shared by every provider so the form can speak one language.
PLATFORM_WINDOWS = "Windows"
PLATFORM_WINDOWS_ARM = "Windows ARM"
PLATFORM_MAC = "Mac OS X"
PLATFORM_LINUX = "Linux"


@dataclass(frozen=True)
class VendorBuild:
    """One downloadable file: a product at a version for a platform."""

    vendor: str
    product: str
    version: str
    platform: str
    date: str
    download_id: str
    name: str
    beta: int = 0
    variant: str = ""  # installer flavour glued to the version: msix, exe, dmg
    notes: str = ""  # what follows the date: size, marks, remarks
    portable_id: str = ""  # the portable build of the same version, its own download id (equal to download_id when the card is portable-only)

    @property
    def label(self) -> str:
        text = self.version
        if self.beta:
            text += f" beta {self.beta}"
        if self.variant:
            text += f" {self.variant}"
        if self.date:
            text += f" - {self.date}"
        if self.notes:
            text += f" - {self.notes}"
        return text


class RegistrationRequired(RuntimeError):
    """The vendor issues the link only after a form; the caller must supply the fields."""

    def __init__(self, message: str, fields: tuple[str, ...]) -> None:
        super().__init__(message)
        self.fields = fields


class VendorProvider(Protocol):
    id: str
    name: str
    platforms: tuple[str, ...]

    def products(self, platform: str) -> list[str]:
        """Product names that have at least one build for the platform."""

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        """Every build of the product for the platform, newest first.

        `selection` carries a vendor's extra switches from the form, such as
        the chip generation for a driver; a provider ignores what it does not know.
        """

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        """The build behind an id handed out earlier, or None when it is unknown."""

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        """A direct, possibly time-limited download URL for the build.

        A provider whose files are not plain GETs (a form post, say) also
        offers `fetch(build, target) -> Path`; the download service prefers
        it when present and uses `resolve_link` only for the "link" action.
        """


# The order of the tabs, left to right; the first one is the tab the window opens on.
VENDOR_IDS: tuple[str, ...] = ("uupdump", "techpowerup", "nvidia", "blackmagic", "affinity")


def get_provider(vendor_id: str) -> VendorProvider:
    key = str(vendor_id or "").strip().lower()
    if key == "blackmagic":
        from .blackmagic import BlackmagicProvider

        return BlackmagicProvider.instance()
    if key == "affinity":
        from .affinity import AffinityProvider

        return AffinityProvider.instance()
    if key == "nvidia":
        from .nvidia import NvidiaProvider

        return NvidiaProvider.instance()
    if key == "techpowerup":
        from .techpowerup import TechPowerUpProvider

        return TechPowerUpProvider.instance()
    if key == "uupdump":
        from .uupdump import UupDumpProvider

        return UupDumpProvider.instance()
    raise KeyError(f"Unknown vendor provider: {vendor_id}")
