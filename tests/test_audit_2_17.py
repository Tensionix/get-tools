"""Regressions of the 2.17.0 audit (E:/TOOLS/AUDIT/Audion-Get-Tools-AUDIT.md): the auditor's own scenarios.

G1 (a zip entry outside the target) lives with the other extraction tests in test_vendors.py.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from system_core.core.jobs import execute_operation
from system_core.core.manifest import Operation
from system_core.core.paths import ensure_project_dirs, get_project_paths
from system_core.services import portable_browser_service as browsers
from system_core.services import winget_service as winget


def operation(name: str, service: str, params: dict) -> Operation:
    return Operation(id=name, title=name, description="audit regression", service=service, parameters=params)


class AuditCase(unittest.TestCase):
    def setUp(self) -> None:
        # A folder with a space in it, as the project's own path has.
        self._temp = tempfile.TemporaryDirectory(prefix="audion audit ")
        self.paths = get_project_paths(Path(self._temp.name) / "case root")
        ensure_project_dirs(self.paths)

    def tearDown(self) -> None:
        self._temp.cleanup()


@unittest.skipUnless(sys.platform == "win32", "CMD and job objects are Windows")
class CancelTests(AuditCase):
    """G2: STOP is seen while the process is silent, and the whole tree stops."""

    def _run_with_stop(self, script_body: str, stop_after: float):
        root = self.paths.root
        (root / "child.py").write_text(script_body, encoding="utf-8")
        (root / "run.cmd").write_text(f'@echo off\n"{sys.executable}" "%~dp0child.py"\n', encoding="utf-8")
        cancel = threading.Event()
        timer = threading.Timer(stop_after, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            op = operation("stop", "system_core.services.winget_service:terminal_command", {"shell": "cmd", "command": "call run.cmd"})
            result = execute_operation(self.paths, op, cancel_callback=cancel.is_set)
        finally:
            timer.join()
        return result, time.monotonic() - started

    def test_a_silent_process_is_stopped_at_once(self) -> None:
        body = 'import time\nfrom pathlib import Path\ntime.sleep(2)\nPath(__file__).with_name("after_stop.txt").write_text("written after stop")\n'
        result, elapsed = self._run_with_stop(body, 0.35)
        self.assertFalse(result.ok)
        self.assertLess(elapsed, 1.8)
        time.sleep(2.2)
        self.assertFalse((self.paths.root / "after_stop.txt").exists())

    def test_the_child_of_cmd_does_not_outlive_stop(self) -> None:
        body = 'import time\nfrom pathlib import Path\ntime.sleep(0.7)\nprint("wake reader", flush=True)\ntime.sleep(1.2)\nPath(__file__).with_name("survived.txt").write_text("child survived stop")\n'
        result, _ = self._run_with_stop(body, 0.3)
        self.assertFalse(result.ok)
        time.sleep(2.5)
        self.assertFalse((self.paths.root / "survived.txt").exists())


@unittest.skipUnless(sys.platform == "win32", "CMD quoting is Windows")
class CmdQuotingTests(AuditCase):
    """G4: quotes and spaces reach CMD as written."""

    def test_a_quoted_path_in_a_manual_command(self) -> None:
        script = self.paths.root / "hello.py"
        script.write_text('from pathlib import Path\nPath(__file__).with_name("quoted-ran.txt").write_text("ok")\n', encoding="utf-8")
        op = operation("quotes", "system_core.services.winget_service:terminal_command", {"shell": "cmd", "command": f'"{sys.executable}" "{script}"'})
        result = execute_operation(self.paths, op)
        self.assertTrue(result.ok, result.message)
        self.assertTrue((self.paths.root / "quoted-ran.txt").exists())

    def test_a_classic_script_in_a_folder_with_spaces(self) -> None:
        batch = self.paths.root / "hello script.cmd"
        batch.write_text('@echo off\necho AUDIT_BATCH_OK>"%~dp0batch-ran.txt"\n', encoding="ascii")
        op = operation("classic", "system_core.services.winget_service:run_cmd_operation", {"script": str(batch), "args": ["a b", "x&y"]})
        result = execute_operation(self.paths, op)
        self.assertTrue(result.ok, result.message)
        self.assertTrue((self.paths.root / "batch-ran.txt").exists())

    def test_cmd_quote(self) -> None:
        self.assertEqual(winget.cmd_quote("plain"), "plain")
        self.assertEqual(winget.cmd_quote("a b"), '"a b"')
        self.assertEqual(winget.cmd_quote("x&y"), '"x&y"')
        self.assertEqual(winget.cmd_quote(""), '""')


class _Truncating(BaseHTTPRequestHandler):
    full = False
    requests = 0

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        type(self).requests += 1
        body = b"A" * (4096 if type(self).full else 1024)
        self.send_response(200)
        self.send_header("Content-Length", "4096")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, *_args) -> None:
        pass


class TruncatedDownloadTests(AuditCase):
    """G3: a short body is not a download, and a retry fetches the whole file."""

    def test_short_body_fails_and_the_retry_downloads_again(self) -> None:
        _Truncating.full = False
        _Truncating.requests = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Truncating)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            op = operation("chrome", "system_core.services.portable_browser_service:download_google_chrome_web", {})
            with patch.object(browsers, "CHROME_WEB_INSTALLER_URL", f"http://127.0.0.1:{server.server_port}/fixture.bin"):
                first = execute_operation(self.paths, op)
                _Truncating.full = True
                second = execute_operation(self.paths, op)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertFalse(first.ok)
        self.assertTrue(second.ok, second.message)
        self.assertEqual(second.data.get("size"), 4096)
        self.assertEqual(_Truncating.requests, 2)


class WingetDownloadErrorTests(AuditCase):
    """G5: a failed winget download is a failure even when a dependency was left behind."""

    def test_a_dependency_alone_is_not_done(self) -> None:
        def failed_winget(context, arguments, **kwargs):
            target = Path(arguments[arguments.index("--download-directory") + 1])
            (target / "Dependencies").mkdir(parents=True, exist_ok=True)
            (target / "Dependencies" / "dependency.msix").write_bytes(b"fixture dependency, main installer absent")
            return winget.ProcessResult(exit_code=1, lines=("download failed",))

        op = operation("download", "system_core.services.winget_service:download_package", {"download_package_id": "Audit.Fixture"})
        with patch.object(winget, "_run_winget", failed_winget), patch.object(winget, "package_installer_github", return_value=None), \
                patch.object(winget, "_github_repo_for_package", return_value=None):
            result = execute_operation(self.paths, op)
        self.assertFalse(result.ok)
        self.assertIn("dependency.msix", result.message)


if __name__ == "__main__":
    unittest.main()
