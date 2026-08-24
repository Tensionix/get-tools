"""Visual C++ runtime version normalization and benign installer exit codes."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_core.services.vcredist_service import normalize_runtime_version  # noqa: E402
from system_core.services.winget_service import (  # noqa: E402
    BENIGN_INSTALLER_EXIT_CODES,
    _unsigned_exit_code,
)


class RuntimeVersionTests(unittest.TestCase):
    def test_registry_and_winget_spellings_are_the_same_build(self) -> None:
        """`VC\\Runtimes` writes `v14.51.36247.00`, WinGet reports `14.51.36247.0`."""
        self.assertEqual(
            normalize_runtime_version("v14.51.36247.00"),
            normalize_runtime_version("14.51.36247.0"),
        )
        self.assertEqual(normalize_runtime_version("v14.51.36247.00"), "14.51.36247")

    def test_trailing_zero_components_do_not_change_the_build(self) -> None:
        self.assertEqual(normalize_runtime_version("12.0.40664.0"), "12.0.40664")
        self.assertEqual(normalize_runtime_version("14.0"), "14")

    def test_empty_and_non_numeric_values_survive(self) -> None:
        self.assertEqual(normalize_runtime_version(""), "")
        self.assertEqual(normalize_runtime_version("   "), "")
        self.assertEqual(normalize_runtime_version("v14.51.beta"), "14.51.beta")


class InstallerExitCodeTests(unittest.TestCase):
    def test_hresult_is_read_back_from_the_signed_form(self) -> None:
        """Python reports `0x80070666` as a negative number."""
        self.assertEqual(_unsigned_exit_code(-2147023258), 0x80070666)
        self.assertIn(_unsigned_exit_code(-2147023258), BENIGN_INSTALLER_EXIT_CODES)

    def test_product_version_and_reboot_codes_are_not_failures(self) -> None:
        self.assertEqual(BENIGN_INSTALLER_EXIT_CODES[1638][0], "already_installed")
        self.assertEqual(BENIGN_INSTALLER_EXIT_CODES[3010][0], "reboot_required")
        self.assertEqual(BENIGN_INSTALLER_EXIT_CODES[1641][0], "reboot_required")

    def test_plain_codes_pass_through(self) -> None:
        self.assertEqual(_unsigned_exit_code(0), 0)
        self.assertEqual(_unsigned_exit_code(1), 1)
        self.assertNotIn(1, BENIGN_INSTALLER_EXIT_CODES)


if __name__ == "__main__":
    unittest.main()
