"""Vendor providers: the Blackmagic catalog, the Affinity pages, link resolution, the resumable download."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
from pathlib import Path
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_core.core.jobs import JobContext  # noqa: E402
from system_core.core.manifest import Operation  # noqa: E402
from system_core.core.paths import get_project_paths  # noqa: E402
from system_core.services import vendor_service  # noqa: E402
from system_core.vendors import RegistrationRequired, VendorBuild, get_provider  # noqa: E402
from system_core.vendors.affinity import AffinityProvider, parse_update_page  # noqa: E402
from system_core.vendors.nvidia import DriverMarks, NvidiaProvider  # noqa: E402
from system_core.vendors.uupdump import UupDumpProvider, classify_title  # noqa: E402
from system_core.vendors.blackmagic import (  # noqa: E402
    BlackmagicProvider,
    REGISTRATION_FIELDS,
    catalog_builds,
    order_products,
    parse_build_name,
    registration_body,
    version_key,
)


CATALOG = {
    "downloads": [
        {"name": "DaVinci Resolve Studio 21.0.4 Update", "date": "05 Aug 2026",
         "urls": {"Windows": [{"downloadId": "studio-2104-win"}], "Linux": [{"downloadId": "studio-2104-lin"}]}},
        {"name": "DaVinci Resolve 21.0.4 Update", "date": "05 Aug 2026",
         "urls": {"Windows": [{"downloadId": "free-2104-win"}]}},
        {"name": "DaVinci Resolve Studio 21", "date": "03 Jun 2026",
         "urls": {"Windows": [{"downloadId": "studio-21-win"}]}},
        {"name": "DaVinci Resolve Studio 21 Public Beta 3", "date": "12 May 2026",
         "urls": {"Windows": [{"downloadId": "studio-21b3-win"}]}},
        {"name": "DaVinci Resolve Studio 20.3.2", "date": "12 Feb 2026",
         "urls": {"Windows": [{"downloadId": "studio-2032-win"}]}},
        {"name": "Blackmagic RAW 5.1", "date": "04 Nov 2025",
         "urls": {"Windows": [{"downloadId": "braw-51-win"}]}},
        {"name": "ATEM Switchers 9.6.2", "date": "01 Jan 2026",
         "urls": {"Windows": [{"downloadId": "atem-962-win"}]}},
        {"name": "Something without a version", "date": "", "urls": {"Windows": [{"downloadId": "x"}]}},
        {"name": "DaVinci Resolve Project Server 21", "date": "03 Jun 2026", "urls": {"Mac OS X": []}},
    ]
}


class FakeBlackmagicHttp:
    """Answers like the Blackmagic API: catalog on GET, link or 403 on POST."""

    def __init__(self, free_ids: set[str] | None = None) -> None:
        self.free_ids = free_ids or set()
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(self, method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append((method, url, body))
        if method == "GET":
            return 200, json.dumps(CATALOG)
        download_id = url.rsplit("/", 1)[-1]
        payload = json.loads(body or "{}")
        if download_id in self.free_ids and "firstname" not in payload:
            return 403, "Error: Must register to be able to perform the download"
        return 200, f"https://sw.blackmagicdesign.com/x/{download_id}.zip?Expires=1"


def blackmagic_with(http: FakeBlackmagicHttp) -> BlackmagicProvider:
    return BlackmagicProvider(http=http)


SERIF_PAGE = """
<a href="https://downloads.affinstatic.com/windows/photo2/2.6.5/affinity-photo-2.6.5.msix?Expires=1&amp;Signature=SIG1&amp;Key-Pair-Id=K" class="x"> MSIX (x64) – 788.66MB </a>
<a href="https://downloads.affinstatic.com/windows/photo2/2.6.5/affinity-photo-msi-2.6.5.exe?Expires=1&amp;Signature=SIG2&amp;Key-Pair-Id=K" class="x"> MSI/EXE (x64) – 760.23MB </a>
<a href="https://downloads.affinstatic.com/windows/photo2/2.6.5/affinity-photo-arm64-2.6.5.msix?Expires=1&amp;Signature=SIG3&amp;Key-Pair-Id=K" class="x"> MSIX (ARM64) – 784.94MB </a>
<a href="https://downloads.affinstatic.com/windows/photo2/2.6.4/affinity-photo-2.6.4.msix?Expires=1&amp;Signature=SIG4&amp;Key-Pair-Id=K" class="x"> MSIX (x64) – 787.75MB </a>
<a href="https://downloads.affinstatic.com/windows/photo2/2.6.4/affinity-photo-2.6.4.msix?Expires=1&amp;Signature=SIG4dup&amp;Key-Pair-Id=K" class="x"> MSIX (x64) – 787.75MB </a>
<a href="https://store.serif.com/en-us/faq/" class="x"> FAQ </a>
"""

SERIF_MAC_PAGE = """
<a href="https://downloads.affinstatic.com/macos/photo2/2.6.5/affinity-photo-2.6.5.dmg?Expires=1&amp;Signature=MAC1&amp;Key-Pair-Id=K"> 900.00MB </a>
"""


class FakeAffinityHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.signature = "SIG"

    def __call__(self, method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append(url)
        page = SERIF_MAC_PAGE if "/macos/" in url else SERIF_PAGE
        return 200, page.replace("SIG", self.signature)


def fake_head(url: str) -> dict[str, str]:
    return {"last-modified": "Wed, 15 Jul 2026 09:00:22 GMT", "content-length": "662137928"}


def affinity_with(http: FakeAffinityHttp) -> AffinityProvider:
    return AffinityProvider(http=http, head=fake_head)


class NameParsingTests(unittest.TestCase):
    def test_update_suffix_and_beta_are_recognised(self) -> None:
        parsed = parse_build_name("DaVinci Resolve Studio 21.0.4 Update")
        assert parsed is not None
        self.assertEqual((parsed.product, parsed.version, parsed.update, parsed.beta), ("DaVinci Resolve Studio", "21.0.4", True, 0))
        beta = parse_build_name("Fusion 21 Public Beta 2")
        assert beta is not None
        self.assertEqual((beta.product, beta.version, beta.beta), ("Fusion", "21", 2))

    def test_names_without_a_version_are_skipped(self) -> None:
        self.assertIsNone(parse_build_name("Something without a version"))
        self.assertIsNone(parse_build_name(""))

    def test_version_key_pads_and_orders(self) -> None:
        self.assertGreater(version_key("21"), version_key("20.3.2"))
        self.assertGreater(version_key("21.0.4"), version_key("21"))


class BlackmagicCatalogTests(unittest.TestCase):
    def test_builds_come_per_platform_with_ids(self) -> None:
        builds = catalog_builds(CATALOG)
        ids = {build.download_id for build in builds}
        self.assertIn("studio-2104-win", ids)
        self.assertIn("studio-2104-lin", ids)
        self.assertNotIn("x", ids)
        self.assertFalse(any(build.product == "DaVinci Resolve Project Server" for build in builds))

    def test_products_are_ordered_preferred_first(self) -> None:
        provider = blackmagic_with(FakeBlackmagicHttp())
        products = provider.products("Windows")
        self.assertEqual(products[:3], ["DaVinci Resolve Studio", "DaVinci Resolve", "Blackmagic RAW"])
        self.assertEqual(products[-1], "ATEM Switchers")
        self.assertEqual(order_products(["Zed", "Alpha", "Fusion"]), ["Fusion", "Alpha", "Zed"])

    def test_versions_newest_first_with_beta_below_final(self) -> None:
        provider = blackmagic_with(FakeBlackmagicHttp())
        versions = [build.download_id for build in provider.builds("DaVinci Resolve Studio", "Windows")]
        self.assertEqual(versions, ["studio-2104-win", "studio-21-win", "studio-21b3-win", "studio-2032-win"])
        self.assertEqual(provider.builds("DaVinci Resolve Studio", "Linux")[0].download_id, "studio-2104-lin")
        self.assertEqual(provider.builds("Fusion", "Windows"), [])

    def test_catalog_is_fetched_once_within_ttl(self) -> None:
        http = FakeBlackmagicHttp()
        provider = blackmagic_with(http)
        provider.products("Windows")
        provider.builds("Blackmagic RAW", "Windows")
        self.assertEqual(sum(1 for call in http.calls if call[0] == "GET"), 1)

    def test_label_carries_version_beta_and_date(self) -> None:
        provider = blackmagic_with(FakeBlackmagicHttp())
        beta = provider.build_by_id("studio-21b3-win")
        assert beta is not None
        self.assertEqual(beta.label, "21 beta 3 - 12 May 2026")


class BlackmagicLinkTests(unittest.TestCase):
    def test_studio_link_needs_only_the_country(self) -> None:
        http = FakeBlackmagicHttp(free_ids={"free-2104-win"})
        provider = blackmagic_with(http)
        build = provider.build_by_id("studio-2104-win")
        assert build is not None
        url = provider.resolve_link(build)
        self.assertTrue(url.startswith("https://sw.blackmagicdesign.com/"))
        method, _url, body = http.calls[-1]
        self.assertEqual((method, json.loads(body or "{}")), ("POST", {"country": "us"}))

    def test_free_resolve_asks_for_the_form(self) -> None:
        provider = blackmagic_with(FakeBlackmagicHttp(free_ids={"free-2104-win"}))
        build = provider.build_by_id("free-2104-win")
        assert build is not None
        with self.assertRaises(RegistrationRequired) as caught:
            provider.resolve_link(build)
        self.assertEqual(caught.exception.fields, REGISTRATION_FIELDS)
        with self.assertRaises(RegistrationRequired):
            provider.resolve_link(build, {"firstname": "A"})  # half a form is no form

    def test_filled_form_is_sent_and_unlocks_the_link(self) -> None:
        http = FakeBlackmagicHttp(free_ids={"free-2104-win"})
        provider = blackmagic_with(http)
        build = provider.build_by_id("free-2104-win")
        assert build is not None
        form = {key: f"v-{key}" for key in REGISTRATION_FIELDS}
        url = provider.resolve_link(build, form)
        self.assertIn("free-2104-win", url)
        sent = json.loads(http.calls[-1][2] or "{}")
        self.assertEqual(sent["platform"], "Windows")
        self.assertTrue(sent["policy"])
        self.assertEqual(sent["email"], "v-email")
        self.assertEqual(registration_body("Linux", form)["platform"], "Linux")

    def test_registry_hands_out_singletons(self) -> None:
        self.assertIs(get_provider("blackmagic"), get_provider("Blackmagic"))
        self.assertIs(get_provider("affinity"), get_provider("affinity"))
        with self.assertRaises(KeyError):
            get_provider("nobody")


class AffinityTests(unittest.TestCase):
    def test_serif_page_is_parsed_into_builds(self) -> None:
        entries = parse_update_page(SERIF_PAGE, "Affinity Photo 2")
        self.assertEqual(len(entries), 4)  # the duplicate link and the FAQ link are dropped
        build, signed = entries[0]
        self.assertEqual((build.version, build.variant, build.platform, build.date), ("2.6.5", "msix", "Windows", "788.66MB"))
        self.assertEqual(build.download_id, "https://downloads.affinstatic.com/windows/photo2/2.6.5/affinity-photo-2.6.5.msix")
        self.assertIn("Signature=SIG1", signed)
        self.assertEqual(entries[2][0].platform, "Windows ARM")

    def test_v2_builds_follow_platform_and_order(self) -> None:
        provider = affinity_with(FakeAffinityHttp())
        windows = provider.builds("Affinity Photo 2", "Windows")
        self.assertEqual([build.label for build in windows], ["2.6.5 msix - 788.66MB", "2.6.5 exe - 760.23MB", "2.6.4 msix - 787.75MB"])
        arm = provider.builds("Affinity Photo 2", "Windows ARM")
        self.assertEqual([build.variant for build in arm], ["msix"])
        mac = provider.builds("Affinity Photo 2", "Mac OS X")
        self.assertEqual([(build.variant, build.platform) for build in mac], [("dmg", "Mac OS X")])
        self.assertEqual(provider.builds("Affinity Photo 2", "Linux"), [])

    def test_v3_builds_are_fixed_files_with_the_header_date(self) -> None:
        provider = affinity_with(FakeAffinityHttp())
        builds = provider.builds("Affinity 3", "Windows")
        self.assertEqual([build.label for build in builds], ["latest exe - 15 Jul 2026", "latest msix - 15 Jul 2026"])
        self.assertEqual(builds[0].name, "Affinity x64.exe")
        self.assertEqual(provider.products("Windows")[0], "Affinity 3")
        mac = provider.builds("Affinity 3", "Mac OS X")
        self.assertEqual(mac[0].download_id, "https://downloads.affinity.studio/Affinity.dmg")

    def test_link_is_resigned_from_a_fresh_page(self) -> None:
        http = FakeAffinityHttp()
        provider = affinity_with(http)
        build = provider.builds("Affinity Photo 2", "Windows")[0]
        self.assertIn("Signature=SIG1", provider.resolve_link(build))
        self.assertEqual(provider.resolve_link(provider.builds("Affinity 3", "Windows")[0]), "https://downloads.affinity.studio/Affinity%20x64.exe")

    def test_build_by_id_finds_both_generations(self) -> None:
        provider = affinity_with(FakeAffinityHttp())
        v2 = provider.build_by_id("https://downloads.affinstatic.com/windows/photo2/2.6.4/affinity-photo-2.6.4.msix")
        assert v2 is not None
        self.assertEqual(v2.version, "2.6.4")
        v3 = provider.build_by_id("https://downloads.affinity.studio/Affinity%20arm64.msix")
        assert v3 is not None
        self.assertEqual((v3.platform, v3.variant), ("Windows ARM", "msix"))
        self.assertIsNone(provider.build_by_id("https://example.com/nothing.zip"))


class OptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (BlackmagicProvider._instance, AffinityProvider._instance)
        BlackmagicProvider._instance = blackmagic_with(FakeBlackmagicHttp(free_ids={"free-2104-win"}))
        AffinityProvider._instance = affinity_with(FakeAffinityHttp())

    def tearDown(self) -> None:
        BlackmagicProvider._instance, AffinityProvider._instance = self._saved

    def test_build_options_for_one_product(self) -> None:
        options = vendor_service.vendor_build_options(None, {"vendor": "blackmagic", "bmd_platform": "Windows", "bmd_product": "DaVinci Resolve Studio"})
        self.assertEqual(options[0]["value"], "studio-2104-win")
        self.assertEqual(options[0]["label"], "21.0.4 - 05 Aug 2026 - latest")
        self.assertEqual(options[1]["label"], "21 - 03 Jun 2026")
        self.assertNotIn("latest", options[2]["label"])  # the beta is never "latest"
        nothing = vendor_service.vendor_build_options(None, {"vendor": "blackmagic", "bmd_platform": "Mac OS X", "bmd_product": "Fusion"})
        self.assertEqual(nothing[0]["value"], "")

    def test_whole_catalog_lists_every_product_with_its_name(self) -> None:
        options = vendor_service.vendor_build_options(None, {"vendor": "blackmagic", "bmd_platform": "Windows", "bmd_product": "*"})
        labels = [item["label"] for item in options]
        self.assertEqual(labels[0], "DaVinci Resolve Studio 21.0.4 - 05 Aug 2026")
        self.assertIn("ATEM Switchers 9.6.2 - 01 Jan 2026", labels)
        self.assertEqual(len(options), 7)
        linux = vendor_service.vendor_build_options(None, {"vendor": "blackmagic", "bmd_platform": "Linux"})
        self.assertEqual([item["value"] for item in linux], ["studio-2104-lin"])

    def test_vendor_switch_reads_the_vendors_own_fields(self) -> None:
        options = vendor_service.vendor_build_options(None, {"vendor": "affinity", "aff_platform": "Windows ARM", "aff_product": "Affinity 3", "bmd_platform": "Linux"})
        self.assertEqual([item["label"] for item in options], ["latest exe - 15 Jul 2026", "latest msix - 15 Jul 2026"])
        everything = vendor_service.vendor_build_options(None, {"vendor": "affinity", "aff_platform": "Windows", "aff_product": "*"})
        self.assertEqual(everything[0]["label"], "Affinity 3 latest exe - 15 Jul 2026")
        self.assertIn("Affinity Photo 2 2.6.5 msix - 788.66MB", [item["label"] for item in everything])


NVIDIA_DRIVERS = {
    "Success": 3,
    "IDS": [
        {"downloadInfo": {"Version": "616.56", "ReleaseDateTime": "Wed Aug 26, 2026", "Name": "GeForce%20Game%20Ready%20Driver",
                          "DownloadURLFileSize": "984.3 MB", "DownloadURL": "https://us.download.nvidia.com/Windows/616.56/616.56-desktop-win10-win11-64bit-international-dch-whql.exe"}},
        {"downloadInfo": {"Version": "581.57", "ReleaseDateTime": "Tue Oct 14, 2025", "Name": "GeForce%20Game%20Ready%20Driver",
                          "DownloadURLFileSize": "900.0 MB", "DownloadURL": "https://us.download.nvidia.com/Windows/581.57/581.57-desktop-win10-win11-64bit-international-dch-whql.exe"}},
        {"downloadInfo": {"Version": "566.36", "ReleaseDateTime": "Tue Dec 03, 2024", "Name": "GeForce%20Security%20Update%20Driver",
                          "DownloadURLFileSize": "700.0 MB", "DownloadURL": "https://us.download.nvidia.com/Windows/566.36/566.36-desktop-win10-win11-64bit-international-dch-whql.exe"}},
        {"downloadInfo": {"Version": "460.79", "ReleaseDateTime": "Wed Dec 09, 2020", "Name": "GeForce%20Game%20Ready%20Driver",
                          "DownloadURLFileSize": "600.0 MB", "DownloadURL": "https://us.download.nvidia.com/Windows/460.79/460.79-desktop-win10-64bit-international-dch-whql.exe"}},
    ],
}
NVIDIA_STUDIO = {
    "Success": 1,
    "IDS": [
        {"downloadInfo": {"Version": "616.56", "ReleaseDateTime": "Wed Aug 26, 2026", "Name": "NVIDIA%20Studio%20Driver",
                          "DownloadURLFileSize": "984.3 MB", "DownloadURL": "https://us.download.nvidia.com/Windows/616.56/616.56-desktop-win10-win11-64bit-international-nsd-dch-whql.exe"}},
    ],
}
NVIDIA_APP_PAGE = '<a href="https://us.download.nvidia.com/nvapp/client/11.0.8.299/NVIDIA_app_v11.0.8.299.exe">Download</a>'
CUDA_ARCHIVE_PAGE = """
<a href="/cuda-13-3-0-download-archive">CUDA Toolkit 13.3.0</a>
<a href="/cuda-13-4-0-download-archive">CUDA Toolkit 13.4.0 Developer Preview</a>
<a href="/cuda-12-8-1-download-archive">CUDA Toolkit 12.8.1</a>
"""
CUDA_VERSION_PAGE = 'x "https://developer.download.nvidia.com/compute/cuda/13.3.0/network_installers/cuda_13.3.0_windows_network.exe" y "https://developer.download.nvidia.com/compute/cuda/13.3.0/local_installers/cuda_13.3.0_windows.exe"'
TPU_DDU_PAGE = """
<div class="version "><h3 class="title"> Display Driver Uninstaller (DDU) 18.1.5.7 </h3><span class="date">August 19th, 2026</span>
<ul class="files">
<li class="file clearfix expanded"><div class="filesize">1.7 MB</div><h4 class="title">Installer</h4><div class="filename" title="File Name">DDU-v18.1.5.7_setup.exe</div>
<div class="hash-name">SHA256:</div><div class="hash-value">27E2644515ED30C7D7D64425869D55545F2EABDD2366E9444841F5CE67C86AEF</div>
<form><input type="hidden" name="id" value="3218" /></form></li>
<li class="file clearfix compact"><div class="filesize">1.2 MB</div><h4 class="title">Portable</h4><div class="filename" title="File Name">DDU v18.1.5.7.exe</div>
<form><input type="hidden" name="id" value="3219" /></form></li>
</ul></div>
<div class="version hidden"><h3 class="title"> Display Driver Uninstaller (DDU) 18.1.5.6 </h3><span class="date">July 1st, 2026</span>
<ul class="files">
<li class="file clearfix compact"><div class="filesize">1.7 MB</div><h4 class="title">Installer</h4><div class="filename" title="File Name">DDU-v18.1.5.6_setup.exe</div>
<form><input type="hidden" name="id" value="3204" /></form></li>
</ul></div>
"""


class FakeNvidiaHttp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
        self.calls.append(url)
        if "AjaxDriverService" in url:
            return 200, json.dumps(NVIDIA_STUDIO if "upCRD=1" in url else NVIDIA_DRIVERS)
        if "nvidia-app" in url:
            return 200, NVIDIA_APP_PAGE
        if url.endswith("cuda-toolkit-archive"):
            return 200, CUDA_ARCHIVE_PAGE
        if "download-archive" in url:
            return 200, CUDA_VERSION_PAGE
        return 404, ""


def nvidia_with(http: FakeNvidiaHttp, marks: Path | None) -> "NvidiaProvider":
    from system_core.services.techpowerup_service import parse_versions

    # No device of the machine the tests run on: what a PC with an NVIDIA card changes has a test of its own.
    return NvidiaProvider(
        devices=lambda: [],
        http=http,
        tpu_list=lambda slug: parse_versions(TPU_DDU_PAGE),
        tpu_resolve=lambda slug, file_id: f"https://mirror.techpowerup.com/{slug}/{file_id}.exe?signed",
        marks_path=marks,
    )


class NvidiaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.marks = Path(self.temp.name) / "vendor_nvidia.yaml"
        self.marks.write_text(
            'golden:\n  - "566.36"\n  - "581.57"\nnvenc_sdk:\n  "13.1": "610.0"\n  "13.0": "570.0"\n  "12.2": "551.76"\n'
            'cuda_min_driver:\n  "13.0": "580.88"\n  "12.8": "570.65"\n  "12.6": "560.76"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_driver_marks_from_config(self) -> None:
        marks = DriverMarks.load(self.marks)
        self.assertEqual(marks.notes("616.56", "GeForce Game Ready Driver"), ["NVENC SDK 13.1", "CUDA <= 13.0", "★ golden"] if "616.56" in marks.golden else ["NVENC SDK 13.1", "CUDA <= 13.0"])
        self.assertEqual(marks.notes("581.57", "x"), ["NVENC SDK 13.0", "CUDA <= 13.0", "★ golden"])
        self.assertEqual(marks.notes("566.36", "GeForce Security Update Driver"), ["security update", "NVENC SDK 12.2", "CUDA <= 12.6", "★ golden"])
        self.assertEqual(marks.notes("460.79", "x"), [])
        self.assertEqual(DriverMarks.load(None).notes("616.56", "x"), [])

    def test_driver_builds_carry_marks_and_sort(self) -> None:
        http = FakeNvidiaHttp()
        provider = nvidia_with(http, self.marks)
        builds = provider.builds("Game Ready Driver", "Windows", series="RTX 40", form="desktop")
        self.assertEqual([build.version for build in builds], ["616.56", "581.57", "566.36", "460.79"])
        self.assertEqual(builds[1].label, "581.57 - 14 Oct 2025 - 900.0 MB - NVENC SDK 13.0 - CUDA <= 13.0 - ★ golden")
        self.assertIn("psid=127", http.calls[-1])
        self.assertIn("isWHQL=1", http.calls[-1])
        studio = provider.builds("Studio Driver", "Windows", series="RTX 40", form="notebook")
        self.assertEqual(studio[0].name, "616.56-desktop-win10-win11-64bit-international-nsd-dch-whql.exe")
        self.assertIn("psid=129", http.calls[-1])
        self.assertIn("isWHQL=0&dltype=-1&dch=1&upCRD=1", http.calls[-1])
        self.assertEqual(provider.builds("Game Ready Driver", "Windows", series="Voodoo"), [])

    def test_app_cuda_and_techpowerup_builds(self) -> None:
        provider = nvidia_with(FakeNvidiaHttp(), self.marks)
        app = provider.builds("NVIDIA App", "Windows")
        self.assertEqual((app[0].version, app[0].name), ("11.0.8.299", "NVIDIA_app_v11.0.8.299.exe"))
        cuda = provider.builds("CUDA Toolkit", "Windows")
        self.assertEqual([build.version for build in cuda], ["13.3.0", "13.4.0", "12.8.1"])
        self.assertEqual(cuda[1].notes, "Developer Preview")
        self.assertEqual(cuda[1].label, "13.4.0 - Developer Preview")
        dlss = provider.builds("DLSS DLL", "Windows")
        self.assertEqual([build.label for build in dlss], [
            "18.1.5.7 - August 19th, 2026 - installer - 1.7 MB",
            "18.1.5.7 - August 19th, 2026 - portable - 1.2 MB",
            "18.1.5.6 - July 1st, 2026 - installer - 1.7 MB",
        ])
        self.assertEqual(dlss[1].download_id, "tpu:nvidia-dlss-dll:3219")
        self.assertNotIn("Display Driver Uninstaller", provider.products("Windows"))

    def test_links_and_ids(self) -> None:
        provider = nvidia_with(FakeNvidiaHttp(), self.marks)
        cuda = provider.builds("CUDA Toolkit", "Windows")[0]
        self.assertEqual(provider.resolve_link(cuda), "https://developer.download.nvidia.com/compute/cuda/13.3.0/local_installers/cuda_13.3.0_windows.exe")
        dlss = provider.builds("DLSS DLL", "Windows")[1]
        self.assertEqual(provider.resolve_link(dlss), "https://mirror.techpowerup.com/nvidia-dlss-dll/3219.exe?signed")
        fresh = nvidia_with(FakeNvidiaHttp(), self.marks)  # nothing listed yet: ids are decoded on their own
        driver = fresh.build_by_id("https://us.download.nvidia.com/Windows/581.57/581.57-desktop-win10-win11-64bit-international-nsd-dch-whql.exe")
        assert driver is not None
        self.assertEqual((driver.product, driver.version), ("Studio Driver", "581.57"))
        self.assertEqual(fresh.resolve_link(driver), driver.download_id)
        tpu = fresh.build_by_id("tpu:nvidia-dlss-dll:3204")
        assert tpu is not None
        self.assertEqual(tpu.name, "DDU-v18.1.5.6_setup.exe")
        self.assertIsNone(fresh.build_by_id("https://example.com/x.zip"))

    def test_my_cards_is_the_intersection_of_the_generations(self) -> None:
        self.marks.write_text('my_generations:\n  - "GTX 16"\n  - "RTX 50"\n  - "Voodoo"\n', encoding="utf-8")

        class PerSeriesHttp(FakeNvidiaHttp):
            def __call__(self, method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
                if "AjaxDriverService" in url and "psid=131" in url:  # RTX 50 desktop: only the two newest
                    return 200, json.dumps({"IDS": NVIDIA_DRIVERS["IDS"][:2]})
                return super().__call__(method, url, body, headers)

        provider = nvidia_with(PerSeriesHttp(), self.marks)
        mine = provider.builds("Game Ready Driver", "Windows", series="*", form="desktop")
        self.assertEqual([build.version for build in mine], ["616.56", "581.57"])
        self.assertEqual(DriverMarks.load(self.marks).mine, ("GTX 16", "RTX 50"))
        self.marks.write_text("golden: []\n", encoding="utf-8")
        self.assertEqual(nvidia_with(FakeNvidiaHttp(), self.marks).builds("Game Ready Driver", "Windows", series="*"), [])

    def test_service_hands_series_and_form_to_the_provider(self) -> None:
        saved = NvidiaProvider._instance
        NvidiaProvider._instance = nvidia_with(FakeNvidiaHttp(), self.marks)
        try:
            options = vendor_service.vendor_build_options(None, {"vendor": "nvidia", "nv_product": "Game Ready Driver", "nv_series": "GTX 10", "nv_form": "notebook"})
            # Over the cards - the line about the card of this PC (none here).
            self.assertTrue(options[0]["note"] and "no NVIDIA card on this PC" in options[0]["label"])
            options = options[1:]
            self.assertEqual(options[0]["value"].split("/")[4], "616.56")
            self.assertIn("★ golden", options[1]["label"])
            tools = vendor_service.vendor_build_options(None, {"vendor": "nvidia", "nv_product": "DLSS DLL"})
            self.assertTrue(tools[0]["value"].startswith("tpu:nvidia-dlss-dll:"))
        finally:
            NvidiaProvider._instance = saved


UUP_CATALOG = {
    "response": {
        "builds": {
            "1": {"title": "Windows 11, version 24H2 (26100.9278)", "build": "26100.9278", "arch": "amd64", "created": 1787800000, "uuid": "aaa"},
            "2": {"title": "Windows 11, version 24H2 (26100.9278)", "build": "26100.9278", "arch": "arm64", "created": 1787800000, "uuid": "aaa-arm"},
            "3": {"title": "Feature update to Windows 10, version 22H2 (19045.6400)", "build": "19045.6400", "arch": "amd64", "created": 1787000000, "uuid": "bbb"},
            "4": {"title": "Windows 11 Insider Preview Feature Update (28020.2818)", "build": "28020.2818", "arch": "amd64", "created": 1787900000, "uuid": "ccc"},
            "5": {"title": "Windows 11 Insider Preview 10.0.28120.2824 (rs_prerelease)", "build": "28120.2824", "arch": "amd64", "created": 1787950000, "uuid": "ddd"},
            "6": {"title": "Cumulative Update for Windows 11 (26100.9278)", "build": "26100.9278", "arch": "amd64", "created": 1787800000, "uuid": "eee"},
            "7": {"title": "Windows 11, version 23H2 (22631.7517)", "build": "22631.7517", "arch": "amd64", "created": 1786500000, "uuid": "fff"},
        }
    }
}


class FakeUupHttp:
    def __init__(self) -> None:
        self.posts: list[tuple[str, bytes]] = []

    def __call__(self, method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
        if method == "GET":
            return 200, json.dumps(UUP_CATALOG).encode("utf-8"), {}
        self.posts.append((url, body or b""))
        # a real (tiny) package: the Windows download always unpacks it
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("uup_download_windows.cmd", "@echo off\r\n")
            bundle.writestr("ConvertConfig.ini", "[convert-UUP]\r\nAutoExit     =0\r\n")
        return 200, buffer.getvalue(), {"content-type": "archive/zip"}


class UupDumpTests(unittest.TestCase):
    def test_titles_are_classified_and_the_rest_skipped(self) -> None:
        self.assertEqual(classify_title("Windows 11, version 24H2 (26100.9278)"), ("Windows 11", "24H2", "26100.9278"))
        self.assertEqual(classify_title("Feature update to Windows 10, version 22H2 (19045.6400)"), ("Windows 10", "22H2", "19045.6400"))
        self.assertEqual(classify_title("Windows 11 Insider Preview Feature Update (28020.2818)"), ("Windows 11 Insider", "Feature Update", "28020.2818"))
        self.assertEqual(classify_title("Windows 11 Insider Preview 10.0.28120.2824 (rs_prerelease)"), ("Windows 11 Insider", "rs_prerelease", "10.0.28120.2824"))
        self.assertIsNone(classify_title("Cumulative Update for Windows 11 (26100.9278)"))
        self.assertIsNone(classify_title("Cumulative Update for Windows Server 2019 (17763.1)"))

    def test_builds_follow_product_platform_language_and_edition(self) -> None:
        provider = UupDumpProvider(http=FakeUupHttp())
        win11 = provider.builds("Windows 11", "Windows", lang="ru-ru", edition="core")
        self.assertEqual([build.version for build in win11], ["24H2 26100.9278", "23H2 22631.7517"])
        self.assertEqual(win11[0].label, "24H2 26100.9278 - 27 Aug 2026 - ru-ru home")
        self.assertEqual(win11[0].download_id, "uup:aaa:ru-ru:core")
        self.assertEqual(win11[0].name, "Win11_24H2_26100.9278_x64_ru-ru_home.zip")
        arm = provider.builds("Windows 11", "Windows ARM")
        self.assertEqual([build.download_id for build in arm], ["uup:aaa-arm:en-us:professional"])
        self.assertEqual([build.version for build in provider.builds("Windows 10", "Windows")], ["22H2 19045.6400"])
        insider = provider.builds("Windows 11 Insider", "Windows")
        self.assertEqual([build.version for build in insider], ["rs_prerelease 10.0.28120.2824", "Feature Update 28020.2818"])
        self.assertEqual(provider.builds("Windows 11", "Linux"), [])

    def test_fetch_posts_the_iso_form_and_saves_the_package(self) -> None:
        http = FakeUupHttp()
        provider = UupDumpProvider(http=http)
        build = provider.build_by_id("uup:bbb:en-gb:professionaln")
        assert build is not None
        self.assertEqual(build.product, "Windows 10")
        self.assertEqual(provider.resolve_link(build), "https://uupdump.net/download.php?id=bbb&pack=en-gb&edition=professionaln")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "pack" / build.name
            self.assertEqual(provider.fetch(build, target), target)
            self.assertTrue(target.read_bytes().startswith(b"PK"))
        url, body = http.posts[-1]
        self.assertIn("id=bbb&pack=en-gb&edition=professionaln", url)
        self.assertIn(b"autodl=2", body)
        self.assertIn(b"updates=1", body)
        self.assertNotIn(b"virtualEditions", body)
        self.assertIsNone(provider.build_by_id("uup:nope:en-us:core"))
        self.assertIsNone(provider.build_by_id("https://example.com/x.zip"))

    def test_additional_editions_travel_in_the_id_and_the_form(self) -> None:
        http = FakeUupHttp()
        provider = UupDumpProvider(http=http)
        builds = provider.builds("Windows 11", "Windows", virtual=["IoTEnterprise", "Enterprise", "Bogus"])
        self.assertEqual(builds[0].download_id, "uup:aaa:en-us:professional:Enterprise+IoTEnterprise")
        self.assertEqual(builds[0].notes, "en-us pro +Enterprise +IoTEnterprise")
        self.assertTrue(builds[0].name.endswith("_pro_plus2.zip"))
        again = provider.build_by_id(builds[0].download_id)
        assert again is not None
        self.assertEqual(again.name, builds[0].name)
        with tempfile.TemporaryDirectory() as temp:
            provider.fetch(again, Path(temp) / again.name)
        body = http.posts[-1][1].decode("ascii")
        self.assertIn("virtualEditions%5B%5D=Enterprise", body)
        self.assertIn("virtualEditions%5B%5D=IoTEnterprise", body)
        self.assertNotIn("Bogus", body)
        self.assertEqual(vendor_service.selection({"uup_virtual": ["Education"], "uup_lang": "ru-ru"}, "uupdump")[2], {"lang": "ru-ru", "edition": "", "virtual": ["Education"]})
        # the third language button hands over to the drop-down
        self.assertEqual(vendor_service.selection({"uup_lang": "other", "uup_lang_other": "de-de"}, "uupdump")[2]["lang"], "de-de")
        self.assertEqual(vendor_service.selection({"uup_lang": "other"}, "uupdump")[2]["lang"], "en-us")

    def test_newest_build_of_each_windows_generation_is_gold(self) -> None:
        saved = UupDumpProvider._instance
        UupDumpProvider._instance = UupDumpProvider(http=FakeUupHttp())
        try:
            options = vendor_service.vendor_build_options(None, {"vendor": "uupdump", "uup_platform": "Windows", "uup_product": "Windows 11", "uup_lang": "en-us", "uup_edition": "professional"})
            heads = [o["label"] for o in options if o.get("header")]
            cards = [o["label"] for o in options if not o.get("header")]
            # A generation is a block under its heading; a card under it does not say the product and the generation again.
            self.assertEqual(heads, ["Windows 11 24H2 (1)", "Windows 11 23H2 (1)"])
            self.assertEqual(cards[0], "★ 26100.9278 - 27 Aug 2026 - en-us pro - latest")
            self.assertTrue(cards[1].startswith("★ 22631.7517 - "), cards)
            self.assertNotIn("latest", cards[1])
        finally:
            UupDumpProvider._instance = saved

    def test_generations_of_windows_stand_newest_first_whatever_the_order_of_the_catalogue(self) -> None:
        def build(release: str, number: str, product: str = "Windows 11") -> VendorBuild:
            return VendorBuild(vendor="uupdump", product=product, version=f"{release} {number}", platform="Windows",
                               date="1 Oct 2026", download_id=f"uup:{release}:{number}", name="x.zip", notes="ru-ru pro")

        options = vendor_service.windows_build_options([
            build("26H1", "28000.3086"), build("25H2", "26200.7985"), build("25H2", "26200.7781"), build("24H2", "26100.6508"),
            build("26H2", "26300.9550"), build("rs_prerelease", "10.0.28120.1", "Windows 11 Insider"), build("25H2", "26200.9550"),
        ])
        self.assertEqual([o["label"] for o in options if o.get("header")], [
            "Windows 11 26H2 (1)", "Windows 11 26H1 (1)", "Windows 11 25H2 (3)", "Windows 11 24H2 (1)", "Windows 11 Insider RS_PRERELEASE (1)"])
        cards = [o["label"] for o in options if not o.get("header")]
        self.assertEqual(cards[0], "★ 26300.9550 - 1 Oct 2026 - ru-ru pro - latest")
        # 26H1 is no chosen one: its newest build stands plain.
        self.assertEqual(cards[1], "28000.3086 - 1 Oct 2026 - ru-ru pro")
        self.assertEqual(cards[2:5], ["★ 26200.7985 - 1 Oct 2026 - ru-ru pro", "26200.7781 - 1 Oct 2026 - ru-ru pro", "26200.9550 - 1 Oct 2026 - ru-ru pro"])
        # A preview is no gold and is marked for "the latest stable" to pass by.
        self.assertEqual(cards[6], "10.0.28120.1 - 1 Oct 2026 - ru-ru pro")
        self.assertEqual([bool(o.get("beta")) for o in options if not o.get("header")], [False] * 6 + [True])

    def test_download_job_uses_fetch_and_refuses_iso_in_a_path_with_spaces(self) -> None:
        saved = UupDumpProvider._instance
        UupDumpProvider._instance = UupDumpProvider(http=FakeUupHttp())
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                context = RecordingContext(root)
                context.operation = Operation(
                    id="vendor_download", title="t", description="", service="x:y",
                    parameters={"vendor": "uupdump", "vendor_versions": ["uup:aaa:en-us:professional"], "output_path": str(root / "store"), "vendor_extract": False, "uup_work_root": str(root / "UUP")},
                )
                context.report_dir.mkdir(parents=True, exist_ok=True)
                result = vendor_service.download_vendor_builds(context)
                entry = result["results"][0]
                folder = Path(str(entry["folder"]))
                # the package is unpacked in the UUP work folder, not under the destination,
                # and a Windows package is always unpacked with the zip dropped
                self.assertEqual(folder, root / "UUP" / "Win11_24H2_26100.9278_x64_en-us_pro")
                self.assertEqual(entry["archive"], "")
                self.assertFalse((folder / "Win11_24H2_26100.9278_x64_en-us_pro.zip").exists())
                self.assertTrue((folder / "uup_download_windows.cmd").exists())
                self.assertEqual(result["output"], str(root / "store" / "Vendors" / "Windows (UUP dump)"))
                self.assertEqual(vendor_service.uup_work_root(context), root / "UUP")
                spaced = root / "with space"
                spaced.mkdir()
                (spaced / vendor_service.UUP_SCRIPT).write_text("@echo off\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "spaces"):
                    vendor_service.run_uup_script(context, spaced)
                deep = root / ("x" * 60)
                deep.mkdir()
                (deep / vendor_service.UUP_SCRIPT).write_text("@echo off\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "short"):
                    vendor_service.run_uup_script(context, deep)
                with self.assertRaisesRegex(RuntimeError, "unpack"):
                    vendor_service.run_uup_script(context, root / "nothing")
        finally:
            UupDumpProvider._instance = saved

    def test_finished_iso_moves_up_and_the_cache_goes_only_when_asked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "UUP"
            folder = root / "Win11_pro"
            (folder / "UUPs").mkdir(parents=True)
            (folder / "UUPs" / "a.cab").write_bytes(b"x" * 10)
            iso = folder / "26200.iso"
            iso.write_bytes(b"iso")
            context = RecordingContext(Path(temp))
            kept = vendor_service.hand_over_iso(context, iso, folder, root, clean_cache=False)
            self.assertEqual(Path(kept), root / "26200.iso")
            self.assertTrue((root / "26200.iso").exists())
            self.assertFalse(iso.exists())
            self.assertTrue((folder / "UUPs" / "a.cab").exists())
            # a second image with the same name does not overwrite the first: it goes into a dated subfolder
            iso.write_bytes(b"iso2")
            second = Path(vendor_service.hand_over_iso(context, iso, folder, root, clean_cache=True))
            self.assertEqual(second.name, "26200.iso")
            self.assertEqual(second.parent.parent, root)
            self.assertRegex(second.parent.name, r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}$")
            self.assertEqual(second.read_bytes(), b"iso2")
            self.assertEqual((root / "26200.iso").read_bytes(), b"iso")
            self.assertFalse(folder.exists())
            self.assertTrue(any("[CLEAN] build cache removed" in line for line in context.lines))

    def test_pnputil_listing_is_parsed_by_value_shapes_not_labels(self) -> None:
        listing = (
            "Microsoft PnP Utility\n\n"
            "Published Name:     oem41.inf\nOriginal Name:      5b10w13975.inf\nProvider Name:      Lenovo Ltd.\n"
            "Class Name:         Firmware\nClass GUID:         {f2e7dd72-6468-4e36-b6f1-6488f42c1b52}\nDriver Version:     12/26/2022 265.0.0.2\n\n"
            "Опубликованное имя: oem74.inf\nИсходное имя:       netwtw6e.inf\nПоставщик:          Intel\n"
            "Имя класса:         Net\nGUID класса:        {4D36E972-E325-11CE-BFC1-08002BE10318}\nВерсия драйвера:    24.40.0.4\n\n"
            "Published Name:     oem22.inf\nOriginal Name:      ibtusb.inf\nProvider Name:      Intel Corporation\n"
            "Class Name:         Bluetooth\nClass GUID:         {e0cbf06c-cd8b-4647-bb8a-263b43f0f974}\nDriver Version:     05/30/2022 22.150.0.6\n\n"
            "Published Name:     oem90.inf\nOriginal Name:      ibtusb.inf\nProvider Name:      Intel Corporation\n"
            "Class Name:         Bluetooth\nClass GUID:         {e0cbf06c-cd8b-4647-bb8a-263b43f0f974}\nDriver Version:     07/02/2026 24.60.0.4\n"
        )
        drivers = vendor_service.parse_pnputil_drivers(listing)
        self.assertEqual([d["published"] for d in drivers], ["oem41.inf", "oem74.inf", "oem22.inf", "oem90.inf"])
        self.assertEqual(vendor_service.driver_version_key("07/02/2026 24.60.0.4"), (24, 60, 0, 4))
        self.assertGreater(vendor_service.driver_version_key("07/02/2026 24.60.0.4"), vendor_service.driver_version_key("05/30/2022 22.150.0.6"))
        self.assertEqual(drivers[1], {"published": "oem74.inf", "original": "netwtw6e.inf", "provider": "Intel", "class": "Net", "guid": "{4d36e972-e325-11ce-bfc1-08002be10318}", "version": "24.40.0.4"})
        network = [d for d in drivers if d["guid"] in vendor_service.NETWORK_CLASS_GUIDS]
        self.assertEqual([d["original"] for d in network], ["netwtw6e.inf", "ibtusb.inf", "ibtusb.inf"])
        original = vendor_service.list_machine_drivers
        try:
            vendor_service.list_machine_drivers = lambda: drivers  # type: ignore[assignment]
            options = vendor_service.machine_driver_options(Path("."), {})
        finally:
            vendor_service.list_machine_drivers = original  # type: ignore[assignment]
        # cards under a header per class, network classes first; of two ibtusb.inf versions only the newest is ticked; firmware last and unticked
        self.assertEqual([o["value"] for o in options], ["", "oem74.inf", "", "oem22.inf", "oem90.inf", "", "oem41.inf"])
        self.assertEqual([o["label_ru"] for o in options if not o["value"]], ["Сеть (1)", "Bluetooth (2)", "Прошивка (1)"])
        self.assertTrue(all(o.get("header") for o in options if not o["value"]))
        self.assertEqual([o["default"] for o in options if o["value"]], [True, False, True, False])
        self.assertEqual(options[1]["label_ru"], "netwtw6e.inf · Intel 24.40.0.4")
        self.assertEqual(options[6]["label"], "5b10w13975.inf · Lenovo Ltd. 12/26/2022 265.0.0.2")

    def test_drivers_folder_is_copied_into_the_build_and_the_switch_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input"
            # packages anywhere under input, under any folder names, at any depth
            (source / "Дрова" / "netwtw6e_oem127").mkdir(parents=True)
            (source / "Дрова" / "netwtw6e_oem127" / "netwtw6e.inf").write_text("[Version]\n", encoding="utf-8")
            (source / "Дрова" / "netwtw6e_oem127" / "netwtw14.sys").write_bytes(b"sys")
            (source / "Drivers" / "ibtusb_oem122").mkdir(parents=True)
            (source / "Drivers" / "ibtusb_oem122" / "ibtusb.inf").write_text("[Version]\n", encoding="utf-8")
            (source / "winget-export.json").write_text("{}", encoding="utf-8")
            build = root / "UUP" / "Win11_pro"
            build.mkdir(parents=True)
            self.assertEqual([p.name for p in vendor_service.find_driver_packages(source)], ["ibtusb_oem122", "netwtw6e_oem127"])
            self.assertEqual(vendor_service.apply_converter_drivers(build, source), 2)
            self.assertTrue((build / "Drivers" / "OS" / "netwtw6e_oem127" / "netwtw14.sys").exists())
            self.assertTrue((build / "Drivers" / "OS" / "ibtusb_oem122" / "ibtusb.inf").exists())
            self.assertFalse((build / "Drivers" / "OS" / "winget-export.json").exists())
            with self.assertRaisesRegex(RuntimeError, "not found"):
                vendor_service.apply_converter_drivers(build, root / "nowhere")
            empty = root / "empty"
            empty.mkdir()
            (empty / "setup.exe").write_bytes(b"MZ")
            with self.assertRaisesRegex(RuntimeError, "No .inf"):
                vendor_service.apply_converter_drivers(build, empty)
            # once the ticks are exported, an empty or missing input is fine
            self.assertEqual(vendor_service.apply_converter_drivers(build, empty, required=False), 0)
            self.assertEqual(vendor_service.apply_converter_drivers(build, root / "nowhere", required=False), 0)
            # the ticked cards are exported straight into the build, one folder per package
            calls: list[list[str]] = []

            def fake_pnputil(command: list[str]):
                calls.append(command)
                Path(command[-1], "exported.inf").write_text("[Version]\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            context = RecordingContext(root)
            self.assertEqual(vendor_service.export_driver_packages(context, ["oem122.inf", "oem127.inf"], build / "Drivers" / "OS", runner=fake_pnputil), 2)
            self.assertEqual([c[1:3] for c in calls], [["/export-driver", "oem122.inf"], ["/export-driver", "oem127.inf"]])
            self.assertTrue((build / "Drivers" / "OS" / "oem122_oem122" / "exported.inf").exists())
            # packages under input show up as ticked cards under their own header, and only the ticked ones are copied
            cards = vendor_service.input_driver_options(source)
            self.assertEqual(cards[0]["label_ru"], "Пакеты из input (2)")
            self.assertTrue(cards[0]["header"])
            self.assertEqual([c["value"] for c in cards[1:]], ["input:Drivers/ibtusb_oem122", "input:Дрова/netwtw6e_oem127"])
            self.assertTrue(all(c["default"] for c in cards[1:]))
            self.assertEqual(cards[2]["label"], "netwtw6e_oem127 · netwtw6e.inf")
            self.assertEqual(vendor_service.split_driver_ticks(["oem1.inf", "input:Drivers/ibtusb_oem122"]), (["oem1.inf"], ["Drivers/ibtusb_oem122"]))
            build2 = root / "UUP" / "Win11_two"
            build2.mkdir(parents=True)
            self.assertEqual(vendor_service.embed_input_packages(build2, source, ["Drivers/ibtusb_oem122", "gone/away"]), 1)
            self.assertTrue((build2 / "Drivers" / "OS" / "ibtusb_oem122" / "ibtusb.inf").exists())
            self.assertFalse((build2 / "Drivers" / "OS" / "netwtw6e_oem127").exists())
            config = build / "ConvertConfig.ini"
            config.write_bytes(b"[convert-UUP]\r\nAutoExit     =0\r\nAddDrivers   =0\r\nDrv_Source   =\\Drivers\r\n")
            self.assertTrue(vendor_service.enable_converter_autoexit(build, add_drivers=True))
            self.assertIn(b"AddDrivers   =1\r\n", config.read_bytes())
            self.assertIn(b"Drv_Source   =\\Drivers\r\n", config.read_bytes())
            config.write_bytes(b"[convert-UUP]\r\nAutoExit     =1\r\nAddDrivers   =0\r\n")
            self.assertFalse(vendor_service.enable_converter_autoexit(build))
            self.assertIn(b"AddDrivers   =0\r\n", config.read_bytes())

    def test_converter_autoexit_is_switched_on(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertFalse(vendor_service.enable_converter_autoexit(folder))
            config = folder / "ConvertConfig.ini"
            config.write_bytes(b"[convert-UUP]\r\nAutoStart    =1\r\nAutoExit     =0\r\nSkipISO      =0\r\n")
            self.assertTrue(vendor_service.enable_converter_autoexit(folder))
            text = config.read_bytes()
            self.assertIn(b"AutoExit     =1\r\n", text)
            self.assertIn(b"AutoStart    =1\r\n", text)
            self.assertIn(b"SkipISO      =0\r\n", text)
            # Extra editions listed -> the converter must be told to build them.
            config.write_bytes(b"[convert-UUP]\r\nStartVirtual =0\r\nAutoExit     =1\r\n[create_virtual_editions]\r\nvAutoEditions=Enterprise,Education\r\n")
            self.assertTrue(vendor_service.enable_converter_autoexit(folder))
            self.assertIn(b"StartVirtual =1\r\n", config.read_bytes())
            config.write_bytes(b"[convert-UUP]\r\nStartVirtual =0\r\nAutoExit     =1\r\n[create_virtual_editions]\r\nvAutoEditions=\r\n")
            self.assertFalse(vendor_service.enable_converter_autoexit(folder))
            self.assertIn(b"StartVirtual =0\r\n", config.read_bytes())
            config.write_bytes(b"[convert-UUP]\r\nAutoExit     =1\r\nSkipEdge     =0\r\n")
            self.assertFalse(vendor_service.enable_converter_autoexit(folder))
            self.assertTrue(vendor_service.enable_converter_autoexit(folder, skip_edge=True))
            self.assertIn(b"SkipEdge     =1\r\n", config.read_bytes())

    def test_store_apps_are_limited_to_the_chosen_ones(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "ConvertConfig.ini").write_bytes(b"[Store_Apps]\r\nSkipApps     =0\r\nAppsLevel    =0\r\nCustomList   =0\r\n")
            (folder / "CustomAppsList.txt").write_text(
                "### header\n\nMicrosoft.WindowsStore_8wekyb3d8bbwe\n# Microsoft.Windows.Photos_8wekyb3d8bbwe\n# Microsoft.WindowsNotepad_8wekyb3d8bbwe\n",
                encoding="utf-8",
            )
            self.assertEqual(vendor_service.apply_converter_apps(folder, "stock", ["x"]), [])
            self.assertIn(b"CustomList   =0", (folder / "ConvertConfig.ini").read_bytes())
            kept = vendor_service.apply_converter_apps(folder, "custom", ["Microsoft.WindowsNotepad_8wekyb3d8bbwe", "microsoft.windowsstore_8wekyb3d8bbwe"])
            self.assertEqual(kept, ["Microsoft.WindowsStore_8wekyb3d8bbwe", "Microsoft.WindowsNotepad_8wekyb3d8bbwe"])
            text = (folder / "CustomAppsList.txt").read_text(encoding="utf-8")
            self.assertIn("\nMicrosoft.WindowsNotepad_8wekyb3d8bbwe\n", text)
            self.assertIn("\n# Microsoft.Windows.Photos_8wekyb3d8bbwe\n", text)
            self.assertIn("### header", text)
            self.assertIn(b"CustomList   =1", (folder / "ConvertConfig.ini").read_bytes())
            self.assertEqual(vendor_service.apply_converter_apps(folder, "none", []), [])
            # Edge is a card of the list, not a Store family: it drives SkipEdge and never reaches CustomAppsList
            self.assertFalse(vendor_service.converter_skips_edge("stock", []))
            self.assertTrue(vendor_service.converter_skips_edge("none", ["Microsoft.Edge"]))
            self.assertTrue(vendor_service.converter_skips_edge("custom", ["Microsoft.WindowsStore_8wekyb3d8bbwe"]))
            self.assertFalse(vendor_service.converter_skips_edge("custom", ["microsoft.edge", "Microsoft.WindowsStore_8wekyb3d8bbwe"]))
            config = (folder / "ConvertConfig.ini").read_bytes()
            self.assertIn(b"SkipApps     =1", config)
            self.assertIn(b"CustomList   =0", config)

    def test_two_base_editions_get_a_short_joint_name(self) -> None:
        provider = UupDumpProvider(http=FakeUupHttp())
        both = provider.builds("Windows 11", "Windows", edition="core;professional")[0]
        self.assertEqual(both.name, "Win11_24H2_26100.9278_x64_en-us_home-pro.zip")
        self.assertEqual(both.download_id, "uup:aaa:en-us:core;professional")
        again = provider.build_by_id(both.download_id)
        assert again is not None
        self.assertEqual(provider.resolve_link(again), "https://uupdump.net/download.php?id=aaa&pack=en-us&edition=core;professional")

    def test_driver_service_gets_a_second_try_after_a_timeout(self) -> None:
        from system_core.vendors import nvidia as nvidia_module
        from unittest import mock

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"IDS": []}'

        calls: list[int] = []

        def flaky(request, timeout=0):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError("The read operation timed out")
            return Response()

        with mock.patch.object(nvidia_module, "urlopen", flaky):
            self.assertEqual(nvidia_module._default_http("GET", "https://example.test/x", None, {}), (200, '{"IDS": []}'))
        self.assertEqual(len(calls), 2)
        calls.clear()

        def dead(request, timeout=0):
            calls.append(timeout)
            raise TimeoutError("The read operation timed out")

        with mock.patch.object(nvidia_module, "urlopen", dead):
            with self.assertRaisesRegex(RuntimeError, "twice"):
                nvidia_module._default_http("GET", "https://example.test/x", None, {})
        self.assertEqual(len(calls), 2)

    def test_my_generations_round_trip_through_the_marks_file(self) -> None:
        from system_core.vendors.nvidia import DriverMarks
        with tempfile.TemporaryDirectory() as temp:
            marks = Path(temp) / "vendor_nvidia.yaml"
            marks.write_text("# marks\nmy_generations:\n  - \"GTX 16\"\n  - \"RTX 40\"\n\ngolden:\n  - \"566.36\"\n", encoding="utf-8")
            self.assertEqual(DriverMarks.load(marks).mine, ("GTX 16", "RTX 40"))
            self.assertTrue(DriverMarks.save_my_generations(marks, ["RTX 50", "GTX 700", "Bogus"]))
            text = marks.read_text(encoding="utf-8")
            self.assertIn("# marks", text)
            self.assertIn('golden:\n  - "566.36"', text)
            self.assertEqual(DriverMarks.load(marks).mine, ("RTX 50", "GTX 700"))
            self.assertFalse(DriverMarks.save_my_generations(marks, ["RTX 50", "GTX 700"]))
            self.assertTrue(DriverMarks.save_my_generations(marks, []))
            self.assertEqual(DriverMarks.load(marks).mine, ())
            self.assertIn("my_generations: []", marks.read_text(encoding="utf-8"))

    def test_download_only_script_and_hotpatch_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "Win11_25H2_26200.9448_x64_ru-ru_pro"
            folder.mkdir()
            (folder / "uup_download_windows.cmd").write_bytes(b"@echo off\r\necho downloading\r\nif EXIST convert-UUP.cmd goto :START_CONVERT\r\npause\r\n:START_CONVERT\r\ncall convert-UUP.cmd\r\n")
            script = vendor_service.download_only_script(folder)
            text = script.read_bytes().decode("utf-8")
            self.assertEqual(script.name, "uup_download_only.cmd")
            self.assertIn("goto :EOF\r\npause", text)
            self.assertNotIn("goto :START_CONVERT", text)
            self.assertEqual(vendor_service.hotpatch_members(["SSU-26100.9441-x64.cab", "Windows11.0-KB5129241-Hotpatch-x64.cab", "Windows11.0-KB5129241-Hotpatch-x64-baseless.psf", "Windows11.0-KB5124008-x64.wim"]), ["Windows11.0-KB5129241-Hotpatch-x64.cab", "Windows11.0-KB5129241-Hotpatch-x64-baseless.psf"])
            # originals and stripped copies swap places around the download step
            uups = folder / "UUPs"
            uups.mkdir()
            (uups / "Windows11.0-KB5129241-x64.msu").write_bytes(b"original")
            (uups / "Windows11.0-KB5043080-x64.msu").write_bytes(b"plain")
            (uups / "Windows11.0-KB5129241-x64.1.msu").write_bytes(b"aria2 duplicate")
            context = RecordingContext(Path(temp))
            stripped_files: list[str] = []

            def fake_strip(ctx, msu: Path, wimlib: Path) -> bool:
                if "KB5129241" in msu.name:
                    msu.write_bytes(b"stripped")
                    stripped_files.append(msu.name)
                    return True
                return False

            self.assertEqual(vendor_service.prepare_msus_for_converter(context, folder, stripper=fake_strip), 1)
            self.assertEqual(stripped_files, ["Windows11.0-KB5129241-x64.msu"])
            self.assertFalse((uups / "Windows11.0-KB5129241-x64.1.msu").exists())
            self.assertEqual((uups / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"stripped")
            self.assertEqual((uups / "_hotpatch_original" / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"original")
            self.assertFalse((uups / "_hotpatch_original" / "Windows11.0-KB5043080-x64.msu").exists())
            # before the next download the original is back and the stripped copy waits aside
            self.assertEqual(vendor_service.restore_original_msus(folder), 1)
            self.assertEqual((uups / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"original")
            self.assertEqual((uups / "_hotpatch_stripped" / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"stripped")
            # after that download the swap is a rename, the stripper is not called again
            stripped_files.clear()
            self.assertEqual(vendor_service.prepare_msus_for_converter(context, folder, stripper=fake_strip), 1)
            self.assertEqual(stripped_files, [])
            self.assertEqual((uups / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"stripped")
            self.assertEqual((uups / "_hotpatch_original" / "Windows11.0-KB5129241-x64.msu").read_bytes(), b"original")

    def test_iso_build_is_verified_against_the_request(self) -> None:
        import struct
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "Win11_25H2_26200.9448_x64_ru-ru_pro"
            (folder / "ISOFOLDER" / "sources").mkdir(parents=True)
            xml = '<WIM><IMAGE INDEX="1"><WINDOWS><BUILD>26100</BUILD><SPBUILD>1742</SPBUILD></WINDOWS></IMAGE></WIM>'.encode("utf-16")
            header = bytearray(208)
            header[:8] = b"MSWIM\x00\x00\x00"
            struct.pack_into("<I", header, 0x2C, 1)
            struct.pack_into("<Q", header, 0x48, len(xml) | (2 << 56))
            struct.pack_into("<q", header, 0x50, 208)
            wim = folder / "ISOFOLDER" / "sources" / "install.wim"
            wim.write_bytes(bytes(header) + xml)
            self.assertEqual(vendor_service.wim_image_builds(wim), ["26100.1742"])
            context = RecordingContext(Path(temp))
            base = folder / "26100.1742.240906-0316.GE_RELEASE_SVC_PROD1_CLIENTPRO_OEMRET_X64FRE_RU-RU.ISO"
            base.write_bytes(b"iso")
            with self.assertRaisesRegex(RuntimeError, "26100.1742 instead of the requested 26200.9448"):
                vendor_service.verify_iso_build(context, base, folder, "25H2 26200.9448")
            # the same major build with a lower revision is the update without its hotpatch part: accepted
            hotpatchless = folder / "26200.9445.260905-1224.25H2_GE_RELEASE_SVC_PROD1_CLIENTPRO_OEMRET_X64FRE_RU-RU.ISO"
            hotpatchless.write_bytes(b"iso")
            wim.write_bytes(bytes(header) + xml.replace(b"2\x006\x001\x000\x000", b"2\x006\x002\x000\x000").replace(b"1\x007\x004\x002", b"9\x004\x004\x005"))
            vendor_service.verify_iso_build(context, hotpatchless, folder, "25H2 26200.9448")
            # the right build passes, whatever the rest of the name says
            good = folder / "26200.9448.260911-1500.25H2_GE_RELEASE_SVC_PROD3_CLIENTPRO_OEMRET_X64FRE_RU-RU.ISO"
            good.write_bytes(b"iso")
            wim.write_bytes(bytes(header) + xml.replace(b"2\x006\x001\x000\x000", b"2\x006\x002\x000\x000").replace(b"1\x007\x004\x002", b"9\x004\x004\x008"))
            vendor_service.verify_iso_build(context, good, folder, "25H2 26200.9448")
            # a request without a build number (nothing to compare) is left alone
            vendor_service.verify_iso_build(context, base, folder, "")

    def test_adk_version_build_reads_the_kit_build(self) -> None:
        self.assertEqual(vendor_service.adk_version_build("10.1.22621.5337"), 22621)
        self.assertEqual(vendor_service.adk_version_build("10.1.26100.2454"), 26100)
        self.assertEqual(vendor_service.adk_version_build(""), 0)
        self.assertEqual(vendor_service.adk_version_build("garbage"), 0)

    def test_download_gate_waits_for_the_adk_only_for_windows(self) -> None:
        original, rights = vendor_service.adk_dism_path, vendor_service.is_elevated
        try:
            vendor_service.is_elevated = lambda: True  # type: ignore[assignment]
            vendor_service.adk_dism_path = lambda: None  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "gate_adk_missing")
            self.assertEqual(vendor_service.download_gate({"vendor": "nvidia"}), "")
            vendor_service.adk_dism_path = lambda: Path("C:/adk/dism.exe")  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "")
            # The image build needs administrator rights: without them the gate is closed and says so.
            # The other vendors download files and need none.
            vendor_service.is_elevated = lambda: False  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "gate_admin_missing")
            self.assertEqual(vendor_service.download_gate({"vendor": "nvidia"}), "")
            # The rights are what the build needs: the package alone is downloaded without them.
            for off in (False, "false", "0"):
                self.assertEqual(vendor_service.download_gate({"vendor": "uupdump", "uup_run": off}), "", off)
            for on in (True, "true", None):
                self.assertEqual(vendor_service.download_gate({"vendor": "uupdump", "uup_run": on}), "gate_admin_missing", on)
            # The kit is asked for first: it is installed from this very page.
            vendor_service.adk_dism_path = lambda: None  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "gate_adk_missing")
        finally:
            vendor_service.adk_dism_path, vendor_service.is_elevated = original, rights  # type: ignore[assignment]

    def test_the_consoles_of_the_image_build_leave_the_job_of_the_caller(self) -> None:
        # The audit of 10.10.2026: a window that fell took cmd, aria2 and DISM with it.
        import inspect

        from system_core.services import external_tools_service as tools

        body = inspect.getsource(vendor_service.run_uup_script)
        self.assertEqual(body.count("start_on_its_own(context, [\"cmd.exe\", \"/c\", "), 2)
        self.assertNotIn("subprocess.Popen(", body)
        # What is started is handed back: the build waits for it and stops it by its process tree.
        with tempfile.TemporaryDirectory() as temp:
            started = object()
            self.assertIs(tools.start_on_its_own(RecordingContext(Path(temp)), ["cmd.exe"], cwd=Path(temp), console=True,
                                                 popen=lambda argv, cwd, creationflags: started), started)

            def refused(argv: list[str], cwd: str, creationflags: int):
                if creationflags & tools.CREATE_BREAKAWAY_FROM_JOB:
                    raise PermissionError(13, "Access is denied")
                return started

            self.assertIs(tools.start_on_its_own(RecordingContext(Path(temp)), ["cmd.exe"], cwd=Path(temp), console=True, popen=refused), started)

    def test_iso_build_refuses_to_start_without_the_adk(self) -> None:
        original = vendor_service.adk_dism_path
        limit = vendor_service.UUP_PATH_SOFT_LIMIT
        try:
            vendor_service.adk_dism_path = lambda: None  # type: ignore[assignment]
            vendor_service.UUP_PATH_SOFT_LIMIT = 500  # the temp folder itself is long; that is not what is tested here
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / "short"
                folder.mkdir()
                (folder / vendor_service.UUP_SCRIPT).write_text("@echo off\n", encoding="utf-8")
                context = RecordingContext(Path(temp))
                with self.assertRaisesRegex(RuntimeError, "ADK"):
                    vendor_service.run_uup_script(context, folder)
        finally:
            vendor_service.adk_dism_path = original  # type: ignore[assignment]
            vendor_service.UUP_PATH_SOFT_LIMIT = limit

    def test_file_build_number_reads_a_windows_binary(self) -> None:
        import sys

        if sys.platform != "win32":
            self.skipTest("Windows file versions only")
        system_dism = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "dism.exe"
        if not system_dism.exists():
            self.skipTest("no system dism.exe")
        self.assertGreaterEqual(vendor_service.file_build_number(system_dism), 10240)
        self.assertEqual(vendor_service.file_build_number(Path("nowhere.exe")), 0)

    def test_converter_error_log_fails_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertEqual(vendor_service.converter_errors(folder), [])
            (folder / "ErrorLog_1.txt").write_text("\nFailed adding Edge.wim\n\nDism.exe failed adding ServicingStack update{s}\n\nDism.exe failed adding ServicingStack update{s}\n", encoding="utf-8")
            self.assertEqual(vendor_service.converter_errors(folder), ["Failed adding Edge.wim", "Dism.exe failed adding ServicingStack update{s}"])


class ExternalToolsTests(unittest.TestCase):
    def test_powershell_7_is_preferred_and_5_1_is_the_fallback(self) -> None:
        from system_core.services import external_tools_service as tools

        with tempfile.TemporaryDirectory() as temp:
            # A PowerShell of the machine: WinUtil draws its window with WPF, and the PowerShell
            # the program carries (App\AudionPs.exe) has none.
            self.assertEqual(tools.find_powershell(which=lambda name: r"C:\pwsh\pwsh.exe" if name.startswith("pwsh") else None), [r"C:\pwsh\pwsh.exe"])
            installed = Path(temp) / "PowerShell" / "7"
            installed.mkdir(parents=True)
            (installed / "pwsh.exe").write_bytes(b"")
            self.assertEqual(tools.find_powershell(which=lambda name: None, program_files=Path(temp)), [str(installed / "pwsh.exe")])
            fallback = tools.find_powershell(
                which=lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if name == "powershell.exe" else None,
                program_files=Path(temp) / "no-program-files",
            )
            self.assertTrue(fallback[0].endswith("powershell.exe"))

    def test_windows_terminal_wraps_the_shell_when_present(self) -> None:
        from system_core.services import external_tools_service as tools

        argv = tools.terminal_launch_command(["pwsh.exe"], "irm x | iex", "WinUtil", which=lambda name: r"C:\wt\wt.exe" if name == "wt.exe" else None)
        self.assertEqual(argv[:5], [r"C:\wt\wt.exe", "-w", "new", "--title", "WinUtil"])
        self.assertEqual(argv[5:], ["pwsh.exe", "-NoLogo", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "irm x | iex"])
        self.assertFalse(tools.needs_console(argv))

    def test_without_windows_terminal_the_shell_itself_is_started(self) -> None:
        # The audit of 10.10.2026, finding 10: `cmd /c start` read the `|` of the command as its own pipe.
        from system_core.services import external_tools_service as tools

        plain = tools.terminal_launch_command(["powershell.exe"], "irm x | iex", "Win'Util", which=lambda name: None)
        self.assertEqual(plain[:-1], ["powershell.exe", "-NoLogo", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command"])
        self.assertEqual(plain[-1], "$Host.UI.RawUI.WindowTitle = 'Win''Util'; irm x | iex")
        self.assertNotIn("start", plain)
        self.assertNotIn("cmd.exe", plain)
        self.assertTrue(tools.needs_console(plain))

    def test_a_tool_left_running_is_started_out_of_the_job(self) -> None:
        from system_core.services import external_tools_service as tools

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for console, base in ((True, tools.CREATE_NEW_CONSOLE), (False, 0)):
                asked: list[tuple[list[str], int]] = []
                context = RecordingContext(root)
                tools.start_on_its_own(context, ["tool.exe", "x"], cwd=root, console=console,
                                       popen=lambda argv, cwd, creationflags: asked.append((argv, creationflags)))
                self.assertEqual(asked, [(["tool.exe", "x"], base | tools.CREATE_BREAKAWAY_FROM_JOB)])
                self.assertEqual(context.lines, [])

            # A job that lets nobody out: started as before, and the log says what it means.
            tries: list[int] = []

            def refused(argv: list[str], cwd: str, creationflags: int) -> None:
                tries.append(creationflags)
                if creationflags & tools.CREATE_BREAKAWAY_FROM_JOB:
                    raise PermissionError(13, "Access is denied")

            context = RecordingContext(root)
            tools.start_on_its_own(context, ["tool.exe"], cwd=root, console=True, popen=refused)
            self.assertEqual(tries, [tools.CREATE_NEW_CONSOLE | tools.CREATE_BREAKAWAY_FROM_JOB, tools.CREATE_NEW_CONSOLE])
            self.assertEqual([line[:6] for line in context.lines], ["[WARN]"])

            # A program that is not there is an error of the operation, not "launched".
            context = RecordingContext(root)
            with self.assertRaises(FileNotFoundError):
                tools.start_on_its_own(context, [str(root / "no-such-shell.exe")], cwd=root, console=True)
            self.assertEqual(context.lines, [])


# ----- resumable download against a local range-aware server -----

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB, distinct bytes


class RangeHandler(BaseHTTPRequestHandler):
    """A server of one file. What it does with a range, and which version of the file it holds, the test sets."""

    supports_range = True
    requests: list[str] = []
    payload = PAYLOAD
    etag = '"v1"'  # '' - the server names no version: nothing to resume by
    honours_if_range = True  # False - it sends the rest of whatever it holds now, whatever the caller had
    piece = 0  # > 0 - a whole answer stops after so many bytes of body while naming the full length
    head_names_size = True  # False - HEAD says nothing of the size: a signed address that answers GET alone
    get_names_size = True  # False - a whole answer names no length either
    modified = ""  # Last-Modified of every answer, when the server names one

    def log_message(self, *_args: object) -> None:  # keep the test output quiet
        return

    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(200)
        if self.head_names_size:
            self.send_header("Content-Length", str(len(self.payload)))
        if self.modified:
            self.send_header("Last-Modified", self.modified)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        range_header = self.headers.get("Range")
        RangeHandler.requests.append(range_header or "")
        asked = self.headers.get("If-Range")
        partial = bool(range_header) and self.supports_range and (not self.honours_if_range or not asked or asked == self.etag)
        if partial:
            start = int(range_header.split("=")[1].split("-")[0])
            last = min(len(self.payload), start + self.piece) - 1 if self.piece else len(self.payload) - 1
            body = self.payload[start:last + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{last}/{len(self.payload)}")
        else:
            body = self.payload
            self.send_response(200)
        if self.etag:
            self.send_header("ETag", self.etag)
        if self.modified:
            self.send_header("Last-Modified", self.modified)
        if partial or self.get_names_size:
            self.send_header("Content-Length", str(len(body)))
        else:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class NvidiaThisPcTests(unittest.TestCase):
    """What the NVIDIA card of the PC changes in the list of drivers."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.marks = Path(self.folder.name) / "vendor_nvidia.yaml"
        self.marks.write_text("golden: ['581.57']\nmy_generations: ['RTX 40', 'RTX 50']\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.folder.cleanup()

    def provider(self, card_name: str = "", driver: str = "") -> "NvidiaProvider":
        from system_core.core import pc_devices

        card = pc_devices.Device(r"PCI\VEN_10DE&DEV_2786&SUBSYS_00001043&REV_A1\4&1", card_name, (r"PCI\VEN_10DE&CC_030000", r"PCI\CC_0300"), driver)
        return NvidiaProvider(http=FakeNvidiaHttp(), marks_path=self.marks, devices=lambda: [card] if card_name else [])

    def test_the_card_is_read_by_its_name(self) -> None:
        from system_core.vendors import nvidia

        self.assertEqual([nvidia.series_of(name) for name in (
            "NVIDIA GeForce RTX 4070 Laptop GPU", "NVIDIA GeForce GTX 1660 SUPER", "NVIDIA GeForce GTX 1060 6GB",
            "NVIDIA GeForce GTX 970", "NVIDIA GeForce GTX 750 Ti", "NVIDIA Quadro P2000")], ["RTX 40", "GTX 16", "GTX 10", "GTX 900", "GTX 700", ""])
        self.assertTrue(nvidia.is_notebook_card("NVIDIA GeForce RTX 4070 Laptop GPU"))
        self.assertFalse(nvidia.is_notebook_card("NVIDIA GeForce RTX 4070"))
        # Windows' 32.0.15.8157 is NVIDIA's 581.57.
        self.assertEqual((nvidia.driver_number("32.0.15.8157"), nvidia.driver_number("31.0.15.3623"), nvidia.driver_number("")), ("581.57", "536.23", ""))

    def test_the_newest_golden_driver_is_ticked_for_the_cards_own_generation(self) -> None:
        made = self.provider("NVIDIA GeForce RTX 4070", "32.0.15.6636")
        builds = made.builds("Game Ready Driver", "Windows", series="RTX 40", form="desktop")
        self.assertEqual([(build.version, build.ticked) for build in builds][:3], [("616.56", False), ("581.57", True), ("566.36", False)])
        self.assertIn("installed", builds[2].notes)
        self.assertEqual(made.note("Game Ready Driver")[0], "NVIDIA · this PC: NVIDIA GeForce RTX 4070 · 566.36")
        # "My cards" holds the generation of the card too.
        mine = made.builds("Game Ready Driver", "Windows", series="*", form="desktop")
        self.assertTrue(any(build.ticked for build in mine))

    def test_the_golden_driver_is_not_ticked_when_the_card_runs_it(self) -> None:
        builds = self.provider("NVIDIA GeForce RTX 4070", "32.0.15.8157").builds("Game Ready Driver", "Windows", series="RTX 40", form="desktop")
        self.assertFalse(any(build.ticked for build in builds))

    def test_a_list_of_another_generation_ticks_nothing_and_says_why(self) -> None:
        made = self.provider("NVIDIA GeForce RTX 4070", "32.0.15.6636")
        builds = made.builds("Game Ready Driver", "Windows", series="GTX 10", form="desktop")
        self.assertFalse(any(build.ticked for build in builds))
        self.assertIn("another generation", made.note("Game Ready Driver")[0])

    def test_my_cards_asks_its_generations_side_by_side(self) -> None:
        import threading
        import time

        inside = 0
        most = 0
        lock = threading.Lock()
        answer = FakeNvidiaHttp()

        def slow(method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
            nonlocal inside, most
            with lock:
                inside += 1
                most = max(most, inside)
            time.sleep(0.2)
            with lock:
                inside -= 1
            return answer(method, url, body, headers)

        made = NvidiaProvider(http=slow, marks_path=self.marks, devices=lambda: [])
        builds = made.builds("Game Ready Driver", "Windows", series="*", form="desktop")
        self.assertTrue(builds)
        # Two generations of the file: both were inside the service at once - the wait is one answer, not two.
        self.assertEqual(most, 2)
        # The list is the first generation's, in its order - as it was when they were asked in turn.
        alone = NvidiaProvider(http=FakeNvidiaHttp(), marks_path=self.marks, devices=lambda: [])
        first = alone.builds("Game Ready Driver", "Windows", series="RTX 40", form="desktop")
        self.assertEqual([build.version for build in builds], [build.version for build in first if build.version in {item.version for item in builds}])

    def test_my_generations_start_as_the_owners_file_has_them(self) -> None:
        saved = NvidiaProvider._instance
        NvidiaProvider._instance = self.provider()
        try:
            self.assertEqual(vendor_service.default_my_generations({}), ["RTX 40", "RTX 50"])
            self.marks.write_text("golden: []\n", encoding="utf-8")
            # A file that names none leaves the default of the manifest.
            self.assertIsNone(vendor_service.default_my_generations({}))
        finally:
            NvidiaProvider._instance = saved

    def test_without_an_nvidia_card_nothing_is_ticked(self) -> None:
        made = self.provider()
        self.assertFalse(any(build.ticked for build in made.builds("Game Ready Driver", "Windows", series="RTX 40", form="desktop")))
        self.assertIn("no NVIDIA card on this PC", made.note("Game Ready Driver")[0])
        self.assertEqual(made.note("CUDA Toolkit"), ("", ""))


class NvidiaFromTechPowerUpTests(unittest.TestCase):
    """Game Ready drivers from TechPowerUp's page: the list the NVIDIA page opens on."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.marks = Path(self.folder.name) / "vendor_nvidia.yaml"
        self.marks.write_text("golden: ['616.92', '581.57']\nmy_generations: ['RTX 40', 'RTX 50']\n", encoding="utf-8")
        self.asked: list[str] = []
        self.saved = (NvidiaProvider._instance, vendor_service.download_with_resume, vendor_service.authenticode)

    def tearDown(self) -> None:
        NvidiaProvider._instance, vendor_service.download_with_resume, vendor_service.authenticode = self.saved  # type: ignore[assignment]
        self.folder.cleanup()

    def page(self, slug: str) -> list:
        from system_core.services.techpowerup_service import TechPowerUpFile

        self.asked.append(slug)
        tail = "64bit-international"
        return [
            TechPowerUpFile("617.50 Hotfix", "October 8th, 2026", "", f"617.50-desktop-notebook-win10-win11-{tail}-dch.hf.exe", "9", "945.1 MB", "A9"),
            TechPowerUpFile("617.42 WHQL", "October 6th, 2026", "", f"617.42-desktop-win10-win11-{tail}-dch-whql.exe", "8", "945.0 MB", "a8"),
            TechPowerUpFile("616.92 WHQL", "September 9th, 2026", "", f"616.92-desktop-win10-win11-{tail}-dch-whql.exe", "7", "945.0 MB", "A7"),
            TechPowerUpFile("595.76 Beta", "March 3rd, 2026", "", f"595.76-desktop-win10-win11-{tail}-dch-whql.exe", "6", "930.0 MB", "A6"),
            TechPowerUpFile("595.59 WHQL", "February 2nd, 2026", "", f"DANGEROUS-DO-NOT-USE-SAVED-FOR-ARCHIVING-AND-RESEARCH-595.59-desktop-win10-win11-{tail}-dch-whql.exe", "5", "930.0 MB", "A5"),
            TechPowerUpFile("511.17 WHQL", "January 11th, 2022", "Only for RTX 3080 12 GB", f"511.17-desktop-win10-win11-{tail}-dch-whql.exe", "4", "780.0 MB", "A4"),
            # A version of five files: Windows 7, 32 bits, the Standard and the DCH driver.
            TechPowerUpFile("472.12 WHQL", "September 20th, 2021", "Standard Driver", f"472.12-desktop-win10-win11-{tail}-whql.exe", "31", "700.0 MB", "A31"),
            TechPowerUpFile("472.12 WHQL", "September 20th, 2021", "DCH Driver", f"472.12-desktop-win10-win11-{tail}-dch-whql.exe", "32", "720.0 MB", "A32"),
            TechPowerUpFile("472.12 WHQL", "September 20th, 2021", "", f"472.12-desktop-win8-win7-{tail}-whql.exe", "33", "650.0 MB", "A33"),
            TechPowerUpFile("391.35 WHQL", "March 27th, 2018", "", "391.35-desktop-win10-32bit-international-whql.exe", "21", "380.0 MB", "A21"),
            TechPowerUpFile("391.35 WHQL", "March 27th, 2018", "", f"391.35-desktop-win10-{tail}-whql.exe", "22", "450.0 MB", "A22"),
            TechPowerUpFile("391.35 WHQL", "March 27th, 2018", "", f"391.35-desktop-win8-win7-{tail}-whql.exe", "23", "440.0 MB", "A23"),
        ]

    def provider(self, card: tuple[str, str] | None = None) -> "NvidiaProvider":
        from system_core.core import pc_devices

        devices = [pc_devices.Device(r"PCI\VEN_10DE&DEV_2786&SUBSYS_1\4&1", card[0], (r"PCI\VEN_10DE&CC_030000",), card[1])] if card else []

        def no_service(method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
            raise AssertionError("NVIDIA's service was asked: " + url)

        def signed_link(slug: str, file_id: str) -> str:
            # As the site signs it: a host of its own, the name of the file at the end.
            name = next(item.file_name for item in self.page(slug) if item.file_id == file_id)
            return f"https://us9-dl.techpowerup.com/files/sig{file_id}/{name}"

        return NvidiaProvider(http=no_service, tpu_list=self.page, tpu_resolve=signed_link, marks_path=self.marks, devices=lambda: devices)

    def test_one_card_a_version_its_driver_for_a_64_bit_windows_10_or_11(self) -> None:
        from system_core.vendors import nvidia

        made = self.provider()
        builds = made.builds("Game Ready Driver", "Windows", source="techpowerup", series="*", form="notebook")
        self.assertEqual(self.asked, [nvidia.GEFORCE_SLUG])
        # The list is kept: asked for again, the page is not read again.
        made.builds("Game Ready Driver", "Windows", source="techpowerup")
        self.assertEqual(len(self.asked), 1)
        # Newest first, as the page has them. The file kept "for research" is not offered at all.
        self.assertEqual([build.version for build in builds], ["617.50 Hotfix", "617.42", "616.92", "595.76 Beta", "511.17", "472.12", "391.35"])
        self.assertEqual([build.preview for build in builds], [True, False, False, True, False, False, False])
        by_version = {build.version: build for build in builds}
        # Of the files of one version: the DCH driver; where there is none - the driver for a 64-bit Windows 10.
        self.assertEqual(by_version["472.12"].download_id, "tpu:nvidia-geforce-graphics-drivers:32")
        self.assertEqual(by_version["391.35"].name, "391.35-desktop-win10-64bit-international-whql.exe")
        self.assertEqual(by_version["617.42"].date, "6 Oct 2026")
        self.assertEqual(by_version["616.92"].notes.split(" - ")[0], "945.0 MB")
        self.assertIn("★ golden", by_version["616.92"].notes)
        self.assertIn("Only for RTX 3080 12 GB", by_version["511.17"].notes)
        self.assertNotIn("DCH Driver", by_version["472.12"].notes)
        self.assertFalse(any(build.ticked for build in builds))
        self.assertEqual(made.note("Game Ready Driver")[0], "TechPowerUp · every Game Ready release in one list · no NVIDIA card on this PC")
        self.assertEqual((made.expected_sha256(by_version["617.42"]), made.signer_for(by_version["617.42"])), ("A8", "NVIDIA Corporation"))
        self.assertEqual(made.resolve_link(by_version["617.42"]), "https://us9-dl.techpowerup.com/files/sig8/617.42-desktop-win10-win11-64bit-international-dch-whql.exe")
        self.assertEqual((nvidia.geforce_file_rank("x-desktop-win10-win11-64bit-international-dch-whql.exe"), nvidia.geforce_file_rank("x-desktop-win7-64bit-international-whql.exe")), (3, None))
        self.assertEqual((nvidia.tpu_date("March 3rd, 2026"), nvidia.tpu_date("May 1st, 2025"), nvidia.tpu_date("soon")), ("3 Mar 2026", "1 May 2025", "soon"))

    def test_the_card_of_the_pc_is_named_and_its_driver_marked_but_nothing_is_ticked(self) -> None:
        made = self.provider(("NVIDIA GeForce RTX 4070", "32.0.16.1692"))
        builds = made.builds("Game Ready Driver", "Windows", source="techpowerup")
        self.assertFalse(any(build.ticked for build in builds))
        self.assertEqual([build.version for build in builds if "installed" in build.notes], ["616.92"])
        self.assertIn("nothing ticked · this PC: NVIDIA GeForce RTX 4070 · 616.92", made.note("Game Ready Driver")[0])
        self.assertIn("ничего не отмечено · этот ПК", made.note("Game Ready Driver")[1])

    def test_a_run_in_another_process_finds_the_build_by_its_id(self) -> None:
        fresh = self.provider()
        build = fresh.build_by_id("tpu:nvidia-geforce-graphics-drivers:7")
        self.assertEqual((build.version, build.name), ("616.92", "616.92-desktop-win10-win11-64bit-international-dch-whql.exe"))  # type: ignore[union-attr]
        self.assertEqual(fresh.expected_sha256(build), "A7")  # type: ignore[arg-type]
        self.assertIsNone(fresh.build_by_id("tpu:nvidia-geforce-graphics-drivers:5"))  # the file kept "for research"
        self.assertIsNone(fresh.build_by_id("tpu:nvidia-geforce-graphics-drivers:404"))

    def test_studio_and_the_switch_to_nvidia_ask_nvidias_own_service(self) -> None:
        made = NvidiaProvider(http=FakeNvidiaHttp(), tpu_list=self.page, tpu_resolve=lambda slug, file_id: "", marks_path=self.marks, devices=lambda: [])
        own = made.builds("Game Ready Driver", "Windows", source="nvidia", series="RTX 40", form="desktop")
        studio = made.builds("Studio Driver", "Windows", source="techpowerup", series="RTX 40", form="desktop")
        self.assertTrue(own and studio)
        self.assertEqual(self.asked, [])
        self.assertTrue(all("download.nvidia.com" in build.download_id for build in own + studio))
        self.assertEqual((made.signer_for(own[0]), made.expected_sha256(own[0])), ("", ""))

    def test_the_form_marks_the_newest_release_not_the_hotfix_over_it(self) -> None:
        NvidiaProvider._instance = self.provider()
        options = vendor_service.vendor_build_options(None, {"vendor": "nvidia", "nv_product": "Game Ready Driver", "nv_source": "techpowerup"})
        self.assertTrue(options[0].get("note"))
        cards = options[1:]
        self.assertEqual((cards[0]["label"].split(" - ")[0], cards[0].get("beta")), ("617.50 Hotfix", True))
        self.assertTrue(cards[1]["label"].startswith("617.42 - 6 Oct 2026 - 945.0 MB"))
        self.assertTrue(cards[1]["label"].endswith(" - latest"))
        self.assertEqual(len([card for card in cards if card["label"].endswith(" - latest")]), 1)
        self.assertEqual(cards[3].get("beta"), True)  # 595.76 Beta

    def test_the_file_of_the_mirror_is_compared_by_sha256_and_checked_for_nvidias_signature(self) -> None:
        import hashlib

        payload = b"the driver"
        made = self.provider()
        NvidiaProvider._instance = made
        made.builds("Game Ready Driver", "Windows", source="techpowerup")
        made._sha256["tpu:nvidia-geforce-graphics-drivers:8"] = hashlib.sha256(payload).hexdigest().upper()  # type: ignore[attr-defined]
        made._sha256["tpu:nvidia-geforce-graphics-drivers:7"] = hashlib.sha256(payload).hexdigest().upper()  # type: ignore[attr-defined]

        def fake_download(context: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            return target

        signed = {"617.42-desktop-win10-win11-64bit-international-dch-whql.exe": ("Valid", "CN=NVIDIA Corporation, O=NVIDIA Corporation, L=Santa Clara"),
                  "616.92-desktop-win10-win11-64bit-international-dch-whql.exe": ("Valid", "CN=Somebody Else, O=Somebody")}
        vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
        vendor_service.authenticode = lambda path: signed.get(path.name, ("NotSigned", ""))  # type: ignore[assignment]

        def run(root: Path, file_id: str) -> tuple[dict[str, object], RecordingContext]:
            context = RecordingContext(root)
            context.operation = Operation(id="vendor_download", title="t", description="", service="x:y", parameters={
                "vendor": "nvidia", "nv_product": "Game Ready Driver", "nv_source": "techpowerup",
                "vendor_versions": [f"tpu:nvidia-geforce-graphics-drivers:{file_id}"], "output_path": str(root / "store")})
            return vendor_service.download_vendor_builds(context), context

        with tempfile.TemporaryDirectory() as temp:
            result, context = run(Path(temp), "8")
            entry = result["results"][0]  # type: ignore[index]
            self.assertEqual((entry["sha256"], entry["signature"]), ("matches", "Valid"))
            self.assertIn("[OK] 617.42-desktop-win10-win11-64bit-international-dch-whql.exe: SHA256 matches TechPowerUp", context.lines)
            self.assertIn("[OK] 617.42-desktop-win10-win11-64bit-international-dch-whql.exe: signed by NVIDIA Corporation", context.lines)
            # A file that is somebody else's fails the run and is named.
            with self.assertRaisesRegex(RuntimeError, "signature not confirmed"):
                run(Path(temp), "7")


class TechPowerUpVendorTests(unittest.TestCase):
    def test_products_list_versions_from_the_mirrored_pages_and_resolve_signed_links(self) -> None:
        from system_core.vendors.techpowerup import KINDS, PRODUCTS, TechPowerUpProvider
        from system_core.services.techpowerup_service import parse_versions
        calls: list[str] = []

        def fake_list(slug: str):
            calls.append(slug)
            return parse_versions(TPU_DDU_PAGE)

        provider = TechPowerUpProvider(tpu_list=fake_list, tpu_resolve=lambda slug, file_id: f"https://us1-dl.techpowerup.com/{slug}/{file_id}.exe?sig=1", devices=lambda: [])
        self.assertEqual(provider.products("Windows"), list(PRODUCTS))
        self.assertEqual(provider.products("Linux"), [])
        builds = provider.builds("Display Driver Uninstaller", "Windows")
        self.assertEqual(calls, ["display-driver-uninstaller-ddu"])
        # one card per version: the installer is the card, the portable build rides along
        self.assertEqual([build.label for build in builds], [
            "18.1.5.7 - August 19th, 2026 - 1.7 MB, portable 1.2 MB",
            "18.1.5.6 - July 1st, 2026 - 1.7 MB",
        ])
        first, second = builds
        self.assertEqual(first.name, "DDU-v18.1.5.7_setup.exe")
        self.assertTrue(first.download_id.startswith("tpu:display-driver-uninstaller-ddu:"))
        self.assertEqual(first.portable_id, "tpu:display-driver-uninstaller-ddu:3219")
        self.assertEqual(second.portable_id, "")
        portable = provider.build_by_id(first.portable_id)
        assert portable is not None
        self.assertEqual((portable.name, portable.notes, portable.portable_id), ("DDU v18.1.5.7.exe", "portable 1.2 MB", portable.download_id))
        self.assertIs(provider.build_by_id(first.download_id), provider.build_by_id(first.download_id))
        self.assertTrue(provider.resolve_link(portable).startswith("https://us1-dl.techpowerup.com/display-driver-uninstaller-ddu/3219"))
        # a second listing of the same product comes from the cache
        provider.builds("Display Driver Uninstaller", "Windows")
        self.assertEqual(len(calls), 1)
        self.assertEqual(provider.builds("Nothing", "Windows"), [])
        self.assertEqual(sum(len(items) for items in KINDS.values()), len(PRODUCTS))
        self.assertIn("Display Driver Uninstaller", KINDS["tools"])
        # the kind switch says which product field counts
        values = {"tpu_kind": "tools", "tpu_driver": "AMD Ryzen Chipset", "tpu_tool": "NVCleanstall"}
        self.assertEqual(vendor_service.selection(values, "techpowerup")[:2], ("Windows", "NVCleanstall"))
        values["tpu_kind"] = "drivers"
        self.assertEqual(vendor_service.selection(values, "techpowerup")[:2], ("Windows", "AMD Ryzen Chipset"))

    def test_a_driver_is_ticked_only_on_a_pc_that_has_its_device(self) -> None:
        from system_core.core import pc_devices
        from system_core.services.techpowerup_service import parse_versions
        from system_core.vendors.techpowerup import TechPowerUpProvider, same_version

        page = TPU_DDU_PAGE.replace("Display Driver Uninstaller (DDU) 18.1.5.7", "Intel Graphics Drivers 101.7085 Beta") \
            .replace("Display Driver Uninstaller (DDU) 18.1.5.6", "Intel Graphics Drivers 101.7080")
        card = pc_devices.Device(r"PCI\VEN_8086&DEV_46A8&SUBSYS_22E717AA&REV_0C\3&11583659&0&10", "Intel(R) Iris(R) Xe Graphics",
                                 (r"PCI\VEN_8086&CC_030000", r"PCI\CC_0300"), "32.0.101.7000")

        def made(devices):
            return TechPowerUpProvider(tpu_list=lambda slug: parse_versions(page), tpu_resolve=lambda slug, file_id: "", devices=lambda: devices)

        # The PC has the device: the newest release - a beta is none - is ticked, and the list says whose it is.
        with_card = made([card])
        builds = with_card.builds("Intel Graphics (Arc, iGPU)", "Windows")
        self.assertEqual([(build.version, build.ticked) for build in builds], [("101.7085 Beta", False), ("101.7080", True)])
        self.assertIn("this PC: Intel(R) Iris(R) Xe Graphics · 32.0.101.7000", with_card.note("Intel Graphics (Arc, iGPU)")[0])
        # The device runs that release already (Windows' 32.0.101.7080 is the page's 101.7080): marked, not ticked.
        current = made([pc_devices.Device(card.device_id, card.name, card.compatible, "32.0.101.7080")]).builds("Intel Graphics (Arc, iGPU)", "Windows")
        self.assertEqual([(build.ticked, "installed" in build.notes) for build in current], [(False, False), (False, True)])
        # No such device: nothing is ticked, and the list says so.
        without = made([])
        self.assertFalse(any(build.ticked for build in without.builds("Intel Graphics (Arc, iGPU)", "Windows")))
        self.assertIn("nothing ticked", without.note("Intel Graphics (Arc, iGPU)")[0])
        # A utility is for any PC: its newest release is ticked, and nothing is said over its list.
        tools = TechPowerUpProvider(tpu_list=lambda slug: parse_versions(TPU_DDU_PAGE), tpu_resolve=lambda slug, file_id: "", devices=lambda: [])
        self.assertEqual([build.ticked for build in tools.builds("Display Driver Uninstaller", "Windows")], [True, False])
        self.assertEqual(tools.note("Display Driver Uninstaller"), ("", ""))

        self.assertTrue(same_version("32.0.101.7085", "101.7085"))
        self.assertTrue(same_version("23.160.0.4", "23.160.0"))
        self.assertFalse(same_version("32.0.21025.1024", "26.9.1"))
        self.assertFalse(same_version("24.70.0.3", "24"))

    def test_portable_files_are_told_apart_and_the_switch_flips_the_list(self) -> None:
        from system_core.vendors.techpowerup import TechPowerUpProvider, is_portable_file
        from system_core.services.techpowerup_service import parse_versions
        # the word is said wherever the file needs no installation
        self.assertTrue(is_portable_file("Display Driver Uninstaller", "Portable", "DDU v18.exe"))
        self.assertFalse(is_portable_file("Display Driver Uninstaller", "Installer", "DDU_setup.exe"))
        self.assertTrue(is_portable_file("CPU-Z", "ZIP Archive", "cpu-z_3.01-en.zip"))
        self.assertTrue(is_portable_file("ThrottleStop", "", "ThrottleStop_9.7.3.zip"))
        self.assertTrue(is_portable_file("GPU-Z", "Standard Version", "GPU-Z.2.70.0.exe"))
        self.assertTrue(is_portable_file("NVCleanstall", "", "NVCleanstall_1.19.0.exe"))
        self.assertFalse(is_portable_file("Samsung Magician", "", "Samsung_Magician_Installer.exe"))
        saved = TechPowerUpProvider._instance
        TechPowerUpProvider._instance = TechPowerUpProvider(tpu_list=lambda slug: parse_versions(TPU_DDU_PAGE), tpu_resolve=lambda slug, file_id: "", devices=lambda: [])
        try:
            base = {"vendor": "techpowerup", "tpu_kind": "tools", "tpu_tool": "Display Driver Uninstaller"}
            options = vendor_service.vendor_build_options(None, base)
            self.assertEqual(len(options), 2)
            self.assertEqual(options[0]["portable_id"], "tpu:display-driver-uninstaller-ddu:3219")
            self.assertEqual(options[0]["installer_id"], options[0]["value"])
            self.assertNotIn("portable_id", options[1])
            self.assertTrue(options[0]["label"].endswith("- latest"))
            portable = vendor_service.vendor_build_options(None, {**base, "vendor_portable_only": True})
            self.assertEqual([option["value"] for option in portable], ["tpu:display-driver-uninstaller-ddu:3219"])
            self.assertEqual(portable[0]["installer_id"], options[0]["value"])
        finally:
            TechPowerUpProvider._instance = saved


class RecordingContext(JobContext):
    """A JobContext whose cancel flag flips after a given number of checks."""

    def __init__(self, root: Path, cancel_after: int | None = None) -> None:
        operation = Operation(id="test", title="test", description="", service="x:y")
        super().__init__(get_project_paths(root), operation, root / "log.txt", root / "report")
        self.lines: list[str] = []
        self.checks = 0
        self.cancel_after = cancel_after

    def log(self, message: str) -> None:  # type: ignore[override]
        self.lines.append(message)

    def cancelled(self) -> bool:  # type: ignore[override]
        self.checks += 1
        return self.cancel_after is not None and self.checks > self.cancel_after


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/build.zip"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        RangeHandler.requests = []
        RangeHandler.supports_range = True
        RangeHandler.payload = PAYLOAD
        RangeHandler.etag = '"v1"'
        RangeHandler.honours_if_range = True
        RangeHandler.piece = 0
        RangeHandler.head_names_size = RangeHandler.get_names_size = True
        RangeHandler.modified = ""
        vendor_service.CHUNK_BYTES = 64 * 1024

    def broken_off(self, root: Path) -> Path:
        """A download cancelled part way: the `.part` and its note are left for the next run."""
        target = root / "out" / "build.zip"
        with self.assertRaisesRegex(RuntimeError, "Cancelled"):
            vendor_service.download_with_resume(RecordingContext(root, cancel_after=3), self.url, target, "build")
        part = target.with_name("build.zip.part")
        self.assertTrue(0 < part.stat().st_size < len(PAYLOAD))
        RangeHandler.requests = []
        return target

    def tearDown(self) -> None:
        vendor_service.CHUNK_BYTES = 1024 * 1024

    def test_cancel_keeps_part_and_second_run_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "out" / "build.zip"
            first = RecordingContext(root, cancel_after=3)
            with self.assertRaisesRegex(RuntimeError, "Cancelled"):
                vendor_service.download_with_resume(first, self.url, target, "build")
            part = target.with_name("build.zip.part")
            self.assertTrue(part.exists())
            self.assertLess(part.stat().st_size, len(PAYLOAD))

            second = RecordingContext(root)
            result = vendor_service.download_with_resume(second, self.url, target, "build")
            self.assertEqual(result, target)
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertFalse(part.exists())
            self.assertFalse(part.with_name(part.name + ".json").exists())
            self.assertTrue(any(request.startswith("bytes=") for request in RangeHandler.requests))
            self.assertTrue(any(line.startswith("[RESUME]") for line in second.lines))

    def test_without_a_version_named_by_the_server_nothing_is_resumed(self) -> None:
        # A fixed address may hold a new version by now: with nothing to tell the same file by, the download starts over.
        RangeHandler.etag = ""
        with tempfile.TemporaryDirectory() as temp:
            target = self.broken_off(Path(temp))
            vendor_service.download_with_resume(RecordingContext(Path(temp)), self.url, target, "build")
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertEqual(RangeHandler.requests, [""])

    def test_a_file_that_changed_on_the_server_is_fetched_whole(self) -> None:
        other = bytes(reversed(PAYLOAD))
        with tempfile.TemporaryDirectory() as temp:
            target = self.broken_off(Path(temp))
            RangeHandler.payload, RangeHandler.etag = other, '"v2"'
            context = RecordingContext(Path(temp))
            vendor_service.download_with_resume(context, self.url, target, "build")
            # Not the head of the old file with the tail of the new one.
            self.assertEqual(target.read_bytes(), other)
            self.assertTrue(any("starting over" in line for line in context.lines))

    def test_a_server_that_continues_another_file_is_asked_once_more_from_zero(self) -> None:
        other = bytes(reversed(PAYLOAD))
        with tempfile.TemporaryDirectory() as temp:
            target = self.broken_off(Path(temp))
            # It ignores If-Range and sends the rest of the new file: its ETag tells.
            RangeHandler.payload, RangeHandler.etag, RangeHandler.honours_if_range = other, '"v2"', False
            context = RecordingContext(Path(temp))
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(target.read_bytes(), other)
            self.assertEqual(len(RangeHandler.requests), 2)
            self.assertEqual(RangeHandler.requests[1], "")

    def test_a_server_that_sends_the_rest_in_pieces_is_asked_until_the_file_is_whole(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = self.broken_off(Path(temp))
            RangeHandler.piece = 300_000
            context = RecordingContext(Path(temp))
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertGreater(len(RangeHandler.requests), 2)
            self.assertTrue(any("asking for the rest" in line for line in context.lines))

    def test_complete_file_is_not_fetched_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "build.zip"
            target.write_bytes(PAYLOAD)
            context = RecordingContext(root)
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(RangeHandler.requests, [])
            self.assertTrue(any(line.startswith("[CACHE]") for line in context.lines))

    def finished(self, root: Path, content: bytes, *, changed: float | None = None) -> Path:
        """A file already in the folder, changed at the given time."""
        target = root / "build.zip"
        target.write_bytes(content)
        if changed is not None:
            os.utime(target, (changed, changed))
        return target

    def test_a_server_that_names_no_size_is_asked_and_the_file_is_replaced(self) -> None:
        # The audit of 10.10.2026, finding 11: no size on HEAD made any file of that name a finished one.
        RangeHandler.head_names_size = RangeHandler.get_names_size = False
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = self.finished(root, b"an installer of last year, another size")
            context = RecordingContext(root)
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(RangeHandler.requests, [""])
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertFalse(any(line.startswith("[CACHE]") for line in context.lines))

    def test_a_server_that_names_the_size_only_with_the_file_decides_by_its_answer(self) -> None:
        # A signed address answers GET alone: its first lines tell the size, and the body is left unread.
        RangeHandler.head_names_size = False
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            same = bytes(reversed(PAYLOAD))  # the size of the server's file - all a server without a time can be asked
            target = self.finished(root, same)
            context = RecordingContext(root)
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(RangeHandler.requests, [""])
            self.assertEqual(target.read_bytes(), same)
            self.assertTrue(any(line.startswith("[CACHE]") for line in context.lines))
            self.assertFalse(target.with_name("build.zip.part").exists())

            RangeHandler.requests = []
            other = self.finished(root, PAYLOAD[:-1])
            vendor_service.download_with_resume(RecordingContext(root), self.url, other, "build")
            self.assertEqual(RangeHandler.requests, [""])
            self.assertEqual(other.read_bytes(), PAYLOAD)

    def test_a_file_of_the_same_size_older_than_the_servers_is_fetched_again(self) -> None:
        # A maker puts a new build under the old name, of the very size: the time tells them apart.
        from email.utils import formatdate

        put_out = 1_780_000_000.0
        RangeHandler.modified = formatdate(put_out, usegmt=True)
        for names_size_on_head in (True, False):
            RangeHandler.head_names_size, RangeHandler.requests = names_size_on_head, []
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = self.finished(root, bytes(reversed(PAYLOAD)), changed=put_out - 86_400)
                context = RecordingContext(root)
                vendor_service.download_with_resume(context, self.url, target, "build")
                self.assertEqual(RangeHandler.requests, [""], names_size_on_head)
                self.assertEqual(target.read_bytes(), PAYLOAD, names_size_on_head)
                self.assertFalse(any(line.startswith("[CACHE]") for line in context.lines))

    def test_a_file_of_the_same_size_not_older_than_the_servers_is_ready(self) -> None:
        from email.utils import formatdate

        put_out = 1_780_000_000.0
        RangeHandler.modified = formatdate(put_out, usegmt=True)
        for names_size_on_head, asked in ((True, []), (False, [""])):
            RangeHandler.head_names_size, RangeHandler.requests = names_size_on_head, []
            for changed in (put_out, put_out + 3_600):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    mine = bytes(reversed(PAYLOAD))
                    target = self.finished(root, mine, changed=changed)
                    context = RecordingContext(root)
                    RangeHandler.requests = []
                    vendor_service.download_with_resume(context, self.url, target, "build")
                    self.assertEqual(RangeHandler.requests, asked, (names_size_on_head, changed))
                    self.assertEqual(target.read_bytes(), mine)
                    self.assertTrue(any(line.startswith("[CACHE]") for line in context.lines))

    def test_server_without_ranges_restarts_from_zero(self) -> None:
        RangeHandler.supports_range = False
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "build.zip"
            target.with_name("build.zip.part").write_bytes(PAYLOAD[:1000])
            context = RecordingContext(root)
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_extract_zip_writes_files_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "a.zip"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                bundle.writestr("Install Resolve.exe", b"exe")
                bundle.writestr("sub/data.dat", b"dat")
            archive.write_bytes(buffer.getvalue())
            context = RecordingContext(root)
            self.assertEqual(vendor_service.extract_zip(context, archive, root / "out"), 2)
            self.assertEqual((root / "out" / "sub" / "data.dat").read_bytes(), b"dat")

    def test_extract_zip_drops_a_single_root_folder(self) -> None:
        self.assertEqual(vendor_service.zip_single_root(["ThrottleStop/", "ThrottleStop/ThrottleStop.exe", "ThrottleStop/doc/readme.txt"]), "ThrottleStop")
        self.assertEqual(vendor_service.zip_single_root(["ThrottleStop/a.exe", "readme.txt"]), "")
        self.assertEqual(vendor_service.zip_single_root(["a/x.exe", "b/y.exe"]), "")
        self.assertEqual(vendor_service.zip_single_root([]), "")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "ThrottleStop_9.7.3.zip"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                bundle.writestr("ThrottleStop/", b"")
                bundle.writestr("ThrottleStop/ThrottleStop.exe", b"exe")
                bundle.writestr("ThrottleStop/doc/readme.txt", b"txt")
            archive.write_bytes(buffer.getvalue())
            context = RecordingContext(root)
            target = root / "ThrottleStop_9.7.3"
            self.assertEqual(vendor_service.extract_zip(context, archive, target), 2)
            self.assertEqual((target / "ThrottleStop.exe").read_bytes(), b"exe")
            self.assertEqual((target / "doc" / "readme.txt").read_bytes(), b"txt")
            self.assertFalse((target / "ThrottleStop").exists())

    def test_extract_zip_refuses_an_entry_outside_the_target(self) -> None:
        # Audit G1: after the common root folder is dropped, `package/../sentinel.txt` pointed at the
        # folder above the target and overwrote a file there. Now the archive is refused before writing.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("KEEP ORIGINAL", encoding="utf-8")
            archive = root / "evil.zip"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as bundle:
                bundle.writestr("package/readme.txt", b"readme")
                bundle.writestr("package/../sentinel.txt", b"OVERWRITTEN BY ARCHIVE")
            archive.write_bytes(buffer.getvalue())
            target = root / "extracted"
            with self.assertRaises(RuntimeError):
                vendor_service.extract_zip(RecordingContext(root), archive, target)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP ORIGINAL")
            self.assertFalse((target / "readme.txt").exists())

    @unittest.skipUnless(vendor_service.tar_program().is_file(), "the tar of Windows writes the 7z the test reads")
    def test_extract_7z_unpacks_as_a_zip_does(self) -> None:
        def pack(source: Path, archive: Path, *names: str) -> None:
            subprocess.run([str(vendor_service.tar_program()), "--format=7zip", "-cf", str(archive), *names], cwd=source, check=True, capture_output=True)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src" / "TestMem5" / "bin").mkdir(parents=True)
            (root / "src" / "TestMem5" / "TM5.exe").write_bytes(b"exe")
            (root / "src" / "TestMem5" / "bin" / "cfg.ini").write_bytes(b"ini")
            (root / "src" / "readme.txt").write_bytes(b"txt")
            context = RecordingContext(root)

            # One root folder: dropped, the program is at the top of its folder.
            pack(root / "src", root / "TestMem5.7z", "TestMem5")
            self.assertEqual(vendor_service.extract_7z(context, root / "TestMem5.7z", root / "one"), 2)
            self.assertEqual((root / "one" / "TM5.exe").read_bytes(), b"exe")
            self.assertEqual((root / "one" / "bin" / "cfg.ini").read_bytes(), b"ini")
            self.assertFalse((root / "one" / "TestMem5").exists())

            # A file beside the folder: nothing is dropped.
            pack(root / "src", root / "both.7z", "TestMem5", "readme.txt")
            self.assertEqual(vendor_service.extract_7z(context, root / "both.7z", root / "two"), 3)
            self.assertEqual((root / "two" / "TestMem5" / "TM5.exe").read_bytes(), b"exe")
            self.assertEqual((root / "two" / "readme.txt").read_bytes(), b"txt")

            # What is no archive is left as it came, and says so.
            (root / "broken.7z").write_bytes(b"not an archive at all")
            self.assertEqual(vendor_service.extract_7z(context, root / "broken.7z", root / "three"), 0)
            self.assertTrue((root / "broken.7z").is_file())
            self.assertFalse((root / "three").exists())
            self.assertIn("left as it came", context.lines[-1])

    def test_extract_zip_refuses_drive_and_stream_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "out"
            for bad in ("C:/Windows/evil.dll", "//server/share/x", "a/../../x", "file.txt:stream", "a/b/../../../x"):
                with self.assertRaises(ValueError, msg=bad):
                    vendor_service.zip_destination(target, bad)
            self.assertEqual(vendor_service.zip_destination(target, "sub/ok.txt"), (target / "sub" / "ok.txt").resolve())


class CountingHandler(BaseHTTPRequestHandler):
    """A server of one file: what it gives is set by the test, and every GET is counted."""

    content = b""
    gets = 0

    def log_message(self, *args: object) -> None:
        pass

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).content)))
        self.end_headers()

    def do_GET(self) -> None:
        type(self).gets += 1
        self.send_response(200)
        self.send_header("Content-Length", str(len(type(self).content)))
        self.end_headers()
        self.wfile.write(type(self).content)


class UntrustedTests(unittest.TestCase):
    """A file the checks refused does not stay under its own name, and the next run downloads anew (S1 of the audit of 10.10.2026)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.saved = (vendor_service.get_provider, vendor_service.authenticode)
        self.signed: dict[str, tuple[str, str]] = {}
        vendor_service.authenticode = lambda path: self.signed.get(path.name, ("NotSigned", ""))  # type: ignore[assignment]
        CountingHandler.gets = 0

    def tearDown(self) -> None:
        vendor_service.get_provider, vendor_service.authenticode = self.saved  # type: ignore[assignment]

    def shelf(self, *, sha256: str = "", signer: str = "", sources: list[str] | None = None) -> None:
        base = self.base
        hosts = sources

        class Shelf:
            id = "affinity"
            name = "Shelf"
            platforms = ("Windows",)

            def build_by_id(self, download_id: str) -> VendorBuild:
                return VendorBuild(vendor=self.id, product="Tool", version="1", platform="Windows", date="", download_id=download_id, name=download_id)

            def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
                return f"{base}/{build.download_id}"

            def expected_sha256(self, build: VendorBuild) -> str:
                return sha256

        made = Shelf()
        if signer:
            made.signer = signer  # type: ignore[attr-defined]
        if hosts:
            from system_core.vendors import BuildSource

            made.sources = lambda build: [BuildSource(host=host, name=build.download_id, link=lambda host=host: (f"{base}/{host}/{build.download_id}", ""))  # type: ignore[attr-defined]
                                          for host in hosts]
        vendor_service.get_provider = lambda vendor_id: made  # type: ignore[assignment]

    def run_job(self, root: Path, name: str) -> tuple[dict[str, object], RecordingContext]:
        context = RecordingContext(root)
        context.operation = Operation(id="vendor_download", title="t", description="", service="x:y",
                                      parameters={"vendor": "affinity", "vendor_versions": [name], "output_path": str(root / "store")})
        context.report_dir.mkdir(parents=True, exist_ok=True)
        return vendor_service.download_vendor_builds(context), context

    def test_a_file_whose_sha256_differs_leaves_its_name_and_the_next_run_downloads_anew(self) -> None:
        import hashlib

        good = b"the installer as its maker made it"
        self.shelf(sha256=hashlib.sha256(good).hexdigest())
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "store" / "Vendors" / "Shelf" / "tool-1.0-setup"
            CountingHandler.content = b"x" * len(good)  # damaged on the way, the same size
            with self.assertRaisesRegex(RuntimeError, r"1 of 1 build\(s\) are not to be trusted: tool-1.0-setup.exe: SHA256 differs"):
                self.run_job(Path(temp), "tool-1.0-setup.exe")
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["tool-1.0-setup.exe.untrusted"])
            self.assertEqual(CountingHandler.gets, 1)

            # The server gives the right bytes of the same size: the run asks it again and ends done.
            CountingHandler.content = good
            result, context = self.run_job(Path(temp), "tool-1.0-setup.exe")
            self.assertEqual(CountingHandler.gets, 2)
            self.assertFalse(any(line.startswith("[CACHE]") for line in context.lines))
            self.assertEqual(result["results"][0]["sha256"], "matches")  # type: ignore[index]
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["tool-1.0-setup.exe"])

    def test_a_path_too_long_for_windows_is_told_before_anything_is_asked_of_the_server(self) -> None:
        # The audit of 10.10.2026, finding 17: past 259 characters the run ended with "file not found" of a folder that was there.
        asked: list[str] = []
        self.shelf()
        provider = vendor_service.get_provider("affinity")
        resolve = provider.resolve_link
        provider.resolve_link = lambda build, registration=None: asked.append(build.name) or resolve(build, registration)  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as temp:
            deep = Path(temp) / ("a folder of the person with a long name " * 5).strip()
            context = RecordingContext(Path(temp))
            context.operation = Operation(id="vendor_download", title="t", description="", service="x:y",
                                          parameters={"vendor": "affinity", "vendor_versions": ["short.exe", "tool-1.0-setup.exe"], "output_path": str(deep)})
            context.report_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(RuntimeError, r"tool-1\.0-setup\.exe: the path of its file is \d+ characters .* Windows opens 259 at most - choose a downloads folder nearer the root"):
                vendor_service.download_vendor_builds(context)
            self.assertEqual((asked, CountingHandler.gets), ([], 0))
            self.assertFalse(deep.exists())
            self.assertFalse(any(line.startswith(("[DOWNLOAD]", "[URL]")) for line in context.lines))
        # The count is of the longest thing a download writes: the note beside the part.
        edge = Path("E:/") / ("x" * (vendor_service.MAX_PATH - len("E:\\") - len(vendor_service.PART_NOTE_SUFFIX)))
        self.assertEqual(len(str(edge) + vendor_service.PART_NOTE_SUFFIX), vendor_service.MAX_PATH)
        self.assertEqual(vendor_service.too_long(edge, "edge"), "")
        self.assertIn("260 characters", vendor_service.too_long(Path(str(edge) + "y"), "edge"))

    def test_a_file_named_by_its_address_is_measured_before_its_folder_is_made(self) -> None:
        # A name known only once the address is: the download itself refuses, and no folder is left behind.
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / ("deep " * 40).strip() / "from-the-address.exe"
            with self.assertRaisesRegex(RuntimeError, r"build: the path of its file is \d+ characters"):
                vendor_service.download_with_resume(RecordingContext(Path(temp)), f"{self.base}/from-the-address.exe", target, "build")
            self.assertEqual(CountingHandler.gets, 0)
            self.assertFalse(target.parent.exists())

    def test_an_installer_without_the_signature_leaves_its_name_and_the_next_run_downloads_anew(self) -> None:
        self.shelf(signer="Intel Corporation")
        CountingHandler.content = b"an installer"
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "store" / "Vendors" / "Shelf" / "SetupChipset_1.0"
            with self.assertRaisesRegex(RuntimeError, r"signature not confirmed \(NotSigned\)"):
                self.run_job(Path(temp), "SetupChipset_1.0.exe")
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["SetupChipset_1.0.exe.untrusted"])

            self.signed = {"SetupChipset_1.0.exe": ("Valid", "CN=Intel Corporation, O=Intel Corporation")}
            result, _context = self.run_job(Path(temp), "SetupChipset_1.0.exe")
            self.assertEqual(CountingHandler.gets, 2)
            self.assertEqual(result["results"][0]["signature"], "Valid")  # type: ignore[index]
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["SetupChipset_1.0.exe"])

    def test_a_source_whose_file_is_not_signed_hands_over_to_the_next(self) -> None:
        self.shelf(signer="Intel Corporation", sources=["first.example", "second.example"])
        CountingHandler.content = b"an installer"
        asked: list[str] = []
        verdicts = iter([("NotSigned", ""), ("Valid", "CN=Intel Corporation, O=Intel Corporation")])

        def by_turn(path: Path) -> tuple[str, str]:
            asked.append(path.name)
            return next(verdicts)

        vendor_service.authenticode = by_turn  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "Setup.exe")
            entry = result["results"][0]  # type: ignore[index]
            self.assertEqual((entry["source"], entry["signature"]), ("second.example", "Valid"))
            self.assertEqual(asked, ["Setup.exe", "Setup.exe"])
            self.assertIn("[WARN] first.example gave a file that is not to be trusted (Setup.exe: signature not confirmed (NotSigned)); the next source", context.lines)
            folder = Path(temp) / "store" / "Vendors" / "Shelf" / "Setup"
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["Setup.exe"])

    def test_a_package_with_an_unsigned_installer_never_reaches_the_folder_of_the_build(self) -> None:
        self.shelf(signer="Intel Corporation")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("Setup.exe", b"exe")
            bundle.writestr("Tool/helper.msi", b"msi")
            bundle.writestr("Readme.txt", b"txt")
        CountingHandler.content = buffer.getvalue()
        self.signed = {"Setup.exe": ("Valid", "CN=Intel Corporation, O=Intel Corporation")}  # helper.msi is nobody's
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "store" / "Vendors" / "Shelf" / "package"
            with self.assertRaisesRegex(RuntimeError, r"helper.msi: signature not confirmed"):
                self.run_job(Path(temp), "package.zip")
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["_untrusted"])
            self.assertTrue((folder / "_untrusted" / "Tool" / "helper.msi").is_file())

            # Signed all through: its files are in the folder of the build, and nothing of the refused try is kept.
            self.signed["helper.msi"] = self.signed["Setup.exe"]
            result, _context = self.run_job(Path(temp), "package.zip")
            self.assertEqual((result["results"][0]["extracted"], result["results"][0]["signature"]), (3, "Valid"))  # type: ignore[index]
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["Readme.txt", "Setup.exe", "Tool"])


