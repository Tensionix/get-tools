"""Vendor providers: the Blackmagic catalog, the Affinity pages, link resolution, the resumable download."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
from system_core.vendors import RegistrationRequired, get_provider  # noqa: E402
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

    return NvidiaProvider(
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
        self.assertEqual(win11[0].label, "24H2 26100.9278 - 27 Aug 2026 - ru-ru core")
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
        self.assertEqual(builds[0].notes, "en-us professional +Enterprise +IoTEnterprise")
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
            labels = [o["label"] for o in options]
            self.assertTrue(all(label.startswith("★ Windows 11 ") for label in labels), labels)
            self.assertIn("24H2", labels[0])
            self.assertIn("latest", labels[0])
            self.assertIn("23H2", labels[1])
        finally:
            UupDumpProvider._instance = saved

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
        # network classes first; of two ibtusb.inf versions only the newest is ticked; firmware last and unticked
        self.assertEqual([o["value"] for o in options], ["oem22.inf", "oem90.inf", "oem74.inf", "oem41.inf"])
        self.assertEqual([o["default"] for o in options], [False, True, True, False])
        self.assertEqual(options[2]["label_ru"], "Сеть: netwtw6e.inf · Intel 24.40.0.4")
        self.assertEqual(options[3]["label"], "Firmware: 5b10w13975.inf · Lenovo Ltd. 12/26/2022 265.0.0.2")

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

    def test_adk_version_build_reads_the_kit_build(self) -> None:
        self.assertEqual(vendor_service.adk_version_build("10.1.22621.5337"), 22621)
        self.assertEqual(vendor_service.adk_version_build("10.1.26100.2454"), 26100)
        self.assertEqual(vendor_service.adk_version_build(""), 0)
        self.assertEqual(vendor_service.adk_version_build("garbage"), 0)

    def test_download_gate_waits_for_the_adk_only_for_windows(self) -> None:
        original = vendor_service.adk_dism_path
        try:
            vendor_service.adk_dism_path = lambda: None  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "gate_adk_missing")
            self.assertEqual(vendor_service.download_gate({"vendor": "nvidia"}), "")
            vendor_service.adk_dism_path = lambda: Path("C:/adk/dism.exe")  # type: ignore[assignment]
            self.assertEqual(vendor_service.download_gate({"vendor": "uupdump"}), "")
        finally:
            vendor_service.adk_dism_path = original  # type: ignore[assignment]

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
            root = Path(temp)
            self.assertEqual(tools.find_powershell(root, which=lambda name: r"C:\pwsh\pwsh.exe" if name.startswith("pwsh") else None), [r"C:\pwsh\pwsh.exe"])
            portable = root / "system_core" / "powershell"
            portable.mkdir(parents=True)
            (portable / "pwsh.exe").write_bytes(b"")
            self.assertEqual(tools.find_powershell(root, which=lambda name: None), [str(portable / "pwsh.exe")])
            fallback = tools.find_powershell(
                Path(temp) / "elsewhere",
                which=lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if name == "powershell.exe" else None,
                program_files=Path(temp) / "no-program-files",
            )
            self.assertTrue(fallback[0].endswith("powershell.exe"))

    def test_windows_terminal_wraps_the_shell_when_present(self) -> None:
        from system_core.services import external_tools_service as tools

        argv = tools.terminal_launch_command(["pwsh.exe"], "irm x | iex", "WinUtil", which=lambda name: r"C:\wt\wt.exe" if name == "wt.exe" else None)
        self.assertEqual(argv[:5], [r"C:\wt\wt.exe", "-w", "new", "--title", "WinUtil"])
        self.assertEqual(argv[5:], ["pwsh.exe", "-NoLogo", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "irm x | iex"])
        plain = tools.terminal_launch_command(["powershell.exe"], "irm x | iex", "WinUtil", which=lambda name: None)
        self.assertEqual(plain[:4], ["cmd.exe", "/c", "start", "WinUtil"])
        self.assertEqual(plain[-1], "irm x | iex")


# ----- resumable download against a local range-aware server -----

PAYLOAD = bytes(range(256)) * 4096  # 1 MiB, distinct bytes


class RangeHandler(BaseHTTPRequestHandler):
    supports_range = True
    requests: list[str] = []

    def log_message(self, *_args: object) -> None:  # keep the test output quiet
        return

    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        range_header = self.headers.get("Range")
        RangeHandler.requests.append(range_header or "")
        if range_header and self.supports_range:
            start = int(range_header.split("=")[1].split("-")[0])
            body = PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            body = PAYLOAD
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TechPowerUpVendorTests(unittest.TestCase):
    def test_products_list_versions_from_the_mirrored_pages_and_resolve_signed_links(self) -> None:
        from system_core.vendors.techpowerup import KINDS, PRODUCTS, TechPowerUpProvider
        from system_core.services.techpowerup_service import parse_versions
        calls: list[str] = []

        def fake_list(slug: str):
            calls.append(slug)
            return parse_versions(TPU_DDU_PAGE)

        provider = TechPowerUpProvider(tpu_list=fake_list, tpu_resolve=lambda slug, file_id: f"https://us1-dl.techpowerup.com/{slug}/{file_id}.exe?sig=1")
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
        TechPowerUpProvider._instance = TechPowerUpProvider(tpu_list=lambda slug: parse_versions(TPU_DDU_PAGE), tpu_resolve=lambda slug, file_id: "")
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
        vendor_service.CHUNK_BYTES = 64 * 1024

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
            self.assertTrue(any(request.startswith("bytes=") for request in RangeHandler.requests))
            self.assertTrue(any(line.startswith("[RESUME]") for line in second.lines))

    def test_complete_file_is_not_fetched_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "build.zip"
            target.write_bytes(PAYLOAD)
            context = RecordingContext(root)
            vendor_service.download_with_resume(context, self.url, target, "build")
            self.assertEqual(RangeHandler.requests, [])
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


if __name__ == "__main__":
    unittest.main()
