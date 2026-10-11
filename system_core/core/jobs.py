from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import importlib
import json
import os
import subprocess
import traceback

from .logging_utils import append_log, timestamp
from .manifest import Operation
from .paths import ProjectPaths


LogCallback = Callable[[str], None]
ProgressCallback = Callable[[float], None]
CancelCallback = Callable[[], bool]
ActivityCallback = Callable[[str], None]


@dataclass
class JobContext:
    paths: ProjectPaths
    operation: Operation
    log_file: Path
    report_dir: Path
    log_callback: LogCallback | None = None
    progress_callback: ProgressCallback | None = None
    cancel_callback: CancelCallback | None = None
    activity_callback: ActivityCallback | None = None

    def log(self, message: str) -> None:
        append_log(self.log_file, message)
        if self.log_callback:
            self.log_callback(message)

    def trace(self, message: str) -> None:
        """Disk-only record: keeps the file log complete without new terminal rows."""
        append_log(self.log_file, message)

    def activity(self, message: str) -> None:
        """One live line pinned under the terminal output; empty text clears it."""
        if self.activity_callback:
            self.activity_callback(str(message))

    def progress(self, value: float) -> None:
        if self.progress_callback:
            self.progress_callback(max(0.0, min(1.0, float(value))))

    def cancelled(self) -> bool:
        return bool(self.cancel_callback and self.cancel_callback())


@dataclass
class JobResult:
    ok: bool
    message: str
    data: dict[str, Any]


# A parameter of these names is a secret: the GitHub token goes to its service as a parameter,
# and the line "Parameters: ..." went to the screen and into the log file with the token in it.
SENSITIVE_PARAMETER_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "api_key",
    "access_key",
    "private_key",
    "credential",
)


def redact_parameters(value: Any) -> Any:
    """The parameters as they may be shown: a secret is replaced, an empty one stays empty."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            # `llm_max_output_tokens` is a number of tokens, not a token.
            if any(part in lowered for part in SENSITIVE_PARAMETER_PARTS) and not lowered.endswith("_tokens"):
                result[key_text] = "***REDACTED***" if item not in {"", None} else item
            else:
                result[key_text] = redact_parameters(item)
        return result
    if isinstance(value, list):
        return [redact_parameters(item) for item in value]
    return value


def stopped_by_person(context: "JobContext", error: BaseException) -> bool:
    """A service stopped because Cancel was pressed, not because something broke.

    The services say it differently - `OperationCancelledError` of WinGet, a `RuntimeError`
    "Cancelled: ..." of the downloads - and all of them only after they asked `cancelled()` and
    got "yes". So both are needed: the stop was asked for, and the error says it is one.
    """
    return context.cancelled() and "cancel" in f"{error.__class__.__name__} {error}".lower()


def utf8_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


def hidden_subprocess_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def hidden_subprocess_creationflags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def hidden_subprocess_kwargs() -> dict[str, Any]:
    return {
        "startupinfo": hidden_subprocess_startupinfo(),
        "creationflags": hidden_subprocess_creationflags(),
    }


def _load_callable(service: str) -> Callable[[JobContext], Any]:
    module_name, function_name = service.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def execute_operation(
    paths: ProjectPaths,
    operation: Operation,
    log_callback: LogCallback | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    activity_callback: ActivityCallback | None = None,
) -> JobResult:
    run_stamp = timestamp().replace(":", "-")
    log_file = paths.logs / f"{run_stamp}_{operation.id}.log"
    report_dir = paths.report / f"{run_stamp}_{operation.id}"
    report_dir.mkdir(parents=True, exist_ok=True)
    context = JobContext(
        paths,
        operation,
        log_file,
        report_dir,
        log_callback,
        progress_callback,
        cancel_callback,
        activity_callback,
    )

    try:
        context.log(f"Starting operation: {operation.id}")
        if operation.parameters:
            shown = redact_parameters(operation.parameters)
            context.log(f"Parameters: {json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str)}")
        context.progress(0.0)
        result = _load_callable(operation.service)(context)
        context.progress(1.0)
        context.log(f"Finished operation: {operation.id}")

        if isinstance(result, dict):
            return JobResult(True, "Operation finished.", result)
        return JobResult(True, str(result or "Operation finished."), {})

    except Exception as exc:
        if stopped_by_person(context, exc):
            # Nothing failed: one line, not the traceback a failure gets.
            context.log(f"Stopped: {exc}")
            return JobResult(False, f"{exc.__class__.__name__}: {exc}", {"cancelled": True})
        context.log(traceback.format_exc())
        return JobResult(False, f"{exc.__class__.__name__}: {exc}", {})

    finally:
        context.activity("")
        # The folder is made before the operation so that it may write at once; most operations
        # write nothing there (a command of the terminal line never does), and an empty folder
        # for each of them only grows report\. rmdir takes an empty folder alone.
        try:
            report_dir.rmdir()
        except OSError:
            pass