class ForeignNameTests(unittest.TestCase):
    """A file name that comes from foreign data is checked before anything is written (S2 of the audit of 10.10.2026)."""

    def test_a_name_is_a_name_never_a_path(self) -> None:
        for good in ("rufus-4.15.exe", "Affinity%20x64.exe", "Modrinth App_0.21.9_x64-setup.exe", "TcNo.Account.Switcher.-.Installer_2025-11-20_03.exe", "a..b"):
            self.assertEqual(vendor_service.safe_file_name(good), good)
        for bad in ("", ".", "..", r"..\..\x.txt", "../x.txt", "a/b.exe", "C:x.exe", "file.txt:stream", "..%5C..%5Cx.txt", "%2E%2E", "name.", "name ", "a\x07b"):
            with self.assertRaisesRegex(RuntimeError, "unsafe file name", msg=bad):
                vendor_service.safe_file_name(bad)

    def test_a_source_cannot_name_a_file_or_a_folder_outside_the_build(self) -> None:
        from system_core.vendors import BuildSource

        fetched: list[Path] = []

        def fake_download(context: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
            fetched.append(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return target

        saved = vendor_service.download_with_resume
        vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                folder = root / "Vendors" / "Shelf" / "build"
                context = RecordingContext(root)
                by_name = [BuildSource("a.example", r"..\..\escaped-by-name.txt", lambda: ("https://a.example/file.exe", ""))]
                by_address = [BuildSource("a.example", "", lambda: ("https://a.example/..%5C..%5Cescaped-by-url.txt", ""))]
                for sources in (by_name, by_address):
                    with self.assertRaisesRegex(RuntimeError, "unsafe file name"):
                        vendor_service.download_from_sources(context, sources, folder, "build")
                self.assertEqual(fetched, [])
                self.assertEqual([item.name for item in root.rglob("*escaped*")], [])
                for bad in (r"..\up", "a/../../up", "C:/Windows", "//server/share"):
                    with self.assertRaisesRegex(RuntimeError, "unsafe folder name", msg=bad):
                        vendor_service.safe_folder(root / "Vendors" / "Shelf", bad)
                self.assertEqual(vendor_service.safe_folder(root / "Vendors" / "Shelf", "Rufus/Portable"), (root / "Vendors" / "Shelf" / "Rufus" / "Portable").resolve())
        finally:
            vendor_service.download_with_resume = saved  # type: ignore[assignment]


class JobTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (BlackmagicProvider._instance, AffinityProvider._instance)
        BlackmagicProvider._instance = blackmagic_with(FakeBlackmagicHttp(free_ids={"free-2104-win"}))
        AffinityProvider._instance = affinity_with(FakeAffinityHttp())

    def tearDown(self) -> None:
        BlackmagicProvider._instance, AffinityProvider._instance = self._saved

    def _context(self, root: Path, **parameters: object) -> RecordingContext:
        context = RecordingContext(root)
        operation = Operation(id="vendor_link", title="t", description="", service="x:y", parameters=dict(parameters))
        context.operation = operation
        context.report_dir.mkdir(parents=True, exist_ok=True)
        return context

    def test_link_job_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = self._context(root, vendor="blackmagic", vendor_versions=["studio-2104-win", "studio-2032-win"])
            result = vendor_service.link_vendor_builds(context)
            self.assertEqual(len(result["links"]), 2)
            report = Path(str(result["report"]))
            self.assertIn("studio-2032-win.zip", report.read_text(encoding="utf-8"))

    def test_link_job_explains_the_missing_form(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            context = self._context(Path(temp), vendor="blackmagic", vendor_versions=["free-2104-win"])
            with self.assertRaisesRegex(RuntimeError, "registration form"):
                vendor_service.link_vendor_builds(context)

    def test_download_job_folders_follow_vendor_and_archive_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captured: dict[str, object] = {}

            def fake_download(ctx: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
                captured["target"] = target
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"zip")
                return target

            original = vendor_service.download_with_resume
            vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
            try:
                context = self._context(root, vendor="blackmagic", vendor_versions=["studio-2104-win"], output_path=str(root / "store"), vendor_extract=False)
                result = vendor_service.download_vendor_builds(context)
                self.assertEqual(captured["target"], root / "store" / "Vendors" / "Blackmagic Design" / "studio-2104-win" / "studio-2104-win.zip")
                self.assertEqual(result["downloaded"], 1)

                flat = self._context(root, vendor="blackmagic", vendor_versions=["studio-2104-win"], output_path=str(root / "store"), vendor_extract=False, vendor_flat=True)
                vendor_service.download_vendor_builds(flat)
                self.assertEqual(captured["target"], root / "store" / "studio-2104-win" / "studio-2104-win.zip")

                affinity = self._context(root, vendor="affinity", vendor_versions=["https://downloads.affinity.studio/Affinity%20x64.exe"], output_path=str(root / "store"))
                vendor_service.download_vendor_builds(affinity)
                self.assertEqual(captured["target"], root / "store" / "Vendors" / "Affinity" / "Affinity x64" / "Affinity x64.exe")
            finally:
                vendor_service.download_with_resume = original  # type: ignore[assignment]

    @unittest.skipUnless(vendor_service.tar_program().is_file(), "the tar of Windows writes the 7z the test reads")
    def test_download_job_unpacks_a_7z_and_keeps_one_it_cannot_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src" / "Tool").mkdir(parents=True)
            (root / "src" / "Tool" / "tool.exe").write_bytes(b"exe")
            subprocess.run([str(vendor_service.tar_program()), "--format=7zip", "-cf", str(root / "good.7z"), "Tool"], cwd=root / "src", check=True, capture_output=True)
            payload = {"good": (root / "good.7z").read_bytes(), "bad": b"not an archive at all"}

            def fake_download(ctx: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload["bad" if "bad" in url else "good"])
                return target

            class Shelf:
                """A vendor whose builds are whatever address is asked for."""

                id = "affinity"
                name = "Shelf"
                platforms = ("Windows",)

                def build_by_id(self, download_id: str) -> VendorBuild:
                    return VendorBuild(vendor=self.id, product="Tool", version="1", platform="Windows", date="", download_id=download_id, name=download_id.rsplit("/", 1)[-1])

                def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
                    return build.download_id

            original = (vendor_service.download_with_resume, vendor_service.get_provider)
            vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
            vendor_service.get_provider = lambda vendor_id: Shelf()  # type: ignore[assignment]
            try:
                store = root / "store"
                ids = ["https://shelf.example/good.7z", "https://shelf.example/bad.7z"]
                result = vendor_service.download_vendor_builds(self._context(root, vendor="affinity", vendor_versions=ids, output_path=str(store)))
                good, bad = result["results"]  # type: ignore[misc]
                self.assertEqual((good["extracted"], good["archive"]), (1, ""))
                self.assertEqual((store / "Vendors" / "Shelf" / "good" / "tool.exe").read_bytes(), b"exe")
                self.assertFalse((store / "Vendors" / "Shelf" / "good" / "good.7z").exists())
                self.assertEqual(bad["extracted"], 0)
                self.assertTrue((store / "Vendors" / "Shelf" / "bad" / "bad.7z").is_file())
            finally:
                vendor_service.download_with_resume, vendor_service.get_provider = original  # type: ignore[assignment]


class MakerOfThisPcTests(unittest.TestCase):
    """A page of drivers opens on the maker this PC has a device of."""

    WIFI = [
        {"id": "intel", "values": {"vendor": "techpowerup", "tpu_kind": "drivers", "tpu_driver": "Intel Wi-Fi"}},
        {"id": "realtek", "values": {"vendor": "mscatalog", "msc_kind": "wifi", "msc_wifi": "Realtek Wi-Fi"}},
        {"id": "mediatek", "values": {"vendor": "mscatalog", "msc_kind": "wifi", "msc_wifi": "MediaTek Wi-Fi"}},
    ]
    GPU = [{"id": "nvidia", "values": {"vendor": "nvidia"}},
           {"id": "amd", "values": {"vendor": "techpowerup", "tpu_kind": "drivers", "tpu_driver": "AMD Radeon Graphics"}}]

    def chosen(self, devices: list, choices: list) -> str:
        from system_core.core import pc_devices

        kept = vendor_service._this_pc
        vendor_service._this_pc = pc_devices.Snapshot(lambda: devices)
        try:
            return vendor_service.choice_for_this_pc(choices)
        finally:
            vendor_service._this_pc = kept

    def test_the_maker_of_the_device_of_this_pc_is_chosen(self) -> None:
        from system_core.core import pc_devices

        realtek = pc_devices.Device(r"PCI\VEN_10EC&DEV_C852&SUBSYS_1&REV_00\4&1", "Realtek RTL8852CE WiFi 6E PCI-E NIC", (r"PCI\VEN_10EC&CC_028000", r"PCI\CC_0280"), "6001.16.1.0")
        mediatek = pc_devices.Device(r"PCI\VEN_14C3&DEV_0616&SUBSYS_1&REV_00\4&1", "MediaTek Wi-Fi 6E MT7922", (r"PCI\VEN_14C3&CC_028000", r"PCI\CC_0280"), "3.4.0.1")
        card = pc_devices.Device(r"PCI\VEN_10DE&DEV_2786&SUBSYS_1\4&1", "NVIDIA GeForce RTX 4070", (r"PCI\VEN_10DE&CC_030000",), "32.0.15.9597")
        self.assertEqual(self.chosen([realtek], self.WIFI), "realtek")
        self.assertEqual(self.chosen([mediatek, card], self.WIFI), "mediatek")
        # Two adapters: the first of the row is the one - the order of the page is the order of the makers.
        self.assertEqual(self.chosen([mediatek, realtek], self.WIFI), "realtek")
        self.assertEqual(self.chosen([card], self.GPU), "nvidia")
        # No device of any maker of the page: nothing is named, and the page opens on its first.
        self.assertEqual(self.chosen([card], self.WIFI), "")
        self.assertEqual(self.chosen([], self.GPU), "")
        # A choice that names a product nobody knows is passed by, not an error.
        self.assertEqual(self.chosen([realtek], [{"id": "odd", "values": {"vendor": "techpowerup", "tpu_driver": "No such"}}, *self.WIFI]), "realtek")

    def test_a_page_opens_on_its_first_maker_when_the_machine_cannot_be_asked(self) -> None:
        from system_core.core import window_layout

        choices = window_layout.page_choices({"choices": [{"id": "a", "values": {"vendor": "nvidia"}}, {"id": "b", "values": {"vendor": "nvidia"}}, "not a choice", {"title": "no id"}]})
        self.assertEqual([choice["id"] for choice in choices], ["a", "b"])
        self.assertEqual(window_layout.chosen({"choose": "no_such_module:function"}, choices), "a")
        self.assertEqual(window_layout.chosen({"choose": "system_core.services.vendor_service:no_such_function"}, choices), "a")
        self.assertEqual(window_layout.chosen({}, choices), "a")
        self.assertEqual(window_layout.chosen({"choose": "system_core.services.vendor_service:choice_for_this_pc"}, []), "")


class MachineStandingTests(unittest.TestCase):
    """The badge over a list of drivers: the version this PC runs, the newest of the list, behind or not."""

    class Source:
        """A provider as the service sees it: what this PC runs, the line over the list, and maybe lines of drivers."""

        def __init__(self, running: list[str], *, numbers_of_windows: bool = False, lines: bool = False) -> None:
            self._running = running
            if numbers_of_windows:
                self.numbers_of_windows = True
            if lines:
                self.line = lambda product, version: str(version).split(".", 1)[0]

        def running(self, product: str) -> list[str]:
            return list(self._running)

        def note(self, product: str) -> tuple[str, str]:
            what = "; ".join(f"Card · {version}" for version in self._running) or "Card"
            return (f"Source · this PC: {what}", f"Источник · этот ПК: {what}")

    @staticmethod
    def builds(*rows: str) -> list[VendorBuild]:
        """Rows are "version" or "version|notes"; a version that ends with "b" is a preview."""
        made = []
        for row in rows:
            version, _, notes = row.partition("|")
            made.append(VendorBuild(vendor="x", product="p", version=version.rstrip("b"), platform="Windows", date="", download_id=version,
                                    name=version, notes=notes, preview=version.endswith("b")))
        return made

    def standing(self, source, *rows: str):
        return vendor_service.machine_standing(source, "p", self.builds(*rows))

    def test_the_driver_of_the_pc_is_found_in_the_list_by_its_mark(self) -> None:
        source = self.Source(["32.0.101.7085"])
        # It runs the newest: not behind. The number is the list's own, not the one Windows writes.
        self.assertEqual(self.standing(source, "101.7085|installed", "101.7000"),
                         {"running": "101.7085", "newest": "101.7085", "behind": False, "said": ["32.0.101.7085"]})
        # A newer release stands above it: behind.
        self.assertEqual(self.standing(source, "101.8991", "101.7085|installed"),
                         {"running": "101.7085", "newest": "101.8991", "behind": True, "said": ["32.0.101.7085"]})
        # What stands above it is a preview, and the newest release is the one it runs: not behind.
        told = self.standing(source, "101.9000b", "101.7085|installed", "101.7000")
        self.assertEqual((told["newest"], told["behind"]), ("101.7085", False))
        # It runs the preview itself, above the newest release: not behind either.
        told = self.standing(source, "101.9000b|installed", "101.7085")
        self.assertEqual((told["running"], told["newest"], told["behind"]), ("101.9000", "101.7085", False))

    def test_a_driver_the_list_does_not_hold_is_judged_only_where_the_numbers_are_the_same_scheme(self) -> None:
        # AMD's driver 32.x says nothing about Adrenalin 25.x: no badge.
        self.assertIsNone(self.standing(self.Source(["32.0.21025.1"]), "25.10.2", "25.9.1"))
        # The Microsoft catalog and NVIDIA write the numbers Windows shows: compared as numbers.
        same = self.Source(["6.0.9764.1"], numbers_of_windows=True)
        self.assertEqual(self.standing(same, "6.0.10029.1", "6.0.10013.1"),
                         {"running": "6.0.9764.1", "newest": "6.0.10029.1", "behind": True, "said": ["6.0.9764.1"]})
        ahead = self.Source(["6.0.10100.1"], numbers_of_windows=True)
        self.assertFalse(self.standing(ahead, "6.0.10029.1")["behind"])
        # 10029 is newer than 9764 as a number, not as a word.
        self.assertTrue(self.standing(self.Source(["6.0.9764.1"], numbers_of_windows=True), "6.0.10029.1")["behind"])
        # Two devices: the newer of their drivers is the one shown.
        two = self.Source(["6.0.9000.1", "6.0.9764.1"], numbers_of_windows=True)
        self.assertEqual(self.standing(two, "6.0.10029.1")["running"], "6.0.9764.1")

    def test_a_driver_is_measured_against_its_own_line(self) -> None:
        # Realtek: a line is a chip. The PC runs the newest 1127; the 1168 above it is another card's.
        lined = self.Source(["1127.20.1"], numbers_of_windows=True, lines=True)
        told = self.standing(lined, "1168.30.1", "1127.20.1|installed", "1127.10.1")
        self.assertEqual((told["running"], told["newest"], told["behind"]), ("1127.20.1", "1127.20.1", False))
        told = self.standing(lined, "1168.30.1", "1127.25.1", "1127.20.1|installed")
        self.assertEqual((told["newest"], told["behind"]), ("1127.25.1", True))
        # A line the list does not hold at all: nothing to measure against.
        self.assertIsNone(self.standing(self.Source(["1001.1.1"], numbers_of_windows=True, lines=True), "1168.30.1"))
        # Without lines the newest of the whole list is the measure.
        plain = self.Source(["1127.20.1"], numbers_of_windows=True)
        self.assertEqual(self.standing(plain, "1168.30.1", "1127.20.1|installed")["newest"], "1168.30.1")

    def test_no_device_no_badge(self) -> None:
        self.assertIsNone(self.standing(self.Source([]), "1.0|installed"))
        self.assertIsNone(vendor_service.machine_standing(self.Source(["1.0"], numbers_of_windows=True), "p", []))
        # A provider that does not say what the PC runs has no badge.
        self.assertIsNone(vendor_service.machine_standing(object(), "p", self.builds("1.0|installed")))  # type: ignore[arg-type]
        # A list of previews alone has no newest release to measure against.
        self.assertIsNone(self.standing(self.Source(["1.0"], numbers_of_windows=True), "2.0b"))

    def test_the_line_over_the_list_carries_the_badge_and_does_not_repeat_the_version(self) -> None:
        source = self.Source(["6.0.9764.1"], numbers_of_windows=True)
        over = vendor_service.note_option(source, "p", self.builds("6.0.10029.1", "6.0.10013.1"))
        self.assertEqual(over, {"value": "", "label": "Source · this PC: Card", "label_ru": "Источник · этот ПК: Card", "header": True, "note": True,
                                "running": "6.0.9764.1", "newest": "6.0.10029.1", "behind": True})
        # No badge - the line stays as the provider wrote it, the version in it.
        bare = vendor_service.note_option(self.Source(["32.0.21025.1"]), "p", self.builds("25.10.2"))
        self.assertEqual((bare["label"], "running" in bare), ("Source · this PC: Card · 32.0.21025.1", False))
        # A provider with nothing to say over its list has no line at all.
        self.assertIsNone(vendor_service.note_option(object(), "p", self.builds("1.0")))  # type: ignore[arg-type]

    def test_the_three_sources_of_drivers_say_what_the_pc_runs(self) -> None:
        # The providers whose lists are for the devices of this PC answer the question the badge asks.
        for name in ("mscatalog", "techpowerup", "nvidia"):
            provider = get_provider(name)
            self.assertTrue(callable(getattr(provider, "running", None)), name)
            self.assertEqual(provider.running("no such product"), [], name)
        # ... and the two that write the numbers Windows shows say so.
        self.assertTrue(get_provider("mscatalog").numbers_of_windows and get_provider("nvidia").numbers_of_windows)
        self.assertFalse(getattr(get_provider("techpowerup"), "numbers_of_windows", False))
        self.assertEqual((get_provider("mscatalog").line("Realtek LAN", "1168.30.1"), get_provider("mscatalog").line("no such", "1.2")), ("1168", ""))


if __name__ == "__main__":
    unittest.main()
