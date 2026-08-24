from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.argv[0] = str(Path(__file__).resolve())

from system_core.ui_nicegui import app as gui_app
from system_core.ui_nicegui import window
from system_core.ui_nicegui.workbench import WorkbenchConfig, WorkbenchHistory


EXPECTED_WORKBENCH_SHA256 = "81695288AE6C85C53FAADCE72BD056F4F4F974FE68E0FA561FFCF396E4A31730"


class WorkbenchIntegrationTests(unittest.TestCase):
    def test_canonical_module_hash(self) -> None:
        path = Path(gui_app.__file__).with_name("workbench.py")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest().upper(), EXPECTED_WORKBENCH_SHA256)

    def test_history_reset_keeps_pins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input"
            target = root / "output"
            source.mkdir()
            target.mkdir()
            history = WorkbenchHistory(
                WorkbenchConfig(root, source, target, root / "history.json", history_limit=24)
            )
            history.ensure_initial()
            external = root / "external source"
            history.remember("source", str(external))
            history.set_pinned("source", str(external), True, required_message="required")
            result = history.clear_cache_keep_pins()
            self.assertGreaterEqual(result["removed_sources"], 1)
            self.assertEqual(result["kept_pins"], 1)
            self.assertEqual([item["path"] for item in history.entries("source")], [str(external)])
            self.assertEqual(history.delete("source", str(external), required_message="required")["removed"], 1)

    def test_delete_helpers_are_guarded_and_file_aware(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "source"
            folder.mkdir()
            (folder / ".gitkeep").touch()
            (folder / "one.txt").write_text("one", encoding="utf-8")
            nested = folder / "nested"
            nested.mkdir()
            (nested / "two.txt").write_text("two", encoding="utf-8")
            result = gui_app.delete_workspace_path_contents(folder)
            self.assertEqual(result["kind"], "folder")
            # input and output must end up genuinely empty, so .gitkeep goes too
            self.assertFalse((folder / ".gitkeep").exists())
            self.assertFalse((folder / "one.txt").exists())
            self.assertFalse(nested.exists())

            selected_file = root / "selected.txt"
            selected_file.write_text("selected", encoding="utf-8")
            self.assertEqual(gui_app.delete_workspace_path_contents(selected_file)["kind"], "file")
            self.assertFalse(selected_file.exists())

            same = root / "same"
            same.mkdir()
            (same / "payload.txt").write_text("payload", encoding="utf-8")
            both = gui_app.delete_workspace_io_contents(same, same)
            self.assertEqual(both["source"]["kind"], "folder")
            self.assertEqual(both["target"]["kind"], "same")
            with self.assertRaises(RuntimeError):
                gui_app.validate_workspace_delete_target(gui_app.ROOT)

    def test_single_file_source_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "package.txt"
            source.write_text("package", encoding="utf-8")
            lines = gui_app.input_file_list_lines(source)
            self.assertEqual(lines[-1], "001. package.txt")

    def test_second_picker_is_rejected(self) -> None:
        self.assertTrue(gui_app._PICKER_RUN_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "already open"):
                gui_app.run_picker_script("", "failed")
        finally:
            gui_app._PICKER_RUN_LOCK.release()

    def test_removed_dispatchers_are_absent(self) -> None:
        from system_core.services import winget_service

        self.assertFalse(hasattr(winget_service, "run_single_package"))
        self.assertFalse(hasattr(winget_service, "uninstall_installed_package"))

    def test_remote_host_guard(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDION_ALLOW_REMOTE_GUI", None)
            window.assert_gui_host_allowed("127.0.0.1")
            window.assert_gui_host_allowed("::1")
            with self.assertRaises(SystemExit):
                window.assert_gui_host_allowed("0.0.0.0")

    @unittest.skipUnless(os.name == "nt", "Windows desktop wrapper test")
    def test_server_process_tree_stops(self) -> None:
        host = "127.0.0.1"
        port = window.choose_port(host, 18765)
        server = window.start_server(host, port)
        try:
            self.assertIsNotNone(server)
            self.assertTrue(window.wait_for_server(host, port, timeout=20.0))
        finally:
            window.stop_server(server)
        self.assertIsNotNone(server.poll() if server else None)
        self.assertFalse(window.GUI_PID_FILE.exists())


if __name__ == "__main__":
    unittest.main()
