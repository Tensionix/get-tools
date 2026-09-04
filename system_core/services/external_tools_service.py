"""Service procedures that hand the machine to an outside tool for a while.

Chris Titus's WinUtil is a PowerShell script pulled from the author's site and
run interactively: it draws its own window, elevates itself, and outlives this
program. So it is started in a terminal of its own - Windows Terminal when it
is installed, otherwise a plain console - with PowerShell 7 when present and
Windows PowerShell 5.1 as the fallback. The job only launches and reports what
it launched.

The activation check is the opposite kind of tool: read-only, quick, and its
output belongs in the operation log.
"""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess

from system_core.core.jobs import JobContext, hidden_subprocess_kwargs


WINUTIL_COMMAND = 'irm "https://christitus.com/win" | iex'


def find_powershell(root: Path | None = None, which=shutil.which, program_files: Path | None = None) -> list[str]:
    """PowerShell 7 if it is anywhere to be found, else Windows PowerShell 5.1."""
    if root is not None:
        portable = root / "system_core" / "powershell" / "pwsh.exe"
        if portable.exists():
            return [str(portable)]
    found = which("pwsh.exe") or which("pwsh")
    if found:
        return [found]
    if program_files is None:
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    installed = program_files / "PowerShell" / "7" / "pwsh.exe"
    if installed.exists():
        return [str(installed)]
    return [which("powershell.exe") or "powershell.exe"]


def terminal_launch_command(shell: list[str], command: str, title: str, which=shutil.which) -> list[str]:
    """The argv that opens `command` in its own window, through Windows Terminal when available."""
    shell_args = [*shell, "-NoLogo", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", command]
    terminal = which("wt.exe")
    if terminal:
        return [terminal, "-w", "new", "--title", title, *shell_args]
    return ["cmd.exe", "/c", "start", title, *shell_args]


def launch_winutil(context: JobContext) -> dict[str, object]:
    """Open Chris Titus's WinUtil in its own terminal window."""
    shell = find_powershell(context.paths.root)
    argv = terminal_launch_command(shell, WINUTIL_COMMAND, "WinUtil")
    context.log(f"[LAUNCH] {WINUTIL_COMMAND}")
    context.log(f"[SHELL] {shell[0]}")
    context.log("[INFO] the script opens its own window and asks for administrator rights there; this program does not wait for it")
    subprocess.Popen(argv, cwd=str(context.paths.root))
    return {"launched": True, "shell": shell[0], "terminal": argv[0]}


def check_windows_activation(context: JobContext) -> dict[str, object]:
    """Print the licence state of this Windows: edition, channel and expiry, via slmgr."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    slmgr = system32 / "slmgr.vbs"
    if not slmgr.exists():
        raise RuntimeError(f"slmgr.vbs not found in {system32}")
    lines: list[str] = []
    for option in ("/xpr", "/dli"):
        result = subprocess.run(
            [str(system32 / "cscript.exe"), "//Nologo", str(slmgr), option],
            capture_output=True,
            text=True,
            encoding="cp866" if os.name == "nt" else "utf-8",
            errors="replace",
            timeout=120,
            **hidden_subprocess_kwargs(),
        )
        text = (result.stdout or "").strip() or (result.stderr or "").strip()
        context.log(f"[slmgr {option}]")
        for line in text.splitlines():
            if line.strip():
                context.log(f"  {line.rstrip()}")
                lines.append(line.rstrip())
    return {"lines": lines}
