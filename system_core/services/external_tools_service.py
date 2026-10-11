"""Service procedures that hand the machine to an outside tool for a while.

Chris Titus's WinUtil is a PowerShell script pulled from the author's site and
run interactively: it draws its own window, elevates itself, and outlives this
program. So it is started in a terminal of its own - Windows Terminal when it
is installed, otherwise a plain console - with PowerShell 7 when present and
Windows PowerShell 5.1 as the fallback. It is a PowerShell of the machine, not
the one the program carries (App\\AudionPs.exe): WinUtil draws its window with
WPF, and the program's PowerShell has none. The job only launches and reports
what it launched.

"Outlives this program" has to be asked for. The window puts every command it
starts into a job that Windows ends with the window, and STOP of a later run
ends everything the job holds: a tool left running on purpose is started out
of that job (`start_on_its_own`).

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

CREATE_NEW_CONSOLE = 0x00000010
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def find_powershell(which=shutil.which, program_files: Path | None = None) -> list[str]:
    """A PowerShell of the machine for a tool with a window of its own: PowerShell 7 when installed, else Windows PowerShell 5.1."""
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
    """The argv that opens `command` in its own window, through Windows Terminal when available.

    Without Windows Terminal it is PowerShell itself, to be started in a console of its own
    (`needs_console`); the title of that console is set by its first command. Not `cmd /c start`:
    cmd reads the `|` of the command as a pipe of its own, and a shell that is not there still
    ends as "launched".
    """
    arguments = ["-NoLogo", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command"]
    terminal = which("wt.exe")
    if terminal:
        return [terminal, "-w", "new", "--title", title, *shell, *arguments, command]
    named = title.replace("'", "''")
    return [*shell, *arguments, f"$Host.UI.RawUI.WindowTitle = '{named}'; {command}"]


def needs_console(argv: list[str]) -> bool:
    """Whether the program has to be given a console: Windows Terminal draws its own window, a shell does not."""
    return Path(argv[0]).name.lower() != "wt.exe"


def start_on_its_own(context: JobContext, argv: list[str], *, cwd: Path, console: bool, popen=subprocess.Popen):
    """Start a program that is left running: out of the job of whoever started this one.

    Windows refuses the leave to a process inside a job that does not allow it (access denied):
    the program is then started as before, and the log says what that means for it. A program
    that is not there is an error of the operation, as `Popen` raises it. The process is handed
    back: a caller that waits for it - the image build - asks it whether it has ended.
    """
    flags = CREATE_NEW_CONSOLE if console else 0
    try:
        return popen(argv, cwd=str(cwd), creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
    except PermissionError:
        process = popen(argv, cwd=str(cwd), creationflags=flags)
        context.log("[WARN] Windows did not let the program leave the job it was started in: it closes together with this program's window")
        return process


def launch_winutil(context: JobContext) -> dict[str, object]:
    """Open Chris Titus's WinUtil in its own terminal window."""
    shell = find_powershell()
    argv = terminal_launch_command(shell, WINUTIL_COMMAND, "WinUtil")
    context.log(f"[LAUNCH] {WINUTIL_COMMAND}")
    context.log(f"[SHELL] {shell[0]}")
    context.log("[INFO] the script opens its own window and asks for administrator rights there; this program does not wait for it")
    start_on_its_own(context, argv, cwd=context.paths.root, console=needs_console(argv))
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
