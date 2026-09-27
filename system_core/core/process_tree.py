"""A started process and everything it starts, stopped as one.

A Windows job object holds the process; the children it spawns join the job on their own, so
cancelling stops them too - also those whose parent (a CMD wrapper) has already exited. Without a
job (not Windows, or the job could not be made) the fallback is `taskkill /T` on the process.

A process that runs elevated after a UAC prompt is started by Windows outside this tree and is
not stopped here; the caller says so.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time

PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class ProcessTree:
    """The job of one started process: `kill()` stops the process and its descendants."""

    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        self._job = None
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.OpenProcess.restype = wintypes.HANDLE
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            process = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, self.pid)
            if not process:
                kernel32.CloseHandle(wintypes.HANDLE(job))
                return
            try:
                if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(process)):
                    kernel32.CloseHandle(wintypes.HANDLE(job))
                    return
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(process))
            self._kernel32 = kernel32
            self._job = wintypes.HANDLE(job)
        except (OSError, AttributeError):
            self._job = None

    def active(self) -> int:
        """Processes of the tree still running (-1 when unknown)."""
        if self._job is None:
            return -1
        info = _BasicAccounting()
        if not self._kernel32.QueryInformationJobObject(
            self._job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            return -1
        return int(info.ActiveProcesses)

    def kill(self, wait_seconds: float = 5.0) -> bool:
        """Stop every process of the tree and wait for them to exit; True when none is left."""
        if self._job is not None:
            self._kernel32.TerminateJobObject(self._job, 1)
            deadline = time.monotonic() + max(0.0, wait_seconds)
            while time.monotonic() < deadline:
                if self.active() == 0:
                    return True
                time.sleep(0.05)
            return self.active() == 0
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.pid)],
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return False

    def close(self) -> None:
        """Let go of the job; processes still running (a program the command launched) stay."""
        if self._job is not None:
            self._kernel32.CloseHandle(self._job)
            self._job = None
