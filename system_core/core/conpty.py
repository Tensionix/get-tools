"""Windows pseudo console (ConPTY) child runner.

Console tools decide how much they print by looking at their output handle.
When stdout is a plain pipe, `winget` drops the download/install progress bar
and the ANSI colors, so the GUI terminal only shows a few final lines and then
stays silent for minutes. Running the child under a pseudo console keeps the
same live picture a real PowerShell window would show.

The module is deliberately self-contained: only ctypes, no extra dependency,
and every failure path raises `ConsoleUnavailable` so callers can fall back to
ordinary pipes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
import ctypes
import os
import subprocess
import threading
import time

from ctypes import wintypes


IS_WINDOWS = os.name == "nt"

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
ERROR_BROKEN_PIPE = 109
ERROR_HANDLE_EOF = 38
ERROR_NO_DATA = 232
INFINITE = 0xFFFFFFFF
WAIT_TIMEOUT = 0x00000102

DEFAULT_CONSOLE_COLUMNS = 140
DEFAULT_CONSOLE_ROWS = 50


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# The pseudo console renders asynchronously: closing it the moment the child
# exits throws away the last frames, so the final table or summary never
# reaches the reader. Give conhost a short window to flush first.
CONSOLE_DRAIN_SECONDS = _env_float("AUDION_GET_CONSOLE_DRAIN_SECONDS", 0.4)
CONSOLE_DRAIN_LIMIT_SECONDS = _env_float("AUDION_GET_CONSOLE_DRAIN_LIMIT_SECONDS", 5.0)


class ConsoleUnavailable(RuntimeError):
    """The pseudo console could not be created for this command."""


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_kernel32: ctypes.WinDLL | None = None
_prototypes_ready = False


def _load_kernel32() -> ctypes.WinDLL:
    global _kernel32, _prototypes_ready
    if not IS_WINDOWS:
        raise ConsoleUnavailable("Pseudo console requires Windows.")
    if _kernel32 is not None and _prototypes_ready:
        return _kernel32

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not hasattr(kernel32, "CreatePseudoConsole"):
        raise ConsoleUnavailable("CreatePseudoConsole is not available on this Windows build.")

    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL

    kernel32.CreatePseudoConsole.argtypes = [
        COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long

    kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    kernel32.ClosePseudoConsole.restype = None

    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None

    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL

    _kernel32 = kernel32
    _prototypes_ready = True
    return kernel32


def console_supported() -> bool:
    """True when this Windows build can host a pseudo console."""
    try:
        _load_kernel32()
    except (ConsoleUnavailable, OSError):
        return False
    return True


def _environment_block(env: dict[str, str] | None) -> ctypes.Array | None:
    if env is None:
        return None
    parts = [f"{key}={value}" for key, value in env.items() if key]
    parts.append("")
    return ctypes.create_unicode_buffer("\0".join(parts) + "\0")


class ConsoleProcess:
    """A child process attached to its own pseudo console."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        columns: int = DEFAULT_CONSOLE_COLUMNS,
        rows: int = DEFAULT_CONSOLE_ROWS,
    ) -> None:
        self._kernel32 = _load_kernel32()
        self._closed = False
        self._exit_code: int | None = None
        self._pseudo_console = wintypes.HANDLE()
        self._output_read = wintypes.HANDLE()
        self._input_write = wintypes.HANDLE()
        self._process_handle = wintypes.HANDLE()
        self._thread_handle = wintypes.HANDLE()
        self._attribute_buffer: ctypes.Array | None = None
        self._attribute_list_ready = False
        self._last_chunk_at = time.monotonic()
        self._console_lock = threading.Lock()
        self._console_closed = False
        self._waiter: threading.Thread | None = None

        try:
            self._create(command, cwd, env, columns, rows)
        except BaseException:
            self.close()
            raise

    # -- setup ---------------------------------------------------------------

    def _create(
        self,
        command: list[str],
        cwd: str | Path | None,
        env: dict[str, str] | None,
        columns: int,
        rows: int,
    ) -> None:
        kernel32 = self._kernel32
        input_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()

        if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(self._input_write), None, 0):
            raise ConsoleUnavailable(f"CreatePipe failed: {ctypes.get_last_error()}")
        if not kernel32.CreatePipe(ctypes.byref(self._output_read), ctypes.byref(output_write), None, 0):
            kernel32.CloseHandle(input_read)
            raise ConsoleUnavailable(f"CreatePipe failed: {ctypes.get_last_error()}")

        try:
            size = COORD(max(40, int(columns)), max(10, int(rows)))
            result = kernel32.CreatePseudoConsole(
                size,
                input_read,
                output_write,
                0,
                ctypes.byref(self._pseudo_console),
            )
            if result != 0:
                raise ConsoleUnavailable(f"CreatePseudoConsole failed: 0x{result & 0xFFFFFFFF:08X}")
        finally:
            kernel32.CloseHandle(input_read)
            kernel32.CloseHandle(output_write)

        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        # Without this the child keeps our own standard handles (a GUI job
        # streams through pipes), writes there instead of into the pseudo
        # console, and nothing reaches the reader. NULL handles make the child
        # pick up the pseudo console it is attached to.
        startup.StartupInfo.dwFlags |= STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = None
        startup.StartupInfo.hStdOutput = None
        startup.StartupInfo.hStdError = None

        attribute_size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        if attribute_size.value == 0:
            raise ConsoleUnavailable("InitializeProcThreadAttributeList returned an empty size.")
        self._attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        startup.lpAttributeList = ctypes.cast(self._attribute_buffer, ctypes.c_void_p)

        if not kernel32.InitializeProcThreadAttributeList(
            startup.lpAttributeList, 1, 0, ctypes.byref(attribute_size)
        ):
            raise ConsoleUnavailable(f"InitializeProcThreadAttributeList failed: {ctypes.get_last_error()}")
        self._attribute_list_ready = True
        if not kernel32.UpdateProcThreadAttribute(
            startup.lpAttributeList,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            self._pseudo_console,
            ctypes.sizeof(wintypes.HANDLE),
            None,
            None,
        ):
            raise ConsoleUnavailable(f"UpdateProcThreadAttribute failed: {ctypes.get_last_error()}")

        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        environment = _environment_block(env)
        process_info = PROCESS_INFORMATION()
        created = kernel32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            # No CREATE_NO_WINDOW / CREATE_NEW_CONSOLE here: those give the child
            # its own console and detach it from the pseudo console, which is
            # exactly the output we want to capture. The pseudo console itself
            # is headless, so no window appears.
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
            ctypes.cast(environment, ctypes.c_void_p) if environment is not None else None,
            str(cwd) if cwd else None,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        )
        if not created:
            error = ctypes.get_last_error()
            raise OSError(0, f"CreateProcessW failed for {command[0]}", None, error)

        self._process_handle = wintypes.HANDLE(process_info.hProcess)
        self._thread_handle = wintypes.HANDLE(process_info.hThread)
        self.pid = int(process_info.dwProcessId)

        self._waiter = threading.Thread(target=self._wait_and_release, name="audion-conpty-wait", daemon=True)
        self._waiter.start()

    # -- lifetime ------------------------------------------------------------

    def _close_pseudo_console(self) -> None:
        with self._console_lock:
            if self._console_closed or not self._pseudo_console:
                return
            self._console_closed = True
            handle = self._pseudo_console
            self._pseudo_console = wintypes.HANDLE()
        self._kernel32.ClosePseudoConsole(handle)

    def _drain_pending_output(self) -> None:
        """Wait until the reader has been idle for a moment.

        Peeking the pipe here would deadlock: the reader thread holds a
        blocking `ReadFile` on the same handle, and synchronous pipe I/O is
        serialized per handle. Watching the reader's own timestamp is enough.
        """
        quiet = max(0.0, CONSOLE_DRAIN_SECONDS)
        deadline = time.monotonic() + max(quiet, CONSOLE_DRAIN_LIMIT_SECONDS)
        while time.monotonic() < deadline:
            if time.monotonic() - self._last_chunk_at >= quiet:
                return
            time.sleep(0.05)

    def _wait_and_release(self) -> None:
        kernel32 = self._kernel32
        kernel32.WaitForSingleObject(self._process_handle, INFINITE)
        code = wintypes.DWORD(0)
        if kernel32.GetExitCodeProcess(self._process_handle, ctypes.byref(code)):
            self._exit_code = int(code.value)
        # Let the renderer push the final frames into the pipe first; closing
        # the pseudo console breaks the pipe and ends `read_chunks()`.
        self._drain_pending_output()
        self._close_pseudo_console()

    def read_chunks(self, buffer_size: int = 16384) -> Iterator[bytes]:
        kernel32 = self._kernel32
        buffer = ctypes.create_string_buffer(buffer_size)
        read_count = wintypes.DWORD(0)
        while True:
            if not self._output_read:
                return
            ok = kernel32.ReadFile(
                self._output_read,
                buffer,
                buffer_size,
                ctypes.byref(read_count),
                None,
            )
            if not ok:
                error = ctypes.get_last_error()
                if error in {ERROR_BROKEN_PIPE, ERROR_HANDLE_EOF, ERROR_NO_DATA, 0}:
                    return
                raise OSError(0, "ReadFile failed while reading pseudo console output", None, error)
            if read_count.value == 0:
                return
            self._last_chunk_at = time.monotonic()
            yield bytes(buffer.raw[: read_count.value])

    def terminate(self, exit_code: int = 1) -> None:
        if self._process_handle:
            self._kernel32.TerminateProcess(self._process_handle, exit_code)

    kill = terminate

    def wait(self, timeout: float | None = None) -> int:
        if self._process_handle:
            milliseconds = INFINITE if timeout is None else max(0, int(timeout * 1000))
            if self._kernel32.WaitForSingleObject(self._process_handle, milliseconds) == WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired("conpty", timeout or 0)
        if self._waiter is not None:
            self._waiter.join(timeout if timeout is not None else 10.0)
        if self._exit_code is None and self._process_handle:
            code = wintypes.DWORD(0)
            if self._kernel32.GetExitCodeProcess(self._process_handle, ctypes.byref(code)):
                self._exit_code = int(code.value)
        return int(self._exit_code or 0)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        kernel32 = self._kernel32

        waiter_finished = True
        if self._waiter is not None and self._waiter.is_alive():
            self.terminate()
            self._waiter.join(5.0)
            waiter_finished = not self._waiter.is_alive()
        self._close_pseudo_console()

        handles = ["_output_read", "_input_write", "_thread_handle"]
        if waiter_finished:
            # The waiter thread owns the process handle until it returns.
            handles.append("_process_handle")
        for attribute in handles:
            handle = getattr(self, attribute, None)
            if handle:
                kernel32.CloseHandle(handle)
                setattr(self, attribute, wintypes.HANDLE())
        if self._attribute_buffer is not None:
            if self._attribute_list_ready:
                kernel32.DeleteProcThreadAttributeList(ctypes.cast(self._attribute_buffer, ctypes.c_void_p))
            self._attribute_buffer = None

    def __enter__(self) -> "ConsoleProcess":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
